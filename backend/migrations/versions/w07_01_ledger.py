"""w07_01_ledger: 账本三表（钱/注意力/订阅）

Revision ID: w07_01_ledger
Revises: w02_01_intake
Create Date: 2026-10-18

窗口: W07（issue W07-I01 / I02 / I05）

注：down_revision 串接在已合入 main 的 w02_01_intake 之后，
维持 alembic 单头（多窗口迁移由 marshal 排序合并）。

- ledger_entries    钱账本，六分项，UNIQUE(source, source_id) 保证归集幂等，
                    CHECK(task_id/goal_id 至少其一) 拒绝无主成本；
- attention_entries 注意力账本（分钟）；
- subscriptions     订阅登记（摊销 + 配额消耗率的输入）。

金额一律 NUMERIC(14,4)，不用 float。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "w07_01_ledger"
down_revision: str | None = "w02_01_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 4), nullable=False),
        sa.Column("currency", sa.String(8), server_default="CNY", nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("goal_id", sa.Uuid(), nullable=True),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("source", "source_id", name="uq_ledger_source"),
        sa.CheckConstraint(
            "task_id IS NOT NULL OR goal_id IS NOT NULL", name="ck_ledger_traceable"
        ),
        sa.CheckConstraint(
            "category IN ('subscription','tools','device','rework','qc','labor')",
            name="ck_ledger_category",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_ledger_amount_nonzero"),
    )
    op.create_index("ix_ledger_occurred_at", "ledger_entries", ["occurred_at"])
    op.create_index("ix_ledger_goal_id", "ledger_entries", ["goal_id"])
    op.create_index("ix_ledger_task_id", "ledger_entries", ["task_id"])

    op.create_table(
        "attention_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("minutes", sa.Numeric(10, 2), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attention_entries")),
        sa.CheckConstraint("minutes > 0", name="ck_attention_minutes_positive"),
    )
    op.create_index("ix_attention_occurred_at", "attention_entries", ["occurred_at"])
    op.create_index("ix_attention_source", "attention_entries", ["source"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("monthly_fee", sa.Numeric(14, 4), nullable=False),
        sa.Column("quota_limit", sa.Integer(), nullable=True),
        sa.Column("quota_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("amortize_mode", sa.String(16), server_default="calendar", nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.CheckConstraint("amortize_mode IN ('calendar','usage')", name="ck_subscription_mode"),
        sa.CheckConstraint("monthly_fee > 0", name="ck_subscription_fee_positive"),
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_index("ix_attention_source", table_name="attention_entries")
    op.drop_index("ix_attention_occurred_at", table_name="attention_entries")
    op.drop_table("attention_entries")
    op.drop_index("ix_ledger_task_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_goal_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_occurred_at", table_name="ledger_entries")
    op.drop_table("ledger_entries")
