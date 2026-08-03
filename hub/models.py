"""Nine models.

The validation here is the plan's immutability section, enforced. Anything that
would retroactively change a mark somebody already has to defend raises
ValidationError rather than being left to discipline.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from hub import scoring


class Employee(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name="employee")
    manager = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="reports")
    job_title = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)

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
    NOT_STARTED, IN_PROGRESS, SUBMITTED, APPROVED, RETURNED = (
        "not_started", "in_progress", "submitted", "approved", "returned")
    STATUS_CHOICES = [
        (NOT_STARTED, "Not started"), (IN_PROGRESS, "In progress"),
        (SUBMITTED, "Awaiting approval"), (APPROVED, "Approved"), (RETURNED, "Sent back"),
    ]

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
        if self.kpi_id and not self.weight:
            raise ValidationError("A task linked to a KPI needs a weight.")
        if not self.kpi_id and self.weight:
            raise ValidationError("Only a KPI-linked task can carry a weight.")
        if self.kpi_id:
            assigned = GoalAssignment.objects.filter(
                goal=self.kpi.goal, employee=self.assignee).exists()
            if not assigned:
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
    PENDING, APPROVED, RETURNED = "pending", "approved", "returned"
    DECISION_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"), (RETURNED, "Sent back")]

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="updates")
    author = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="task_updates")
    note = models.TextField(blank=True)
    proposed_status = models.CharField(max_length=12, choices=Task.STATUS_CHOICES,
                                       default=Task.APPROVED)
    submitted_at = models.DateTimeField(auto_now_add=True)

    decision = models.CharField(max_length=10, choices=DECISION_CHOICES, default=PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.task.title} · {self.get_decision_display()}"


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


def month_is_scored(employee, year, month_index):
    return ScoredMonth.objects.filter(
        employee=employee, year=year, month_index=month_index).exists()
