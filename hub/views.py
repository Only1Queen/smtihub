import csv
import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from hub import permissions, scoring, services, signals
from hub.audit import log_event
from hub.forms import (AcknowledgeForm, DecisionForm, EmployeeForm, GoalForm, KpiFormSet,
                       ReasonForm, SetPasswordForm, TaskForm, TaskUpdateForm)
from hub.models import (Announcement, AppraisalYear, AuditEvent, Employee, Goal, Kpi,
                        Score, Task, TaskUpdate, YearAcknowledgement, month_is_scored)
from hub.permissions import manager_required, require


def _year(request):
    year = services.open_year()
    if year is None:
        raise ValidationError("No open appraisal year. Create one in the admin.")
    return year


def _selected_year(request):
    """Closed years stay readable — history is the point of keeping it."""
    requested = request.GET.get("year")
    if requested:
        return get_object_or_404(AppraisalYear, pk=requested)
    year = services.current_year()
    if year is None:
        raise ValidationError("No appraisal year exists. Create one in the admin.")
    return year


def csv_response(name, header, rows):
    """Every export on the site. The data is a team's year — small enough to
    build in one response, so nothing here streams or paginates."""
    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localdate().isoformat()
    response["Content-Disposition"] = f'attachment; filename="{name}-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def team_of(user):
    """The people this manager appraises — her reports, not herself. Without
    this the manager appears in her own team list with no goals and no score."""
    me = permissions.employee_of(user)
    reports = Employee.objects.filter(manager=me).select_related("user")
    if reports.exists():
        return reports
    # A superuser with no reports of their own still needs to see the team.
    return Employee.objects.exclude(pk=getattr(me, "pk", None)).select_related("user")


def _row(employee, year):
    summaries = services.year_summaries(employee, year)
    complete = [i for i, s in enumerate(summaries) if s.complete]
    current = next((i for i in range(12) if i not in complete), 11)
    return {
        "employee": employee,
        "summaries": summaries,
        "annual": scoring.annual_percent(summaries),
        "band": scoring.band(scoring.annual_percent(summaries)),
        "complete": len(complete),
        "current": summaries[current],
        "current_month": current,
    }


# --- Team -------------------------------------------------------------------

@login_required
def home(request):
    """Root URL sends you to your own home.

    Managers get the team; analysts get their own record. Without this an
    analyst is redirected to Team by LOGIN_REDIRECT_URL and meets a 403 the
    moment they sign in.
    """
    if permissions.is_manager(request.user):
        return team(request)
    return redirect("my_appraisal")


@login_required
@manager_required
def team(request):
    year = _year(request)
    rows = [_row(e, year) for e in team_of(request.user)]
    active = [r for r in rows if r["employee"].active]
    done = [r["annual"] for r in active if r["annual"] is not None]
    if request.GET.get("export") == "csv":
        return csv_response(
            f"smti-team-{year.label}",
            ["Analyst", "Job title", "Active"] + scoring.MONTHS
            + ["Year to date", "Band", "Months complete"],
            ([r["employee"].name, r["employee"].job_title,
              "yes" if r["employee"].active else "no"]
             + ["" if s.percent is None else f"{s.percent:.1f}" for s in r["summaries"]]
             + ["" if r["annual"] is None else f"{r['annual']:.1f}", r["band"], r["complete"]]
             for r in rows))
    return render(request, "hub/team.html", {
        "screen": "team",
        "rows": rows,
        "team_average": sum(done) / len(done) if done else None,
        "team_band": scoring.band(sum(done) / len(done) if done else None),
        "year": year,
        "months": scoring.MONTHS,
    })


@login_required
@manager_required
def employee_new(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        emp = form.save(manager=permissions.employee_of(request.user))
        log_event(request.user, "employee.create", f"{emp.name} · {emp.job_title}", after="active")
        _log_role_change(request, form, emp)
        messages.success(request, f"{emp.name} added.")
        return redirect("team")
    return render(request, "hub/employee_form.html", {"form": form, "heading": "Add analyst"})


@login_required
@manager_required
def employee_edit(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    form = EmployeeForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        before = f"{emp.name} · {emp.job_title}"
        emp = form.save()
        log_event(request.user, "employee.update", emp.name,
                  before=before, after=f"{emp.name} · {emp.job_title}")
        _log_role_change(request, form, emp)
        messages.success(request, "Saved.")
        return redirect("team")
    return render(request, "hub/employee_form.html", {"form": form, "heading": "Edit analyst"})


def _log_role_change(request, form, employee):
    """Granting manager is the most privileged thing this app can do. It gets
    its own audit action rather than hiding inside employee.update."""
    if form.role_change:
        log_event(request.user, f"role.{form.role_change}", employee.name,
                  after="Manager" if form.role_change == "granted" else None,
                  before="Manager" if form.role_change == "revoked" else None,
                  source="local")


@login_required
@manager_required
def employee_toggle(request, pk):
    """Deactivate, never delete: a leaver keeps their place in the year's history."""
    emp = get_object_or_404(Employee, pk=pk)
    emp.active = not emp.active
    emp.save(update_fields=["active"])
    emp.user.is_active = emp.active
    emp.user.save(update_fields=["is_active"])
    log_event(request.user, "employee.reactivate" if emp.active else "employee.deactivate",
              emp.name, before="active" if not emp.active else "inactive",
              after="active" if emp.active else "inactive")
    return redirect("team")


# --- Goals ------------------------------------------------------------------

@login_required
@manager_required
def goals(request):
    year = _year(request)
    goal_rows = []
    for goal in year.goals.prefetch_related("kpis", "assignees__user"):
        goal_rows.append({
            "goal": goal,
            "kpis": list(goal.kpis.all()),
            "assigned": list(goal.assignees.all()),
            "marks": goal.total_marks,
            "locked": goal.has_scores(),
        })
    if request.GET.get("export") == "csv":
        return csv_response(
            f"smti-goals-{year.label}",
            ["Goal", "Goal name", "KPI", "KPI text", "Max marks", "Quarterly",
             "Scoring", "Assigned to"],
            ([r["goal"].code, r["goal"].name, k.code, k.text, k.max_marks,
              "yes" if k.quarterly else "no", k.get_scoring_mode_display(),
              _assignment_label(r["goal"])]
             for r in goal_rows for k in r["kpis"]))
    return render(request, "hub/goals.html", {
        "screen": "goals",
        "rows": goal_rows, "year": year,
        "total_marks": sum(r["marks"] for r in goal_rows),
        "auto_count": Kpi.objects.filter(goal__year=year, scoring_mode=scoring.FROM_TASKS).count(),
        "kpi_count": Kpi.objects.filter(goal__year=year).count(),
        "team": team_of(request.user).filter(active=True),
    })


def _assignment_label(goal):
    names = sorted(e.name for e in goal.assignees.all())
    return ", ".join(names) if names else "nobody"


def _next_goal_code(year):
    used = set(year.goals.values_list("code", flat=True))
    return next((c for c in map(chr, range(65, 91)) if c not in used), "Z")


@login_required
@manager_required
def goal_new(request):
    year = _year(request)
    form = GoalForm(request.POST or None, year=year,
                    initial={"code": _next_goal_code(year)},
                    team=team_of(request.user).filter(active=True))
    formset = KpiFormSet(request.POST or None, instance=Goal())
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        goal = form.save()
        formset.instance = goal
        formset.save()
        log_event(request.user, "goal.create", f"{goal.code} · {goal.name}",
                  after=f"{goal.total_marks} marks", assigned_to=_assignment_label(goal))
        messages.success(request, f"Goal {goal.code} created.")
        return redirect("goals")
    return render(request, "hub/goal_form.html",
                  {"form": form, "formset": formset, "heading": "New goal", "year": year})


@login_required
@manager_required
def goal_edit(request, pk):
    goal = get_object_or_404(Goal, pk=pk)
    form = GoalForm(request.POST or None, instance=goal, year=goal.year,
                    team=team_of(request.user).filter(active=True))
    formset = KpiFormSet(request.POST or None, instance=goal)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        before = f"{goal.name} · {goal.total_marks} marks"
        assigned_before = _assignment_label(goal)
        goal = form.save()
        try:
            formset.save()
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return render(request, "hub/goal_form.html",
                          {"form": form, "formset": formset, "heading": f"Edit goal {goal.code}",
                           "year": goal.year, "goal": goal})
        assigned_after = _assignment_label(goal)
        log_event(request.user, "goal.update", f"{goal.code} · {goal.name}",
                  before=before, after=f"{goal.name} · {goal.total_marks} marks",
                  assigned_before=assigned_before, assigned_after=assigned_after,
                  assignment_changed=assigned_before != assigned_after)
        messages.success(request, "Saved.")
        return redirect("goals")
    return render(request, "hub/goal_form.html",
                  {"form": form, "formset": formset, "heading": f"Edit goal {goal.code}",
                   "year": goal.year, "goal": goal})


# --- Tasks ------------------------------------------------------------------

def _task_context(task):
    latest = task.updates.first()
    return {"task": task, "latest": latest}


@login_required
@manager_required
def tasks(request):
    year = _year(request)
    month = int(request.GET.get("month", _current_month(year)))
    qs = Task.objects.filter(year=year).select_related("assignee__user", "kpi__goal")
    pending = [_task_context(t) for t in qs.filter(status=Task.SUBMITTED)]
    rest = [_task_context(t) for t in qs.filter(scoring_month=month).exclude(status=Task.SUBMITTED)]

    # Grouped by analyst: the flat list answered "what is outstanding" but never
    # "what is this person carrying", which is the question in a one-to-one.
    by_person = {}
    for item in rest:
        by_person.setdefault(item["task"].assignee, []).append(item)
    people = [{"employee": e, "items": items,
               "open": len([i for i in items if not i["task"].approved])}
              for e, items in sorted(by_person.items(), key=lambda kv: kv[0].name)]

    if request.GET.get("export") == "csv":
        # The whole year, not the month on screen: a spreadsheet of one month is
        # a screenshot, and the question asked of an export is always "so far".
        return csv_response(
            f"smti-tasks-{year.label}",
            ["Task", "Analyst", "Status", "Scoring month", "Due", "KPI", "Goal",
             "Weight %", "Grade", "Created", "Updates", "Last update"],
            ([t.title, t.assignee.name, t.get_status_display(), t.month_label,
              t.due_date or "", t.kpi.code if t.kpi else "",
              t.kpi.goal.code if t.kpi else "", t.weight or "",
              "" if t.grade is None else t.grade,
              timezone.localtime(t.created_at).date(), len(t.updates.all()),
              (timezone.localtime(t.updates.all()[0].submitted_at)
               .strftime("%Y-%m-%d %H:%M") if t.updates.all() else "")]
             for t in qs.prefetch_related("updates")))
    return render(request, "hub/tasks.html", {
        "screen": "tasks",
        "pending": pending, "people": people, "month": month,
        "months": list(enumerate(scoring.MONTHS)), "year": year,
        "linked": qs.filter(scoring_month=month, kpi__isnull=False).count(),
        "open_count": qs.filter(scoring_month=month).exclude(status=Task.APPROVED).count(),
        "month_total": qs.filter(scoring_month=month).count(),
    })


def _current_month(year):
    scored = set()
    for emp in Employee.objects.filter(active=True):
        scored |= {m for m in range(12) if month_is_scored(emp, year, m)}
    return min((m for m in range(12) if m not in scored), default=11)


@login_required
@manager_required
def task_new(request):
    year = _year(request)
    form = TaskForm(request.POST or None, year=year,
                    initial={"scoring_month": _current_month(year)})
    if request.method == "POST" and form.is_valid():
        try:
            created = form.save(created_by=request.user)
        except ValidationError as exc:
            for msg in exc.messages:
                form.add_error(None, msg)
        else:
            # One audit row per analyst: each of these is a separate task with
            # its own approval and its own marks.
            for task in created:
                log_event(request.user, "task.create", f"{task.assignee.name} · {task.title}",
                          after=f"{task.kpi.code} · {task.weight}%" if task.kpi else "operational")
            messages.success(request, f"{len(created)} task{'' if len(created) == 1 else 's'} created.")
            return redirect("tasks")
    return render(request, "hub/task_form.html", {"form": form, "year": year})


@login_required
def task_detail(request, pk):
    """Everything about one task on one page: what it is, where it is, the whole
    trail, and the box the analyst drops a daily update into."""
    task = get_object_or_404(Task.objects.select_related("assignee__user", "kpi__goal"), pk=pk)
    mine = permissions.can_submit_update(request.user, task)
    require(mine or permissions.can_decide_task(request.user, task)
            or permissions.can_view_employee(request.user, task.assignee),
            "That task is not yours to read.")

    if request.method == "POST":
        require(mine, "Only the analyst who owns a task can post updates on it.")
        try:
            services.daily_update(task, permissions.employee_of(request.user),
                                  request.POST.get("note", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Daily update posted.")
        return redirect("task_detail", pk=task.pk)

    return render(request, "hub/task_detail.html", {
        "task": task, "trail": list(task.updates.all()), "mine": mine,
        "can_decide": permissions.can_decide_task(request.user, task),
        "screen": "mytasks" if mine else "tasks",
        "overdue": bool(task.due_date and task.due_date < timezone.localdate()
                        and not task.approved),
    })


DASHBOARD_DAYS = 28


def audience(user):
    """(me, the people whose updates I see) — a manager's active team plus
    herself, or an analyst on their own. Every update screen scopes on this."""
    me = permissions.employee_of(user)
    if not permissions.is_manager(user):
        return me, ([me] if me else [])
    people = list(team_of(user).filter(active=True))
    if me and me not in people:
        people = [me] + people
    return me, people


@login_required
def updates_dashboard(request):
    """Who posted an update on which day, what they said, and the box an analyst
    drops today's update into. Managers see the team, analysts see themselves.
    Weekends are marked, not counted as missed."""
    me, people = audience(request.user)

    # Only tasks assigned to me, still open: goals are not updated here, and a
    # completed task has nothing left to report.
    my_open = list(Task.objects.filter(assignee=me).exclude(status=Task.APPROVED)
                   .select_related("kpi__goal")) if me else []

    if request.method == "POST" and request.POST.get("review"):
        update = get_object_or_404(TaskUpdate, pk=request.POST["review"])
        require(permissions.can_review_update(request.user, update),
                "You can only review your own team's updates.")
        services.review_update(update, request.user, request.POST.get("comment", ""))
        messages.success(request, f"Reviewed {update.author.name}'s update.")
        # A name, never a URL from the form: taking a redirect target from POST
        # is how a login page ends up bouncing people off-site.
        return redirect("update_comments" if request.POST.get("from") == "comments"
                        else "updates_dashboard")

    if request.method == "POST":
        task = get_object_or_404(Task, pk=request.POST.get("task"))
        require(permissions.can_submit_update(request.user, task),
                "You can only post updates on your own open tasks.")
        try:
            services.daily_update(task, me, request.POST.get("note", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Daily update posted.")
        return redirect("updates_dashboard")

    today = timezone.localdate()
    start = today - timedelta(days=DASHBOARD_DAYS - 1)
    days = [start + timedelta(days=i) for i in range(DASHBOARD_DAYS)]

    rows = []
    for person in people:
        posted = services.update_days(person, start, today)
        owed = services.expected_days(person, days)
        cells = []
        for d in days:
            weekend = d.weekday() >= 5
            expected = d in owed and not weekend
            cells.append({
                "date": d, "posted": d in posted, "weekend": weekend,
                "today": d == today, "expected": expected,
                "missed": expected and d not in posted,
                "idle": not weekend and d not in owed,
            })
        rows.append({
            "employee": person, "cells": cells,
            "posted": len([c for c in cells if c["posted"]]),
            "missed": len([c for c in cells if c["missed"]]),
            "expected": len([c for c in cells if c["expected"]]),
        })

    # A manager's feed is a queue: reviewed updates leave it, so what is left is
    # what still wants reading. Their own posts are not theirs to review, so they
    # would never leave — an analyst's own feed keeps everything.
    feed = (TaskUpdate.objects.filter(author__in=people, decision=TaskUpdate.NOT_NEEDED)
            .select_related("task", "author__user", "decided_by"))
    manager = permissions.is_manager(request.user)
    if manager:
        feed = feed.filter(reviewed_at__isnull=True).exclude(author=me)
    who = request.GET.get("who")
    if who:
        feed = feed.filter(author_id=who)

    export = request.GET.get("export")
    if export == "csv":
        return csv_response(
            "smti-update-coverage",
            ["Analyst", "Date", "Weekday", "State"],
            ([r["employee"].name, c["date"], c["date"].strftime("%A"),
              "weekend" if c["weekend"] else "posted" if c["posted"]
              else "no open task" if c["idle"] else "missed"]
             for r in rows for c in r["cells"]))
    if export == "notes":
        return csv_response(
            "smti-daily-updates",
            ["Date", "Time", "Analyst", "Task", "Update", "Reviewed", "Manager comment"],
            ([timezone.localtime(u.submitted_at).date(),
              timezone.localtime(u.submitted_at).strftime("%H:%M"),
              u.author.name, u.task.title, u.note,
              timezone.localtime(u.reviewed_at).strftime("%Y-%m-%d %H:%M") if u.reviewed_at else "",
              u.manager_comment]
             for u in feed))

    # The screen shows a readable page of them; the export is the lot.
    feed = list(feed[:80])
    return render(request, "hub/updates_dashboard.html", {
        "screen": "updates", "rows": rows, "days": days, "feed": feed, "who": who,
        "can_review": manager,
        "people": people, "my_open": my_open, "me": me,
        "start": start, "today": today, "day_count": DASHBOARD_DAYS,
    })


@login_required
@manager_required
def update_comments(request):
    """Every comment the manager has left on a daily update, in one place.

    The dashboard queue empties as updates are reviewed, which is the point of
    it — this is where what was said goes on living.
    """
    me, people = audience(request.user)
    comments = (TaskUpdate.objects.filter(author__in=people, reviewed_at__isnull=False)
                .exclude(decision_note="")
                .select_related("task", "author__user", "decided_by")
                .order_by("-reviewed_at"))
    who = request.GET.get("who")
    if who:
        comments = comments.filter(author_id=who)

    if request.GET.get("export") == "csv":
        return csv_response(
            "smti-update-comments",
            ["Reviewed", "Analyst", "Task", "Update", "Manager", "Comment"],
            ([timezone.localtime(c.reviewed_at).strftime("%Y-%m-%d %H:%M"), c.author.name,
              c.task.title, c.note,
              c.decided_by.get_full_name() or c.decided_by.get_username() if c.decided_by else "",
              c.decision_note]
             for c in comments))

    return render(request, "hub/update_comments.html", {
        "screen": "comments", "comments": list(comments[:200]), "people": people, "who": who,
    })


@login_required
def announcements(request):
    """The noticeboard. Managers post, everyone reads."""
    manager = permissions.is_manager(request.user)
    me = permissions.employee_of(request.user)

    if request.method == "POST":
        require(manager, "Only a manager can post an announcement.")
        try:
            services.post_announcement(me, request.POST.get("title", ""),
                                       request.POST.get("body", ""))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Announcement posted.")
        return redirect("announcements")

    return render(request, "hub/announcements.html", {
        "screen": "announcements", "can_post": manager,
        "items": list(Announcement.objects.select_related("author__user")[:50]),
    })


@login_required
def notifications(request):
    """One list: announcements, comments on your updates, and days an update was
    owed and never came. Opening the page is what marks them read."""
    me, people = audience(request.user)
    items = services.notifications(me, people)
    seen = me.notifications_seen_at if me else None
    for item in items:
        item["unread"] = not seen or item["when"] > seen
    if me:
        me.notifications_seen_at = timezone.now()
        me.save(update_fields=["notifications_seen_at"])
    return render(request, "hub/notifications.html", {
        "screen": "notifications", "items": items, "days": services.NOTICE_DAYS,
    })


@login_required
def task_submit(request, pk):
    task = get_object_or_404(Task, pk=pk)
    require(permissions.can_submit_update(request.user, task),
            "You can only submit updates on your own tasks.")
    form = TaskUpdateForm(request.POST or None, task=task)
    if request.method == "POST" and form.is_valid():
        services.submit_update(task, permissions.employee_of(request.user),
                               form.cleaned_data["note"], form.cleaned_data["status"])
        messages.success(request, "Sent to your manager to review and grade." if form.complete
                         else f"Marked {form.cleaned_data['status'].replace('_', ' ')}.")
        return redirect("my_tasks")
    return render(request, "hub/task_submit.html", {"form": form, "task": task})


@login_required
def task_decide(request, pk):
    task = get_object_or_404(Task, pk=pk)
    require(permissions.can_decide_task(request.user, task),
            "You cannot approve this task.")
    update = task.updates.filter(decision=TaskUpdate.PENDING).first()
    if update is None:
        return HttpResponseBadRequest("Nothing awaiting a decision on this task.")
    linked = task.counts_toward_score
    # The whole trail, not just the completion note: how the work went is the
    # thing being graded, and it is spread across the progress updates.
    ctx = {"task": task, "update": update, "trail": list(task.updates.all())}
    if request.method != "POST":
        return render(request, "hub/task_decide.html",
                      dict(ctx, form=DecisionForm(kpi_linked=linked)))
    approve = request.POST.get("decision") == "approve"
    form = DecisionForm(request.POST, kpi_linked=linked)
    # A mark of 500 used to be dropped in silence; now the manager sees why.
    if not form.is_valid():
        return render(request, "hub/task_decide.html", dict(ctx, form=form))
    services.decide_update(update, request.user, approve, form.cleaned_data.get("note", ""),
                           grade=form.cleaned_data.get("grade"))
    messages.success(request, "Approved." if approve else "Sent back.")
    return redirect("tasks")


# --- Scoring ----------------------------------------------------------------

def _entry_context(employee, year, month):
    kpis = services.year_kpis(year)
    assigned = services.assigned_goal_ids(employee, year)
    values = services.month_values(employee, year, month, kpis)
    frozen = month_is_scored(employee, year, month)

    groups = []
    for goal in year.goals.prefetch_related("kpis"):
        rows = []
        for kpi in goal.kpis.all():
            eligible = scoring.eligible(kpi, month, assigned)
            auto = kpi.scoring_mode == scoring.FROM_TASKS
            related = list(Task.objects.filter(assignee=employee, kpi=kpi, scoring_month=month)) if auto else []
            approved = sum(t.weight for t in related if t.approved)
            total = sum(t.weight for t in related)
            rows.append({
                "kpi": kpi, "eligible": eligible, "auto": auto, "value": values.get(kpi.code),
                "tasks": related, "approved_weight": approved, "total_weight": total,
                "reason": ("" if eligible else
                           "quarterly" if kpi.quarterly else "not assigned"),
            })
        if any(r["eligible"] for r in rows):
            groups.append({"goal": goal, "rows": rows,
                           "marks": sum(r["kpi"].max_marks for r in rows if r["eligible"])})

    summary = scoring.month_summary(kpis, month, assigned, values)
    return {"employee": employee, "year": year, "month": month, "groups": groups,
            "summary": summary, "frozen": frozen,
            "months": list(enumerate(scoring.MONTHS)),
            "quarter_end": scoring.is_quarter_end(month),
            "complete_months": [m for m in range(12)
                                if services.month_summary(employee, year, m, kpis, assigned).complete]}


def _year_grid_context(employee, year):
    """Every KPI against every month, for one analyst.

    The month view stays the place a month is closed; this is the same data
    laid flat so a whole year can be typed in one pass.
    """
    kpis = services.year_kpis(year)
    assigned = services.assigned_goal_ids(employee, year)
    values = {m: services.month_values(employee, year, m, kpis) for m in range(12)}
    frozen = {m for m in range(12) if month_is_scored(employee, year, m)}
    summaries = services.year_summaries(employee, year)

    groups = []
    for goal in year.goals.prefetch_related("kpis", "assignees__user"):
        # Every goal is listed, including ones this analyst is not on: a goal
        # missing from the sheet with no explanation reads as a broken page.
        applies = goal.pk in assigned
        rows = []
        for kpi in goal.kpis.all():
            auto = kpi.scoring_mode == scoring.FROM_TASKS
            cells = []
            for m in range(12):
                eligible = applies and scoring.eligible(kpi, m, assigned)
                cells.append({
                    "month": m,
                    "eligible": eligible,
                    # Task-derived marks are never typed: they come from approvals.
                    "editable": eligible and not auto and m not in frozen and not year.closed,
                    "frozen": m in frozen,
                    "value": values[m].get(kpi.code),
                })
            rows.append({"kpi": kpi, "cells": cells, "auto": auto})
        pct = services.goal_percent(employee, year, goal) if applies else None
        groups.append({"goal": goal, "rows": rows, "percent": pct, "band": scoring.band(pct),
                       "marks": goal.total_marks, "applies": applies,
                       "assigned_to": ", ".join(sorted(e.name for e in goal.assignees.all()))})

    annual = scoring.annual_percent(summaries)
    return {
        "employee": employee, "year": year, "groups": groups,
        "months": list(enumerate(scoring.MONTHS)),
        "month_cols": [{"index": m, "label": scoring.MONTHS[m],
                        "quarter_end": scoring.is_quarter_end(m),
                        "frozen": m in frozen, "summary": summaries[m],
                        "band": scoring.band(summaries[m].percent)} for m in range(12)],
        "annual": annual, "annual_band": scoring.band(annual),
        "complete": len([s for s in summaries if s.complete]),
        "applied": len([g for g in groups if g["applies"]]),
    }


@login_required
def score_year(request, employee_id):
    employee = get_object_or_404(Employee, pk=employee_id)
    require(permissions.can_score(request.user, employee))
    ctx = _year_grid_context(employee, _year(request))
    ctx["screen"] = "team"
    return render(request, "hub/score_year.html", ctx)


@login_required
def score_entry(request, employee_id, month):
    employee = get_object_or_404(Employee, pk=employee_id)
    require(permissions.can_score(request.user, employee))
    ctx = _entry_context(employee, _year(request), month)
    ctx["screen"] = "entry"
    return render(request, "hub/score_entry.html", ctx)


@login_required
def score_save(request, employee_id, month):
    """One cell at a time — the grid autosaves, so a stale write must fail loudly
    rather than overwrite whatever the other tab saved."""
    employee = get_object_or_404(Employee, pk=employee_id)
    require(permissions.can_score(request.user, employee))
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    kpi = get_object_or_404(Kpi, pk=request.POST.get("kpi"), goal__year=_year(request))
    raw = request.POST.get("value", "").strip()
    try:
        if raw == "":
            Score.objects.filter(employee=employee, kpi=kpi, month_index=month).delete()
            log_event(request.user, "score.clear",
                      f"{employee.name} · {kpi.code} · {scoring.MONTHS[month]}")
        else:
            services.set_score(employee, kpi, month, float(raw), request.user,
                               request.POST.get("updated_at") or None)
    except services.ConflictError as exc:
        return HttpResponse(
            f'<span class="err">Changed in another tab — now {exc.current.value}. Reload.</span>',
            status=409)
    except (ValidationError, ValueError) as exc:
        msgs = exc.messages if isinstance(exc, ValidationError) else [str(exc)]
        return HttpResponse(f'<span class="err">{"; ".join(msgs)}</span>', status=400)

    ctx = _entry_context(employee, _year(request), month)
    return render(request, "hub/_saveline.html", ctx)


@login_required
def month_close(request, employee_id, month):
    employee = get_object_or_404(Employee, pk=employee_id)
    require(permissions.can_score(request.user, employee))
    try:
        services.close_month(employee, _year(request), month, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{scoring.MONTHS[month]} scored and frozen for {employee.name}.")
    return redirect("score_entry", employee_id=employee.pk, month=month)


# --- Analyst views ----------------------------------------------------------

@login_required
def my_appraisal(request, employee_id=None):
    year = _selected_year(request)
    employee = (get_object_or_404(Employee, pk=employee_id) if employee_id
                else permissions.employee_of(request.user))
    if employee is None:
        raise ValidationError("Your account is not linked to an employee record.")
    require(permissions.can_view_employee(request.user, employee))

    kpis = services.year_kpis(year)
    assigned = services.assigned_goal_ids(employee, year)
    summaries = services.year_summaries(employee, year)
    complete = [m for m, s in enumerate(summaries) if s.complete]
    values_by_month = {m: services.month_values(employee, year, m, kpis) for m in range(12)}

    # The month currently being worked: the first that is not yet complete.
    open_month = next((m for m in range(12) if m not in complete), 11)

    goal_rows = []
    for goal in year.goals.prefetch_related("kpis"):
        if goal.pk not in assigned:
            continue
        kpi_rows = []
        for kpi in goal.kpis.all():
            cells, raw, mx = [], 0, 0
            for m in range(12):
                if not scoring.eligible(kpi, m, assigned):
                    cells.append({"state": "na"})
                    continue
                v = values_by_month[m].get(kpi.code)
                if v is None:
                    cells.append({"state": "empty"})
                    continue
                if m in complete:
                    raw += v
                    mx += kpi.max_marks
                cells.append({"state": "value", "value": v,
                              "band": scoring.band(v / kpi.max_marks * 100), "month": scoring.MONTHS[m]})
            kpi_rows.append({
                "kpi": kpi, "cells": cells,
                "percent": (raw / mx * 100) if mx else None,
                "band": scoring.band((raw / mx * 100) if mx else None),
                "tasks": (list(Task.objects.filter(assignee=employee, kpi=kpi,
                                                   scoring_month=open_month))
                          if kpi.scoring_mode == scoring.FROM_TASKS else []),
                "task_month": scoring.MONTHS[open_month],
            })
        pct = services.goal_percent(employee, year, goal)
        goal_rows.append({"goal": goal, "kpis": kpi_rows, "percent": pct,
                          "band": scoring.band(pct), "scored": len(complete)})

    annual = scoring.annual_percent(summaries)
    chart = [{"month": scoring.MONTHS[m], "short": scoring.MONTHS[m][:3],
              "percent": summaries[m].percent,
              "band": scoring.band(summaries[m].percent)} for m in range(12)]
    return render(request, "hub/my_appraisal.html", {
        "employee": employee, "year": year, "years": AppraisalYear.objects.all(),
        "screen": "mine", "goal_rows": goal_rows, "annual": annual,
        "annual_band": scoring.band(annual), "summaries": summaries, "chart": chart,
        "months": scoring.MONTHS, "complete": len(complete),
        "best": max((c["percent"] for c in chart if c["percent"] is not None), default=None),
        "readonly": not permissions.can_score(request.user, employee),
        "acknowledgement": YearAcknowledgement.objects.filter(employee=employee, year=year).first(),
        # Only your own record is yours to sign.
        "can_acknowledge": year.closed and permissions.employee_of(request.user) == employee,
    })


@login_required
def my_tasks(request):
    me = permissions.employee_of(request.user)
    if me is None:
        raise ValidationError("Your account is not linked to an employee record.")
    year = _year(request)
    today = timezone.localdate()
    qs = (Task.objects.filter(assignee=me, year=year)
          .select_related("kpi__goal").prefetch_related("updates"))

    def rows(queryset):
        out = []
        for task in queryset:
            item = _task_context(task)
            item["overdue"] = bool(task.due_date and task.due_date < today and not task.approved)
            item["updates"] = len(task.updates.all())
            out.append(item)
        return out

    open_tasks = rows(qs.exclude(status=Task.APPROVED))
    # Soonest due first, undated last: what is late belongs at the top, not
    # wherever the title happened to sort it.
    open_tasks.sort(key=lambda i: (i["task"].due_date is None, i["task"].due_date or today))
    done_tasks = rows(qs.filter(status=Task.APPROVED))
    graded = [i["task"].grade for i in done_tasks if i["task"].grade is not None]

    return render(request, "hub/my_tasks.html", {
        "screen": "mytasks",
        "open_tasks": open_tasks, "done_tasks": done_tasks, "me": me, "year": year,
        "waiting": len([i for i in open_tasks if i["task"].status == Task.SUBMITTED]),
        "overdue": len([i for i in open_tasks if i["overdue"]]),
        "average_grade": sum(graded) / len(graded) if graded else None,
    })


# --- Reporting --------------------------------------------------------------

@login_required
@manager_required
def year_summary(request):
    year = _selected_year(request)
    rows = [_row(e, year) for e in team_of(request.user)]
    for row in rows:
        row["cells"] = [{
            "percent": s.percent, "band": scoring.band(s.percent),
            "partial": not s.complete and s.entered > 0,
        } for s in row["summaries"]]

    if request.GET.get("export") == "csv":
        return _csv(rows, year)
    return render(request, "hub/year_summary.html", {
        "rows": rows, "months": scoring.MONTHS, "year": year,
        "years": AppraisalYear.objects.all(), "screen": "summary",
    })


def _csv(rows, year):
    """Small enough to stream synchronously — five analysts by twelve months."""
    return csv_response(
        f"smti-{year.label}", ["Analyst"] + scoring.MONTHS + ["Year to date"],
        ([row["employee"].name]
         + [("" if c["percent"] is None else f"{c['percent']:.1f}") for c in row["cells"]]
         + ["" if row["annual"] is None else f"{row['annual']:.1f}"]
         for row in rows))


@login_required
@manager_required
def activity(request):
    events = AuditEvent.objects.all()
    action = request.GET.get("action", "")
    who = request.GET.get("q", "")
    if action:
        events = events.filter(action=action)
    if who:
        events = events.filter(Q(target__icontains=who) | Q(actor_label__icontains=who))

    if request.GET.get("export") == "csv":
        return _audit_csv(events)

    # Paginated rather than sliced at 200: the old cap silently hid everything
    # older, which is the opposite of what a permanent record is for.
    page = Paginator(events, 100).get_page(request.GET.get("page"))
    actions = AuditEvent.objects.values_list("action", flat=True).distinct().order_by("action")
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "hub/activity.html", {
        "screen": "activity",
        "events": page, "page": page, "actions": actions, "action": action, "q": who,
        "query": query.urlencode(),
    })


def _audit_csv(events):
    """The whole filtered set, not the page — an auditor asking for "everything
    on this account" should not have to click through pages to get it."""
    def rows():
        for e in events.iterator(chunk_size=500):
            detail = {k: v for k, v in e.detail.items() if k not in {"before", "after"}}
            yield [e.timestamp.isoformat(timespec="seconds"), e.actor_label, e.action,
                   e.target, e.detail.get("before", ""), e.detail.get("after", ""),
                   json.dumps(detail, default=str) if detail else ""]

    return csv_response("smti-activity",
                        ["Timestamp", "Actor", "Action", "Target", "Before", "After", "Detail"],
                        rows())


@login_required
def settings_page(request):
    me = permissions.employee_of(request.user)
    return render(request, "hub/settings.html", {
        "year": services.current_year(),
        "years": AppraisalYear.objects.all(),
        "screen": "settings",
        # An AD account has no password here to change; sending them to a form
        # that cannot work is worse than saying so.
        "ad_account": request.user.groups.filter(name=signals.LDAP_GROUP).exists(),
        "acknowledgements": (YearAcknowledgement.objects.filter(employee=me)
                             .select_related("year") if me else []),
    })


# --- Acknowledgement --------------------------------------------------------

@login_required
def year_acknowledge(request, year_id):
    """The analyst signs off their own closed year. Nobody can do this for
    somebody else — an acknowledgement recorded by a manager would be worth
    nothing, so there is no route that accepts an employee id."""
    year = get_object_or_404(AppraisalYear, pk=year_id)
    me = permissions.employee_of(request.user)
    if me is None:
        raise ValidationError("Your account is not linked to an employee record.")
    if not year.closed:
        messages.error(request, f"{year.label} is still open. There is nothing final to sign yet.")
        return redirect("my_appraisal")
    if YearAcknowledgement.objects.filter(employee=me, year=year).exists():
        messages.error(request, f"You have already acknowledged {year.label}.")
        return redirect("my_appraisal")

    annual = scoring.annual_percent(services.year_summaries(me, year))
    form = AcknowledgeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        ack = YearAcknowledgement.objects.create(
            employee=me, year=year, comment=form.cleaned_data["comment"],
            annual_percent=None if annual is None else round(annual, 1))
        log_event(request.user, "year.acknowledged", f"{me.name} · {year.label}",
                  after=None if annual is None else f"{annual:.1f}%",
                  comment=ack.comment)
        messages.success(request, f"{year.label} acknowledged. Thank you.")
        return redirect("my_appraisal")

    return render(request, "hub/acknowledge.html", {
        "form": form, "year": year, "employee": me, "annual": annual,
        "band": scoring.band(annual), "screen": "mine",
    })


# --- Health -----------------------------------------------------------------

def healthz(request):
    """For the container healthcheck. It touches the database on purpose: a web
    process that cannot reach Postgres serves nothing but 500s, and a check that
    only proves gunicorn is listening would call that healthy."""
    try:
        connection.ensure_connection()
    except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
        return HttpResponse(f"db unavailable: {exc.__class__.__name__}", status=503,
                            content_type="text/plain")
    return HttpResponse("ok", content_type="text/plain")


# --- Freeze escape hatches --------------------------------------------------

@login_required
@manager_required
def month_reopen(request, employee_id, month):
    """Unfreeze a scored month. Allowed, but it leaves a record."""
    employee = get_object_or_404(Employee, pk=employee_id)
    require(permissions.can_score(request.user, employee))
    year = _year(request)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reopen_month(employee, year, month, request.user,
                                  form.cleaned_data["reason"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{scoring.MONTHS[month]} reopened for corrections.")
            return redirect("score_entry", employee_id=employee.pk, month=month)
    return render(request, "hub/reason_form.html", {
        "form": form, "heading": f"Reopen {scoring.MONTHS[month]}",
        "lede": f"{employee.name} · {scoring.MONTHS[month]}",
        "warning": "Task-derived marks will go back to computing live from their tasks. "
                   "Manual marks are kept. The reopen is recorded permanently.",
        "cancel_url": reverse("score_entry", args=[employee.pk, month]),
        "screen": "team",
    })


@login_required
@manager_required
def year_close(request, pk):
    year = get_object_or_404(AppraisalYear, pk=pk)
    if year.closed:
        messages.error(request, f"{year.label} is already closed.")
        return redirect("settings")
    if request.method == "POST":
        services.close_year(year, request.user)
        messages.success(request, f"{year.label} closed. Every score, goal and task is frozen.")
        return redirect("settings")
    return render(request, "hub/confirm.html", {
        "heading": f"Close {year.label}",
        "lede": "This freezes every score, goal, KPI, assignment and task in the year.",
        "warning": "It can be reopened later for a correction, but that is audited and needs "
                   "a reason. Take a backup first.",
        "action": reverse("year_close", args=[year.pk]),
        "confirm_label": "Close the year",
        "cancel_url": reverse("settings"),
        "screen": "settings",
    })


@login_required
@manager_required
def year_reopen(request, pk):
    year = get_object_or_404(AppraisalYear, pk=pk)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.reopen_year(year, request.user, form.cleaned_data["reason"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{year.label} reopened.")
            return redirect("settings")
    return render(request, "hub/reason_form.html", {
        "form": form, "heading": f"Reopen {year.label}",
        "lede": "Freezing exists to stop history changing silently, not to stop it changing.",
        "warning": "The reason below is recorded permanently and appears in the activity log "
                   "beside the reopen.",
        "cancel_url": reverse("settings"),
        "screen": "settings",
    })


@login_required
@manager_required
def employee_password(request, pk):
    """Set an analyst's password. Without this, adding someone through the UI
    leaves them unable to sign in until somebody reaches for a shell."""
    employee = get_object_or_404(Employee, pk=pk)
    form = SetPasswordForm(request.POST or None, user=employee.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_event(request.user, "account.password_set", employee.name)
        messages.success(request, f"Password set for {employee.name}.")
        return redirect("team")
    return render(request, "hub/employee_form.html", {
        "form": form, "heading": f"Set password — {employee.name}",
        "lede": "They sign in with their username and this password.",
        "screen": "team",
    })
