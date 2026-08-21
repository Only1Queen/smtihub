from hub import permissions, services


def hub_context(request):
    """Sidebar needs the role and the approval count on every page."""
    if not request.user.is_authenticated:
        return {}
    from hub.models import Task
    from hub.views import audience
    manager = permissions.is_manager(request.user)
    pending = (Task.objects.filter(status=Task.SUBMITTED).count() if manager else 0)
    me, people = audience(request.user)
    return {
        "is_manager": manager,
        "me": me,
        "pending_count": pending,
        "unread_count": services.unread_count(me, people) if me else 0,
        "open_year": services.open_year(),
    }
