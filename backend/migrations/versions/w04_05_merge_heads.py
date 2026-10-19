"""merge heads: w04 链 × w06 链

Revision ID: w04_05_merge_heads
Revises: w04_04_commands, w06_03_merge_heads
Create Date: 2026-10-19

W04 的迁移从 0001_baseline 起链，与 W06 合入后的主链（w06_03_merge_heads）分叉。
本 revision 合回单头，无 schema 变更。
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "w04_05_merge_heads"
down_revision: str | None = ("w04_04_commands", "w06_03_merge_heads")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
