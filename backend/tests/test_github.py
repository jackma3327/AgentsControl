"""W09 · GitHub 验收总线测试。

策略：
- 不依赖 PG/Redis：FakeGitHubRepo 实现 Protocol；FakeEscalator 记录调用次数；
- webhook 用真实 payload 样本（tests/fixtures/github/），签名用 HMAC 真算；
- 关键边界：重放幂等、3 次失败才熔断（第 2 次不熔断）、恢复后计数归零、Issue 同步零写请求。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import create_token
from app.main import create_app
from app.modules.github_sync import service as svc
from app.modules.github_sync.models import GitHubEvent, GitHubIssue, GitHubPull
from app.modules.github_sync.router import (
    get_escalator,
    get_github_repository,
    get_issue_client,
)
from app.modules.github_sync.verify import compute_signature, require_signature, verify_signature

OWNER = "owner-1"
TOKEN = create_token(OWNER, "access", extra={"role": "owner"})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FIXTURES = Path(__file__).parent / "fixtures" / "github"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeGitHubRepo:
    def __init__(self) -> None:
        self.events: list[GitHubEvent] = []
        self.pulls: dict[int, GitHubPull] = {}
        self.issues: dict[int, GitHubIssue] = {}
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def insert_event(self, event: GitHubEvent) -> tuple[GitHubEvent, bool]:
        for existing in self.events:
            if existing.delivery_id == event.delivery_id:
                return existing, False
        self.events.append(event)
        return event, True

    async def get_event(self, event_id: str) -> GitHubEvent:
        from app.core.errors import NotFoundError

        for event in self.events:
            if event.id == event_id:
                return event
        raise NotFoundError("事件不存在")

    async def list_events(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[GitHubEvent]:
        rows = self.events if status is None else [e for e in self.events if e.status == status]
        return rows[-limit:][::-1]

    async def save_event(
        self, event: GitHubEvent, *, status: str, error: str | None
    ) -> GitHubEvent:
        event.status = status
        event.error = error
        event.attempts += 1
        event.processed_at = datetime.now(UTC)
        return event

    async def get_pull(self, number: int) -> GitHubPull | None:
        return self.pulls.get(number)

    async def save_pull(self, pull: GitHubPull) -> GitHubPull:
        self.pulls[pull.number] = pull
        return pull

    async def list_pulls(
        self, *, state: str | None = None, verdict: str | None = None, limit: int = 50
    ) -> list[GitHubPull]:
        rows = list(self.pulls.values())
        if state:
            rows = [p for p in rows if p.state == state]
        if verdict:
            rows = [p for p in rows if p.verdict == verdict]
        return rows[:limit]

    async def get_issue(self, number: int) -> GitHubIssue | None:
        return self.issues.get(number)

    async def save_issue(self, issue: GitHubIssue) -> GitHubIssue:
        self.issues[issue.number] = issue
        return issue

    async def list_issues(
        self, *, state: str | None = None, limit: int = 100
    ) -> list[GitHubIssue]:
        rows = list(self.issues.values())
        if state:
            rows = [i for i in rows if i.state == state]
        return rows[:limit]


class FakeIssueClient:
    """只实现 list_issues；`write_calls` 用于断言"零写请求"。"""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.write_calls = 0
        self.list_calls = 0

    async def list_issues(self, *, state: str = "all") -> list[dict[str, Any]]:
        self.list_calls += 1
        return list(self.items)


class FakeEscalator:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    async def __call__(self, pull: GitHubPull, reason: str) -> None:
        self.calls.append((pull.number, reason))


@pytest.fixture()
def repo() -> FakeGitHubRepo:
    return FakeGitHubRepo()


@pytest.fixture()
def escalator() -> FakeEscalator:
    return FakeEscalator()


@pytest.fixture()
def client(
    repo: FakeGitHubRepo, escalator: FakeEscalator, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setattr(settings, "github_webhook_secret", "test-secret")
    monkeypatch.setattr(settings, "github_token", "")
    monkeypatch.setattr(settings, "github_repo", "")
    app = create_app()
    app.dependency_overrides[get_github_repository] = lambda: repo
    app.dependency_overrides[get_escalator] = lambda: escalator
    return TestClient(app)


def post_webhook(
    client: TestClient,
    body: bytes,
    *,
    event: str,
    delivery: str,
    secret: str = "test-secret",
    eager: bool = True,
    signature: str | None = None,
):
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature or compute_signature(secret, body),
    }
    suffix = "?eager=true" if eager else ""
    return client.post(f"/api/v1/github/webhook{suffix}", content=body, headers=headers)


# --------------------------------------------------------------------- I01
def test_signature_roundtrip_and_mismatch() -> None:
    body = b'{"hello":"world"}'
    sig = compute_signature("s3cret", body)
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("s3cret", body, "sha256=deadbeef")
    assert not verify_signature("", body, sig)  # 未配置密钥一律拒绝
    with pytest.raises(UnauthorizedError):
        require_signature("s3cret", body, None)


def test_webhook_rejects_bad_signature(client: TestClient) -> None:
    body = json.dumps(load("pull_request_merged.json")).encode()
    resp = post_webhook(
        client, body, event="pull_request", delivery="d-1", signature="sha256=nope"
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_webhook_rejects_missing_delivery(client: TestClient) -> None:
    body = json.dumps(load("issues_opened.json")).encode()
    resp = client.post(
        "/api/v1/github/webhook?eager=true",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": compute_signature("test-secret", body),
        },
    )
    assert resp.status_code == 401


def test_webhook_is_idempotent_by_delivery(
    client: TestClient, repo: FakeGitHubRepo
) -> None:
    body = json.dumps(load("pull_request_merged.json")).encode()
    first = post_webhook(client, body, event="pull_request", delivery="d-42")
    assert first.status_code == 202
    assert first.json()["status"] == "processed"

    second = post_webhook(client, body, event="pull_request", delivery="d-42")
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert len(repo.events) == 1


def test_webhook_persists_event_before_processing(
    client: TestClient, repo: FakeGitHubRepo
) -> None:
    body = json.dumps({"action": "created", "weird": True}).encode()
    resp = post_webhook(client, body, event="star", delivery="d-star")
    assert resp.status_code == 202
    assert resp.json()["status"] == "ignored"
    assert repo.events[0].payload["weird"] is True
    assert repo.events[0].status == "ignored"


# --------------------------------------------------------------------- I02
@pytest.mark.parametrize(
    ("merged", "green", "failed", "state", "expected"),
    [
        (True, True, 0, "closed", "ok"),
        (True, None, 0, "closed", "ok"),
        (True, False, 4, "closed", "escalated"),
        (False, False, 2, "open", "pending"),
        (False, False, 3, "open", "escalated"),
        (False, False, 0, "closed", "rework"),
        (False, None, 0, "open", "pending"),
    ],
)
def test_verdict_pure_function(
    merged: bool, green: bool | None, failed: int, state: str, expected: str
) -> None:
    assert (
        svc.compute_verdict(
            merged=merged, checks_green=green, ci_failed_count=failed, state=state
        )
        == expected
    )


def test_pull_request_event_sets_verdict_and_links_issues(
    client: TestClient, repo: FakeGitHubRepo
) -> None:
    body = json.dumps(load("pull_request_merged.json")).encode()
    post_webhook(client, body, event="pull_request", delivery="d-pr-42")

    pull = repo.pulls[42]
    assert pull.merged is True
    assert pull.verdict == "ok"
    assert pull.linked_issues == [8, 9]
    assert pull.author == "jackma3327"

    detail = client.get("/api/v1/github/pulls/42", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["verdict"] == "ok"
    assert detail.json()["linked_issues"] == [8, 9]


def test_linked_issues_regex() -> None:
    assert svc.extract_linked_issues("Closes #12") == [12]
    assert svc.extract_linked_issues("fixes #3 and resolved #7") == [3, 7]
    assert svc.extract_linked_issues("see #5 for context") == []
    assert svc.extract_linked_issues(None) == []


# --------------------------------------------------------------------- I04
def test_escalates_only_at_third_consecutive_failure(
    client: TestClient, repo: FakeGitHubRepo, escalator: FakeEscalator
) -> None:
    pr_body = json.dumps(
        {
            "action": "opened",
            "number": 42,
            "pull_request": {
                "number": 42,
                "title": "x",
                "body": "",
                "state": "open",
                "merged": False,
                "user": {"login": "a"},
                "head": {"ref": "b"},
                "base": {"ref": "main"},
            },
        }
    ).encode()
    post_webhook(client, pr_body, event="pull_request", delivery="d-open")

    failure = json.dumps(load("check_suite_failure.json")).encode()
    post_webhook(client, failure, event="check_suite", delivery="d-f1")
    post_webhook(client, failure, event="check_suite", delivery="d-f2")
    assert repo.pulls[42].ci_failed_count == 2
    assert repo.pulls[42].verdict == "pending"
    assert escalator.calls == []

    post_webhook(client, failure, event="check_suite", delivery="d-f3")
    assert repo.pulls[42].ci_failed_count == 3
    assert repo.pulls[42].verdict == "escalated"
    assert len(escalator.calls) == 1

    # 第 4 次失败不再重复开审批（escalated_at 已置位）
    post_webhook(client, failure, event="check_suite", delivery="d-f4")
    assert len(escalator.calls) == 1


def test_success_resets_failure_counter(client: TestClient, repo: FakeGitHubRepo) -> None:
    pr_body = json.dumps(
        {
            "action": "opened",
            "number": 7,
            "pull_request": {
                "number": 7,
                "title": "y",
                "body": "",
                "state": "open",
                "merged": False,
                "user": {"login": "a"},
                "head": {"ref": "b"},
                "base": {"ref": "main"},
            },
        }
    ).encode()
    post_webhook(client, pr_body, event="pull_request", delivery="d7")
    failure = json.dumps(load("check_suite_failure.json")).encode().replace(b"42", b"7")
    post_webhook(client, failure, event="check_suite", delivery="d7-f1")
    assert repo.pulls[7].ci_failed_count == 1

    success = json.dumps(load("check_suite_success.json")).encode().replace(b"42", b"7")
    post_webhook(client, success, event="check_suite", delivery="d7-s1")
    assert repo.pulls[7].ci_failed_count == 0
    assert repo.pulls[7].checks_green is True


# --------------------------------------------------------------------- I03
def test_issue_event_mirrors_fields(client: TestClient, repo: FakeGitHubRepo) -> None:
    body = json.dumps(load("issues_opened.json")).encode()
    post_webhook(client, body, event="issues", delivery="d-issue-12")
    issue = repo.issues[12]
    assert issue.title.startswith("[W10]")
    assert issue.labels == ["window:W10", "priority/p0"]
    assert issue.state == "open"


def test_full_sync_is_readonly_and_idempotent(
    client: TestClient, repo: FakeGitHubRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeIssueClient(
        [
            {
                "number": 12,
                "title": "[W10] 前端控制台",
                "state": "open",
                "labels": [{"name": "window:W10"}],
                "html_url": "https://example/12",
                "user": {"login": "jackma3327"},
            }
        ]
    )
    app = client.app
    app.dependency_overrides[get_issue_client] = lambda: fake  # type: ignore[attr-defined]

    first = client.post("/api/v1/github/sync", headers=AUTH)
    assert first.status_code == 200
    assert first.json()["upserted"] == 1

    second = client.post("/api/v1/github/sync", headers=AUTH)
    assert second.status_code == 200
    assert second.json()["upserted"] == 1  # upsert 幂等，不会重复建行
    assert len(repo.issues) == 1

    # 单向同步：客户端只有 list_issues，从未发生写请求
    assert fake.write_calls == 0
    assert fake.list_calls == 2


def test_sync_without_token_conflicts(client: TestClient) -> None:
    resp = client.post("/api/v1/github/sync", headers=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


# ---------------------------------------------------------------- 鉴权与重放
def test_owner_endpoints_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/github/pulls").status_code == 401
    assert client.get("/api/v1/github/issues").status_code == 401


def test_failed_event_can_be_replayed(
    client: TestClient, repo: FakeGitHubRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.dumps(load("issues_opened.json")).encode()
    post_webhook(client, body, event="issues", delivery="d-replay")

    event_id = repo.events[0].id
    # 人为制造一次失败：把 payload 改成不可处理的结构
    repo.events[0].payload = {"action": "opened", "issue": {"number": None}}
    resp = client.post(f"/api/v1/github/events/{event_id}/replay", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
