"""W01 identity 单测：登录、令牌轮换、重放、限流、白名单。

设计说明：
- CI 的 backend job 没有 PG/Redis 服务，因此 service 依赖通过
  `dependency_overrides` 替换为内存实现（FakeRepo / FakeLimiter），
  只测业务逻辑，不测 SQL 方言（迁移由 CI migrations job 覆盖）；
- 涉及全局配置（白名单）的用例用 monkeypatch 就地改 settings 单例并自动还原。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.core.deps import Principal, require_scope
from app.core.errors import ForbiddenError
from app.core.security import create_token
from app.main import create_app
from app.modules.identity.cookies import REFRESH_COOKIE_PATH
from app.modules.identity.rbac import ROLE_SCOPES, scopes_for_role
from app.modules.identity.repository import RefreshTokenRecord, UserRecord, hash_refresh_token
from app.modules.identity.router import get_auth_service, get_rate_limiter
from app.modules.identity.service import (
    LOGIN_FAILURE_LIMIT,
    LOGIN_FAILURE_WINDOW_SECONDS,
    AuthService,
    hash_password,
    verify_password,
)

OWNER = "owner@example.com"
PASSWORD = "correct-password-1"


# ------------------------------------------------------------ 内存替身


@dataclass
class _TokenRow:
    record: RefreshTokenRecord
    revoked_at: datetime | None = None
    replaced_by: str | None = None


class FakeRepo:
    def __init__(self, users: list[UserRecord]) -> None:
        self.users = {u.subject: u for u in users}
        self.tokens: dict[str, _TokenRow] = {}
        self.events: list[dict[str, Any]] = []
        self.commits = 0

    async def get_user(self, subject: str) -> UserRecord | None:
        return self.users.get(subject)

    async def add_refresh_token(self, record: RefreshTokenRecord) -> None:
        self.tokens[record.token_hash] = _TokenRow(record=record)

    async def get_refresh_token(self, token_hash: str) -> RefreshTokenRecord | None:
        row = self.tokens.get(token_hash)
        if row is None:
            return None
        return RefreshTokenRecord(
            subject=row.record.subject,
            session_id=row.record.session_id,
            token_hash=row.record.token_hash,
            expires_at=row.record.expires_at,
            revoked_at=row.revoked_at,
            replaced_by=row.replaced_by,
        )

    async def revoke_refresh_token(self, token_hash: str, *, replaced_by: str | None) -> bool:
        row = self.tokens.get(token_hash)
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(UTC)
        row.replaced_by = replaced_by
        return True

    async def revoke_session(self, session_id: str, *, subject: str | None = None) -> int:
        n = 0
        for row in self.tokens.values():
            if row.record.session_id != session_id:
                continue
            if subject is not None and row.record.subject != subject:
                continue
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                n += 1
        return n

    async def revoke_all_sessions(self, subject: str) -> int:
        n = 0
        for row in self.tokens.values():
            if row.record.subject == subject and row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                n += 1
        return n

    async def add_event(
        self,
        *,
        subject: str,
        session_id: str | None,
        event_type: str,
        detail: dict | None = None,
    ) -> None:
        self.events.append(
            {"subject": subject, "session_id": session_id, "event_type": event_type, "detail": detail}
        )

    async def commit(self) -> None:
        self.commits += 1

    def session_of(self, refresh_token: str) -> str:
        return self.tokens[hash_refresh_token(refresh_token)].record.session_id


@dataclass
class FakeLimiter:
    counts: dict[str, tuple[int, datetime]] = field(default_factory=dict)

    async def count(self, key: str) -> int:
        entry = self.counts.get(key)
        if entry is None:
            return 0
        n, expires_at = entry
        return n if expires_at > datetime.now(UTC) else 0

    async def incr(self, key: str, window_seconds: int) -> int:
        n = await self.count(key) + 1
        self.counts[key] = (n, datetime.now(UTC) + timedelta(seconds=window_seconds))
        return n

    async def ttl(self, key: str) -> int:
        entry = self.counts.get(key)
        if entry is None:
            return 0
        _n, expires_at = entry
        return max(0, ceil((expires_at - datetime.now(UTC)).total_seconds()))

    async def reset(self, key: str) -> None:
        self.counts.pop(key, None)


@dataclass
class Harness:
    client: TestClient
    repo: FakeRepo
    limiter: FakeLimiter


@pytest.fixture()
def h() -> Iterator[Harness]:
    repo = FakeRepo([UserRecord(OWNER, hash_password(PASSWORD), "owner", True)])
    limiter = FakeLimiter()
    service = AuthService(repo, limiter)
    app = create_app()
    app.dependency_overrides[get_auth_service] = lambda: service
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    with TestClient(app) as client:
        yield Harness(client=client, repo=repo, limiter=limiter)


def _login(h: Harness, subject: str = OWNER, password: str = PASSWORD) -> dict[str, Any]:
    r = h.client.post("/api/v1/auth/login", json={"subject": subject, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _body_channel(h: Harness) -> None:
    """清掉 TestClient cookie jar 里的 refresh_token，让后续请求只走 body 渠道。

    S1-01 起 /auth/refresh 优先读 Cookie；jar 会按 Path 匹配自动带上登录时写入的
    Cookie，body 渠道的兼容用例必须显式清 jar，否则测的不是自己想测的渠道。
    """
    h.client.cookies.delete("refresh_token", path=REFRESH_COOKIE_PATH)


def _set_cookie(h: Harness, token: str) -> None:
    """往 jar 里放一条 refresh_token（等价于浏览器持有登录 Cookie）。"""
    h.client.cookies.set("refresh_token", token, path=REFRESH_COOKIE_PATH)


def _set_cookie_header(r: Any) -> str:
    """从响应头里挑出 refresh_token 那条 Set-Cookie。"""
    cookies = r.headers.get_list("set-cookie")
    return next(c for c in cookies if c.startswith("refresh_token="))


# ------------------------------------------------------------ 密码原语


def test_password_hash_roundtrip() -> None:
    stored = hash_password(PASSWORD)
    assert stored != PASSWORD
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)
    assert not verify_password(PASSWORD, "not-a-valid-format")


# ------------------------------------------------------------ 登录


def test_login_returns_pair_and_me(h: Harness) -> None:
    body = _login(h)
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900  # 15 min（settings 默认）
    assert body["role"] == "owner"
    assert "auth:manage" in body["scopes"]

    me = h.client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    data = me.json()
    assert data["subject"] == OWNER
    assert data["role"] == "owner"
    assert "tasks:write" in data["scopes"]


def test_login_wrong_password_rejected(h: Harness) -> None:
    r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_login_unknown_subject_rejected(h: Harness) -> None:
    r = h.client.post("/api/v1/auth/login", json={"subject": "ghost", "password": PASSWORD})
    assert r.status_code == 401


def test_login_locked_after_5_failures(h: Harness) -> None:
    for _ in range(LOGIN_FAILURE_LIMIT):
        r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "bad"})
        assert r.status_code == 401
    # 第 6 次：即使密码正确也 429（锁定检查先于密码校验）
    r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": PASSWORD})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_login_success_resets_failure_counter(h: Harness) -> None:
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "bad"})
    _login(h)  # 成功登录清零
    for _ in range(LOGIN_FAILURE_LIMIT - 1):
        h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "bad"})
    # 仍是 401 而非 429：计数被成功登录重置过
    r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "bad"})
    assert r.status_code == 401


# ------------------------------------------------------------ 刷新轮换与重放


def test_refresh_rotates_old_token_becomes_invalid(h: Harness) -> None:
    first = _login(h)
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    # 同一条会话链
    assert h.repo.session_of(second["refresh_token"]) == h.repo.session_of(first["refresh_token"])

    # 旧 refresh token 立即失效（body 渠道，jar 已清）
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 401


def test_refresh_replay_revokes_entire_chain(h: Harness) -> None:
    first = _login(h)
    _body_channel(h)
    second = h.client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()
    # 重放已轮换的旧令牌 → 401，且整条链（含最新令牌）被吊销
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 401
    assert "重放" in r.json()["error"]["message"]

    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert r.status_code == 401


def test_expired_refresh_token_rejected(h: Harness) -> None:
    body = _login(h)
    _body_channel(h)
    expired = create_token(OWNER, "refresh", ttl_seconds=-10, extra={"sid": "whatever"})
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": expired})
    assert r.status_code == 401
    # 正常令牌不受影响
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200


def test_refresh_unknown_token_rejected(h: Harness) -> None:
    _login(h)  # 让系统有至少一条会话
    _body_channel(h)
    forged = create_token(OWNER, "refresh", extra={"sid": "fake"})  # 签名合法但未入库
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": forged})
    assert r.status_code == 401


def test_refresh_access_token_rejected(h: Harness) -> None:
    body = _login(h)
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": body["access_token"]})
    assert r.status_code == 401


# ------------------------------------------------------------ Cookie 渠道（S1-01 / 契约 C1-C2）


def test_login_sets_refresh_cookie(h: Harness) -> None:
    r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": PASSWORD})
    assert r.status_code == 200
    cookie = _set_cookie_header(r)
    assert "HttpOnly" in cookie
    assert "Path=/api/v1/auth" in cookie  # 只在 auth 路径生效，别的接口收不到
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie  # 本地 http：带 Secure 会被浏览器丢弃，登录不了
    assert f"Max-Age={settings.refresh_token_ttl_seconds}" in cookie


def test_login_cookie_secure_in_prod(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "prod")
    r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": PASSWORD})
    assert r.status_code == 200
    assert "Secure" in _set_cookie_header(r)


def test_refresh_reads_cookie_with_empty_body(h: Harness) -> None:
    """浏览器形状：只带 Cookie、空 body → 200，且轮换后 Cookie 同步换新。"""
    first = _login(h)
    _set_cookie(h, first["refresh_token"])
    r = h.client.post("/api/v1/auth/refresh")
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["access_token"] != first["access_token"]
    assert second["refresh_token"] != first["refresh_token"]
    assert second["refresh_token"] in _set_cookie_header(r)


def test_refresh_falls_back_to_body_without_cookie(h: Harness) -> None:
    """兼容渠道：无 Cookie 时 body 传参仍然 200（小程序 / 设备场景）。"""
    first = _login(h)
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] != first["access_token"]


def test_refresh_without_cookie_or_body_rejected(h: Harness) -> None:
    _login(h)
    _body_channel(h)  # 模拟没有 Cookie 的全新隐身窗口
    r = h.client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_logout_clears_cookie_and_kills_refresh(h: Harness) -> None:
    body = _login(h)
    r = h.client.post("/api/v1/auth/logout", headers=_auth(body["access_token"]))
    assert r.status_code == 200
    cookie = _set_cookie_header(r)
    assert "Max-Age=0" in cookie  # 浏览器侧立即失效

    # 登出后的旧 Cookie 再刷新 → 401 unauthorized
    _set_cookie(h, body["refresh_token"])
    r = h.client.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_revoke_all_clears_cookie(h: Harness) -> None:
    body = _login(h)
    r = h.client.post("/api/v1/auth/revoke-all", headers=_auth(body["access_token"]))
    assert r.status_code == 200
    assert "Max-Age=0" in _set_cookie_header(r)


# ------------------------------------------------------------ 限流剩余时间（S1-02 / 契约 C3）


def test_login_lockout_returns_retry_after(h: Harness) -> None:
    for _ in range(LOGIN_FAILURE_LIMIT):
        h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "bad"})
    r = h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": PASSWORD})
    assert r.status_code == 429
    error = r.json()["error"]
    assert error["code"] == "rate_limited"
    retry_after = error["details"]["retry_after"]
    assert 0 < retry_after <= LOGIN_FAILURE_WINDOW_SECONDS
    assert r.headers["Retry-After"] == str(retry_after)


# ------------------------------------------------------------ 白名单


def test_subject_outside_whitelist_forbidden(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "owner_openids", "someone-else@example.com")
    token = create_token(OWNER, "access", extra={"role": "owner", "scopes": []})
    r = h.client.get("/api/v1/auth/me", headers=_auth(token))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


# ------------------------------------------------------------ 登出


def test_logout_revokes_session_chain(h: Harness) -> None:
    body = _login(h)
    r = h.client.post("/api/v1/auth/logout", headers=_auth(body["access_token"]))
    assert r.status_code == 200
    assert r.json()["revoked"] == 1

    # 刷新令牌已死（body 渠道复放）
    _body_channel(h)
    r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 401

    # 设计取舍：旧 access token 在过期前（≤15 min）仍然有效
    me = h.client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200


def test_logout_requires_sid_claim(h: Harness) -> None:
    token = create_token(OWNER, "access", extra={"role": "owner"})
    r = h.client.post("/api/v1/auth/logout", headers=_auth(token))
    assert r.status_code == 401


def test_me_requires_auth(h: Harness) -> None:
    r = h.client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


# ------------------------------------------------------------ RBAC（I02）


def test_rbac_role_scopes_mapping() -> None:
    owner = set(scopes_for_role("owner"))
    operator = set(scopes_for_role("operator"))
    viewer = set(scopes_for_role("viewer"))

    assert "auth:manage" in owner
    assert "auth:manage" not in operator  # 会话管理仅 owner
    assert "tasks:write" in operator
    assert "tasks:write" not in viewer  # viewer 只读
    assert "tasks:read" in viewer
    # 子集关系：viewer ⊆ operator ⊆ owner
    assert viewer < operator < owner
    # 未知角色降级为最低权限
    assert set(scopes_for_role("ghost")) == viewer


def test_scopes_endpoint_returns_mapping_and_current(h: Harness) -> None:
    body = _login(h)
    r = h.client.get("/api/v1/auth/scopes", headers=_auth(body["access_token"]))
    assert r.status_code == 200
    data = r.json()
    assert set(data["roles"]) == {"owner", "operator", "viewer"}
    assert data["roles"]["operator"] == sorted(ROLE_SCOPES["operator"])
    assert data["current"]["subject"] == OWNER
    assert data["current"]["role"] == "owner"


async def test_require_scope_forbidden_and_allowed() -> None:
    dep = require_scope("tasks:write")
    viewer = Principal(subject="u", role="viewer", scopes=scopes_for_role("viewer"))
    operator = Principal(subject="u", role="operator", scopes=scopes_for_role("operator"))

    with pytest.raises(ForbiddenError) as exc:
        await dep(viewer)
    assert exc.value.code == "forbidden"  # 越权必须 403 + error.code=forbidden
    assert "tasks:write" in exc.value.message

    assert (await dep(operator)).subject == "u"
    assert dep.__required_scope__ == "tasks:write"  # 审计脚本依赖该标记


# ------------------------------------------------------------ 吊销联动（I03）


def test_revoke_all_kills_every_session(h: Harness) -> None:
    s1 = _login(h)
    s2 = _login(h)
    r = h.client.post("/api/v1/auth/revoke-all", headers=_auth(s1["access_token"]))
    assert r.status_code == 200
    assert r.json()["revoked"] == 2

    # 所有会话的刷新令牌全部死亡（body 渠道复放）
    _body_channel(h)
    for body in (s1, s2):
        r = h.client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 401
    # 设计取舍：已签发的 access token 在过期前（≤15 min）仍然有效
    assert h.client.get("/api/v1/auth/me", headers=_auth(s2["access_token"])).status_code == 200


def test_revoke_all_requires_manage_scope(h: Harness) -> None:
    viewer_scopes = list(scopes_for_role("viewer"))
    token = create_token("v@example.com", "access", extra={"role": "viewer", "scopes": viewer_scopes})
    r = h.client.post("/api/v1/auth/revoke-all", headers=_auth(token))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_auth_events_recorded(h: Harness) -> None:
    body = _login(h)
    h.client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    h.client.post("/api/v1/auth/logout", headers=_auth(body["access_token"]))
    types = [e["event_type"] for e in h.repo.events]
    assert types == ["login_success", "token_refresh", "logout"]
    # 失败登录也留痕
    h.client.post("/api/v1/auth/login", json={"subject": OWNER, "password": "bad"})
    assert h.repo.events[-1]["event_type"] == "login_failure"


# ------------------------------------------------------------ 加固（I04）


def test_auth_ip_bucket_rate_limits(h: Harness) -> None:
    # /auth/* 独立请求桶：单 IP 30 次/分钟（用不触发失败计数的 /me 探测）
    for _ in range(30):
        r = h.client.get("/api/v1/auth/me")
        assert r.status_code == 401
    r = h.client.get("/api/v1/auth/me")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_login_ip_lockout_across_subjects(h: Harness) -> None:
    # 同一 IP 换 subject 爆破：15 分钟内失败 20 次后，新主体也 429（subject 维度还没到 5）
    for i in range(20):
        r = h.client.post(
            "/api/v1/auth/login", json={"subject": f"victim-{i}@example.com", "password": "x"}
        )
        assert r.status_code == 401
    r = h.client.post("/api/v1/auth/login", json={"subject": "victim-final@example.com", "password": "x"})
    assert r.status_code == 429


def test_prod_disables_docs_and_openapi(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient as TC

    from app import main as app_main

    prod = Settings(
        app_env="prod",
        jwt_secret="x" * 40,
        github_webhook_secret="whsec",
        owner_openids="owner@example.com",
    )
    monkeypatch.setattr(app_main, "settings", prod)
    prod_app = app_main.create_app()
    assert prod_app.docs_url is None
    assert prod_app.openapi_url is None
    with TC(prod_app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
