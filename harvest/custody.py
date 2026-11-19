"""封筐归属：事实归属与分配可能范围严格分开。

* 已签且只有一段：整筐交接，按净重实数计入该段。
* 已签且多段带 confirmed_kg：按已签份额实数计入各段。
* 待分（同筐两段共用、无份额）：不按时长硬分，只输出每段的可能范围
  ``[0, 净重]``，并在工资处理中挂起相关计件差额。
所有结论都以 cutoff 时点可见的最新版本为准。
"""
from __future__ import annotations

from dataclasses import dataclass

from .dataio import SourceData
from .evidence import AsOf, latest_custody


@dataclass(frozen=True)
class BlockKg:
    block: str
    confirmed_kg: float          # 实数归属（整筐交接或已签份额）
    possible_min_kg: float       # 含待分筐的可能下界
    possible_max_kg: float       # 含待分筐的可能上界
    pending_baskets: tuple[str, ...]
    pending_kg: float            # 涉及的待分净重（不重复计入 confirmed）


def build_attribution(data: SourceData, asof: AsOf) -> dict[str, BlockKg]:
    confirmed: dict[str, float] = {b: 0.0 for b in data.blocks}
    pmin: dict[str, float] = {b: 0.0 for b in data.blocks}
    pmax: dict[str, float] = {b: 0.0 for b in data.blocks}
    pending: dict[str, list[str]] = {b: [] for b in data.blocks}
    pending_kg: dict[str, float] = {b: 0.0 for b in data.blocks}

    for basket, b in data.baskets.items():
        rows = latest_custody(data, basket, asof)
        if not rows:
            raise ValueError(f"筐 {basket} 在 {asof.label} 无任何可见归属记录")
        signed = [r for r in rows if r.signed]
        if signed:
            if len(signed) == 1 and signed[0].confirmed_kg is None:
                # 整筐交接
                blk = signed[0].block
                confirmed[blk] += b.scale_kg
                pmin[blk] += b.scale_kg
                pmax[blk] += b.scale_kg
            else:
                total = 0.0
                for r in signed:
                    if r.confirmed_kg is None:
                        raise ValueError(f"筐 {basket} 多段已签但缺少份额，无法实数归属")
                    confirmed[r.block] += r.confirmed_kg
                    pmin[r.block] += r.confirmed_kg
                    pmax[r.block] += r.confirmed_kg
                    total += r.confirmed_kg
                if abs(total - b.scale_kg) > 1e-6:
                    raise ValueError(
                        f"筐 {basket} 已签份额 {total:g} 与净重 {b.scale_kg:g} 不符"
                    )
        else:
            # 待分：只给可能范围，不充实数
            candidates = sorted({r.block for r in rows})
            for blk in candidates:
                pending[blk].append(basket)
                pending_kg[blk] += b.scale_kg
                pmax[blk] += b.scale_kg
            # 下界保持 0：该筐可能整归另一候选段

    return {
        blk: BlockKg(
            blk, confirmed[blk], pmin[blk], pmax[blk],
            tuple(pending[blk]), pending_kg[blk],
        )
        for blk in data.blocks
    }
