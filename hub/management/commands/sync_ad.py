"""Close accounts that Active Directory has closed.

Sign-in already stops the moment AD disables an account — the LDAP user filter
excludes disabled accounts, so they simply cannot authenticate. This command
exists for what that does not do: an analyst who left in March still appears on
the team list, still gets counted, still shows an unscored month, until somebody
remembers to deactivate them here.

One direction only. AD closing an account closes it here; AD reopening one does
not reopen it here, because a person may have been deactivated in the hub for a
reason of its own and a nightly job should not overrule that quietly.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from hub.audit import log_event
from hub.models import Employee
from hub.signals import LDAP_GROUP


class Command(BaseCommand):
    help = "Deactivate hub accounts that Active Directory no longer has, or has disabled."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report, change nothing")
        parser.add_argument(
            "--force", action="store_true",
            help="Proceed even when most accounts look missing (see the safety check)")

    def handle(self, *args, **options):
        if not settings.LDAP_ENABLED:
            raise CommandError("AUTH_LDAP_SERVER_URI is not set — there is no directory to sync.")

        from django_auth_ldap.backend import LDAPBackend

        backend = LDAPBackend()
        # Only accounts that came from AD. A local break-glass account is not
        # in the directory by definition, and must not be closed for it.
        managed = list(Employee.objects.filter(active=True, user__groups__name=LDAP_GROUP)
                       .select_related("user"))
        if not managed:
            self.stdout.write("No AD-sourced accounts are active. Nothing to do.")
            return

        missing = [e for e in managed if backend.populate_user(e.user.get_username()) is None]

        # python-ldap reports a dead domain controller the same way it reports
        # "no such user": nothing came back. Without this check one unreachable
        # DC deactivates the entire team in a single cron run.
        if len(missing) > max(1, len(managed) // 2) and not options["force"]:
            raise CommandError(
                f"{len(missing)} of {len(managed)} accounts look missing from AD. That is more "
                "likely a directory that is unreachable than a team that has left. Nothing was "
                "changed — check the connection, then re-run with --force if it is genuine."
            )

        for employee in missing:
            self.stdout.write(f"  {employee.name} ({employee.user.get_username()}) — not in AD")
            if options["dry_run"]:
                continue
            employee.active = False
            employee.save(update_fields=["active"])
            employee.user.is_active = False
            employee.user.save(update_fields=["is_active"])
            log_event(None, "employee.deactivate", employee.name,
                      before="active", after="inactive", source="ad-sync")

        verb = "would deactivate" if options["dry_run"] else "deactivated"
        self.stdout.write(self.style.SUCCESS(
            f"{len(managed)} AD accounts checked, {verb} {len(missing)}."))
