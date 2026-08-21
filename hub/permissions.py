"""Every access decision. Nothing else in the app should decide who sees what.

Two roles, expressed without a role table:
  manager — in the "Manager" group, or has direct reports
  analyst — everyone else; sees only their own record
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

MANAGER_GROUP = "Manager"


def employee_of(user):
    return getattr(user, "employee", None)


def is_manager(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.groups.filter(name=MANAGER_GROUP).exists():
        return True
    emp = employee_of(user)
    return bool(emp and emp.is_manager)


def manages(user, employee):
    """A manager may act on their own reports. With one team the group alone is
    enough, but this keeps the rule correct when a second manager appears."""
    if not is_manager(user):
        return False
    if user.is_superuser or user.groups.filter(name=MANAGER_GROUP).exists():
        return True
    return employee.manager_id == employee_of(user).pk


def can_view_employee(user, employee):
    return manages(user, employee) or employee_of(user) == employee


def can_score(user, employee):
    return manages(user, employee)


def can_decide_task(user, task):
    """A manager approves their reports' work. Nobody approves their own — that
    would make the approval gate decorative."""
    if employee_of(user) == task.assignee:
        return False
    return manages(user, task.assignee)


def can_submit_update(user, task):
    return employee_of(user) == task.assignee and task.status != task.APPROVED


def can_review_update(user, update):
    """Reviewing somebody's daily update is a manager act on their own report.
    Nobody reviews their own — a self-cleared queue is not a queue."""
    if employee_of(user) == update.author:
        return False
    return manages(user, update.author)


def manager_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_manager(request.user):
            raise PermissionDenied("Manager access required.")
        return view(request, *args, **kwargs)
    return wrapper


def require(condition, message="You do not have access to that."):
    if not condition:
        raise PermissionDenied(message)
