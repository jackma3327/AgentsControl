"""W07 账本测试（test_ledger.py）—— 占位文件已在 W07-I01 落地时替换。

边界打在：幂等回放、负数冲正、0 产出 null（不是 0）、跨月/月中摊销、
配额 > 80% 告警、对账 0.5% 通过 / 2% 标红、CSV BOM。
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.errors import ValidationError
from app.core.security import create_token
from app.main import create_app
from app.modules.ledger import amortize, service
from app.modules.ledger.attention import (
    compute_min_per_outcome,
    month_bounds,
)
from app.modules.ledger.models import (
    AttentionEntry,
    LedgerEntry,
    Subscription,
)
from app.modules.ledger.reconcile import (
    is_consistent,
    reconcile_sources,
)
from app.modules.ledger.router import get_ledger_repo

AUTH = {"Authorization": f"Bearer {create_token('owner-1', 'access')}"}


# ------------------------------------------------------------------ 假仓库


class FakeLedgerRepo:
    """实现 service 用到的仓库接口；时间过滤语义与 SQL 版一致（半开区间）。"""

    def __init__(self) -> None:
        self.entries: list[LedgerEntry] = []
        self.attention: list[AttentionEntry] = []
        self.subscriptions: list[Subscription] = []
        self.goal_budgets: dict[uuid.UUID, Decimal] = {}
        self.outcomes: int | None = None
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def add_entry(self, entry: LedgerEntry) -> LedgerEntry:
        if entry.id is None:
            entry.id = uuid.uuid4()
        if entry.created_at is None:
            entry.created_at = datetime.now(UTC)
        self.entries.append(entry)
        return entry

    async def find_entry(self, source: str, source_id: str) -> LedgerEntry | None:
        return next(
            (e for e in self.entries if e.source == source and e.source_id == source_id),
            None,
        )

    async def list_entries(
        self,
        *,
        task_id=None,
        goal_id=None,
        category=None,
        start=None,
        end=None,
        limit=50,
    ):
        out = []
        for e in reversed(self.entries):  # occurred_at 倒序，与 SQL 版一致
            if task_id is not None and e.task_id != task_id:
                continue
            if goal_id is not None and e.goal_id != goal_id:
                continue
            if category is not None and e.category != category:
                continue
            if start is not None and e.occurred_at < start:
                continue
            if end is not None and e.occurred_at >= end:
                continue
            out.append(e)
        return out[:limit]

    def _in(self, item_at: datetime, start, end) -> bool:
        return (start is None or item_at >= start) and (end is None or item_at < end)

    async def sum_by_category(self, start, end) -> dict[str, Decimal]:
        sums: dict[str, Decimal] = {}
        for e in self.entries:
            if self._in(e.occurred_at, start, end):
                sums[e.category] = sums.get(e.category, Decimal(0)) + e.amount
        return sums

    async def sum_by_source(self, start, end) -> dict[str, Decimal]:
        sums: dict[str, Decimal] = {}
        for e in self.entries:
            if self._in(e.occurred_at, start, end):
                sums[e.source] = sums.get(e.source, Decimal(0)) + e.amount
        return sums

    async def sum_for_goal(self, goal_id) -> Decimal:
        return sum(
            (e.amount for e in self.entries if e.goal_id == goal_id), Decimal(0)
        )

    async def get_goal_budget(self, goal_id) -> Decimal | None:
        return self.goal_budgets.get(goal_id)

    async def add_attention(self, entry: AttentionEntry) -> AttentionEntry:
        if entry.id is None:
            entry.id = uuid.uuid4()
        if entry.created_at is None:
            entry.created_at = datetime.now(UTC)
        self.attention.append(entry)
        return entry

    async def sum_attention_minutes(self, start, end) -> Decimal:
        return sum(
            (
                a.minutes
                for a in self.attention
                if self._in(a.occurred_at, start, end)
            ),
            Decimal(0),
        )

    async def gate_minutes_by_day(self, days: int, now=None) -> dict[date, Decimal]:
        out: dict[date, Decimal] = {}
        for a in self.attention:
            if a.source == "gate":
                d = a.occurred_at.date()
                out[d] = out.get(d, Decimal(0)) + a.minutes
        return out

    async def count_outcomes(self, start, end) -> int | None:
        return self.outcomes

    async def add_subscription(self, sub: Subscription) -> Subscription:
        if sub.id is None:
            sub.id = uuid.uuid4()
        if sub.created_at is None:
            sub.created_at = datetime.now(UTC)
        self.subscriptions.append(sub)
        return sub

    async def get_subscription(self, sub_id):
        return next((s for s in self.subscriptions if s.id == sub_id), None)

    async def list_subscriptions(self):
        return list(reversed(self.subscriptions))


def make_client(fake: FakeLedgerRepo) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_ledger_repo] = lambda: fake
    return TestClient(app)


def entry_payload(**overrides) -> dict:
    payload = {
        "category": "tools",
        "amount": "10.0000",
        "goal_id": str(uuid.uuid4()),
        "source": "manual",
        "source_id": f"t-{uuid.uuid4().hex[:8]}",
    }
    payload.update(overrides)
    return payload


# ----------------------------------------------------------- 纯函数：注意力


def test_month_bounds_and_cross_month_boundary() -> None:
    start, end = month_bounds("2026-10")
    assert (start.year, start.month, start.day) == (2026, 10, 1)
    assert (end.year, end.month, end.day) == (2026, 11, 1)
    # 月末最后一秒属于本月，下月第一秒不属于
    assert start <= datetime(2026, 10, 31, 23, 59, 59, 999999, tzinfo=UTC) < end
    assert datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC) >= end


def test_month_bounds_rejects_garbage() -> None:
    for bad in ("2026-13", "2026/10", "2026-1", "october"):
        with pytest.raises(ValidationError):
            month_bounds(bad)


def test_min_per_outcome_null_without_outcomes() -> None:
    # 0 产出 → null（无产出期不能伪装成高效期）
    assert compute_min_per_outcome(Decimal("12.50"), None) is None
    assert compute_min_per_outcome(Decimal("12.50"), 0) is None
    assert compute_min_per_outcome(Decimal("12.50"), 3) == Decimal("4.17")


# ------------------------------------------------------------ 纯函数：摊销


def test_amortize_full_month() -> None:
    fee = Decimal("200.0000")
    out = amortize.amortize_calendar(fee, date(2026, 1, 5), date(2026, 10, 1), date(2026, 11, 1))
    assert out == Decimal("200.0000")


def test_amortize_mid_month_start() -> None:
    # 10 月 15 日生效 → 只摊 17/31 天
    fee = Decimal("200.0000")
    out = amortize.amortize_calendar(fee, date(2026, 10, 15), date(2026, 10, 1), date(2026, 11, 1))
    assert out == (fee * Decimal(17) / Decimal(31)).quantize(Decimal("0.0001"))
    assert out == Decimal("109.6774")


def test_amortize_cross_month_accumulates() -> None:
    fee = Decimal("200.0000")
    out = amortize.amortize_calendar(fee, date(2026, 6, 1), date(2026, 10, 1), date(2026, 12, 1))
    assert out == Decimal("400.0000")


def test_amortize_before_start_is_zero() -> None:
    out = amortize.amortize_calendar(
        Decimal("200"), date(2026, 12, 1), date(2026, 10, 1), date(2026, 11, 1)
    )
    assert out == Decimal("0.0000")


def test_amortize_usage_mode() -> None:
    assert amortize.amortize_usage(Decimal("200"), 50, 100) == Decimal("100.0000")
    # 超量使用封顶 100%
    assert amortize.amortize_usage(Decimal("200"), 150, 100) == Decimal("200.0000")
    # 无配额限制 → 整月费用
    assert amortize.amortize_usage(Decimal("200"), 9999, None) == Decimal("200.0000")


def test_quota_consumption_and_alert_threshold() -> None:
    assert amortize.quota_consumption(80, 100) == Decimal("0.8000")
    # 恰好 80% 不告警，超过才告警（严格大于）
    assert amortize.quota_alert(amortize.quota_consumption(80, 100)) is False
    assert amortize.quota_alert(amortize.quota_consumption(81, 100)) is True
    assert amortize.quota_alert(amortize.quota_consumption(50, None)) is False


# ------------------------------------------------------------ 纯函数：对账


def test_reconcile_half_percent_passes_two_percent_flags() -> None:
    rows = reconcile_sources({"manual": Decimal("100.5")}, {"manual": Decimal("100")})
    assert len(rows) == 1 and rows[0].flagged is False
    rows = reconcile_sources({"manual": Decimal("102")}, {"manual": Decimal("100")})
    assert rows[0].flagged is True
    assert is_consistent(
        reconcile_sources({"manual": Decimal("100.5")}, {"manual": Decimal("100")})
    )


def test_reconcile_unmatched_sides_flag() -> None:
    rows = reconcile_sources({"orphan": Decimal("1")}, {})
    assert rows[0].flagged and rows[0].source_amount is None
    rows = reconcile_sources({}, {"missing": Decimal("1")})
    assert rows[0].flagged and rows[0].ledger_amount is None


# ------------------------------------------------------- HTTP：成本归集 I01


def test_post_entry_created_then_idempotent_replay() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    body = entry_payload()
    r1 = client.post("/api/v1/ledger/entries", json=body, headers=AUTH)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/v1/ledger/entries", json=body, headers=AUTH)
    # 同一 (source, source_id) 重复上报不重复记账：200 且同一条目
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]
    assert len(fake.entries) == 1


def test_post_entry_requires_trace_and_nonzero() -> None:
    client = make_client(FakeLedgerRepo())
    no_trace = entry_payload()
    no_trace.pop("goal_id")
    assert client.post("/api/v1/ledger/entries", json=no_trace, headers=AUTH).status_code == 422
    zero = entry_payload(amount="0")
    assert client.post("/api/v1/ledger/entries", json=zero, headers=AUTH).status_code == 422


def test_negative_amount_is_correction_not_error() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    goal = uuid.uuid4()
    client.post(
        "/api/v1/ledger/entries", json=entry_payload(amount="10", goal_id=str(goal)), headers=AUTH
    )
    r = client.post(
        "/api/v1/ledger/entries",
        json=entry_payload(amount="-5", goal_id=str(goal), source_id="revert-1"),
        headers=AUTH,
    )
    assert r.status_code == 201
    assert Decimal(r.json()["amount"]) == Decimal("-5")
    # 冲正后汇总净值 5
    s = client.get("/api/v1/ledger/budget-status", params={"goal_id": goal}, headers=AUTH)
    assert Decimal(s.json()["spent"]) == Decimal("5")


def test_list_entries_filters_by_goal_and_period() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    goal = uuid.uuid4()
    client.post(
        "/api/v1/ledger/entries",
        json=entry_payload(goal_id=str(goal), source_id="a", occurred_at="2026-09-15T00:00:00Z"),
        headers=AUTH,
    )
    client.post(
        "/api/v1/ledger/entries",
        json=entry_payload(goal_id=str(goal), source_id="b", occurred_at="2026-10-15T00:00:00Z"),
        headers=AUTH,
    )
    r = client.get(
        "/api/v1/ledger/entries", params={"goal_id": goal, "period": "2026-10"}, headers=AUTH
    )
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["items"][0]["source_id"] == "b"


def test_summary_has_six_categories_and_null_attention() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    client.post(
        "/api/v1/ledger/entries",
        json=entry_payload(
            category="tools", occurred_at="2026-10-05T00:00:00Z"
        ),
        headers=AUTH,
    )
    r = client.get("/api/v1/ledger/summary", params={"period": "2026-10"}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert set(body["categories"]) == {
        "subscription", "tools", "device", "rework", "qc", "labor"
    }
    assert body["categories"]["tools"] == "10.0000"
    assert body["categories"]["rework"] == "0.0000"  # 缺失分项同屏为 0
    assert body["attention"]["outcomes"] is None  # W02 未落地 → null
    assert body["attention"]["min_per_outcome"] is None


def test_attention_with_zero_outcomes_is_null_not_zero() -> None:
    fake = FakeLedgerRepo()
    fake.outcomes = 0
    client = make_client(fake)
    r = client.get("/api/v1/ledger/attention", params={"period": "2026-10"}, headers=AUTH)
    body = r.json()
    assert body["outcomes"] == 0
    assert body["min_per_outcome"] is None


def test_attention_computes_min_per_outcome() -> None:
    fake = FakeLedgerRepo()
    fake.outcomes = 3
    fake.attention.append(
        AttentionEntry(
            id=uuid.uuid4(),
            occurred_at=datetime(2026, 10, 2, tzinfo=UTC),
            minutes=Decimal("12.50"),
            source="gate",
            source_id="x",
            created_at=datetime(2026, 10, 2, tzinfo=UTC),
        )
    )
    client = make_client(fake)
    r = client.get("/api/v1/ledger/attention", params={"period": "2026-10"}, headers=AUTH)
    body = r.json()
    assert body["minutes"] == "12.50"
    assert body["min_per_outcome"] == "4.17"


# ------------------------------------------------------------ 预算状态（契约）


def test_budget_status_frozen_when_overspent() -> None:
    fake = FakeLedgerRepo()
    goal = uuid.uuid4()
    fake.goal_budgets[goal] = Decimal("100")
    client = make_client(fake)
    client.post(
        "/api/v1/ledger/entries", json=entry_payload(amount="150", goal_id=str(goal)), headers=AUTH
    )
    r = client.get("/api/v1/ledger/budget-status", params={"goal_id": goal}, headers=AUTH)
    body = r.json()
    assert Decimal(body["budget"]) == Decimal("100")
    assert Decimal(body["spent"]) == Decimal("150")
    assert Decimal(body["remaining"]) == Decimal("-50")
    assert body["frozen"] is True


def test_budget_status_null_budget_when_goal_table_missing() -> None:
    fake = FakeLedgerRepo()  # goal_budgets 为空 → W02 未落地
    client = make_client(fake)
    r = client.get(
        "/api/v1/ledger/budget-status", params={"goal_id": uuid.uuid4()}, headers=AUTH
    )
    body = r.json()
    assert body["budget"] is None and body["frozen"] is False


# ------------------------------------------------------- HTTP：订阅 I05


def test_subscription_lifecycle_with_quota_alert() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    r = client.post(
        "/api/v1/ledger/subscriptions",
        json={
            "name": "claude-pro",
            "monthly_fee": "200.0000",
            "quota_limit": 100,
            "started_on": "2026-01-05",
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    assert r.json()["amortized_current_period"] == "200.0000"

    # 用量 85 / 100 → 消耗率 0.85 > 0.80 → 告警
    r = client.patch(
        f"/api/v1/ledger/subscriptions/{sub_id}", json={"quota_used": 85}, headers=AUTH
    )
    assert r.status_code == 200
    assert r.json()["quota_consumption"] == "0.8500"
    assert r.json()["quota_alert"] is True

    items = client.get("/api/v1/ledger/subscriptions", headers=AUTH).json()["items"]
    assert items[0]["name"] == "claude-pro"
    assert items[0]["amortized_current_period"] == "200.0000"


def test_subscription_usage_mode_amortization() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    r = client.post(
        "/api/v1/ledger/subscriptions",
        json={
            "name": "api-usage",
            "monthly_fee": "200.0000",
            "quota_limit": 100,
            "amortize_mode": "usage",
            "started_on": "2026-01-01",
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    assert r.json()["amortized_current_period"] == "0.0000"  # 尚无用量
    # 用量 50/100 → 按实际用量摊 → 100
    r = client.patch(
        f"/api/v1/ledger/subscriptions/{sub_id}", json={"quota_used": 50}, headers=AUTH
    )
    assert r.json()["amortized_current_period"] == "100.0000"


# ------------------------------------------------------- HTTP：对账导出 I06


def _seed_ledger_entry(fake: FakeLedgerRepo, amount: str, source: str, source_id: str) -> None:
    fake.entries.append(
        LedgerEntry(
            id=uuid.uuid4(),
            occurred_at=datetime(2026, 10, 5, tzinfo=UTC),
            category="subscription" if source == "subscription" else "tools",
            amount=Decimal(amount),
            currency="CNY",
            goal_id=uuid.uuid4(),
            source=source,
            source_id=source_id,
            created_at=datetime(2026, 10, 5, tzinfo=UTC),
        )
    )


def test_reconcile_flags_two_percent_gap_and_passes_half_percent() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    # 订阅摊销应有 200；登记一条 204 的订阅账（+2%）→ 标红
    client.post(
        "/api/v1/ledger/subscriptions",
        json={"name": "sub", "monthly_fee": "200.0000", "started_on": "2026-01-01"},
        headers=AUTH,
    )
    _seed_ledger_entry(fake, "204", "subscription", "sub-amort-oct")
    r = client.get(
        "/api/v1/ledger/reconcile", params={"period": "2026-10", "dry_run": True}, headers=AUTH
    )
    assert r.status_code == 200
    body = r.json()
    assert body["consistent"] is False
    row = next(x for x in body["rows"] if x["source"] == "subscription")
    assert row["flagged"] is True
    assert Decimal(row["diff"]) == Decimal("4.0000")

    # 0.5% 差异（200.5 vs 200）→ 通过
    fake.entries = []
    _seed_ledger_entry(fake, "200.5", "subscription", "sub-amort-oct2")
    body = client.get(
        "/api/v1/ledger/reconcile", params={"period": "2026-10"}, headers=AUTH
    ).json()
    row = next(x for x in body["rows"] if x["source"] == "subscription")
    assert row["flagged"] is False
    assert body["consistent"] is True


def test_export_csv_has_bom_and_rows() -> None:
    fake = FakeLedgerRepo()
    client = make_client(fake)
    client.post(
        "/api/v1/ledger/entries",
        json=entry_payload(amount="12.5000", memo="十月账", occurred_at="2026-10-05T00:00:00Z"),
        headers=AUTH,
    )
    r = client.get("/api/v1/ledger/export.csv", params={"period": "2026-10"}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("\ufeff")  # BOM：Excel 中文不乱码
    rows = list(csv.reader(io.StringIO(r.text.lstrip("\ufeff"))))
    assert rows[0][0] == "occurred_at"
    assert rows[1][2] == "12.5000"
    assert rows[1][8] == "十月账"


def test_unauthorized_requests_rejected() -> None:
    client = make_client(FakeLedgerRepo())
    assert client.post("/api/v1/ledger/entries", json=entry_payload()).status_code == 401
    assert client.get("/api/v1/ledger/summary").status_code == 401


# ------------------------------------------------------ 服务层直测（跨月窗口）


@pytest.mark.asyncio
async def test_service_summary_uses_month_window() -> None:
    fake = FakeLedgerRepo()
    fake.entries.append(
        LedgerEntry(
            id=uuid.uuid4(),
            occurred_at=datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC),
            category="device",
            amount=Decimal("3"),
            goal_id=uuid.uuid4(),
            source="device_event",
            source_id="d1",
            created_at=datetime.now(UTC),
        )
    )
    fake.entries.append(
        LedgerEntry(
            id=uuid.uuid4(),
            occurred_at=datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC),
            category="device",
            amount=Decimal("7"),
            goal_id=uuid.uuid4(),
            source="device_event",
            source_id="d2",
            created_at=datetime.now(UTC),
        )
    )
    out = await service.summary(fake, "2026-10")
    assert out.categories["device"] == Decimal("3")  # 11 月的那条不算 10 月


def test_record_gate_minutes_writes_attention() -> None:
    import asyncio

    fake = FakeLedgerRepo()
    asyncio.run(
        service.record_gate_minutes(
            fake, source_id="approval:x", minutes=Decimal("5"), now=datetime.now(UTC)
        )
    )
    assert fake.attention[0].source == "gate"
    assert fake.attention[0].minutes == Decimal("5")


def test_reconcile_threshold_boundary_one_percent() -> None:
    # 差异率 = |diff| / max(|ledger|, |expected|)：恰 1% 不标红，超过才标
    rows = reconcile_sources({"m": Decimal("101.0101")}, {"m": Decimal("100")})
    assert rows[0].diff_pct == Decimal("0.0100")
    assert rows[0].flagged is False  # 恰好 1% 不算（严格大于）
    rows = reconcile_sources({"m": Decimal("102.03")}, {"m": Decimal("100")})
    assert rows[0].flagged is True


def test_day_bounds_helper() -> None:
    from app.modules.ledger.attention import day_bounds

    s, e = day_bounds(date(2026, 10, 18))
    assert (e - s) == timedelta(days=1)
