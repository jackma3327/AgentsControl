"""goals 业务服务层。

依赖注入：service 函数接受 `GoalRepository` Protocol，便于单测注入 fake。
"""

from __future__ import annotations

from datetime import datetime

from app.core.errors import ConflictError

from .models import Goal
from .repository import GoalRepository, new_id, to_out
from .rules import assert_transition, build_code
from .schemas import (
    GoalCreate,
    GoalOut,
    GoalPatch,
    StopLossResult,
)

# ---------------------------------------------------------------------------
# W02-I03 · 目标卡：创建 / 读 / 改
# ---------------------------------------------------------------------------


async def create_goal(
    repo: GoalRepository,
    owner: str,
    payload: GoalCreate,
    *,
    now: datetime | None = None,
) -> GoalOut:
    """创建目标，含编号生成（带 retry）。"""
    now = now or datetime.utcnow()
    seq = await repo.next_seq_for(payload.track, now)
    code = build_code(payload.track, now, seq)
    goal = Goal(
        id=new_id(),
        code=code,
        owner_subject=owner,
        track=payload.track,
        template=payload.template,
        title=payload.title,
        acceptance=payload.acceptance,
        horizon=payload.horizon,
        budget_money=payload.budget_money,
        budget_hours=payload.budget_hours,
        attention_minutes_per_day=payload.attention_minutes_per_day,
        north_star=payload.north_star,
        guardrails=payload.guardrails,
        deliverables=payload.deliverables,
        status="draft",
        intake_item_id=payload.intake_item_id,
        created_at=now,
        updated_at=now,
    )
    try:
        saved = await repo.create(goal)
    except ConflictError:
        # 极小概率并发触发了相同编号；retry 一次（产线不能无限重试）
        seq2 = await repo.next_seq_for(payload.track, now)
        goal.code = build_code(payload.track, now, seq2)
        saved = await repo.create(goal)
    # 显式提交（S2/R04）：get_db() 不替业务层 commit，只 flush 会静默丢失
    await repo.commit()
    return to_out(saved)


async def get_goal(repo: GoalRepository, owner: str, goal_id: str) -> GoalOut:
    goal = await repo.get(goal_id, owner=owner)
    return to_out(goal)


async def list_goals(
    repo: GoalRepository,
    owner: str,
    *,
    statuses: tuple[str, ...] | None = None,
    limit: int = 100,
) -> list[GoalOut]:
    rows = await repo.list_by_owner(owner, statuses=statuses, limit=limit)
    return [to_out(g) for g in rows]


async def patch_goal(
    repo: GoalRepository,
    owner: str,
    goal_id: str,
    payload: GoalPatch,
    *,
    now: datetime | None = None,
) -> GoalOut:
    """补丁目标。若携带 status，校验状态机。"""
    now = now or datetime.utcnow()
    goal = await repo.get(goal_id, owner=owner)
    updates = payload.model_dump(exclude_unset=True)
    new_status = updates.pop("status", None)

    if new_status is not None and new_status != goal.status:
        assert_transition(goal.status, new_status)
        updates["status"] = new_status
        # 终态时间戳
        if new_status == "achieved":
            updates["achieved_at"] = now
        elif new_status == "frozen":
            updates["frozen_at"] = None  # 冻结时间戳在 W02-I04 止损判定时落
        # degraded 时间戳由 degrade / archive 路径单独设置
    elif new_status is not None and new_status == goal.status:
        # 状态相同：不做事，但仍允许更新字段
        pass

    if "guardrails" in updates and updates["guardrails"] is None:
        updates.pop("guardrails")
    if "deliverables" in updates and updates["deliverables"] is None:
        updates.pop("deliverables")

    if updates:
        saved = await repo.update(goal_id, updates, at=now)
        # 显式提交（S2/R04）：冻结/降级等状态迁移此前只 flush，PATCH 200 后丢失
        await repo.commit()
    else:
        saved = goal
    return to_out(saved)


# ---------------------------------------------------------------------------
# W02-I03 · 状态机（service 仅做风格化，规则在 rules.py）
# ---------------------------------------------------------------------------


async def transition(
    repo: GoalRepository,
    owner: str,
    goal_id: str,
    target: str,
    *,
    now: datetime | None = None,
) -> GoalOut:
    """显式状态迁移端点。"""
    now = now or datetime.utcnow()
    goal = await repo.get(goal_id, owner=owner)
    assert_transition(goal.status, target)
    updates: dict = {}
    if target == "achieved":
        updates["achieved_at"] = now
    elif target == "frozen":
        updates["frozen_at"] = now
    elif target == "degraded":
        updates["degraded_at"] = now
    saved = await repo.update(goal_id, {"status": target, **updates}, at=now)
    await repo.commit()
    return to_out(saved)


# ---------------------------------------------------------------------------
# W02-I04 · 止损判定（纯函数版 + 落库版）
# ---------------------------------------------------------------------------


async def check_stop_loss(
    repo: GoalRepository,
    owner: str,
    goal_id: str,
    *,
    now: datetime | None = None,
    money_history_p25: float | None = None,
) -> StopLossResult:
    """计算并落库止损触发时间。不在此处自动转状态——

    调用方拿到结果后自行决定是否调 PATCH 把 status 改为 frozen/degraded。
    这是为了让 summary 接口能"重新计算"而不会因为已经 frozen 而失真。
    """
    from .rules import (
        GrowthStopLossWindow,
        is_growth_stop_loss_triggered,
        is_money_stop_loss_triggered,
    )

    now = now or datetime.utcnow()
    goal = await repo.get(goal_id, owner=owner)

    triggered = False
    reason: str | None = None
    if goal.track == "growth":
        has_deliverable = bool(goal.deliverables)
        triggered = is_growth_stop_loss_triggered(
            active_at=goal.created_at, now=now, has_deliverable=has_deliverable
        )
        if triggered:
            reason = f"T+{GrowthStopLossWindow.days}d 无可复用产出物，建议降级为兴趣"
    else:
        history_p25 = money_history_p25 if money_history_p25 is not None else 0.0
        current_output = 0.0  # 实际产出由 W07 账本接口提供，未实现时按 0
        triggered = is_money_stop_loss_triggered(history_p25=history_p25, current_output=current_output)
        if triggered:
            reason = "当前产出低于历史 P25，建议冻结"

    if triggered and goal.stop_loss_triggered_at is None:
        await repo.update(goal_id, {"stop_loss_triggered_at": now}, at=now)
        await repo.commit()

    return StopLossResult(goal_id=goal.id, triggered=triggered, reason=reason)


async def degrade(
    repo: GoalRepository,
    owner: str,
    goal_id: str,
    *,
    now: datetime | None = None,
) -> GoalOut:
    """主动降级为兴趣（仅 active → degraded 允许）。"""
    now = now or datetime.utcnow()
    goal = await repo.get(goal_id, owner=owner)
    assert_transition(goal.status, "degraded")
    saved = await repo.update(
        goal_id,
        {"status": "degraded", "degraded_at": now},
        at=now,
    )
    await repo.commit()
    return to_out(saved)


# ---------------------------------------------------------------------------
# W02-I06 · 目标看板聚合接口
# ---------------------------------------------------------------------------


async def build_summary(repo: GoalRepository, owner: str):
    """单请求聚合：每张卡返回「预算已用 / 剩余天数 / 是否触发止损」。

    详细逻辑在 W02-I06 完善；此处先打通端点链路。
    """
    from .rules import GrowthStopLossWindow, is_growth_stop_loss_triggered
    from .schemas import GoalSummary, GoalSummaryItem

    goals = await repo.list_by_owner(owner, limit=100)
    now = datetime.utcnow()
    items: list[GoalSummaryItem] = []
    for g in goals:
        budget_used = await repo.budget_used_for(g.id)
        # TTL 字段：当前 MVP 用 horizon_at 或 created_at + 7d 作兜底
        days_remaining: int | None = None
        if g.horizon_at is not None:
            delta = (g.horizon_at - now).total_seconds() / 86400
            days_remaining = max(0, int(delta))

        triggered = bool(g.stop_loss_triggered_at)
        reason = None
        if g.track == "growth" and not triggered:
            triggered = is_growth_stop_loss_triggered(
                active_at=g.created_at,
                now=now,
                has_deliverable=bool(g.deliverables),
            )
            if triggered:
                reason = f"T+{GrowthStopLossWindow.days}d 无可复用产出物"

        items.append(
            GoalSummaryItem(
                id=g.id,
                code=g.code,
                title=g.title,
                track=g.track,  # type: ignore[arg-type]
                status=g.status,  # type: ignore[arg-type]
                budget_used=budget_used,  # unknown 时为 None
                budget_remaining=None,  # 待 W07 ledger 给总数后计算
                days_remaining=days_remaining,
                stop_loss_triggered=triggered,
                stop_loss_reason=reason,
            )
        )
    return GoalSummary(items=items, total=len(items))


__all__ = [
    "create_goal",
    "get_goal",
    "list_goals",
    "patch_goal",
    "transition",
    "check_stop_loss",
    "degrade",
    "build_summary",
]