"""The one way audit rows are written.

Every score, goal, task, approval and account change routes through log_event().
When this gets duplicated inline in a view, "is this logged?" stops having a
single answer.
"""

from hub.models import AuditEvent


def log_event(actor, action, target, before=None, after=None, **extra):
    """`actor` is a User (or None for system actions). `before`/`after` are the
    values that changed, so a figure can be traced without anyone's memory."""
    detail = dict(extra)
    if before is not None:
        detail["before"] = before
    if after is not None:
        detail["after"] = after
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=_label(actor),
        action=action,
        target=str(target)[:300],
        detail=detail,
    )


def _label(actor):
    if actor is None or not getattr(actor, "pk", None):
        return "system"
    return actor.get_full_name() or actor.get_username()
