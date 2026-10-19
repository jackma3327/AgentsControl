"""evals HTTP 路由（资产面）。

只做 HTTP：解析入参、调 service、格式化响应。判定规则在 service.py，执行在 runner.py。

跑批不在请求里等模型：
- 默认 `eager=false`：建 run 后交给后台任务，立即返回 run_id；
- `eager=true` 仅用于测试/本地调试（生产禁用），同步执行并返回最终状态。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.core.config import settings
from app.core.db import get_db, get_sessionmaker
from app.core.deps import OwnerDep
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.evals import service as svc
from app.modules.evals.models import CognitionCard, HarnessTemplate
from app.modules.evals.repository import EvalRepository, SqlEvalRepository, new_id
from app.modules.evals.runner import EvalProvider, FakeProvider, execute_run
from app.modules.evals.schemas import (
    CardCreate,
    CardOut,
    CardReuseOut,
    CeilingCheckOut,
    CeilingCheckRequest,
    CeilingOut,
    DriftAckRequest,
    DriftAlertOut,
    DriftScanOut,
    EvalTaskCreate,
    EvalTaskOut,
    HarnessCreate,
    HarnessOut,
    RadarOut,
    ResultOut,
    RunCreate,
    RunOut,
    SkillCreate,
    SkillOut,
    SkillStatusUpdate,
)

logger = logging.getLogger("afc.evals")

MODULE = "evals"
OWNER_WINDOW = "W08"

router = APIRouter(prefix="/evals", tags=["evals"])


@router.get("/_status", summary="模块状态（骨架冒烟测试依赖）")
async def module_status() -> dict[str, str]:
    return {"module": MODULE, "owner_window": OWNER_WINDOW, "state": "implemented"}


async def get_eval_repository(session=Depends(get_db)) -> EvalRepository:
    return SqlEvalRepository(session)


EvalRepoDep = Annotated[EvalRepository, Depends(get_eval_repository)]


def get_eval_provider() -> EvalProvider:
    """模型调用工厂。当前返回确定性 fake；接 LiteLLM 时只换这里。"""
    return FakeProvider()


ProviderDep = Annotated[EvalProvider, Depends(get_eval_provider)]


# ------------------------------------------------------------------ 题库
@router.get("/tasks", response_model=list[EvalTaskOut], summary="题库（默认只返回启用题）")
async def list_tasks(
    _: OwnerDep,
    repo: EvalRepoDep,
    active_only: bool = Query(default=True),
) -> list[EvalTaskOut]:
    tasks = await repo.list_tasks(active_only=active_only)
    return [EvalTaskOut.model_validate(t) for t in tasks]


@router.post(
    "/tasks",
    response_model=EvalTaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="新增题目（题库是资产，改动按新增处理）",
)
async def create_task(
    payload: EvalTaskCreate, _: OwnerDep, repo: EvalRepoDep
) -> EvalTaskOut:
    from app.modules.evals.models import EvalTask  # 局部导入避免循环

    task = EvalTask(
        id=new_id(),
        dimension=payload.dimension,
        title=payload.title,
        prompt=payload.prompt,
        rubric=payload.rubric,
        weight=payload.weight,
        created_at=datetime.now(UTC),
    )
    task = await repo.create_task(task)
    await repo.commit()
    return EvalTaskOut.model_validate(task)


# ------------------------------------------------------------------ 跑批
@router.post(
    "/runs",
    response_model=RunOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="发起评测（异步；eager=true 仅测试用）",
)
async def create_run(
    payload: RunCreate,
    owner: OwnerDep,
    repo: EvalRepoDep,
    provider: ProviderDep,
    background: BackgroundTasks,
    eager: bool = Query(default=False),
    task_ids: list[str] = Query(default_factory=list),
) -> RunOut:
    if eager and settings.app_env == "prod":
        raise ForbiddenError("生产环境不允许同步执行评测（会阻塞请求数分钟）")

    run = await svc.create_run(repo, payload)
    chosen_ids = payload.task_ids or task_ids
    tasks = (
        await repo.list_tasks_by_ids(chosen_ids)
        if chosen_ids
        else await repo.list_tasks(active_only=True)
    )
    run.task_count = len(tasks)
    await repo.commit()

    if eager:
        await execute_run(repo, run, tasks, provider)
        await repo.commit()
        return RunOut.model_validate(run)

    background.add_task(_run_in_background, run.id, [t.id for t in tasks])
    return RunOut.model_validate(run)


async def _run_in_background(run_id: str, task_ids: list[str]) -> None:
    """后台执行：独立 session（请求的 session 在响应后就关了）。"""
    try:
        async with get_sessionmaker()() as session:
            repo = SqlEvalRepository(session)
            run = await repo.get_run(run_id)
            tasks = await repo.list_tasks_by_ids(task_ids)
            await execute_run(repo, run, tasks, FakeProvider())
            await session.commit()
    except Exception:  # noqa: BLE001 - 后台任务必须自己兜底，否则静默失联
        logger.exception("eval run failed in background", extra={"run_id": run_id})
        try:
            async with get_sessionmaker()() as session:
                repo = SqlEvalRepository(session)
                await repo.update_run(run_id, status="error", error="background_failure")
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark run as error", extra={"run_id": run_id})


@router.get("/runs", response_model=list[RunOut], summary="最近跑批")
async def list_runs(_: OwnerDep, repo: EvalRepoDep, limit: int = Query(default=20, ge=1, le=100)) -> list[RunOut]:
    runs = await repo.list_runs(limit=limit)
    return [RunOut.model_validate(r) for r in runs]


@router.get("/runs/{run_id}", response_model=RunOut, summary="跑批状态")
async def get_run(_: OwnerDep, repo: EvalRepoDep, run_id: str) -> RunOut:
    return RunOut.model_validate(await repo.get_run(run_id))


@router.get(
    "/runs/{run_id}/results",
    response_model=list[ResultOut],
    summary="跑批明细（每题每模型）",
)
async def list_results(
    _: OwnerDep, repo: EvalRepoDep, run_id: str
) -> list[ResultOut]:
    run = await repo.get_run(run_id)
    rows = [r for r in await repo.list_results(week=run.week) if r.run_id == run_id]
    if not rows and run.status == "queued":
        raise NotFoundError("该批次尚未产生结果", details={"run_id": run_id})
    return [ResultOut.model_validate(r) for r in rows]


# ------------------------------------------------------------------ 雷达
@router.get("/radar", response_model=RadarOut, summary="8 维雷达（按周）")
async def get_radar(
    _: OwnerDep,
    repo: EvalRepoDep,
    week: str | None = Query(default=None, pattern=r"^\d{4}-W\d{2}$"),
    models: list[str] = Query(default_factory=list),
) -> RadarOut:
    return await svc.get_radar(repo, week=week, models=models or None)


@router.get("/ceiling", response_model=CeilingOut, summary="能力上限标记（立项前必看）")
async def get_ceiling(
    _: OwnerDep,
    repo: EvalRepoDep,
    week: str | None = Query(default=None, pattern=r"^\d{4}-W\d{2}$"),
) -> CeilingOut:
    return await svc.get_ceiling(repo, week=week)


@router.post(
    "/ceiling/check",
    response_model=CeilingCheckOut,
    summary="立项校验：这些维度现在能不能做",
)
async def check_ceiling(
    payload: CeilingCheckRequest,
    _: OwnerDep,
    repo: EvalRepoDep,
    week: str | None = Query(default=None, pattern=r"^\d{4}-W\d{2}$"),
) -> CeilingCheckOut:
    radar = await svc.get_radar(repo, week=week)
    return svc.check_capability(radar, payload)


# ------------------------------------------------------------------ 漂移
@router.get("/drift", response_model=list[DriftAlertOut], summary="漂移告警列表")
async def list_drift(
    _: OwnerDep,
    repo: EvalRepoDep,
    week: str | None = Query(default=None, pattern=r"^\d{4}-W\d{2}$"),
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[DriftAlertOut]:
    alerts = await repo.list_alerts(week=week, status=status_filter)
    return [svc.alert_out(a) for a in alerts]


@router.post("/drift/scan", response_model=DriftScanOut, summary="计算周环比（幂等）")
async def scan_drift(
    _: OwnerDep,
    repo: EvalRepoDep,
    week: str | None = Query(default=None, pattern=r"^\d{4}-W\d{2}$"),
) -> DriftScanOut:
    week = week or svc.current_week()
    inserted, skipped = await svc.scan_drift(repo, week=week)
    await repo.commit()
    return DriftScanOut(
        week=week,
        prev_week=svc.prev_week(week),
        created=len(inserted),
        skipped=skipped,
        alerts=[svc.alert_out(a) for a in inserted],
    )


@router.patch("/drift/{alert_id}", response_model=DriftAlertOut, summary="标记已知/忽略")
async def ack_drift(
    alert_id: str, payload: DriftAckRequest, _: OwnerDep, repo: EvalRepoDep
) -> DriftAlertOut:
    alert = await repo.update_alert(alert_id, status=payload.status, note=payload.note)
    await repo.commit()
    return svc.alert_out(alert)


# --------------------------------------------------------------- skill / harness
@router.get("/skills", response_model=list[SkillOut], summary="skill 版本列表")
async def list_skills(
    _: OwnerDep, repo: EvalRepoDep, status_filter: str | None = Query(default=None, alias="status")
) -> list[SkillOut]:
    skills = await svc.list_skills(repo, status=status_filter)
    await repo.commit()
    return [svc.skill_out(s) for s in skills]


@router.post(
    "/skills",
    response_model=SkillOut,
    status_code=status.HTTP_201_CREATED,
    summary="登记 skill（无 eval set 不能 active）",
)
async def create_skill(
    payload: SkillCreate, _: OwnerDep, repo: EvalRepoDep
) -> SkillOut:
    skill = await svc.create_skill(repo, payload)
    await repo.commit()
    return svc.skill_out(skill)


@router.patch("/skills/{skill_id}/status", response_model=SkillOut, summary="上线/暂停/退役")
async def set_skill_status(
    skill_id: str, payload: SkillStatusUpdate, _: OwnerDep, repo: EvalRepoDep
) -> SkillOut:
    skill = await svc.set_skill_status(repo, skill_id, payload.status)
    await repo.commit()
    return svc.skill_out(skill)


@router.get("/harness", response_model=list[HarnessOut], summary="harness 模板列表")
async def list_harness(_: OwnerDep, repo: EvalRepoDep) -> list[HarnessOut]:
    rows = await repo.list_harness()
    return [HarnessOut.model_validate(h) for h in rows]


@router.post(
    "/harness",
    response_model=HarnessOut,
    status_code=status.HTTP_201_CREATED,
    summary="登记 harness 模板（必须带 eval 集才算模板）",
)
async def create_harness(
    payload: HarnessCreate, _: OwnerDep, repo: EvalRepoDep
) -> HarnessOut:
    harness = HarnessTemplate(
        id=new_id(),
        name=payload.name,
        version=payload.version,
        skill_ids=payload.skill_ids,
        config=payload.config,
        eval_task_ids=payload.eval_task_ids,
        created_at=datetime.now(UTC),
    )
    harness = await repo.create_harness(harness)
    await repo.commit()
    return HarnessOut.model_validate(harness)


# ------------------------------------------------------------------ 认知卡
@router.get("/cards", response_model=list[CardOut], summary="认知卡库")
async def list_cards(
    _: OwnerDep, repo: EvalRepoDep, status_filter: str | None = Query(default=None, alias="status")
) -> list[CardOut]:
    cards = await repo.list_cards(status=status_filter)
    return [CardOut.model_validate(c) for c in cards]


@router.post(
    "/cards",
    response_model=CardOut,
    status_code=status.HTTP_201_CREATED,
    summary="新建认知卡（draft，需被复用一次才算完成）",
)
async def create_card(payload: CardCreate, _: OwnerDep, repo: EvalRepoDep) -> CardOut:
    card = CognitionCard(
        id=new_id(),
        title=payload.title,
        trigger=payload.trigger,
        rule=payload.rule,
        counterexample=payload.counterexample,
        confidence=payload.confidence,
        reuse_count=0,
        expires_at=payload.expires_at,
        status="draft",
        created_at=datetime.now(UTC),
    )
    card = await repo.create_card(card)
    await repo.commit()
    return CardOut.model_validate(card)


@router.post(
    "/cards/{card_id}/reuse",
    response_model=CardReuseOut,
    summary="复用一次（进化轨验收：reuse_count>0）",
)
async def reuse_card(card_id: str, _: OwnerDep, repo: EvalRepoDep) -> CardReuseOut:
    out = await svc.reuse_card(repo, card_id)
    await repo.commit()
    return out


__all__ = ["router"]
