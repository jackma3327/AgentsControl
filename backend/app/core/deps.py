"""通用 FastAPI 依赖。

`get_current_owner` 只做「验签 + 主体解析 + 白名单」，RBAC 细化在 identity 模块（W01）。
本文件是**冻结文件**：任何窗口不要修改签名，只允许在其上组合。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from .config import settings
from .errors import ForbiddenError, UnauthorizedError
from .security import TokenError, decode_token


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    role: str
    scopes: tuple[str, ...] = ()

    def has(self, scope: str) -> bool:
        return scope in self.scopes or self.role == "owner"


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("缺少 Bearer 令牌")
    return authorization.split(" ", 1)[1].strip()


async def get_current_owner(authorization: Annotated[str | None, Header()] = None) -> Principal:
    token = _bearer(authorization)
    try:
        payload = decode_token(token, expect="access")
    except TokenError as exc:
        raise UnauthorizedError("令牌无效或已过期") from exc

    subject = str(payload.get("sub") or "")
    if not subject:
        raise UnauthorizedError("令牌缺少主体")

    allow = settings.owner_openid_list
    if allow and subject not in allow:
        raise ForbiddenError("该主体不在白名单内")

    scopes = payload.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return Principal(subject=subject, role=str(payload.get("role") or "owner"), scopes=tuple(scopes))


OwnerDep = Annotated[Principal, Depends(get_current_owner)]


def require_scope(scope: str):  # noqa: ANN201 - 返回依赖函数
    """scope 校验依赖工厂（W01-I02 接线）。

    用法：`Depends(require_scope("tasks:write"))`。
    本函数必须保持 **同步**：`Depends(require_scope("x"))` 在路由定义期即调用，
    若为 async 会把协程对象当依赖值，端点拿到的是函数而不是 Principal。
    scope 白名单的唯一权威来源是 `app/modules/identity/rbac.py`；
    返回的依赖带 `__required_scope__` 标记，`backend/scripts/audit_scopes.py`
    据此断言「每个写端点都挂了 scope」。
    """

    async def _dep(principal: OwnerDep) -> Principal:
        if not principal.has(scope):
            raise ForbiddenError(f"缺少权限: {scope}")
        return principal

    _dep.__required_scope__ = scope  # type: ignore[attr-defined]
    return _dep
