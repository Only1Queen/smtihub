"""Give every completed task a readable completion date.

Coverage is derived on read, so there is no cached "is it done" to repair. What
can be wrong is the one stored fact that derivation needs: the approving
TaskUpdate's decided_at. decide_update always writes it, but a task approved
before that flow existed, or edited in the admin, can be left without one — and
open_task_days then falls back to the task's creation date, which erases every
day the analyst genuinely owed an update on it before approval.

Tasks approved with no approving update at all are reported, not invented: a
completion the trail never recorded is not one this command can date.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from hub.audit import log_event
from hub.models import Task, TaskUpdate


class Command(BaseCommand):
    help = "Stamp decided_at on approvals that lack one, so completed tasks stop being chased."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true",
                            help="Actually write. Without it, reports only.")

    @transaction.atomic
    def handle(self, *args, **options):
        undated, orphaned = [], []
        for task in Task.objects.filter(status=Task.APPROVED).select_related("assignee__user"):
            approval = task.updates.filter(decision=TaskUpdate.APPROVED).first()
            if approval is None:
                orphaned.append(task)
            elif approval.decided_at is None:
                undated.append((task, approval))

        for task, approval in undated:
            self.stdout.write(f"  {task.assignee.name:<24} {task.title[:40]:<40} "
                              f"-> {approval.submitted_at:%Y-%m-%d}")
            if not options["yes"]:
                continue
            approval.decided_at = approval.submitted_at
            approval.save(update_fields=["decided_at"])
            log_event(None, "task.backfill_approval", f"{task.assignee.name} · {task.title}",
                      after=str(approval.decided_at.date()), source="backfill_approvals")

        for task in orphaned:
            self.stdout.write(self.style.WARNING(
                f"  {task.assignee.name:<24} {task.title[:40]:<40} "
                f"-- approved with no approval in the trail; left alone"))

        if not undated and not orphaned:
            self.stdout.write("Every completed task already has a dated approval.")
            return
        verb = "Stamped" if options["yes"] else "Would stamp"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {len(undated)} approval{'' if len(undated) == 1 else 's'}"
            f", {len(orphaned)} left alone."
            + ("" if options["yes"] else " Re-run with --yes to write.")))
