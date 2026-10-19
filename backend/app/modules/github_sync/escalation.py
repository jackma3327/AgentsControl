"""熔断转人工：把 GitHub 的 CI 熔断接进 W07 的闸门（L1）。

单独一个文件的原因：
- github_sync 只依赖"能创建 L1 审批"这一件事，不关心闸门内部实现；
- 若 W07 改接口，只改这里，不污染事件处理逻辑。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.approvals.repository import ApprovalRepository
from app.modules.approvals.schemas import ApprovalCreate
from app.modules.approvals.service import create_approval

from .models import GitHubPull

logger = logging.getLogger("afc.github.escalation")


async def create_ci_escalation(session: AsyncSession, pull: GitHubPull, reason: str) -> None:
    """为熔断的 PR 建一条 L1 审批。

    幂等由调用方保证（`pull.escalated_at` 为空才调用）；这里不去重，
    因为"恢复后再次熔断"应当产生新的一条，而不是被历史记录吞掉。
    """
    repo = ApprovalRepository(session)
    payload = {
        "source": "github_sync",
        "pr_number": pull.number,
        "pr_url": pull.url,
        "ci_failed_count": int(pull.ci_failed_count),
        "reason": reason,
    }
    approval, deferred = await create_approval(
        repo,
        ApprovalCreate(
            level="L1",
            title=f"PR #{pull.number} CI 熔断，需人工判断：{pull.title[:120]}",
            payload=payload,
        ),
    )
    logger.warning(
        "ci circuit breaker opened",
        extra={
            "pr": pull.number,
            "approval_id": str(approval.id),
            "deferred": deferred,
            "failed_count": pull.ci_failed_count,
        },
    )


__all__ = ["create_ci_escalation"]
