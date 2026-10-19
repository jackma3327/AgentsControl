"""身份与权限模块 —— owner: W01

登录/刷新/登出、RBAC 判定、会话与令牌吊销；单用户白名单，不开放注册。

分层约定（不可越界）：
- `router.py`      只做 HTTP：解析入参、调用 service、格式化响应，**不写业务逻辑**
- `service.py`     业务规则与编排，**不依赖 Request/Response**，多步写入走事务
- `repository.py`  数据访问，只接受/返回领域对象或行记录
- `models.py`      ORM 模型，继承 `app.core.db.Base`
- `cookies.py`     refresh_token Cookie 契约（属性集中在这里，端点不散字面量）

端点：
- POST /auth/login       登录（subject + password，失败限流 5 次/15 分钟）；成功写 HttpOnly Cookie
- POST /auth/refresh     刷新令牌（每次轮换；重放即吊销整条会话链）；优先读 Cookie，回退 body
- POST /auth/logout      登出（吊销当前会话链的刷新令牌，清除 Cookie）
- POST /auth/revoke-all  吊销全部会话（需 auth:manage scope）
- GET  /auth/me          当前主体信息（role + scopes）
- GET  /auth/scopes      scope 白名单自省（RBAC）
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import OwnerDep, Principal, require_scope
from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.security import TokenError, decode_token

from .cookies import REFRESH_COOKIE_NAME, clear_refresh_cookie, set_refresh_cookie
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
    key = f"auth:bucket:ip:{ip}"
    count = await limiter.incr(key, AUTH_BUCKET_WINDOW_SECONDS)
    if count > AUTH_BUCKET_LIMIT:
        # details.retry_after 让客户端知道桶窗口还剩多久（S1-02）。
        # 依赖层抛出的异常走 core/errors.py 的全局 handler（冻结区，不带自定义头），
        # 因此这里只保证 body 里有剩余秒数；Retry-After 头在端点内补（见 login）。
        raise RateLimitedError(
            "请求过于频繁，请稍后再试",
            details={"retry_after": await limiter.ttl(key) or AUTH_BUCKET_WINDOW_SECONDS},
        )


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


def _rate_limited_response(request: Request, exc: RateLimitedError) -> JSONResponse:
    """429 + Retry-After（S1-02）。

    `core/errors.py` 的全局 handler 是冻结区（不支持自定义响应头），因此限流的
    头在这里补；body 结构与全局 handler 逐字段一致（契约 C3），前端无感。
    """
    rid = getattr(request.state, "request_id", None) or str(uuid.uuid4())
    details = exc.details if isinstance(exc.details, dict) else None
    retry_after = details.get("retry_after") if details else None
    headers = {"Retry-After": str(int(retry_after))} if isinstance(retry_after, int) else {}
    body: dict = {"error": {"code": exc.code, "message": exc.message, "request_id": rid}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


# ------------------------------------------------------------ 端点


@router.post("/login", response_model=TokenPairResponse, summary="登录（签发令牌对）")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth: AuthDep,
) -> Response:
    try:
        pair = await auth.login(subject=body.subject, password=body.password, ip=_client_ip(request))
    except RateLimitedError as exc:
        return _rate_limited_response(request, exc)
    # refresh token 同时进 HttpOnly Cookie（契约 C1）：浏览器端不再依赖 body 里的字段
    set_refresh_cookie(response, pair.refresh_token)
    return _pair_response(pair)


@router.post("/refresh", response_model=TokenPairResponse, summary="刷新令牌（轮换 + 防重放）")
async def refresh(
    request: Request,
    response: Response,
    auth: AuthDep,
    body: RefreshRequest | None = None,
) -> Response:
    # 契约 C2：优先读 Cookie，回退 body（小程序 / 设备场景）。两者皆无 → service 明确 401。
    token = request.cookies.get(REFRESH_COOKIE_NAME) or (body.refresh_token if body else None)
    pair = await auth.refresh(refresh_token=token, ip=_client_ip(request))
    # 轮换：旧 Cookie 值立即失效，写入新值
    set_refresh_cookie(response, pair.refresh_token)
    return _pair_response(pair)


@router.post("/logout", response_model=LogoutResponse, summary="登出（吊销当前会话链）")
async def logout(
    principal: OwnerDep,
    sid: SessionIdDep,
    response: Response,
    auth: AuthDep,
) -> LogoutResponse:
    revoked = await auth.logout(subject=principal.subject, session_id=sid)
    clear_refresh_cookie(response)
    return LogoutResponse(revoked=revoked)


@router.post(
    "/revoke-all",
    response_model=LogoutResponse,
    summary="吊销全部会话（全端下线，需 auth:manage）",
)
async def revoke_all(
    principal: Annotated[Principal, Depends(require_scope("auth:manage"))],
    response: Response,
    auth: AuthDep,
) -> LogoutResponse:
    revoked = await auth.revoke_all(subject=principal.subject)
    # 全部会话已死，本浏览器的 Cookie 也没有留存价值，一并清除
    clear_refresh_cookie(response)
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
