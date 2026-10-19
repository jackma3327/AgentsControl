"""github_sync 数据访问层。

接口是 Protocol，测试用内存 fake；生产实现只用 SQLAlchemy，不掺业务判断。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError

from .models import GitHubEvent, GitHubIssue, GitHubPull


def new_id() -> str:
    return uuid.uuid4().hex


class GitHubRepository(Protocol):
    async def commit(self) -> None: ...

    # --- 原始事件 ---
    async def insert_event(self, event: GitHubEvent) -> tuple[GitHubEvent, bool]: ...
    async def get_event(self, event_id: str) -> GitHubEvent: ...
    async def list_events(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[GitHubEvent]: ...
    async def save_event(self, event: GitHubEvent, *, status: str, error: str | None) -> GitHubEvent: ...

    # --- PR ---
    async def get_pull(self, number: int) -> GitHubPull | None: ...
    async def save_pull(self, pull: GitHubPull) -> GitHubPull: ...
    async def list_pulls(
        self, *, state: str | None = None, verdict: str | None = None, limit: int = 50
    ) -> list[GitHubPull]: ...

    # --- Issue（只读镜像）---
    async def get_issue(self, number: int) -> GitHubIssue | None: ...
    async def save_issue(self, issue: GitHubIssue) -> GitHubIssue: ...
    async def list_issues(
        self, *, state: str | None = None, limit: int = 100
    ) -> list[GitHubIssue]: ...


class SqlGitHubRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def commit(self) -> None:
        await self._s.commit()

    # ------------------------------------------------------------ 原始事件
    async def insert_event(self, event: GitHubEvent) -> tuple[GitHubEvent, bool]:
        """按 delivery_id 幂等：重复投递返回既有行，created=False。"""
        stmt = (
            pg_insert(GitHubEvent)
            .values(
                id=event.id,
                delivery_id=event.delivery_id,
                event=event.event,
                action=event.action,
                payload=event.payload,
                status=event.status,
                attempts=event.attempts,
                received_at=event.received_at,
            )
            .on_conflict_do_nothing(index_elements=["delivery_id"])
            .returning(GitHubEvent)
        )
        created_row = (await self._s.execute(stmt)).scalar_one_or_none()
        if created_row is not None:
            return created_row, True
        existing = await self._s.execute(
            select(GitHubEvent).where(GitHubEvent.delivery_id == event.delivery_id)
        )
        row = existing.scalar_one()
        return row, False

    async def get_event(self, event_id: str) -> GitHubEvent:
        row = await self._s.get(GitHubEvent, event_id)
        if row is None:
            raise NotFoundError("事件不存在", details={"event_id": event_id})
        return row

    async def list_events(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[GitHubEvent]:
        stmt = select(GitHubEvent)
        if status:
            stmt = stmt.where(GitHubEvent.status == status)
        stmt = stmt.order_by(desc(GitHubEvent.received_at)).limit(limit)
        return list((await self._s.execute(stmt)).scalars())

    async def save_event(
        self, event: GitHubEvent, *, status: str, error: str | None
    ) -> GitHubEvent:
        event.status = status
        event.error = error
        event.attempts = int(event.attempts) + 1
        event.processed_at = datetime.now(UTC)
        await self._s.flush()
        return event

    # ------------------------------------------------------------------ PR
    async def get_pull(self, number: int) -> GitHubPull | None:
        stmt = select(GitHubPull).where(GitHubPull.number == number)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def save_pull(self, pull: GitHubPull) -> GitHubPull:
        self._s.add(pull)
        await self._s.flush()
        return pull

    async def list_pulls(
        self, *, state: str | None = None, verdict: str | None = None, limit: int = 50
    ) -> list[GitHubPull]:
        stmt = select(GitHubPull)
        if state:
            stmt = stmt.where(GitHubPull.state == state)
        if verdict:
            stmt = stmt.where(GitHubPull.verdict == verdict)
        stmt = stmt.order_by(desc(GitHubPull.updated_at)).limit(limit)
        return list((await self._s.execute(stmt)).scalars())

    # --------------------------------------------------------------- Issue
    async def get_issue(self, number: int) -> GitHubIssue | None:
        stmt = select(GitHubIssue).where(GitHubIssue.number == number)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def save_issue(self, issue: GitHubIssue) -> GitHubIssue:
        self._s.add(issue)
        await self._s.flush()
        return issue

    async def list_issues(
        self, *, state: str | None = None, limit: int = 100
    ) -> list[GitHubIssue]:
        stmt = select(GitHubIssue)
        if state:
            stmt = stmt.where(GitHubIssue.state == state)
        stmt = stmt.order_by(desc(GitHubIssue.updated_at)).limit(limit)
        return list((await self._s.execute(stmt)).scalars())


__all__ = ["GitHubRepository", "SqlGitHubRepository", "new_id"]
