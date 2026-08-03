"""Small helpers so tests read as scenarios, not setup."""

from django.contrib.auth.models import Group, User

from hub import scoring
from hub.models import (AppraisalYear, Employee, Goal, GoalAssignment, Kpi, Task,
                        TaskUpdate)


def make_user(username, name="", manager=False):
    first, _, last = name.partition(" ")
    user = User.objects.create_user(username=username, password="correct-horse-battery",
                                    first_name=first, last_name=last)
    if manager:
        group, _ = Group.objects.get_or_create(name="Manager")
        user.groups.add(group)
    return user


def make_employee(username, name="", manager=None, is_manager=False):
    user = make_user(username, name or username.title(), manager=is_manager)
    return Employee.objects.create(user=user, manager=manager, job_title="Analyst")


def year():
    return AppraisalYear.objects.get(label="FY 2026-27")


def goal(code):
    return Goal.objects.get(year=year(), code=code)


def kpi(code):
    return Kpi.objects.get(goal__year=year(), code=code)


def assign_all(employee, codes=("A", "B", "C", "D", "E")):
    for code in codes:
        GoalAssignment.objects.get_or_create(goal=goal(code), employee=employee)


def make_task(employee, creator, month=0, kpi_obj=None, weight=None, status=Task.NOT_STARTED,
              title="Task"):
    task = Task(year=year(), title=title, assignee=employee, created_by=creator,
                scoring_month=month, kpi=kpi_obj, weight=weight, status=status)
    task.full_clean()
    task.save()
    return task


def fill_month(employee, actor, month, exclude=()):
    """Give every eligible KPI full marks, so the month completes."""
    from hub import services

    assigned = services.assigned_goal_ids(employee, year())
    for k in scoring.eligible_kpis(services.year_kpis(year()), month, assigned):
        if k.code in exclude or k.scoring_mode == scoring.FROM_TASKS:
            continue
        services.set_score(employee, k, month, k.max_marks, actor)
