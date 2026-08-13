"""Bridges the ORM to scoring.py.

scoring.py stays free of Django; this module does the fetching and the freezing.
The one rule worth stating twice: a task-derived mark is live only while its
month is open. Closing the month writes the value into Score, after which no
task change can move it.
"""

from django.db import transaction
from django.utils import timezone

from hub import scoring
from hub.audit import log_event
from hub.models import (AppraisalYear, Kpi, Score, ScoredMonth, Task, TaskUpdate,
                        month_is_scored)
from hub.models import assigned_goal_ids as models_assigned_goal_ids


def assigned_goal_ids(employee, year):
    """One rule, defined on the model so Task validation shares it."""
    return models_assigned_goal_ids(employee, year)


def year_kpis(year):
    return list(Kpi.objects.filter(goal__year=year).select_related("goal"))


def task_specs(employee, kpi, month):
    """Only this analyst's tasks for this KPI and month. Pooling across analysts
    would let one person's work move another's score."""
    return [scoring.TaskSpec(t.weight, t.approved) for t in
            Task.objects.filter(assignee=employee, kpi=kpi, scoring_month=month)]


def month_values(employee, year, month, kpis=None):
    """kpi.code -> mark. Manual marks come from Score. Task-derived marks come
    from Score once the month is scored, and are computed live before that."""
    kpis = kpis if kpis is not None else year_kpis(year)
    stored = {s.kpi.code: s.value for s in
              Score.objects.filter(employee=employee, kpi__goal__year=year, month_index=month)
              .select_related("kpi")}
    frozen = month_is_scored(employee, year, month)

    values = {}
    for kpi in kpis:
        if kpi.scoring_mode == scoring.FROM_TASKS and not frozen:
            values[kpi.code] = scoring.task_rollup(kpi.max_marks, task_specs(employee, kpi, month))
        else:
            raw = stored.get(kpi.code)
            values[kpi.code] = float(raw) if raw is not None else None
    return values


def month_summary(employee, year, month, kpis=None, assigned=None):
    kpis = kpis if kpis is not None else year_kpis(year)
    assigned = assigned if assigned is not None else assigned_goal_ids(employee, year)
    return scoring.month_summary(kpis, month, assigned, month_values(employee, year, month, kpis))


def year_summaries(employee, year):
    kpis = year_kpis(year)
    assigned = assigned_goal_ids(employee, year)
    return [month_summary(employee, year, m, kpis, assigned) for m in range(12)]


def annual_percent(employee, year):
    return scoring.annual_percent(year_summaries(employee, year))


def goal_percent(employee, year, goal):
    kpis = list(goal.kpis.all())
    assigned = assigned_goal_ids(employee, year)
    summaries = year_summaries(employee, year)
    complete = [m for m, s in enumerate(summaries) if s.complete]
    values_by_month = {m: month_values(employee, year, m) for m in complete}
    return scoring.goal_percent(kpis, assigned, values_by_month, complete)


@transaction.atomic
def close_month(employee, year, month, actor):
    """Snapshot every task-derived mark into Score, then mark the month scored.

    This is the fix for the mutable denominator: after this runs, adding or
    reassigning a task cannot change the month's figures.
    """
    if month_is_scored(employee, year, month):
        return None

    kpis = year_kpis(year)
    assigned = assigned_goal_ids(employee, year)
    summary = month_summary(employee, year, month, kpis, assigned)
    if not summary.complete:
        raise ValueError(
            f"{scoring.MONTHS[month]} is not complete for {employee.name} "
            f"({summary.entered} of {summary.slots} scored)."
        )

    values = month_values(employee, year, month, kpis)
    for kpi in scoring.eligible_kpis(kpis, month, assigned):
        if kpi.scoring_mode != scoring.FROM_TASKS:
            continue
        Score.objects.update_or_create(
            employee=employee, kpi=kpi, month_index=month,
            defaults={"value": values[kpi.code], "scored_by": actor,
                      "comment": "Computed from approved tasks at month close."},
        )

    scored = ScoredMonth.objects.create(employee=employee, year=year,
                                        month_index=month, closed_by=actor)
    log_event(actor, "month.close", f"{employee.name} · {scoring.MONTHS[month]}",
              after=f"{summary.percent:.1f}%")
    return scored


@transaction.atomic
def set_score(employee, kpi, month, value, actor, expected_updated_at=None):
    """Manual score write. `expected_updated_at` is the optimistic lock: the grid
    autosaves and may be open in two tabs, so a stale write must fail loudly
    rather than overwrite."""
    if kpi.scoring_mode == scoring.FROM_TASKS:
        raise ValueError(f"{kpi.code} is scored from tasks; edit its tasks instead.")
    if month_is_scored(employee, kpi.goal.year, month):
        raise ValueError(f"{scoring.MONTHS[month]} is already scored. Reopen it to correct.")

    existing = Score.objects.filter(employee=employee, kpi=kpi, month_index=month).first()
    if existing and expected_updated_at and existing.updated_at.isoformat() != expected_updated_at:
        raise ConflictError(existing)

    before = float(existing.value) if existing else None
    score = existing or Score(employee=employee, kpi=kpi, month_index=month)
    score.value = value
    score.scored_by = actor
    score.full_clean()
    score.save()
    log_event(actor, "score.update" if existing else "score.create",
              f"{employee.name} · {kpi.code} · {scoring.MONTHS[month]}",
              before=before, after=float(value))
    return score


@transaction.atomic
def reopen_month(employee, year, month, actor, reason):
    """Unfreeze a scored month so a correction can be made.

    The snapshotted task-derived Scores are deleted, so the KPI goes back to
    computing live from its tasks. Manual marks are left alone — they are what
    the manager typed, and losing them would be a second error.
    """
    if not reason.strip():
        raise ValueError("A reason is required to reopen a scored month.")
    scored = ScoredMonth.objects.filter(employee=employee, year=year, month_index=month).first()
    if scored is None:
        raise ValueError(f"{scoring.MONTHS[month]} is not scored, so there is nothing to reopen.")

    removed = Score.objects.filter(
        employee=employee, kpi__goal__year=year, month_index=month,
        kpi__scoring_mode=scoring.FROM_TASKS).delete()[0]
    scored.delete()
    log_event(actor, "month.reopen", f"{employee.name} · {scoring.MONTHS[month]}",
              before="scored", after="open", reason=reason.strip(),
              snapshots_cleared=removed)


@transaction.atomic
def close_year(year, actor, reason=""):
    year.closed = True
    year.closed_at = timezone.now()
    year.save(update_fields=["closed", "closed_at"])
    log_event(actor, "year.close", year.label, before="open", after="closed",
              reason=reason.strip() or None)
    return year


@transaction.atomic
def reopen_year(year, actor, reason):
    """The escape hatch. Freezing exists to stop history changing silently, not
    to stop it changing at all — so this is allowed, but it leaves a record."""
    if not reason.strip():
        raise ValueError("A reason is required to reopen a closed year.")
    year.closed = False
    year.closed_at = None
    year.save(update_fields=["closed", "closed_at"])
    log_event(actor, "year.reopen", year.label, before="closed", after="open",
              reason=reason.strip())
    return year


class ConflictError(Exception):
    """Someone else saved this cell since it was loaded."""

    def __init__(self, current):
        self.current = current
        super().__init__("This score changed in another tab since you loaded it.")


@transaction.atomic
def submit_update(task, author, note="", new_status=Task.SUBMITTED):
    """The analyst moving their own task along.

    Only "completed" asks for anything: it enters the approval queue. The rungs
    below it record where the work has got to and leave the task with them.
    """
    if new_status not in Task.ANALYST_STATUSES:
        raise ValueError(f"{new_status} is not a status an analyst can set.")
    complete = new_status == Task.SUBMITTED
    before = task.get_status_display()

    update = TaskUpdate.objects.create(
        task=task, author=author, note=note, proposed_status=new_status,
        decision=TaskUpdate.PENDING if complete else TaskUpdate.NOT_NEEDED)
    task.status = new_status
    task.save(update_fields=["status"])
    log_event(author.user, "task.submit" if complete else "task.progress",
              f"{author.name} · {task.title}",
              before=before, after=task.get_status_display())
    return update


@transaction.atomic
def daily_update(task, author, note):
    """A day's note against a task, with no change of status.

    Stored as an ordinary TaskUpdate so it lands in the same trail the manager
    reads — a parallel table would have split one conversation in two.
    """
    if not note.strip():
        raise ValueError("A daily update needs something in it.")
    update = TaskUpdate.objects.create(task=task, author=author, note=note.strip(),
                                       proposed_status="", decision=TaskUpdate.NOT_NEEDED)
    log_event(author.user, "task.daily", f"{author.name} · {task.title}",
              after=task.get_status_display())
    return update


def expected_days(employee, days):
    """The days this analyst actually owed an update: ones where they were
    holding a task that was not yet completed.

    Without this every day before their first task counts as missed, and a grid
    that is red for everybody says nothing about anybody.
    """
    spans = []
    for task in Task.objects.filter(assignee=employee).prefetch_related("updates"):
        start = timezone.localtime(task.created_at).date()
        finished = None
        if task.approved:
            decision = task.updates.filter(decision=TaskUpdate.APPROVED).first()
            finished = (timezone.localtime(decision.decided_at).date()
                        if decision and decision.decided_at else start)
        spans.append((start, finished))
    return {d for d in days
            if any(s <= d and (e is None or d <= e) for s, e in spans)}


def update_days(employee, start, end):
    """The dates this analyst posted anything, between two dates inclusive.

    Local dates, not UTC: an update posted at 9pm in Lagos belongs to that day,
    and the grid is read by people who were there when it happened.
    """
    stamps = (TaskUpdate.objects.filter(author=employee, submitted_at__date__gte=start,
                                        submitted_at__date__lte=end)
              .values_list("submitted_at", flat=True))
    return {timezone.localtime(s).date() for s in stamps}


@transaction.atomic
def decide_update(update, actor, approve, note="", grade=None):
    """The only way a task's status changes. Approving a KPI-linked task moves
    that analyst's live score, which is logged alongside the approval."""
    task = update.task
    before = None
    if task.counts_toward_score:
        before = scoring.task_rollup(task.kpi.max_marks,
                                     task_specs(task.assignee, task.kpi, task.scoring_month))

    update.decision = TaskUpdate.APPROVED if approve else TaskUpdate.RETURNED
    update.decided_by = actor
    update.decided_at = timezone.now()
    update.decision_note = note
    update.save()

    task.status = Task.APPROVED if approve else Task.RETURNED
    # Only for work with no KPI: a KPI task's mark is the rollup, and a second
    # number would contradict it.
    if grade is not None and not task.counts_toward_score:
        task.grade = grade
    task.save(update_fields=["status", "grade"])

    log_event(actor, "task.approve" if approve else "task.return",
              f"{task.assignee.name} · {task.title}",
              before="submitted", after="approved" if approve else "returned",
              reason=note or None,
              grade=task.grade if not task.counts_toward_score else None)

    if task.counts_toward_score:
        after = scoring.task_rollup(task.kpi.max_marks,
                                    task_specs(task.assignee, task.kpi, task.scoring_month))
        if before != after:
            log_event(actor, "score.recompute",
                      f"{task.assignee.name} · {task.kpi.code} · {task.month_label}",
                      before=before, after=after)
    return update


def open_year():
    return AppraisalYear.objects.filter(closed=False).order_by("-start_year").first()


def current_year():
    """The open year, or the most recent one if every year is closed — so the
    app still renders read-only history rather than erroring out."""
    return open_year() or AppraisalYear.objects.order_by("-start_year").first()
