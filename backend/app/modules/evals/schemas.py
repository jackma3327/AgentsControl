"""evals 模块 Pydantic 契约。

命名约定：`*In` 为入参，`*Out` 为出参；出参一律 `from_attributes`，便于直接从 ORM 映射。
分数一律 0–100，缺样本用 `status="insufficient"` + `score=null`，**绝不返回 0**
（0 会被读成"很差"，而"没测"和"很差"是完全不同的决策依据）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.evals.models import DIMENSIONS

DimensionCode = Literal[
    "writing",
    "code",
    "extraction",
    "tool_use",
    "vision",
    "long_context",
    "chinese",
    "instruction",
]

DIMENSION_CODES = tuple(code for code, _ in DIMENSIONS)


class EvalTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dimension: str
    title: str
    prompt: str
    rubric: str
    weight: float
    active: bool


class EvalTaskCreate(BaseModel):
    dimension: DimensionCode
    title: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1)
    rubric: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0, le=10)


class RunCreate(BaseModel):
    models: list[str] = Field(default_factory=list, max_length=10)
    week: str | None = Field(default=None, pattern=r"^\d{4}-W\d{2}$")
    cost_cap_usd: float = Field(default=20.0, gt=0, le=1000)
    task_ids: list[str] = Field(default_factory=list)

    @field_validator("models")
    @classmethod
    def _strip_models(cls, v: list[str]) -> list[str]:
        cleaned = [m.strip() for m in v if m.strip()]
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("models 不允许重复")
        return cleaned


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    week: str
    status: str
    models: list[str]
    cost_cap_usd: float
    cost_used_usd: float
    task_count: int
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    task_id: str
    dimension: str
    model: str
    score: float
    cost_usd: float
    latency_ms: int
    raw_ref: str | None = None


class DimensionScore(BaseModel):
    dimension: str
    label: str
    status: Literal["ok", "insufficient"]
    # score = 该维度上表现最好的模型的均分（立项时关心"能不能做到"，不是"平均有多差"）
    score: float | None = None
    best_model: str | None = None
    by_model: dict[str, float] = Field(default_factory=dict)
    cost_usd: float = 0
    p50_latency_ms: int | None = None
    samples: int = 0


class RadarOut(BaseModel):
    week: str
    models: list[str]
    dimensions: list[DimensionScore]
    generated_at: datetime


class CeilingDimension(BaseModel):
    dimension: str
    label: str
    best_score: float | None
    status: Literal["ok", "risky", "blocked", "insufficient"]
    advice: str


class CeilingOut(BaseModel):
    week: str
    dimensions: list[CeilingDimension]
    blocked: list[str]
    risky: list[str]


class CeilingCheckRequest(BaseModel):
    dimensions: list[DimensionCode] = Field(min_length=1, max_length=8)

    @field_validator("dimensions")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("dimensions 不允许重复")
        return v


class CeilingIssue(BaseModel):
    dimension: str
    label: str
    score: float | None
    status: str
    advice: str


class CeilingCheckOut(BaseModel):
    verdict: Literal["ok", "risky", "blocked", "insufficient"]
    issues: list[CeilingIssue]


class DriftAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    model: str
    dimension: str
    week: str
    prev_week: str
    prev_score: float
    curr_score: float
    delta_pct: float
    status: str
    note: str | None = None
    created_at: datetime


class DriftScanOut(BaseModel):
    week: str
    prev_week: str
    created: int
    skipped: int
    alerts: list[DriftAlertOut]


class DriftAckRequest(BaseModel):
    status: Literal["ack", "ignored"]
    note: str | None = Field(default=None, max_length=500)


class SkillCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(default="", max_length=500)
    trigger: str = ""
    criteria: str = ""
    counterexample: str = ""
    eval_task_ids: list[str] = Field(default_factory=list)
    rollout_pct: int = Field(default=0, ge=0, le=100)
    rollout_until: datetime | None = None
    review_due_at: datetime | None = None
    status: Literal["draft", "active", "paused"] = "draft"


class SkillStatusUpdate(BaseModel):
    status: Literal["draft", "active", "paused", "stale", "retired"]


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    status: str
    description: str
    trigger: str
    criteria: str
    counterexample: str
    eval_task_ids: list[str]
    rollout_pct: int
    rollout_until: datetime | None
    review_due_at: datetime | None
    created_at: datetime


class HarnessCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    skill_ids: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    eval_task_ids: list[str] = Field(default_factory=list)


class HarnessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    skill_ids: list[str]
    config: dict
    eval_task_ids: list[str]
    created_at: datetime


class CardCreate(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    trigger: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    counterexample: str = Field(min_length=1)
    confidence: int = Field(default=50, ge=0, le=100)
    expires_at: datetime | None = None


class CardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    trigger: str
    rule: str
    counterexample: str
    confidence: int
    expires_at: datetime | None
    reuse_count: int
    status: str
    last_reused_at: datetime | None
    created_at: datetime


class CardReuseOut(BaseModel):
    card: CardOut
    completed: bool  # reuse_count > 0 才算完成（进化轨验收）


class RadarQuery(BaseModel):
    week: str | None = None
    models: list[str] = Field(default_factory=list)
