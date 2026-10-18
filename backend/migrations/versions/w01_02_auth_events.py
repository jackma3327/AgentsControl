"""w01_02: auth_events（认证事件流水）

Revision ID: w01_02_auth_events
Revises: w01_01_refresh_token
Create Date: 2026-10-18

窗口: W01（issue W01-I03）

- auth_events：登录/刷新/重放/登出/吊销的审计流水，只追加不修改；
- 设备 token 吊销由 W04 实现，但事件统一写本表（event_type 约定见 ADR 0002）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "w01_02_auth_events"
down_revision: str | None = "w01_01_refresh_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_auth_events_subject", "auth_events", ["subject"])
    op.create_index("ix_auth_events_session_id", "auth_events", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_events_session_id", table_name="auth_events")
    op.drop_index("ix_auth_events_subject", table_name="auth_events")
    op.drop_table("auth_events")
