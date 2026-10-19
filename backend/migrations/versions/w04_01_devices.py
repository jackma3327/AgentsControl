"""w04_01: 设备注册表、引导码表、心跳表、告警表

窗口: W04（issue W04-I01 / W04-I03；device_heartbeats 与 device_alerts 分别供 I03 使用，
随本迁移预建以保持迁移链单头）

硬约束：
- devices.fingerprint 唯一 —— 注册幂等与指纹冲突判定都靠它；
- devices.token_hash 只存 sha256，吊销时置 NULL；
- device_heartbeats 以 node_id 为主键 —— 心跳是 UPSERT 单行，不是追加日志；
- device_alerts 只追加不更新（buffer_dropped / clock_skew / lease_reclaim）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "w04_01_devices"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", sa.Text(), nullable=False, unique=True),
        sa.Column("fingerprint", sa.Text(), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=True),
        sa.Column("trust", sa.String(16), nullable=False, server_default="managed"),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("squad", sa.Text(), nullable=True),
        sa.Column("tolerance", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("caps", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="offline"),
        sa.Column("agent_version", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missed_heartbeats", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_event_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("woke_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_devices_status", "devices", ["status"])

    op.create_table(
        "device_bootstrap_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by_node", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "device_heartbeats",
        sa.Column("node_id", sa.Text(),
                  sa.ForeignKey("devices.node_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("metrics", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("clock_skew_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        "device_alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_device_alerts_node_created", "device_alerts", ["node_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_alerts_node_created", table_name="device_alerts")
    op.drop_table("device_alerts")
    op.drop_table("device_heartbeats")
    op.drop_table("device_bootstrap_codes")
    op.drop_index("ix_devices_status", table_name="devices")
    op.drop_table("devices")
