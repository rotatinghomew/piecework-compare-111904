"""新季困难地补贴试算。

两套成本必须分开表述：
* roster_basis：按原班表申报（含同一人被两个班重复列名）机械加价，回答"成本会涨多少"；
* feasible_basis：只保留班长确实能调来、且共同时间段不重复占用的人工，
  受熟果窗 max_picks_min 与 max_trial_days 约束，回答"能多完成多少有证据的工作"。
借来熟手造成的高产不会被记成补贴增产。

预算口径：共同准备金同时保护待决工资留置、新季已签与新承诺；
已签最低劳时不可撤回，待定协议不占最终额度；额度不足时输出补批项而非硬排。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .dataio import SourceData, CrewChoice
from .evidence import AsOf
from .productivity import SubsidyEvidence


def _to_dt(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+08:00")


@dataclass(frozen=True)
class CrewConflict:
    worker: str
    day: str
    proposals: tuple[str, ...]
    parties: tuple[str, ...]
    windows: tuple[str, ...]
    overlap_min: float


def find_crew_conflicts(data: SourceData) -> list[CrewConflict]:
    """同一工人在同一天被多个班申报、且时间段相交：共同时间只能算一个人的占用。"""
    by_worker_day: dict[tuple[str, str], list[CrewChoice]] = {}
    for c in data.crew:
        by_worker_day.setdefault((c.worker, c.day), []).append(c)

    out: list[CrewConflict] = []
    for (worker, day), choices in sorted(by_worker_day.items()):
        if len(choices) < 2:
            continue
        # 两两检查时间窗相交
        overlap_any = False
        overlap_min = 0.0
        for i in range(len(choices)):
            for j in range(i + 1, len(choices)):
                a, b = choices[i], choices[j]
                lo = max(_to_dt(day, a.start_time), _to_dt(day, b.start_time))
                hi = min(_to_dt(day, a.end_time), _to_dt(day, b.end_time))
                ov = max(0.0, (hi - lo).total_seconds() / 60.0)
                if ov > 0:
                    overlap_any = True
                    overlap_min = max(overlap_min, ov)
        if overlap_any:
            out.append(CrewConflict(
                worker=worker, day=day,
                proposals=tuple(sorted({c.proposal for c in choices})),
                parties=tuple(sorted({c.claim_party for c in choices})),
                windows=tuple(f"{c.proposal}:{c.start_time}-{c.end_time}" for c in choices),
                overlap_min=overlap_min,
            ))
    return out


def feasible_crew_minutes(data: SourceData) -> dict[str, float]:
    """每个 proposal 实际可调的人工分钟（去共同时间段重复占用）。

    同一工人同一天跨 proposal 重叠时，每段重叠分钟只允许保留在一个 proposal 内：
    按 proposal 编号、窗口起始排序后，先占先得，被挤掉的分钟不计 feasible。
    """
    # 构造工人级时间线占用
    claimed: dict[tuple[str, str], list[CrewChoice]] = {}
    for c in data.crew:
        claimed.setdefault((c.worker, c.day), []).append(c)

    minutes: dict[str, float] = {p: 0.0 for p in data.slots}
    for (worker, day), choices in claimed.items():
        # 先占先得的确定性顺序
        choices.sort(key=lambda c: (c.proposal, c.start_time, c.end_time))
        busy: list[tuple[datetime, datetime]] = []
        for c in choices:
            start, end = _to_dt(day, c.start_time), _to_dt(day, c.end_time)
            # 减去已被占用的部分
            usable_start = start
            grant = 0.0
            for b0, b1 in busy:
                if b1 <= usable_start:
                    continue
                if b0 >= end:
                    break
                if b0 > usable_start:
                    grant += (b0 - usable_start).total_seconds() / 60.0
                usable_start = max(usable_start, b1)
            if usable_start < end:
                grant += (end - usable_start).total_seconds() / 60.0
            busy.append((start, end))
            busy.sort()
            minutes[c.proposal] += grant
    return minutes


@dataclass(frozen=True)
class ProposalTrial:
    proposal: str
    plot: str
    required_skill: str
    roster_claim_min: float
    feasible_min: float
    window_cap_min: float
    trial_cap_min: float
    effective_min: float          # 再受熟果窗与试排上限约束后的可承诺分钟
    capped_by: str
    evidence_verdict: str
    note: str


def trial_proposals(
    data: SourceData, evidence: list[SubsidyEvidence]
) -> list[ProposalTrial]:
    verdict_by_plot = {e.plot: e.verdict for e in evidence}
    feasible = feasible_crew_minutes(data)
    conflicts = find_crew_conflicts(data)
    conflict_workers = {cf.worker for cf in conflicts}

    roster_min: dict[str, float] = {p: 0.0 for p in data.slots}
    skill_ok: dict[str, bool] = {}
    for c in data.crew:
        roster_min[c.proposal] += (
            _to_dt(c.day, c.end_time) - _to_dt(c.day, c.start_time)
        ).total_seconds() / 60.0
        slot = data.slots[c.proposal]
        skill_ok.setdefault(c.proposal, True)
        if slot.required_skill not in c.skills:
            skill_ok[c.proposal] = False

    out: list[ProposalTrial] = []
    for proposal, slot in sorted(data.slots.items()):
        window_cap = slot.max_picks_min
        trial_cap = slot.max_trial_days * 240.0  # 每个试排日按一个 4 小时作业段
        raw_feasible = feasible[proposal]
        if slot.max_trial_days == 0:
            effective = min(raw_feasible, window_cap)
            capped_by = "熟果窗" if raw_feasible > window_cap else "无约束"
            note = "无试排额度（max_trial_days=0），只能在窗口内排已协议人工"
        else:
            effective = min(raw_feasible, window_cap, trial_cap)
            if raw_feasible <= min(window_cap, trial_cap):
                capped_by = "无约束"
            elif trial_cap <= window_cap:
                capped_by = "试排上限"
            else:
                capped_by = "熟果窗"
            note = f"试排上限 {slot.max_trial_days} 天（{trial_cap:g} 分钟）；窗内上限 {window_cap:g} 分钟"
        if not skill_ok.get(proposal, True):
            note += "；存在申报人工不具备主要工种资格，已排除其资格承诺"
        if any(c.worker in conflict_workers for c in data.crew if c.proposal == proposal):
            note += "；含重复申报人工，feasible 已去共同时段占用"
        out.append(ProposalTrial(
            proposal=proposal, plot=slot.plot, required_skill=slot.required_skill,
            roster_claim_min=roster_min[proposal], feasible_min=raw_feasible,
            window_cap_min=window_cap, trial_cap_min=trial_cap,
            effective_min=effective, capped_by=capped_by,
            evidence_verdict=verdict_by_plot.get(slot.plot, "无对照"),
            note=note,
        ))
    return out


@dataclass(frozen=True)
class CostScenario:
    proposal: str
    uplift_cents_per_kg: float
    roster_basis_cents: float     # 按原班表申报机械加价
    feasible_basis_cents: float   # 按可承诺人工比例折算的加价
    note: str


def cost_scenarios(data: SourceData, trials: list[ProposalTrial]) -> list[CostScenario]:
    """用同一批历史工时做加价试算；未批准新价绝不回写历史申诉。

    无直接公斤预测时，以历史同地块午后段平均班产作为试算基数（明确标注为试算假设），
    roster_basis 对足额班产加价，feasible_basis 按 effective/roster 比例折减。
    """
    return _cost_with_baseline(data, trials, baseline_kg=_historical_baseline(data))


def _historical_baseline(data: SourceData) -> dict[str, float]:
    """以最新证据下常规段、午后熟果窗的平均确认公斤作为地块班产基数。

    新季试排均落在采熟果窗（午后段），按规则不能拿早班先采段无条件比较，
    因此基数只取 slot=='1' 的常规段；E 地同样只有午后段，可在受限试排中使用，
    但该地块证据结论仍为"仅受限试排"。
    """
    from .evidence import AsOf
    from .custody import build_attribution

    last = AsOf(max(datetime.fromisoformat(c) for c in data.policy["cutoffs"]), "baseline")
    attr = build_attribution(data, last)
    sums: dict[str, list[float]] = {}
    for block, blk in data.blocks.items():
        if block.startswith("B") and blk.slot == "1":
            sums.setdefault(blk.plot, []).append(attr[block].confirmed_kg)
    return {plot: sum(v) / len(v) for plot, v in sorted(sums.items())}


def _cost_with_baseline(
    data: SourceData, trials: list[ProposalTrial], baseline_kg: dict[str, float]
) -> list[CostScenario]:
    out: list[CostScenario] = []
    uplifts = sorted(data.policy["next_season_uplift_choices_cents_per_kg"])
    for t in trials:
        base_kg = baseline_kg.get(t.plot, 0.0)
        ratio = (t.effective_min / t.roster_claim_min) if t.roster_claim_min else 0.0
        for up in uplifts:
            roster_cost = base_kg * up
            feasible_cost = roster_cost * ratio
            out.append(CostScenario(
                proposal=t.proposal, uplift_cents_per_kg=up,
                roster_basis_cents=round(roster_cost, 1),
                feasible_basis_cents=round(feasible_cost, 1),
                note=f"班产基数 {base_kg:.1f}kg（历史同地块常规段均值），可调人工比 {ratio:.2f}",
            ))
    return out


@dataclass(frozen=True)
class BudgetPosition:
    combined_reserve_cents: int
    dispute_hold_cents: int
    signed_next_season_cents: int
    pending_next_season_cents: int
    available_for_new_cents: int
    uncommittable: tuple[str, ...]
    needs_approval: tuple[str, ...]


def budget_position(data: SourceData, asof: AsOf) -> BudgetPosition:
    # 争议留置：同号取 cutoff 内最新一起（同号覆盖旧量）
    latest: dict[str, int] = {}
    for ev in data.reserves:
        if ev.known_at <= asof.cutoff:
            latest[ev.dispute] = ev.cents  # 时间线已按文件顺序，后者覆盖
    dispute_hold = sum(latest.values())

    signed = sum(a.guaranteed_cents for a in data.agreements
                 if a.state == "已签" and a.known_at <= asof.cutoff)
    pending = sum(a.guaranteed_cents for a in data.agreements
                  if a.state == "待定" and a.known_at <= asof.cutoff)
    reserve = int(data.policy["combined_reserve_cents"])
    available = reserve - dispute_hold - signed
    uncommittable: list[str] = []
    needs: list[str] = []
    if pending:
        uncommittable.append(
            f"待定协议 {','.join(a.agreement for a in data.agreements if a.state == '待定')} "
            f"共 {pending} 分，不占最终额度，钱/人不足时不得承诺"
        )
    if available < 0:
        needs.append(f"准备金缺口 {-available} 分，需预算主管补批后才能签新承诺")
    return BudgetPosition(
        reserve, dispute_hold, signed, pending, available,
        tuple(uncommittable), tuple(needs),
    )
