"""The backfill dates approvals that were never dated, and invents nothing."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from hub import services
from hub.models import Task, TaskUpdate
from hub.tests import factories as f


class BackfillApprovalsTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)
        self.task = f.make_task(self.a, self.boss.user, title="Tune the SIEM")
        update = services.submit_update(self.task, self.a, "Done.", Task.SUBMITTED)
        services.decide_update(update, self.boss.user, approve=True)
        self.approval = update

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_approvals", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_without_writing(self):
        TaskUpdate.objects.filter(pk=self.approval.pk).update(decided_at=None)
        self.assertIn("Would stamp 1", self._run())
        self.assertIsNone(TaskUpdate.objects.get(pk=self.approval.pk).decided_at)

    def test_yes_dates_the_approval_from_when_it_was_submitted(self):
        TaskUpdate.objects.filter(pk=self.approval.pk).update(decided_at=None)
        self._run("--yes")
        fixed = TaskUpdate.objects.get(pk=self.approval.pk)
        self.assertEqual(fixed.decided_at, fixed.submitted_at)

    def test_approval_with_no_trail_is_left_alone(self):
        TaskUpdate.objects.filter(pk=self.approval.pk).delete()
        output = self._run("--yes")
        self.assertIn("no approval in the trail", output)
        self.assertIn("1 left alone", output)

    def test_clean_data_changes_nothing(self):
        self.assertIn("already has a dated approval", self._run("--yes"))
