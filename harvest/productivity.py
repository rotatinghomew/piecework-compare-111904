"""工效对照与补贴证据分级。

可比性规则：
* 早班(picked_before=0)与午后段(已采 20%+)不做无条件比较；同地块同时段才同组。
* 工种分列：采摘工效看采摘暴露分钟，抬筐另列，不混算。
* 班组名称相似不构成可比性；只按地块/时段/进度档/工种的实际重合条件分组。
* E 地只有午后段、仅两个班组、且最低已采 24%：不存在早班对照，只可受限试排。
* X1/X2 是一次性异常段，不进入新季补贴证据集。
"""
from __future__ import annotations

from dataclasses import dataclass

from .dataio import SourceData
from .labor import BlockLabor
from .custody import BlockKg


@dataclass(frozen=True)
class GroupStat:
    plot: str
    slot: str
    role: str
    n_blocks: int
    n_teams: int
    slope_deg: float
    haul_metres: float
    before_min: float
    before_max: float
    kg_total: float
    exposure_min: float
    kg_per_exposure_min: float
    blocks: tuple[str, ...]


def _role_minutes(labor: BlockLabor, role: str) -> float:
    return labor.role_exposure(role)


def productivity_groups(
    data: SourceData,
    labor: dict[str, BlockLabor],
    attr: dict[str, BlockKg],
) -> list[GroupStat]:
    buckets: dict[tuple[str, str, str], dict] = {}
    for block, blk in data.blocks.items():
        if not block.startswith("B"):  # X1/X2 不进证据集
            continue
        for role in ("采摘", "抬筐"):
            key = (blk.plot, blk.slot, role)
            entry = buckets.setdefault(key, {
                "blocks": [], "teams": set(), "kg": 0.0, "min": 0.0,
                "before": [],
            })
            entry["blocks"].append(block)
            entry["teams"].add(blk.team)
            # 产量是段级事实，两个工种共享；工效差异通过各自暴露分钟体现
            entry["kg"] += attr[block].confirmed_kg
            entry["min"] += _role_minutes(labor[block], role)
            entry["before"].append(blk.picked_before_pct)

    out: list[GroupStat] = []
    for (plot, slot, role), e in sorted(buckets.items()):
        p = data.plots[plot]
        out.append(GroupStat(
            plot=plot, slot=slot, role=role,
            n_blocks=len(e["blocks"]), n_teams=len(e["teams"]),
            slope_deg=p.slope_deg, haul_metres=p.haul_metres,
            before_min=min(e["before"]), before_max=max(e["before"]),
            kg_total=round(e["kg"], 2), exposure_min=e["min"],
            kg_per_exposure_min=round(e["kg"] / e["min"], 4) if e["min"] else 0.0,
            blocks=tuple(sorted(e["blocks"])),
        ))
    return out


@dataclass(frozen=True)
class SubsidyEvidence:
    plot: str
    slope_deg: float
    haul_metres: float
    morning_blocks: int
    afternoon_blocks: int
    teams: int
    comparable: bool
    verdict: str
    reason: str


def subsidy_evidence(data: SourceData) -> list[SubsidyEvidence]:
    """按地块判断记录能否支持难度补贴假设。"""
    agg: dict[str, dict] = {}
    for block, blk in data.blocks.items():
        if not block.startswith("B"):
            continue
        e = agg.setdefault(blk.plot, {"morning": 0, "afternoon": 0, "teams": set()})
        e["morning" if blk.slot == "0" else "afternoon"] += 1
        e["teams"].add(blk.team)

    out: list[SubsidyEvidence] = []
    for plot in sorted(agg):
        e = agg[plot]
        p = data.plots[plot]
        n_morn, n_after, teams = e["morning"], e["afternoon"], len(e["teams"])
        if plot == "E":
            verdict = "仅受限试排"
            comparable = False
            reason = (
                f"仅 {n_after} 个午后段、{teams} 个班组、最低已采 24%，"
                "无早班先采对照，不能支持补贴因果假设"
            )
        elif n_morn >= 5 and n_after >= 5 and teams >= 3:
            verdict = "证据支持"
            comparable = True
            reason = f"早班 {n_morn} 段、午后 {n_after} 段、{teams} 个班组，时段内条件重合充分"
        else:
            verdict = "对照不足"
            comparable = False
            reason = f"早班 {n_morn} 段、午后 {n_after} 段、{teams} 个班组，重合观察不足"
        out.append(SubsidyEvidence(
            plot, p.slope_deg, p.haul_metres, n_morn, n_after, teams,
            comparable, verdict, reason,
        ))
    return out
