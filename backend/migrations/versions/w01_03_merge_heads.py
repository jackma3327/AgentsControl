"""w01_03: merge heads（w01 链 × w02 链）

Revision ID: w01_03_merge_heads
Revises: w01_02_auth_events, w02_02_goals
Create Date: 2026-10-18

窗口: W01

W01 与 W02 的迁移从 0001_baseline 并行分叉，本 revision 把两条链合并回单一 head。
无 schema 变更（alembic merge 节点）。
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "w01_03_merge_heads"
# 注解保持 str | None 与项目静态检查约定一致；值可以是 tuple（alembic 原生支持）
down_revision: str | None = ("w01_02_auth_events", "w02_02_goals")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
