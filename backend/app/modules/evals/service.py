"""evals 业务规则：雷达聚合、能力上限、漂移判定、资产状态机。

这个模块的所有判定都是**纯函数优先**：输入结果列表/雷达，输出判定。
理由：这些阈值决定"要不要立项"，是最需要穷举边界（69.9/70.1/79.9、-3.1/-2.9）的地方，
纯函数才能单测到位；查库与 HTTP 放在外层。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from statistics import median

from app.core.errors import ConflictError

from .models import (
    DIMENSION_LABELS,
    DIMENSIONS,
    CognitionCard,
    DriftAlert,
    EvalResult,
    EvalRun,
    Skill,
)
from .repository import EvalRepository, new_id
from .runner import DEFAULT_MODELS
from .schemas import (
    CardReuseOut,
    CeilingCheckOut,
    CeilingCheckRequest,
    CeilingDimension,
    CeilingIssue,
    CeilingOut,
    DimensionScore,
    DriftAlertOut,
    RadarOut,
    RunCreate,
    SkillCreate,
    SkillOut,
)

# 阈值集中在这里：改阈值必须同步改单测，避免"悄悄放宽标准"
CEILING_BLOCKED_BELOW = 70.0
CEILING_RISKY_BELOW = 80.0
DRIFT_ALERT_DROP = 3.0  # 下降严格大于 3pct 才告警

ADVICE_BLOCKED = "所有模型都做不到：拆小环节 / 换规则 / 整段砍掉，不要换模型硬上"
ADVICE_RISKY = "可做但需拆分步骤并加人工复核，不要一次跑完整链路"
ADVICE_OK = "能力足够，可直接立项"
ADVICE_INSUFFICIENT = "本周没有样本：先跑一轮评测再决策"


# --------------------------------------------------------------------- 周历
def current_week(now: datetime | None = None) -> str:
    d = (now or datetime.now(UTC)).date()
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def prev_week(week: str) -> str:
    year, w = week.split("-W")
    monday = date.fromisocalendar(int(year), int(w), 1)
    prev_monday = monday - timedelta(days=7)
    iso = prev_monday.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# ------------------------------------------------------------------ 雷达聚合
def aggregate_radar(
    results: list[EvalResult], *, week: str, models: list[str] | None = None
) -> RadarOut:
    """按维度聚合成雷达。

    每维取"表现最好的模型"作为该维得分：立项问题是"能不能做到"，
    不是"平均有多差"。但 by_model 完整保留，前端可以画多模型对比。
    """
    model_list = models or sorted({r.model for r in results})
    dims: list[DimensionScore] = []

    for code, label in DIMENSIONS:
        subset = [r for r in results if r.dimension == code and r.model in model_list]
        if not subset:
            dims.append(
                DimensionScore(
                    dimension=code, label=label, status="insufficient", samples=0
                )
            )
            continue

        by_model: dict[str, float] = {}
        best_model: str | None = None
        best_score = -1.0
        for model in model_list:
            rows = [r for r in subset if r.model == model]
            if not rows:
                continue
            avg = sum(float(r.score) for r in rows) / len(rows)
            by_model[model] = round(avg, 2)
            if avg > best_score:
                best_score, best_model = avg, model

        if best_model is None:
            dims.append(
                DimensionScore(dimension=code, label=label, status="insufficient", samples=0)
            )
            continue

        best_rows = [r for r in subset if r.model == best_model]
        latencies = [r.latency_ms for r in best_rows if r.latency_ms > 0]
        dims.append(
            DimensionScore(
                dimension=code,
                label=label,
                status="ok",
                score=round(best_score, 2),
                best_model=best_model,
                by_model=by_model,
                cost_usd=round(sum(float(r.cost_usd) for r in best_rows), 6),
                p50_latency_ms=int(median(latencies)) if latencies else None,
                samples=len(best_rows),
            )
        )

    return RadarOut(
        week=week, models=model_list, dimensions=dims, generated_at=datetime.now(UTC)
    )


# ---------------------------------------------------------------- 能力上限
def ceiling_from_radar(radar: RadarOut) -> CeilingOut:
    dims: list[CeilingDimension] = []
    blocked: list[str] = []
    risky: list[str] = []

    for d in radar.dimensions:
        if d.status == "insufficient" or d.score is None:
            dims.append(
                CeilingDimension(
                    dimension=d.dimension,
                    label=d.label,
                    best_score=None,
                    status="insufficient",
                    advice=ADVICE_INSUFFICIENT,
                )
            )
            continue
        if d.score < CEILING_BLOCKED_BELOW:
            status = "blocked"
            blocked.append(d.dimension)
            advice = f"{d.best_model} 最高 {d.score:.1f} 分，{ADVICE_BLOCKED}"
        elif d.score < CEILING_RISKY_BELOW:
            status = "risky"
            risky.append(d.dimension)
            advice = f"{d.best_model} 最高 {d.score:.1f} 分，{ADVICE_RISKY}"
        else:
            status = "ok"
            advice = f"{d.best_model} 最高 {d.score:.1f} 分，{ADVICE_OK}"
        dims.append(
            CeilingDimension(
                dimension=d.dimension,
                label=d.label,
                best_score=d.score,
                status=status,
                advice=advice,
            )
        )

    return CeilingOut(
        week=radar.week, dimensions=dims, blocked=blocked, risky=risky
    )


def check_capability(
    radar: RadarOut, payload: CeilingCheckRequest
) -> CeilingCheckOut:
    """立项前校验：只回答被问到的维度，且给出具体分数，不说"可能不行"。"""
    mapping = {d.dimension: d for d in radar.dimensions}
    issues: list[CeilingIssue] = []
    seen: list[str] = []

    for code in payload.dimensions:
        d = mapping.get(code)
        label = DIMENSION_LABELS.get(code, code)
        if d is None or d.status == "insufficient" or d.score is None:
            issues.append(
                CeilingIssue(
                    dimension=code,
                    label=label,
                    score=None,
                    status="insufficient",
                    advice=ADVICE_INSUFFICIENT,
                )
            )
            seen.append("insufficient")
            continue
        if d.score < CEILING_BLOCKED_BELOW:
            status, advice = "blocked", f"最高 {d.score:.1f} 分（{d.best_model}），{ADVICE_BLOCKED}"
        elif d.score < CEILING_RISKY_BELOW:
            status, advice = "risky", f"最高 {d.score:.1f} 分（{d.best_model}），{ADVICE_RISKY}"
        else:
            status, advice = "ok", f"最高 {d.score:.1f} 分（{d.best_model}），{ADVICE_OK}"
        seen.append(status)
        issues.append(
            CeilingIssue(
                dimension=code, label=label, score=d.score, status=status, advice=advice
            )
        )

    if "blocked" in seen:
        verdict = "blocked"
    elif "risky" in seen:
        verdict = "risky"
    elif "insufficient" in seen:
        verdict = "insufficient"
    else:
        verdict = "ok"
    return CeilingCheckOut(verdict=verdict, issues=issues)  # type: ignore[arg-type]


# ------------------------------------------------------------------ 漂移判定
def build_drift_alerts(
    curr: list[EvalResult], prev: list[EvalResult], *, week: str, prev_week_str: str
) -> list[DriftAlert]:
    """同模型同维度周环比：下降严格大于 3pct 才告警（3.0 不报、3.1 报）。"""
    alerts: list[DriftAlert] = []
    for model in sorted({r.model for r in curr} & {r.model for r in prev}):
        for code, _ in DIMENSIONS:
            cur_rows = [r for r in curr if r.model == model and r.dimension == code]
            prev_rows = [r for r in prev if r.model == model and r.dimension == code]
            if not cur_rows or not prev_rows:
                continue
            cur_score = sum(float(r.score) for r in cur_rows) / len(cur_rows)
            prev_score = sum(float(r.score) for r in prev_rows) / len(prev_rows)
            delta = cur_score - prev_score
            if delta >= -DRIFT_ALERT_DROP:
                continue
            alerts.append(
                DriftAlert(
                    id=new_id(),
                    model=model,
                    dimension=code,
                    week=week,
                    prev_week=prev_week_str,
                    prev_score=round(prev_score, 2),
                    curr_score=round(cur_score, 2),
                    delta_pct=round(delta, 2),
                    status="open",
                    created_at=datetime.now(UTC),
                )
            )
    return alerts


# ---------------------------------------------------------------- 跑批编排
async def create_run(repo: EvalRepository, payload: RunCreate) -> EvalRun:
    week = payload.week or current_week()
    models = payload.models or list(DEFAULT_MODELS)
    run = EvalRun(
        id=new_id(),
        week=week,
        status="queued",
        models=models,
        cost_cap_usd=payload.cost_cap_usd,
        cost_used_usd=0,
        task_count=0,
        created_at=datetime.now(UTC),
    )
    return await repo.create_run(run)


async def get_radar(
    repo: EvalRepository, *, week: str | None = None, models: list[str] | None = None
) -> RadarOut:
    week = week or current_week()
    results = await repo.list_results(week=week)
    if models:
        results = [r for r in results if r.model in models]
    return aggregate_radar(results, week=week, models=models)


async def get_ceiling(repo: EvalRepository, *, week: str | None = None) -> CeilingOut:
    return ceiling_from_radar(await get_radar(repo, week=week))


async def scan_drift(repo: EvalRepository, *, week: str | None = None) -> tuple[list[DriftAlert], int]:
    week = week or current_week()
    previous = prev_week(week)
    curr = await repo.list_results(week=week)
    prev = await repo.list_results(week=previous)
    candidates = build_drift_alerts(curr, prev, week=week, prev_week_str=previous)
    inserted = await repo.insert_alerts(candidates)
    return inserted, len(candidates) - len(inserted)


# -------------------------------------------------------------- skill 状态机
def ensure_activatable(skill: Skill) -> None:
    """没有 eval set 的 skill 不许上线（产品红线：没评测就等于没验证）。"""
    if not skill.eval_task_ids:
        raise ConflictError(
            "该 skill 没有 eval set，不能上线（只能保持 draft）",
            details={"skill": skill.name, "version": skill.version},
        )


async def create_skill(repo: EvalRepository, payload: SkillCreate) -> Skill:
    existing = await repo.get_skill_by_version(payload.name, payload.version)
    if existing is not None:
        raise ConflictError(
            "同名同版本已存在（版本不可覆盖，请升版本号）",
            details={"name": payload.name, "version": payload.version},
        )
    skill = Skill(
        id=new_id(),
        name=payload.name,
        version=payload.version,
        status=payload.status,
        description=payload.description,
        trigger=payload.trigger,
        criteria=payload.criteria,
        counterexample=payload.counterexample,
        eval_task_ids=payload.eval_task_ids,
        rollout_pct=payload.rollout_pct,
        rollout_until=payload.rollout_until,
        review_due_at=payload.review_due_at,
        created_at=datetime.now(UTC),
    )
    if skill.status == "active":
        ensure_activatable(skill)
    return await repo.create_skill(skill)


async def set_skill_status(repo: EvalRepository, skill_id: str, status: str) -> Skill:
    skill = await repo.get_skill(skill_id)
    if status == "active":
        ensure_activatable(skill)
    return await repo.update_skill_status(skill_id, status=status)


async def list_skills(repo: EvalRepository, *, status: str | None = None) -> list[Skill]:
    """季度复核过期自动降级为 stale（过期资产继续生效是隐性风险）。"""
    skills = await repo.list_skills(status=status)
    now = datetime.now(UTC)
    for s in skills:
        if s.status == "active" and s.review_due_at is not None and s.review_due_at < now:
            s = await repo.update_skill_status(s.id, status="stale")
    return await repo.list_skills(status=status)


# ---------------------------------------------------------------- 认知卡
async def reuse_card(repo: EvalRepository, card_id: str) -> CardReuseOut:
    card = await repo.increment_card_reuse(card_id, at=datetime.now(UTC))
    return CardReuseOut(card=card, completed=int(card.reuse_count) > 0)


def card_status(card: CognitionCard) -> str:
    """被用过一次才算完成：draft 状态下 reuse_count>0 也视为 active。"""
    if card.status == "draft" and card.reuse_count > 0:
        return "active"
    return card.status


# ------------------------------------------------------------------ 序列化
def alert_out(alert: DriftAlert) -> DriftAlertOut:
    return DriftAlertOut.model_validate(alert)


def skill_out(skill: Skill) -> SkillOut:
    return SkillOut.model_validate(skill)


__all__ = [
    "ADVICE_BLOCKED",
    "ADVICE_INSUFFICIENT",
    "ADVICE_OK",
    "ADVICE_RISKY",
    "CEILING_BLOCKED_BELOW",
    "CEILING_RISKY_BELOW",
    "DRIFT_ALERT_DROP",
    "aggregate_radar",
    "alert_out",
    "build_drift_alerts",
    "card_status",
    "ceiling_from_radar",
    "check_capability",
    "create_run",
    "create_skill",
    "current_week",
    "ensure_activatable",
    "get_ceiling",
    "get_radar",
    "list_skills",
    "prev_week",
    "reuse_card",
    "scan_drift",
    "set_skill_status",
    "skill_out",
]
