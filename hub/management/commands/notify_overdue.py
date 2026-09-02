"""Daily nudge to each analyst about their own overdue tasks.

Inline mail from cron, like send_reminders: one team, a handful of messages a
day, and a queue would be two more services to run. Nobody on leave is mailed —
the same rule the missed-update notices already follow.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from hub import services
from hub.models import Employee


class Command(BaseCommand):
    help = "Email each analyst the tasks of theirs that are past their due date."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Print instead of sending")

    def handle(self, *args, **options):
        people = list(Employee.objects.filter(active=True).select_related("user"))
        by_person = {}
        for person, task in services.overdue_tasks(people):
            by_person.setdefault(person, []).append(task)

        if not by_person:
            self.stdout.write("Nothing overdue. No mail sent.")
            return

        for person, tasks in by_person.items():
            body = self._body(person, tasks)
            if options["dry_run"] or not person.user.email:
                self.stdout.write(f"--- would send to {person.user.email or '(no email)'} ---")
                self.stdout.write(body)
                continue
            send_mail(
                subject=f"SMTI HUB — {len(tasks)} overdue task{'' if len(tasks) == 1 else 's'}",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[person.user.email],
            )
            self.stdout.write(self.style.SUCCESS(f"Sent to {person.user.email}."))

    def _body(self, person, tasks):
        today = timezone.localdate()
        lines = [f"{person.name} — these are past their due date:", ""]
        for task in tasks:
            late = (today - task.due_date).days
            lines.append(f"  {task.title:<40} due {task.due_date:%d %b} "
                         f"({late} day{'' if late == 1 else 's'} ago) · "
                         f"{task.get_status_display()}")
        lines += ["", "Post an update, or send the work for review, in the SMTI HUB."]
        return "\n".join(lines)
