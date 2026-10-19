"""W04 设备接入模块测试。

测试策略（与 CI 一致：backend job 没有 Postgres/Redis）：
- 用 FakeRepo（内存实现，与生产 DeviceRepository 同方法签名）+ FakeClock（可控时钟，不 sleep）
  覆盖 service 层全部业务规则；
- 用 FastAPI dependency_overrides 注入，走真实 HTTP 栈（TestClient）验证路由、
  认证（Owner Bearer / 设备 HMAC）、错误码与响应结构；
- 数据库层约束（UNIQUE(node_id,seq)、UPSERT、保留 30 份的单条 SQL）由生产 repository +
  alembic 迁移保证，CI 的 migrations job 做迁移 roundtrip。

对应窗口契约：plan/W04.md（W04-I01…W04-I06）。
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_token, hash_device_token, sign_hmac
from app.main import create_app
from app.modules.devices.deps import get_device_service
from app.modules.devices.repository import DeviceRecord
from app.modules.devices.service import DeviceService

API = "/api/v1/devices"
T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)
TS0 = int(T0.timestamp())


# =========================================================================
# 测试基础设施
# =========================================================================


class FakeClock:
    """可控时钟：service 的 now() 全部走它，测试里显式推进，绝不 sleep。"""

    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: int = 0, minutes: int = 0) -> None:
        self.now += timedelta(seconds=seconds, minutes=minutes)


class FakeRepo:
    """内存版 DeviceRepository：与生产实现保持同一方法签名（随 issue 逐步扩展）。"""

    def __init__(self) -> None:
        self.devices: dict[str, DeviceRecord] = {}
        self.codes: dict[str, dict] = {}
        self.heartbeats: dict[str, dict] = {}
        self.alerts: list[dict] = []
        self.events: dict[tuple[str, int], dict] = {}
        self.snapshots: dict[str, list[dict]] = {}
        self.cap_events: dict[str, list[dict]] = {}
        self.snap_seq = 0
        self.commands: dict[str, dict] = {}
        self.command_seq = 0

    async def commit(self) -> None:
        return None

    # ------------------------------------------------------------- 设备（I01）

    async def get_device(self, node_id: str) -> DeviceRecord | None:
        return self.devices.get(node_id)

    async def get_device_by_fingerprint(self, fingerprint: str) -> DeviceRecord | None:
        for record in self.devices.values():
            if record.fingerprint == fingerprint:
                return record
        return None

    async def create_device(self, record: DeviceRecord) -> DeviceRecord:
        self.devices[record.node_id] = record
        return record

    async def update_device(self, node_id: str, **fields: object) -> DeviceRecord | None:
        record = self.devices.get(node_id)
        if record is None:
            return None
        updated = record.with_updates(**fields)
        self.devices[node_id] = updated
        return updated

    async def list_devices(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[DeviceRecord]:
        rows = [r for r in self.devices.values() if status is None or r.status == status]
        rows.sort(key=lambda r: r.node_id)
        return rows[offset : offset + limit]

    # ------------------------------------------------------------- 引导码（I01）

    async def create_bootstrap_code(self, code_hash: str, expires_at: datetime | None) -> None:
        self.codes[code_hash] = {"expires_at": expires_at, "used_at": None, "used_by_node": None}

    async def bootstrap_code_valid(self, code_hash: str, *, now: datetime) -> bool:
        row = self.codes.get(code_hash)
        if row is None or row["used_at"] is not None:
            return False
        return row["expires_at"] is None or row["expires_at"] > now

    async def mark_bootstrap_used(self, code_hash: str, node_id: str, *, now: datetime) -> bool:
        row = self.codes.get(code_hash)
        if row is None or row["used_at"] is not None:
            return False
        row["used_at"] = now
        row["used_by_node"] = node_id
        return True

    # ------------------------------------------------------------- 心跳与状态机（I03）

    async def upsert_heartbeat(
        self,
        node_id: str,
        *,
        seq: int,
        metrics: dict,
        clock_skew_seconds: int,
        received_at: datetime,
    ) -> None:
        self.heartbeats[node_id] = {
            "node_id": node_id,
            "seq": seq,
            "metrics": dict(metrics),
            "clock_skew_seconds": clock_skew_seconds,
            "received_at": received_at,
        }

    async def get_heartbeat(self, node_id: str) -> dict | None:
        return self.heartbeats.get(node_id)

    async def insert_alert(self, node_id: str, kind: str, detail: dict) -> None:
        self.alerts.append({"node_id": node_id, "kind": kind, "detail": detail})

    async def sweep_candidates(self) -> list[DeviceRecord]:
        return [
            r
            for r in self.devices.values()
            if r.last_seen_at is not None and r.status != "revoked"
        ]

    # ------------------------------------------------------------- 增量事件（I04）

    async def insert_events(self, rows: list[dict]) -> int:
        inserted = 0
        for row in rows:
            key = (row["node_id"], row["seq"])
            if key in self.events:
                continue  # ON CONFLICT DO NOTHING
            self.events[key] = dict(row)
            inserted += 1
        return inserted

    async def list_events(self, node_id: str, *, since: int = 0, limit: int = 200) -> list[dict]:
        rows = [
            dict(row)
            for (nid, seq), row in sorted(self.events.items())
            if nid == node_id and seq > since
        ]
        return rows[:limit]

    # ------------------------------------------------------------- 快照（I05）

    async def insert_snapshot(
        self,
        node_id: str,
        *,
        ts: int,
        versions: dict,
        sessions: list[dict],
        caps: list[str],
        disk_used_gb: float,
        received_at: datetime,
    ) -> int:
        self.snap_seq += 1
        self.snapshots.setdefault(node_id, []).append(
            {
                "id": self.snap_seq,
                "node_id": node_id,
                "ts": ts,
                "versions": dict(versions),
                "sessions": [dict(s) for s in sessions],
                "caps": list(caps),
                "disk_used_gb": disk_used_gb,
                "received_at": received_at,
            }
        )
        return self.snap_seq

    async def prune_snapshots(self, node_id: str, *, keep: int = 30) -> int:
        rows = self.snapshots.get(node_id, [])
        if len(rows) <= keep:
            return 0
        removed = rows[:-keep]
        self.snapshots[node_id] = rows[-keep:]
        return len(removed)

    async def insert_cap_event(self, node_id: str, *, added: list[str], removed: list[str]) -> None:
        self.cap_events.setdefault(node_id, []).append({"added": added, "removed": removed})

    async def list_snapshots(self, node_id: str, *, limit: int = 30) -> list[dict]:
        rows = list(reversed(self.snapshots.get(node_id, [])))
        return rows[:limit]

    # ------------------------------------------------------------- 指令（I06）

    async def create_command(
        self, node_id: str, *, kind: str, payload: dict, approved_by: str | None
    ) -> dict:
        self.command_seq += 1
        row = {
            "id": f"cmd-{self.command_seq:04d}",
            "node_id": node_id,
            "kind": kind,
            "payload": dict(payload),
            "approved_by": approved_by,
            "status": "pending",
            "detail": "",
            "created_at": T0,
            "delivered_at": None,
            "acked_at": None,
        }
        self.commands[row["id"]] = row
        return dict(row)

    async def get_command(self, command_id: str) -> dict | None:
        row = self.commands.get(command_id)
        return dict(row) if row else None

    async def pull_pending_command(self, node_id: str, *, now: datetime) -> dict | None:
        pending = sorted(
            (r for r in self.commands.values() if r["node_id"] == node_id and r["status"] == "pending"),
            key=lambda r: r["created_at"],
        )
        if not pending:
            return None
        row = pending[0]
        row["status"] = "delivered"
        row["delivered_at"] = now
        return dict(row)

    async def ack_command(self, command_id: str, *, status: str, detail: str, now: datetime) -> dict | None:
        row = self.commands.get(command_id)
        if row is None:
            return None
        row["status"] = status
        row["detail"] = detail
        row["acked_at"] = now
        return dict(row)


class FakeState:
    """一次测试的全部可注入状态：clock + repo + service。"""

    def __init__(self, clock: FakeClock, repo: FakeRepo) -> None:
        self.clock = clock
        self.repo = repo
        self.service = DeviceService(repo, clock=clock)


def make_fake() -> FakeState:
    return FakeState(FakeClock(), FakeRepo())


@pytest.fixture()
def fake() -> FakeState:
    return make_fake()


@pytest.fixture()
def client(fake: FakeState) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_device_service] = lambda: fake.service
    return TestClient(app)


@pytest.fixture()
def owner_headers() -> dict[str, str]:
    token = create_token("owner-1", "access", extra={"role": "owner"})
    return {"Authorization": f"Bearer {token}"}


def issue_code(client: TestClient, owner_headers: dict[str, str]) -> str:
    r = client.post(f"{API}/bootstrap-codes", headers=owner_headers)
    assert r.status_code == 200, r.text
    return r.json()["code"]


def register_device(
    client: TestClient,
    fake: FakeState,
    owner_headers: dict[str, str],
    node_id: str = "mac-1",
    *,
    fingerprint: str | None = None,
    trust: str = "managed",
    caps: list[str] | None = None,
    code: str | None = None,
) -> dict:
    payload = {
        "bootstrap_code": code or issue_code(client, owner_headers),
        "node_id": node_id,
        "fingerprint": fingerprint or f"fp-{node_id}",
        "caps": caps if caps is not None else ["gui", "orca"],
        "tolerance": 300,
        "squad": "alpha",
        "purpose": "dev",
        "trust": trust,
    }
    r = client.post(f"{API}/register", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def device_post(
    client: TestClient,
    path: str,
    token: str,
    payload: dict,
    *,
    node: str | None = None,
    ts: int | None = None,
    key: str | None = None,
):
    """以设备身份发 HMAC 签名请求（签名密钥 = sha256(明文 token)，与生产派生规则一致）。

    注意：HMAC 头的时间戳用真实当前时间（验签窗口以服务端真实时钟为准）；
    payload 里的业务时间戳（如 ts/occurred_at）才用 FakeClock 的 TS0。
    """
    body = json.dumps(payload).encode()
    ts = int(time.time()) if ts is None else ts
    sig = sign_hmac(key or hash_device_token(token), body, ts)
    return client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Afc-Node": node or payload["node"],
            "X-Afc-Timestamp": str(ts),
            "X-Afc-Signature": sig,
        },
    )


# =========================================================================
# W04-I01 · 设备注册与 token
# =========================================================================


class TestRegister:
    def test_register_requires_bootstrap_code(self, client: TestClient, fake: FakeState) -> None:
        # 无凭据自助注册被拒绝：缺引导码 422，坏引导码 401
        r = client.post(f"{API}/register", json={"node_id": "mac-1", "fingerprint": "fp-mac-1"})
        assert r.status_code == 422
        r = client.post(
            f"{API}/register",
            json={"bootstrap_code": "no-such-code-000", "node_id": "mac-1", "fingerprint": "fp-mac-1"},
        )
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"

    def test_register_returns_token_once(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "mac-1")
        assert body["created"] is True
        token = body["token"]
        assert token and len(token) >= 32

        # 明文只出现一次：设备记录里只有 sha256
        record = fake.repo.devices["mac-1"]
        assert record.token_hash == hash_device_token(token)
        assert record.token_hash != token

        listed = client.get(f"{API}/", headers=owner_headers).json()
        assert len(listed) == 1
        assert "token" not in listed[0]
        assert "fingerprint" not in listed[0]  # 指纹不暴露给前端用于撞注册

    def test_register_idempotent_same_fingerprint(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        first = register_device(client, fake, owner_headers, "mac-1")
        second = register_device(client, fake, owner_headers, "mac-1")  # 同机同 node 重注册
        assert second["created"] is False
        assert second["token"] is None  # 不换发 token
        assert fake.repo.devices["mac-1"].token_hash == hash_device_token(first["token"])

    def test_register_fingerprint_conflict(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1", fingerprint="fp-same-machine")
        r = client.post(
            f"{API}/register",
            json={
                "bootstrap_code": issue_code(client, owner_headers),
                "node_id": "mac-2",
                "fingerprint": "fp-same-machine",  # 同一台机器伪装成第二个节点
            },
        )
        assert r.status_code == 409

    def test_register_node_id_taken(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1", fingerprint="fp-machine-a")
        r = client.post(
            f"{API}/register",
            json={
                "bootstrap_code": issue_code(client, owner_headers),
                "node_id": "mac-1",
                "fingerprint": "fp-machine-b",
            },
        )
        assert r.status_code == 409

    def test_bootstrap_code_is_single_use(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        code = issue_code(client, owner_headers)
        register_device(client, fake, owner_headers, "mac-1", code=code)
        r = client.post(
            f"{API}/register",
            json={"bootstrap_code": code, "node_id": "mac-2", "fingerprint": "fp-mac-2"},
        )
        assert r.status_code == 401  # 已消耗的引导码不能再用

    def test_register_third_party_trust(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "api-ext", trust="third_party", caps=["platform"])
        assert body["created"] is True
        assert fake.repo.devices["api-ext"].trust == "third_party"

    def test_patch_device_fields(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1")
        r = client.patch(
            f"{API}/mac-1",
            headers=owner_headers,
            json={"caps": ["gui"], "tolerance": 3600, "squad": "night", "purpose": "batch"},
        )
        assert r.status_code == 200
        record = fake.repo.devices["mac-1"]
        assert record.caps == ["gui"]
        assert record.tolerance == 3600
        assert record.squad == "night"

        r = client.patch(f"{API}/mac-1", headers=owner_headers, json={"trust": "bogus"})
        assert r.status_code == 422

    def test_revoke_device(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1")
        r = client.post(f"{API}/mac-1/revoke", headers=owner_headers)
        assert r.status_code == 200
        record = fake.repo.devices["mac-1"]
        assert record.status == "revoked"
        assert record.token_hash is None  # token 即刻失效
        assert record.revoked_at is not None

        r = client.post(f"{API}/mac-2/revoke", headers=owner_headers)
        assert r.status_code == 404

    def test_revoke_then_re_register_reactivates(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        first = register_device(client, fake, owner_headers, "mac-1")
        client.post(f"{API}/mac-1/revoke", headers=owner_headers)
        body = register_device(client, fake, owner_headers, "mac-1")
        assert body["reactivated"] is True
        assert body["token"] and body["token"] != first["token"]
        record = fake.repo.devices["mac-1"]
        assert record.status == "offline"
        assert record.token_hash == hash_device_token(body["token"])
        assert record.revoked_at is None


# =========================================================================
# W04-I02 · HMAC 验签中间件
# =========================================================================


def _starlette_request(body: bytes, headers: dict[str, str]):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/devices/heartbeat",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
    }

    async def receive():  # noqa: ANN202
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _signed_headers(token: str, payload: dict, *, ts: int | None = None, key: str | None = None) -> dict[str, str]:
    """构造 HMAC 头。ts 必须用真实当前时间（verify_hmac 的时间窗以服务端真实时钟为准）。"""
    body = json.dumps(payload).encode()
    ts = int(time.time()) if ts is None else ts
    return {
        "X-Afc-Node": payload["node"],
        "X-Afc-Timestamp": str(ts),
        "X-Afc-Signature": sign_hmac(key or hash_device_token(token), body, ts),
    }


class TestHmac:
    async def _verify(self, fake: FakeState, payload: dict, headers: dict[str, str], body: bytes | None = None):
        from app.modules.devices.hmac import verify_device_signature

        request = _starlette_request(body if body is not None else json.dumps(payload).encode(), headers)
        return await verify_device_signature(
            request,
            fake.service,
            x_afc_node=headers["X-Afc-Node"],
            x_afc_timestamp=int(headers["X-Afc-Timestamp"]),
            x_afc_signature=headers["X-Afc-Signature"],
        )

    async def test_valid_signature_passes(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "mac-1")
        payload = {"node": "mac-1", "ts": TS0, "seq": 1}
        # 密钥 = sha256(注册响应里的明文 token) —— 与设备端派生规则一致
        principal = await self._verify(fake, payload, _signed_headers(body["token"], payload))
        assert principal.node_id == "mac-1"
        assert principal.trust == "managed"

    async def test_tampered_body_rejected(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "mac-1")
        payload = {"node": "mac-1", "ts": TS0, "seq": 1}
        headers = _signed_headers(body["token"], payload)
        # 签名对 payload，实际发送被篡改的 body —— 必须拒绝
        from app.core.errors import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            await self._verify(fake, payload, headers, body=json.dumps({**payload, "seq": 2}).encode())

    async def test_stale_timestamp_rejected(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "mac-1")
        payload = {"node": "mac-1", "ts": TS0, "seq": 1}
        # 用正确密钥签，但时间戳超出 300s 窗口（相对服务端真实时钟）
        from app.core.errors import UnauthorizedError

        headers = _signed_headers(body["token"], payload, ts=int(time.time()) - 400)
        with pytest.raises(UnauthorizedError):
            await self._verify(fake, payload, headers, body=json.dumps(payload).encode())

    async def test_wrong_key_rejected(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1")
        payload = {"node": "mac-1", "ts": TS0, "seq": 1}
        from app.core.errors import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            await self._verify(fake, payload, _signed_headers("wrong-key-not-the-token", payload))

    async def test_unknown_node_same_error_as_bad_signature(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        """防枚举：未知节点与签名错误必须是同一个错误、同一文案。"""
        from app.core.errors import UnauthorizedError

        payload = {"node": "ghost", "ts": TS0, "seq": 1}
        with pytest.raises(UnauthorizedError) as e_unknown:
            await self._verify(fake, payload, _signed_headers("whatever-key", payload))
        register_device(client, fake, owner_headers, "mac-1")
        payload2 = {"node": "mac-1", "ts": TS0, "seq": 1}
        with pytest.raises(UnauthorizedError) as e_badsig:
            await self._verify(fake, payload2, _signed_headers("whatever-key", payload2))
        assert str(e_unknown.value) == str(e_badsig.value)

    async def test_revoked_device_rejected(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "mac-1")
        client.post(f"{API}/mac-1/revoke", headers=owner_headers)
        payload = {"node": "mac-1", "ts": TS0, "seq": 1}
        from app.core.errors import UnauthorizedError

        with pytest.raises(UnauthorizedError):
            await self._verify(fake, payload, _signed_headers(body["token"], payload))

    async def test_rate_limit_after_10_failures(self, fake: FakeState, client: TestClient, owner_headers, monkeypatch) -> None:
        register_device(client, fake, owner_headers, "mac-1")

        calls = {"n": 0}

        async def fake_incr(key: str, ttl: int) -> int:
            calls["n"] += 1
            return calls["n"]

        monkeypatch.setattr("app.modules.devices.hmac.incr_with_ttl", fake_incr)

        from app.core.errors import RateLimitedError, UnauthorizedError
        from app.modules.devices.hmac import HMAC_FAIL_LIMIT_PER_MINUTE

        payload = {"node": "mac-1", "ts": TS0, "seq": 1}
        headers = _signed_headers("bad-key", payload)
        for _ in range(HMAC_FAIL_LIMIT_PER_MINUTE - 1):
            with pytest.raises(UnauthorizedError):
                await self._verify(fake, payload, headers)
        with pytest.raises(RateLimitedError):  # 第 10 次失败 → 429
            await self._verify(fake, payload, headers)

    async def test_third_party_can_report(self, fake: FakeState, client: TestClient, owner_headers) -> None:
        """第三方设备能正常验签上报（它只是不接收指令，I06 验证）。"""
        body = register_device(client, fake, owner_headers, "api-ext", trust="third_party")
        payload = {"node": "api-ext", "ts": TS0, "seq": 1}
        principal = await self._verify(fake, payload, _signed_headers(body["token"], payload))
        assert principal.trust == "third_party"


# =========================================================================
# W04-I03 · 心跳与设备状态机
# =========================================================================


def send_heartbeat(
    client: TestClient,
    fake: FakeState,
    owner_headers,
    node_id: str = "mac-1",
    *,
    token: str | None = None,
    **overrides,
) -> dict:
    """发一次心跳；token 为空时先注册。返回心跳响应。"""
    if token is None:
        token = register_device(client, fake, owner_headers, node_id)["token"]
    payload = {"node": node_id, "ts": TS0, "seq": 1, "load": 0.4, "mem": 60, "disk": 70,
               "battery": -1, "sessions": 2, "queue": 0, "agent_ver": "0.1.0"}
    payload.update(overrides)
    r = device_post(client, f"{API}/heartbeat", token, payload)
    assert r.status_code == 200, r.text
    return r.json()


def run_sweep(fake: FakeState) -> dict:
    """在独立事件循环里跑一轮巡检（等价于 scripts/sweep_devices.py --once）。"""
    import asyncio

    return asyncio.run(fake.service.sweep())


class TestHeartbeat:
    def test_heartbeat_upserts_single_row(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        send_heartbeat(client, fake, owner_headers, token=token, seq=1)
        send_heartbeat(client, fake, owner_headers, token=token, seq=2, load=0.9)
        # UPSERT：一节点一行（不是追加日志）
        assert len(fake.repo.heartbeats) == 1
        hb = fake.repo.heartbeats["mac-1"]
        assert hb["seq"] == 2
        assert hb["metrics"]["load"] == 0.9

        record = fake.repo.devices["mac-1"]
        assert record.status == "online"
        assert record.last_seen_at == T0
        assert record.missed_heartbeats == 0
        assert record.agent_version == "0.1.0"

    def test_node_mismatch_rejected(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        body = register_device(client, fake, owner_headers, "mac-1")
        payload = {"node": "mac-2", "ts": TS0, "seq": 1}  # 签名是 mac-1，内容冒充 mac-2
        r = device_post(client, f"{API}/heartbeat", body["token"], payload, node="mac-1")
        assert r.status_code == 403

    def test_suspect_after_3_missed_heartbeats(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        send_heartbeat(client, fake, owner_headers)
        # 丢 3 次（60s 间隔 × 3）→ suspect，必须立即停止派新活（W03 读 status）
        fake.clock.advance(minutes=3)
        result = run_sweep(fake)
        assert result["transitions"]["suspect"] == 1
        assert fake.repo.devices["mac-1"].status == "suspect"

        status = client.get(f"{API}/mac-1/status", headers=owner_headers).json()
        assert status["effective_status"] == "suspect"
        assert status["missed_heartbeats"] == 3

    def test_offline_after_5_minutes_emits_lease_reclaim(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        send_heartbeat(client, fake, owner_headers)
        fake.clock.advance(minutes=6)  # 丢 5 分钟 → offline
        result = run_sweep(fake)
        assert result["transitions"]["offline"] == 1
        record = fake.repo.devices["mac-1"]
        assert record.status == "offline"

        # 租约回收只发事件，不直接改 tasks 表
        kinds = [a["kind"] for a in fake.repo.alerts]
        assert "lease_reclaim" in kinds

    def test_recovery_back_online(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        send_heartbeat(client, fake, owner_headers, token=token)
        fake.clock.advance(minutes=3)
        run_sweep(fake)
        assert fake.repo.devices["mac-1"].status == "suspect"

        # 心跳恢复 → 立即回 online，missed 归零
        send_heartbeat(client, fake, owner_headers, token=token, seq=9)
        record = fake.repo.devices["mac-1"]
        assert record.status == "online"
        assert record.missed_heartbeats == 0

    def test_clock_skew_flagged_and_alerted(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        # 设备时钟慢了 10 分钟：服务端 received_at 为准，偏差打标 + 告警
        resp = send_heartbeat(client, fake, owner_headers, ts=TS0 - 600)
        assert resp["clock_skew_seconds"] == 600
        assert any(a["kind"] == "clock_skew" for a in fake.repo.alerts)
        hb = fake.repo.heartbeats["mac-1"]
        assert hb["clock_skew_seconds"] == 600

    def test_status_endpoint_not_found(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        r = client.get(f"{API}/ghost/status", headers=owner_headers)
        assert r.status_code == 404


# =========================================================================
# W04-I04 · 增量事件（幂等 + 游标）
# =========================================================================


def post_events(
    client: TestClient,
    fake: FakeState,
    owner_headers,
    node_id: str = "mac-1",
    *,
    token: str | None = None,
    from_seq: int = 1,
    kinds: list[str] | None = None,
    dropped: int = 0,
) -> dict:
    if token is None:
        token = register_device(client, fake, owner_headers, node_id)["token"]
    kinds = kinds if kinds is not None else ["task_started", "task_completed"]
    events = [
        {"kind": k, "task": "t-1", "model": "glm-5", "cost": 0.01, "ms": 100,
         "occurred_at": TS0 + i}
        for i, k in enumerate(kinds)
    ]
    payload = {"node": node_id, "from_seq": from_seq,
               "to_seq": from_seq + len(events) - 1, "events": events, "dropped": dropped}
    r = device_post(client, f"{API}/events", token, payload)
    assert r.status_code == 200, r.text
    return r.json()


class TestEvents:
    def test_events_idempotent_same_batch_three_times(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        first = post_events(client, fake, owner_headers, token=token, from_seq=1)
        assert first["accepted"] == 2 and first["duplicates"] == 0

        # 同一批次重复投递 3 次：事件表行数不变，重复计入 duplicates 而非 409
        for _ in range(3):
            again = post_events(client, fake, owner_headers, token=token, from_seq=1)
            assert again["accepted"] == 0
            assert again["duplicates"] == 2
        assert len(fake.repo.events) == 2  # 幂等对客户端无害

    def test_unknown_kind_counted_and_accepted(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        resp = post_events(client, fake, owner_headers, kinds=["task_started", "boom_kind"])
        assert resp["rejected"] == 1  # 未知 kind 拒收并计数，不报 4xx
        assert resp["accepted"] == 1
        kinds = [e["kind"] for e in fake.repo.events.values()]
        assert "boom_kind" not in kinds

    def test_dropped_count_writes_alert(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        post_events(client, fake, owner_headers, dropped=7)
        assert any(
            a["kind"] == "buffer_dropped" and a["detail"]["dropped"] == 7
            for a in fake.repo.alerts
        )

    def test_batch_over_500_rejected(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        events = [{"kind": "span"} for _ in range(501)]
        payload = {"node": "mac-1", "from_seq": 1, "to_seq": 501, "events": events}
        r = device_post(client, f"{API}/events", token, payload)
        assert r.status_code == 422

    def test_node_mismatch_rejected(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        payload = {"node": "mac-2", "from_seq": 1, "to_seq": 1, "events": [{"kind": "span"}]}
        r = device_post(client, f"{API}/events", token, payload, node="mac-1")
        assert r.status_code == 403

    def test_events_since_cursor(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        post_events(client, fake, owner_headers, token=token, from_seq=1, kinds=["task_started", "task_completed"])
        post_events(client, fake, owner_headers, token=token, from_seq=3, kinds=["span"])

        page1 = client.get(f"{API}/mac-1/events?since=0&limit=2", headers=owner_headers).json()
        assert [e["seq"] for e in page1["events"]] == [1, 2]
        assert page1["next_since"] == 2

        page2 = client.get(f"{API}/mac-1/events?since=2", headers=owner_headers).json()
        assert [e["seq"] for e in page2["events"]] == [3]

        # 设备游标（last_event_seq）随上报推进
        assert fake.repo.devices["mac-1"].last_event_seq == 3


# =========================================================================
# W04-I05 · 快照与能力变更
# =========================================================================


def post_snapshot(
    client: TestClient,
    fake: FakeState,
    owner_headers,
    node_id: str = "mac-1",
    *,
    token: str | None = None,
    caps: list[str] | None = None,
) -> dict:
    if token is None:
        token = register_device(client, fake, owner_headers, node_id)["token"]
    payload = {
        "node": node_id,
        "ts": TS0,
        "versions": {"agent": "0.1.0", "orca": "1.2", "skills": {"commit": "1.0"}},
        "sessions": [{"id": "s-1", "task": "t-1", "state": "running", "worktree": "wt-1"}],
        "caps": caps if caps is not None else ["gui", "orca"],
        "disk_used_gb": 320.5,
    }
    r = device_post(client, f"{API}/snapshot", token, payload)
    assert r.status_code == 200, r.text
    return r.json()


class TestSnapshot:
    def test_snapshot_ingest_and_list(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        resp = post_snapshot(client, fake, owner_headers)
        assert resp["snapshot_id"] == 1
        assert resp["pruned"] == 0

        record = fake.repo.devices["mac-1"]
        assert record.woke_at == T0  # 收到快照即标记 woke_at
        assert record.last_snapshot_at == T0
        assert record.caps == ["gui", "orca"]

        listed = client.get(f"{API}/mac-1/snapshots", headers=owner_headers).json()
        assert len(listed) == 1
        assert listed[0]["versions"]["agent"] == "0.1.0"
        assert listed[0]["caps"] == ["gui", "orca"]

    def test_retention_keeps_30(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        for _ in range(33):
            post_snapshot(client, fake, owner_headers, token=token)
        # 只保留最近 30 份，超限删最旧
        assert len(fake.repo.snapshots["mac-1"]) == 30
        ids = [s["id"] for s in fake.repo.snapshots["mac-1"]]
        assert ids[0] == 4  # 最旧的 1-3 被删
        assert ids[-1] == 33

    def test_cap_change_writes_cap_events(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        post_snapshot(client, fake, owner_headers, token=token, caps=["gui", "orca"])
        post_snapshot(client, fake, owner_headers, token=token, caps=["gui", "browser"])
        events = fake.repo.cap_events["mac-1"]
        # 第一次快照与注册时能力一致 → 不产生变更事件；第二次才有
        assert len(events) == 1
        assert events[0]["added"] == ["browser"]
        assert events[0]["removed"] == ["orca"]
        # 设备当前能力同步更新（W03 派活匹配读这里）
        assert fake.repo.devices["mac-1"].caps == ["gui", "browser"]

    def test_backfill_events_after_snapshot_are_flagged(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        """唤醒后先快照再增量：晚到但 occurred_at 更早的增量不打乱当前状态。"""
        token = register_device(client, fake, owner_headers, "mac-1")["token"]
        # 休眠前的事件（occurred_at 在快照之前）
        stale = {"node": "mac-1", "from_seq": 1, "to_seq": 1,
                 "events": [{"kind": "task_started", "occurred_at": TS0 - 3600}]}
        r = device_post(client, f"{API}/events", token, stale)
        assert r.status_code == 200

        post_snapshot(client, fake, owner_headers, token=token)  # 唤醒，先发快照
        late = {"node": "mac-1", "from_seq": 2, "to_seq": 2,
                "events": [{"kind": "task_completed", "occurred_at": TS0 - 3500}]}
        r = device_post(client, f"{API}/events", token, late)
        assert r.status_code == 200

        stored = sorted(fake.repo.events.values(), key=lambda e: e["seq"])
        assert stored[0]["is_backfill"] is False  # 快照前的事件，无快照可比
        assert stored[1]["is_backfill"] is True  # 快照之后到达但 occurred_at 更早 → 回填标
        # 当前能力/快照视图不被早到的增量破坏
        assert fake.repo.devices["mac-1"].last_snapshot_at is not None


def device_pull(client: TestClient, token: str, node_id: str, *, wait: int = 0):
    """设备长轮询拉指令：GET 无 body，签名覆盖空 body。"""
    return device_signed_request(client, "GET", f"{API}/{node_id}/commands?wait={wait}", token, node_id)


def device_signed_request(
    client: TestClient, method: str, path: str, token: str, node_id: str, payload: dict | None = None
):
    """以设备身份发 HMAC 签名请求（body 参与签名；GET 视为空 body）。"""
    body = json.dumps(payload).encode() if payload is not None else b""
    ts = int(time.time())
    sig = sign_hmac(hash_device_token(token), body, ts)
    kwargs: dict = {"headers": {
        "Content-Type": "application/json",
        "X-Afc-Node": node_id,
        "X-Afc-Timestamp": str(ts),
        "X-Afc-Signature": sig,
    }}
    if payload is not None:
        kwargs["content"] = body
    return client.request(method, path, **kwargs)


# =========================================================================
# W04-I06 · 指令下发白名单
# =========================================================================


class TestCommands:
    def test_create_command_whitelist_only(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1")
        # 白名单五种都允许（低风险不需要审批人）
        r = client.post(f"{API}/mac-1/commands", headers=owner_headers, json={"kind": "collect_snapshot"})
        assert r.status_code == 200
        # 白名单之外（包括任意 shell）一律 422 —— 这是编译进 Literal 类型的硬约束
        for bad in ("run_shell", "exec", "rm -rf /"):
            r = client.post(f"{API}/mac-1/commands", headers=owner_headers, json={"kind": bad})
            assert r.status_code == 422

    def test_medium_risk_requires_approval(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "mac-1")
        r = client.post(f"{API}/mac-1/commands", headers=owner_headers, json={"kind": "restart_agent"})
        assert r.status_code == 409  # 未审批的中风险指令

        r = client.post(
            f"{API}/mac-1/commands",
            headers=owner_headers,
            json={"kind": "restart_agent", "approved_by": "owner-1"},
        )
        assert r.status_code == 200
        assert next(iter(fake.repo.commands.values()))["approved_by"] == "owner-1"

        r = client.post(f"{API}/mac-1/commands", headers=owner_headers, json={"kind": "run_task"})
        assert r.status_code == 409
        r = client.post(
            f"{API}/mac-1/commands",
            headers=owner_headers,
            json={"kind": "run_task", "payload": {"task_id": "t-9"}, "approved_by": "owner-1"},
        )
        assert r.status_code == 200

    def test_third_party_never_receives_commands(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        register_device(client, fake, owner_headers, "api-ext", trust="third_party")
        # 控制台下发 → 403
        r = client.post(f"{API}/api-ext/commands", headers=owner_headers, json={"kind": "collect_snapshot"})
        assert r.status_code == 403

        # 设备自己来长轮询拉 → 403
        code = issue_code(client, owner_headers)
        reg = client.post(
            f"{API}/register",
            json={"bootstrap_code": code, "node_id": "api-2", "fingerprint": "fp-api-2",
                  "trust": "third_party"},
        )
        r = device_pull(client, reg.json()["token"], "api-2", wait=0)
        assert r.status_code == 403

    def test_long_poll_deliver_and_ack(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        reg = register_device(client, fake, owner_headers, "mac-1")
        client.post(
            f"{API}/mac-1/commands",
            headers=owner_headers,
            json={"kind": "run_task", "payload": {"task_id": "t-1"}, "approved_by": "owner-1"},
        )
        # 设备长轮询领取（wait=0 立即返回）
        r = device_pull(client, reg["token"], "mac-1", wait=0)
        assert r.status_code == 200
        command = r.json()
        assert command["kind"] == "run_task"
        assert command["status"] == "delivered"

        # 领取后队列里没有 pending → 204（不用 WebSocket）
        r = device_pull(client, reg["token"], "mac-1", wait=0)
        assert r.status_code == 204

        # 回执（签名覆盖 body）
        r = device_signed_request(
            client, "POST", f"{API}/commands/{command['id']}/ack", reg["token"], "mac-1",
            {"status": "acked", "detail": "done"},
        )
        assert r.status_code == 200
        assert fake.repo.commands[command["id"]]["status"] == "acked"
        assert fake.repo.commands[command["id"]]["detail"] == "done"

    def test_ack_other_device_command_rejected(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        reg1 = register_device(client, fake, owner_headers, "mac-1")
        register_device(client, fake, owner_headers, "mac-2")
        client.post(f"{API}/mac-1/commands", headers=owner_headers, json={"kind": "drain"})
        r = device_pull(client, reg1["token"], "mac-1", wait=0)
        command_id = r.json()["id"]

        reg2 = client.post(
            f"{API}/register",
            json={"bootstrap_code": issue_code(client, owner_headers), "node_id": "mac-3",
                  "fingerprint": "fp-mac-3"},
        )
        # mac-3 用自己的合法签名 ack mac-1 的指令 → 403
        r = device_signed_request(
            client, "POST", f"{API}/commands/{command_id}/ack", reg2.json()["token"], "mac-3",
            {"status": "acked"},
        )
        assert r.status_code == 403
        assert fake.repo.commands[command_id]["status"] == "delivered"

    def test_command_for_unknown_device_404(self, client: TestClient, fake: FakeState, owner_headers) -> None:
        r = client.post(f"{API}/ghost/commands", headers=owner_headers, json={"kind": "drain"})
        assert r.status_code == 404
