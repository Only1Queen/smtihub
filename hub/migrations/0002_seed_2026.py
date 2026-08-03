"""Seeds FY 2026-27 with the SMTI goals.

These arrive as ordinary rows, not a fixed template: the manager can rename,
reweight, reassign or add to them from the Goals screen. Never edit this file to
change the structure — that would rewrite history. Add a new migration, or use
the Goals screen.

Marks: A-E are 20 each, 100 total. B2 and C3 are quarterly (Jun/Sep/Dec/Mar).
"""

from django.db import migrations

GOALS = [
    ("A", "Operational Tasks",
     "Day-to-day delivery: assigned work completed on time, and blockers raised early "
     "enough to act on.", [
         ("A1", "Timely and efficient completion of assigned tasks", 10, False),
         ("A2", "Maintain task completion rate of 99% within agreed timelines", 5, False),
         ("A3", "Prompt escalation of issues or blockers affecting task delivery", 5, False),
     ]),
    ("B", "Threat Intelligence Dissemination",
     "Intelligence reaches the people who can act on it, with validated indicators and a "
     "clear recommendation.", [
         ("B1", "Threat intel reports disseminated (min. 2/month)", 10, False),
         ("B2", "New detection rules deployed based on threat intel insights", 5, True),
         ("B3", "Shared intel includes validated IOCs and actionable recommendations", 5, False),
     ]),
    ("C", "Security Monitoring",
     "Continuous watch over the estate: anomalies spotted, incidents escalated in time, "
     "detection tuned as gaps appear.", [
         ("C1", "Daily monitoring of activities to identify anomalies or suspicious behaviour", 10, False),
         ("C2", "Timely detection and escalation of security incidents", 5, False),
         ("C3", "Tuning/optimization improvements based on identified gaps (min. 2/quarter)", 5, True),
     ]),
    ("D", "Security Posture Improvement",
     "Leaving the estate measurably harder to attack than it was last quarter.", [
         ("D1", "Tool configuration reviews and quarterly health checks completed", 5, False),
         ("D2", "New or improved detection rules/reports implemented (min. 2/month)", 5, False),
         ("D3", "Identified security gaps tracked and remediated within agreed timelines", 5, False),
         ("D4", "Actionable recommendations provided to enhance security controls", 5, False),
     ]),
    ("E", "Team Collaboration & Communication",
     "Knowledge moves around the team rather than sitting with one person.", [
         ("E1", "SOC requests from DFIR team efficiently completed", 4, False),
         ("E2", "Assigned monthly practical sessions delivered", 4, False),
         ("E3", "Bi-weekly knowledge transfer sessions conducted", 4, False),
         ("E4", "Professional security course or certification progress", 4, False),
         ("E5", "Active participation in team discussions, incident reviews and process improvements", 4, False),
     ]),
]


def seed(apps, schema_editor):
    AppraisalYear = apps.get_model("hub", "AppraisalYear")
    Goal = apps.get_model("hub", "Goal")
    Kpi = apps.get_model("hub", "Kpi")
    Group = apps.get_model("auth", "Group")

    Group.objects.get_or_create(name="Manager")

    year, _ = AppraisalYear.objects.get_or_create(
        label="FY 2026-27", defaults={"start_year": 2026})

    for order, (code, name, description, kpis) in enumerate(GOALS):
        goal, created = Goal.objects.get_or_create(
            year=year, code=code,
            defaults={"name": name, "description": description, "order": order})
        if not created:
            continue
        for k_order, (k_code, text, marks, quarterly) in enumerate(kpis):
            Kpi.objects.create(goal=goal, code=k_code, text=text, max_marks=marks,
                               quarterly=quarterly, scoring_mode="manual", order=k_order)


def unseed(apps, schema_editor):
    """Only removes the seeded year if nothing has been scored against it."""
    AppraisalYear = apps.get_model("hub", "AppraisalYear")
    Score = apps.get_model("hub", "Score")
    year = AppraisalYear.objects.filter(label="FY 2026-27").first()
    if year and not Score.objects.filter(kpi__goal__year=year).exists():
        year.delete()


class Migration(migrations.Migration):
    dependencies = [("hub", "0001_initial"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(seed, unseed)]
