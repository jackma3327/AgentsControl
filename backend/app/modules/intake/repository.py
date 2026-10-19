"""intake 模块数据访问层。

设计原则：
- `IntakeRepository` 是 Protocol（接口），便于测试用 fake 替换；
- `SqlIntakeRepository` 是生产实现，依赖 `AsyncSession`；
- service 不直接依赖 SqlIntakeRepository，只依赖 Protocol，便于在
  单测中注入 fake。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError

from .models import IntakeItem
from .schemas import IntakeItemCreate, IntakeItemOut


def hash_text(owner: str, text: str) -> str:
    """同一 owner + 文本的稳定哈希，用于 5 分钟去重。"""
    return hashlib.sha256(f"{owner}\x1f{text[:2000]}".encode()).hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex  # 32 字符无连字符，便于索引与日志


class IntakeRepository(Protocol):
    async def create_or_merge(
        self, owner: str, payload: IntakeItemCreate, *, dedup_window: timedelta
    ) -> tuple[IntakeItem, bool]:
        """返回 (item, created)。created=False 表示命中 5 分钟去重返回既有记录。"""
        ...

    async def get(self, item_id: str, *, owner: str) -> IntakeItem:
        ...

    async def list_by_owner(
        self,
        owner: str,
        *,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
        statuses: tuple[str, ...] | None = None,
    ) -> tuple[list[IntakeItem], bool]:
        """keyset 分页：按 (created_at DESC, id DESC)。

        返回 (items, has_more)。has_more=True 时存在更旧记录。
        """
        ...

    async def list_interests(self, owner: str, *, limit: int = 100) -> list[IntakeItem]:
        ...

    async def list_stale_raw(
        self, before: datetime, *, limit: int = 500
    ) -> list[IntakeItem]:
        """14 天未结构化的 raw 条目，供归档脚本用。"""
        ...

    async def archive_as_interest(
        self, item_ids: list[str], *, at: datetime
    ) -> int:
        """批量转 archived_interest。返回受影响行数。幂等：已 archived 的不会再改。"""
        ...

    async def save_structure(
        self,
        item_id: str,
        *,
        structured: dict,
        model: str,
        cost_usd: float,
        at: datetime,
    ) -> IntakeItem:
        ...

    async def mark_promoted(
        self, item_id: str, *, goal_id: str, at: datetime
    ) -> None:
        ...

    async def commit(self) -> None:
        """提交当前事务。service 写路径末尾显式调用（S2/R04）。

        背景：`core/db.py#get_db()` 语义是"业务层不 commit"，intake 的
        `create_or_merge` 只 flush，导致 POST 201 后数据随会话关闭丢失。
        提交权归 service 层；repository 的写方法仍只负责 flush。
        """
        ...


class SqlIntakeRepository:
    """生产实现。所有写入由调用方在事务里 commit。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create_or_merge(
        self, owner: str, payload: IntakeItemCreate, *, dedup_window: timedelta
    ) -> tuple[IntakeItem, bool]:
        cutoff = datetime.utcnow() - dedup_window
        stmt = (
            select(IntakeItem)
            .where(
                and_(
                    IntakeItem.owner_subject == owner,
                    IntakeItem.text_hash == hash_text(owner, payload.text),
                    IntakeItem.created_at >= cutoff,
                    IntakeItem.status != "archived_interest",
                )
            )
            .order_by(desc(IntakeItem.created_at))
            .limit(1)
        )
        existing = (await self._s.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return existing, False

        now = datetime.utcnow()
        item = IntakeItem(
            id=new_id(),
            owner_subject=owner,
            text=payload.text,
            text_hash=hash_text(owner, payload.text),
            source=payload.source,
            status="raw",
            created_at=now,
        )
        self._s.add(item)
        await self._s.flush()
        return item, True

    async def get(self, item_id: str, *, owner: str) -> IntakeItem:
        stmt = select(IntakeItem).where(
            and_(IntakeItem.id == item_id, IntakeItem.owner_subject == owner)
        )
        item = (await self._s.execute(stmt)).scalar_one_or_none()
        if item is None:
            raise NotFoundError("intake item 不存在或不属于当前用户")
        return item

    async def list_by_owner(
        self,
        owner: str,
        *,
        cursor_created_at: datetime | None,
        cursor_id: str | None,
        limit: int,
        statuses: tuple[str, ...] | None = None,
    ) -> tuple[list[IntakeItem], bool]:
        conds = [IntakeItem.owner_subject == owner]
        if statuses:
            conds.append(IntakeItem.status.in_(statuses))
        if cursor_created_at is not None and cursor_id is not None:
            # keyset：(created_at, id) < (cursor.created_at, cursor.id) 且降序
            conds.append(
                (
                    IntakeItem.created_at < cursor_created_at
                )
                | (
                    (IntakeItem.created_at == cursor_created_at)
                    & (IntakeItem.id < cursor_id)
                )
            )
        stmt = (
            select(IntakeItem)
            .where(*conds)
            .order_by(desc(IntakeItem.created_at), desc(IntakeItem.id))
            .limit(limit + 1)
        )
        rows = list((await self._s.execute(stmt)).scalars())
        has_more = len(rows) > limit
        items = rows[:limit]
        return items, has_more

    async def list_interests(self, owner: str, *, limit: int = 100) -> list[IntakeItem]:
        stmt = (
            select(IntakeItem)
            .where(
                and_(
                    IntakeItem.owner_subject == owner,
                    IntakeItem.status == "archived_interest",
                )
            )
            .order_by(desc(IntakeItem.created_at))
            .limit(limit)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def list_stale_raw(
        self, before: datetime, *, limit: int = 500
    ) -> list[IntakeItem]:
        stmt = (
            select(IntakeItem)
            .where(
                and_(
                    IntakeItem.status == "raw",
                    IntakeItem.created_at < before,
                )
            )
            .order_by(IntakeItem.created_at.asc())
            .limit(limit)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def archive_as_interest(
        self, item_ids: list[str], *, at: datetime
    ) -> int:
        if not item_ids:
            return 0
        from sqlalchemy import update

        stmt = (
            update(IntakeItem)
            .where(
                and_(
                    IntakeItem.id.in_(item_ids),
                    IntakeItem.status == "raw",
                )
            )
            .values(status="archived_interest", archived_at=at)
        )
        result = await self._s.execute(stmt)
        await self._s.commit()
        return int(result.rowcount or 0)

    async def save_structure(
        self,
        item_id: str,
        *,
        structured: dict,
        model: str,
        cost_usd: float,
        at: datetime,
    ) -> IntakeItem:
        from sqlalchemy import update

        stmt = (
            update(IntakeItem)
            .where(IntakeItem.id == item_id)
            .values(
                structured=structured,
                structure_model=model,
                structure_cost_usd=cost_usd,
                structured_at=at,
                status="structured",
            )
            .returning(IntakeItem)
        )
        result = await self._s.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("intake item 不存在")
        await self._s.commit()
        return row

    async def mark_promoted(
        self, item_id: str, *, goal_id: str, at: datetime
    ) -> None:
        from sqlalchemy import update

        stmt = (
            update(IntakeItem)
            .where(IntakeItem.id == item_id)
            .values(status="promoted", promoted_goal_id=goal_id)
        )
        await self._s.execute(stmt)
        await self._s.commit()

    async def commit(self) -> None:
        await self._s.commit()


def to_out(item: IntakeItem) -> IntakeItemOut:
    return IntakeItemOut(
        id=item.id,
        owner_subject=item.owner_subject,
        text=item.text,
        source=item.source,
        status=item.status,  # type: ignore[arg-type]
        created_at=item.created_at,
        structured_at=item.structured_at,
        structured=item.structured,
        structure_model=item.structure_model,
        structure_cost_usd=(
            float(item.structure_cost_usd) if item.structure_cost_usd is not None else None
        ),
        archived_at=item.archived_at,
        promoted_goal_id=item.promoted_goal_id,
    )


__all__ = [
    "IntakeRepository",
    "SqlIntakeRepository",
    "to_out",
    "hash_text",
    "new_id",
]
