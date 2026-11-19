"""有效劳动重建：借工去重、停等分离、待分清挂起。

口径（同一时刻一个人只能在一个作业段形成有效劳动）：

* 借工生效窗内的分钟记借入段；借出段名册覆盖借窗的部分从借出段有效劳时中剔除。
* cutoff 时点调令尚未确认时，两段名册与借窗重叠的分钟不记任何一段的有效劳动，
  单列为 ``unallocated_min``（借工待分清），不按时长硬分。
* 停等分钟保留在已确认出勤（paid_min，工时保底照算），但从工效暴露
  （exposure_min）中剔除——等待装车不是采不到熟果。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .dataio import SourceData, RosterRow, Loan
from .evidence import AsOf, visible_loans


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    lo, hi = max(a0, b0), min(a1, b1)
    return max(0.0, (hi - lo).total_seconds() / 60.0)


@dataclass
class WorkerLabor:
    worker: str
    block: str
    role: str
    roster_min: float = 0.0
    paid_min: float = 0.0          # 计保底的已确认出勤分钟（含停等）
    exposure_min: float = 0.0      # 工效暴露分钟（不含停等、不含借窗让出/待分）
    waiting_min: float = 0.0
    loan_out_min: float = 0.0      # 已确认借出，计入借入段
    loan_in_min: float = 0.0
    unallocated_min: float = 0.0   # 借工待分清：两段都不计有效劳动
    notes: list[str] = field(default_factory=list)


@dataclass
class BlockLabor:
    block: str
    by_worker: dict[str, WorkerLabor]

    @property
    def paid_min(self) -> float:
        return sum(w.paid_min for w in self.by_worker.values())

    @property
    def exposure_min(self) -> float:
        return sum(w.exposure_min for w in self.by_worker.values())

    @property
    def unallocated_min(self) -> float:
        return sum(w.unallocated_min for w in self.by_worker.values())

    def role_exposure(self, role: str) -> float:
        return sum(w.exposure_min for w in self.by_worker.values() if w.role == role)


def _waiting_intervals(data: SourceData, block: str) -> list[tuple[datetime, datetime]]:
    return [(r.from_time, r.to_time) for r in data.waiting if r.block == block]


def _loan_for(data: SourceData, worker: str, asof: AsOf) -> Loan | None:
    for ln in visible_loans(data, asof):
        if ln.worker == worker:
            return ln
    return None


def build_labor(data: SourceData, asof: AsOf) -> dict[str, BlockLabor]:
    result: dict[str, dict[str, WorkerLabor]] = {}
    for row in data.roster:
        result.setdefault(row.block, {})[row.worker] = WorkerLabor(
            worker=row.worker, block=row.block, role=row.role,
            roster_min=row.minutes,
        )

    for row in data.roster:
        rec = result[row.block][row.worker]
        loan = _loan_for(data, row.worker, asof)
        waits = _waiting_intervals(data, row.block)
        wait_min = sum(_overlap(row.from_time, row.to_time, *w) for w in waits)
        rec.waiting_min = wait_min

        if loan is None:
            # 无已确认调令：整段名册都是本块出勤
            rec.paid_min = row.minutes
            rec.exposure_min = row.minutes - wait_min
            # 若存在尚未确认的借工调令，重叠部分挂起
            pending = next((ln for ln in data.loans
                            if ln.worker == row.worker and not asof.visible(ln.confirmed_at)), None)
            if pending is not None:
                pend = _overlap(row.from_time, row.to_time, pending.from_time, pending.to_time)
                rec.unallocated_min = pend
                # 借窗与停等互斥（_assert_wait_loan_disjoint 已校验），可直接相减
                rec.paid_min = row.minutes - pend
                rec.exposure_min = rec.paid_min - wait_min
                rec.notes.append(f"借工待分清挂起 {pend:g} 分钟")
            else:
                rec.paid_min = row.minutes
                rec.exposure_min = row.minutes - wait_min
            continue

        in_window = _overlap(row.from_time, row.to_time, loan.from_time, loan.to_time)
        if row.block == loan.borrowed_block and in_window > 0:
            rec.loan_in_min = in_window
            rec.paid_min = row.minutes            # 借入段整段名册计出勤（含借窗）
            rec.exposure_min = row.minutes - wait_min
            rec.notes.append(f"借入窗 {in_window:g} 分钟计本块")
        elif row.block == loan.home_block and in_window > 0:
            rec.loan_out_min = in_window
            rec.paid_min = row.minutes - in_window
            rec.exposure_min = rec.paid_min - wait_min
            rec.notes.append(f"借出窗 {in_window:g} 分钟从本块剔除")
        else:
            rec.paid_min = row.minutes
            rec.exposure_min = row.minutes - wait_min

    _assert_wait_loan_disjoint(data)
    return {block: BlockLabor(block, workers) for block, workers in result.items()}


def _assert_wait_loan_disjoint(data: SourceData) -> None:
    """本数据集里借窗与停等不重叠；若未来数据出现重叠，必须显式定口径而不是静默相减。"""
    for ln in data.loans:
        for wr in data.waiting:
            if wr.block in (ln.home_block, ln.borrowed_block) and _overlap(
                ln.from_time, ln.to_time, wr.from_time, wr.to_time
            ) > 0:
                raise ValueError(
                    f"借窗与停等重叠，需要显式口径: {ln.worker} vs {wr.block}"
                )
