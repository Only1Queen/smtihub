"""Nine models.

The validation here is the plan's immutability section, enforced. Anything that
would retroactively change a mark somebody already has to defend raises
ValidationError rather than being left to discipline.
"""

from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from hub import scoring


class Employee(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name="employee")
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="reports")
    job_title = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    # One timestamp instead of a read-flag per notification: notifications are
    # derived, not stored, so there is no row to mark.
    notifications_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["user__last_name", "user__username"]

    def __str__(self):
        return self.name

    @property
    def name(self):
        return self.user.get_full_name() or self.user.get_username()

    @property
    def initials(self):
        parts = [p for p in self.name.replace(".", " ").split() if p]
        return "".join(p[0] for p in parts[:2]).upper() or "??"

    @property
    def is_manager(self):
        return self.reports.exists()

    @property
    def on_leave(self):
        return self.leaves.filter(end_date__isnull=True).exists()


class AppraisalYear(models.Model):
    label = models.CharField(max_length=40, unique=True)
    start_year = models.PositiveIntegerField(help_text="Calendar year the May start falls in")
    closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_year"]

    def __str__(self):
        return self.label

    def month_labels(self):
        return scoring.MONTHS

    @property
    def ends_at(self):
        """The year runs May to April, so it ends the moment 1 May opens.

        Local midnight, not UTC: the countdown in the topbar has to hit zero
        when the year ends here, not when it ends in London.
        """
        return timezone.make_aware(datetime(self.start_year + 1, 5, 1))


class Goal(models.Model):
    year = models.ForeignKey(AppraisalYear, on_delete=models.CASCADE, related_name="goals")
    code = models.CharField(max_length=2)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    assignees = models.ManyToManyField(Employee, through="GoalAssignment", related_name="goals")

    class Meta:
        ordering = ["order", "code"]
        constraints = [models.UniqueConstraint(fields=["year", "code"], name="uniq_goal_code_per_year")]

    def __str__(self):
        return f"{self.code} · {self.name}"

    @property
    def total_marks(self):
        return sum(k.max_marks for k in self.kpis.all())

    def has_scores(self):
        return Score.objects.filter(kpi__goal=self).exists()

    def clean(self):
        if self.year_id and self.year.closed:
            raise ValidationError("This appraisal year is closed. Reopen it to make changes.")


class Kpi(models.Model):
    MODE_CHOICES = [(scoring.MANUAL, "Scored manually"),
                    (scoring.FROM_TASKS, "Scored from approved tasks")]

    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="kpis")
    code = models.CharField(max_length=6)
    text = models.CharField(max_length=400)
    max_marks = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(50)])
    quarterly = models.BooleanField(default=False)
    scoring_mode = models.CharField(max_length=12, choices=MODE_CHOICES, default=scoring.MANUAL)
    order = models.PositiveSmallIntegerField(default=0)

    # scoring.py duck-types this row directly: it reads `code`, `goal_id`,
    # `max_marks`, `quarterly` and `scoring_mode`, all of which are real fields.
    class Meta:
        ordering = ["order", "code"]
        constraints = [models.UniqueConstraint(fields=["goal", "code"], name="uniq_kpi_code_per_goal")]

    def __str__(self):
        return f"{self.code} — {self.text[:48]}"

    def has_scores(self):
        return self.scores.exists()

    def clean(self):
        if self.goal_id and self.goal.year.closed:
            raise ValidationError("This appraisal year is closed. Reopen it to make changes.")
        if not self.pk:
            return
        # Locked once scores exist: all three change what recorded marks mean.
        before = Kpi.objects.filter(pk=self.pk).first()
        if before and self.has_scores():
            for field, label in (("max_marks", "maximum marks"),
                                 ("quarterly", "quarterly flag"),
                                 ("scoring_mode", "scoring method")):
                if getattr(before, field) != getattr(self, field):
                    raise ValidationError(
                        f"{self.code} already has scores, so its {label} cannot change — "
                        f"it would alter percentages already recorded. Change it next year instead."
                    )

    def save(self, *args, **kwargs):
        # Enforced at the model layer, not left to the form: an edit that would
        # rewrite recorded percentages must fail wherever it comes from.
        if not kwargs.pop("skip_validation", False):
            self.clean()
        super().save(*args, **kwargs)


class GoalAssignment(models.Model):
    goal = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name="assignments")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="goal_assignments")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["goal", "employee"], name="uniq_goal_assignment")]

    def __str__(self):
        return f"{self.goal.code} → {self.employee.name}"


class Score(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="scores")
    kpi = models.ForeignKey(Kpi, on_delete=models.CASCADE, related_name="scores")
    month_index = models.PositiveSmallIntegerField(validators=[MaxValueValidator(11)])
    value = models.DecimalField(max_digits=5, decimal_places=1)
    scored_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name="scores_given")
    comment = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee", "kpi", "month_index"]
        constraints = [models.UniqueConstraint(fields=["employee", "kpi", "month_index"],
                                               name="uniq_score_per_kpi_month")]

    def __str__(self):
        return f"{self.employee.name} · {self.kpi.code} · {scoring.MONTHS[self.month_index]}"

    @property
    def year(self):
        return self.kpi.goal.year

    def clean(self):
        if self.kpi.goal.year.closed:
            raise ValidationError("This appraisal year is closed. Reopen it to record a correction.")
        if self.value is not None and self.value > self.kpi.max_marks:
            raise ValidationError(f"{self.kpi.code} is out of {self.kpi.max_marks} marks.")
        if self.value is not None and self.value < 0:
            raise ValidationError("A score cannot be negative.")
        if self.kpi.quarterly and not scoring.is_quarter_end(self.month_index):
            raise ValidationError(
                f"{self.kpi.code} is quarterly — it can only be scored in Jun, Sep, Dec or Mar."
            )


class ScoredMonth(models.Model):
    """Marks a month closed for one analyst. Task-derived values are snapshotted
    into Score at this point and stop moving; without that, adding a task later
    would silently change a mark already earned."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="scored_months")
    year = models.ForeignKey(AppraisalYear, on_delete=models.CASCADE, related_name="scored_months")
    month_index = models.PositiveSmallIntegerField(validators=[MaxValueValidator(11)])
    closed_at = models.DateTimeField(auto_now_add=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "year", "month_index"],
                                               name="uniq_scored_month")]

    def __str__(self):
        return f"{self.employee.name} · {scoring.MONTHS[self.month_index]} (scored)"


class Task(models.Model):
    NOT_STARTED, PICKED_UP, IN_PROGRESS, ON_TRACK, SUBMITTED, APPROVED, RETURNED = (
        "not_started", "picked_up", "in_progress", "on_track",
        "submitted", "approved", "returned")
    STATUS_CHOICES = [
        (NOT_STARTED, "Not started"), (PICKED_UP, "Task picked up"),
        (IN_PROGRESS, "In progress"), (ON_TRACK, "On track"),
        (SUBMITTED, "Sent for review"), (APPROVED, "Completed"), (RETURNED, "Sent back"),
    ]
    # What an analyst may set on their own task. Completing and sending back are
    # the manager's alone, so they are not on this list.
    ANALYST_STATUSES = [PICKED_UP, IN_PROGRESS, ON_TRACK, SUBMITTED]

    year = models.ForeignKey(AppraisalYear, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=NOT_STARTED)

    # Explicit, never inferred from due_date: a task's month must not shift.
    scoring_month = models.PositiveSmallIntegerField(validators=[MaxValueValidator(11)])

    kpi = models.ForeignKey(Kpi, null=True, blank=True, on_delete=models.PROTECT, related_name="tasks")
    weight = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)])

    # Work with no KPI behind it earns no marks in the appraisal maths, which
    # left the manager nothing to say about it at all. This is that mark, given
    # at approval, out of 100.
    grade = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(100)])

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scoring_month", "due_date", "title"]

    def __str__(self):
        return self.title

    @property
    def approved(self):
        return self.status == self.APPROVED

    @property
    def counts_toward_score(self):
        return self.kpi_id is not None

    @property
    def month_label(self):
        return scoring.MONTHS[self.scoring_month]

    def clean(self):
        if self.kpi_id and self.grade is not None:
            raise ValidationError(
                "A KPI-linked task is marked by its KPI. A second number here would "
                "contradict the mark the goal already gives it."
            )
        if self.kpi_id and not self.weight:
            raise ValidationError("A task linked to a KPI needs a weight.")
        if not self.kpi_id and self.weight:
            raise ValidationError("Only a KPI-linked task can carry a weight.")
        if self.kpi_id:
            # The same rule the score sheet uses, or a task could be refused for
            # a goal the analyst is visibly being scored on.
            if self.kpi.goal_id not in assigned_goal_ids(self.assignee, self.kpi.goal.year):
                raise ValidationError(
                    f"{self.assignee.name} is not assigned goal {self.kpi.goal.code}, "
                    f"so this task cannot count toward {self.kpi.code}."
                )
            if self.year_id and self.kpi.goal.year_id != self.year_id:
                raise ValidationError("The KPI belongs to a different appraisal year.")
        if not self.year_id:
            return
        if self.year.closed:
            raise ValidationError("This appraisal year is closed.")
        if self.pk is None and month_is_scored(self.assignee, self.year, self.scoring_month):
            raise ValidationError(
                f"{scoring.MONTHS[self.scoring_month]} is already scored for "
                f"{self.assignee.name}. A task added now cannot change that month."
            )


class TaskUpdate(models.Model):
    PENDING, APPROVED, RETURNED, NOT_NEEDED = "pending", "approved", "returned", "not_needed"
    # NOT_NEEDED is a progress note: the analyst is telling the manager where
    # they are, not asking for anything. Left as PENDING it would sit in the
    # approval queue forever waiting on a decision nobody owes it.
    DECISION_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"),
                        (RETURNED, "Sent back"), (NOT_NEEDED, "Progress update")]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="updates")
    author = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="task_updates")
    note = models.TextField(blank=True)
    # Blank on a daily update: it reports the day's work and moves nothing, so
    # naming a status here would credit it with a transition it never made.
    proposed_status = models.CharField(max_length=12, choices=Task.STATUS_CHOICES,
                                       default=Task.APPROVED, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    decision = models.CharField(max_length=10, choices=DECISION_CHOICES, default=PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    # A daily update needs no decision, but the manager still has to clear it
    # off the queue and be able to say something about it. Reviewing sets this
    # and writes the comment into decision_note — the same field the trail
    # already renders, so a manager's words live in one place.
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.task.title} · {self.get_decision_display()}"

    @property
    def manager_comment(self):
        return self.decision_note if self.reviewed_at else ""


class Leave(models.Model):
    """A stretch of days an analyst owes no daily update.

    Ended rather than deleted, and dated rather than a flag on Employee: the
    coverage grid looks backwards, so "were they away on the 3rd?" has to keep
    having an answer after they come back.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leaves")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Empty means still away")
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="+")

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.employee.name} on leave from {self.start_date}"

    def covers(self, day):
        return self.start_date <= day and (self.end_date is None or day <= self.end_date)

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("Leave cannot end before it starts.")


class Announcement(models.Model):
    """Something the manager wants the whole team to read. No targeting, no
    read receipts — one team, one noticeboard."""

    author = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="announcements")
    title = models.CharField(max_length=160)
    body = models.TextField()
    posted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-posted_at"]

    def __str__(self):
        return self.title


class YearAcknowledgement(models.Model):
    """The analyst's own record that they have seen their year.

    The percentage is copied in rather than looked up later: if the year is
    reopened for a correction, what they signed must stay what they saw.
    """

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="acknowledgements")
    year = models.ForeignKey(AppraisalYear, on_delete=models.CASCADE, related_name="acknowledgements")
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    annual_percent = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    comment = models.TextField(blank=True, help_text="Optional — anything the analyst wants recorded")

    class Meta:
        ordering = ["-acknowledged_at"]
        constraints = [models.UniqueConstraint(fields=["employee", "year"],
                                               name="uniq_acknowledgement_per_year")]

    def __str__(self):
        return f"{self.employee.name} acknowledged {self.year.label}"


class AuditEvent(models.Model):
    """Append-only. The application never updates or deletes a row here, and in
    production the database role holds only INSERT and SELECT on this table."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                              related_name="+")
    actor_label = models.CharField(max_length=150, help_text="Kept even if the account is removed")
    action = models.CharField(max_length=40, db_index=True)
    target = models.CharField(max_length=300)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp", "-id"]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.target}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Audit events are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events are append-only and cannot be deleted.")


def assigned_goal_ids(employee, year):
    """Which goals this analyst is measured on.

    Explicit assignments, plus every goal nobody has been assigned to at all: a
    goal with no names against it is a team goal, and everyone carries it. The
    old rule — explicit assignments only — meant a freshly created year scored
    nobody until somebody remembered to tick five boxes per analyst, and an
    empty score sheet looks like a broken screen, not a missing tick.

    Ticking anybody into a goal narrows it to exactly those people, so a goal
    that is genuinely only one analyst's still works the way it always did.
    """
    explicit = set(GoalAssignment.objects.filter(employee=employee, goal__year=year)
                   .values_list("goal_id", flat=True))
    team_wide = set(Goal.objects.filter(year=year, assignments__isnull=True)
                    .values_list("id", flat=True))
    return explicit | team_wide


def month_is_scored(employee, year, month_index):
    return ScoredMonth.objects.filter(
        employee=employee, year=year, month_index=month_index).exists()
