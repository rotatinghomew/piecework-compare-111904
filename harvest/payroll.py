"""工资复核：原口径重算、借工挂起、申诉差额凭证。

不可违背的约束：
* 计件价与工时保底一律取作业段自身 table_version 对应的价目表；试价不回溯。
* 已确认工资单（payslips）金额不被重算覆盖；重算只产生差额凭证建议。
* 借工待分清时，只挂起与待分相关的计件差额；双方明确的工时保底和其他地段照办。
* 共享筐归属变化时同时重算两个班的保底与计件，不能拿一边多付抵另一边应补。
"""
from __future__ import annotations

from dataclasses import dataclass

from .dataio import SourceData
from .evidence import AsOf
from .labor import BlockLabor
from .custody import BlockKg


def round_cents(value: float) -> int:
    """最终差额按分办：四舍五入到整数分。"""
    return int(value + 0.5 if value >= 0 else value - 0.5)


@dataclass(frozen=True)
class BlockPayroll:
    block: str
    table_version: str
    rate_cents_per_kg: float
    floor_hour_cents: float
    paid_min: float
    confirmed_kg: float
    piece_cents: int
    floor_cents: int
    payable_cents: int            # max(计件, 保底)，待分件不计入
    pending_kg: float
    pending_piece_low: int        # 待分筐若全归别班
    pending_piece_high: int       # 待分筐若全归本班
    has_unallocated_labor: bool
    note: str


def recompute_block(
    data: SourceData, block: str, labor: BlockLabor, attr: BlockKg
) -> BlockPayroll:
    blk = data.blocks[block]
    rate = data.pay_table[blk.table_version]
    piece = round_cents(attr.confirmed_kg * rate.cents_per_kg)
    floor = round_cents(labor.paid_min / 60.0 * rate.floor_hour_cents)
    low = piece
    high = round_cents((attr.confirmed_kg + attr.pending_kg) * rate.cents_per_kg)
    note_parts = []
    if attr.pending_baskets:
        note_parts.append(f"待分筐 {','.join(attr.pending_baskets)} 计件差额挂起")
    if labor.unallocated_min > 0:
        note_parts.append(f"借工待分清挂起 {labor.unallocated_min:g} 分钟，不计保底")
    return BlockPayroll(
        block=block,
        table_version=blk.table_version,
        rate_cents_per_kg=rate.cents_per_kg,
        floor_hour_cents=rate.floor_hour_cents,
        paid_min=labor.paid_min,
        confirmed_kg=attr.confirmed_kg,
        piece_cents=piece,
        floor_cents=floor,
        payable_cents=max(piece, floor),
        pending_kg=attr.pending_kg,
        pending_piece_low=low,
        pending_piece_high=high,
        has_unallocated_labor=labor.unallocated_min > 0,
        note="；".join(note_parts),
    )


def recompute_all(
    data: SourceData, asof: AsOf, labor: dict[str, BlockLabor], attr: dict[str, BlockKg]
) -> dict[str, BlockPayroll]:
    return {
        block: recompute_block(data, block, labor[block], attr[block])
        for block in data.blocks
    }


@dataclass(frozen=True)
class PayslipRecheck:
    document: str
    block: str
    payslip_cents: int
    evidence: tuple[str, ...]
    recomputed_cents: int
    delta_voucher_cents: int     # 建议补发的差额凭证（不重复覆盖原单）
    status: str
    detail: str


def recheck_payslips(
    data: SourceData, payroll: dict[str, BlockPayroll], asof: AsOf
) -> list[PayslipRecheck]:
    out: list[PayslipRecheck] = []
    for slip in data.payslips:
        if slip.confirmed_at > asof.cutoff:
            continue  # 该时点工资单尚未形成
        rec = payroll.get(slip.block)
        if rec is None:
            continue
        # 待分未决：不发差额凭证，只说明挂起范围
        if rec.pending_kg > 0 or rec.has_unallocated_labor:
            status = "挂起"
            delta = 0
            detail = (
                f"原单 {slip.cents} 分不动；待分件区间 "
                f"{rec.pending_piece_low}-{rec.pending_piece_high} 分，"
                f"当前可办保底/已确认计件 {rec.payable_cents} 分"
            )
        else:
            delta = max(0, rec.payable_cents - slip.cents)
            status = "差额凭证" if delta > 0 else "无差额"
            detail = (
                f"重算应付 {rec.payable_cents} 分（计件 {rec.piece_cents} / "
                f"保底 {rec.floor_cents} 取高），原单 {slip.cents} 分"
            )
        out.append(PayslipRecheck(
            slip.document, slip.block, slip.cents, slip.evidence,
            rec.payable_cents, delta, status, detail,
        ))
    return out
