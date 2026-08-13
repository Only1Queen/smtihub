from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.models import Group, User
from django.db import transaction

from hub import scoring
from hub.models import Employee, Goal, GoalAssignment, Kpi, Task


class GoalForm(forms.ModelForm):
    assignees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(active=True),
        widget=forms.CheckboxSelectMultiple, required=False,
        help_text="Leave every box empty for a goal the whole team carries. Tick names only "
                  "when the goal belongs to some analysts and not others — the ones left off "
                  "are excluded entirely, never counted as a zero.")

    class Meta:
        model = Goal
        fields = ["code", "name", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2,
                "placeholder": "What good looks like for this goal, in a sentence the team will recognise."}),
            "name": forms.TextInput(attrs={"placeholder": "e.g. Cloud Security Coverage"}),
        }

    def __init__(self, *args, year=None, team=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.year = year or (self.instance.year if self.instance.pk else None)
        if team is not None:
            self.fields["assignees"].queryset = team
        if self.instance.pk:
            self.fields["assignees"].initial = self.instance.assignees.all()
            self.fields["code"].disabled = True

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        clash = Goal.objects.filter(year=self.year, code=code).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f"Goal {code} already exists this year.")
        return code

    def save(self, commit=True):
        goal = super().save(commit=False)
        goal.year = self.year
        goal.save()
        wanted = set(self.cleaned_data["assignees"])
        GoalAssignment.objects.filter(goal=goal).exclude(employee__in=wanted).delete()
        for emp in wanted:
            GoalAssignment.objects.get_or_create(goal=goal, employee=emp)
        return goal


class KpiForm(forms.ModelForm):
    class Meta:
        model = Kpi
        fields = ["code", "text", "max_marks", "quarterly", "scoring_mode"]
        widgets = {"text": forms.TextInput(attrs={"placeholder": "What is being measured"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.has_scores():
            for name in ("max_marks", "quarterly", "scoring_mode"):
                self.fields[name].disabled = True
                self.fields[name].help_text = "Locked — this KPI already has scores."


# extra=3 gives room to add KPIs without a save-and-reopen round trip; blank
# rows are ignored. A JS row-adder would be more code for the same outcome.
KpiFormSet = forms.inlineformset_factory(Goal, Kpi, form=KpiForm, extra=3, can_delete=True)


class TaskForm(forms.ModelForm):
    """One form, one task each for everybody ticked.

    Assignees stay one-per-Task rather than becoming a many-to-many: approval,
    scoring and the freeze rules are all per-analyst, and a shared row would
    have to invent an answer for "approved for whom".
    """

    assignees = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.filter(active=True),
        widget=forms.CheckboxSelectMultiple, label="Assign to",
        help_text="Tick everyone who has this task. Each gets their own copy to update, "
                  "submit and be scored on.")

    class Meta:
        model = Task
        fields = ["title", "description", "due_date", "scoring_month", "kpi", "weight"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2,
                "placeholder": "What needs doing, and what finished looks like."}),
            "title": forms.TextInput(attrs={"placeholder": "e.g. Publish November threat landscape brief"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, year=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.year = year
        self.instance.year = year
        self.fields["scoring_month"] = forms.TypedChoiceField(
            choices=[(i, m) for i, m in enumerate(scoring.MONTHS)], coerce=int,
            label="Scoring month",
            help_text="Set explicitly, so a task's month never shifts if the due date moves.")
        self.fields["kpi"].queryset = Kpi.objects.filter(goal__year=year).select_related("goal")
        self.fields["kpi"].required = False
        self.fields["kpi"].label = "Counts toward"
        self.fields["kpi"].empty_label = "Nothing — operational task"
        self.fields["weight"].required = False
        self.fields["weight"].label = "Weight within that KPI"

    def clean(self):
        data = super().clean()
        if data.get("kpi") and not data.get("weight"):
            data["weight"] = 100
        if not data.get("kpi"):
            data["weight"] = None
        return data

    def _post_clean(self):
        """Skipped: there is no single instance to validate. Task.clean() reads
        `assignee`, which only exists once per person in save() below."""

    @transaction.atomic
    def save(self, commit=True, created_by=None):
        """All or nothing: if one analyst's task is invalid, nobody gets one, so
        the manager fixes it and submits once rather than chasing duplicates."""
        shared = {f: self.cleaned_data[f] for f in self.Meta.fields}
        tasks = []
        for employee in self.cleaned_data["assignees"]:
            task = Task(year=self.year, assignee=employee, created_by=created_by, **shared)
            task.full_clean()
            task.save()
            tasks.append(task)
        return tasks


class TaskUpdateForm(forms.Form):
    """The analyst's own progress ladder. Only the last rung asks for anything:
    the first three just record where the work has got to."""

    status = forms.ChoiceField(
        choices=[
            (Task.PICKED_UP, "Task picked up — it is mine and it has started"),
            (Task.IN_PROGRESS, "In progress"),
            (Task.ON_TRACK, "On track"),
            (Task.SUBMITTED, "Completed — send to my manager to review and grade"),
        ],
        widget=forms.RadioSelect, label="Where is this now?")
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3,
        "placeholder": "What you did, and anything the manager should know."}),
        required=False, label="Update")

    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Start on where the task already is, so posting a note does not quietly
        # walk the status backwards.
        current = getattr(task, "status", None)
        self.fields["status"].initial = (current if current in Task.ANALYST_STATUSES
                                         else Task.PICKED_UP)

    @property
    def complete(self):
        return self.cleaned_data.get("status") == Task.SUBMITTED


class DecisionForm(forms.Form):
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 2,
        "placeholder": "Why it is going back, and what to change."}),
        required=False, label="Reason")
    grade = forms.IntegerField(
        required=False, min_value=0, max_value=100, label="Mark for this work (out of 100)",
        help_text="Optional. Recorded on the task and shown to the analyst. It does not move the "
                  "appraisal percentage — that maths belongs to the KPIs, so link the task to one "
                  "if it should count.")

    def __init__(self, *args, kpi_linked=False, **kwargs):
        super().__init__(*args, **kwargs)
        # A KPI task already has a mark, computed from its weight and approval.
        if kpi_linked:
            del self.fields["grade"]


class ReasonForm(forms.Form):
    """Reopening frozen data is allowed, but never silently."""

    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3,
            "placeholder": "Why this needs to change, and what is being corrected."}),
        label="Reason", help_text="Recorded permanently in the activity log.")

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if len(reason) < 10:
            raise forms.ValidationError(
                "Give enough detail that this still makes sense in six months.")
        return reason


class SetPasswordForm(forms.Form):
    password1 = forms.CharField(widget=forms.PasswordInput, label="New password",
                                help_text="At least 12 characters.")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        if data.get("password1") != data.get("password2"):
            raise forms.ValidationError("The two passwords do not match.")
        if data.get("password1"):
            password_validation.validate_password(data["password1"], self.user)
        return data

    def save(self):
        self.user.set_password(self.cleaned_data["password1"])
        self.user.save(update_fields=["password"])
        return self.user


class EmployeeForm(forms.Form):
    """Creates or edits the User + Employee pair in one step."""

    full_name = forms.CharField(max_length=120, label="Full name")
    job_title = forms.CharField(max_length=120, required=False, label="Job title")
    email = forms.EmailField(required=False, label="Work email")
    username = forms.CharField(max_length=150, help_text="Used to sign in.")
    is_manager = forms.BooleanField(
        required=False, label="Manager",
        help_text="Can score the team, approve tasks and read the activity log. "
                  "For an account that signs in with Active Directory this is set by "
                  "AD group membership and resets at their next sign-in.")
    # Set here, at creation: an account made without one cannot sign in at all
    # until somebody remembers the separate password screen.
    password1 = forms.CharField(
        widget=forms.PasswordInput, label="Password", required=False,
        help_text="At least 12 characters. Give it to them; they can change it themselves "
                  "under Settings once they are in.")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password",
                                required=False)

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        self.role_change = None
        if instance and not kwargs.get("data"):
            kwargs["initial"] = {
                "full_name": instance.name, "job_title": instance.job_title,
                "email": instance.user.email, "username": instance.user.get_username(),
                "is_manager": instance.user.groups.filter(name="Manager").exists(),
            }
        super().__init__(*args, **kwargs)
        if instance:
            self.fields["username"].disabled = True
            # Editing has its own password screen; two ways to do it on one form
            # is how somebody resets a password by accident.
            del self.fields["password1"], self.fields["password2"]
        else:
            self.fields["password1"].required = True
            self.fields["password2"].required = True

    def clean(self):
        data = super().clean()
        if "password1" not in self.fields:
            return data
        if data.get("password1") != data.get("password2"):
            raise forms.ValidationError("The two passwords do not match.")
        if data.get("password1"):
            name = data.get("full_name", "")
            first, _, last = name.partition(" ")
            password_validation.validate_password(
                data["password1"],
                User(username=data.get("username", ""), first_name=first, last_name=last))
        return data

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username=username)
        if self.instance:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError("That username is taken.")
        return username

    def save(self, manager=None):
        name = self.cleaned_data["full_name"].strip()
        first, _, last = name.partition(" ")
        if self.instance:
            user = self.instance.user
            employee = self.instance
        else:
            user = User(username=self.cleaned_data["username"])
            user.set_password(self.cleaned_data["password1"])
            employee = Employee(user=user, manager=manager)
        user.first_name, user.last_name = first, last
        user.email = self.cleaned_data["email"]
        user.save()
        employee.user = user
        employee.job_title = self.cleaned_data["job_title"]
        employee.save()
        self._set_role(user, self.cleaned_data["is_manager"])
        return employee

    def _set_role(self, user, wants_manager):
        """Managers were previously only made from a shell, which meant the one
        privileged action in the app was the one with no audit trail."""
        group, _ = Group.objects.get_or_create(name="Manager")
        had = user.groups.filter(pk=group.pk).exists()
        if wants_manager and not had:
            user.groups.add(group)
            self.role_change = "granted"
        elif had and not wants_manager:
            user.groups.remove(group)
            self.role_change = "revoked"


class AcknowledgeForm(forms.Form):
    """Signing off a year. The tick is the record; the comment is the analyst's
    own words, kept whether or not anyone agrees with them."""

    confirm = forms.BooleanField(
        label="I have seen this appraisal and discussed it with my manager.")
    comment = forms.CharField(
        required=False, label="Comment (optional)",
        widget=forms.Textarea(attrs={"rows": 3,
            "placeholder": "Anything you want recorded alongside your acknowledgement."}))


def ensure_manager_group(user):
    group, _ = Group.objects.get_or_create(name="Manager")
    user.groups.add(group)
