"""github_sync HTTP 路由。

只有一条路是"外部系统打进来"的：`POST /webhook`。
它不要求 Owner 令牌，但**必须**签名校验通过；其余端点都要登录。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status

from app.core.config import settings
from app.core.db import get_db, get_sessionmaker
from app.core.deps import OwnerDep
from app.core.errors import AppError, NotFoundError, ValidationError
from app.modules.github_sync.escalation import create_ci_escalation
from app.modules.github_sync.models import GitHubEvent, GitHubPull
from app.modules.github_sync.repository import (
    GitHubRepository,
    SqlGitHubRepository,
    new_id,
)
from app.modules.github_sync.schemas import (
    EventOut,
    IssueOut,
    PullOut,
    ReplayOut,
    SyncResult,
    WebhookAck,
)
from app.modules.github_sync.service import (
    HttpIssueClient,
    IssueClient,
    process_event,
    sync_issues,
)
from app.modules.github_sync.verify import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    parse_delivery,
    require_signature,
)

logger = logging.getLogger("afc.github")

MODULE = "github_sync"
OWNER_WINDOW = "W09"
MAX_PAYLOAD_BYTES = 1_000_000  # 1 MB：超出说明不是正常 webhook

router = APIRouter(prefix="/github", tags=["github"])

Escalator = Callable[[GitHubPull, str], Awaitable[None]]


@router.get("/_status", summary="模块状态（骨架冒烟测试依赖）")
async def module_status() -> dict[str, str]:
    return {"module": MODULE, "owner_window": OWNER_WINDOW, "state": "implemented"}


async def get_github_repository(session=Depends(get_db)) -> GitHubRepository:
    return SqlGitHubRepository(session)


GitHubRepoDep = Annotated[GitHubRepository, Depends(get_github_repository)]


async def get_escalator(session=Depends(get_db)) -> Escalator:
    async def _escalate(pull: GitHubPull, reason: str) -> None:
        await create_ci_escalation(session, pull, reason)

    return _escalate


EscalatorDep = Annotated[Escalator, Depends(get_escalator)]


def get_issue_client() -> IssueClient:
    return HttpIssueClient(settings.github_token, settings.github_repo)


IssueClientDep = Annotated[IssueClient, Depends(get_issue_client)]


# --------------------------------------------------------------------- webhook
@router.post(
    "/webhook",
    response_model=WebhookAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="GitHub webhook 入口（HMAC 验签 + delivery 幂等）",
)
async def github_webhook(
    request: Request,
    response: Response,
    repo: GitHubRepoDep,
    escalate: EscalatorDep,
    background: BackgroundTasks,
    eager: bool = Query(default=False, description="同步处理，仅测试/本地用"),
) -> WebhookAck:
    body = await request.body()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise AppError("payload 过大", code="payload_too_large", status_code=413)

    require_signature(settings.github_webhook_secret, body, request.headers.get(SIGNATURE_HEADER))
    delivery_id, event_name = parse_delivery(
        request.headers.get(DELIVERY_HEADER), request.headers.get(EVENT_HEADER)
    )

    try:
        payload: dict[str, Any] = json.loads(body or b"{}")
    except json.JSONDecodeError as exc:
        raise ValidationError("payload 不是合法 JSON") from exc

    event = GitHubEvent(
        id=new_id(),
        delivery_id=delivery_id,
        event=event_name,
        action=payload.get("action"),
        payload=payload,
        status="received",
        attempts=0,
    )
    stored, created = await repo.insert_event(event)
    await repo.commit()

    if not created:
        response.status_code = status.HTTP_200_OK
        return WebhookAck(
            status="duplicate", delivery_id=delivery_id, event=event_name, event_id=stored.id
        )

    if eager:
        final = await process_event(repo, stored, escalate=escalate)
        await repo.commit()
        return WebhookAck(
            status=final, delivery_id=delivery_id, event=event_name, event_id=stored.id
        )

    background.add_task(_process_in_background, stored.id)
    return WebhookAck(
        status="accepted", delivery_id=delivery_id, event=event_name, event_id=stored.id
    )


async def _process_in_background(event_id: str) -> None:
    """独立 session：webhook 的请求 session 在响应后即关闭。"""
    try:
        async with get_sessionmaker()() as session:
            repo = SqlGitHubRepository(session)

            async def escalate(pull: GitHubPull, reason: str) -> None:
                await create_ci_escalation(session, pull, reason)

            event = await repo.get_event(event_id)
            await process_event(repo, event, escalate=escalate)
            await session.commit()
    except Exception:  # noqa: BLE001 - 后台任务必须自己兜底
        logger.exception("webhook background processing crashed", extra={"event_id": event_id})


# ------------------------------------------------------------------------- PR
@router.get("/pulls", response_model=list[PullOut], summary="PR 验收结论")
async def list_pulls(
    _: OwnerDep,
    repo: GitHubRepoDep,
    state_filter: str | None = Query(default=None, alias="state"),
    verdict: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[PullOut]:
    pulls = await repo.list_pulls(state=state_filter, verdict=verdict, limit=limit)
    return [PullOut.model_validate(p) for p in pulls]


@router.get("/pulls/{number}", response_model=PullOut, summary="单个 PR 的判定与连败次数")
async def get_pull(number: int, _: OwnerDep, repo: GitHubRepoDep) -> PullOut:
    pull = await repo.get_pull(number)
    if pull is None:
        raise NotFoundError("PR 尚未同步", details={"number": number})
    return PullOut.model_validate(pull)


# ---------------------------------------------------------------------- Issue
@router.get("/issues", response_model=list[IssueOut], summary="Issue 只读镜像")
async def list_issues(
    _: OwnerDep,
    repo: GitHubRepoDep,
    state_filter: str | None = Query(default=None, alias="state"),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[IssueOut]:
    issues = await repo.list_issues(state=state_filter, limit=limit)
    return [IssueOut.model_validate(i) for i in issues]


@router.post("/sync", response_model=SyncResult, summary="全量拉取 Issues（只读，幂等）")
async def sync(_: OwnerDep, repo: GitHubRepoDep, client: IssueClientDep) -> SyncResult:
    from datetime import UTC, datetime

    fetched, upserted, skipped = await sync_issues(repo, client)
    await repo.commit()
    return SyncResult(
        fetched=fetched, upserted=upserted, skipped=skipped, synced_at=datetime.now(UTC)
    )


# --------------------------------------------------------------------- events
@router.get("/events", response_model=list[EventOut], summary="最近的 webhook 事件（排障用）")
async def list_events(
    _: OwnerDep,
    repo: GitHubRepoDep,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[EventOut]:
    events = await repo.list_events(limit=limit, status=status_filter)
    return [EventOut.model_validate(e) for e in events]


@router.post(
    "/events/{event_id}/replay",
    response_model=ReplayOut,
    summary="重放失败事件（幂等：同一事件可重复重放）",
)
async def replay_event(
    event_id: str, _: OwnerDep, repo: GitHubRepoDep, escalate: EscalatorDep
) -> ReplayOut:
    event = await repo.get_event(event_id)
    final = await process_event(repo, event, escalate=escalate)
    await repo.commit()
    return ReplayOut(event_id=event_id, status=final, error=event.error)


__all__ = ["router"]
