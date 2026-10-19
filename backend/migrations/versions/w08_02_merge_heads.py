"""merge heads: w03 链 × w08 链

Revision ID: w08_02_merge_heads
Revises: w03_03_merge_heads, w08_01_evals
Create Date: 2026-10-19

W03（任务队列）与 W08（资产面）并行合入，各自基于旧 main 建链，形成两个 head。
本 revision 收口回单头，无 schema 变更。
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "w08_02_merge_heads"
down_revision: str | None = ("w03_03_merge_heads", "w08_01_evals")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
