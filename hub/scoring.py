"""Appraisal maths.

Imports nothing from Django on purpose: every rule that decides an analyst's
number is a plain function over plain values, so the golden vectors test it with
no database and no fixtures.

Functions duck-type their inputs. A "kpi" is anything with `code`, `goal_id`,
`max_marks`, `quarterly` and `scoring_mode`; Django model instances and the
KpiSpec below both satisfy that.
"""

from collections import namedtuple

MONTHS = [
    "May-26", "Jun-26", "Jul-26", "Aug-26", "Sep-26", "Oct-26",
    "Nov-26", "Dec-26", "Jan-27", "Feb-27", "Mar-27", "Apr-27",
]

# Jun, Sep, Dec, Mar — the only months a quarterly KPI can be scored in.
QUARTER_END = frozenset({1, 4, 7, 10})

MANUAL = "manual"
FROM_TASKS = "from_tasks"

KpiSpec = namedtuple("KpiSpec", "code goal_id max_marks quarterly scoring_mode")
TaskSpec = namedtuple("TaskSpec", "weight approved")

MonthSummary = namedtuple("MonthSummary", "entered slots raw maximum complete percent")


def is_quarter_end(month):
    return month in QUARTER_END


def eligible(kpi, month, assigned_goal_ids):
    """A KPI counts for an analyst this month only if both hold: the goal is
    assigned to them, and it is not a quarterly KPI outside a quarter end."""
    if kpi.goal_id not in assigned_goal_ids:
        return False
    return not kpi.quarterly or is_quarter_end(month)


def eligible_kpis(kpis, month, assigned_goal_ids):
    return [k for k in kpis if eligible(k, month, assigned_goal_ids)]


def task_rollup(max_marks, tasks):
    """Marks for a `from_tasks` KPI: approved weight over total weight.

    Returns None when there are no tasks — the caller must treat that as "not
    scorable yet", never as zero, or a KPI could quietly stop counting.

    Callers must pass only the tasks for one (assignee, kpi, scoring_month).
    Pooling across analysts would let one person's tasks move another's score.
    """
    tasks = list(tasks)
    if not tasks:
        return None
    total = sum(t.weight for t in tasks)
    if total <= 0:
        return None
    done = sum(t.weight for t in tasks if t.approved)
    return round(max_marks * done / total, 1)


def month_summary(kpis, month, assigned_goal_ids, values):
    """`values` maps kpi.code -> mark (or None). A month is complete only when
    every eligible KPI has a value; partial months show progress instead of a
    misleading percentage."""
    slots = eligible_kpis(kpis, month, assigned_goal_ids)
    marks = [values.get(k.code) for k in slots]
    entered = sum(1 for m in marks if m is not None)
    raw = sum(m for m in marks if m is not None)
    maximum = sum(k.max_marks for k in slots)
    complete = bool(slots) and entered == len(slots)
    percent = (raw / maximum * 100) if complete and maximum else None
    return MonthSummary(entered, len(slots), raw, maximum, complete, percent)


def annual_percent(summaries):
    """Unweighted mean of the complete months. None when nothing is complete."""
    done = [s.percent for s in summaries if s.percent is not None]
    return sum(done) / len(done) if done else None


def goal_percent(goal_kpis, assigned_goal_ids, values_by_month, complete_months):
    """A goal's year-to-date figure, counting complete months only."""
    raw = maximum = 0
    for month in complete_months:
        slots = eligible_kpis(goal_kpis, month, assigned_goal_ids)
        values = values_by_month.get(month, {})
        raw += sum(values.get(k.code) or 0 for k in slots)
        maximum += sum(k.max_marks for k in slots)
    return (raw / maximum * 100) if maximum else None


def band(percent):
    """Shared thresholds so the UI and any export agree."""
    if percent is None:
        return "none"
    if percent >= 90:
        return "good"
    if percent >= 70:
        return "warn"
    return "bad"
