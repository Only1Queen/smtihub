"""Create the next appraisal year.

A new year is data entry, not a code change: goals, KPIs and assignments are
year-scoped, so copying them forward cannot alter a previous year's numbers.
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from hub.models import AppraisalYear, Goal, GoalAssignment, Kpi


class Command(BaseCommand):
    help = "Create an appraisal year, copying last year's goals or loading a revised set."

    def add_arguments(self, parser):
        parser.add_argument("label", help='e.g. "FY 2027-28"')
        parser.add_argument("start_year", type=int, help="Calendar year the May start falls in")
        parser.add_argument("--from-year", help="Label to copy goals from (default: the latest)")
        parser.add_argument("--goals-file", help="JSON file of a revised goal set instead of copying")
        parser.add_argument("--no-assignments", action="store_true",
                            help="Copy goals but not who they are assigned to")

    @transaction.atomic
    def handle(self, *args, **options):
        label = options["label"]
        if AppraisalYear.objects.filter(label=label).exists():
            raise CommandError(f"{label} already exists.")

        year = AppraisalYear.objects.create(label=label, start_year=options["start_year"])

        if options["goals_file"]:
            self._from_file(year, options["goals_file"])
        else:
            source = self._source_year(options["from_year"], exclude=year)
            self._copy(year, source, with_assignments=not options["no_assignments"])
            self.stdout.write(f"Copied goals from {source.label}.")

        self._report(year)

    def _source_year(self, label, exclude):
        qs = AppraisalYear.objects.exclude(pk=exclude.pk)
        year = qs.filter(label=label).first() if label else qs.order_by("-start_year").first()
        if year is None:
            raise CommandError("No previous year to copy from. Use --goals-file.")
        return year

    def _copy(self, year, source, with_assignments):
        for goal in source.goals.prefetch_related("kpis", "assignments"):
            new_goal = Goal.objects.create(
                year=year, code=goal.code, name=goal.name,
                description=goal.description, order=goal.order)
            for kpi in goal.kpis.all():
                Kpi.objects.create(
                    goal=new_goal, code=kpi.code, text=kpi.text, max_marks=kpi.max_marks,
                    quarterly=kpi.quarterly, scoring_mode=kpi.scoring_mode, order=kpi.order)
            if with_assignments:
                for assignment in goal.assignments.all():
                    GoalAssignment.objects.create(goal=new_goal, employee=assignment.employee)

    def _from_file(self, year, path):
        with open(path) as handle:
            data = json.load(handle)
        for order, entry in enumerate(data):
            goal = Goal.objects.create(
                year=year, code=entry["code"], name=entry["name"],
                description=entry.get("description", ""), order=order)
            for k_order, kpi in enumerate(entry["kpis"]):
                Kpi.objects.create(
                    goal=goal, code=kpi["code"], text=kpi["text"],
                    max_marks=kpi["max_marks"], quarterly=kpi.get("quarterly", False),
                    scoring_mode=kpi.get("scoring_mode", "manual"), order=k_order)
        self.stdout.write(f"Loaded {len(data)} goals from {path}.")

    def _report(self, year):
        """Print the structure so the manager can confirm before scoring opens."""
        self.stdout.write(self.style.SUCCESS(f"\n{year.label}"))
        total = 0
        for goal in year.goals.prefetch_related("kpis", "assignees__user"):
            marks = goal.total_marks
            total += marks
            names = ", ".join(e.name for e in goal.assignees.all()) or "nobody yet"
            self.stdout.write(f"  {goal.code}  {goal.name}  —  {marks} marks")
            for kpi in goal.kpis.all():
                flags = []
                if kpi.quarterly:
                    flags.append("quarterly")
                if kpi.scoring_mode == "from_tasks":
                    flags.append("from tasks")
                suffix = f"  [{', '.join(flags)}]" if flags else ""
                self.stdout.write(f"        {kpi.code}  {kpi.max_marks:>3}  {kpi.text[:60]}{suffix}")
            self.stdout.write(f"        assigned: {names}")
        self.stdout.write(self.style.SUCCESS(f"\n  Total: {total} marks"))
        self.stdout.write("\nCheck the structure above, then open scoring for the new year.")
