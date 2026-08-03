"""Freezing exists to stop history changing silently, not to stop it changing.

These cover the escape hatches: reopening a scored month, reopening a closed
year, and the rule that both must leave a reason behind.
"""

from django.test import TestCase
from django.urls import reverse

from hub import scoring, services
from hub.models import AuditEvent, Score, ScoredMonth, month_is_scored
from hub.tests import factories as f


class MonthReopenTests(TestCase):
    def setUp(self):
        self.year = f.year()
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)
        self.b1 = f.kpi("B1")
        self.b1.scoring_mode = scoring.FROM_TASKS
        self.b1.save()

        task = f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100)
        services.decide_update(services.submit_update(task, self.a, "done"),
                               self.boss.user, approve=True)
        f.fill_month(self.a, self.boss.user, 0)
        services.close_month(self.a, self.year, 0, self.boss.user)

    def test_reopen_requires_a_reason(self):
        with self.assertRaises(ValueError):
            services.reopen_month(self.a, self.year, 0, self.boss.user, "   ")
        self.assertTrue(month_is_scored(self.a, self.year, 0))

    def test_reopen_clears_the_task_snapshot_only(self):
        """Task-derived marks go back to live; manual marks are what the manager
        typed, and discarding them would be a second error."""
        manual_before = float(Score.objects.get(employee=self.a, kpi__code="A1", month_index=0).value)

        services.reopen_month(self.a, self.year, 0, self.boss.user,
                              "Task was approved against the wrong month.")

        self.assertFalse(month_is_scored(self.a, self.year, 0))
        self.assertFalse(Score.objects.filter(employee=self.a, kpi=self.b1, month_index=0).exists())
        self.assertEqual(
            float(Score.objects.get(employee=self.a, kpi__code="A1", month_index=0).value),
            manual_before)

    def test_reopened_month_recomputes_from_tasks(self):
        services.reopen_month(self.a, self.year, 0, self.boss.user, "Correcting a mistake here.")
        self.assertEqual(services.month_values(self.a, self.year, 0)["B1"], 10.0)

    def test_reopen_is_audited_with_the_reason(self):
        services.reopen_month(self.a, self.year, 0, self.boss.user, "Approved the wrong task.")
        event = AuditEvent.objects.get(action="month.reopen")
        self.assertEqual(event.detail["reason"], "Approved the wrong task.")
        self.assertEqual(event.detail["before"], "scored")

    def test_cannot_reopen_a_month_that_was_never_scored(self):
        with self.assertRaises(ValueError):
            services.reopen_month(self.a, self.year, 3, self.boss.user, "No such month scored.")

    def test_reopen_then_rescore_works_end_to_end(self):
        services.reopen_month(self.a, self.year, 0, self.boss.user, "Correcting A1.")
        services.set_score(self.a, f.kpi("A1"), 0, 4, self.boss.user)
        services.close_month(self.a, self.year, 0, self.boss.user)
        self.assertTrue(month_is_scored(self.a, self.year, 0))
        self.assertEqual(float(Score.objects.get(employee=self.a, kpi__code="A1", month_index=0).value), 4.0)


class YearCloseTests(TestCase):
    def setUp(self):
        self.year = f.year()
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def test_close_then_reopen_round_trip(self):
        services.close_year(self.year, self.boss.user)
        self.year.refresh_from_db()
        self.assertTrue(self.year.closed)
        self.assertIsNotNone(self.year.closed_at)

        services.reopen_year(self.year, self.boss.user, "Late correction to September.")
        self.year.refresh_from_db()
        self.assertFalse(self.year.closed)
        self.assertIsNone(self.year.closed_at)

    def test_reopen_requires_a_reason(self):
        services.close_year(self.year, self.boss.user)
        with self.assertRaises(ValueError):
            services.reopen_year(self.year, self.boss.user, "")
        self.year.refresh_from_db()
        self.assertTrue(self.year.closed)

    def test_both_actions_are_audited(self):
        services.close_year(self.year, self.boss.user)
        services.reopen_year(self.year, self.boss.user, "Correcting an approval error.")
        actions = list(AuditEvent.objects.values_list("action", flat=True))
        self.assertIn("year.close", actions)
        self.assertIn("year.reopen", actions)
        self.assertEqual(AuditEvent.objects.get(action="year.reopen").detail["reason"],
                         "Correcting an approval error.")

    def test_scoring_works_again_after_reopen(self):
        services.close_year(self.year, self.boss.user)
        services.reopen_year(self.year, self.boss.user, "Needed to fix one mark.")
        services.set_score(self.a, f.kpi("A1"), 0, 7, self.boss.user)
        self.assertEqual(float(Score.objects.get(kpi__code="A1").value), 7.0)


class ViewGateTests(TestCase):
    def setUp(self):
        self.year = f.year()
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def test_analyst_cannot_reopen_a_year(self):
        services.close_year(self.year, self.boss.user)
        self.client.force_login(self.a.user)
        response = self.client.post(reverse("year_reopen", args=[self.year.pk]),
                                    {"reason": "I would like a better mark please."})
        self.assertEqual(response.status_code, 403)
        self.year.refresh_from_db()
        self.assertTrue(self.year.closed)

    def test_analyst_cannot_set_a_password(self):
        self.client.force_login(self.a.user)
        response = self.client.post(reverse("employee_password", args=[self.boss.pk]),
                                    {"password1": "hijacked-account-1", "password2": "hijacked-account-1"})
        self.assertEqual(response.status_code, 403)
        self.boss.user.refresh_from_db()
        self.assertFalse(self.boss.user.check_password("hijacked-account-1"))

    def test_manager_sets_an_analyst_password(self):
        self.client.force_login(self.boss.user)
        response = self.client.post(reverse("employee_password", args=[self.a.pk]),
                                    {"password1": "shift-handover-42", "password2": "shift-handover-42"})
        self.assertEqual(response.status_code, 302)
        self.a.user.refresh_from_db()
        self.assertTrue(self.a.user.check_password("shift-handover-42"))
        self.assertTrue(AuditEvent.objects.filter(action="account.password_set").exists())

    def test_weak_password_is_rejected(self):
        self.client.force_login(self.boss.user)
        response = self.client.post(reverse("employee_password", args=[self.a.pk]),
                                   {"password1": "short", "password2": "short"})
        self.assertEqual(response.status_code, 200)
        self.a.user.refresh_from_db()
        self.assertFalse(self.a.user.check_password("short"))

    def test_short_reason_is_rejected_by_the_form(self):
        services.close_year(self.year, self.boss.user)
        self.client.force_login(self.boss.user)
        response = self.client.post(reverse("year_reopen", args=[self.year.pk]), {"reason": "oops"})
        self.assertEqual(response.status_code, 200)
        self.year.refresh_from_db()
        self.assertTrue(self.year.closed)

    def test_closed_year_still_renders_read_only(self):
        f.fill_month(self.a, self.boss.user, 0, exclude=("B1",))
        services.close_year(self.year, self.boss.user)
        self.client.force_login(self.boss.user)
        response = self.client.get(reverse("employee_appraisal", args=[self.a.pk]))
        self.assertEqual(response.status_code, 200)
