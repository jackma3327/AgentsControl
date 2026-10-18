"""HTTP 入参/出参模型（router 层专用，不含业务规则）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=128, description="登录主体（白名单内）")
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


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
