"""Search and filtering on the two task screens.

Employee.name is a property, not a column, so any lookup that reaches for it
through the ORM blows up at request time and never at import time — these
tests exist so that failure mode stays caught.
"""

from django.test import TestCase
from django.urls import reverse

from hub.models import Task
from hub.tests import factories as f


class ManagerTaskSearchTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.bello = f.make_employee("bello", "Amina Bello", manager=self.boss)
        self.okoro = f.make_employee("okoro", "Chidi Okoro", manager=self.boss)
        self.phish = f.make_task(self.bello, self.boss.user, title="Phishing takedown")
        self.hunt = f.make_task(self.okoro, self.boss.user, title="Velociraptor hunt")
        self.client.force_login(self.boss.user)

    def get(self, **params):
        return self.client.get(reverse("tasks"), params)

    def test_unfiltered_page_lists_everyone(self):
        page = self.get()
        self.assertContains(page, "Phishing takedown")
        self.assertContains(page, "Velociraptor hunt")

    def test_search_matches_the_task_title(self):
        page = self.get(q="phishing")
        self.assertContains(page, "Phishing takedown")
        self.assertNotContains(page, "Velociraptor hunt")

    def test_search_matches_the_analyst_name(self):
        page = self.get(q="Okoro")
        self.assertContains(page, "Velociraptor hunt")
        self.assertNotContains(page, "Phishing takedown")

    def test_who_narrows_to_one_analyst(self):
        page = self.get(who=self.bello.pk)
        self.assertContains(page, "Phishing takedown")
        self.assertNotContains(page, "Velociraptor hunt")

    def test_overview_counts_the_whole_year_per_analyst(self):
        self.hunt.status = Task.APPROVED
        self.hunt.save()
        rows = {r["employee"]: r for r in self.get().context["overview"]}
        self.assertEqual(rows[self.bello]["open"], 1)
        self.assertEqual(rows[self.okoro]["done"], 1)
        self.assertEqual(rows[self.okoro]["percent"], 100)

    def test_export_follows_the_filter(self):
        body = self.get(q="phishing", export="csv").content.decode()
        self.assertIn("Phishing takedown", body)
        self.assertNotIn("Velociraptor hunt", body)


class AnalystTaskSearchTests(TestCase):
    def setUp(self):
        self.boss = f.make_employee("manager", "SMTI Manager", is_manager=True)
        self.me = f.make_employee("bello", "Amina Bello", manager=self.boss)
        f.make_task(self.me, self.boss.user, title="Phishing takedown")
        f.make_task(self.me, self.boss.user, title="Velociraptor hunt",
                    status=Task.SUBMITTED)
        self.client.force_login(self.me.user)

    def get(self, **params):
        return self.client.get(reverse("my_tasks"), params)

    def test_search_narrows_the_list(self):
        page = self.get(q="hunt")
        self.assertContains(page, "Velociraptor hunt")
        self.assertNotContains(page, "Phishing takedown")

    def test_status_filter_narrows_the_list(self):
        page = self.get(status=Task.SUBMITTED)
        self.assertContains(page, "Velociraptor hunt")
        self.assertNotContains(page, "Phishing takedown")

    def test_no_match_says_so_instead_of_all_clear(self):
        page = self.get(q="nothing-matches-this")
        self.assertContains(page, "No open task matches")
        self.assertNotContains(page, "All clear")
