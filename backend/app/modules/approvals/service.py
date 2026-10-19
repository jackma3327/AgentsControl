"""闸门业务规则：分级入队、日限额、决策、夜间 L0 静默、带宽快照。"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.core.errors import ConflictError, NotFoundError
from app.modules.approvals import breaker
from app.modules.approvals.models import QUEUE_LEVELS, Approval
from app.modules.approvals.repository import ApprovalRepository
from app.modules.approvals.schemas import ApprovalCreate, DecideIn
from app.modules.ledger import service as ledger_service
from app.modules.ledger.repository import LedgerRepository


def utcnow() -> datetime:
    return datetime.now(UTC)


# 日限额：L0 ≤2 个/日、L1 ≤8 个/日（超了自动转明日，不是丢掉）
GATE_DAILY_CAPS: dict[str, int] = {"L0": 2, "L1": 8}


def gate_daily_caps() -> dict[str, int]:
    caps = dict(GATE_DAILY_CAPS)
    for level in caps:
        raw = os.environ.get(f"AFC_GATE_{level}_DAILY")
        if raw and raw.strip().isdigit():
            caps[level] = int(raw)
    return caps


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    return start, start + timedelta(days=1)


# ---------------------------------------------------------------- I03 创建与队列


async def create_approval(
    repo: ApprovalRepository, payload: ApprovalCreate, *, now: datetime | None = None
) -> tuple[Approval, bool]:
    """创建审批。进人队级别超日限额 → visible_at 顺延一天（转明日）。

    返回 (approval, deferred)。
    """
    now = now or utcnow()
    visible_at = now
    deferred = False
    cap = gate_daily_caps().get(payload.level)
    if cap is not None:
        start, end = _day_bounds(now)
        created_today = await repo.count_created_between(payload.level, start, end)
        if created_today >= cap:
            visible_at = now + timedelta(days=1)
            deferred = True
    approval = Approval(
        level=payload.level,
        title=payload.title,
        payload=payload.payload,
        task_id=payload.task_id,
        goal_id=payload.goal_id,
        status="pending",
        visible_at=visible_at,
    )
    await repo.add(approval)
    await repo.commit()
    return approval, deferred


async def queue(
    repo: ApprovalRepository,
    *,
    levels: tuple[str, ...] | None = None,
    now: datetime | None = None,
    limit: int = 50,
) -> list[Approval]:
    """默认只返回 L0/L1（进队列打扰人的级别）；抽检页传 L2/L3。"""
    now = now or utcnow()
    return await repo.queue(levels=levels or QUEUE_LEVELS, now=now, limit=limit)


# ---------------------------------------------------------------- I03 决策


@dataclass(frozen=True, slots=True)
class DecideResult:
    approval: Approval
    held: bool  # True = 夜间 L0：只记录"待明日确认"，不给结论


async def decide(
    approval_repo: ApprovalRepository,
    approval_id: uuid.UUID,
    payload: DecideIn,
    *,
    decider: str,
    ledger_repo: LedgerRepository | None = None,
    now: datetime | None = None,
    config: breaker.BreakerConfig | None = None,
) -> DecideResult:
    """决策一条审批。

    - 不存在 → NotFoundError(404)；重复决策 → ConflictError(409)；
    - L0 在静默窗口（默认 23:00–07:00 本地时间）被决策 → 不给结论，标记
      held_reason/held_at，审批保持 pending（待明日确认）；
    - 正常决策 → 写 decided_at / decision / decider / reason（拒绝即记录，
      rejected 是一等决策）；
    - 带分钟数 → 同步计入注意力账本（source=gate）。
    """
    now = now or utcnow()
    config = config or breaker.load_breaker_config()
    approval = await approval_repo.get(approval_id)
    if approval is None:
        raise NotFoundError("审批不存在")
    if approval.status == "decided":
        raise ConflictError("该审批已决策，不允许重复决策")
    hour = breaker.local_hour(now.hour, config.night_tz_offset_hours)
    if approval.level == "L0" and breaker.is_night(hour, config):
        approval.held_reason = "night_window"
        approval.held_at = now
        await approval_repo.commit()
        return DecideResult(approval=approval, held=True)
    approval.status = "decided"
    approval.decided_at = now
    approval.decision = "approved" if payload.decision == "approve" else "rejected"
    approval.decider = decider
    approval.reason = payload.reason
    await approval_repo.commit()
    if payload.minutes is not None and ledger_repo is not None:
        await ledger_service.record_gate_minutes(
            ledger_repo,
            source_id=f"approval:{approval.id}",
            minutes=Decimal(payload.minutes),
            now=now,
            memo=f"gate decide {payload.decision}",
        )
    return DecideResult(approval=approval, held=False)


# ---------------------------------------------------------------- I04 带宽熔断


async def bandwidth_status(
    approval_repo: ApprovalRepository,
    ledger_repo: LedgerRepository,
    *,
    now: datetime | None = None,
    config: breaker.BreakerConfig | None = None,
) -> tuple[breaker.BreakerConfig, breaker.BreakerInput, breaker.BreakerVerdict]:
    """把两张表的事实压成 BreakerInput，交给纯函数 evaluate。"""
    now = now or utcnow()
    config = config or breaker.load_breaker_config()

    decisions_today = await approval_repo.decided_today(now)

    days = config.duration_window_days
    minutes_by_day: Mapping[date, Decimal] = await ledger_repo.gate_minutes_by_day(days, now)
    decisions_by_day = await approval_repo.decisions_by_day(days, now)
    daily_avg: list[float] = []
    for offset in range(days - 1, -1, -1):  # 旧 → 新
        day = (now - timedelta(days=offset)).date()
        total = minutes_by_day.get(day, Decimal(0))
        n = decisions_by_day.get(day, 0)
        avg = float(total) / n if n > 0 else 0.0
        daily_avg.append(avg)

    recent_start = now - timedelta(days=7)
    baseline_start = now - timedelta(days=14)
    approved_r, rejected_r = await approval_repo.decision_stats(recent_start, now)
    approved_b, rejected_b = await approval_repo.decision_stats(baseline_start, recent_start)

    def _rate(approved: int, rejected: int) -> Decimal:
        total = approved + rejected
        if total == 0:
            return Decimal("0")
        return (Decimal(rejected) / Decimal(total)).quantize(Decimal("0.0001"))

    hour = breaker.local_hour(now.hour, config.night_tz_offset_hours)
    inp = breaker.BreakerInput(
        decisions_today=decisions_today,
        daily_avg_minutes=tuple(daily_avg),
        veto_rate_recent=_rate(approved_r, rejected_r),
        veto_rate_baseline=_rate(approved_b, rejected_b),
        now_hour=hour,
    )
    verdict = breaker.evaluate(inp, config)
    return config, inp, verdict
