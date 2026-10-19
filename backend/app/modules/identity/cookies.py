"""refresh_token Cookie 契约（评审 Issue 1 / S1-01，契约编号 C1）。

集中定义 Cookie 属性，router 只调用这里的函数，不让字面量散落在端点里：
- 名字 `refresh_token`；Path 精确到 `<api_prefix>/auth`——refresh 只在 auth 路径用，
  `/auth/me` 之类的接口收不到它（有意的收敛，缩小 Cookie 的暴露面）；
- `HttpOnly` + `SameSite=Lax`；
- `Secure` 仅在生产（`app_env == "prod"`）：本地 http 带 Secure 会被浏览器直接丢弃，登录不了；
- `Max-Age` 与 refresh token TTL 一致（`settings.refresh_token_ttl_seconds`）。

改这个文件等于改前后端契约，需同步 `docs/adr/` 与前端 `web/src/lib/api/client.ts`。
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import settings

REFRESH_COOKIE_NAME = "refresh_token"
# api_prefix 默认 /api/v1；用 settings 拼而不是写死，跟随配置单一来源
REFRESH_COOKIE_PATH = f"{settings.api_prefix.rstrip('/')}/auth"


def _secure() -> bool:
    """生产才带 Secure。TLS 在 Caddy 层终止，本地 http 开发环境不能带。"""
    return settings.app_env == "prod"


def set_refresh_cookie(response: Response, token: str) -> None:
    """登录 / 刷新成功时写入（轮换）：新 Cookie 覆盖旧值。"""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=settings.refresh_token_ttl_seconds,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_secure(),
    )


def clear_refresh_cookie(response: Response) -> None:
    """登出 / 全端吊销时清除。属性必须与写入一致，否则浏览器不清不掉。"""
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="lax",
        secure=_secure(),
    )
