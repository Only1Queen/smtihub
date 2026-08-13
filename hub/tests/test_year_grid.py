"""The two things added here: scoring a whole year on one screen, and giving
one task to several analysts at once."""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from hub import services
from hub.models import GoalAssignment, Score, Task, TaskUpdate
from hub.tests import factories as f


class TeamWideGoalTests(TestCase):
    """A goal nobody is ticked into belongs to everyone. Before this, a new year
    scored nobody until five boxes per analyst were ticked."""

    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.b = f.make_employee("jimoh", "I Jimoh", manager=self.boss)

    def test_an_unassigned_goal_counts_for_everyone(self):
        ids = services.assigned_goal_ids(self.a, f.year())
        self.assertIn(f.goal("A").pk, ids)

    def test_ticking_one_name_narrows_the_goal_to_them(self):
        GoalAssignment.objects.create(goal=f.goal("A"), employee=self.a)
        self.assertIn(f.goal("A").pk, services.assigned_goal_ids(self.a, f.year()))
        self.assertNotIn(f.goal("A").pk, services.assigned_goal_ids(self.b, f.year()))
        # Untouched goals stay team-wide.
        self.assertIn(f.goal("B").pk, services.assigned_goal_ids(self.b, f.year()))

    def test_the_sheet_shows_every_goal_either_way(self):
        GoalAssignment.objects.create(goal=f.goal("A"), employee=self.a)
        self.client.force_login(self.boss.user)
        response = self.client.get(reverse("score_year", args=[self.b.pk]))
        for code in ("A", "B", "C", "D", "E"):
            self.assertContains(response, escape(f.goal(code).name))
        self.assertContains(response, "not I Jimoh's")

    def test_a_task_can_count_toward_a_team_wide_goal(self):
        """Task validation and the score sheet have to agree on who carries a
        goal, or work is refused for a goal the analyst is scored on."""
        task = f.make_task(self.a, self.boss.user, kpi_obj=f.kpi("A1"), weight=100)
        self.assertEqual(task.kpi.code, "A1")


class YearGridTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)
        self.client.force_login(self.boss.user)

    def test_grid_offers_a_cell_for_every_month(self):
        response = self.client.get(reverse("score_year", args=[self.a.pk]))
        self.assertEqual(response.status_code, 200)
        for month in range(12):
            self.assertContains(response, reverse("score_save", args=[self.a.pk, month]))

    def test_a_cell_saves_into_the_month_it_belongs_to(self):
        kpi = f.kpi("A1")
        self.client.post(reverse("score_save", args=[self.a.pk, 7]),
                         {"kpi": kpi.pk, "value": "4"})
        score = Score.objects.get(employee=self.a, kpi=kpi)
        self.assertEqual(score.month_index, 7)

    def test_an_analyst_cannot_open_someone_elses_year(self):
        other = f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        self.client.force_login(other.user)
        self.assertEqual(
            self.client.get(reverse("score_year", args=[self.a.pk])).status_code, 403)


class NewAnalystTests(TestCase):
    """An account made without a password cannot sign in at all, which is how a
    new analyst ends up locked out of their own appraisal."""

    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.client.force_login(self.boss.user)

    def post(self, **extra):
        data = {"full_name": "N Okoro", "job_title": "Analyst", "email": "",
                "username": "okoro", "password1": "gale-window-73-fern",
                "password2": "gale-window-73-fern"}
        data.update(extra)
        return self.client.post(reverse("employee_new"), data)

    def test_the_new_analyst_can_sign_in_with_the_password_set_for_them(self):
        self.assertEqual(self.post().status_code, 302)
        self.client.logout()
        self.assertTrue(self.client.login(username="okoro", password="gale-window-73-fern"))

    def test_mismatched_passwords_are_refused(self):
        response = self.post(password2="something-else-entirely")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="okoro").exists())

    def test_a_weak_password_is_refused(self):
        response = self.post(password1="short", password2="short")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="okoro").exists())


class ProgressUpdateTests(TestCase):
    """Telling your manager where you are is not the same as asking them to
    close the task."""

    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.task = f.make_task(self.a, self.boss.user, title="Ongoing work")
        self.client.force_login(self.a.user)

    def post(self, status):
        return self.client.post(reverse("task_submit", args=[self.task.pk]),
                                {"status": status, "note": "Half done"})

    def test_each_rung_of_the_ladder_moves_the_task(self):
        for status in (Task.PICKED_UP, Task.IN_PROGRESS, Task.ON_TRACK):
            self.assertEqual(self.post(status).status_code, 302, status)
            self.task.refresh_from_db()
            self.assertEqual(self.task.status, status)
            self.assertEqual(self.task.updates.first().decision, TaskUpdate.NOT_NEEDED)

    def test_progress_short_of_completed_never_enters_the_review_queue(self):
        self.post(Task.ON_TRACK)
        self.client.force_login(self.boss.user)
        response = self.client.get(reverse("task_decide", args=[self.task.pk]))
        self.assertEqual(response.status_code, 400)

    def test_an_analyst_cannot_approve_their_own_task(self):
        """The ladder stops at completed; the last two rungs are the manager's."""
        response = self.post(Task.APPROVED)
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db()
        self.assertNotEqual(self.task.status, Task.APPROVED)

    def test_completing_asks_the_manager_to_review_and_grade(self):
        self.post(Task.SUBMITTED)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.SUBMITTED)
        self.client.force_login(self.boss.user)
        self.assertEqual(
            self.client.get(reverse("task_decide", args=[self.task.pk])).status_code, 200)


class TaskDetailTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.b = f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        self.task = f.make_task(self.a, self.boss.user, title="Ongoing work")

    def test_the_analyst_can_post_a_daily_update_without_changing_status(self):
        self.client.force_login(self.a.user)
        response = self.client.post(reverse("task_detail", args=[self.task.pk]),
                                    {"note": "Wrote the queries today"})
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.NOT_STARTED)
        update = self.task.updates.first()
        self.assertEqual(update.note, "Wrote the queries today")
        # Blank, not the task's own status: a daily note claims no transition.
        self.assertEqual(update.proposed_status, "")

    def test_an_empty_daily_update_is_refused(self):
        self.client.force_login(self.a.user)
        self.client.post(reverse("task_detail", args=[self.task.pk]), {"note": "   "})
        self.assertFalse(self.task.updates.exists())

    def test_the_manager_can_read_the_task_but_not_post_on_it(self):
        self.client.force_login(self.boss.user)
        self.assertEqual(
            self.client.get(reverse("task_detail", args=[self.task.pk])).status_code, 200)
        response = self.client.post(reverse("task_detail", args=[self.task.pk]),
                                    {"note": "Not mine to write"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.task.updates.exists())

    def test_another_analyst_cannot_open_it(self):
        self.client.force_login(self.b.user)
        self.assertEqual(
            self.client.get(reverse("task_detail", args=[self.task.pk])).status_code, 403)


class UpdatesDashboardTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.task = f.make_task(self.a, self.boss.user, title="Ongoing work")

    def test_today_counts_as_covered_once_something_is_posted(self):
        today = timezone.localdate()
        self.assertNotIn(today, services.update_days(self.a, today, today))
        services.daily_update(self.task, self.a, "Did the thing")
        self.assertIn(today, services.update_days(self.a, today, today))

    def test_a_day_with_no_task_open_is_not_a_missed_day(self):
        """Otherwise every day before their first task reads as a failure."""
        days = [timezone.localdate() - timedelta(days=n) for n in (10, 0)]
        owed = services.expected_days(self.a, days)
        self.assertNotIn(days[0], owed)   # ten days ago: no task existed
        self.assertIn(days[1], owed)      # today: one is open

    def test_the_analyst_sees_only_their_own_row(self):
        f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        self.client.force_login(self.a.user)
        response = self.client.get(reverse("updates_dashboard"))
        self.assertContains(response, "A Bello")
        self.assertNotContains(response, "I Jimoh")

    def test_the_manager_sees_the_whole_team(self):
        f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        self.client.force_login(self.boss.user)
        response = self.client.get(reverse("updates_dashboard"))
        self.assertContains(response, "A Bello")
        self.assertContains(response, "I Jimoh")


class MultiAssigneeTaskTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.b = f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        f.assign_all(self.a)
        f.assign_all(self.b)
        self.client.force_login(self.boss.user)

    def post(self, **extra):
        data = {"title": "Publish the brief", "description": "", "due_date": "",
                "scoring_month": 0, "kpi": "", "weight": ""}
        data.update(extra)
        return self.client.post(reverse("task_new"), data)

    def test_one_form_creates_one_task_each(self):
        response = self.post(assignees=[self.a.pk, self.b.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Task.objects.count(), 2)
        self.assertEqual({t.assignee_id for t in Task.objects.all()}, {self.a.pk, self.b.pk})

    def test_a_single_assignee_still_works(self):
        self.post(assignees=[self.a.pk])
        self.assertEqual(Task.objects.count(), 1)

    def test_nobody_ticked_is_rejected(self):
        response = self.post(assignees=[])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.exists())

    def test_a_task_with_no_kpi_can_still_be_graded_on_approval(self):
        task = f.make_task(self.a, self.boss.user, title="Operational work")
        services.submit_update(task, self.a, "done")
        response = self.client.post(reverse("task_decide", args=[task.pk]),
                                    {"decision": "approve", "note": "", "grade": "80"})
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.APPROVED)
        self.assertEqual(task.grade, 80)

    def test_a_mark_over_100_is_refused_rather_than_dropped(self):
        task = f.make_task(self.a, self.boss.user, title="Operational work")
        services.submit_update(task, self.a, "done")
        response = self.client.post(reverse("task_decide", args=[task.pk]),
                                    {"decision": "approve", "note": "", "grade": "500"})
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertIsNone(task.grade)
        self.assertNotEqual(task.status, Task.APPROVED)

    def test_a_kpi_task_is_not_offered_a_second_mark(self):
        """Its mark is the rollup; a typed number beside it would contradict."""
        task = f.make_task(self.a, self.boss.user, kpi_obj=f.kpi("A1"), weight=100)
        services.submit_update(task, self.a, "done")
        response = self.client.get(reverse("task_decide", args=[task.pk]))
        self.assertNotContains(response, 'name="grade"')

    def test_one_invalid_assignee_creates_nothing(self):
        """All or nothing — a half-created batch would have the manager chasing
        duplicates to fix one person's task."""
        unassigned = f.make_employee("okoro", "N Okoro", manager=self.boss)
        response = self.post(assignees=[self.a.pk, unassigned.pk],
                             kpi=f.kpi("A1").pk, weight=100)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.exists())
        self.assertContains(response, "not assigned goal")
