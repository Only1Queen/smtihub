from django import template

from hub import scoring

register = template.Library()

STATUS_CLASS = {
    "not_started": "st-idle", "picked_up": "st-pick", "in_progress": "st-prog",
    "on_track": "st-near", "submitted": "st-wait", "approved": "st-done",
    "returned": "st-back",
    "": "st-idle",  # a daily update, which moves nothing
}


@register.filter
def status_class(status):
    return STATUS_CLASS.get(status, "st-idle")


@register.filter
def update_label(proposed_status):
    """What the analyst did, not what it left the task waiting for. "Awaiting
    review" on their own update line reads as somebody else's state."""
    from hub.models import Task

    if not proposed_status:
        return "Daily update"
    if proposed_status == Task.SUBMITTED:
        return "Sent for review"
    return dict(Task.STATUS_CHOICES).get(proposed_status, proposed_status)


@register.filter
def band_class(percent):
    """Same thresholds the scoring module uses, so UI and exports agree."""
    return "s-" + scoring.band(percent)


@register.filter
def index(sequence, i):
    try:
        return sequence[i]
    except (IndexError, TypeError, KeyError):
        return None


@register.filter
def mul(value, arg):
    return int(value) * int(arg)


# Chart geometry: 0% sits on the baseline at y=150, 100% at y=10.
@register.filter
def bar_y(percent):
    return round(150 - (float(percent) / 100) * 140, 1)


@register.filter
def bar_h(percent):
    return round((float(percent) / 100) * 140, 1)
