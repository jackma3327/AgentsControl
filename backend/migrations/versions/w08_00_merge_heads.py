"""merge heads: w01 链 × w07 链

Revision ID: w08_00_merge_heads
Revises: w01_03_merge_heads, w07_02_approvals
Create Date: 2026-10-19

窗口: W08（合并头是 W08 加迁移前的前置动作）

main 在 W07 合入后出现两个 head：w01_03_merge_heads 与 w07_02_approvals。
本 revision 把两条链合回单头，之后的 w08_01 基于它继续。
无 schema 变更（alembic merge 节点）。
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "w08_00_merge_heads"
down_revision: str | None = ("w01_03_merge_heads", "w07_02_approvals")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
