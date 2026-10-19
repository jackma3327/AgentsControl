"""w04_04: 设备指令表

窗口: W04（issue W04-I06）

硬约束：
- kind 只允许白名单五种（应用层 commands.py 校验，库里不设枚举约束以便扩容走评审）；
- 中风险指令（restart_agent / run_task）必须有 approved_by；
- third_party 设备的指令在应用层一律 403，不会产生记录。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "w04_04_commands"
down_revision: str | None = "w04_03_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_commands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("node_id", sa.Text(),
                  sa.ForeignKey("devices.node_id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_commands_node_status", "device_commands",
                    ["node_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_commands_node_status", table_name="device_commands")
    op.drop_table("device_commands")
