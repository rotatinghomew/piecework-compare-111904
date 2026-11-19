"""报表生成：领班、复核员、经营者三类视角，全部带 cutoff 证据范围。

报表只写 reports/ 下的 CSV/Markdown，不回写 data/，不修改任何历史工资单。
"""
from __future__ import annotations

import csv
from pathlib import Path

from .dataio import SourceData
from .evidence import AsOf
from .labor import BlockLabor, build_labor
from .custody import BlockKg, build_attribution
from .payroll import (
    BlockPayroll, recompute_all, recheck_payslips,
)
from .productivity import productivity_groups, subsidy_evidence
from .season import (
    find_crew_conflicts, trial_proposals, cost_scenarios, budget_position,
    feasible_crew_minutes,
)

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"


def _write_csv(path: Path, header: list[str], rows: list[list], asof: AsOf) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        # 元信息行：保证单看文件内容也能复现当时证据范围（以 # 开头，可被注释感知读取器跳过）
        writer.writerow([f"# cutoff={asof.cutoff.isoformat()} ({asof.label})"])
        writer.writerow(["# evidence=only rows with known_at/confirmed_at <= cutoff; read-only output"])
        writer.writerow(header)
        writer.writerows(rows)


def _header_lines(asof: AsOf, title: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"- 交班日 cutoff：`{asof.cutoff.isoformat()}`（{asof.label}）",
        "- 证据范围：仅采用 known_at/confirmed_at 不晚于 cutoff 的记录版本",
        "- 本报表为只读分析产物，不修改 data/ 原始记录与已确认工资单",
        "",
    ]


def snapshot(data: SourceData, asof: AsOf) -> dict:
    labor = build_labor(data, asof)
    attr = build_attribution(data, asof)
    payroll = recompute_all(data, asof, labor, attr)
    return {"labor": labor, "attr": attr, "payroll": payroll}


def foreman_report(data: SourceData, asof: AsOf, snap: dict) -> Path:
    """领班视角：一筐/一段为何算不进去——劳时构成、借工、停等、待分筐。"""
    labor: dict[str, BlockLabor] = snap["labor"]
    attr: dict[str, BlockKg] = snap["attr"]
    rows = []
    for block in sorted(data.blocks):
        blk = data.blocks[block]
        lb = labor[block]
        ab = attr[block]
        notes = []
        for w in lb.by_worker.values():
            notes.extend(f"{w.worker}:{n}" for n in w.notes)
        rows.append([
            block, blk.plot, blk.team, blk.table_version,
            blk.started.strftime("%Y-%m-%d %H:%M"), blk.ended.strftime("%H:%M"),
            round(lb.paid_min, 1), round(lb.exposure_min, 1),
            round(lb.unallocated_min, 1),
            round(ab.confirmed_kg, 2), round(ab.possible_min_kg, 2),
            round(ab.possible_max_kg, 2),
            "|".join(ab.pending_baskets),
            "；".join(notes),
        ])
    path = REPORT_DIR / f"foreman_labor_{asof.cutoff:%Y%m%dT%H%M}.csv"
    _write_csv(path, [
        "block", "plot", "team", "table_version", "started", "ended",
        "paid_min(含停等)", "exposure_min(工效暴露)", "unallocated_min(借工待分)",
        "confirmed_kg(实数)", "possible_min_kg", "possible_max_kg",
        "pending_baskets", "notes",
    ], rows, asof)

    md = REPORT_DIR / f"foreman_explain_{asof.cutoff:%Y%m%dT%H%M}.md"
    lines = _header_lines(asof, "领班说明：借工与待分筐为何算不进去")
    lines += [
        "## 关键异常段",
        "",
    ]
    for block in ("X1", "X2"):
        lb, ab = labor[block], attr[block]
        lines.append(f"### {block}（{data.blocks[block].plot} 地，{data.blocks[block].table_version}）")
        for w in lb.by_worker.values():
            lines.append(
                f"- {w.worker}（{w.role}）：名册 {w.roster_min:g} 分钟，"
                f"计保底 {w.paid_min:g}，工效暴露 {w.exposure_min:g}，"
                f"待分挂起 {w.unallocated_min:g}"
                + (f"；{'；'.join(w.notes)}" if w.notes else "")
            )
        lines.append(
            f"- 筐归属：实数 {ab.confirmed_kg:g}kg；可能范围 "
            f"{ab.possible_min_kg:g}–{ab.possible_max_kg:g}kg；"
            f"待分筐 {','.join(ab.pending_baskets) or '无'}"
        )
        lines.append("")
    lines += [
        "## 口径",
        "",
        "1. 借工生效窗内分钟计借入段，借出段同步剔除，同一时刻不重复计有效劳动。",
        "2. 调令在 cutoff 后才确认的，重叠分钟两段都不计有效劳动，列 unallocated_min。",
        "3. 停等分钟保留在 paid_min（保底照算），但从 exposure_min 剔除，不充作采摘产出。",
        "4. 待分筐只给可能范围，不按时长硬分；计件差额挂起至归属签认。",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    return path


def auditor_report(data: SourceData, asof: AsOf, snap: dict) -> Path:
    """复核员视角：同一次申诉对几个班的应付影响、工资单差额凭证、准备金时间线。"""
    payroll: dict[str, BlockPayroll] = snap["payroll"]
    rechecks = recheck_payslips(data, payroll, asof)

    pay_rows = []
    for block in sorted(data.blocks):
        p = payroll[block]
        pay_rows.append([
            block, p.table_version, p.rate_cents_per_kg, p.floor_hour_cents,
            round(p.paid_min, 1), round(p.confirmed_kg, 2),
            p.piece_cents, p.floor_cents, p.payable_cents,
            round(p.pending_kg, 2), p.pending_piece_low, p.pending_piece_high,
            p.note,
        ])
    path = REPORT_DIR / f"auditor_payroll_{asof.cutoff:%Y%m%dT%H%M}.csv"
    _write_csv(path, [
        "block", "table_version", "rate_cents_per_kg", "floor_hour_cents",
        "paid_min", "confirmed_kg", "piece_cents", "floor_cents", "payable_cents",
        "pending_kg", "pending_piece_low", "pending_piece_high", "note",
    ], pay_rows, asof)

    md = REPORT_DIR / f"auditor_recheck_{asof.cutoff:%Y%m%dT%H%M}.md"
    lines = _header_lines(asof, "工资复核：申诉对各班应付的影响")
    lines += ["## 已确认工资单复核（原单金额不改写）", ""]
    lines.append("| 单据 | 班 | 原单(分) | 证据版本 | 重算应付(分) | 差额凭证(分) | 状态 | 说明 |")
    lines.append("|---|---|---:|---|---:|---:|---|---|")
    for r in rechecks:
        lines.append(
            f"| {r.document} | {r.block} | {r.payslip_cents} | {';'.join(r.evidence)} | "
            f"{r.recomputed_cents} | {r.delta_voucher_cents} | {r.status} | {r.detail} |"
        )
    lines += ["", "## 争议准备金时间线（同号后值覆盖旧量）", ""]
    lines.append("| 争议 | 班 | 金额(分) | 阶段 | 登记时间 | cutoff 内是否有效 |")
    lines.append("|---|---|---:|---|---|---|")
    latest_by_dispute: dict[str, int] = {}
    for ev in data.reserves:
        if ev.known_at <= asof.cutoff:
            latest_by_dispute[ev.dispute] = ev.cents
    for ev in data.reserves:
        visible = ev.known_at <= asof.cutoff
        effective = visible and latest_by_dispute.get(ev.dispute) == ev.cents
        flag = "有效（最新）" if effective else ("已被同号后续覆盖" if visible else "cutoff 后，不可见")
        lines.append(
            f"| {ev.dispute} | {ev.block} | {ev.cents} | {ev.stage} | "
            f"{ev.known_at:%Y-%m-%d %H:%M} | {flag} |"
        )
    lines += [
        "",
        "## 复核纪律",
        "",
        "1. 计件价/保底只取作业段 table_version：试价不回溯到秋价段，未批准新价不用于历史申诉。",
        "2. 借工待分清只挂起相关计件差额；明确的工时保底与其他地段工资照常办理。",
        "3. 共享筐归属变化时同时重算两个班的保底与计件，禁止以一班多付抵另一班应补。",
        "4. 申诉确认后只发差额凭证，不重复覆盖原工资单。",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    return path


def operator_report(data: SourceData, asof: AsOf, snap: dict) -> Path:
    """经营者视角：补贴证据覆盖、可比工效、两套成本区间、准备金与不可承诺项。"""
    labor: dict[str, BlockLabor] = snap["labor"]
    attr: dict[str, BlockKg] = snap["attr"]
    groups = productivity_groups(data, labor, attr)
    evidence = subsidy_evidence(data)
    trials = trial_proposals(data, evidence)
    costs = cost_scenarios(data, trials)
    budget = budget_position(data, asof)
    conflicts = find_crew_conflicts(data)

    gpath = REPORT_DIR / f"operator_productivity_{asof.cutoff:%Y%m%dT%H%M}.csv"
    _write_csv(gpath, [
        "plot", "slot", "role", "n_blocks", "n_teams", "slope_deg", "haul_metres",
        "before_min", "before_max", "kg_total", "exposure_min", "kg_per_exposure_min",
    ], [[
        g.plot, g.slot, g.role, g.n_blocks, g.n_teams, g.slope_deg, g.haul_metres,
        g.before_min, g.before_max, g.kg_total, round(g.exposure_min, 1),
        g.kg_per_exposure_min,
    ] for g in groups], asof)

    cpath = REPORT_DIR / f"operator_costs_{asof.cutoff:%Y%m%dT%H%M}.csv"
    _write_csv(cpath, [
        "proposal", "uplift_cents_per_kg", "roster_basis_cents",
        "feasible_basis_cents", "note",
    ], [[c.proposal, c.uplift_cents_per_kg, c.roster_basis_cents,
         c.feasible_basis_cents, c.note] for c in costs], asof)

    md = REPORT_DIR / f"operator_subsidy_{asof.cutoff:%Y%m%dT%H%M}.md"
    lines = _header_lines(asof, "经营者决策：困难地补贴证据、成本区间与预算约束")
    lines += ["## 一、各地块补贴证据分级", "",
              "| 地块 | 坡度° | 运距m | 早班段 | 午后段 | 班组数 | 结论 | 理由 |",
              "|---|---:|---:|---:|---:|---:|---|---|"]
    for e in evidence:
        lines.append(
            f"| {e.plot} | {e.slope_deg:g} | {e.haul_metres:g} | {e.morning_blocks} | "
            f"{e.afternoon_blocks} | {e.teams} | {e.verdict} | {e.reason} |"
        )
    lines += ["", "## 二、新季试算：原班表成本 vs 可调人工可行性", "",
              "| 方案 | 地块 | 需资格 | 班表申报分钟 | 可调分钟(去重) | 可承诺分钟 | 生效约束 | 证据结论 |",
              "|---|---|---|---:|---:|---:|---|---|"]
    for t in trials:
        lines.append(
            f"| {t.proposal} | {t.plot} | {t.required_skill} | {t.roster_claim_min:g} | "
            f"{t.feasible_min:g} | {t.effective_min:g} | {t.capped_by} | {t.evidence_verdict} |"
        )
    lines += ["", "### 人工重复申报（同一人共同时间段只能占一个方案）", ""]
    for cf in conflicts:
        lines.append(
            f"- {cf.worker} {cf.day}：{','.join(cf.proposals)}（{','.join(cf.parties)}），"
            f"窗口 {'; '.join(cf.windows)}，重叠 {cf.overlap_min:g} 分钟"
        )
    lines += ["", "## 三、加价试算区间（单位：分；新价未批准，不回溯历史）", "",
              "| 方案 | 加价(分/kg) | 原班表口径 | 可调人工口径 |",
              "|---|---:|---:|---:|"]
    for c in costs:
        lines.append(
            f"| {c.proposal} | {c.uplift_cents_per_kg:g} | {c.roster_basis_cents:g} | "
            f"{c.feasible_basis_cents:g} |"
        )
    lines += ["", "## 四、共同准备金与不可承诺项", "",
              f"- 共同准备金：{budget.combined_reserve_cents} 分",
              f"- cutoff 内争议留置（同号取最新）：{budget.dispute_hold_cents} 分",
              f"- 新季已签最低劳时（不可撤回）：{budget.signed_next_season_cents} 分",
              f"- 待定协议（不占最终额度）：{budget.pending_next_season_cents} 分",
              f"- 可用于新承诺余额：{budget.available_for_new_cents} 分",
              ""]
    for item in budget.uncommittable:
        lines.append(f"- 不可承诺：{item}")
    for item in budget.needs_approval:
        lines.append(f"- 需预算主管补批：{item}")
    lines += ["",
              "## 五、解释纪律", "",
              "1. 借来熟手造成的高产归入借工口径，不计入补贴增产证据。",
              "2. E 地无早班对照，只能报受限试排；试排仍须给已签保底与借工协议留人工，不得重复占人。",
              "3. 后补归属可改善后续可比分析，但本报表结论只代表 cutoff 当时可见证据。",
              ""]
    md.write_text("\n".join(lines), encoding="utf-8")
    return gpath


def generate_all(data: SourceData, asof: AsOf) -> list[Path]:
    snap = snapshot(data, asof)
    return [
        foreman_report(data, asof, snap),
        auditor_report(data, asof, snap),
        operator_report(data, asof, snap),
    ]
