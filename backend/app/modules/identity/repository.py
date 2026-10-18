"""数据访问层：仓储协议 + SQLAlchemy 实现。

service 只依赖本文件的 `AuthRepository` 协议（结构化类型），
单测用内存实现替换（见 tests/test_identity.py），不依赖真实 PG。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuthEvent, RefreshToken, User


def hash_refresh_token(raw: str) -> str:
    """刷新令牌只存 SHA-256 哈希：库泄露拿不到可用凭据。"""
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class UserRecord:
    subject: str
    password_hash: str
    role: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class RefreshTokenRecord:
    subject: str
    session_id: str
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None = None
    replaced_by: str | None = None


class AuthRepository(Protocol):
    async def get_user(self, subject: str) -> UserRecord | None: ...

    async def add_refresh_token(self, record: RefreshTokenRecord) -> None: ...

    async def get_refresh_token(self, token_hash: str) -> RefreshTokenRecord | None: ...

    async def revoke_refresh_token(self, token_hash: str, *, replaced_by: str | None) -> bool: ...

    async def revoke_session(self, session_id: str, *, subject: str | None = None) -> int: ...

    async def revoke_all_sessions(self, subject: str) -> int: ...

    async def add_event(
        self,
        *,
        subject: str,
        session_id: str | None,
        event_type: str,
        detail: dict | None = None,
    ) -> None: ...

    async def commit(self) -> None: ...


class SqlAuthRepository:
    """基于 AsyncSession 的实现。多步写入由 service 显式 commit（见 core/db.py 约定）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user(self, subject: str) -> UserRecord | None:
        row = await self._session.get(User, subject)
        if row is None:
            return None
        return UserRecord(
            subject=row.subject,
            password_hash=row.password_hash,
            role=row.role,
            is_active=row.is_active,
        )

    async def add_refresh_token(self, record: RefreshTokenRecord) -> None:
        self._session.add(
            RefreshToken(
                subject=record.subject,
                session_id=record.session_id,
                token_hash=record.token_hash,
                expires_at=record.expires_at,
            )
        )

    async def get_refresh_token(self, token_hash: str) -> RefreshTokenRecord | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return RefreshTokenRecord(
            subject=row.subject,
            session_id=row.session_id,
            token_hash=row.token_hash,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            replaced_by=row.replaced_by,
        )

    async def revoke_refresh_token(self, token_hash: str, *, replaced_by: str | None) -> bool:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), replaced_by=replaced_by)
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount)

    async def revoke_session(self, session_id: str, *, subject: str | None = None) -> int:
        stmt = update(RefreshToken).where(
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
        )
        if subject is not None:
            stmt = stmt.where(RefreshToken.subject == subject)
        stmt = stmt.values(revoked_at=datetime.now(UTC))
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def revoke_all_sessions(self, subject: str) -> int:
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.subject == subject, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def add_event(
        self,
        *,
        subject: str,
        session_id: str | None,
        event_type: str,
        detail: dict | None = None,
    ) -> None:
        self._session.add(
            AuthEvent(
                subject=subject,
                session_id=session_id,
                event_type=event_type,
                detail=detail,
            )
        )

    async def commit(self) -> None:
        await self._session.commit()
