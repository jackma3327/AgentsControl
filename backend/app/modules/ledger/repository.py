"""账本数据访问。只接受/返回领域对象或行记录，不写业务规则。"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ledger.models import AttentionEntry, LedgerEntry, Subscription

GATE_SOURCE = "gate"


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    # ---------------------------------------------------------------- 钱账本

    async def add_entry(self, entry: LedgerEntry) -> LedgerEntry:
        self._session.add(entry)
        await self._session.flush()
        await self._session.refresh(entry)
        return entry

    async def find_entry(self, source: str, source_id: str) -> LedgerEntry | None:
        stmt = select(LedgerEntry).where(
            LedgerEntry.source == source, LedgerEntry.source_id == source_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_entries(
        self,
        *,
        task_id: uuid.UUID | None = None,
        goal_id: uuid.UUID | None = None,
        category: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> list[LedgerEntry]:
        stmt = select(LedgerEntry).order_by(LedgerEntry.occurred_at.desc()).limit(limit)
        if task_id is not None:
            stmt = stmt.where(LedgerEntry.task_id == task_id)
        if goal_id is not None:
            stmt = stmt.where(LedgerEntry.goal_id == goal_id)
        if category is not None:
            stmt = stmt.where(LedgerEntry.category == category)
        if start is not None:
            stmt = stmt.where(LedgerEntry.occurred_at >= start)
        if end is not None:
            stmt = stmt.where(LedgerEntry.occurred_at < end)
        return list((await self._session.execute(stmt)).scalars())

    async def sum_by_category(self, start: datetime, end: datetime) -> dict[str, Decimal]:
        stmt = (
            select(LedgerEntry.category, func.coalesce(func.sum(LedgerEntry.amount), 0))
            .where(LedgerEntry.occurred_at >= start, LedgerEntry.occurred_at < end)
            .group_by(LedgerEntry.category)
        )
        rows = (await self._session.execute(stmt)).all()
        return {str(category): Decimal(total) for category, total in rows}

    async def sum_by_source(self, start: datetime, end: datetime) -> dict[str, Decimal]:
        stmt = (
            select(LedgerEntry.source, func.coalesce(func.sum(LedgerEntry.amount), 0))
            .where(LedgerEntry.occurred_at >= start, LedgerEntry.occurred_at < end)
            .group_by(LedgerEntry.source)
        )
        rows = (await self._session.execute(stmt)).all()
        return {str(source): Decimal(total) for source, total in rows}

    async def sum_for_goal(self, goal_id: uuid.UUID) -> Decimal:
        stmt = select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.goal_id == goal_id
        )
        return Decimal((await self._session.execute(stmt)).scalar_one())

    async def get_goal_budget(self, goal_id: uuid.UUID) -> Decimal | None:
        """只读依赖（W02 的 goals 表）。表未落地时返回 None，不抛错。

        goals.id 是 String(36)，由 W02 的 new_id() 以 uuid4().hex（无连字符）生成，
        而入参是 UUID 对象 —— 两种文本形态都兜住，避免永远匹配不上。
        """
        try:
            row = (
                await self._session.execute(
                    text(
                        "SELECT budget_money FROM goals "
                        "WHERE id IN (:gid_hex, :gid_str)"
                    ),
                    {"gid_hex": goal_id.hex, "gid_str": str(goal_id)},
                )
            ).first()
        except ProgrammingError:
            # goals 表/列尚未由 W02-I03 落地 → 预算未知，返回 null
            await self._session.rollback()
            return None
        if row is None or row[0] is None:
            return None
        return Decimal(str(row[0]))

    # -------------------------------------------------------------- 注意力账本

    async def add_attention(self, entry: AttentionEntry) -> AttentionEntry:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def sum_attention_minutes(self, start: datetime, end: datetime) -> Decimal:
        stmt = select(func.coalesce(func.sum(AttentionEntry.minutes), 0)).where(
            AttentionEntry.occurred_at >= start,
            AttentionEntry.occurred_at < end,
        )
        return Decimal((await self._session.execute(stmt)).scalar_one())

    async def gate_minutes_by_day(self, days: int, now: datetime) -> dict[date, Decimal]:
        """最近 N 天每天的闸门注意力分钟数（用于带宽熔断的耗时趋势）。"""
        since = now - timedelta(days=days)
        stmt = (
            select(
                func.date(AttentionEntry.occurred_at).label("d"),
                func.coalesce(func.sum(AttentionEntry.minutes), 0),
            )
            .where(AttentionEntry.source == GATE_SOURCE, AttentionEntry.occurred_at >= since)
            .group_by(func.date(AttentionEntry.occurred_at))
        )
        rows = (await self._session.execute(stmt)).all()
        return {day: Decimal(total) for day, total in rows if day is not None}

    async def count_outcomes(self, start: datetime, end: datetime) -> int | None:
        """达标产出数（目标 achieved 或任务 done 且验收通过）。

        唯一口径定义见 attention.py 模块注释。goals（W02）/ tasks（W03）
        各自独立探测：未落地的表按 0 计、不拖累另一张表；
        两张表都没有时才返回 None（min/outcome 显示 null，而不是伪造 0）。
        """
        queries = (
            # goals 终态是 status 列（draft → active → {achieved, frozen, degraded}）
            "SELECT count(*) FROM goals "
            "WHERE status = 'achieved' "
            "AND achieved_at >= :s AND achieved_at < :e",
            "SELECT count(*) FROM tasks "
            "WHERE state = 'done' AND accepted = TRUE "
            "AND completed_at >= :s AND completed_at < :e",
        )
        total = 0
        landed_any = False
        for sql in queries:
            try:
                total += int(
                    (
                        await self._session.execute(text(sql), {"s": start, "e": end})
                    ).scalar_one()
                )
                landed_any = True
            except ProgrammingError:
                await self._session.rollback()
        return total if landed_any else None

    # ------------------------------------------------------------------ 订阅

    async def add_subscription(self, sub: Subscription) -> Subscription:
        self._session.add(sub)
        await self._session.flush()
        await self._session.refresh(sub)
        return sub

    async def get_subscription(self, sub_id: uuid.UUID) -> Subscription | None:
        return await self._session.get(Subscription, sub_id)

    async def list_subscriptions(self) -> list[Subscription]:
        stmt = select(Subscription).order_by(Subscription.created_at.desc())
        return list((await self._session.execute(stmt)).scalars())
