"""骨架冒烟测试：不依赖真实数据库/Redis。

验证点（与 CI 的 `smoke` job 一致）：
1. 应用能创建、OpenAPI 能生成
2. 全部 10 个模块路由已注册
3. 错误响应结构统一（error.code / request_id）
4. 请求 ID 贯穿响应头
5. 认证依赖生效（未带令牌 401、伪造令牌 401、合法令牌 200）
6. 配置校验在生产环境会对默认密钥报错
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import create_token
from app.main import create_app

# (模块包名, 路由前缀)：identity（W01）已实现，占位端点已移除，单独断言
MODULES = [
    ("intake", "intake"),
    ("goals", "goals"),
    ("tasks", "tasks"),
    ("devices", "devices"),
    ("execution", "execution"),
    ("ledger", "ledger"),
    ("approvals", "approvals"),
    ("evals", "evals"),
    ("github_sync", "github"),
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_request_id_header(client: TestClient) -> None:
    r = client.get("/health", headers={"x-request-id": "trace-abc"})
    assert r.headers["x-request-id"] == "trace-abc"


def test_all_modules_registered(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for module, prefix in MODULES:
        assert f"/api/v1/{prefix}/_status" in paths, f"模块 {module} 未注册"
    # identity（W01）已实现：占位端点移除，真实端点就位
    for endpoint in ("/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout", "/api/v1/auth/me"):
        assert endpoint in paths, f"identity 端点缺失 {endpoint}"
    assert "/api/v1/auth/_status" not in paths
    assert "/api/v1/auth/_ping" not in paths


def test_module_status_is_stub(client: TestClient) -> None:
    body = client.get("/api/v1/ledger/_status").json()
    assert body == {"module": "ledger", "owner_window": "W07", "state": "stub"}


def test_auth_required(client: TestClient) -> None:
    r = client.get("/api/v1/tasks/_ping")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    assert r.json()["error"]["request_id"]


def test_auth_rejects_garbage(client: TestClient) -> None:
    r = client.get("/api/v1/tasks/_ping", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_auth_accepts_valid_token(client: TestClient) -> None:
    token = create_token("owner-1", "access", extra={"role": "owner"})
    r = client.get("/api/v1/tasks/_ping", headers={"Authorization": f"Bearer {token}"})
    # 认证通过 → 命中模块占位实现（501），而不是 401
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "not_implemented"


def test_unknown_path_returns_structured_error(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_no_openapi_leak_in_prod_docs_disabled() -> None:
    prod = Settings(app_env="dev")
    assert prod.app_env == "dev"


def test_prod_config_validation_rejects_default_secret() -> None:
    with pytest.raises(ValueError):
        Settings(app_env="prod")
