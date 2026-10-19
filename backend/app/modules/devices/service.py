"""设备接入业务逻辑（I01：注册与 token；I03–I06 逐步扩展心跳/事件/快照/指令）。

分层约定：
- 不依赖 Request/Response，只操作领域对象与 Pydantic 模型；
- 多步写入放在一个事务里（末尾统一 `commit()`）；
- 状态机与时钟相关计算全部经过注入的 `clock`，测试可控、不 sleep。

三通道语义（TECH-ARCHITECTURE §0.2 / §4.3，任何一条做错都等于设备已死）：
- 心跳 60s：丢 3 次 → suspect（停止派新活）；丢 5 分钟 → offline（回收租约，只发事件）；
- 增量 30s 批量 + 游标：幂等基础是 (node_id, seq) 唯一索引；
- 快照 60min：收到即标记 woke_at，早于快照的增量不打乱（is_backfill）。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.security import hash_device_token

from .commands import COMMAND_KINDS, MEDIUM_RISK_KINDS
from .repository import DeviceRecord, DeviceRepository
from .schemas import TRUST_VALUES, EventBatchIn, HeartbeatIn, RegisterIn, SnapshotIn

#: 引导码默认有效期（人工签发，一次性使用）
BOOTSTRAP_CODE_TTL_DAYS = 7
#: 设备时钟偏差告警阈值（秒）
CLOCK_SKEW_ALERT_SECONDS = 300
#: 时钟偏差告警的 kind
ALERT_CLOCK_SKEW = "clock_skew"
#: offline 触发租约回收的 kind（只发事件，不直接改 tasks 表 —— 那是 W03 的职责）
ALERT_LEASE_RECLAIM = "lease_reclaim"
#: 缓冲区丢弃告警的 kind（dropped_count > 0 必须上报，静默丢是红线）
ALERT_BUFFER_DROPPED = "buffer_dropped"

#: 增量事件 kind 白名单（未知 kind 拒收并计数，不报 4xx，避免设备端整体重传）
EVENT_KINDS = (
    "task_started",
    "task_progress",
    "task_completed",
    "task_failed",
    "session_started",
    "session_ended",
    "span",
    "cost",
    "error",
)
_ALLOWED_PATCH_FIELDS = ("caps", "tolerance", "squad", "purpose", "trust")


@dataclass(slots=True)
class RegisterResult:
    node_id: str
    token: str | None
    created: bool
    reactivated: bool


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeviceService:
    def __init__(
        self, repo: DeviceRepository, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._repo = repo
        self._clock = clock or _utcnow

    # ------------------------------------------------------------- I01 注册与 token

    async def issue_bootstrap_code(self, *, ttl_days: int = BOOTSTRAP_CODE_TTL_DAYS) -> tuple[str, datetime | None]:
        """人工签发引导码：明文只在响应中出现一次，库里只存 sha256。"""
        raw = secrets.token_urlsafe(24)
        expires_at = self._clock() + timedelta(days=ttl_days) if ttl_days > 0 else None
        await self._repo.create_bootstrap_code(hash_device_token(raw), expires_at)
        await self._repo.commit()
        return raw, expires_at

    async def register(self, payload: RegisterIn) -> RegisterResult:
        """注册。

        - 必须持有效引导码（一次性、未过期）——不允许无凭据自助注册；
        - (指纹, node_id) 已存在且未吊销 → 幂等返回既有节点，不签发新 token；
        - 指纹已绑定其它 node_id → 409（防止一台机器伪装成两个节点）；
        - 已吊销的设备凭新引导码重注册 → 重激活并换发新 token（旧 token 已被清空）。
        """
        now = self._clock()
        code_hash = hash_device_token(payload.bootstrap_code)
        if not await self._repo.bootstrap_code_valid(code_hash, now=now):
            raise UnauthorizedError("引导码无效、已使用或已过期")

        existing = await self._repo.get_device_by_fingerprint(payload.fingerprint)
        if existing is not None:
            if existing.node_id != payload.node_id:
                raise ConflictError("该机器指纹已绑定其它节点")
            if existing.status == "revoked":
                # 吊销后重注册：换发新 token，重新上线
                token = secrets.token_urlsafe(32)
                await self._repo.update_device(
                    existing.node_id,
                    token_hash=hash_device_token(token),
                    status="offline",
                    revoked_at=None,
                    missed_heartbeats=0,
                )
                await self._repo.mark_bootstrap_used(code_hash, payload.node_id, now=now)
                await self._repo.commit()
                return RegisterResult(payload.node_id, token, created=False, reactivated=True)
            # 幂等：同指纹同 node_id 重复注册，返回既有节点，不消耗引导码
            return RegisterResult(existing.node_id, None, created=False, reactivated=False)

        clash = await self._repo.get_device(payload.node_id)
        if clash is not None:
            raise ConflictError("node_id 已被其它机器占用")

        token = secrets.token_urlsafe(32)
        record = DeviceRecord(
            node_id=payload.node_id,
            fingerprint=payload.fingerprint,
            token_hash=hash_device_token(token),
            trust=payload.trust,
            purpose=payload.purpose,
            squad=payload.squad,
            tolerance=payload.tolerance,
            caps=list(payload.caps),
        )
        await self._repo.create_device(record)
        await self._repo.mark_bootstrap_used(code_hash, payload.node_id, now=now)
        await self._repo.commit()
        return RegisterResult(payload.node_id, token, created=True, reactivated=False)

    async def list_devices(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[DeviceRecord]:
        if status is not None and status not in ("online", "suspect", "offline", "revoked"):
            raise ValidationError("status 取值非法")
        return await self._repo.list_devices(status=status, limit=limit, offset=offset)

    async def update_device(self, node_id: str, patch: dict) -> DeviceRecord:
        device = await self._require_device(node_id)
        updates = {k: v for k, v in patch.items() if k in _ALLOWED_PATCH_FIELDS and v is not None}
        if not updates:
            return device
        if "trust" in updates and updates["trust"] not in TRUST_VALUES:
            raise ValidationError("trust 取值非法")
        updated = await self._repo.update_device(node_id, **updates)
        assert updated is not None
        await self._repo.commit()
        return updated

    async def revoke_device(self, node_id: str) -> DeviceRecord:
        """吊销设备：token_hash 置空即刻失效（HMAC 验签拿不到密钥），状态转 revoked。"""
        await self._require_device(node_id)
        updated = await self._repo.update_device(
            node_id, status="revoked", revoked_at=self._clock(), token_hash=None
        )
        assert updated is not None
        await self._repo.commit()
        return updated

    async def get_device(self, node_id: str) -> DeviceRecord:
        return await self._require_device(node_id)

    async def get_device_for_auth(self, node_id: str) -> DeviceRecord | None:
        """供 HMAC 验签依赖使用：不抛错，把"存在性"折叠进签名校验防枚举。"""
        return await self._repo.get_device(node_id)

    async def _require_device(self, node_id: str) -> DeviceRecord:
        device = await self._repo.get_device(node_id)
        if device is None:
            raise NotFoundError("设备不存在")
        return device

    def _require_not_third_party(self, device: DeviceRecord, action: str) -> None:
        if device.trust == "third_party":
            raise ForbiddenError(f"第三方设备不接收{action}")

    # ------------------------------------------------------------- I03 心跳与状态机

    async def heartbeat(self, payload: HeartbeatIn) -> dict:
        """心跳：UPSERT 单条 SQL + 状态回 online。时钟以服务端 received_at 为准。"""
        now = self._clock()
        device = await self._require_device(payload.node)
        # third_party 设备可以上报数据，只是永不接收指令（I06 的下发/长轮询会 403）

        skew = int(now.timestamp() - payload.ts)
        if abs(skew) > CLOCK_SKEW_ALERT_SECONDS:
            await self._repo.insert_alert(
                payload.node,
                ALERT_CLOCK_SKEW,
                {"skew_seconds": skew, "device_ts": payload.ts, "seq": payload.seq},
            )
        await self._repo.upsert_heartbeat(
            payload.node,
            seq=payload.seq,
            metrics={
                "load": payload.load,
                "mem": payload.mem,
                "disk": payload.disk,
                "battery": payload.battery,
                "sessions": payload.sessions,
                "queue": payload.queue,
                "agent_ver": payload.agent_ver,
            },
            clock_skew_seconds=skew,
            received_at=now,
        )
        # 恢复即回 online（哪怕之前是 suspect/offline）
        await self._repo.update_device(
            payload.node,
            status="online",
            missed_heartbeats=0,
            last_seen_at=now,
            agent_version=payload.agent_ver or device.agent_version,
        )
        await self._repo.commit()
        return {
            "node_id": payload.node,
            "status": "online",
            "received_at": now,
            "clock_skew_seconds": skew,
        }

    async def device_status(self, node_id: str) -> dict:
        device = await self._require_device(node_id)
        hb = await self._repo.get_heartbeat(node_id)
        effective, missed = self._effective_status(device, self._clock())
        return {
            "node_id": node_id,
            "status": device.status,
            "effective_status": effective,
            "last_seen_at": device.last_seen_at,
            "missed_heartbeats": missed,
            "agent_version": device.agent_version,
            "clock_skew_seconds": (hb or {}).get("clock_skew_seconds"),
            "last_event_seq": device.last_event_seq,
        }

    def _effective_status(self, device: DeviceRecord, now: datetime) -> tuple[str, int]:
        """按"现在"实时推导状态：不依赖 sweep 是否跑过（读时推导，写时落库）。

        - 丢 3 次心跳（≈3 分钟没音讯）→ suspect，W03 读到 suspect 立即停止派新活；
        - 丢 5 分钟（offline_after）→ offline，触发租约回收（只发事件）。
        """
        if device.status == "revoked":
            return "revoked", 0
        if device.last_seen_at is None:
            return "offline", 0
        idle = (now - device.last_seen_at).total_seconds()
        missed = int(idle // 60)
        if idle > device.tolerance and idle >= 300:
            return "offline", missed
        if missed >= 3:
            return "suspect", missed
        return "online", missed

    async def sweep(self, *, now: datetime | None = None) -> dict:
        """后台巡检：suspect(丢 3 次) → offline(丢 5 分钟)。

        offline 触发租约回收只发 device_alerts 事件（kind=lease_reclaim），
        不直接改 tasks 表 —— W03 订阅该事件后自行回收。
        """
        now = now or self._clock()
        transitions = {"suspect": 0, "offline": 0}
        checked = 0
        for device in await self._repo.sweep_candidates():
            checked += 1
            effective, missed = self._effective_status(device, now)
            if effective == device.status:
                continue
            if effective == "offline" and device.status != "offline":
                transitions["offline"] += 1
                await self._repo.insert_alert(
                    device.node_id,
                    ALERT_LEASE_RECLAIM,
                    {"from_status": device.status, "last_seen_at": device.last_seen_at},
                )
            elif effective == "suspect":
                transitions["suspect"] += 1
            await self._repo.update_device(device.node_id, status=effective, missed_heartbeats=missed)
        if any(transitions.values()):
            await self._repo.commit()
        return {"checked": checked, "transitions": transitions}

    # ------------------------------------------------------------- I04 增量事件

    async def ingest_events(self, batch: EventBatchIn) -> dict:
        """增量事件：幂等基础是 (node_id, seq) 唯一索引。

        - 重复投递返回 200 且计入 duplicates，对客户端无害；
        - 未知 kind 不入库、计入 rejected，仍返回 200（避免设备端整体重传）；
        - dropped > 0 写告警表（静默丢是红线）。
        """
        if batch.to_seq < batch.from_seq + max(len(batch.events) - 1, 0):
            raise ValidationError("to_seq 与事件数量不一致（dropped 只允许丢最旧）")

        now = self._clock()
        device = await self._require_device(batch.node)

        accepted_rows: list[dict] = []
        rejected = 0
        whitelist = set(EVENT_KINDS)
        for index, event in enumerate(batch.events):
            if event.kind not in whitelist:
                rejected += 1
                continue
            occurred = event.occurred_at or 0
            backfill = bool(
                occurred
                and device.last_snapshot_at
                and occurred < device.last_snapshot_at.timestamp()
            )
            accepted_rows.append(
                {
                    "node_id": batch.node,
                    "seq": batch.seq_at(index),
                    "kind": event.kind,
                    "task": event.task,
                    "model": event.model,
                    "cost": event.cost,
                    "ms": event.ms,
                    "extra": event.extra,
                    "occurred_at": occurred,
                    "received_at": now,
                    "is_backfill": backfill,
                }
            )

        inserted = await self._repo.insert_events(accepted_rows) if accepted_rows else 0
        duplicates = len(accepted_rows) - inserted

        if batch.dropped > 0:
            await self._repo.insert_alert(
                batch.node,
                ALERT_BUFFER_DROPPED,
                {"dropped": batch.dropped, "from_seq": batch.from_seq, "to_seq": batch.to_seq},
            )
        if batch.to_seq > device.last_event_seq:
            await self._repo.update_device(batch.node, last_event_seq=batch.to_seq)
        await self._repo.commit()
        return {
            "accepted": inserted,
            "duplicates": duplicates,
            "rejected": rejected,
            "from_seq": batch.from_seq,
            "to_seq": batch.to_seq,
        }

    async def list_events(self, node_id: str, *, since: int = 0, limit: int = 200) -> dict:
        await self._require_device(node_id)
        rows = await self._repo.list_events(node_id, since=since, limit=limit)
        next_since = max((r["seq"] for r in rows), default=since)
        return {"node_id": node_id, "since": since, "events": rows, "next_since": next_since}

    # ------------------------------------------------------------- I05 快照与能力变更

    async def ingest_snapshot(self, payload: SnapshotIn) -> dict:
        """快照：保留最近 30 份；能力变化写 device_cap_events；收到即标记 woke_at。

        唤醒容错：woke_at / last_snapshot_at 落库后，之后到达且 occurred_at 更早的
        增量事件会被打 is_backfill 标（见 ingest_events），不打乱当前状态视图。
        """
        now = self._clock()
        device = await self._require_device(payload.node)

        snapshot_id = await self._repo.insert_snapshot(
            payload.node,
            ts=payload.ts,
            versions=payload.versions.model_dump(),
            sessions=[s.model_dump() for s in payload.sessions],
            caps=list(payload.caps),
            disk_used_gb=payload.disk_used_gb,
            received_at=now,
        )
        prev_caps = set(device.caps or [])
        new_caps = set(payload.caps)
        added = sorted(new_caps - prev_caps)
        removed = sorted(prev_caps - new_caps)
        if added or removed:
            await self._repo.insert_cap_event(payload.node, added=added, removed=removed)

        await self._repo.update_device(
            payload.node,
            caps=list(payload.caps),
            woke_at=now,
            last_snapshot_at=now,
            agent_version=payload.versions.agent or device.agent_version,
        )
        pruned = await self._repo.prune_snapshots(payload.node, keep=30)
        await self._repo.commit()
        return {
            "snapshot_id": snapshot_id,
            "caps_added": added,
            "caps_removed": removed,
            "pruned": pruned,
            "woke_at": now,
        }

    async def list_snapshots(self, node_id: str, *, limit: int = 30) -> dict:
        await self._require_device(node_id)
        rows = await self._repo.list_snapshots(node_id, limit=limit)
        return {"node_id": node_id, "snapshots": rows}

    # ------------------------------------------------------------- I06 指令下发

    async def create_command(self, node_id: str, *, kind: str, payload: dict,
                             approved_by: str | None) -> dict:
        """控制台下发指令：白名单五种、永不支持任意 shell；中风险需审批人。"""
        device = await self._require_device(node_id)
        self._require_not_third_party(device, "指令")
        if kind not in COMMAND_KINDS:
            raise ValidationError("指令不在白名单内")
        if kind in MEDIUM_RISK_KINDS and not approved_by:
            raise ConflictError("中风险指令需要 approved_by 审批人")
        command = await self._repo.create_command(
            node_id, kind=kind, payload=payload, approved_by=approved_by
        )
        await self._repo.commit()
        return command

    async def pull_command(self, node_id: str) -> dict | None:
        """设备拉取：原子领取最旧 pending（UPDATE...WHERE status='pending' 防重复下发）。"""
        await self._require_device(node_id)
        command = await self._repo.pull_pending_command(node_id, now=self._clock())
        await self._repo.commit()
        return command

    async def ack_command(self, command_id: str, *, node_id: str, status: str, detail: str = "") -> dict:
        command = await self._repo.get_command(command_id)
        if command is None:
            raise NotFoundError("指令不存在")
        if command["node_id"] != node_id:
            raise ForbiddenError("该指令不属于当前设备")
        updated = await self._repo.ack_command(command_id, status=status, detail=detail, now=self._clock())
        await self._repo.commit()
        return updated or command
