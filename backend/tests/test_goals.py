"""W02 · goals 模块测试。

覆盖：
- W02-I03 双轨目标卡：创建、编号、状态机、PATCH；
- W02-I04 模板库与止损判定（纯函数 + 落库）；
- W02-I05 主动降级；
- W02-I06 看板聚合（含 unknown 而非 0 的约束）。

测试风格与 test_intake 一致：fake repository + dependency_overrides。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ConflictError
from app.core.security import create_token
from app.main import create_app
from app.modules.goals import service as gsvc
from app.modules.goals.models import Goal
from app.modules.goals.repository import new_id, to_out
from app.modules.goals.router import get_goals_repository
from app.modules.goals.rules import (
    assert_transition,
    build_code,
    is_growth_stop_loss_triggered,
    is_money_stop_loss_triggered,
    track_prefix,
)
from app.modules.goals.templates import get_template_defaults, list_templates

OWNER = "owner-1"
TOKEN = create_token(OWNER, "access", extra={"role": "owner"})
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# Fake repository
# ---------------------------------------------------------------------------


class FakeGoalRepo:
    def __init__(self) -> None:
        self._goals: list[Goal] = []
        self._budget_used: dict[str, float] = {}
        # S2/R04 回归计数：service 写路径必须显式 commit，
        # 只 flush 不 commit 的 bug 表现为 POST 201 / PATCH 200 后数据丢失。
        self.commit_calls = 0

    def _clone(self, g: Goal) -> Goal:
        copy = Goal(
            id=g.id,
            code=g.code,
            owner_subject=g.owner_subject,
            track=g.track,
            template=g.template,
            title=g.title,
            acceptance=g.acceptance,
            horizon=g.horizon,
            horizon_at=g.horizon_at,
            budget_money=g.budget_money,
            budget_hours=g.budget_hours,
            attention_minutes_per_day=g.attention_minutes_per_day,
            north_star=g.north_star,
            guardrails=g.guardrails,
            deliverables=g.deliverables,
            status=g.status,
            intake_item_id=g.intake_item_id,
            created_at=g.created_at,
            updated_at=g.updated_at,
            achieved_at=g.achieved_at,
            frozen_at=g.frozen_at,
            degraded_at=g.degraded_at,
            stop_loss_triggered_at=g.stop_loss_triggered_at,
        )
        return copy

    async def create(self, goal: Goal) -> Goal:
        # 校验 code unique
        if any(g.code == goal.code for g in self._goals):
            raise ConflictError("目标编号冲突")
        self._goals.append(goal)
        return self._clone(goal)

    async def get(self, goal_id: str, *, owner: str) -> Goal:
        for g in self._goals:
            if g.id == goal_id and g.owner_subject == owner:
                return self._clone(g)
        from app.core.errors import NotFoundError

        raise NotFoundError("目标不存在或不属于当前用户")

    async def get_by_code(self, code: str, *, owner: str) -> Goal:
        for g in self._goals:
            if g.code == code and g.owner_subject == owner:
                return self._clone(g)
        from app.core.errors import NotFoundError

        raise NotFoundError("目标不存在或不属于当前用户")

    async def list_by_owner(
        self,
        owner: str,
        *,
        statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[Goal]:
        rows = [g for g in self._goals if g.owner_subject == owner]
        if statuses:
            rows = [g for g in rows if g.status in statuses]
        rows.sort(key=lambda x: x.created_at, reverse=True)
        return [self._clone(g) for g in rows[:limit]]

    async def update(self, goal_id: str, updates: dict, *, at: datetime | None = None) -> Goal:
        for g in self._goals:
            if g.id == goal_id:
                for k, v in updates.items():
                    setattr(g, k, v)
                if at is not None:
                    g.updated_at = at
                return self._clone(g)
        from app.core.errors import NotFoundError

        raise NotFoundError("目标不存在")

    async def next_seq_for(self, track: str, date: datetime) -> int:
        prefix = f"O-{'M' if track == 'money' else 'E'}-{date.strftime('%Y%m%d')}-"
        max_seq = 0
        for g in self._goals:
            if g.code.startswith(prefix):
                try:
                    max_seq = max(max_seq, int(g.code.rsplit("-", 1)[-1]))
                except ValueError:
                    pass
        return max_seq + 1

    async def budget_used_for(self, goal_id: str) -> float | None:
        return self._budget_used.get(goal_id)

    async def mark_intake_promoted(self, intake_item_id: str, goal_id: str, at: datetime) -> None:
        return None

    async def commit(self) -> None:
        """记录一次显式提交（S2/R04 回归断言用）。"""
        self.commit_calls += 1


@pytest.fixture
def fake_repo() -> FakeGoalRepo:
    return FakeGoalRepo()


@pytest.fixture
def client(fake_repo: FakeGoalRepo) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_goals_repository] = lambda: fake_repo
    return TestClient(app)


# ---------------------------------------------------------------------------
# W02-I03 · 编号与创建
# ---------------------------------------------------------------------------


def test_build_code_format() -> None:
    d = datetime(2026, 10, 18, 12, 0, 0)
    assert build_code("money", d, 1) == "O-M-20261018-001"
    assert build_code("growth", d, 12) == "O-E-20261018-012"
    assert build_code("money", d, 999) == "O-M-20261018-999"


def test_track_prefix_validation() -> None:
    assert track_prefix("money") == "M"
    assert track_prefix("growth") == "E"
    with pytest.raises(ValueError):
        track_prefix("xxx")  # type: ignore[arg-type]


def test_create_goal_money_track(client: TestClient) -> None:
    r = client.post(
        "/api/v1/goals/",
        json={
            "track": "money",
            "template": "A",
            "title": "30 天 300 私域线索",
            "acceptance": "留下联系方式且通过初筛",
            "horizon": "30 天",
            "budget_money": 6000,
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["code"].startswith("O-M-")
    assert body["track"] == "money"
    assert body["status"] == "draft"
    assert body["budget_money"] == 6000.0
    assert body["budget_hours"] is None


def test_create_goal_growth_track(client: TestClient) -> None:
    r = client.post(
        "/api/v1/goals/",
        json={
            "track": "growth",
            "template": "D",
            "title": "读完《XX》并产出 1 张认知卡",
            "acceptance": "1 张卡（触发条件/规则/反例/置信度）",
            "horizon": "14 天",
            "budget_hours": 4,
        },
        headers=AUTH,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["code"].startswith("O-E-")
    assert body["track"] == "growth"
    assert body["budget_hours"] == 4.0
    assert body["budget_money"] is None


def test_goal_code_increments_within_same_day(client: TestClient) -> None:
    payload = {
        "track": "money",
        "title": "目标 A",
        "acceptance": "验收 A",
    }
    r1 = client.post("/api/v1/goals/", json=payload, headers=AUTH)
    r2 = client.post("/api/v1/goals/", json={**payload, "title": "目标 B"}, headers=AUTH)
    r3 = client.post("/api/v1/goals/", json={**payload, "title": "目标 C"}, headers=AUTH)
    assert r1.status_code == r2.status_code == r3.status_code == 201
    assert r1.json()["code"] != r2.json()["code"] != r3.json()["code"]
    # 序号 1 → 2 → 3
    seqs = [int(r.json()["code"].rsplit("-", 1)[-1]) for r in (r1, r2, r3)]
    assert seqs == [1, 2, 3]


def test_create_goal_rejects_missing_acceptance(client: TestClient) -> None:
    r = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "无验收"},
        headers=AUTH,
    )
    assert r.status_code == 422


def test_create_goal_rejects_blank_acceptance(client: TestClient) -> None:
    r = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "空", "acceptance": "   "},
        headers=AUTH,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# W02-I03 · 状态机
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current,target,expected",
    [
        ("draft", "active", True),
        ("active", "achieved", True),
        ("active", "frozen", True),
        ("active", "degraded", True),
        # 非法
        ("draft", "achieved", False),  # 跳级
        ("draft", "degraded", False),
        ("achieved", "active", False),  # 反向
        ("frozen", "active", False),
        ("degraded", "active", False),
        ("achieved", "frozen", False),  # 终态互转
    ],
)
def test_state_machine_pure_function(current: str, target: str, expected: bool) -> None:
    from app.modules.goals.rules import can_transition

    assert can_transition(current, target) is expected


def test_assert_transition_raises_on_illegal() -> None:
    with pytest.raises(ConflictError):
        assert_transition("achieved", "active")
    with pytest.raises(ConflictError):
        assert_transition("draft", "achieved")
    # 合法不抛
    assert_transition("draft", "active")
    assert_transition("active", "achieved")


def test_patch_goal_transitions_active_to_achieved(client: TestClient, fake_repo: FakeGoalRepo) -> None:
    created = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "达标", "acceptance": "完成"},
        headers=AUTH,
    ).json()
    # draft → active
    r1 = client.patch(
        f"/api/v1/goals/{created['id']}", json={"status": "active"}, headers=AUTH
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "active"
    # active → achieved
    r2 = client.patch(
        f"/api/v1/goals/{created['id']}", json={"status": "achieved"}, headers=AUTH
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "achieved"
    assert r2.json()["achieved_at"] is not None


def test_patch_goal_rejects_illegal_transition(client: TestClient) -> None:
    created = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "非法", "acceptance": "验收"},
        headers=AUTH,
    ).json()
    # draft → achieved 是非法跳级
    r = client.patch(
        f"/api/v1/goals/{created['id']}", json={"status": "achieved"}, headers=AUTH
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_patch_goal_rejects_reverse_from_terminal(client: TestClient) -> None:
    created = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "终态", "acceptance": "验收"},
        headers=AUTH,
    ).json()
    # 推到 active → achieved
    client.patch(f"/api/v1/goals/{created['id']}", json={"status": "active"}, headers=AUTH)
    client.patch(f"/api/v1/goals/{created['id']}", json={"status": "achieved"}, headers=AUTH)
    # 反向：achieved → active
    r = client.patch(
        f"/api/v1/goals/{created['id']}", json={"status": "active"}, headers=AUTH
    )
    assert r.status_code == 409


def test_patch_goal_updates_fields(client: TestClient) -> None:
    created = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "原标题", "acceptance": "原验收"},
        headers=AUTH,
    ).json()
    r = client.patch(
        f"/api/v1/goals/{created['id']}",
        json={"title": "新标题", "budget_money": 5000, "horizon": "14 天"},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "新标题"
    assert body["budget_money"] == 5000.0
    assert body["horizon"] == "14 天"


# ---------------------------------------------------------------------------
# W02-I03 · 读：单条 / 列表 / 按状态过滤
# ---------------------------------------------------------------------------


def test_get_goal_returns_200(client: TestClient) -> None:
    created = client.post(
        "/api/v1/goals/",
        json={"track": "growth", "title": "阅读", "acceptance": "1 张卡"},
        headers=AUTH,
    ).json()
    r = client.get(f"/api/v1/goals/{created['id']}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_goal_404_when_missing(client: TestClient) -> None:
    r = client.get("/api/v1/goals/nonexistent", headers=AUTH)
    assert r.status_code == 404


def test_list_goals_filters_by_status(client: TestClient) -> None:
    g1 = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "草稿", "acceptance": "a"},
        headers=AUTH,
    ).json()
    g2 = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "激活", "acceptance": "b"},
        headers=AUTH,
    ).json()
    client.patch(f"/api/v1/goals/{g2['id']}", json={"status": "active"}, headers=AUTH)

    all_draft = client.get("/api/v1/goals/?status=draft", headers=AUTH).json()
    assert len(all_draft) == 1
    assert all_draft[0]["id"] == g1["id"]

    all_active = client.get("/api/v1/goals/?status=active", headers=AUTH).json()
    assert len(all_active) == 1
    assert all_active[0]["id"] == g2["id"]


def test_list_goals_requires_auth(client: TestClient) -> None:
    r = client.get("/api/v1/goals/")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# W02-I04 · 模板库
# ---------------------------------------------------------------------------


def test_list_templates_three_available_rest_planned() -> None:
    tpls = list_templates()
    assert len(tpls) == 8
    available = [t for t in tpls if t["status"] == "available"]
    planned = [t for t in tpls if t["status"] == "planned"]
    assert len(available) == 3
    assert len(planned) == 5
    ids_avail = {t["id"] for t in available}
    assert ids_avail == {"A", "G", "D"}
    ids_planned = {t["id"] for t in planned}
    assert ids_planned == {"B", "C", "E", "F", "H"}


def test_templates_endpoint_returns_3_available_5_planned(client: TestClient) -> None:
    r = client.get("/api/v1/goals/templates")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 8
    assert {t["id"] for t in items if t["status"] == "available"} == {"A", "G", "D"}


def test_get_template_defaults_known() -> None:
    d = get_template_defaults("A")
    assert d is not None
    assert d.acceptance and "线索" in d.acceptance
    assert d.horizon and "30 天" in d.horizon


def test_get_template_defaults_unknown_returns_none() -> None:
    assert get_template_defaults("X") is None
    assert get_template_defaults("") is None


# ---------------------------------------------------------------------------
# W02-I04 · 止损判定纯函数
# ---------------------------------------------------------------------------


def test_growth_stop_loss_triggers_after_7d_without_deliverable() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0)
    active_at = datetime(2026, 10, 18, 12, 0, 0)
    assert is_growth_stop_loss_triggered(
        active_at=active_at, now=now, has_deliverable=False
    ) is True


def test_growth_stop_loss_not_triggered_within_7d() -> None:
    now = datetime(2026, 10, 20, 12, 0, 0)
    active_at = datetime(2026, 10, 18, 12, 0, 0)
    assert is_growth_stop_loss_triggered(
        active_at=active_at, now=now, has_deliverable=False
    ) is False


def test_growth_stop_loss_not_triggered_with_deliverable() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0)
    active_at = datetime(2026, 10, 18, 12, 0, 0)
    # 即使 T+7 没到，但有产出物 → 不触发
    assert is_growth_stop_loss_triggered(
        active_at=active_at, now=now, has_deliverable=True
    ) is False


def test_money_stop_loss_triggered_when_output_below_p25() -> None:
    assert is_money_stop_loss_triggered(history_p25=100.0, current_output=50.0) is True


def test_money_stop_loss_not_triggered_when_output_at_or_above_p25() -> None:
    assert is_money_stop_loss_triggered(history_p25=100.0, current_output=100.0) is False
    assert is_money_stop_loss_triggered(history_p25=100.0, current_output=200.0) is False


def test_check_stop_loss_endpoint_growth_triggers_after_7d(
    client: TestClient,
) -> None:
    # 直接造一条 growth 目标，created_at 在 8 天前
    async def seed():
        repo = FakeGoalRepo()
        old = datetime.utcnow() - timedelta(days=8)
        goal = Goal(
            id=new_id(),
            code="O-E-20261010-001",
            owner_subject=OWNER,
            track="growth",
            template="D",
            title="老目标",
            acceptance="一张卡",
            status="active",
            created_at=old,
            updated_at=old,
        )
        repo._goals.append(goal)
        return repo

    repo = asyncio.run(seed())
    app = create_app()
    app.dependency_overrides[get_goals_repository] = lambda: repo
    c = TestClient(app)
    gid = repo._goals[0].id
    r = c.post(f"/api/v1/goals/{gid}/check-stop-loss", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["triggered"] is True
    assert "T+" in body["reason"]


def test_check_stop_loss_endpoint_growth_not_triggered_with_deliverable(
    fake_repo: FakeGoalRepo,
) -> None:
    old = datetime.utcnow() - timedelta(days=8)
    goal = Goal(
        id=new_id(),
        code="O-E-20261010-002",
        owner_subject=OWNER,
        track="growth",
        template="D",
        title="有产出物",
        acceptance="一张卡",
        status="active",
        created_at=old,
        updated_at=old,
        deliverables={"cards": ["card-1"]},
    )
    fake_repo._goals.append(goal)

    async def run() -> None:
        result = await gsvc.check_stop_loss(fake_repo, OWNER, goal.id)
        assert result.triggered is False

    asyncio.run(run())


# ---------------------------------------------------------------------------
# W02-I05 · 主动降级
# ---------------------------------------------------------------------------


def test_degrade_only_allowed_from_active(client: TestClient) -> None:
    created = client.post(
        "/api/v1/goals/",
        json={"track": "growth", "title": "降", "acceptance": "a"},
        headers=AUTH,
    ).json()
    # draft → degrade 非法
    r = client.post(f"/api/v1/goals/{created['id']}/degrade", headers=AUTH)
    assert r.status_code == 409
    # 先 active → degrade 合法
    client.patch(f"/api/v1/goals/{created['id']}", json={"status": "active"}, headers=AUTH)
    r2 = client.post(f"/api/v1/goals/{created['id']}/degrade", headers=AUTH)
    assert r2.status_code == 200
    assert r2.json()["status"] == "degraded"
    assert r2.json()["degraded_at"] is not None


# ---------------------------------------------------------------------------
# W02-I06 · 看板聚合
# ---------------------------------------------------------------------------


def test_summary_returns_aggregated_fields(fake_repo: FakeGoalRepo) -> None:
    """预算数据缺失（W07 未就绪）时 budget_used=None（不是 0）。"""
    async def run():
        g1 = Goal(
            id=new_id(),
            code="O-M-20261018-001",
            owner_subject=OWNER,
            track="money",
            template="A",
            title="赚钱",
            acceptance="300 线索",
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            budget_money=6000,
        )
        g2 = Goal(
            id=new_id(),
            code="O-E-20261018-002",
            owner_subject=OWNER,
            track="growth",
            template="D",
            title="阅读",
            acceptance="1 张卡",
            status="active",
            created_at=datetime.utcnow() - timedelta(days=8),
            updated_at=datetime.utcnow() - timedelta(days=8),
        )
        fake_repo._goals.extend([g1, g2])
        # 假装 W07 ledger 给出 g1 已用 1200 元
        fake_repo._budget_used[g1.id] = 1200.0
        summary = await gsvc.build_summary(fake_repo, OWNER)
        assert summary.total == 2
        by_id = {i.id: i for i in summary.items}
        # g1: 有预算已用、未触发止损（未提供 P25）
        assert by_id[g1.id].budget_used == 1200.0
        assert by_id[g1.id].stop_loss_triggered is False
        # g2: 预算 unknown(None)，触发 T+7 止损
        assert by_id[g2.id].budget_used is None  # unknown 而非 0
        assert by_id[g2.id].stop_loss_triggered is True
        # days_remaining 在没有 horizon_at 时为 None
        assert by_id[g1.id].days_remaining is None

    asyncio.run(run())


def test_summary_endpoint_via_http(
    client: TestClient, fake_repo: FakeGoalRepo
) -> None:
    # 先建两条目标
    client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "a", "acceptance": "x"},
        headers=AUTH,
    )
    r = client.get("/api/v1/goals/summary", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    item = body["items"][0]
    # 必含这三项
    assert "budget_used" in item
    assert "days_remaining" in item
    assert "stop_loss_triggered" in item


def test_summary_does_not_call_repo_per_item(
    fake_repo: FakeGoalRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """禁 N+1：build_summary 内部对 budget_used_for 的调用次数应 ≤ goal 数。
    这里通过计数 fake 调用次数验证：list_by_owner 只调一次。
    """
    call_count = {"list": 0}

    orig_list = fake_repo.list_by_owner

    async def counting_list(*args, **kwargs):
        call_count["list"] += 1
        return await orig_list(*args, **kwargs)

    monkeypatch.setattr(fake_repo, "list_by_owner", counting_list)

    async def run():
        g = Goal(
            id=new_id(),
            code="O-M-20261018-005",
            owner_subject=OWNER,
            track="money",
            template="",
            title="a",
            acceptance="b",
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        fake_repo._goals.append(g)
        await gsvc.build_summary(fake_repo, OWNER)
        assert call_count["list"] == 1, "禁止 N+1：list_by_owner 必须只调一次"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# 兴趣池端点（W02-I05）
# ---------------------------------------------------------------------------


def test_interests_endpoint_lists_archived_items(
    fake_repo: FakeGoalRepo,
) -> None:
    """GET /intake/interests 由 intake 模块提供，已在 test_intake.py 覆盖。
    这里只验证 goals 与 intake 在同一 app 内共存不冲突。
    """
    app = create_app()
    # 注入两个模块的 repo（共存测试）
    from app.modules.intake.router import get_intake_repository

    class EmptyIntakeRepo:
        async def list_interests(self, owner, *, limit=100):
            return []

    app.dependency_overrides[get_goals_repository] = lambda: fake_repo
    app.dependency_overrides[get_intake_repository] = lambda: EmptyIntakeRepo()
    c = TestClient(app)
    r = c.get("/api/v1/intake/interests", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# S2/R04 · 写路径显式 commit 回归（评审 Issue 2：返回 201 但数据静默丢失）
# ---------------------------------------------------------------------------


def test_create_goal_commits_transaction(client: TestClient, fake_repo: FakeGoalRepo) -> None:
    """POST /goals/ 201 后必须可见，且 service 确实提交了事务。

    回归背景：create_goal 只 flush 不 commit，请求结束即丢失——
    POST 201 但 GET /goals/ 为空、psql 0 行。
    """
    r = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "落库回归", "acceptance": "可见"},
        headers=AUTH,
    )
    assert r.status_code == 201
    created_id = r.json()["id"]
    assert fake_repo.commit_calls == 1

    r2 = client.get("/api/v1/goals/?limit=100", headers=AUTH)
    assert r2.status_code == 200
    assert any(g["id"] == created_id for g in r2.json())


def test_patch_goal_commits_transaction(client: TestClient, fake_repo: FakeGoalRepo) -> None:
    """PATCH（含状态迁移）后必须提交，否则 200 但状态不落库。"""
    created = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "补丁回归", "acceptance": "a"},
        headers=AUTH,
    ).json()
    before = fake_repo.commit_calls

    r = client.patch(
        f"/api/v1/goals/{created['id']}", json={"status": "active"}, headers=AUTH
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert fake_repo.commit_calls == before + 1

    got = client.get(f"/api/v1/goals/{created['id']}", headers=AUTH)
    assert got.json()["status"] == "active"


def test_patch_goal_illegal_transition_does_not_commit(
    client: TestClient, fake_repo: FakeGoalRepo
) -> None:
    """非法迁移在写库前就被状态机拒绝：不应产生任何提交。"""
    created = client.post(
        "/api/v1/goals/",
        json={"track": "money", "title": "非法回归", "acceptance": "a"},
        headers=AUTH,
    ).json()
    before = fake_repo.commit_calls
    r = client.patch(
        f"/api/v1/goals/{created['id']}", json={"status": "achieved"}, headers=AUTH
    )
    assert r.status_code == 409
    assert fake_repo.commit_calls == before


def test_degrade_commits_transaction(client: TestClient, fake_repo: FakeGoalRepo) -> None:
    """POST /goals/{id}/degrade 的降级写入必须提交。"""
    created = client.post(
        "/api/v1/goals/",
        json={"track": "growth", "title": "降级回归", "acceptance": "a"},
        headers=AUTH,
    ).json()
    client.patch(f"/api/v1/goals/{created['id']}", json={"status": "active"}, headers=AUTH)
    before = fake_repo.commit_calls

    r = client.post(f"/api/v1/goals/{created['id']}/degrade", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert fake_repo.commit_calls == before + 1

    got = client.get(f"/api/v1/goals/{created['id']}", headers=AUTH)
    assert got.json()["status"] == "degraded"
    assert got.json()["degraded_at"] is not None


def test_check_stop_loss_commits_only_when_triggered(fake_repo: FakeGoalRepo) -> None:
    """止损触发时才写 stop_loss_triggered_at；未触发不写不提交。"""
    old = datetime.utcnow() - timedelta(days=8)
    triggered_goal = Goal(
        id=new_id(),
        code="O-E-20261010-001",
        owner_subject=OWNER,
        track="growth",
        template="D",
        title="老目标",
        acceptance="一张卡",
        status="active",
        created_at=old,
        updated_at=old,
    )
    fresh_goal = Goal(
        id=new_id(),
        code="O-E-20261010-002",
        owner_subject=OWNER,
        track="growth",
        template="D",
        title="新目标",
        acceptance="一张卡",
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    fake_repo._goals.extend([triggered_goal, fresh_goal])

    async def run() -> None:
        r1 = await gsvc.check_stop_loss(fake_repo, OWNER, triggered_goal.id)
        assert r1.triggered is True
        assert fake_repo.commit_calls == 1
        # 落库值可读回
        saved = await fake_repo.get(triggered_goal.id, owner=OWNER)
        assert saved.stop_loss_triggered_at is not None

        r2 = await gsvc.check_stop_loss(fake_repo, OWNER, fresh_goal.id)
        assert r2.triggered is False
        assert fake_repo.commit_calls == 1, "未触发不应产生提交"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# to_out 归一化（CODE-REVIEW P1-1 / issue #37：脏数据不打 500）
# ---------------------------------------------------------------------------


def _goal(**overrides) -> Goal:
    base = dict(
        id=new_id(),
        code="O-M-20261019-001",
        owner_subject=OWNER,
        track="money",
        template="",
        title="测试目标",
        acceptance="可验收",
        status="draft",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    base.update(overrides)
    return Goal(**base)


def test_to_out_wraps_legacy_array_json_columns() -> None:
    """历史库里 guardrails/deliverables 是数组时归一成 {"items": [...]}。

    GoalOut 契约是 dict[str, Any] | None；修复前一条脏数据让
    GET /goals/ 整个列表 500（CODE-REVIEW P1-1）。
    """
    goal = _goal(guardrails=["不碰灰产渠道"], deliverables=["20 篇内容"])
    out = to_out(goal)
    assert out.guardrails == {"items": ["不碰灰产渠道"]}
    assert out.deliverables == {"items": ["20 篇内容"]}


def test_to_out_keeps_dict_and_none() -> None:
    """dict 原样、None 原样——正常数据零变化。"""
    goal = _goal(guardrails={"违规": 0}, deliverables=None)
    out = to_out(goal)
    assert out.guardrails == {"违规": 0}
    assert out.deliverables is None


def test_to_out_wraps_scalar_json_columns() -> None:
    """标量脏数据也不炸（包成 {"value": ...}）。"""
    goal = _goal(guardrails="写坏了", deliverables=42)
    out = to_out(goal)
    assert out.guardrails == {"value": "写坏了"}
    assert out.deliverables == {"value": 42}


def test_templates_json_columns_are_dicts() -> None:
    """全量模板的 guardrails/deliverables 必须是 dict。

    模板 D 曾把 guardrails 写成 set 笔误（{"每周 ≤2 本", "无卡不算完成"}），
    流出后客户端回传即产生数组脏数据——这里防回归。
    """
    for t in list_templates():
        if t["status"] != "available":
            continue
        assert isinstance(t["guardrails"], dict), f"模板 {t['id']} guardrails 不是 dict"
        assert isinstance(t["deliverables"], dict), f"模板 {t['id']} deliverables 不是 dict"
