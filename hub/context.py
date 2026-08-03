from hub import permissions, services


def hub_context(request):
    """Sidebar needs the role and the approval count on every page."""
    if not request.user.is_authenticated:
        return {}
    from hub.models import Task
    manager = permissions.is_manager(request.user)
    pending = (Task.objects.filter(status=Task.SUBMITTED).count() if manager else 0)
    return {
        "is_manager": manager,
        "me": permissions.employee_of(request.user),
        "pending_count": pending,
        "open_year": services.open_year(),
    }
