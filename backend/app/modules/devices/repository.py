"""设备模块数据访问层。

约定：
- 只接受/返回领域对象（DeviceRecord）或行 dict，不把 ORM 对象泄露给 service；
- 写操作不 commit —— 由 service 显式调用 `commit()`（多步写入共事务）；
- 生产实现基于 SQLAlchemy/asyncpg；测试用内存实现（tests/test_devices.py 的 FakeRepo）
  保持同一方法签名，保证 service 逻辑可脱离数据库验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models


@dataclass(slots=True)
class DeviceRecord:
    """devices 表的领域视图（service 只见它，不见 ORM）。"""

    node_id: str
    fingerprint: str
    token_hash: str | None = None
    trust: str = "managed"
    purpose: str = ""
    squad: str | None = None
    tolerance: int = 300
    caps: list[str] = field(default_factory=list)
    status: str = "offline"
    agent_version: str | None = None
    last_seen_at: datetime | None = None
    missed_heartbeats: int = 0
    last_event_seq: int = 0
    woke_at: datetime | None = None
    last_snapshot_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None

    def with_updates(self, **changes: Any) -> DeviceRecord:
        return replace(self, **changes)


_RECORD_FIELDS = set(DeviceRecord.__dataclass_fields__)


def _to_record(row: models.Device) -> DeviceRecord:
    return DeviceRecord(
        node_id=row.node_id,
        fingerprint=row.fingerprint,
        token_hash=row.token_hash,
        trust=row.trust,
        purpose=row.purpose,
        squad=row.squad,
        tolerance=row.tolerance,
        caps=list(row.caps or []),
        status=row.status,
        agent_version=row.agent_version,
        last_seen_at=row.last_seen_at,
        missed_heartbeats=row.missed_heartbeats,
        last_event_seq=row.last_event_seq,
        woke_at=row.woke_at,
        last_snapshot_at=row.last_snapshot_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


class DeviceRepository:
    """SQLAlchemy 实现（PostgreSQL 专用：JSONB / ON CONFLICT / RETURNING）。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    # ------------------------------------------------------------- 设备

    async def get_device(self, node_id: str) -> DeviceRecord | None:
        # devices 主键是 UUID，node_id 是业务键，走 select
        stmt = select(models.Device).where(models.Device.node_id == node_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row else None

    async def get_device_by_fingerprint(self, fingerprint: str) -> DeviceRecord | None:
        stmt = select(models.Device).where(models.Device.fingerprint == fingerprint)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row else None

    async def create_device(self, record: DeviceRecord) -> DeviceRecord:
        row = models.Device(
            node_id=record.node_id,
            fingerprint=record.fingerprint,
            token_hash=record.token_hash,
            trust=record.trust,
            purpose=record.purpose,
            squad=record.squad,
            tolerance=record.tolerance,
            caps=record.caps,
            status=record.status,
        )
        self.session.add(row)
        await self.session.flush()
        return _to_record(row)

    async def update_device(self, node_id: str, **fields: Any) -> DeviceRecord | None:
        stmt = select(models.Device).where(models.Device.node_id == node_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        for key, value in fields.items():
            if key not in _RECORD_FIELDS:
                raise ValueError(f"devices 表没有字段 {key}")
            setattr(row, key, value)
        await self.session.flush()
        return _to_record(row)

    async def list_devices(
        self, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[DeviceRecord]:
        stmt = select(models.Device).order_by(models.Device.created_at.desc())
        if status:
            stmt = stmt.where(models.Device.status == status)
        stmt = stmt.limit(limit).offset(offset)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_record(r) for r in rows]

    # ------------------------------------------------------------- 引导码

    async def create_bootstrap_code(self, code_hash: str, expires_at: datetime) -> None:
        self.session.add(
            models.DeviceBootstrapCode(code_hash=code_hash, expires_at=expires_at)
        )
        await self.session.flush()

    async def bootstrap_code_valid(self, code_hash: str, *, now: datetime) -> bool:
        stmt = select(models.DeviceBootstrapCode).where(
            models.DeviceBootstrapCode.code_hash == code_hash
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        # expires_at == NULL 视为永不过期（人工长期码）
        return bool(
            row
            and row.used_at is None
            and (row.expires_at is None or row.expires_at > now)
        )

    async def mark_bootstrap_used(self, code_hash: str, node_id: str, *, now: datetime) -> bool:
        stmt = select(models.DeviceBootstrapCode).where(
            models.DeviceBootstrapCode.code_hash == code_hash
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None or row.used_at is not None:
            return False
        row.used_at = now
        row.used_by_node = node_id
        await self.session.flush()
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
        """心跳 UPSERT 单条 SQL（PostgreSQL ON CONFLICT），一节点只保留最新一条。"""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        values = {
            "node_id": node_id,
            "seq": seq,
            "metrics": metrics,
            "clock_skew_seconds": clock_skew_seconds,
            "received_at": received_at,
        }
        stmt = pg_insert(models.DeviceHeartbeat).values(**values).on_conflict_do_update(
            index_elements=[models.DeviceHeartbeat.node_id],
            set_={
                "seq": seq,
                "metrics": metrics,
                "clock_skew_seconds": clock_skew_seconds,
                "received_at": received_at,
            },
        )
        await self.session.execute(stmt)

    async def get_heartbeat(self, node_id: str) -> dict | None:
        stmt = select(models.DeviceHeartbeat).where(models.DeviceHeartbeat.node_id == node_id)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return {
            "node_id": row.node_id,
            "seq": row.seq,
            "metrics": dict(row.metrics or {}),
            "clock_skew_seconds": row.clock_skew_seconds,
            "received_at": row.received_at,
        }

    async def insert_alert(self, node_id: str, kind: str, detail: dict) -> None:
        self.session.add(models.DeviceAlert(node_id=node_id, kind=kind, detail=detail))
        await self.session.flush()

    async def sweep_candidates(self) -> list[DeviceRecord]:
        """需要巡检的设备：曾上线且未吊销。"""
        stmt = select(models.Device).where(
            models.Device.last_seen_at.is_not(None),
            models.Device.status != "revoked",
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_to_record(r) for r in rows]

    # ------------------------------------------------------------- 增量事件（I04）

    async def insert_events(self, rows: list[dict]) -> int:
        """批量入库，UNIQUE(node_id, seq) 冲突即跳过（幂等），返回实际插入条数。"""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(models.DeviceEvent).values(rows).on_conflict_do_nothing(
            index_elements=["node_id", "seq"]
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def list_events(self, node_id: str, *, since: int = 0, limit: int = 200) -> list[dict]:
        stmt = (
            select(models.DeviceEvent)
            .where(models.DeviceEvent.node_id == node_id, models.DeviceEvent.seq > since)
            .order_by(models.DeviceEvent.seq.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            {
                "seq": r.seq,
                "kind": r.kind,
                "task": r.task,
                "model": r.model,
                "cost": r.cost or 0.0,
                "ms": r.ms or 0,
                "extra": dict(r.extra or {}),
                "occurred_at": r.occurred_at or 0,
                "received_at": r.received_at,
                "is_backfill": r.is_backfill,
            }
            for r in rows
        ]

    # ------------------------------------------------------------- 快照与能力变更（I05）

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
        row = models.DeviceSnapshot(
            node_id=node_id,
            ts=ts,
            versions=versions,
            sessions=sessions,
            caps=caps,
            disk_used_gb=disk_used_gb,
            received_at=received_at,
        )
        self.session.add(row)
        await self.session.flush()
        return int(row.id)

    async def prune_snapshots(self, node_id: str, *, keep: int = 30) -> int:
        """只保留最近 keep 份，删最旧 —— 单条 SQL（子查询圈定要删的 id）。"""
        from sqlalchemy import delete, select

        subq = (
            select(models.DeviceSnapshot.id)
            .where(models.DeviceSnapshot.node_id == node_id)
            .order_by(models.DeviceSnapshot.id.desc())
            .offset(keep)
            .scalar_subquery()
        )
        result = await self.session.execute(
            delete(models.DeviceSnapshot).where(models.DeviceSnapshot.id.in_(subq))
        )
        return int(result.rowcount or 0)

    async def insert_cap_event(self, node_id: str, *, added: list[str], removed: list[str]) -> None:
        self.session.add(
            models.DeviceCapEvent(node_id=node_id, added=added, removed=removed)
        )
        await self.session.flush()

    async def list_snapshots(self, node_id: str, *, limit: int = 30) -> list[dict]:
        stmt = (
            select(models.DeviceSnapshot)
            .where(models.DeviceSnapshot.node_id == node_id)
            .order_by(models.DeviceSnapshot.id.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            {
                "id": r.id,
                "node_id": r.node_id,
                "ts": r.ts,
                "versions": dict(r.versions or {}),
                "sessions": list(r.sessions or []),
                "caps": list(r.caps or []),
                "disk_used_gb": r.disk_used_gb,
                "received_at": r.received_at,
            }
            for r in rows
        ]

    # ------------------------------------------------------------- 指令下发（I06）

    async def create_command(
        self, node_id: str, *, kind: str, payload: dict, approved_by: str | None
    ) -> dict:
        row = models.DeviceCommand(
            node_id=node_id, kind=kind, payload=payload, approved_by=approved_by
        )
        self.session.add(row)
        await self.session.flush()
        return _command_to_dict(row)

    async def get_command(self, command_id: str) -> dict | None:
        try:
            stmt = select(models.DeviceCommand).where(models.DeviceCommand.id == command_id)
            row = (await self.session.execute(stmt)).scalar_one_or_none()
        except ValueError:
            return None  # 非 UUID 直接视为不存在
        return _command_to_dict(row) if row else None

    async def pull_pending_command(self, node_id: str, *, now: datetime) -> dict | None:
        """原子领取最旧 pending（UPDATE ... WHERE status='pending'，防重复下发）。"""
        from sqlalchemy import update

        subq = (
            select(models.DeviceCommand.id)
            .where(
                models.DeviceCommand.node_id == node_id,
                models.DeviceCommand.status == "pending",
            )
            .order_by(models.DeviceCommand.created_at.asc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            update(models.DeviceCommand)
            .where(models.DeviceCommand.id == subq)
            .values(status="delivered", delivered_at=now)
            .returning(models.DeviceCommand)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _command_to_dict(row) if row else None

    async def ack_command(
        self, command_id: str, *, status: str, detail: str, now: datetime
    ) -> dict | None:
        from sqlalchemy import update

        stmt = (
            update(models.DeviceCommand)
            .where(models.DeviceCommand.id == command_id)
            .values(status=status, detail=detail, acked_at=now)
            .returning(models.DeviceCommand)
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _command_to_dict(row) if row else None


def _command_to_dict(row: models.DeviceCommand) -> dict:
    return {
        "id": str(row.id),
        "node_id": row.node_id,
        "kind": row.kind,
        "payload": dict(row.payload or {}),
        "approved_by": row.approved_by,
        "status": row.status,
        "detail": row.detail,
        "created_at": row.created_at,
        "delivered_at": row.delivered_at,
        "acked_at": row.acked_at,
    }
