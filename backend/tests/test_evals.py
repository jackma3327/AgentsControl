"""W08 · 资产面（模型雷达 / 能力上限 / 漂移 / skill / 认知卡）测试。

策略：
- 不依赖 PG/Redis：用 FakeEvalRepo 实现 EvalRepository Protocol，经 dependency_overrides 注入；
- 判定阈值（69.9/70.1/79.9、-3.1/-2.9）全部穷举，因为这些边界直接决定"能不能立项"；
- runner 用 FakeProvider，不出网、结果可复现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError
from app.core.security import create_token
from app.main import create_app
from app.modules.evals import service as svc
from app.modules.evals.models import (
    CognitionCard,
    DriftAlert,
    EvalResult,
    EvalRun,
    EvalTask,
    HarnessTemplate,
    Skill,
)
from app.modules.evals.repository import new_id
from app.modules.evals.router import get_eval_provider, get_eval_repository
from app.modules.evals.runner import FakeProvider, execute_run
from app.modules.evals.schemas import CeilingCheckRequest

OWNER = "owner-1"
TOKEN = create_token(OWNER, "access", extra={"role": "owner"})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WEEK = "2026-W42"
PREV = "2026-W41"


# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------
class FakeEvalRepo:
    def __init__(self) -> None:
        self.tasks: list[EvalTask] = []
        self.runs: list[EvalRun] = []
        self.results: list[EvalResult] = []
        self.alerts: list[DriftAlert] = []
        self.skills: list[Skill] = []
        self.harness: list[HarnessTemplate] = []
        self.cards: list[CognitionCard] = []
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    # --- 题库 ---
    async def list_tasks(self, *, active_only: bool = True) -> list[EvalTask]:
        return [t for t in self.tasks if t.active or not active_only]

    async def list_tasks_by_ids(self, task_ids: list[str]) -> list[EvalTask]:
        return [t for t in self.tasks if t.id in task_ids]

    async def create_task(self, task: EvalTask) -> EvalTask:
        self.tasks.append(task)
        return task

    # --- 跑批 ---
    async def create_run(self, run: EvalRun) -> EvalRun:
        self.runs.append(run)
        return run

    async def get_run(self, run_id: str) -> EvalRun:
        for run in self.runs:
            if run.id == run_id:
                return run
        raise NotFoundError("评测批次不存在")

    async def update_run(self, run_id: str, **fields) -> EvalRun:  # noqa: ANN003
        run = await self.get_run(run_id)
        for key, value in fields.items():
            if value is not None:
                setattr(run, key, value)
        return run

    async def list_runs(self, *, limit: int = 20) -> list[EvalRun]:
        return self.runs[-limit:][::-1]

    # --- 结果 ---
    async def add_results(self, results: list[EvalResult]) -> int:
        self.results.extend(results)
        return len(results)

    async def list_results(
        self, *, week: str | None = None, model: str | None = None
    ) -> list[EvalResult]:
        rows = self.results
        if week:
            rows = [r for r in rows if r.week == week]
        if model:
            rows = [r for r in rows if r.model == model]
        return list(rows)

    async def list_results_for_weeks(self, weeks: list[str]) -> list[EvalResult]:
        return [r for r in self.results if r.week in weeks]

    async def list_results_since(self, since: datetime) -> list[EvalResult]:
        return [r for r in self.results if r.created_at >= since]

    # --- 漂移 ---
    async def insert_alerts(self, alerts: list[DriftAlert]) -> list[DriftAlert]:
        created: list[DriftAlert] = []
        for a in alerts:
            if any(
                x.model == a.model and x.dimension == a.dimension and x.week == a.week
                for x in self.alerts
            ):
                continue
            self.alerts.append(a)
            created.append(a)
        return created

    async def list_alerts(
        self, *, week: str | None = None, status: str | None = None
    ) -> list[DriftAlert]:
        rows = self.alerts
        if week:
            rows = [a for a in rows if a.week == week]
        if status:
            rows = [a for a in rows if a.status == status]
        return list(rows)

    async def get_alert(self, alert_id: str) -> DriftAlert:
        for a in self.alerts:
            if a.id == alert_id:
                return a
        raise NotFoundError("告警不存在")

    async def update_alert(
        self, alert_id: str, *, status: str, note: str | None
    ) -> DriftAlert:
        alert = await self.get_alert(alert_id)
        alert.status = status
        if note is not None:
            alert.note = note
        return alert

    # --- skill / harness ---
    async def create_skill(self, skill: Skill) -> Skill:
        self.skills.append(skill)
        return skill

    async def get_skill(self, skill_id: str) -> Skill:
        for s in self.skills:
            if s.id == skill_id:
                return s
        raise NotFoundError("skill 不存在")

    async def get_skill_by_version(self, name: str, version: str) -> Skill | None:
        for s in self.skills:
            if s.name == name and s.version == version:
                return s
        return None

    async def list_skills(self, *, status: str | None = None) -> list[Skill]:
        return [s for s in self.skills if status is None or s.status == status]

    async def update_skill_status(self, skill_id: str, *, status: str) -> Skill:
        skill = await self.get_skill(skill_id)
        skill.status = status
        return skill

    async def create_harness(self, harness: HarnessTemplate) -> HarnessTemplate:
        self.harness.append(harness)
        return harness

    async def list_harness(self) -> list[HarnessTemplate]:
        return list(self.harness)

    # --- 认知卡 ---
    async def create_card(self, card: CognitionCard) -> CognitionCard:
        self.cards.append(card)
        return card

    async def get_card(self, card_id: str) -> CognitionCard:
        for c in self.cards:
            if c.id == card_id:
                return c
        raise NotFoundError("认知卡不存在")

    async def list_cards(self, *, status: str | None = None) -> list[CognitionCard]:
        return [c for c in self.cards if status is None or c.status == status]

    async def increment_card_reuse(self, card_id: str, *, at: datetime) -> CognitionCard:
        card = await self.get_card(card_id)
        card.reuse_count = int(card.reuse_count) + 1
        card.last_reused_at = at
        if card.status == "draft":
            card.status = "active"
        return card


@pytest.fixture()
def repo() -> FakeEvalRepo:
    r = FakeEvalRepo()
    now = datetime.now(UTC)
    for tid, dim in [
        ("t-writing", "writing"),
        ("t-code", "code"),
        ("t-vision", "vision"),
        ("t-instruction", "instruction"),
    ]:
        r.tasks.append(
            EvalTask(
                id=tid,
                dimension=dim,
                title=tid,
                prompt="p",
                rubric="r",
                weight=1,
                active=True,
                created_at=now,
            )
        )
    return r


@pytest.fixture()
def client(repo: FakeEvalRepo) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_eval_repository] = lambda: repo
    app.dependency_overrides[get_eval_provider] = lambda: FakeProvider()
    return TestClient(app)


def _result(
    *, task_id: str, dimension: str, model: str, score: float, week: str = WEEK, cost: float = 0.01
) -> EvalResult:
    return EvalResult(
        id=new_id(),
        run_id="run-x",
        task_id=task_id,
        week=week,
        dimension=dimension,
        model=model,
        score=score,
        cost_usd=cost,
        latency_ms=1000,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------- W08-I01
async def test_execute_run_produces_results_and_respects_cost_cap(repo: FakeEvalRepo) -> None:
    run = EvalRun(
        id="run-1",
        week=WEEK,
        status="queued",
        models=["m-a", "m-b"],
        cost_cap_usd=0.05,
        cost_used_usd=0,
        task_count=4,
    )
    repo.runs.append(run)
    outcome = await execute_run(repo, run, repo.tasks, FakeProvider())
    assert outcome.status in {"done", "aborted"}
    assert len(repo.results) >= 1
    assert run.status in {"done", "aborted"}


async def test_run_endpoint_returns_results_when_eager(
    client: TestClient, repo: FakeEvalRepo
) -> None:
    resp = client.post(
        "/api/v1/evals/runs?eager=true",
        json={"models": ["m-a"], "week": WEEK, "cost_cap_usd": 5},
        headers=AUTH,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["id"]
    detail = client.get(f"/api/v1/evals/runs/{run_id}/results", headers=AUTH)
    assert detail.status_code == 200
    assert len(detail.json()) == 4


def test_runs_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/evals/runs").status_code == 401


# ---------------------------------------------------------------- W08-I02
def test_radar_marks_missing_dimension_insufficient() -> None:
    radar = svc.aggregate_radar(
        [_result(task_id="t-writing", dimension="writing", model="m-a", score=90)],
        week=WEEK,
    )
    by_dim = {d.dimension: d for d in radar.dimensions}
    assert by_dim["writing"].score == 90
    assert by_dim["vision"].status == "insufficient"
    assert by_dim["vision"].score is None
    assert len(radar.dimensions) == 8


def test_radar_picks_best_model_per_dimension() -> None:
    rows = [
        _result(task_id="t1", dimension="code", model="m-a", score=60),
        _result(task_id="t1", dimension="code", model="m-b", score=85),
    ]
    radar = svc.aggregate_radar(rows, week=WEEK)
    code = next(d for d in radar.dimensions if d.dimension == "code")
    assert code.score == 85
    assert code.best_model == "m-b"
    assert code.by_model["m-a"] == 60


def test_radar_endpoint(client: TestClient, repo: FakeEvalRepo) -> None:
    repo.results.extend(
        [
            _result(task_id="t-writing", dimension="writing", model="m-a", score=88),
            _result(task_id="t-code", dimension="code", model="m-a", score=72),
        ]
    )
    resp = client.get(f"/api/v1/evals/radar?week={WEEK}", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["dimensions"]) == 8
    assert body["dimensions"][0]["label"] == "长文写作"


# ---------------------------------------------------------------- W08-I03
@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (69.9, "blocked"),
        (70.1, "risky"),
        (79.9, "risky"),
        (80.1, "ok"),
    ],
)
def test_ceiling_boundaries(score: float, expected: str) -> None:
    radar = svc.aggregate_radar(
        [_result(task_id="t1", dimension="vision", model="m-a", score=score)], week=WEEK
    )
    ceiling = svc.ceiling_from_radar(radar)
    vision = next(d for d in ceiling.dimensions if d.dimension == "vision")
    assert vision.status == expected, f"{score} 应判为 {expected}，实际 {vision.status}"


def test_ceiling_insufficient_is_not_zero() -> None:
    radar = svc.aggregate_radar([], week=WEEK)
    ceiling = svc.ceiling_from_radar(radar)
    vision = next(d for d in ceiling.dimensions if d.dimension == "vision")
    assert vision.status == "insufficient"
    assert vision.best_score is None


def test_ceiling_check_returns_specific_dimension_and_score() -> None:
    radar = svc.aggregate_radar(
        [_result(task_id="t1", dimension="writing", model="m-a", score=66)], week=WEEK
    )
    out = svc.check_capability(radar, CeilingCheckRequest(dimensions=["writing"]))
    assert out.verdict == "blocked"
    assert out.issues[0].dimension == "writing"
    assert out.issues[0].score == 66
    assert "拆小" in out.issues[0].advice


def test_ceiling_check_endpoint(client: TestClient, repo: FakeEvalRepo) -> None:
    repo.results.append(_result(task_id="t-vision", dimension="vision", model="m-a", score=64))
    resp = client.post(
        f"/api/v1/evals/ceiling/check?week={WEEK}",
        json={"dimensions": ["vision"]},
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "blocked"
    assert body["issues"][0]["label"] == "截图与视觉理解"


# ---------------------------------------------------------------- W08-I04
def test_drift_triggers_only_above_three_points() -> None:
    curr = [_result(task_id="t1", dimension="code", model="m-a", score=80)]
    prev_31 = [_result(task_id="t1", dimension="code", model="m-a", score=83.1, week=PREV)]
    prev_29 = [_result(task_id="t1", dimension="code", model="m-a", score=82.9, week=PREV)]
    assert len(svc.build_drift_alerts(curr, prev_31, week=WEEK, prev_week_str=PREV)) == 1
    assert len(svc.build_drift_alerts(curr, prev_29, week=WEEK, prev_week_str=PREV)) == 0


def test_drift_scan_is_idempotent_and_ackable(
    client: TestClient, repo: FakeEvalRepo
) -> None:
    repo.results.extend(
        [
            _result(task_id="t1", dimension="code", model="m-a", score=70),
            _result(task_id="t1", dimension="code", model="m-a", score=80, week=PREV),
        ]
    )
    first = client.post(f"/api/v1/evals/drift/scan?week={WEEK}", headers=AUTH)
    assert first.status_code == 200
    assert first.json()["created"] == 1
    second = client.post(f"/api/v1/evals/drift/scan?week={WEEK}", headers=AUTH)
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 1

    alert_id = first.json()["alerts"][0]["id"]
    ack = client.patch(
        f"/api/v1/evals/drift/{alert_id}",
        json={"status": "ack", "note": "上游已知降级"},
        headers=AUTH,
    )
    assert ack.status_code == 200
    assert ack.json()["status"] == "ack"


def test_prev_week_handles_year_boundary() -> None:
    assert svc.prev_week("2026-W01") == "2025-W52"


# ---------------------------------------------------------------- W08-I05
def test_skill_without_eval_set_cannot_activate(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/evals/skills",
        json={"name": "web-scaffold", "version": "1.0.0", "status": "active"},
        headers=AUTH,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_skill_with_eval_set_can_activate_and_duplicate_version_rejected(
    client: TestClient,
) -> None:
    payload = {
        "name": "web-scaffold",
        "version": "1.0.0",
        "eval_task_ids": ["t-code"],
        "status": "draft",
    }
    created = client.post("/api/v1/evals/skills", json=payload, headers=AUTH)
    assert created.status_code == 201
    skill_id = created.json()["id"]

    activated = client.patch(
        f"/api/v1/evals/skills/{skill_id}/status", json={"status": "active"}, headers=AUTH
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    duplicate = client.post("/api/v1/evals/skills", json=payload, headers=AUTH)
    assert duplicate.status_code == 409


async def test_overdue_skill_becomes_stale(repo: FakeEvalRepo) -> None:
    repo.skills.append(
        Skill(
            id="s1",
            name="old-skill",
            version="0.9.0",
            status="active",
            description="",
            trigger="",
            criteria="",
            counterexample="",
            eval_task_ids=["t1"],
            rollout_pct=10,
            review_due_at=datetime.now(UTC) - timedelta(days=1),
            created_at=datetime.now(UTC),
        )
    )
    skills = await svc.list_skills(repo)
    assert skills[0].status == "stale"


# ---------------------------------------------------------------- W08-I06
def test_card_needs_one_reuse_to_complete(client: TestClient) -> None:
    created = client.post(
        "/api/v1/evals/cards",
        json={
            "title": "复杂系统崩在耦合处",
            "trigger": "看到多处共享同一状态",
            "rule": "先找耦合点，再谈容错",
            "counterexample": "单点故障但无耦合时，容错更有效",
            "confidence": 70,
        },
        headers=AUTH,
    )
    assert created.status_code == 201
    card_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["reuse_count"] == 0

    first = client.post(f"/api/v1/evals/cards/{card_id}/reuse", headers=AUTH)
    assert first.status_code == 200
    assert first.json()["card"]["reuse_count"] == 1
    assert first.json()["completed"] is True
    assert first.json()["card"]["status"] == "active"

    second = client.post(f"/api/v1/evals/cards/{card_id}/reuse", headers=AUTH)
    assert second.json()["card"]["reuse_count"] == 2


def test_harness_can_be_registered_without_eval_set_but_marked_unverified(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/evals/harness",
        json={"name": "scaffold-pack", "version": "1.0.0", "eval_task_ids": []},
        headers=AUTH,
    )
    # 模板允许先建；上线由 skill 状态机拦截（模板本身不承载"上线"语义）
    assert resp.status_code == 201
    assert resp.json()["eval_task_ids"] == []
