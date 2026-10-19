"""evals 数据访问层。

`EvalRepository` 是 Protocol（测试注入 fake），`SqlEvalRepository` 是生产实现。
service 只依赖 Protocol，因此聚合、边界判定可以完全脱离数据库单测。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError

from .models import (
    CognitionCard,
    DriftAlert,
    EvalResult,
    EvalRun,
    EvalTask,
    HarnessTemplate,
    Skill,
)


def new_id() -> str:
    return uuid.uuid4().hex


class EvalRepository(Protocol):
    async def commit(self) -> None: ...

    # --- 题库 ---
    async def list_tasks(self, *, active_only: bool = True) -> list[EvalTask]: ...
    async def list_tasks_by_ids(self, task_ids: list[str]) -> list[EvalTask]: ...
    async def create_task(self, task: EvalTask) -> EvalTask: ...

    # --- 跑批 ---
    async def create_run(self, run: EvalRun) -> EvalRun: ...
    async def get_run(self, run_id: str) -> EvalRun: ...
    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        cost_used_usd: float | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> EvalRun: ...
    async def list_runs(self, *, limit: int = 20) -> list[EvalRun]: ...

    # --- 结果 ---
    async def add_results(self, results: list[EvalResult]) -> int: ...
    async def list_results(
        self, *, week: str | None = None, model: str | None = None
    ) -> list[EvalResult]: ...
    async def list_results_for_weeks(self, weeks: list[str]) -> list[EvalResult]: ...
    async def list_results_since(self, since: datetime) -> list[EvalResult]: ...

    # --- 漂移 ---
    async def insert_alerts(self, alerts: list[DriftAlert]) -> list[DriftAlert]: ...
    async def list_alerts(
        self, *, week: str | None = None, status: str | None = None
    ) -> list[DriftAlert]: ...
    async def get_alert(self, alert_id: str) -> DriftAlert: ...
    async def update_alert(
        self, alert_id: str, *, status: str, note: str | None
    ) -> DriftAlert: ...

    # --- skill / harness ---
    async def create_skill(self, skill: Skill) -> Skill: ...
    async def get_skill(self, skill_id: str) -> Skill: ...
    async def get_skill_by_version(self, name: str, version: str) -> Skill | None: ...
    async def list_skills(self, *, status: str | None = None) -> list[Skill]: ...
    async def update_skill_status(self, skill_id: str, *, status: str) -> Skill: ...
    async def create_harness(self, harness: HarnessTemplate) -> HarnessTemplate: ...
    async def list_harness(self) -> list[HarnessTemplate]: ...

    # --- 认知卡 ---
    async def create_card(self, card: CognitionCard) -> CognitionCard: ...
    async def get_card(self, card_id: str) -> CognitionCard: ...
    async def list_cards(self, *, status: str | None = None) -> list[CognitionCard]: ...
    async def increment_card_reuse(
        self, card_id: str, *, at: datetime
    ) -> CognitionCard: ...


class SqlEvalRepository:
    """生产实现。写入由调用方 commit（service 层控制事务边界）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def commit(self) -> None:
        await self._s.commit()

    # ------------------------------------------------------------------ 题库
    async def list_tasks(self, *, active_only: bool = True) -> list[EvalTask]:
        stmt = select(EvalTask)
        if active_only:
            stmt = stmt.where(EvalTask.active.is_(True))
        stmt = stmt.order_by(EvalTask.dimension, EvalTask.id)
        return list((await self._s.execute(stmt)).scalars())

    async def list_tasks_by_ids(self, task_ids: list[str]) -> list[EvalTask]:
        if not task_ids:
            return []
        stmt = select(EvalTask).where(EvalTask.id.in_(task_ids))
        return list((await self._s.execute(stmt)).scalars())

    async def create_task(self, task: EvalTask) -> EvalTask:
        self._s.add(task)
        await self._s.flush()
        return task

    # ------------------------------------------------------------------ 跑批
    async def create_run(self, run: EvalRun) -> EvalRun:
        self._s.add(run)
        await self._s.flush()
        return run

    async def get_run(self, run_id: str) -> EvalRun:
        run = await self._s.get(EvalRun, run_id)
        if run is None:
            raise NotFoundError("评测批次不存在", details={"run_id": run_id})
        return run

    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        cost_used_usd: float | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> EvalRun:
        run = await self.get_run(run_id)
        if status is not None:
            run.status = status
        if cost_used_usd is not None:
            run.cost_used_usd = cost_used_usd
        if started_at is not None:
            run.started_at = started_at
        if finished_at is not None:
            run.finished_at = finished_at
        if error is not None:
            run.error = error
        await self._s.flush()
        return run

    async def list_runs(self, *, limit: int = 20) -> list[EvalRun]:
        stmt = select(EvalRun).order_by(desc(EvalRun.created_at)).limit(limit)
        return list((await self._s.execute(stmt)).scalars())

    # ------------------------------------------------------------------ 结果
    async def add_results(self, results: list[EvalResult]) -> int:
        if not results:
            return 0
        self._s.add_all(results)
        await self._s.flush()
        return len(results)

    async def list_results(
        self, *, week: str | None = None, model: str | None = None
    ) -> list[EvalResult]:
        stmt = select(EvalResult)
        if week:
            stmt = stmt.where(EvalResult.week == week)
        if model:
            stmt = stmt.where(EvalResult.model == model)
        return list((await self._s.execute(stmt)).scalars())

    async def list_results_for_weeks(self, weeks: list[str]) -> list[EvalResult]:
        if not weeks:
            return []
        stmt = select(EvalResult).where(EvalResult.week.in_(weeks))
        return list((await self._s.execute(stmt)).scalars())

    async def list_results_since(self, since: datetime) -> list[EvalResult]:
        """7 天缓存用：按 created_at 取近端结果。"""
        stmt = select(EvalResult).where(EvalResult.created_at >= since)
        return list((await self._s.execute(stmt)).scalars())

    # ------------------------------------------------------------------ 漂移
    async def insert_alerts(self, alerts: list[DriftAlert]) -> list[DriftAlert]:
        """幂等写入：同 (model, dimension, week) 已存在则跳过。"""
        if not alerts:
            return []
        rows = [
            {
                "id": a.id,
                "model": a.model,
                "dimension": a.dimension,
                "week": a.week,
                "prev_week": a.prev_week,
                "prev_score": a.prev_score,
                "curr_score": a.curr_score,
                "delta_pct": a.delta_pct,
                "status": a.status,
                "note": a.note,
            }
            for a in alerts
        ]
        stmt = (
            pg_insert(DriftAlert)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_drift_week")
            .returning(DriftAlert)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def list_alerts(
        self, *, week: str | None = None, status: str | None = None
    ) -> list[DriftAlert]:
        stmt = select(DriftAlert)
        if week:
            stmt = stmt.where(DriftAlert.week == week)
        if status:
            stmt = stmt.where(DriftAlert.status == status)
        stmt = stmt.order_by(desc(DriftAlert.created_at))
        return list((await self._s.execute(stmt)).scalars())

    async def get_alert(self, alert_id: str) -> DriftAlert:
        alert = await self._s.get(DriftAlert, alert_id)
        if alert is None:
            raise NotFoundError("告警不存在", details={"alert_id": alert_id})
        return alert

    async def update_alert(
        self, alert_id: str, *, status: str, note: str | None
    ) -> DriftAlert:
        alert = await self.get_alert(alert_id)
        alert.status = status
        if note is not None:
            alert.note = note
        await self._s.flush()
        return alert

    # --------------------------------------------------------- skill / harness
    async def create_skill(self, skill: Skill) -> Skill:
        self._s.add(skill)
        await self._s.flush()
        return skill

    async def get_skill(self, skill_id: str) -> Skill:
        skill = await self._s.get(Skill, skill_id)
        if skill is None:
            raise NotFoundError("skill 不存在", details={"skill_id": skill_id})
        return skill

    async def get_skill_by_version(self, name: str, version: str) -> Skill | None:
        stmt = select(Skill).where(Skill.name == name, Skill.version == version)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def list_skills(self, *, status: str | None = None) -> list[Skill]:
        stmt = select(Skill)
        if status:
            stmt = stmt.where(Skill.status == status)
        stmt = stmt.order_by(Skill.name, desc(Skill.created_at))
        return list((await self._s.execute(stmt)).scalars())

    async def update_skill_status(self, skill_id: str, *, status: str) -> Skill:
        skill = await self.get_skill(skill_id)
        skill.status = status
        await self._s.flush()
        return skill

    async def create_harness(self, harness: HarnessTemplate) -> HarnessTemplate:
        self._s.add(harness)
        await self._s.flush()
        return harness

    async def list_harness(self) -> list[HarnessTemplate]:
        stmt = select(HarnessTemplate).order_by(HarnessTemplate.name, desc(HarnessTemplate.created_at))
        return list((await self._s.execute(stmt)).scalars())

    # ------------------------------------------------------------------ 认知卡
    async def create_card(self, card: CognitionCard) -> CognitionCard:
        self._s.add(card)
        await self._s.flush()
        return card

    async def get_card(self, card_id: str) -> CognitionCard:
        card = await self._s.get(CognitionCard, card_id)
        if card is None:
            raise NotFoundError("认知卡不存在", details={"card_id": card_id})
        return card

    async def list_cards(self, *, status: str | None = None) -> list[CognitionCard]:
        stmt = select(CognitionCard)
        if status:
            stmt = stmt.where(CognitionCard.status == status)
        stmt = stmt.order_by(desc(CognitionCard.created_at))
        return list((await self._s.execute(stmt)).scalars())

    async def increment_card_reuse(self, card_id: str, *, at: datetime) -> CognitionCard:
        card = await self.get_card(card_id)
        card.reuse_count = int(card.reuse_count) + 1
        card.last_reused_at = at
        if card.status == "draft":
            card.status = "active"
        await self._s.flush()
        return card


def utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["EvalRepository", "SqlEvalRepository", "new_id", "utcnow"]
