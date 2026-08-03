"""Retention for the audit trail.

The table is append-only for the application: the model refuses updates and
deletes, and in production the web role holds only INSERT and SELECT on it. That
is exactly why pruning is a command run by hand as the owner role rather than
anything the app can reach — "how long do we keep this" should be a decision
somebody makes, not a side effect.

Default is seven years, which is the usual keep-period for employment records.
Confirm the number with whoever owns HR retention before the first run.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from hub.models import AuditEvent


class Command(BaseCommand):
    help = "Delete audit events older than the retention period. Run as the database owner role."

    def add_arguments(self, parser):
        parser.add_argument("--years", type=float, default=7.0, help="Retention period (default 7)")
        parser.add_argument("--yes", action="store_true", help="Actually delete. Without it, counts only.")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=365.25 * options["years"])
        old = AuditEvent.objects.filter(timestamp__lt=cutoff)
        count = old.count()

        if not count:
            self.stdout.write(f"Nothing older than {cutoff:%Y-%m-%d}. Kept {AuditEvent.objects.count()} events.")
            return
        if not options["yes"]:
            oldest = old.order_by("timestamp").first()
            self.stdout.write(
                f"{count} events are older than {cutoff:%Y-%m-%d} "
                f"(earliest {oldest.timestamp:%Y-%m-%d}). Re-run with --yes to delete them.")
            return

        # Queryset delete, which bypasses AuditEvent.delete() by design: the
        # model guard is there to stop the application editing history, not to
        # stop the owner of the database applying a retention policy.
        old.delete()
        self.stdout.write(self.style.SUCCESS(
            f"Deleted {count} events older than {cutoff:%Y-%m-%d}."))
