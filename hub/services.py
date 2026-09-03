"""Bridges the ORM to scoring.py.

scoring.py stays free of Django; this module does the fetching and the freezing.
The one rule worth stating twice: a task-derived mark is live only while its
month is open. Closing the month writes the value into Score, after which no
task change can move it.
"""

from datetime import datetime, time, timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from hub import scoring
from hub.audit import log_event
from hub.models import (Announcement, AppraisalYear, Kpi, Leave, Score, ScoredMonth, Task,
                        TaskUpdate, month_is_scored)
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


def open_task_days(employee, days):
    """date -> the tasks this analyst was holding open that day.

    Without this every day before their first task counts as missed, and a grid
    that is red for everybody says nothing about anybody. The tasks come back
    too, so a missed-update notice can name what went unreported.

    Days the analyst was on leave are dropped here rather than in each caller:
    the grid, the missed-update notices and the CSV all route through this, and
    a guard in one of them would leave the other two chasing someone on holiday.
    """
    away = leave_days(employee, days)
    days = [d for d in days if d not in away]
    spans = []
    for task in Task.objects.filter(assignee=employee).prefetch_related("updates"):
        start = timezone.localtime(task.created_at).date()
        finished = None
        if task.approved:
            decision = task.updates.filter(decision=TaskUpdate.APPROVED).first()
            finished = (timezone.localtime(decision.decided_at).date()
                        if decision and decision.decided_at else start)
        spans.append((start, finished, task))
    # The end is exclusive: once the manager confirms a task complete the analyst
    # can no longer post on it, so counting the approval day as owed chases them
    # for an update the app itself refuses to take.
    def open_on(day):
        return [t for s, e, t in spans if s <= day and (e is None or day < e)]

    return {d: open_on(d) for d in days if open_on(d)}


def leave_days(employee, days):
    """Which of these days the analyst was signed off."""
    spans = list(employee.leaves.all())
    return {d for d in days if any(span.covers(d) for span in spans)}


def expected_days(employee, days):
    """The days this analyst actually owed an update."""
    return set(open_task_days(employee, days))


def updates_by_day(employee, start, end):
    """date -> the ids of the tasks this analyst posted about that day.

    Local dates, not UTC: an update posted at 9pm in Lagos belongs to that day,
    and the grid is read by people who were there when it happened.
    """
    out = {}
    for task_id, stamp in (TaskUpdate.objects
                           .filter(author=employee, submitted_at__date__gte=start,
                                   submitted_at__date__lte=end)
                           .values_list("task_id", "submitted_at")):
        out.setdefault(timezone.localtime(stamp).date(), set()).add(task_id)
    return out


def update_days(employee, start, end):
    """The dates this analyst posted anything, between two dates inclusive."""
    return set(updates_by_day(employee, start, end))


def day_coverage(employee, days):
    """date -> (tasks covered by an update, tasks that went unreported).

    A day with three open tasks and one update is not the same day as one with
    three updates, and the grid was calling both of them green.
    """
    owed = open_task_days(employee, days)
    if not owed:
        return {}
    posted = updates_by_day(employee, min(days), max(days))
    out = {}
    for day, tasks in owed.items():
        done = posted.get(day, set())
        out[day] = ([t for t in tasks if t.pk in done], [t for t in tasks if t.pk not in done])
    return out


@transaction.atomic
def start_leave(employee, actor, start=None, reason=""):
    """Mark somebody away. No update is owed until they are back."""
    if employee.on_leave:
        raise ValueError(f"{employee.name} is already marked on leave.")
    leave = Leave(employee=employee, start_date=start or timezone.localdate(),
                  reason=reason.strip(), created_by=actor)
    leave.full_clean()
    leave.save()
    log_event(actor, "leave.start", employee.name, after=str(leave.start_date),
              reason=leave.reason or None)
    return leave


@transaction.atomic
def end_leave(employee, actor, end=None):
    """Back at work: today onwards an update is expected again."""
    leave = employee.leaves.filter(end_date__isnull=True).first()
    if leave is None:
        raise ValueError(f"{employee.name} is not marked on leave.")
    leave.end_date = end or timezone.localdate()
    leave.full_clean()
    leave.save(update_fields=["end_date"])
    log_event(actor, "leave.end", employee.name, before=str(leave.start_date),
              after=str(leave.end_date))
    return leave


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


# How far back the notification feed and the missed-update check look. Anything
# older is history, and history lives on the dashboard, not in a bell.
NOTICE_DAYS = 14


def review_update(update, actor, comment=""):
    """The manager has read this daily update. It leaves their queue; the
    comment, if there is one, goes where the analyst already reads decisions."""
    update.reviewed_at = timezone.now()
    update.decided_by = actor
    update.decision_note = comment.strip()
    update.save(update_fields=["reviewed_at", "decided_by", "decision_note"])
    log_event(actor, "update.reviewed", f"{update.author.name} · {update.task.title}",
              comment=update.decision_note)
    return update


@transaction.atomic
def set_reviewed(updates, actor, reviewed):
    """Mark a batch of daily updates read or unread in one go.

    Un-reviewing keeps the comment: it puts the update back in the queue, it
    does not unsay what the manager wrote.
    """
    count = 0
    for update in updates:
        if reviewed:
            review_update(update, actor, update.decision_note)
        else:
            update.reviewed_at = None
            update.save(update_fields=["reviewed_at"])
        count += 1
    if count and not reviewed:
        log_event(actor, "update.unreviewed", f"{count} daily update(s)")
    return count


def post_announcement(author, title, body):
    if not title.strip() or not body.strip():
        raise ValueError("An announcement needs a title and something to say.")
    item = Announcement.objects.create(author=author, title=title.strip(), body=body.strip())
    log_event(author.user, "announcement.posted", item.title)
    return item


def _end_of(day):
    """A missed day is only missed once it is over, so its notice is stamped at
    the end of it — otherwise today shows as missed all morning."""
    return timezone.make_aware(datetime.combine(day, time(23, 59)))


def missed_updates(people, days):
    """(employee, date, tasks) for every weekday a task was open and nothing was
    said about it.

    Per task, not per day: an analyst holding three tasks who updates one of
    them has still left two unreported, and the old day-level check called that
    a clean day. Weekends and leave are not owed; today is not over yet.
    """
    today = timezone.localdate()
    out = []
    for person in people:
        for day, (_, missed) in day_coverage(person, days).items():
            if day.weekday() >= 5 or day >= today or not missed:
                continue
            out.append((person, day, missed))
    return out


def overdue_tasks(people):
    """(employee, task) for every open task past its due date.

    Anyone signed off is skipped: chasing somebody on leave for a deadline they
    are not at work for is the same noise as chasing them for a daily update.
    """
    today = timezone.localdate()
    working = [p for p in people if not p.on_leave]
    return [(t.assignee, t) for t in
            Task.objects.filter(assignee__in=working, due_date__lt=today)
            .exclude(status=Task.APPROVED)
            .select_related("assignee__user").order_by("due_date")]


def notifications(employee, people):
    """Everything worth telling this person about, newest first.

    Derived on read rather than written into a table: a stored copy is one more
    thing that can disagree with the updates it describes, and there is nothing
    here that cannot be recomputed from what already happened.
    """
    today = timezone.localdate()
    days = [today - timedelta(days=i) for i in range(NOTICE_DAYS - 1, -1, -1)]
    items = []

    for a in Announcement.objects.select_related("author__user")[:20]:
        items.append({"when": a.posted_at, "kind": "announcement",
                      "title": f"Announcement — {a.title}",
                      "text": a.body, "who": a.author.name,
                      "url": reverse("announcements")})

    if employee:
        commented = (TaskUpdate.objects.filter(author=employee, reviewed_at__isnull=False)
                     .exclude(decision_note="").select_related("task", "decided_by")[:40])
        for u in commented:
            items.append({"when": u.reviewed_at, "kind": "comment",
                          "title": f"Comment on your update — {u.task.title}",
                          "text": u.decision_note,
                          "who": (u.decided_by.get_full_name() or u.decided_by.get_username()
                                  if u.decided_by else "Your manager"),
                          "url": reverse("task_detail", args=[u.task_id])})

    # Both sides get this: the analyst who owes the update and the manager who
    # has to chase it. `people` is the audience of whoever is reading — an
    # analyst's own list, a manager's team — so one loop serves both.
    for person, day, tasks in missed_updates(people, days):
        mine = person == employee
        names = ", ".join(t.title for t in tasks[:3])
        items.append({
            "when": _end_of(day), "kind": "missed",
            "title": f"No update on {len(tasks)} task"
                     f"{'' if len(tasks) == 1 else 's'} — {day:%a %-d %b}",
            "text": (f"{'You' if mine else person.name} posted nothing on "
                     f"{names}{' and others' if len(tasks) > 3 else ''} that day."),
            "who": person.name,
            "url": reverse("updates_day", args=[person.pk, day.isoformat()])})

    for person, task in overdue_tasks(people):
        mine = person == employee
        days_late = (today - task.due_date).days
        items.append({
            "when": _end_of(task.due_date), "kind": "overdue",
            "title": f"Overdue — {task.title}",
            "text": (f"Due {task.due_date:%a %-d %b}, {days_late} day"
                     f"{'' if days_late == 1 else 's'} ago, and still "
                     f"{task.get_status_display().lower()}. "
                     f"{'Post an update or send it for review.' if mine else ''}").strip(),
            "who": person.name,
            "url": reverse("task_detail", args=[task.pk])})

    items.sort(key=lambda i: i["when"], reverse=True)
    return items[:60]


def unread_count(employee, people):
    # ponytail: recomputes the whole feed for a badge on every page. Fine for one
    # team; if this ever gets slow, store a per-employee count and bump it on write.
    seen = employee.notifications_seen_at if employee else None
    return len([i for i in notifications(employee, people) if not seen or i["when"] > seen])
