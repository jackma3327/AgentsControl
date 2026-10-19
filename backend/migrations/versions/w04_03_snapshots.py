"""w04_03: 快照表与能力变更事件表

窗口: W04（issue W04-I05）

硬约束：
- 每节点只保留最近 30 份快照（超限删最旧，prune 用单条 SQL）；
- 能力标签变化写 device_cap_events（W10 的"偏离"视图数据源）；
- 收到快照即标记 devices.woke_at，早于快照的增量靠 device_events.is_backfill 容错。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "w04_03_snapshots"
down_revision: str | None = "w04_02_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.Text(),
                  sa.ForeignKey("devices.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("versions", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("sessions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("caps", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("disk_used_gb", sa.Float(), nullable=False, server_default="-1"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_device_snapshots_node_id_desc", "device_snapshots", ["node_id", "id"])

    op.create_table(
        "device_cap_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("added", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("removed", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_device_cap_events_node_created", "device_cap_events",
                    ["node_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_cap_events_node_created", table_name="device_cap_events")
    op.drop_table("device_cap_events")
    op.drop_index("ix_device_snapshots_node_id_desc", table_name="device_snapshots")
    op.drop_table("device_snapshots")
