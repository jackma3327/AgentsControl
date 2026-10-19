"""github_sync 模块 ORM 模型。

三张表，各自回答一个问题：
- `github_events`：收到的每个 webhook 是什么？处理到哪一步？（先落库再处理，失败可重放）
- `github_pulls`：这个 PR 的验收结论是什么？（ok / rework / escalated）
- `github_issues`：GitHub 上的 Issue 长什么样？（**只读镜像**，永不回写）

刻意不做"双向同步"：任务状态在 Postgres，验收信号在 GitHub，两边各管一件事。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class GitHubEvent(Base):
    """原始 webhook。`delivery_id` 唯一 → 重复投递天然幂等。"""

    __tablename__ = "github_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="received", index=True
    )  # received / processed / ignored / failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GitHubPull(Base):
    """PR 的验收结论。verdict 只由纯函数计算，不做人工覆盖。"""

    __tablename__ = "github_pulls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    head_ref: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    base_ref: Mapped[str] = mapped_column(String(64), nullable=False, default="main")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    merged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checks_green: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ci_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verdict: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )  # pending / ok / rework / escalated
    linked_issues: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GitHubIssue(Base):
    """Issue 只读镜像。**任何代码都不得把这里的字段推回 GitHub。**"""

    __tablename__ = "github_issues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    labels: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    author: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


__all__ = ["GitHubEvent", "GitHubIssue", "GitHubPull"]
