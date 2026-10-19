"""evals 模块 ORM 模型（资产面：模型雷达 / skill / harness / 认知卡）。

只描述数据形状；聚合与判定规则在 service.py，执行在 runner.py。

两条设计约束：
1. **版本不可变**：skill / harness 的新版本新增一行，不原地改（否则历史评测无法解释）。
2. **评测结果只追加**：EvalResult 不允许 UPDATE，重跑产生新 run（同 run 内唯一约束兜底）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# 8 个评测维度（顺序即雷达图的固定顺序，不要按字母重排）
DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("writing", "长文写作"),
    ("code", "代码"),
    ("extraction", "结构化抽取"),
    ("tool_use", "多轮工具调用"),
    ("vision", "截图与视觉理解"),
    ("long_context", "长上下文"),
    ("chinese", "中文语感"),
    ("instruction", "指令遵循"),
)
DIMENSION_LABELS: dict[str, str] = dict(DIMENSIONS)


class EvalTask(Base):
    """题库：20 道自有真实任务。题库本身是资产，改动需新版本而不是覆盖。"""

    __tablename__ = "eval_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # 评分锚点：什么算 100 分。没有锚点的评分不可复现。
    rubric: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


class EvalRun(Base):
    """一次跑批：N 题 × M 模型。成本上限是硬约束，超限中止。"""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    week: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # 2026-W42
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", index=True
    )  # queued/running/done/aborted/error
    models: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    cost_cap_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    cost_used_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalResult(Base):
    """单题单模型结果。score 为 0–100；缺样本不用 0 表示（由聚合层返回 insufficient）。"""

    __tablename__ = "eval_results"
    __table_args__ = (
        UniqueConstraint("run_id", "task_id", "model", name="uq_eval_result"),
        Index("ix_eval_results_week_model", "week", "model", "dimension"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    week: Mapped[str] = mapped_column(String(16), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)  # 0–100
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    raw_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


class Skill(Base):
    """skill 注册表。**没有 eval set 的 skill 不许上线**（状态只能 draft）。"""

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_skill_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", index=True
    )  # draft/active/paused/stale/retired
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    # 触发条件 / 判据 / 反例（skill 的三件套）
    trigger: Mapped[str] = mapped_column(Text, nullable=False, default="")
    criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    counterexample: Mapped[str] = mapped_column(Text, nullable=False, default="")
    eval_task_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    rollout_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rollout_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


class HarnessTemplate(Base):
    """harness 模板：skill + prompt + 工具配置 + eval 集，开箱能跑才算模板。"""

    __tablename__ = "harness_templates"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_harness_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    skill_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    eval_task_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


class CognitionCard(Base):
    """认知卡：进化轨目标的验收物。**被用过一次才算完成**（reuse_count > 0）。"""

    __tablename__ = "cognition_cards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)  # 触发条件
    rule: Mapped[str] = mapped_column(Text, nullable=False)  # 判断规则
    counterexample: Mapped[str] = mapped_column(Text, nullable=False)  # 反例
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=50)  # 0–100
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reuse_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", index=True
    )  # draft/active/stale
    last_reused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


class DriftAlert(Base):
    """漂移告警：同模型同维度周环比降 >3pct。唯一约束保证一周只报一次。"""

    __tablename__ = "drift_alerts"
    __table_args__ = (
        UniqueConstraint("model", "dimension", "week", name="uq_drift_week"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    week: Mapped[str] = mapped_column(String(16), nullable=False)
    prev_week: Mapped[str] = mapped_column(String(16), nullable=False)
    prev_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    curr_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    delta_pct: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)  # 负数=下降
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", index=True
    )  # open/ack/ignored
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa_text("now()"),
        default=lambda: datetime.now(UTC),
    )


__all__ = [
    "DIMENSIONS",
    "DIMENSION_LABELS",
    "CognitionCard",
    "DriftAlert",
    "EvalResult",
    "EvalRun",
    "EvalTask",
    "HarnessTemplate",
    "Skill",
]
