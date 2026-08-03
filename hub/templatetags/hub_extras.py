from django import template

from hub import scoring

register = template.Library()

STATUS_CLASS = {
    "not_started": "st-idle", "in_progress": "st-prog", "submitted": "st-wait",
    "approved": "st-done", "returned": "st-back",
}


@register.filter
def status_class(status):
    return STATUS_CLASS.get(status, "st-idle")


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
