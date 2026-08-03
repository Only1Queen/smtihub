"""Golden vectors for the appraisal maths.

Pure functions, no database. These encode the decisions in the plan; if one of
them changes, that is a policy change and must be argued for, not patched.
"""

from django.test import SimpleTestCase

from hub import scoring
from hub.scoring import KpiSpec, TaskSpec

# The seeded SMTI structure: 5 goals, 18 KPIs, 100 marks, B2 and C3 quarterly.
SMTI = [
    KpiSpec("A1", "A", 10, False, "manual"),
    KpiSpec("A2", "A", 5, False, "manual"),
    KpiSpec("A3", "A", 5, False, "manual"),
    KpiSpec("B1", "B", 10, False, "manual"),
    KpiSpec("B2", "B", 5, True, "manual"),
    KpiSpec("B3", "B", 5, False, "manual"),
    KpiSpec("C1", "C", 10, False, "manual"),
    KpiSpec("C2", "C", 5, False, "manual"),
    KpiSpec("C3", "C", 5, True, "manual"),
    KpiSpec("D1", "D", 5, False, "manual"),
    KpiSpec("D2", "D", 5, False, "manual"),
    KpiSpec("D3", "D", 5, False, "manual"),
    KpiSpec("D4", "D", 5, False, "manual"),
    KpiSpec("E1", "E", 4, False, "manual"),
    KpiSpec("E2", "E", 4, False, "manual"),
    KpiSpec("E3", "E", 4, False, "manual"),
    KpiSpec("E4", "E", 4, False, "manual"),
    KpiSpec("E5", "E", 4, False, "manual"),
]
ALL_GOALS = {"A", "B", "C", "D", "E"}


def full_marks(month, kpis=SMTI, assigned=ALL_GOALS):
    return {k.code: k.max_marks for k in scoring.eligible_kpis(kpis, month, assigned)}


class StructureTests(SimpleTestCase):
    def test_seeded_structure(self):
        self.assertEqual(len(SMTI), 18)
        self.assertEqual(sum(k.max_marks for k in SMTI), 100)

    def test_quarter_end_months(self):
        # Jun-26, Sep-26, Dec-26, Mar-27
        self.assertEqual(
            [i for i, _ in enumerate(scoring.MONTHS) if scoring.is_quarter_end(i)],
            [1, 4, 7, 10],
        )
        self.assertEqual(
            [scoring.MONTHS[i] for i in (1, 4, 7, 10)],
            ["Jun-26", "Sep-26", "Dec-26", "Mar-27"],
        )

    def test_month_maximum_is_100_at_quarter_end_and_90_otherwise(self):
        self.assertEqual(scoring.month_summary(SMTI, 1, ALL_GOALS, full_marks(1)).maximum, 100)
        self.assertEqual(scoring.month_summary(SMTI, 2, ALL_GOALS, full_marks(2)).maximum, 90)

    def test_quarterly_kpis_are_the_only_difference(self):
        self.assertEqual(len(scoring.eligible_kpis(SMTI, 1, ALL_GOALS)), 18)
        self.assertEqual(len(scoring.eligible_kpis(SMTI, 2, ALL_GOALS)), 16)


class EligibilityTests(SimpleTestCase):
    def test_unassigned_goal_is_excluded_not_zeroed(self):
        """The whole point: an analyst not assigned goal B is not marked down
        for it — those KPIs leave the denominator entirely."""
        assigned = {"A", "C", "D", "E"}
        summary = scoring.month_summary(SMTI, 2, assigned, full_marks(2, assigned=assigned))
        self.assertEqual(summary.maximum, 75)  # 90 less B1(10) and B3(5)
        self.assertTrue(summary.complete)
        self.assertEqual(summary.percent, 100.0)

    def test_different_rosters_give_different_denominators(self):
        intel = scoring.month_summary(SMTI, 2, {"A", "B", "C", "E"}, full_marks(2, assigned={"A", "B", "C", "E"}))
        eng = scoring.month_summary(SMTI, 2, {"A", "C", "D", "E"}, full_marks(2, assigned={"A", "C", "D", "E"}))
        self.assertNotEqual(intel.maximum, eng.maximum)
        self.assertEqual(intel.percent, eng.percent)  # both fully scored


class MonthCompletionTests(SimpleTestCase):
    def test_partial_month_does_not_count(self):
        """The decided rule. The prototype counted a month when *any* KPI had a
        value while still dividing by the full maximum, so a half-filled month
        scored near zero and dragged the mean down."""
        values = {"A1": 10, "A2": 5}
        summary = scoring.month_summary(SMTI, 2, ALL_GOALS, values)
        self.assertFalse(summary.complete)
        self.assertIsNone(summary.percent)
        self.assertEqual((summary.entered, summary.slots), (2, 16))

    def test_complete_month_scores(self):
        summary = scoring.month_summary(SMTI, 2, ALL_GOALS, full_marks(2))
        self.assertTrue(summary.complete)
        self.assertEqual(summary.percent, 100.0)

    def test_zero_is_a_value_not_a_gap(self):
        values = {k.code: 0 for k in scoring.eligible_kpis(SMTI, 2, ALL_GOALS)}
        summary = scoring.month_summary(SMTI, 2, ALL_GOALS, values)
        self.assertTrue(summary.complete)
        self.assertEqual(summary.percent, 0.0)

    def test_month_with_no_eligible_kpis_is_not_complete(self):
        summary = scoring.month_summary(SMTI, 2, set(), {})
        self.assertFalse(summary.complete)
        self.assertIsNone(summary.percent)


class AnnualTests(SimpleTestCase):
    def test_annual_is_mean_of_complete_months(self):
        half = dict(full_marks(2))
        half["A1"] = 5  # 85 of 90
        summaries = [
            scoring.month_summary(SMTI, 0, ALL_GOALS, full_marks(0)),
            scoring.month_summary(SMTI, 2, ALL_GOALS, half),
            scoring.month_summary(SMTI, 3, ALL_GOALS, {"A1": 10}),  # partial, ignored
        ]
        expected = (100.0 + 85 / 90 * 100) / 2
        self.assertAlmostEqual(scoring.annual_percent(summaries), expected)

    def test_annual_is_none_before_any_month_completes(self):
        summaries = [scoring.month_summary(SMTI, 0, ALL_GOALS, {"A1": 10})]
        self.assertIsNone(scoring.annual_percent(summaries))


class TaskRollupTests(SimpleTestCase):
    def test_approved_weight_over_total_weight(self):
        tasks = [TaskSpec(50, True), TaskSpec(50, False)]
        self.assertEqual(scoring.task_rollup(10, tasks), 5.0)

    def test_submitted_is_not_approved(self):
        self.assertEqual(scoring.task_rollup(10, [TaskSpec(100, False)]), 0.0)

    def test_all_approved_gives_full_marks(self):
        tasks = [TaskSpec(30, True), TaskSpec(30, True), TaskSpec(40, True)]
        self.assertEqual(scoring.task_rollup(10, tasks), 10.0)

    def test_weights_need_not_sum_to_100(self):
        self.assertEqual(scoring.task_rollup(10, [TaskSpec(3, True), TaskSpec(1, False)]), 7.5)

    def test_no_tasks_is_none_never_zero(self):
        """None means 'not scorable yet' and blocks month completion. Zero would
        let a task-derived KPI silently drag the average down."""
        self.assertIsNone(scoring.task_rollup(10, []))

    def test_zero_total_weight_is_none(self):
        self.assertIsNone(scoring.task_rollup(10, [TaskSpec(0, True)]))

    def test_task_derived_kpi_without_tasks_blocks_the_month(self):
        kpis = [
            KpiSpec("X1", "X", 10, False, scoring.MANUAL),
            KpiSpec("X2", "X", 10, False, scoring.FROM_TASKS),
        ]
        values = {"X1": 10, "X2": scoring.task_rollup(10, [])}
        summary = scoring.month_summary(kpis, 2, {"X"}, values)
        self.assertFalse(summary.complete)
        self.assertIsNone(summary.percent)


class BandTests(SimpleTestCase):
    def test_thresholds_match_the_prototype(self):
        self.assertEqual(scoring.band(90), "good")
        self.assertEqual(scoring.band(89.9), "warn")
        self.assertEqual(scoring.band(70), "warn")
        self.assertEqual(scoring.band(69.9), "bad")
        self.assertEqual(scoring.band(None), "none")
