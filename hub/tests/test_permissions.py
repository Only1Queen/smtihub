from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from hub import permissions, scoring, services
from hub.models import Kpi, Score
from hub.tests import factories as f


class AccessTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        self.b = f.make_employee("jimoh", "I Jimoh", manager=self.boss)
        f.assign_all(self.a)
        f.assign_all(self.b)

    def login(self, employee):
        self.client.force_login(employee.user)

    def test_analyst_cannot_read_another_analysts_record(self):
        self.login(self.a)
        response = self.client.get(reverse("employee_appraisal", args=[self.b.pk]))
        self.assertEqual(response.status_code, 403)

    def test_analyst_can_read_their_own(self):
        self.login(self.a)
        self.assertEqual(self.client.get(reverse("my_appraisal")).status_code, 200)

    def test_analyst_cannot_write_any_score(self):
        self.login(self.a)
        response = self.client.post(
            reverse("score_save", args=[self.a.pk, 0]),
            {"kpi": f.kpi("A1").pk, "value": "10"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Score.objects.exists())

    def test_analyst_cannot_reach_manager_screens(self):
        self.login(self.a)
        for name in ("goals", "tasks", "year_summary", "activity"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_root_sends_an_analyst_to_their_own_record(self):
        """LOGIN_REDIRECT_URL points here, so a 403 would greet every analyst
        the moment they signed in."""
        self.login(self.a)
        response = self.client.get(reverse("team"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("my_appraisal"))

    def test_root_still_shows_the_team_to_a_manager(self):
        self.login(self.boss)
        response = self.client.get(reverse("team"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Team")

    def test_manager_can_score_a_report(self):
        self.login(self.boss)
        response = self.client.post(
            reverse("score_save", args=[self.a.pk, 0]),
            {"kpi": f.kpi("A1").pk, "value": "9"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(Score.objects.get().value), 9.0)

    def test_nobody_approves_their_own_task(self):
        """Otherwise the approval gate is decorative."""
        task = f.make_task(self.boss, self.boss.user, title="Manager's own task")
        self.assertFalse(permissions.can_decide_task(self.boss.user, task))

    def test_analyst_cannot_submit_another_analysts_task(self):
        task = f.make_task(self.b, self.boss.user, title="B's task")
        self.assertFalse(permissions.can_submit_update(self.a.user, task))
        self.login(self.a)
        self.assertEqual(
            self.client.post(reverse("task_submit", args=[task.pk]), {"note": "x"}).status_code,
            403)

    def test_deactivated_analyst_cannot_sign_in(self):
        self.client.post(reverse("employee_toggle", args=[self.a.pk]))  # anonymous, no effect
        self.login(self.boss)
        self.client.post(reverse("employee_toggle", args=[self.a.pk]))
        self.a.refresh_from_db()
        self.assertFalse(self.a.active)
        self.a.user.refresh_from_db()
        self.assertFalse(self.a.user.is_active)
        self.client.logout()
        # Posted through the login view rather than client.login(): axes needs a
        # real request, and this is the path an actual sign-in takes anyway.
        self.client.post(reverse("login"),
                         {"username": "bello", "password": "correct-horse-battery"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("team"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class ScoreValidationTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def test_score_above_maximum_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.set_score(self.a, f.kpi("A1"), 0, 11, self.boss.user)

    def test_negative_score_is_rejected(self):
        with self.assertRaises(ValidationError):
            services.set_score(self.a, f.kpi("A1"), 0, -1, self.boss.user)

    def test_quarterly_kpi_rejected_outside_quarter_end(self):
        with self.assertRaises(ValidationError):
            services.set_score(self.a, f.kpi("B2"), 2, 5, self.boss.user)   # Jul-26
        services.set_score(self.a, f.kpi("B2"), 1, 5, self.boss.user)       # Jun-26 fine

    def test_task_derived_kpi_rejects_a_typed_mark(self):
        kpi = f.kpi("B1")
        kpi.scoring_mode = scoring.FROM_TASKS
        kpi.save()
        with self.assertRaises(ValueError):
            services.set_score(self.a, kpi, 0, 5, self.boss.user)

    def test_concurrent_edit_produces_a_conflict(self):
        """The grid autosaves and may be open in two tabs."""
        score = services.set_score(self.a, f.kpi("A1"), 0, 5, self.boss.user)
        stale = score.updated_at.isoformat()
        services.set_score(self.a, f.kpi("A1"), 0, 7, self.boss.user)  # the other tab
        with self.assertRaises(services.ConflictError):
            services.set_score(self.a, f.kpi("A1"), 0, 9, self.boss.user,
                               expected_updated_at=stale)
        self.assertEqual(float(Score.objects.get().value), 7.0)

    def test_conflict_returns_409_not_a_silent_overwrite(self):
        score = services.set_score(self.a, f.kpi("A1"), 0, 5, self.boss.user)
        stale = score.updated_at.isoformat()
        services.set_score(self.a, f.kpi("A1"), 0, 7, self.boss.user)
        self.client.force_login(self.boss.user)
        response = self.client.post(reverse("score_save", args=[self.a.pk, 0]),
                                    {"kpi": f.kpi("A1").pk, "value": "9", "updated_at": stale})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(float(Score.objects.get().value), 7.0)


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def test_max_marks_locked_once_scored(self):
        kpi = f.kpi("A1")
        services.set_score(self.a, kpi, 0, 8, self.boss.user)
        kpi.max_marks = 20
        with self.assertRaises(ValidationError) as ctx:
            kpi.save()
        self.assertIn("maximum marks", " ".join(ctx.exception.messages))

    def test_quarterly_flag_locked_once_scored(self):
        kpi = f.kpi("A1")
        services.set_score(self.a, kpi, 0, 8, self.boss.user)
        kpi.quarterly = True
        with self.assertRaises(ValidationError):
            kpi.save()

    def test_scoring_mode_locked_once_scored(self):
        kpi = f.kpi("A1")
        services.set_score(self.a, kpi, 0, 8, self.boss.user)
        kpi.scoring_mode = scoring.FROM_TASKS
        with self.assertRaises(ValidationError):
            kpi.save()

    def test_wording_stays_editable(self):
        kpi = f.kpi("A1")
        services.set_score(self.a, kpi, 0, 8, self.boss.user)
        kpi.text = "Reworded, same measurement"
        kpi.save()
        self.assertEqual(Kpi.objects.get(pk=kpi.pk).text, "Reworded, same measurement")

    def test_closed_year_rejects_writes(self):
        year = f.year()
        year.closed = True
        year.save()
        with self.assertRaises(ValidationError):
            services.set_score(self.a, f.kpi("A1"), 0, 5, self.boss.user)
