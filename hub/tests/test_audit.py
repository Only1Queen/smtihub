from django.core.exceptions import ValidationError
from django.test import TestCase

from hub import scoring, services
from hub.audit import log_event
from hub.models import AuditEvent
from hub.tests import factories as f


class AppendOnlyTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)

    def test_an_event_cannot_be_edited(self):
        event = log_event(self.boss.user, "score.update", "A Bello · A1", before=3, after=4)
        event.target = "someone else"
        with self.assertRaises(ValidationError):
            event.save()

    def test_an_event_cannot_be_deleted(self):
        event = log_event(self.boss.user, "score.update", "A Bello · A1")
        with self.assertRaises(ValidationError):
            event.delete()

    def test_actor_label_survives_the_account(self):
        """The row must still say who did it after the account is gone."""
        event = log_event(self.boss.user, "score.update", "A Bello · A1")
        self.assertEqual(event.actor_label, "SMTI Manager")


class ScoreAuditTests(TestCase):
    def setUp(self):
        self.year = f.year()
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def test_score_change_records_before_and_after(self):
        services.set_score(self.a, f.kpi("A1"), 0, 7, self.boss.user)
        services.set_score(self.a, f.kpi("A1"), 0, 9, self.boss.user)

        created, updated = AuditEvent.objects.order_by("id")
        self.assertEqual(created.action, "score.create")
        self.assertEqual(created.detail["after"], 7.0)
        self.assertEqual(updated.action, "score.update")
        self.assertEqual(updated.detail, {"before": 7.0, "after": 9.0})

    def test_approval_logs_the_score_it_moves(self):
        kpi = f.kpi("B1")
        kpi.scoring_mode = scoring.FROM_TASKS
        kpi.save()
        task = f.make_task(self.a, self.boss.user, kpi_obj=kpi, weight=100)
        update = services.submit_update(task, self.a, "done")
        services.decide_update(update, self.boss.user, approve=True)

        actions = list(AuditEvent.objects.values_list("action", flat=True))
        self.assertIn("task.approve", actions)
        self.assertIn("score.recompute", actions)
        recompute = AuditEvent.objects.get(action="score.recompute")
        self.assertEqual(recompute.detail, {"before": 0.0, "after": 10.0})

    def test_returning_a_task_records_the_reason(self):
        task = f.make_task(self.a, self.boss.user, title="Runbook")
        update = services.submit_update(task, self.a, "done")
        services.decide_update(update, self.boss.user, approve=False, note="Old console.")
        event = AuditEvent.objects.get(action="task.return")
        self.assertEqual(event.detail["reason"], "Old console.")

    def test_month_close_is_audited(self):
        f.fill_month(self.a, self.boss.user, 0)
        services.close_month(self.a, self.year, 0, self.boss.user)
        self.assertTrue(AuditEvent.objects.filter(action="month.close").exists())

    def test_goal_assignment_change_is_recorded(self):
        """Assignment decides whose appraisal a goal counts toward. Logging only
        the goal name made every assignment change look like a no-op."""
        from django.urls import reverse
        goal = f.goal("A")
        # setUp assigned this goal to A Bello; handing it to someone else is the
        # change that has to show up. Re-posting the same assignee would not be
        # one, and asserting on that proves nothing.
        other = f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        self.client.force_login(self.boss.user)
        self.client.post(reverse("goal_edit", args=[goal.pk]), {
            "code": "A", "name": goal.name, "description": goal.description,
            "assignees": [other.pk],
            "kpis-TOTAL_FORMS": "0", "kpis-INITIAL_FORMS": "0",
            "kpis-MIN_NUM_FORMS": "0", "kpis-MAX_NUM_FORMS": "1000",
        })
        event = AuditEvent.objects.filter(action="goal.update").first()
        self.assertIsNotNone(event, "editing a goal must be audited")
        self.assertTrue(event.detail.get("assignment_changed"),
                        f"assignment change not recorded: {event.detail}")
        self.assertIn(other.name, event.detail["assigned_after"])
        self.assertIn(self.a.name, event.detail["assigned_before"])
        self.assertNotEqual(event.detail["assigned_before"], event.detail["assigned_after"])

    def test_unlinked_task_approval_logs_no_score_change(self):
        task = f.make_task(self.a, self.boss.user, title="MISP migration")
        update = services.submit_update(task, self.a, "done")
        services.decide_update(update, self.boss.user, approve=True)
        self.assertFalse(AuditEvent.objects.filter(action="score.recompute").exists())
