"""角色 → scope 映射（唯一权威来源）。

规则：
- scope 命名 `<module>:<action>`，全部 scope 集中定义在这里，各窗口不得散落硬编码；
- 三档角色：owner（全部）/ operator（日常运营，除账号与会话管理）/ viewer（只读）；
- `core.deps.Principal.has()` 对 owner 有隐式放行，但 owner 仍显式列出全部 scope，
  保证 `/auth/me`、`/auth/scopes` 的自省输出完整。
"""

from __future__ import annotations

_READ_SCOPES = frozenset(
    {
        "intake:read",
        "goals:read",
        "tasks:read",
        "devices:read",
        "execution:read",
        "ledger:read",
        "approvals:read",
        "evals:read",
        "github:read",
    }
)

_WRITE_SCOPES = frozenset(
    {
        "intake:write",
        "goals:write",
        "tasks:write",
        "devices:write",
        "execution:write",
        "execution:steer",
        "evals:write",
        "github:write",
        "approvals:decide",
    }
)

_MANAGE_SCOPES = frozenset({"auth:manage"})

ALL_SCOPES: frozenset[str] = _READ_SCOPES | _WRITE_SCOPES | _MANAGE_SCOPES

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "owner": ALL_SCOPES,
    "operator": _READ_SCOPES | _WRITE_SCOPES,
    "viewer": _READ_SCOPES,
}

VALID_ROLES = frozenset(ROLE_SCOPES)


def scopes_for_role(role: str) -> tuple[str, ...]:
    """未知角色一律按最低权限（viewer）处理，绝不放行。"""
    return tuple(sorted(ROLE_SCOPES.get(role, ROLE_SCOPES["viewer"])))
