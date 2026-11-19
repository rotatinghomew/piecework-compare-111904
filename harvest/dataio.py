"""原始记录加载与硬校验。

只读取 data/ 下的合成 CSV/JSON，不做任何业务分摊。校验失败直接报错，
避免下游把空值当零、把重复身份当两条事实。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _read_csv(name: str) -> list[dict[str, str]]:
    with (DATA / f"{name}.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    for row in rows:
        # 契约测试保证无 None 键；这里再防一次空字符串混入关键身份列
        if any(v is None for v in row.values()):
            raise ValueError(f"{name} 存在空列: {row}")
    return rows


@dataclass(frozen=True)
class Plot:
    plot: str
    slope_deg: float
    haul_metres: float
    surface: str


@dataclass(frozen=True)
class WorkBlock:
    block: str
    plot: str
    team: str
    started: datetime
    ended: datetime
    picked_before_pct: float
    table_version: str

    @property
    def slot(self) -> str:
        """0=早班(先采段)，1=午/夜班(后采段)，X 段按自身编号保留。"""
        return self.block[-1]

    @property
    def minutes(self) -> float:
        return (self.ended - self.started).total_seconds() / 60.0


@dataclass(frozen=True)
class RosterRow:
    worker: str
    block: str
    role: str
    from_time: datetime
    to_time: datetime

    @property
    def minutes(self) -> float:
        return (self.to_time - self.from_time).total_seconds() / 60.0


@dataclass(frozen=True)
class Basket:
    basket: str
    scale_kg: float
    sealed_at: datetime


@dataclass(frozen=True)
class CustodyRow:
    basket: str
    block: str
    ver: str
    signed: bool
    known_at: datetime
    confirmed_kg: float | None  # None=整筐交接；浮点数=该版本下本段份额


@dataclass(frozen=True)
class WaitingRow:
    block: str
    from_time: datetime
    to_time: datetime
    reason: str

    @property
    def minutes(self) -> float:
        return (self.to_time - self.from_time).total_seconds() / 60.0


@dataclass(frozen=True)
class Loan:
    worker: str
    home_block: str
    borrowed_block: str
    from_time: datetime
    to_time: datetime
    confirmed_at: datetime

    @property
    def minutes(self) -> float:
        return (self.to_time - self.from_time).total_seconds() / 60.0


@dataclass(frozen=True)
class PayRate:
    version: str
    cents_per_kg: float
    floor_hour_cents: float
    transfer_floor_party: str


@dataclass(frozen=True)
class Payslip:
    document: str
    block: str
    cents: int
    confirmed_at: datetime
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class SeasonSlot:
    proposal: str
    plot: str
    month: str
    max_picks_min: float
    available_days: int
    required_skill: str
    max_trial_days: int


@dataclass(frozen=True)
class CrewChoice:
    proposal: str
    worker: str
    skills: tuple[str, ...]
    day: str
    start_time: str
    end_time: str
    claim_party: str


@dataclass(frozen=True)
class Agreement:
    agreement: str
    proposal: str
    worker: str
    start_at: datetime
    end_at: datetime
    guaranteed_cents: int
    state: str
    known_at: datetime


@dataclass(frozen=True)
class ReserveEvent:
    dispute: str
    block: str
    cents: int
    stage: str
    known_at: datetime


@dataclass(frozen=True)
class SourceData:
    plots: dict[str, Plot]
    blocks: dict[str, WorkBlock]
    roster: list[RosterRow]
    baskets: dict[str, Basket]
    custody: list[CustodyRow]
    waiting: list[WaitingRow]
    loans: list[Loan]
    pay_table: dict[str, PayRate]
    payslips: list[Payslip]
    slots: dict[str, SeasonSlot]
    crew: list[CrewChoice]
    agreements: list[Agreement]
    reserves: list[ReserveEvent]
    policy: dict

    def custody_for(self, basket: str, ver: str) -> list[CustodyRow]:
        return [r for r in self.custody if r.basket == basket and r.ver == ver]


def _as_float(value: str) -> float | None:
    return float(value) if value != "" else None


def load_sources() -> SourceData:
    plots = {
        r["plot"]: Plot(r["plot"], float(r["slope_deg"]), float(r["haul_metres"]), r["surface"])
        for r in _read_csv("plots")
    }
    blocks = {
        r["block"]: WorkBlock(
            r["block"], r["plot"], r["team"], parse_dt(r["started"]), parse_dt(r["ended"]),
            float(r["picked_before_pct"]), r["table_version"],
        )
        for r in _read_csv("work")
    }
    roster = [
        RosterRow(r["worker"], r["block"], r["role"], parse_dt(r["from_time"]), parse_dt(r["to_time"]))
        for r in _read_csv("staff")
    ]
    baskets = {
        r["basket"]: Basket(r["basket"], float(r["scale_kg"]), parse_dt(r["sealed_at"]))
        for r in _read_csv("baskets")
    }
    custody = [
        CustodyRow(
            r["basket"], r["block"], r["ver"], r["signed"] == "已签",
            parse_dt(r["known_at"]), _as_float(r["confirmed_kg"]),
        )
        for r in _read_csv("custody")
    ]
    waiting = [
        WaitingRow(r["block"], parse_dt(r["from_time"]), parse_dt(r["to_time"]), r["reason"])
        for r in _read_csv("waiting")
    ]
    loans = [
        Loan(r["worker"], r["home_block"], r["borrowed_block"],
             parse_dt(r["from_time"]), parse_dt(r["to_time"]), parse_dt(r["confirmed_at"]))
        for r in _read_csv("loans")
    ]
    pay_table = {
        r["version"]: PayRate(r["version"], float(r["cents_per_kg"]),
                              float(r["floor_hour_cents"]), r["transfer_floor_party"])
        for r in _read_csv("pay_table")
    }
    payslips = [
        Payslip(r["document"], r["block"], int(r["cents"]), parse_dt(r["confirmed_at"]),
                tuple(r["evidence"].split(";")))
        for r in _read_csv("payslips")
    ]
    slots = {
        r["proposal"]: SeasonSlot(
            r["proposal"], r["plot"], r["month"], float(r["max_picks_min"]),
            int(r["available_days"]), r["required_skill"], int(r["max_trial_days"]),
        )
        for r in _read_csv("season_slots")
    }
    crew = [
        CrewChoice(r["proposal"], r["worker"], tuple(r["skills"].split(";")),
                   r["day"], r["start_time"], r["end_time"], r["claim_party"])
        for r in _read_csv("crew_choices")
    ]
    agreements = [
        Agreement(r["agreement"], r["proposal"], r["worker"], parse_dt(r["start_at"]),
                  parse_dt(r["end_at"]), int(r["guaranteed_cents"]), r["state"],
                  parse_dt(r["known_at"]))
        for r in _read_csv("season_agreements")
    ]
    reserves = [
        ReserveEvent(r["dispute"], r["block"], int(r["cents"]), r["stage"], parse_dt(r["known_at"]))
        for r in _read_csv("dispute_reserves")
    ]
    policy = json.loads((DATA / "policy.json").read_text(encoding="utf-8"))

    data = SourceData(plots, blocks, roster, baskets, custody, waiting, loans,
                      pay_table, payslips, slots, crew, agreements, reserves, policy)
    _validate(data)
    return data


def _validate(data: SourceData) -> None:
    # 作业段引用完整性
    for row in data.roster:
        if row.block not in data.blocks:
            raise ValueError(f"名册引用未知作业段 {row.block}")
    for row in data.waiting:
        if row.block not in data.blocks:
            raise ValueError(f"停等引用未知作业段 {row.block}")
    for loan in data.loans:
        for block in (loan.home_block, loan.borrowed_block):
            if block not in data.blocks:
                raise ValueError(f"借工引用未知作业段 {block}")
    # 封筐身份不重复；归属行引用的筐存在
    if len(data.baskets) != len(_read_csv("baskets")):
        raise ValueError("筐身份重复")
    for row in data.custody:
        if row.basket not in data.baskets:
            raise ValueError(f"归属记录引用未知筐 {row.basket}")
        if row.block not in data.blocks:
            raise ValueError(f"归属记录引用未知作业段 {row.block}")
    # 同筐同版本份额合计不得超过净重（含容差）
    by_ver: dict[tuple[str, str], list[CustodyRow]] = {}
    for row in data.custody:
        by_ver.setdefault((row.basket, row.ver), []).append(row)
    for (basket, _ver), rows in by_ver.items():
        parts = [r.confirmed_kg for r in rows if r.confirmed_kg is not None]
        if parts and sum(parts) > data.baskets[basket].scale_kg + 1e-9:
            raise ValueError(f"筐 {basket} 份额合计超过净重")
    # 时间窗：名册、停等必须落在作业段内
    for row in data.roster:
        blk = data.blocks[row.block]
        if row.from_time < blk.started or row.to_time > blk.ended:
            raise ValueError(f"名册时间越界 {row.worker}@{row.block}")
    for row in data.waiting:
        blk = data.blocks[row.block]
        if row.from_time < blk.started or row.to_time > blk.ended:
            raise ValueError(f"停等时间越界 {row.block}")
    # 价目表版本完整
    for blk in data.blocks.values():
        if blk.table_version not in data.pay_table:
            raise ValueError(f"作业段 {blk.block} 引用未知价目表 {blk.table_version}")
    # 准备金事件按 (dispute, known_at) 唯一（同号覆盖靠时间线区分）
    seen = set()
    for ev in data.reserves:
        key = (ev.dispute, ev.known_at)
        if key in seen:
            raise ValueError(f"准备金事件时间戳重复 {key}")
        seen.add(key)
