"""身份与权限模块 —— owner: W01

登录/刷新/登出、RBAC 判定、会话与令牌吊销；单用户白名单，不开放注册。

分层约定（不可越界）：
- `router.py`      只做 HTTP：解析入参、调用 service、格式化响应，**不写业务逻辑**
- `service.py`     业务规则与编排，**不依赖 Request/Response**，多步写入走事务
- `repository.py`  数据访问，只接受/返回领域对象或行记录
- `models.py`      ORM 模型，继承 `app.core.db.Base`

端点：
- POST /auth/login       登录（subject + password，失败限流 5 次/15 分钟）
- POST /auth/refresh     刷新令牌（每次轮换；重放即吊销整条会话链）
- POST /auth/logout      登出（吊销当前会话链的刷新令牌）
- POST /auth/revoke-all  吊销全部会话（需 auth:manage scope）
- GET  /auth/me          当前主体信息（role + scopes）
- GET  /auth/scopes      scope 白名单自省（RBAC）
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import OwnerDep, Principal, require_scope
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.security import TokenError, decode_token

from .ratelimit import RateLimiter, RedisRateLimiter
from .rbac import ROLE_SCOPES
from .repository import SqlAuthRepository
from .schemas import (
    LoginRequest,
    LogoutResponse,
    MeResponse,
    RefreshRequest,
    ScopesResponse,
    TokenPairResponse,
)
from .service import AuthService, TokenPair

# /auth/* 独立限流桶（I04）：单 IP 30 次/分钟，与登录失败计数（subject/IP 15min）互相独立
AUTH_BUCKET_LIMIT = 30
AUTH_BUCKET_WINDOW_SECONDS = 60


async def auth_ip_bucket(
    request: Request, limiter: Annotated[RateLimiter, Depends(get_rate_limiter)]
) -> None:
    ip = _client_ip(request) or "unknown"
    count = await limiter.incr(f"auth:bucket:ip:{ip}", AUTH_BUCKET_WINDOW_SECONDS)
    if count > AUTH_BUCKET_LIMIT:
        raise RateLimitedError("请求过于频繁，请稍后再试")


router = APIRouter(prefix="/auth", tags=["identity"], dependencies=[Depends(auth_ip_bucket)])


# ------------------------------------------------------------ 依赖装配（测试可替换）


def get_rate_limiter() -> RateLimiter:
    return RedisRateLimiter()


def get_auth_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> AuthService:
    return AuthService(SqlAuthRepository(db), limiter)


AuthDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_session_id(authorization: Annotated[str | None, Header()] = None) -> str:
    """从 access token 中取出会话链标识（sid claim）。

    在 core.deps.get_current_owner 之上组合（不修改冻结签名），
    用于登出时定位要吊销的会话链。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("缺少 Bearer 令牌")
    try:
        payload = decode_token(authorization.split(" ", 1)[1].strip(), expect="access")
    except TokenError as exc:
        raise UnauthorizedError("令牌无效或已过期") from exc
    sid = str(payload.get("sid") or "")
    if not sid:
        raise UnauthorizedError("令牌缺少会话标识，无法定位要登出的会话")
    return sid


SessionIdDep = Annotated[str, Depends(get_session_id)]


def _client_ip(request: Request) -> str | None:
    # 部署在 Caddy 之后；可信代理下取 X-Forwarded-For 首段（ADR 0002 记录信任假设）
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _pair_response(pair: TokenPair) -> TokenPairResponse:
    return TokenPairResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        subject=pair.subject,
        role=pair.role,
        scopes=list(pair.scopes),
    )


# ------------------------------------------------------------ 端点


@router.post("/login", response_model=TokenPairResponse, summary="登录（签发令牌对）")
async def login(body: LoginRequest, request: Request, auth: AuthDep) -> TokenPairResponse:
    pair = await auth.login(subject=body.subject, password=body.password, ip=_client_ip(request))
    return _pair_response(pair)


@router.post("/refresh", response_model=TokenPairResponse, summary="刷新令牌（轮换 + 防重放）")
async def refresh(body: RefreshRequest, request: Request, auth: AuthDep) -> TokenPairResponse:
    pair = await auth.refresh(refresh_token=body.refresh_token, ip=_client_ip(request))
    return _pair_response(pair)


@router.post("/logout", response_model=LogoutResponse, summary="登出（吊销当前会话链）")
async def logout(principal: OwnerDep, sid: SessionIdDep, auth: AuthDep) -> LogoutResponse:
    revoked = await auth.logout(subject=principal.subject, session_id=sid)
    return LogoutResponse(revoked=revoked)


@router.post(
    "/revoke-all",
    response_model=LogoutResponse,
    summary="吊销全部会话（全端下线，需 auth:manage）",
)
async def revoke_all(
    principal: Annotated[Principal, Depends(require_scope("auth:manage"))], auth: AuthDep
) -> LogoutResponse:
    revoked = await auth.revoke_all(subject=principal.subject)
    return LogoutResponse(revoked=revoked)


@router.get("/me", response_model=MeResponse, summary="当前主体信息")
async def me(principal: OwnerDep) -> MeResponse:
    return MeResponse(subject=principal.subject, role=principal.role, scopes=list(principal.scopes))


@router.get("/scopes", response_model=ScopesResponse, summary="scope 白名单自省（RBAC）")
async def scopes(principal: OwnerDep) -> ScopesResponse:
    return ScopesResponse(
        current=MeResponse(
            subject=principal.subject, role=principal.role, scopes=list(principal.scopes)
        ),
        roles={role: sorted(s) for role, s in sorted(ROLE_SCOPES.items())},
    )
