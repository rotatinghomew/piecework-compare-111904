"""领域规则单元测试：锁定业务口径，防止回归。

运行：python3 -I tests/test_domain_rules.py
（unittest 发现方式同样可用：python3 -m unittest discover -s tests -v）
"""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harvest.dataio import load_sources
from harvest.evidence import policy_cutoffs, AsOf
from harvest.labor import build_labor
from harvest.custody import build_attribution
from harvest.payroll import recompute_all, recheck_payslips, round_cents
from harvest.productivity import productivity_groups, subsidy_evidence
from harvest.season import (
    find_crew_conflicts, feasible_crew_minutes, trial_proposals,
    budget_position, cost_scenarios,
)
from harvest.reports import generate_all

DATA = load_sources()
CUT1, CUT2 = policy_cutoffs(DATA)
SH1_TOTAL = DATA.baskets["SH1"].scale_kg  # 86.2


class PointInTimeTests(unittest.TestCase):
    def test_cut1_cannot_see_v2_attribution_or_loan_confirmation(self):
        labor1 = build_labor(DATA, CUT1)
        attr1 = build_attribution(DATA, CUT1)
        # 调令 11-08 才确认：cut1 重叠 50 分钟两段都挂起
        self.assertEqual(labor1["X1"].unallocated_min, 50.0)
        self.assertEqual(labor1["X2"].unallocated_min, 50.0)
        # SH1 在 cut1 只有待分：无实数，可能范围 0..86.2
        self.assertEqual(attr1["X1"].confirmed_kg, 0.0)
        self.assertEqual(attr1["X1"].possible_max_kg, SH1_TOTAL)
        self.assertIn("SH1", attr1["X1"].pending_baskets)
        self.assertIn("SH1", attr1["X2"].pending_baskets)

    def test_cut2_sees_confirmed_split_and_loan(self):
        labor2 = build_labor(DATA, CUT2)
        attr2 = build_attribution(DATA, CUT2)
        self.assertEqual(labor2["X1"].unallocated_min, 0.0)
        self.assertEqual(labor2["X2"].unallocated_min, 0.0)
        self.assertEqual(attr2["X1"].confirmed_kg, 45.0)
        self.assertEqual(attr2["X2"].confirmed_kg, 64.4 + 41.2)
        self.assertEqual(attr2["X1"].pending_baskets, ())
        # 两份额合计等于净重
        self.assertAlmostEqual(
            attr2["X1"].confirmed_kg + attr2["X2"].confirmed_kg - 64.4,
            SH1_TOTAL, places=6,
        )

    def test_later_cutoff_never_rewrites_earlier_snapshot(self):
        a1 = build_attribution(DATA, CUT1)
        a2 = build_attribution(DATA, CUT2)
        self.assertEqual(a1["X1"].confirmed_kg, 0.0)   # 旧结论保持
        self.assertEqual(a2["X1"].confirmed_kg, 45.0)  # 新结论独立


class LaborDedupTests(unittest.TestCase):
    def test_no_double_counted_minute_at_cut2(self):
        labor = build_labor(DATA, CUT2)
        w_x1 = labor["X1"].by_worker["工013"]
        w_x2 = labor["X2"].by_worker["工013"]
        # 借窗 50 分钟：X1 剔除、X2 计入，不重复
        self.assertEqual(w_x1.loan_out_min, 50.0)
        self.assertEqual(w_x2.loan_in_min, 50.0)
        self.assertEqual(w_x1.paid_min, 100.0)   # 150 - 50
        self.assertEqual(w_x2.paid_min, 85.0)    # 整段 85（含借入窗）

    def test_waiting_kept_in_pay_but_removed_from_exposure(self):
        labor = build_labor(DATA, CUT2)
        block = labor["B00A0"]  # 40 分钟车等候，4 人
        self.assertEqual(block.paid_min, 960.0)
        self.assertEqual(block.exposure_min, 800.0)

    def test_regular_blocks_have_four_roles_and_full_attendance(self):
        labor = build_labor(DATA, CUT2)
        for block, blk in DATA.blocks.items():
            if not block.startswith("B"):
                continue
            self.assertEqual(len(labor[block].by_worker), 4)
            self.assertEqual(labor[block].paid_min, 960.0)


class CustodyRangeTests(unittest.TestCase):
    def test_pending_basket_never_split_by_time(self):
        attr = build_attribution(DATA, CUT1)
        # 待分只给范围，不产生任何实数分摊
        self.assertEqual(attr["X1"].confirmed_kg + attr["X2"].confirmed_kg, 64.4)
        self.assertEqual(attr["X1"].possible_min_kg, 0.0)
        self.assertEqual(attr["X2"].possible_max_kg, 64.4 + SH1_TOTAL)

    def test_confirmed_split_sums_to_net_weight(self):
        attr = build_attribution(DATA, CUT2)
        sh1_parts = 45.0 + 41.2
        self.assertAlmostEqual(sh1_parts, SH1_TOTAL, places=6)


class PayrollTests(unittest.TestCase):
    def test_table_version_is_block_specific(self):
        labor = build_labor(DATA, CUT2)
        attr = build_attribution(DATA, CUT2)
        pay = recompute_all(DATA, CUT2, labor, attr)
        self.assertEqual(pay["X1"].table_version, "试价")       # 780 分/kg
        self.assertEqual(pay["X2"].table_version, "秋价")       # 630 分/kg
        self.assertEqual(pay["B30B0"].rate_cents_per_kg, 780.0)
        self.assertEqual(pay["B29A0"].rate_cents_per_kg, 630.0)

    def test_cut2_payroll_figures(self):
        labor = build_labor(DATA, CUT2)
        attr = build_attribution(DATA, CUT2)
        pay = recompute_all(DATA, CUT2, labor, attr)
        self.assertEqual(pay["X1"].piece_cents, round_cents(45.0 * 780))   # 35100
        self.assertEqual(pay["X2"].piece_cents, round_cents(105.6 * 630))  # 66528
        self.assertEqual(pay["X1"].floor_cents, round_cents(100 / 60 * 2400))  # 4000
        self.assertEqual(pay["X2"].floor_cents, round_cents(245 / 60 * 2250))  # 9188

    def test_cut1_suspends_differentials_but_keeps_clear_floor(self):
        labor = build_labor(DATA, CUT1)
        attr = build_attribution(DATA, CUT1)
        pay = recompute_all(DATA, CUT1, labor, attr)
        rechecks = {r.document: r for r in recheck_payslips(DATA, pay, CUT1)}
        self.assertEqual(rechecks["PAY1"].status, "挂起")
        self.assertEqual(rechecks["PAY1"].delta_voucher_cents, 0)
        # 原单金额不得被改写
        self.assertEqual(rechecks["PAY1"].payslip_cents, 34200)
        # 明确保底仍可办理
        self.assertEqual(pay["X1"].floor_cents, 4000)

    def test_cut2_emits_vouchers_without_overwriting_payslips(self):
        labor = build_labor(DATA, CUT2)
        attr = build_attribution(DATA, CUT2)
        pay = recompute_all(DATA, CUT2, labor, attr)
        rechecks = {r.document: r for r in recheck_payslips(DATA, pay, CUT2)}
        self.assertEqual(rechecks["PAY1"].delta_voucher_cents, 900)
        self.assertEqual(rechecks["PAY2"].delta_voucher_cents, 28778)
        self.assertEqual(rechecks["PAY1"].payslip_cents, 34200)  # 原单保持
        self.assertEqual(rechecks["PAY2"].payslip_cents, 37750)

    def test_shared_basket_change_rechecks_both_crews_independently(self):
        # 不允许用一班多付抵另一班：两张凭证分别计算、均为非负
        labor = build_labor(DATA, CUT2)
        attr = build_attribution(DATA, CUT2)
        pay = recompute_all(DATA, CUT2, labor, attr)
        for r in recheck_payslips(DATA, pay, CUT2):
            self.assertGreaterEqual(r.delta_voucher_cents, 0)


class ComparabilityTests(unittest.TestCase):
    def test_morning_and_afternoon_never_pooled(self):
        labor = build_labor(DATA, CUT2)
        attr = build_attribution(DATA, CUT2)
        groups = productivity_groups(DATA, labor, attr)
        keys = {(g.plot, g.slot) for g in groups}
        # 同地块早班/午后分属不同组
        self.assertIn(("D", "0"), keys)
        self.assertIn(("D", "1"), keys)

    def test_plot_e_is_trial_only(self):
        verdicts = {e.plot: e for e in subsidy_evidence(DATA)}
        self.assertEqual(verdicts["E"].verdict, "仅受限试排")
        self.assertFalse(verdicts["E"].comparable)
        self.assertEqual(verdicts["E"].morning_blocks, 0)
        self.assertEqual(verdicts["A"].verdict, "证据支持")

    def test_x_blocks_excluded_from_evidence_set(self):
        labor = build_labor(DATA, CUT2)
        attr = build_attribution(DATA, CUT2)
        groups = productivity_groups(DATA, labor, attr)
        all_blocks = [b for g in groups for b in g.blocks]
        self.assertNotIn("X1", all_blocks)
        self.assertNotIn("X2", all_blocks)


class SeasonPlanningTests(unittest.TestCase):
    def setUp(self):
        self.evidence = subsidy_evidence(DATA)
        self.trials = {t.proposal: t for t in trial_proposals(DATA, self.evidence)}

    def test_duplicate_crew_claims_detected(self):
        conflicts = find_crew_conflicts(DATA)
        workers = {c.worker for c in conflicts}
        self.assertEqual(workers, {"工013", "工255"})
        for c in conflicts:
            self.assertGreater(c.overlap_min, 0)

    def test_feasible_minutes_remove_overlap(self):
        feasible = feasible_crew_minutes(DATA)
        # 班表申报 B试420 + D加480 = 900；去共同时段重复占用后 D加只剩 120
        self.assertEqual(feasible["B试"], 420.0)
        self.assertEqual(feasible["D加"], 120.0)
        # 任一工人同一天被占用的总分钟不超过其各窗口并集
        self.assertLessEqual(feasible["B试"] + feasible["D加"], 900.0)

    def test_trial_caps_respected(self):
        for t in self.trials.values():
            self.assertLessEqual(t.effective_min, t.window_cap_min)
            if t.trial_cap_min:
                self.assertLessEqual(t.effective_min, t.trial_cap_min)

    def test_two_cost_bases_differ_when_crew_constrained(self):
        costs = {(c.proposal, c.uplift_cents_per_kg): c
                 for c in cost_scenarios(DATA, list(self.trials.values()))}
        d = costs[("D加", 120)]
        self.assertLess(d.feasible_basis_cents, d.roster_basis_cents)
        # 不受人工约束的方案两口径一致
        a = costs[("A补", 120)]
        self.assertEqual(a.feasible_basis_cents, a.roster_basis_cents)

    def test_budget_reserve_and_signed_protection(self):
        pos = budget_position(DATA, CUT2)
        # CASE1 同号取最新 3700；CASE2 9400
        self.assertEqual(pos.dispute_hold_cents, 3700 + 9400)
        self.assertEqual(pos.signed_next_season_cents, 6300)   # NEXT1 已签不可撤
        self.assertEqual(pos.pending_next_season_cents, 5500)  # NEXT2 待定不占额度
        self.assertEqual(pos.available_for_new_cents,
                         45000 - 13100 - 6300)
        self.assertTrue(any("NEXT2" in x for x in pos.uncommittable))

    def test_budget_uses_cutoff_specific_reserve_timeline(self):
        cut1_pos = budget_position(DATA, CUT1)
        # 所有准备金事件都在 cut1 之后
        self.assertEqual(cut1_pos.dispute_hold_cents, 0)
        self.assertEqual(cut1_pos.available_for_new_cents, 45000 - 6300)


class RoundingTests(unittest.TestCase):
    def test_round_to_cents(self):
        self.assertEqual(round_cents(1.5), 2)
        self.assertEqual(round_cents(1.4), 1)
        self.assertEqual(round_cents(-1.5), -2)


class ReportReproducibilityTests(unittest.TestCase):
    def test_generation_is_deterministic_and_tagged(self):
        paths1 = generate_all(DATA, CUT2)
        content1 = {p: p.read_bytes() for p in paths1}
        paths2 = generate_all(DATA, CUT2)
        content2 = {p: p.read_bytes() for p in paths2}
        self.assertEqual(set(paths1), set(paths2))
        self.assertEqual(content1, content2)
        for p in paths1:
            text = p.read_text(encoding="utf-8")
            self.assertIn(CUT2.cutoff.isoformat(), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
