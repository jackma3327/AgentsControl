"""goals 数据访问层。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError

from .models import Goal


class GoalRepository(Protocol):
    async def create(self, goal: Goal) -> Goal:
        ...

    async def get(self, goal_id: str, *, owner: str) -> Goal:
        ...

    async def get_by_code(self, code: str, *, owner: str) -> Goal:
        ...

    async def list_by_owner(
        self,
        owner: str,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[Goal]:
        ...

    async def update(self, goal_id: str, updates: dict, *, at: datetime | None = None) -> Goal:
        ...

    async def next_seq_for(self, track: str, date: datetime) -> int:
        """当日单轨最大序号 + 1（基于 code 列前缀匹配）。"""
        ...

    # Ledger 桥接（W07 未实现时返回 None）
    async def budget_used_for(self, goal_id: str) -> float | None:
        ...

    # Intake 标记（W02-I03 与 W02-I05 共用）
    async def mark_intake_promoted(self, intake_item_id: str, goal_id: str, at: datetime) -> None:
        ...

    async def commit(self) -> None:
        """提交当前事务。service 写路径末尾显式调用（S2/R04）。

        背景：`core/db.py#get_db()` 语义是"业务层不 commit"，goals 的
        create/update 只 flush，导致 POST 201 / PATCH 200 后数据静默丢失。
        提交权归 service 层；repository 的写方法仍只负责 flush。
        """
        ...


class SqlGoalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(self, goal: Goal) -> Goal:
        self._s.add(goal)
        try:
            await self._s.flush()
        except Exception as exc:
            # unique(code) 冲突 → ConflictError
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ConflictError("目标编号冲突，请重试") from exc
            raise
        return goal

    async def get(self, goal_id: str, *, owner: str) -> Goal:
        stmt = select(Goal).where(and_(Goal.id == goal_id, Goal.owner_subject == owner))
        goal = (await self._s.execute(stmt)).scalar_one_or_none()
        if goal is None:
            raise NotFoundError("目标不存在或不属于当前用户")
        return goal

    async def get_by_code(self, code: str, *, owner: str) -> Goal:
        stmt = select(Goal).where(and_(Goal.code == code, Goal.owner_subject == owner))
        goal = (await self._s.execute(stmt)).scalar_one_or_none()
        if goal is None:
            raise NotFoundError("目标不存在或不属于当前用户")
        return goal

    async def list_by_owner(
        self,
        owner: str,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[Goal]:
        conds = [Goal.owner_subject == owner]
        if statuses:
            conds.append(Goal.status.in_(statuses))
        stmt = select(Goal).where(*conds).order_by(desc(Goal.created_at)).limit(limit)
        return list((await self._s.execute(stmt)).scalars())

    async def update(self, goal_id: str, updates: dict, *, at: datetime | None = None) -> Goal:
        from sqlalchemy import update as sa_update

        if at is not None:
            updates = {**updates, "updated_at": at}
        stmt = sa_update(Goal).where(Goal.id == goal_id).values(**updates).returning(Goal)
        result = await self._s.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("目标不存在")
        return row

    async def next_seq_for(self, track: str, date: datetime) -> int:

        prefix = f"O-{'M' if track == 'money' else 'E'}-{date.strftime('%Y%m%d')}-"
        # SQLite 与 PG 都支持 LIKE
        stmt = select(func.max(Goal.code)).where(Goal.code.like(prefix + "%"))
        current = (await self._s.execute(stmt)).scalar_one_or_none()
        if not current:
            return 1
        try:
            return int(current.rsplit("-", 1)[-1]) + 1
        except ValueError:
            return 1

    async def budget_used_for(self, goal_id: str) -> float | None:
        """W07 账本未实现时返回 None（service 用 unknown 表达）。"""
        return None

    async def mark_intake_promoted(self, intake_item_id: str, goal_id: str, at: datetime) -> None:
        from sqlalchemy import update as sa_update

        from app.modules.intake.models import IntakeItem

        stmt = (
            sa_update(IntakeItem)
            .where(IntakeItem.id == intake_item_id)
            .values(status="promoted", promoted_goal_id=goal_id)
        )
        await self._s.execute(stmt)

    async def commit(self) -> None:
        await self._s.commit()


def new_id() -> str:
    return uuid.uuid4().hex


def _normalize_json_object(value: Any) -> dict[str, Any] | None:
    """把库里的 JSON 列归一成 GoalOut 契约的 dict[str, Any] | None。

    背景（CODE-REVIEW P1-1）：历史数据里混进过数组形态的 guardrails /
    deliverables（评审时手工插库 + 模板 D 曾经把 guardrails 写成 set 笔误），
    GoalOut 声明是 dict，一条脏数据会让整个列表 500。这里在出口归一：
    数组 → {"items": [...]}，标量 → {"value": ...}，None / dict 原样。
    契约不变，前端无感；写路径不做兼容（脏数据不应再产生）。
    """
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, (list, tuple, set)):
        return {"items": list(value)}
    return {"value": value}


def to_out(goal: Goal):
    from .schemas import GoalOut

    return GoalOut(
        id=goal.id,
        code=goal.code,
        owner_subject=goal.owner_subject,
        track=goal.track,
        template=goal.template,
        title=goal.title,
        acceptance=goal.acceptance,
        horizon=goal.horizon,
        horizon_at=goal.horizon_at,
        budget_money=float(goal.budget_money) if goal.budget_money is not None else None,
        budget_hours=float(goal.budget_hours) if goal.budget_hours is not None else None,
        attention_minutes_per_day=goal.attention_minutes_per_day,
        north_star=goal.north_star,
        guardrails=_normalize_json_object(goal.guardrails),
        deliverables=_normalize_json_object(goal.deliverables),
        status=goal.status,  # type: ignore[arg-type]
        intake_item_id=goal.intake_item_id,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
        achieved_at=goal.achieved_at,
        frozen_at=goal.frozen_at,
        degraded_at=goal.degraded_at,
        stop_loss_triggered_at=goal.stop_loss_triggered_at,
    )


__all__ = ["GoalRepository", "SqlGoalRepository", "new_id", "to_out"]