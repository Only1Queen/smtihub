"""Month-end reminder for the manager.

Sent inline from cron, not a task queue: this is a handful of emails a year for
one team, and a queue would be two more services to run and monitor.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from hub import scoring, services
from hub.models import AppraisalYear, Employee, Task, month_is_scored


class Command(BaseCommand):
    help = "Email the manager about unscored months and tasks awaiting approval."

    def add_arguments(self, parser):
        parser.add_argument("--month", type=int, help="Month index 0-11 (default: current)")
        parser.add_argument("--dry-run", action="store_true", help="Print instead of sending")

    def handle(self, *args, **options):
        year = AppraisalYear.objects.filter(closed=False).order_by("-start_year").first()
        if year is None:
            self.stdout.write("No open appraisal year. Nothing to do.")
            return

        month = options["month"]
        if month is None:
            month = self._current_month(year)

        outstanding = []
        for employee in Employee.objects.filter(active=True).select_related("user"):
            if month_is_scored(employee, year, month):
                continue
            summary = services.month_summary(employee, year, month)
            if summary.slots and not summary.complete:
                outstanding.append((employee, summary))

        pending = Task.objects.filter(year=year, status=Task.SUBMITTED).count()

        if not outstanding and not pending:
            self.stdout.write(f"{scoring.MONTHS[month]}: nothing outstanding. No mail sent.")
            return

        body = self._body(year, month, outstanding, pending)
        recipients = self._managers()

        if options["dry_run"] or not recipients:
            self.stdout.write(f"--- would send to {recipients or '(no manager email set)'} ---")
            self.stdout.write(body)
            return

        send_mail(
            subject=f"SMTI HUB — {scoring.MONTHS[month]} scoring outstanding",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
        )
        self.stdout.write(self.style.SUCCESS(f"Sent to {', '.join(recipients)}."))

    def _current_month(self, year):
        """The earliest month not yet scored for everyone."""
        for month in range(12):
            if any(not month_is_scored(e, year, month)
                   for e in Employee.objects.filter(active=True)):
                return month
        return 11

    def _managers(self):
        return [e.user.email for e in
                Employee.objects.filter(active=True, user__groups__name="Manager")
                if e.user.email]

    def _body(self, year, month, outstanding, pending):
        lines = [f"{year.label} — {scoring.MONTHS[month]}", ""]
        if outstanding:
            lines.append("Not yet complete:")
            for employee, summary in outstanding:
                lines.append(f"  {employee.name:<24} {summary.entered} of {summary.slots} KPIs scored")
            lines.append("")
        if pending:
            lines.append(f"{pending} task{'s' if pending != 1 else ''} awaiting your approval.")
            lines.append("Nothing counts toward a goal until approved.")
            lines.append("")
        lines.append("Sign in to the SMTI HUB to finish.")
        return "\n".join(lines)
