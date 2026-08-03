"""Task-derived scoring: the rules the design review found holes in.

Each test here corresponds to a way the appraisal number could otherwise become
non-reproducible or unfair.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from hub import scoring, services
from hub.models import Score, Task, TaskUpdate
from hub.tests import factories as f


class TaskScoringTests(TestCase):
    def setUp(self):
        self.year = f.year()
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.b = f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        f.assign_all(self.a)
        f.assign_all(self.b)
        self.b1 = f.kpi("B1")
        self.b1.scoring_mode = scoring.FROM_TASKS
        self.b1.save()

    def approve(self, task, actor=None):
        update = services.submit_update(task, task.assignee, "done")
        return services.decide_update(update, actor or self.boss.user, approve=True)

    def test_weight_is_scoped_per_analyst(self):
        """One analyst's tasks must never move another's score."""
        f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=50, title="A one")
        f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=50, title="A two")
        theirs = f.make_task(self.b, self.boss.user, kpi_obj=self.b1, weight=100, title="B one")

        self.approve(theirs)

        values_a = services.month_values(self.a, self.year, 0)
        values_b = services.month_values(self.b, self.year, 0)
        self.assertEqual(values_a["B1"], 0.0)   # neither of A's tasks approved
        self.assertEqual(values_b["B1"], 10.0)  # B's single task carries the KPI

    def test_submitted_is_not_approved(self):
        task = f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100)
        services.submit_update(task, self.a, "please review")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.SUBMITTED)
        self.assertEqual(services.month_values(self.a, self.year, 0)["B1"], 0.0)

    def test_approval_moves_only_that_analysts_score(self):
        mine = f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100)
        f.make_task(self.b, self.boss.user, kpi_obj=self.b1, weight=100)
        before_b = services.month_values(self.b, self.year, 0)["B1"]

        self.approve(mine)

        self.assertEqual(services.month_values(self.a, self.year, 0)["B1"], 10.0)
        self.assertEqual(services.month_values(self.b, self.year, 0)["B1"], before_b)

    def test_no_tasks_blocks_month_completion(self):
        """A task-derived KPI with no tasks must not vanish from the average."""
        f.fill_month(self.a, self.boss.user, 0)
        summary = services.month_summary(self.a, self.year, 0)
        self.assertIsNone(services.month_values(self.a, self.year, 0)["B1"])
        self.assertFalse(summary.complete)
        self.assertEqual(summary.slots - summary.entered, 1)

    def test_task_cannot_link_to_unassigned_goal(self):
        from hub.models import GoalAssignment
        GoalAssignment.objects.filter(goal=f.goal("B"), employee=self.a).delete()
        with self.assertRaises(ValidationError) as ctx:
            f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100)
        self.assertIn("not assigned goal B", " ".join(ctx.exception.messages))

    def test_unlinked_task_moves_no_score(self):
        f.fill_month(self.a, self.boss.user, 0)
        task = f.make_task(self.a, self.boss.user, title="MISP migration")
        before = services.month_summary(self.a, self.year, 0)
        self.approve(task)
        self.assertEqual(services.month_summary(self.a, self.year, 0), before)

    def test_weight_requires_a_kpi_and_vice_versa(self):
        with self.assertRaises(ValidationError):
            f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=None)
        with self.assertRaises(ValidationError):
            f.make_task(self.a, self.boss.user, kpi_obj=None, weight=50)


class MonthFreezeTests(TestCase):
    """The fix for the mutable denominator: once a month is scored, nothing about
    its tasks can change the marks."""

    def setUp(self):
        self.year = f.year()
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)
        self.b1 = f.kpi("B1")
        self.b1.scoring_mode = scoring.FROM_TASKS
        self.b1.save()

        task = f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100)
        update = services.submit_update(task, self.a, "done")
        services.decide_update(update, self.boss.user, approve=True)
        f.fill_month(self.a, self.boss.user, 0)

    def test_close_month_snapshots_task_derived_value(self):
        services.close_month(self.a, self.year, 0, self.boss.user)
        stored = Score.objects.get(employee=self.a, kpi=self.b1, month_index=0)
        self.assertEqual(float(stored.value), 10.0)

    def test_adding_a_task_after_close_cannot_change_the_month(self):
        services.close_month(self.a, self.year, 0, self.boss.user)
        before = services.month_summary(self.a, self.year, 0).percent

        with self.assertRaises(ValidationError) as ctx:
            f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100, title="late")
        # Rejected for being in a scored month, not for anything about the weight.
        self.assertIn("already scored", " ".join(ctx.exception.messages))

        self.assertEqual(services.month_summary(self.a, self.year, 0).percent, before)

    def test_approving_a_later_task_cannot_change_a_closed_month(self):
        # Doubles the KPI's total weight: without the freeze this would halve the mark.
        extra = f.make_task(self.a, self.boss.user, kpi_obj=self.b1, weight=100, title="second")
        services.close_month(self.a, self.year, 0, self.boss.user)
        before = services.month_summary(self.a, self.year, 0).percent

        update = services.submit_update(extra, self.a, "also done")
        services.decide_update(update, self.boss.user, approve=True)

        self.assertEqual(services.month_summary(self.a, self.year, 0).percent, before)

    def test_incomplete_month_cannot_be_closed(self):
        Score.objects.filter(employee=self.a, month_index=0, kpi__code="A1").delete()
        with self.assertRaises(ValueError) as ctx:
            services.close_month(self.a, self.year, 0, self.boss.user)
        self.assertIn("not complete", str(ctx.exception))

    def test_manual_score_rejected_in_a_scored_month(self):
        services.close_month(self.a, self.year, 0, self.boss.user)
        with self.assertRaises(ValueError):
            services.set_score(self.a, f.kpi("A1"), 0, 3, self.boss.user)


class ApprovalFlowTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def test_returned_task_keeps_the_reason(self):
        task = f.make_task(self.a, self.boss.user, title="Update runbook")
        update = services.submit_update(task, self.a, "updated containment steps")
        services.decide_update(update, self.boss.user, approve=False,
                               note="Isolation procedure still references the old console.")
        task.refresh_from_db()
        update.refresh_from_db()
        self.assertEqual(task.status, Task.RETURNED)
        self.assertEqual(update.decision, TaskUpdate.RETURNED)
        self.assertIn("old console", update.decision_note)

    def test_status_only_changes_through_a_decision(self):
        task = f.make_task(self.a, self.boss.user)
        self.assertEqual(task.status, Task.NOT_STARTED)
        services.submit_update(task, self.a, "done")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.SUBMITTED)  # submitted, not approved
