"""Reviewing daily updates, the noticeboard, and the notification feed.

The rules worth pinning: a reviewed update leaves the manager's queue but keeps
its comment where the analyst reads it, nobody clears their own updates, and a
weekday with an open task and no update becomes a notice for both sides.
"""

from datetime import datetime, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hub import services
from hub.models import Announcement, Task, TaskUpdate
from hub.tests import factories as f


class ReviewTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)
        self.task = f.make_task(self.a, self.boss.user, title="Tune the SIEM")
        self.update = services.daily_update(self.task, self.a, "Wrote three rules.")

    def login(self, employee):
        self.client.force_login(employee.user)

    def test_review_records_comment_and_reviewer(self):
        services.review_update(self.update, self.boss.user, "  Good. Add a fourth.  ")
        self.update.refresh_from_db()
        self.assertIsNotNone(self.update.reviewed_at)
        self.assertEqual(self.update.decided_by, self.boss.user)
        self.assertEqual(self.update.manager_comment, "Good. Add a fourth.")

    def test_reviewed_update_leaves_the_manager_queue(self):
        self.login(self.boss)
        self.assertContains(self.client.get(reverse("updates_dashboard")), "Wrote three rules")
        services.review_update(self.update, self.boss.user, "Noted.")
        self.assertNotContains(self.client.get(reverse("updates_dashboard")), "Wrote three rules")

    def test_analyst_still_sees_their_update_and_the_comment(self):
        services.review_update(self.update, self.boss.user, "Add a fourth.")
        self.login(self.a)
        page = self.client.get(reverse("updates_dashboard"))
        self.assertContains(page, "Wrote three rules")
        self.assertContains(page, "Add a fourth.")

    def test_analyst_cannot_review(self):
        self.login(self.a)
        response = self.client.post(reverse("updates_dashboard"),
                                    {"review": self.update.pk, "comment": "fine by me"})
        self.assertEqual(response.status_code, 403)
        self.update.refresh_from_db()
        self.assertIsNone(self.update.reviewed_at)

    def test_comments_page_collects_them(self):
        services.review_update(self.update, self.boss.user, "Add a fourth.")
        self.login(self.boss)
        page = self.client.get(reverse("update_comments"))
        self.assertContains(page, "Add a fourth.")
        # A review with nothing said is not a comment.
        second = services.daily_update(self.task, self.a, "More rules.")
        services.review_update(second, self.boss.user, "")
        self.assertNotContains(self.client.get(reverse("update_comments")), "More rules.")

    def test_comments_page_is_manager_only(self):
        self.login(self.a)
        self.assertEqual(self.client.get(reverse("update_comments")).status_code, 403)


class AnnouncementTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)

    def test_manager_posts_analyst_reads(self):
        self.client.force_login(self.boss.user)
        self.client.post(reverse("announcements"),
                         {"title": "Patch window", "body": "Saturday 08:00."})
        self.assertEqual(Announcement.objects.count(), 1)
        self.client.force_login(self.a.user)
        page = self.client.get(reverse("announcements"))
        self.assertContains(page, "Patch window")
        self.assertNotContains(page, "Post an announcement")

    def test_analyst_cannot_post(self):
        self.client.force_login(self.a.user)
        response = self.client.post(reverse("announcements"), {"title": "x", "body": "y"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Announcement.objects.count(), 0)


class NotificationTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.a = f.make_employee("bello", "A Bello", manager=self.boss)
        f.assign_all(self.a)

    def _task_opened_days_ago(self, days):
        task = f.make_task(self.a, self.boss.user, title="Tune the SIEM")
        Task.objects.filter(pk=task.pk).update(
            created_at=timezone.now() - timedelta(days=days))
        return Task.objects.get(pk=task.pk)

    def _last_weekday(self):
        day = timezone.localdate() - timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def test_missed_day_notifies_both_sides(self):
        self._task_opened_days_ago(10)
        day = self._last_weekday()

        mine = services.notifications(self.a, [self.a])
        theirs = services.notifications(self.boss, [self.boss, self.a])
        for feed, expect in ((mine, "You did not post"), (theirs, "A Bello did not post")):
            missed = [n for n in feed if n["kind"] == "missed"]
            self.assertTrue(any(n["when"].date() == day for n in missed))
            self.assertTrue(any(expect in n["text"] for n in missed))

    def test_posting_clears_the_missed_notice_for_that_day(self):
        task = self._task_opened_days_ago(10)
        day = self._last_weekday()
        update = services.daily_update(task, self.a, "Did the thing.")
        TaskUpdate.objects.filter(pk=update.pk).update(
            submitted_at=timezone.make_aware(datetime.combine(day, time(10))))

        missed = [n for n in services.notifications(self.a, [self.a]) if n["kind"] == "missed"]
        self.assertFalse(any(n["when"].date() == day for n in missed))

    def test_today_is_never_missed(self):
        self._task_opened_days_ago(10)
        today = timezone.localdate()
        missed = [n for n in services.notifications(self.a, [self.a]) if n["kind"] == "missed"]
        self.assertFalse(any(n["when"].date() == today for n in missed))

    def test_comment_reaches_the_analyst_feed(self):
        task = f.make_task(self.a, self.boss.user, title="Tune the SIEM")
        update = services.daily_update(task, self.a, "Wrote three rules.")
        services.review_update(update, self.boss.user, "Add a fourth.")

        feed = services.notifications(self.a, [self.a])
        self.assertTrue(any(n["kind"] == "comment" and "Add a fourth." in n["text"] for n in feed))
        # It is the analyst's notice, not the manager's own.
        self.assertFalse(any(n["kind"] == "comment"
                             for n in services.notifications(self.boss, [self.boss, self.a])))

    def test_opening_the_page_marks_them_read(self):
        services.post_announcement(self.boss, "Patch window", "Saturday 08:00.")
        self.assertEqual(services.unread_count(self.a, [self.a]), 1)

        self.client.force_login(self.a.user)
        self.assertContains(self.client.get(reverse("notifications")), "Patch window")
        self.a.refresh_from_db()
        self.assertEqual(services.unread_count(self.a, [self.a]), 0)
