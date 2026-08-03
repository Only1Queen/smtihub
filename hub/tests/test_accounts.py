"""Sign-in, roles, lockout, acknowledgement and the health check.

The theme: everything an auditor asks about afterwards. Who got in, who was
refused, who was made a manager, and what the analyst signed.
"""

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from hub import services
from hub.models import AuditEvent, Employee, YearAcknowledgement
from hub.tests import factories as f

PASSWORD = "correct-horse-battery"


class AuthAuditTests(TestCase):
    def setUp(self):
        self.a = f.make_employee("bello", "A Bello")

    def actions(self):
        return list(AuditEvent.objects.values_list("action", flat=True))

    def test_successful_sign_in_is_recorded_with_the_address(self):
        self.client.post(reverse("login"), {"username": "bello", "password": PASSWORD},
                         HTTP_X_FORWARDED_FOR="10.1.2.3")
        event = AuditEvent.objects.get(action="auth.login")
        self.assertEqual(event.target, "bello")
        self.assertEqual(event.detail["ip"], "10.1.2.3")
        self.assertEqual(event.detail["source"], "local")

    def test_failed_sign_in_is_recorded_without_naming_an_actor(self):
        self.client.post(reverse("login"), {"username": "bello", "password": "wrong"})
        event = AuditEvent.objects.get(action="auth.login_failed")
        self.assertEqual(event.target, "bello")
        self.assertIsNone(event.actor)
        self.assertEqual(event.actor_label, "system")

    def test_the_password_tried_is_never_written_down(self):
        self.client.post(reverse("login"), {"username": "bello", "password": "hunter2-secret"})
        self.assertNotIn("hunter2-secret", str(AuditEvent.objects.get(action="auth.login_failed").detail))

    def test_sign_out_is_recorded(self):
        self.client.force_login(self.a.user)
        self.client.post(reverse("logout"))
        self.assertIn("auth.logout", self.actions())


@override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3)
class LockoutTests(TestCase):
    def setUp(self):
        f.make_employee("bello", "A Bello")
        from axes.handlers.proxy import AxesProxyHandler

        AxesProxyHandler.reset_attempts()

    def test_repeated_failures_lock_the_account_out(self):
        for _ in range(3):
            response = self.client.post(reverse("login"),
                                        {"username": "bello", "password": "wrong"})
        # The next attempt is refused before the password is even checked, so
        # the correct one does not get in either.
        response = self.client.post(reverse("login"), {"username": "bello", "password": PASSWORD})
        self.assertEqual(response.status_code, 429)  # too many requests, not "wrong password"
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIn("auth.lockout", list(AuditEvent.objects.values_list("action", flat=True)))


class RoleTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.client.force_login(self.boss.user)

    def form(self, **overrides):
        data = {"full_name": "A Bello", "job_title": "Analyst", "email": "",
                "username": "bello"}
        data.update(overrides)
        return data

    def test_manager_role_can_be_granted_from_the_ui_and_is_audited(self):
        self.client.post(reverse("employee_edit", args=[self.a.pk]), self.form(is_manager="on"))
        self.assertTrue(self.a.user.groups.filter(name="Manager").exists())
        self.assertEqual(AuditEvent.objects.filter(action="role.granted").count(), 1)

    def test_revoking_it_is_audited_too(self):
        self.a.user.groups.add(Group.objects.get_or_create(name="Manager")[0])
        self.client.post(reverse("employee_edit", args=[self.a.pk]), self.form())
        self.assertFalse(self.a.user.groups.filter(name="Manager").exists())
        self.assertEqual(AuditEvent.objects.filter(action="role.revoked").count(), 1)

    def test_editing_without_touching_the_role_records_nothing_about_roles(self):
        self.client.post(reverse("employee_edit", args=[self.a.pk]), self.form(job_title="Senior"))
        self.assertFalse(AuditEvent.objects.filter(action__startswith="role.").exists())


class PasswordChangeTests(TestCase):
    """The analyst path: not staff, so the admin's form is not reachable."""

    def setUp(self):
        self.a = f.make_employee("bello", "A Bello")
        self.client.force_login(self.a.user)

    def test_an_analyst_can_change_their_own_password(self):
        response = self.client.post(reverse("password_change"), {
            "old_password": PASSWORD,
            "new_password1": "ninth-lantern-brick",
            "new_password2": "ninth-lantern-brick",
        })
        self.assertRedirects(response, reverse("password_change_done"))
        self.a.user.refresh_from_db()
        self.assertTrue(self.a.user.check_password("ninth-lantern-brick"))

    def test_a_short_password_is_refused(self):
        self.client.post(reverse("password_change"), {
            "old_password": PASSWORD, "new_password1": "short1", "new_password2": "short1"})
        self.a.user.refresh_from_db()
        self.assertTrue(self.a.user.check_password(PASSWORD))


class AcknowledgementTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)
        self.year = f.year()

    def url(self):
        return reverse("year_acknowledge", args=[self.year.pk])

    def close_year(self):
        services.close_year(self.year, self.boss.user)
        self.year.refresh_from_db()

    def test_an_open_year_cannot_be_acknowledged(self):
        self.client.force_login(self.a.user)
        self.client.post(self.url(), {"confirm": "on"})
        self.assertFalse(YearAcknowledgement.objects.exists())

    def test_the_closed_year_page_offers_the_analyst_the_form(self):
        f.fill_month(self.a, self.boss.user, 0)
        self.close_year()
        self.client.force_login(self.a.user)
        self.assertContains(self.client.get(reverse("my_appraisal")), self.url())
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_the_analyst_signs_their_own_closed_year(self):
        f.fill_month(self.a, self.boss.user, 0)
        self.close_year()
        self.client.force_login(self.a.user)
        self.client.post(self.url(), {"confirm": "on", "comment": "I disagree with C2."})
        ack = YearAcknowledgement.objects.get()
        self.assertEqual(ack.employee, self.a)
        self.assertEqual(ack.comment, "I disagree with C2.")
        self.assertTrue(AuditEvent.objects.filter(action="year.acknowledged").exists())

    def test_the_percentage_is_frozen_at_what_was_signed(self):
        f.fill_month(self.a, self.boss.user, 0)
        self.close_year()
        self.client.force_login(self.a.user)
        self.client.post(self.url(), {"confirm": "on"})
        signed = YearAcknowledgement.objects.get().annual_percent
        self.assertIsNotNone(signed)
        # Reopening and changing a mark must not rewrite what they acknowledged.
        services.reopen_year(self.year, self.boss.user, "Correcting an agreed error in B1.")
        f.fill_month(self.a, self.boss.user, 1)
        self.assertEqual(YearAcknowledgement.objects.get().annual_percent, signed)

    def test_nobody_can_acknowledge_on_somebody_elses_behalf(self):
        self.close_year()
        self.client.force_login(self.boss.user)
        self.client.post(self.url(), {"confirm": "on"})
        # The manager signed their own record, not the analyst's.
        self.assertFalse(YearAcknowledgement.objects.filter(employee=self.a).exists())

    def test_it_cannot_be_signed_twice(self):
        self.close_year()
        self.client.force_login(self.a.user)
        self.client.post(self.url(), {"confirm": "on", "comment": "first"})
        self.client.post(self.url(), {"confirm": "on", "comment": "second"})
        self.assertEqual(YearAcknowledgement.objects.filter(employee=self.a).count(), 1)


class ProvisioningTests(TestCase):
    def test_an_account_without_an_employee_record_gets_one_on_sign_in(self):
        """Otherwise an AD account authenticates successfully and then cannot
        open a single page in the app."""
        User.objects.create_user(username="newcomer", password=PASSWORD)
        self.client.post(reverse("login"), {"username": "newcomer", "password": PASSWORD})
        self.assertTrue(Employee.objects.filter(user__username="newcomer").exists())
        self.assertTrue(AuditEvent.objects.filter(action="employee.provisioned").exists())


class HealthTests(TestCase):
    def test_healthz_needs_no_session_and_reports_the_database(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")


class ActivityExportTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.client.force_login(self.boss.user)

    def test_the_export_covers_the_filtered_set_not_just_the_page(self):
        for i in range(120):
            AuditEvent.objects.create(actor_label="system", action="test.event",
                                      target=f"row {i}", detail={})
        response = self.client.get(reverse("activity"), {"action": "test.event", "export": "csv"})
        self.assertEqual(response["Content-Type"], "text/csv")
        rows = response.content.decode().strip().splitlines()
        self.assertEqual(len(rows), 121)  # header + every match, not the first 100

    def test_an_analyst_cannot_export_the_audit_trail(self):
        self.client.force_login(f.make_employee("bello", "A Bello").user)
        response = self.client.get(reverse("activity"), {"export": "csv"})
        self.assertEqual(response.status_code, 403)
