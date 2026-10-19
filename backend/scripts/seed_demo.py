#!/usr/bin/env python3
"""灌入一套可反复执行的演示数据（`[demo]` 前缀，幂等）。

用法（在 backend/ 目录或容器内执行）：

    python scripts/seed_demo.py                 # 只补缺失的演示行
    python scripts/seed_demo.py --reset         # 先清掉旧演示行，再重建
    python scripts/seed_demo.py --owner owner@example.com

约定（与 S4 的「示例」标签共用）：
- goals / intake / tasks / approvals 的标题或正文以 `[demo]` 开头；
- devices 的 node_id 以 `demo-` 开头；
- ledger 的 source 固定 `demo_seed`。

幂等：每行用固定 UUID（uuid5）+ 唯一键判重，重复执行不翻倍；
`--reset` 只删上述标记的行，不碰真实数据。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# 与 create_user.py 同规则：允许在 backend/ 下直接 `python scripts/seed_demo.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import dispose_engine, get_sessionmaker  # noqa: E402
from app.modules.approvals.models import Approval  # noqa: E402
from app.modules.devices.models import Device  # noqa: E402
from app.modules.goals.models import Goal  # noqa: E402
from app.modules.identity.models import User  # noqa: E402
from app.modules.intake.models import IntakeItem  # noqa: E402
from app.modules.ledger.models import LedgerEntry  # noqa: E402
from app.modules.tasks.models import Task  # noqa: E402

DEMO_PREFIX = "[demo]"
DEMO_SOURCE = "demo_seed"
DEMO_NODE_PREFIX = "demo-"


def demo_id(key: str) -> uuid.UUID:
    """稳定 UUID：同一 key 永远得到同一行，保证重复执行不翻倍。"""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"agentscontrol-demo/{key}")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 数据定义
# ---------------------------------------------------------------------------
def _goals(owner: str, now: datetime) -> list[dict]:
    created = now - timedelta(days=7)
    return [
        {
            "id": str(demo_id("goal-money")),
            "code": "O-M-20261019-901",
            "owner_subject": owner,
            "track": "money",
            "template": "",
            "title": f"{DEMO_PREFIX} 咨询副业月入 2 万",
            "acceptance": "连续两个月净收入 ≥ ¥20,000，且每周投入 ≤ 15 小时",
            "horizon": "90 天",
            "horizon_at": now + timedelta(days=83),
            "budget_money": Decimal("8000.00"),
            "attention_minutes_per_day": 45,
            "north_star": "可复购的诊断型咨询产品",
            "guardrails": {"max_weekly_hours": 15, "stop_loss_weeks": 3},
            "deliverables": {"first": "3 个付费客户", "second": "标准化诊断模板"},
            "status": "active",
            "created_at": created,
            "updated_at": created,
        },
        {
            "id": str(demo_id("goal-growth")),
            "code": "O-E-20261019-902",
            "owner_subject": owner,
            "track": "growth",
            "template": "",
            "title": f"{DEMO_PREFIX} 掌握 Agent 编排与评测",
            "acceptance": "独立搭出一条多 Agent 流水线，并通过 10 个固定评测用例",
            "horizon": "T+30",
            "horizon_at": now + timedelta(days=30),
            "budget_hours": Decimal("120.00"),
            "attention_minutes_per_day": 40,
            "north_star": "能自己搭、也能评",
            "guardrails": {"no_new_framework_before_baseline": True},
            "deliverables": {"repo": "agent-pipeline-demo"},
            "status": "active",
            "created_at": created,
            "updated_at": created,
        },
    ]


def _intake(owner: str, now: datetime) -> list[dict]:
    texts = [
        f"{DEMO_PREFIX} 想做一个自动周报工具，每周五自动汇总",
        f"{DEMO_PREFIX} 把家里的旧 Mac mini 接进来当常驻 Agent",
        f"{DEMO_PREFIX} 用三个月把咨询副业做到月入 2 万",
        f"{DEMO_PREFIX} 学一下 Rust 的 async 生态",
    ]
    return [
        {
            "id": str(demo_id("intake-1")),
            "owner_subject": owner,
            "text": texts[0],
            "text_hash": _hash(texts[0]),
            "source": "web",
            "status": "raw",
            "created_at": now - timedelta(hours=3),
        },
        {
            "id": str(demo_id("intake-2")),
            "owner_subject": owner,
            "text": texts[1],
            "text_hash": _hash(texts[1]),
            "source": "web",
            "status": "raw",
            "created_at": now - timedelta(hours=20),
        },
        {
            "id": str(demo_id("intake-3")),
            "owner_subject": owner,
            "text": texts[2],
            "text_hash": _hash(texts[2]),
            "source": "web",
            "status": "structured",
            "created_at": now - timedelta(days=6),
            "structured_at": now - timedelta(days=6) + timedelta(minutes=2),
            "structured": {
                "acceptance": "连续两个月净收入 ≥ ¥20,000",
                "horizon": "90 天",
                "budget": "¥8,000",
                "suggest_interest": False,
            },
            "structure_model": "demo-seed",
            "structure_cost_usd": Decimal("0.001200"),
        },
        {
            "id": str(demo_id("intake-4")),
            "owner_subject": owner,
            "text": texts[3],
            "text_hash": _hash(texts[3]),
            "source": "web",
            "status": "archived_interest",
            "created_at": now - timedelta(days=12),
            "archived_at": now - timedelta(days=5),
            "structured": {
                "acceptance": None,
                "horizon": None,
                "budget": None,
                "suggest_interest": True,
            },
        },
    ]


def _tasks(now: datetime) -> list[dict]:
    return [
        {
            "id": demo_id("task-queued"),
            "goal_id": demo_id("goal-money"),
            "title": f"{DEMO_PREFIX} 整理本周咨询线索并去重",
            "payload": {"input": "inbox-export.csv"},
            "requires": {"browser": True},
            "priority": 3,
            "state": "queued",
            "queued_at": now - timedelta(hours=4),
            "created_at": now - timedelta(hours=4),
            "updated_at": now - timedelta(hours=4),
        },
        {
            "id": demo_id("task-running"),
            "goal_id": demo_id("goal-money"),
            "title": f"{DEMO_PREFIX} 生成周报初稿",
            "payload": {"template": "weekly-v2"},
            "requires": {"orca": True, "always_on": True},
            "priority": 2,
            "state": "running",
            "device_id": "demo-mac-mini",
            "lease_token": demo_id("lease-running"),
            "leased_until": now + timedelta(minutes=5),
            "heartbeat_at": now - timedelta(seconds=20),
            "started_at": now - timedelta(minutes=12),
            "budget_cap": Decimal("30.0000"),
            "cost_spent": Decimal("6.4000"),
            "queued_at": now - timedelta(hours=2),
            "created_at": now - timedelta(hours=2),
            "updated_at": now - timedelta(seconds=20),
        },
        {
            "id": demo_id("task-done"),
            "goal_id": demo_id("goal-money"),
            "title": f"{DEMO_PREFIX} 抓取 10 个竞品定价页",
            "payload": {"urls": 10},
            "requires": {"browser": True, "always_on": True},
            "priority": 4,
            "state": "done",
            "device_id": "demo-vps",
            "started_at": now - timedelta(hours=8),
            "finished_at": now - timedelta(hours=7, minutes=20),
            "budget_cap": Decimal("30.0000"),
            "cost_spent": Decimal("12.5000"),
            "result": {"pages": 10, "changed": 3},
            "queued_at": now - timedelta(hours=9),
            "created_at": now - timedelta(hours=9),
            "updated_at": now - timedelta(hours=7, minutes=20),
        },
        {
            "id": demo_id("task-failed"),
            "goal_id": demo_id("goal-growth"),
            "title": f"{DEMO_PREFIX} 修正周报中的数据口径",
            "payload": {"issue": "revenue_double_count"},
            "requires": {"orca": True},
            "priority": 5,
            "state": "failed",
            "device_id": "demo-mac-mini",
            "last_error": "页面结构变更：选择器 .price 未命中",
            "retry_count": 1,
            "max_retries": 3,
            "next_retry_at": now + timedelta(minutes=15),
            "queued_at": now - timedelta(hours=5),
            "started_at": now - timedelta(hours=4, minutes=30),
            "finished_at": now - timedelta(hours=4, minutes=10),
            "created_at": now - timedelta(hours=5),
            "updated_at": now - timedelta(hours=4, minutes=10),
        },
        {
            "id": demo_id("task-held"),
            "goal_id": demo_id("goal-growth"),
            "title": f"{DEMO_PREFIX} 评估 Orca 会话隔离方案",
            "payload": {"options": ["worktree", "container"]},
            "requires": {"orca": True},
            "priority": 6,
            "state": "held",
            "hold_reason": "预算剩余 12%，低于 20% 保留线",
            "queued_at": now - timedelta(hours=1),
            "created_at": now - timedelta(hours=1),
            "updated_at": now - timedelta(hours=1),
        },
    ]


def _approvals(now: datetime) -> list[dict]:
    return [
        {
            "id": demo_id("approval-l0-budget"),
            "level": "L0",
            "title": f"{DEMO_PREFIX} 是否为「咨询副业」追加 ¥3,000 投放预算",
            "payload": {"amount": 3000, "reason": "两条线索转化率高于基线"},
            "goal_id": demo_id("goal-money"),
            "status": "pending",
            "visible_at": now - timedelta(minutes=40),
            "created_at": now - timedelta(minutes=40),
        },
        {
            "id": demo_id("approval-l0-freeze"),
            "level": "L0",
            "title": f"{DEMO_PREFIX} 是否冻结「掌握 Agent 编排与评测」",
            "payload": {"reason": "连续 7 天无产出"},
            "goal_id": demo_id("goal-growth"),
            "status": "pending",
            "visible_at": now - timedelta(minutes=25),
            "created_at": now - timedelta(minutes=25),
        },
        {
            "id": demo_id("approval-l1-template"),
            "level": "L1",
            "title": f"{DEMO_PREFIX} 周报模板改用新版结构",
            "payload": {"template": "weekly-v2", "rollback": "weekly-v1"},
            "task_id": demo_id("task-running"),
            "goal_id": demo_id("goal-money"),
            "status": "pending",
            "visible_at": now - timedelta(minutes=15),
            "created_at": now - timedelta(minutes=15),
        },
        {
            "id": demo_id("approval-l1-device"),
            "level": "L1",
            "title": f"{DEMO_PREFIX} demo-mac-mini 连续丢 3 次心跳，是否转人工检查",
            "payload": {"node_id": "demo-mac-mini", "missed": 3},
            "status": "pending",
            "visible_at": now - timedelta(minutes=5),
            "created_at": now - timedelta(minutes=5),
        },
    ]


def _ledger(now: datetime) -> list[dict]:
    rows = [
        ("subscription", "199.00", "goal-money", None, "Claude 订阅摊销"),
        ("subscription", "99.00", "goal-growth", None, "LiteLLM 网关订阅"),
        ("tools", "49.00", "goal-money", None, "域名与邮件工具"),
        ("tools", "129.00", "goal-growth", None, "评测题库工具"),
        ("device", "25.50", "goal-money", demo_id("task-done"), "VPS 当月摊销"),
        ("device", "18.00", "goal-growth", None, "Mac mini 电费摊销"),
        ("rework", "88.00", "goal-money", demo_id("task-failed"), "选择器失效导致返工"),
        ("qc", "32.00", "goal-money", demo_id("task-done"), "抓取结果抽检"),
        ("labor", "600.00", "goal-money", None, "外包校对 4 小时"),
        ("labor", "300.00", "goal-growth", None, "方案评审 2 小时"),
    ]
    return [
        {
            "id": demo_id(f"ledger-{index:02d}"),
            # 全部落在最近 100 分钟内：账本页默认看当月，避免月初执行时数据落到上月
            "occurred_at": now - timedelta(minutes=index * 10),
            "category": category,
            "amount": Decimal(amount),
            "currency": "CNY",
            "goal_id": demo_id(goal_key),
            "task_id": task_id,
            "memo": memo,
            "source": DEMO_SOURCE,
            "source_id": f"demo-ledger-{index:02d}",
            "created_at": now - timedelta(minutes=index * 10),
        }
        for index, (category, amount, goal_key, task_id, memo) in enumerate(rows, start=1)
    ]


def _devices(now: datetime) -> list[dict]:
    return [
        {
            "id": demo_id("device-mac-mini"),
            "node_id": "demo-mac-mini",
            "fingerprint": "demo-fingerprint-mac-mini-0001",
            "token_hash": None,
            "trust": "managed",
            "purpose": "远程 Mac mini（常驻 Orca）",
            "squad": "core",
            "tolerance": 300,
            "caps": ["gui", "orca", "browser", "always_on"],
            "status": "online",
            "agent_version": "0.1.0",
            "last_seen_at": now - timedelta(seconds=20),
            "missed_heartbeats": 0,
            "last_event_seq": 42,
            "created_at": now - timedelta(days=30),
        },
        {
            "id": demo_id("device-macbook"),
            "node_id": "demo-macbook",
            "fingerprint": "demo-fingerprint-macbook-0002",
            "token_hash": None,
            "trust": "managed",
            "purpose": "笔记本（只派轻任务）",
            "squad": "core",
            "tolerance": 900,
            "caps": ["gui", "orca", "browser"],
            "status": "suspect",
            "agent_version": "0.1.0",
            "last_seen_at": now - timedelta(minutes=10),
            "missed_heartbeats": 1,
            "last_event_seq": 17,
            "created_at": now - timedelta(days=21),
        },
        {
            "id": demo_id("device-vps"),
            "node_id": "demo-vps",
            "fingerprint": "demo-fingerprint-vps-0003",
            "token_hash": None,
            "trust": "managed",
            "purpose": "云服务器（抓取/计算）",
            "squad": "infra",
            "tolerance": 300,
            "caps": ["browser", "container", "always_on"],
            "status": "online",
            "agent_version": "0.1.0",
            "last_seen_at": now - timedelta(seconds=45),
            "missed_heartbeats": 0,
            "last_event_seq": 88,
            "created_at": now - timedelta(days=60),
        },
        {
            "id": demo_id("device-thirdparty"),
            "node_id": "demo-thirdparty",
            "fingerprint": "demo-fingerprint-thirdparty-0004",
            "token_hash": None,
            "trust": "third_party",
            "purpose": "第三方平台（只读上报）",
            "squad": None,
            "tolerance": 3600,
            "caps": ["api"],
            "status": "offline",
            "agent_version": None,
            "last_seen_at": now - timedelta(hours=1),
            "missed_heartbeats": 0,
            "last_event_seq": 5,
            "created_at": now - timedelta(days=14),
        },
    ]


# ---------------------------------------------------------------------------
# 清理与写入
# ---------------------------------------------------------------------------
async def _reset(session: AsyncSession) -> None:
    """只删带演示标记的行；顺序遵守「先子后父」。"""
    await session.execute(delete(LedgerEntry).where(LedgerEntry.source == DEMO_SOURCE))
    await session.execute(delete(Approval).where(Approval.title.like(f"{DEMO_PREFIX}%")))
    await session.execute(delete(Task).where(Task.title.like(f"{DEMO_PREFIX}%")))
    await session.execute(delete(IntakeItem).where(IntakeItem.text.like(f"{DEMO_PREFIX}%")))
    await session.execute(delete(Goal).where(Goal.title.like(f"{DEMO_PREFIX}%")))
    await session.execute(delete(Device).where(Device.node_id.like(f"{DEMO_NODE_PREFIX}%")))


async def _goal_exists(session: AsyncSession, spec: dict) -> bool:
    if await session.get(Goal, spec["id"]) is not None:
        return True
    found = await session.scalar(select(Goal.id).where(Goal.code == spec["code"]))
    return found is not None


async def _device_exists(session: AsyncSession, spec: dict) -> bool:
    if await session.get(Device, spec["id"]) is not None:
        return True
    found = await session.scalar(select(Device.id).where(Device.node_id == spec["node_id"]))
    return found is not None


async def _ledger_exists(session: AsyncSession, spec: dict) -> bool:
    if await session.get(LedgerEntry, spec["id"]) is not None:
        return True
    found = await session.scalar(
        select(LedgerEntry.id).where(
            LedgerEntry.source == spec["source"],
            LedgerEntry.source_id == spec["source_id"],
        )
    )
    return found is not None


async def _seed(session: AsyncSession, owner: str, *, reset: bool) -> dict[str, int]:
    if reset:
        await _reset(session)

    now = datetime.now(UTC)
    inserted = 0
    skipped = 0

    async def put(spec: dict, model: type, exists: bool) -> None:
        nonlocal inserted, skipped
        if exists:
            skipped += 1
            return
        session.add(model(**spec))
        inserted += 1

    for spec in _goals(owner, now):
        await put(spec, Goal, await _goal_exists(session, spec))
    await session.flush()

    for spec in _intake(owner, now):
        await put(spec, IntakeItem, await session.get(IntakeItem, spec["id"]) is not None)
    await session.flush()

    for spec in _tasks(now):
        await put(spec, Task, await session.get(Task, spec["id"]) is not None)
    await session.flush()

    for spec in _approvals(now):
        await put(spec, Approval, await session.get(Approval, spec["id"]) is not None)
    await session.flush()

    for spec in _ledger(now):
        await put(spec, LedgerEntry, await _ledger_exists(session, spec))
    await session.flush()

    for spec in _devices(now):
        await put(spec, Device, await _device_exists(session, spec))
    await session.flush()

    return {"inserted": inserted, "skipped": skipped}


async def _counts(session: AsyncSession) -> dict[str, int]:
    async def count(stmt) -> int:
        return int(await session.scalar(stmt) or 0)

    return {
        "goals": await count(
            select(func.count(Goal.id)).where(Goal.title.like(f"{DEMO_PREFIX}%"))
        ),
        "intake": await count(
            select(func.count(IntakeItem.id)).where(IntakeItem.text.like(f"{DEMO_PREFIX}%"))
        ),
        "tasks": await count(
            select(func.count(Task.id)).where(Task.title.like(f"{DEMO_PREFIX}%"))
        ),
        "approvals": await count(
            select(func.count(Approval.id)).where(Approval.title.like(f"{DEMO_PREFIX}%"))
        ),
        "ledger": await count(
            select(func.count(LedgerEntry.id)).where(LedgerEntry.source == DEMO_SOURCE)
        ),
        "devices": await count(
            select(func.count(Device.id)).where(Device.node_id.like(f"{DEMO_NODE_PREFIX}%"))
        ),
    }


async def _run(args: argparse.Namespace) -> int:
    async with get_sessionmaker()() as session:
        async with session.begin():
            if await session.get(User, args.owner) is None:
                print(
                    f"[seed] 警告：用户 {args.owner} 不存在，先执行 "
                    f"make create-owner EMAIL={args.owner} PASSWORD='...'",
                    file=sys.stderr,
                )
            stats = await _seed(session, args.owner, reset=args.reset)
            counts = await _counts(session)

    print(
        "goals={goals} intake={intake} tasks={tasks} approvals={approvals} "
        "ledger={ledger} devices={devices}".format(**counts)
    )
    print(f"inserted={stats['inserted']} skipped={stats['skipped']} owner={args.owner}")
    return 0


async def _main(args: argparse.Namespace) -> int:
    try:
        return await _run(args)
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="灌入 [demo] 演示数据（幂等）")
    parser.add_argument("--reset", action="store_true", help="先删除旧的 [demo] 演示数据")
    parser.add_argument(
        "--owner",
        default="owner@example.com",
        help="演示目标/收件箱归属的登录主体（默认 owner@example.com）",
    )
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
