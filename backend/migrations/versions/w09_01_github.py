"""w09_01_github: GitHub 验收总线（事件 / PR 判定 / Issue 镜像）

Revision ID: w09_01_github
Revises: w08_02_merge_heads
Create Date: 2026-10-19

窗口: W09（issue W09-I01 / I02 / I03）

设计取舍：
- `github_events.delivery_id` 唯一：重复投递天然幂等，不需要额外去重表；
- `github_pulls.number` 唯一：一个 PR 只有一条判定记录，更新而不是追加；
- `github_issues` 是只读镜像：库结构上没有任何"待推送"字段，避免有人顺手做双向同步。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w09_01_github"
down_revision: str | None = "w08_02_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("delivery_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="received"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("delivery_id", name="uq_github_delivery"),
    )
    op.create_index("ix_github_events_event", "github_events", ["event"])
    op.create_index("ix_github_events_status", "github_events", ["status"])

    op.create_table(
        "github_pulls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("author", sa.String(120), nullable=True),
        sa.Column("head_ref", sa.String(200), nullable=False, server_default=""),
        sa.Column("base_ref", sa.String(64), nullable=False, server_default="main"),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("merged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("checks_green", sa.Boolean(), nullable=True),
        sa.Column("ci_failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verdict", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("linked_issues", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("number", name="uq_github_pull_number"),
    )
    op.create_index("ix_github_pulls_verdict", "github_pulls", ["verdict"])

    op.create_table(
        "github_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False, server_default=""),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("author", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("number", name="uq_github_issue_number"),
    )
    op.create_index("ix_github_issues_state", "github_issues", ["state"])


def downgrade() -> None:
    op.drop_index("ix_github_issues_state", table_name="github_issues")
    op.drop_table("github_issues")
    op.drop_index("ix_github_pulls_verdict", table_name="github_pulls")
    op.drop_table("github_pulls")
    op.drop_index("ix_github_events_status", table_name="github_events")
    op.drop_index("ix_github_events_event", table_name="github_events")
    op.drop_table("github_events")
