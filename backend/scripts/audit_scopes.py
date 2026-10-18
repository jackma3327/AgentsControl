#!/usr/bin/env python3
"""审计所有路由的认证与 scope 挂载情况（W01-I02）。

规则：
1. 每个路由必须挂认证（依赖链中出现 core.deps.get_current_owner），
   豁免：健康检查、/auth/login、/auth/refresh（凭据端点本身无令牌）、
   并行开发期的 /_status 占位端点；
2. 每个写端点（POST/PATCH/PUT/DELETE）必须挂 scope
   （依赖链中出现带 __required_scope__ 标记的依赖，见 core.deps.require_scope），
   豁免：/auth/logout（自服务操作，任何已认证角色都可登出自己）。

用法：
    python scripts/audit_scopes.py              # 报告模式：输出报告，违规也退出 0
    python scripts/audit_scopes.py --strict     # 门禁模式：有违规退出 1（供 CI 接线）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.routing import APIRoute  # noqa: E402

from app.core.deps import get_current_owner  # noqa: E402
from app.main import create_app  # noqa: E402

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

# 豁免清单（有意不挂认证/不挂 scope 的端点，改动需评审）
AUTH_EXEMPT_PREFIXES = ("/health", "/ready", "/version", "/docs", "/redoc", "/openapi.json")
AUTH_EXEMPT_ROUTES = {"/api/v1/auth/login", "/api/v1/auth/refresh"}
STUB_SUFFIX = "/_status"  # 并行开发期占位端点
SCOPE_EXEMPT_ROUTES = {"/api/v1/auth/logout"}  # 自服务操作


def _walk_calls(dependant: Any) -> set[Any]:
    """递归收集依赖链上的全部可调用对象（含子依赖）。"""
    calls: set[Any] = {dependant.call}
    for sub in dependant.dependencies:
        calls |= _walk_calls(sub)
    return calls


def _expand_route(item: Any, prefix: str) -> list[tuple[str, Any]]:
    """兼容两种路由形态：经典扁平（FastAPI<0.145）与嵌套 _IncludedRouter。"""
    if isinstance(item, APIRoute):
        return [(prefix + item.path, item)]
    original = getattr(item, "original_router", None)
    ctx = getattr(item, "include_context", None)
    if original is not None and ctx is not None:
        return [
            expanded
            for sub in original.routes
            for expanded in _expand_route(sub, prefix + (ctx.prefix or ""))
        ]
    path = getattr(item, "path", None)
    return [(prefix + path, item)] if path else []


def collect_routes(app: Any) -> list[tuple[str, APIRoute]]:
    """展开 app 全部路由为 (完整路径, APIRoute)，兼容任意 include 嵌套深度。"""
    out: list[tuple[str, APIRoute]] = []
    for item in app.routes:
        out.extend(_expand_route(item, ""))
    return out


def route_calls(route: APIRoute) -> set[Any]:
    calls = _walk_calls(route.dependant)
    for d in getattr(route, "dependencies", None) or []:
        dep = getattr(d, "dependant", None)  # 旧版 FastAPI：Dependant 对象
        if dep is not None:
            calls |= _walk_calls(dep)
            continue
        dep_call = getattr(d, "dependency", None)  # 新版 FastAPI：Depends 对象
        if dep_call is not None:
            calls.add(dep_call)
            for sub in getattr(d, "dependencies", None) or []:
                sub_call = getattr(sub, "dependency", None) or getattr(sub, "call", None)
                if sub_call is not None:
                    calls.add(sub_call)
    return calls


def required_scopes(calls: set[Any]) -> set[str]:
    return {
        c.__required_scope__
        for c in calls
        if hasattr(c, "__required_scope__")
    }


def audit() -> list[str]:
    app = create_app()
    problems: list[str] = []
    authed_routes = 0

    for path, route in collect_routes(app):
        if path.endswith(STUB_SUFFIX) or any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
            continue

        calls = route_calls(route)
        has_auth = get_current_owner in calls or bool(required_scopes(calls))

        for method in route.methods or set():
            if method in {"OPTIONS", "HEAD"}:
                continue
            label = f"{method} {path}"
            if path in AUTH_EXEMPT_ROUTES:
                continue
            if not has_auth:
                problems.append(f"[未挂认证] {label}")
                continue
            authed_routes += 1
            if method in WRITE_METHODS and path not in SCOPE_EXEMPT_ROUTES:
                if not required_scopes(calls):
                    problems.append(f"[写端点未挂 scope] {label}")

    print(f"路由审计：{authed_routes} 个已认证路由")
    if problems:
        print(f"\n发现 {len(problems)} 处违规：")
        for p in problems:
            print(f"  {p}")
    else:
        print("认证与 scope 挂载全部合规。")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="有违规时退出 1（CI 门禁）")
    args = parser.parse_args()

    problems = audit()
    if problems and args.strict:
        print("\n--strict：存在违规，阻断。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
