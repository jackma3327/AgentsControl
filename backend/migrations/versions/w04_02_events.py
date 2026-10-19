"""w04_02: 增量事件表

窗口: W04（issue W04-I04）

硬约束：
- PRIMARY KEY (node_id, seq) 是幂等的基础（不是应用层去重）；
- is_backfill 服务于"唤醒后先快照再增量"的容错：occurred_at 早于最近快照的事件
  打回填标，不参与当前状态推导。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "w04_02_events"
down_revision: str | None = "w04_01_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_events",
        sa.Column("node_id", sa.Text(),
                  sa.ForeignKey("devices.node_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("task", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.Text(), nullable=False, server_default=""),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("ms", sa.BigInteger(), nullable=True),
        sa.Column("extra", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.BigInteger(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("is_backfill", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_device_events_node_received", "device_events", ["node_id", "received_at"])


def downgrade() -> None:
    op.drop_index("ix_device_events_node_received", table_name="device_events")
    op.drop_table("device_events")
