"""github_sync 业务规则。

三条设计约束：
1. **verdict 是纯函数**：输入 merged / checks / 连败次数，输出 ok / rework / escalated，
   不查库、不看时间，才能把边界用例穷举。
2. **Issue 只读**：本模块不存在任何"往 GitHub 写"的代码路径（连 client 也只提供 GET）。
3. **处理失败不吞**：事件先落库，处理异常写 `failed` + error，可经 `/events/{id}/replay` 重放。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.errors import ConflictError, UpstreamError

from .models import GitHubEvent, GitHubIssue, GitHubPull
from .repository import GitHubRepository, new_id

logger = logging.getLogger("afc.github")

FAILURE_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}
SUCCESS_CONCLUSIONS = {"success"}
ESCALATE_AFTER = 3

# Closes #12 / fixes #3 / resolved #7（GitHub 官方支持的三种关键词）
_LINKED_RE = re.compile(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b")


# ------------------------------------------------------------------ 纯函数
def extract_linked_issues(body: str | None) -> list[int]:
    if not body:
        return []
    return sorted({int(m) for m in _LINKED_RE.findall(body)})


def should_escalate(ci_failed_count: int) -> bool:
    return ci_failed_count >= ESCALATE_AFTER


def compute_verdict(
    *, merged: bool, checks_green: bool | None, ci_failed_count: int, state: str
) -> str:
    """PR 的验收结论。

    - merged 且 checks 不为 False → ok（计入 CPO）
    - 未合并且连败 ≥3 → escalated（停止自动重试，转人工）
    - closed 未合并 → rework
    - 其余 → pending
    """
    if merged:
        return "ok" if checks_green is not False else "escalated"
    if should_escalate(ci_failed_count):
        return "escalated"
    if state == "closed":
        return "rework"
    return "pending"


def apply_check_conclusion(pull: GitHubPull, conclusion: str | None) -> None:
    """把一次 check 结果写进 PR 的计数与绿灯位（连续失败的语义）。"""
    if conclusion in SUCCESS_CONCLUSIONS:
        pull.checks_green = True
        pull.ci_failed_count = 0
        return
    if conclusion in FAILURE_CONCLUSIONS:
        pull.checks_green = False
        pull.ci_failed_count = int(pull.ci_failed_count) + 1
        return
    # neutral / skipped / None：不改变判定，避免"没跑检查"被当成通过或失败
    return


def _new_pull(number: int) -> GitHubPull:
    """显式给齐默认值：列默认值只在 flush 时生效，纯内存 fake 里会是 None。"""
    now = datetime.now(UTC)
    return GitHubPull(
        id=new_id(),
        number=number,
        title="",
        head_ref="",
        base_ref="main",
        state="open",
        merged=False,
        checks_green=None,
        ci_failed_count=0,
        verdict="pending",
        linked_issues=[],
        url=None,
        created_at=now,
        updated_at=now,
    )


def _new_issue(number: int) -> GitHubIssue:
    now = datetime.now(UTC)
    return GitHubIssue(
        id=new_id(),
        number=number,
        title="",
        state="open",
        labels=[],
        created_at=now,
        updated_at=now,
        synced_at=now,
    )


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------ 事件处理
Escalator = Callable[[GitHubPull, str], Awaitable[None]]


async def handle_event(
    repo: GitHubRepository, event: GitHubEvent, *, escalate: Escalator | None = None
) -> str:
    """分发事件。返回最终状态：processed / ignored / failed。"""
    payload: dict[str, Any] = event.payload or {}
    handler = {
        "pull_request": _handle_pull_request,
        "check_suite": _handle_check,
        "check_run": _handle_check,
        "issues": _handle_issue,
    }.get(event.event)

    if handler is None:
        return "ignored"

    status = await handler(repo, payload, escalate=escalate)
    return status


async def _handle_pull_request(
    repo: GitHubRepository, payload: dict[str, Any], *, escalate: Escalator | None
) -> str:
    pr = payload.get("pull_request") or {}
    number = payload.get("number") or pr.get("number")
    if not number:
        return "ignored"

    pull = await repo.get_pull(int(number))
    if pull is None:
        pull = _new_pull(int(number))

    pull.title = (pr.get("title") or "")[:500]
    pull.author = (pr.get("user") or {}).get("login")
    pull.head_ref = ((pr.get("head") or {}).get("ref") or "")[:200]
    pull.base_ref = ((pr.get("base") or {}).get("ref") or "main")[:64]
    pull.state = pr.get("state") or "open"
    pull.merged = bool(pr.get("merged"))
    pull.url = (pr.get("html_url") or "")[:500] or None
    pull.linked_issues = extract_linked_issues(pr.get("body"))
    pull.merged_at = _parse_ts(pr.get("merged_at"))
    pull.updated_at = datetime.now(UTC)

    pull.verdict = compute_verdict(
        merged=pull.merged,
        checks_green=pull.checks_green,
        ci_failed_count=int(pull.ci_failed_count),
        state=pull.state,
    )
    await repo.save_pull(pull)
    if pull.verdict == "escalated" and pull.escalated_at is None and escalate is not None:
        await escalate(pull, "PR 未合并且 CI 连续失败 ≥3 次")
        pull.escalated_at = datetime.now(UTC)
        await repo.save_pull(pull)
    return "processed"


async def _handle_check(
    repo: GitHubRepository, payload: dict[str, Any], *, escalate: Escalator | None
) -> str:
    suite = payload.get("check_suite") or payload.get("check_run") or {}
    conclusion = suite.get("conclusion")
    targets = payload.get("check_suite", {}).get("pull_requests") or suite.get("pull_requests") or []
    if not targets and payload.get("check_suite") is None and suite.get("check_suite"):
        targets = (suite.get("check_suite") or {}).get("pull_requests") or []

    processed = 0
    for target in targets:
        number = target.get("number") if isinstance(target, dict) else None
        if not number:
            continue
        pull = await repo.get_pull(int(number))
        if pull is None:
            # checks 先于 PR 事件到达：记录无从下手，跳过而不是创建半截记录
            continue
        apply_check_conclusion(pull, conclusion)
        pull.verdict = compute_verdict(
            merged=bool(pull.merged),
            checks_green=pull.checks_green,
            ci_failed_count=int(pull.ci_failed_count),
            state=pull.state,
        )
        pull.updated_at = datetime.now(UTC)
        await repo.save_pull(pull)
        if pull.verdict == "escalated" and pull.escalated_at is None and escalate is not None:
            await escalate(pull, f"CI 连续失败 {pull.ci_failed_count} 次")
            pull.escalated_at = datetime.now(UTC)
            await repo.save_pull(pull)
        processed += 1

    return "processed" if processed else "ignored"


async def _handle_issue(
    repo: GitHubRepository, payload: dict[str, Any], *, escalate: Escalator | None
) -> str:
    data = payload.get("issue") or {}
    number = data.get("number")
    if not number:
        return "ignored"

    issue = await repo.get_issue(int(number))
    if issue is None:
        issue = _new_issue(int(number))

    issue.title = (data.get("title") or "")[:500]
    issue.state = data.get("state") or "open"
    issue.labels = [label.get("name", "") for label in data.get("labels") or [] if label.get("name")]
    issue.url = (data.get("html_url") or "")[:500] or None
    issue.author = (data.get("user") or {}).get("login")
    issue.updated_at = datetime.now(UTC)
    issue.synced_at = datetime.now(UTC)

    await repo.save_issue(issue)
    return "processed"


async def process_event(
    repo: GitHubRepository, event: GitHubEvent, *, escalate: Escalator | None = None
) -> str:
    """处理一条已落库的事件。**异常不外抛**：写 failed 供重放，避免 webhook 500 被 GitHub 重投。"""
    try:
        status = await handle_event(repo, event, escalate=escalate)
    except Exception as exc:  # noqa: BLE001 - 处理失败必须落库，不能静默
        logger.exception("github event failed", extra={"delivery_id": event.delivery_id})
        await repo.save_event(event, status="failed", error=str(exc)[:500])
        return "failed"

    await repo.save_event(event, status=status, error=None)
    return status


# ------------------------------------------------------------------ 全量同步
class IssueClient(Protocol):
    """只提供"读 Issue"。**故意不提供任何写方法**——单向同步靠接口形状保证。"""

    async def list_issues(self, *, state: str = "all") -> list[dict[str, Any]]: ...


class HttpIssueClient:
    def __init__(self, token: str, repo: str, *, timeout: float = 10.0) -> None:
        self._token = token
        self._repo = repo
        self._timeout = timeout

    async def list_issues(self, *, state: str = "all") -> list[dict[str, Any]]:
        if not self._token or not self._repo:
            raise ConflictError("未配置 GITHUB_TOKEN / GITHUB_REPO，无法全量同步")
        import httpx

        url = f"https://api.github.com/repos/{self._repo}/issues"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"state": state, "per_page": 100}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"GitHub API 不可达: {exc}") from exc
        if resp.status_code >= 400:
            raise UpstreamError(f"GitHub API {resp.status_code}")
        return [item for item in resp.json() if "pull_request" not in item]


async def sync_issues(repo: GitHubRepository, client: IssueClient) -> tuple[int, int, int]:
    """全量同步：按 number upsert，幂等。返回 (fetched, upserted, skipped)。"""
    items = await client.list_issues(state="all")
    upserted = 0
    skipped = 0
    now = datetime.now(UTC)
    for item in items:
        number = item.get("number")
        if not number:
            skipped += 1
            continue
        issue = await repo.get_issue(int(number))
        if issue is None:
            issue = _new_issue(int(number))
        issue.title = (item.get("title") or "")[:500]
        issue.state = item.get("state") or "open"
        issue.labels = [
            label.get("name", "") for label in item.get("labels") or [] if label.get("name")
        ]
        issue.url = (item.get("html_url") or "")[:500] or None
        issue.author = (item.get("user") or {}).get("login")
        issue.updated_at = now
        issue.synced_at = now
        await repo.save_issue(issue)
        upserted += 1
    return len(items), upserted, skipped


__all__ = [
    "ESCALATE_AFTER",
    "HttpIssueClient",
    "IssueClient",
    "apply_check_conclusion",
    "compute_verdict",
    "extract_linked_issues",
    "handle_event",
    "process_event",
    "should_escalate",
    "sync_issues",
]
