"""时点（as-of）证据范围。

所有分析都必须声明依据哪个交班日 cutoff：只采用 known_at / confirmed_at
不晚于 cutoff 的记录版本。后来补齐的归属改善以后的分析，但不回写历史结论。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .dataio import SourceData, CustodyRow, Loan


@dataclass(frozen=True)
class AsOf:
    cutoff: datetime
    label: str

    def visible(self, known_at: datetime) -> bool:
        return known_at <= self.cutoff


def policy_cutoffs(data: SourceData) -> list[AsOf]:
    out = []
    for i, raw in enumerate(data.policy["cutoffs"], start=1):
        ts = datetime.fromisoformat(raw)
        out.append(AsOf(ts, f"cut{i} {raw}"))
    return out


def visible_loans(data: SourceData, asof: AsOf) -> list[Loan]:
    return [ln for ln in data.loans if asof.visible(ln.confirmed_at)]


def latest_custody(data: SourceData, basket: str, asof: AsOf) -> list[CustodyRow]:
    """返回该筐在 cutoff 时点可见的最新一个版本的全部归属行。"""
    avail = [r for r in data.custody if r.basket == basket and asof.visible(r.known_at)]
    if not avail:
        return []
    latest = max(r.ver for r in avail)
    return [r for r in avail if r.ver == latest]
