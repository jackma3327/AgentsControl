"""HTTP 入参/出参模型（router 层专用，不含业务规则）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=128, description="登录主体（白名单内）")
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    """刷新入参。refresh_token 改为可选（S1-01 / 契约 C2）：

    浏览器场景优先读 HttpOnly Cookie，body 传参保留给小程序 / 设备等
    无法依赖 Cookie 的调用方——两个渠道都必须继续可用，不得删除字段。
    """

    refresh_token: str | None = Field(default=None, min_length=1, max_length=4096)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="access token 有效期（秒）")
    subject: str
    role: str
    scopes: list[str]


class MeResponse(BaseModel):
    subject: str
    role: str
    scopes: list[str]


class ScopesResponse(BaseModel):
    current: MeResponse
    roles: dict[str, list[str]] = Field(description="角色 → scope 白名单（唯一权威：rbac.py）")


class LogoutResponse(BaseModel):
    revoked: int = Field(description="本次吊销的刷新令牌数")
