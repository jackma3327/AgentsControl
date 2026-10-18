"""身份业务服务：登录、刷新轮换、登出。

核心策略（完整论证见 docs/adr/0002-auth-model.md）：
- access 15 min（无状态，验签即用）；refresh 14 天（有状态，只存哈希）；
- refresh 每次**使用即轮换**：旧令牌立即失效，新令牌接续同一条会话链（session_id）；
- 检测到已吊销令牌重放 → 按盗窃推定吊销整条会话链；
- 登录失败按 subject 维度限流：5 次 / 15 分钟（Redis 计数）；
- 登出不吊销 access token（它最多再活 15 min）——有意的取舍，ADR 有说明。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.security import TokenError, create_token, decode_token

from .ratelimit import RateLimiter
from .rbac import scopes_for_role
from .repository import AuthRepository, RefreshTokenRecord, hash_refresh_token

# PBKDF2-HMAC-SHA256，stdlib 实现，避免原生依赖（bcrypt/passlib）
_PBKDF2_ITERATIONS = 200_000

LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
# IP 维度（I04）：单 IP 15 分钟内登录失败 20 次即锁（跨 subject 的爆破防护）
LOGIN_IP_FAILURE_LIMIT = 20


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    subject: str
    role: str
    scopes: tuple[str, ...]


class AuthService:
    def __init__(self, repo: AuthRepository, limiter: RateLimiter) -> None:
        self._repo = repo
        self._limiter = limiter

    # ------------------------------------------------------------- 登录

    async def login(self, *, subject: str, password: str, ip: str | None = None) -> TokenPair:
        key = f"login:fail:subject:{subject}"
        if await self._limiter.count(key) >= LOGIN_FAILURE_LIMIT:
            raise RateLimitedError("登录失败次数过多，请 15 分钟后再试")
        ip_key = f"login:fail:ip:{ip}" if ip else None
        if ip_key and await self._limiter.count(ip_key) >= LOGIN_IP_FAILURE_LIMIT:
            raise RateLimitedError("该来源登录失败次数过多，请 15 分钟后再试")

        user = await self._repo.get_user(subject)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            await self._limiter.incr(key, LOGIN_FAILURE_WINDOW_SECONDS)
            if ip_key:
                await self._limiter.incr(ip_key, LOGIN_FAILURE_WINDOW_SECONDS)
            await self._repo.add_event(
                subject=subject, session_id=None, event_type="login_failure", detail={"ip": ip}
            )
            await self._repo.commit()
            raise UnauthorizedError("主体或密码错误")

        await self._limiter.reset(key)
        session_id = uuid.uuid4().hex
        pair = await self._issue(user.subject, user.role, session_id=session_id)
        await self._repo.add_event(
            subject=user.subject, session_id=session_id, event_type="login_success", detail={"ip": ip}
        )
        await self._repo.commit()
        return pair

    # ------------------------------------------------------------- 刷新（轮换）

    async def refresh(self, *, refresh_token: str, ip: str | None = None) -> TokenPair:
        try:
            decode_token(refresh_token, expect="refresh")
        except TokenError as exc:
            raise UnauthorizedError("刷新令牌无效或已过期") from exc

        token_hash = hash_refresh_token(refresh_token)
        record = await self._repo.get_refresh_token(token_hash)
        if record is None:
            raise UnauthorizedError("刷新令牌无效")

        if record.revoked_at is not None:
            # 旧令牌重放 → 按盗窃推定，吊销整条会话链（含最新令牌）
            await self._repo.revoke_session(record.session_id, subject=record.subject)
            await self._repo.add_event(
                subject=record.subject,
                session_id=record.session_id,
                event_type="replay_detected",
                detail=None,
            )
            await self._repo.commit()
            raise UnauthorizedError("刷新令牌已被使用（疑似重放），该会话的全部令牌已吊销")

        if record.expires_at <= datetime.now(UTC):
            raise UnauthorizedError("刷新令牌已过期")

        user = await self._repo.get_user(record.subject)
        if user is None or not user.is_active:
            raise UnauthorizedError("主体不存在或已停用")

        rotated = await self._issue(user.subject, user.role, session_id=record.session_id)
        await self._repo.revoke_refresh_token(
            token_hash, replaced_by=hash_refresh_token(rotated.refresh_token)
        )
        await self._repo.add_event(
            subject=user.subject,
            session_id=record.session_id,
            event_type="token_refresh",
            detail=None,
        )
        await self._repo.commit()
        return rotated

    # ------------------------------------------------------------- 登出

    async def logout(self, *, subject: str, session_id: str) -> int:
        """吊销当前会话链上的全部刷新令牌。旧 access token 在过期前仍然有效（设计取舍）。"""
        revoked = await self._repo.revoke_session(session_id, subject=subject)
        await self._repo.add_event(
            subject=subject, session_id=session_id, event_type="logout", detail={"revoked": revoked}
        )
        await self._repo.commit()
        return revoked

    # ------------------------------------------------------------- 吊销全部会话

    async def revoke_all(self, *, subject: str) -> int:
        """吊销该主体的全部会话（全端下线）。旧 access token 仍可存活至过期（≤15 min）。"""
        revoked = await self._repo.revoke_all_sessions(subject)
        await self._repo.add_event(
            subject=subject, session_id=None, event_type="revoke_all", detail={"revoked": revoked}
        )
        await self._repo.commit()
        return revoked

    # ------------------------------------------------------------- 内部

    async def _issue(self, subject: str, role: str, *, session_id: str) -> TokenPair:
        """签发一对令牌并落库刷新令牌哈希。不 commit，由调用方决定事务边界。"""
        scopes = scopes_for_role(role)
        refresh_raw = create_token(subject, "refresh", extra={"sid": session_id})
        await self._repo.add_refresh_token(
            RefreshTokenRecord(
                subject=subject,
                session_id=session_id,
                token_hash=hash_refresh_token(refresh_raw),
                expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
            )
        )
        access_raw = create_token(
            subject,
            "access",
            extra={"role": role, "scopes": list(scopes), "sid": session_id},
        )
        return TokenPair(
            access_token=access_raw,
            refresh_token=refresh_raw,
            expires_in=settings.access_token_ttl_seconds,
            subject=subject,
            role=role,
            scopes=scopes,
        )
