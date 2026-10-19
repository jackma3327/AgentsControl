"""intake 业务服务层。

依赖注入：
- service 函数接受 `IntakeRepository`（Protocol），不直接依赖 Sql 实现；
- 这样在单测中可以传入 fake repository，不走真实 DB。
- service 不依赖 Request/Response，HTTP 在 router。
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta

from app.core.errors import ValidationError

from .litellm import LitellmClient
from .repository import IntakeRepository, to_out
from .schemas import (
    IntakeItemCreate,
    IntakeItemList,
    IntakeItemOut,
    StructureResult,
    ThreeQuestions,
)

# 业务常量（这里集中，但跨模块共享的应在 core/config）。
DEDUP_WINDOW = timedelta(minutes=5)
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def encode_cursor(created_at: datetime, item_id: str) -> str:
    raw = json.dumps(
        {"created_at": created_at.isoformat(), "id": item_id},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad).decode("utf-8")
        data = json.loads(raw)
        return datetime.fromisoformat(data["created_at"]), data["id"]
    except (binascii.Error, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValidationError("cursor 非法，请使用上一页返回的 next_cursor") from exc


async def capture(
    repo: IntakeRepository, owner: str, payload: IntakeItemCreate
) -> tuple[IntakeItemOut, bool]:
    """记录一条体感。返回 (out, created)。

    created=False 表示 5 分钟内同文本已存在，返回既有 id（不报错）。

    事务：写路径末尾显式 commit（S2/R04）。`get_db()` 不会替业务层提交，
    只 flush 的写入会随请求结束静默丢失（返回 201 但查不到）。
    """
    item, created = await repo.create_or_merge(owner, payload, dedup_window=DEDUP_WINDOW)
    await repo.commit()
    return to_out(item), created


async def get_one(repo: IntakeRepository, owner: str, item_id: str) -> IntakeItemOut:
    item = await repo.get(item_id, owner=owner)
    return to_out(item)


async def list_items(
    repo: IntakeRepository,
    owner: str,
    *,
    cursor: str | None,
    limit: int,
) -> IntakeItemList:
    page_size = max(1, min(limit, MAX_PAGE_SIZE))
    cursor_at: datetime | None = None
    cursor_id: str | None = None
    if cursor:
        cursor_at, cursor_id = decode_cursor(cursor)

    items, has_more = await repo.list_by_owner(
        owner,
        cursor_created_at=cursor_at,
        cursor_id=cursor_id,
        limit=page_size,
    )
    next_cursor = (
        encode_cursor(items[-1].created_at, items[-1].id)
        if has_more and items
        else None
    )
    return IntakeItemList(items=[to_out(i) for i in items], next_cursor=next_cursor)


async def list_interests(
    repo: IntakeRepository, owner: str, *, limit: int = 100
) -> list[IntakeItemOut]:
    rows = await repo.list_interests(owner, limit=limit)
    return [to_out(i) for i in rows]


# ---------------------------------------------------------------------------
# W02-I02 · AI 三问结构化
# ---------------------------------------------------------------------------


async def structure(
    repo: IntakeRepository,
    litellm: LitellmClient,
    owner: str,
    item_id: str,
) -> StructureResult:
    """对一条 intake 跑三问，幂等：同 item 重复调返回同一结果。

    幂等实现：先查 item，若已 structured 则直接返回既有结果，不再调用 LLM。
    这样可以避免重复扣费、避免抖动期间被刷。
    """
    item = await repo.get(item_id, owner=owner)
    if item.structured is not None:
        # 已结构化：原样返回（保留 structured_at/model/cost）
        questions = ThreeQuestions(**item.structured)
        return StructureResult(
            item_id=item.id,
            structured=questions,
            model=item.structure_model or "unknown",
            cost_usd=float(item.structure_cost_usd or 0.0),
        )

    # 调 LLM。HttpLitellmClient 在任何上游异常时抛 UpstreamError，
    # **绝不**在这里 fallback 编造三问。
    questions, model_used, cost_usd = await litellm.structure(item.text)

    now = datetime.utcnow()
    saved = await repo.save_structure(
        item.id,
        structured=questions.model_dump(),
        model=model_used,
        cost_usd=cost_usd,
        at=now,
    )
    # Sql 实现的 save_structure 内部已 commit；service 层再显式提交一次，
    # 保证"提交权在 service"的契约不依赖 repository 的内部行为（S2/R04）。
    await repo.commit()
    return StructureResult(
        item_id=saved.id,
        structured=ThreeQuestions(**_to_plain(saved.structured)),
        model=saved.structure_model or model_used,
        cost_usd=float(saved.structure_cost_usd or cost_usd),
    )


def _to_plain(structured: dict | None) -> dict:
    """把 IntakeItem.structured 中的 datetime/枚举强制转 dict（防御性）。"""
    if structured is None:
        return {}
    if isinstance(structured, dict):
        return structured
    return dict(structured)


async def get_questions(
    repo: IntakeRepository, owner: str, item_id: str
) -> StructureResult:
    """GET 三问答案：未结构化返回 404，引导前端先调 POST /structure。"""
    item = await repo.get(item_id, owner=owner)
    if item.structured is None:
        from app.core.errors import NotFoundError

        raise NotFoundError("该 item 尚未结构化，请先 POST /intake/items/{id}/structure")
    return StructureResult(
        item_id=item.id,
        structured=ThreeQuestions(**_to_plain(item.structured)),
        model=item.structure_model or "unknown",
        cost_usd=float(item.structure_cost_usd or 0.0),
    )


__all__ = [
    "capture",
    "get_one",
    "list_items",
    "list_interests",
    "structure",
    "get_questions",
    "encode_cursor",
    "decode_cursor",
    "DEDUP_WINDOW",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
]
