"""设备模块 Pydantic 模型。

三组：
1. 与 agent 端 `agent/internal/protocol/types.go` 镜像的线格式（Heartbeat/Event/EventBatch/Snapshot），
   字段名与 Go 结构体完全一致，改字段必须先改 protocol 文件并两端同时改；
2. 注册/管理类 API 请求响应（I01）；
3. 上报与指令类 API 请求响应（I03/I04/I05/I06）。

约定：未知/缺失指标用 -1 表示"未知"，服务端不做容量假设（0 会被当成"空闲"）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: 信任级别：third_party 只能上报数据，永不接收指令（下发与长轮询一律 403）
Trust = Literal["managed", "third_party"]

TRUST_VALUES = ("managed", "third_party")

#: 批量增量事件的硬上限（超出直接 422）
MAX_EVENTS_PER_BATCH = 500

#: 设备 HMAC 签名密钥派生规则：key = sha256(注册时下发的明文 token) = 库里的 token_hash
DEVICE_SIGN_KEY_NOTE = "key = sha256(raw_device_token)"


# =========================================================================
# 1) 线格式（镜像 agent/internal/protocol/types.go，SchemaVersion = 1）
# =========================================================================


class HeartbeatIn(BaseModel):
    """镜像 protocol.Heartbeat：60s 一次，<1KB。"""

    node: str = Field(min_length=1, max_length=64)
    ts: int = Field(ge=0)  # 设备时钟（epoch 秒），仅用于排序与偏差告警
    seq: int = Field(ge=0)  # 心跳序号，独立于事件序号
    load: float = Field(default=-1.0, ge=-1.0)
    mem: float = Field(default=-1.0, ge=-1.0)  # 使用率百分比
    disk: float = Field(default=-1.0, ge=-1.0)
    battery: float = Field(default=-1.0, ge=-1.0)
    sessions: int = Field(default=-1, ge=-1)
    queue: int = Field(default=-1, ge=-1)
    agent_ver: str = ""


class EventIn(BaseModel):
    """镜像 protocol.Event：kind 白名单由服务端校验，未知 kind 拒收并计数。"""

    kind: str = Field(min_length=1, max_length=64)
    task: str = ""
    node: str = ""
    model: str = ""
    cost: float = Field(default=0.0, ge=0.0)
    ms: int = Field(default=0, ge=0)
    extra: dict[str, Any] = Field(default_factory=dict)
    occurred_at: int = Field(default=0, ge=0)  # 设备时钟 epoch 秒，仅排序用


class EventBatchIn(BaseModel):
    """镜像 protocol.EventBatch：30s 批量 + 游标。服务端按 (node, seq) 唯一索引去重。"""

    node: str = Field(min_length=1, max_length=64)
    from_seq: int = Field(ge=0)
    to_seq: int = Field(ge=0)
    events: list[EventIn] = Field(default_factory=list, max_length=MAX_EVENTS_PER_BATCH)
    # 因缓冲区上限被丢弃的事件数。必须上报，不能静默丢（>0 写告警表）
    dropped: int = Field(default=0, ge=0)

    def seq_at(self, index: int) -> int:
        """第 index 条事件的序号 = from_seq + index（dropped 只可能造成起点偏移）。"""
        return self.from_seq + index


class VersionsIn(BaseModel):
    """镜像 protocol.Versions。"""

    agent: str = ""
    orca: str = ""
    skills: dict[str, str] = Field(default_factory=dict)


class SessionIn(BaseModel):
    """镜像 protocol.Session。"""

    id: str = Field(min_length=1, max_length=128)
    task: str = ""
    state: str = ""
    worktree: str = ""


class SnapshotIn(BaseModel):
    """镜像 protocol.Snapshot：60min 一次，唤醒后先发快照再发增量。"""

    node: str = Field(min_length=1, max_length=64)
    ts: int = Field(ge=0)
    versions: VersionsIn = Field(default_factory=VersionsIn)
    sessions: list[SessionIn] = Field(default_factory=list)
    caps: list[str] = Field(default_factory=list)
    disk_used_gb: float = Field(default=-1.0, ge=-1.0)
    extra: dict[str, str] = Field(default_factory=dict)


# =========================================================================
# 2) 注册与管理（I01）
# =========================================================================


class RegisterIn(BaseModel):
    bootstrap_code: str = Field(min_length=8, max_length=128)
    node_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    fingerprint: str = Field(min_length=8, max_length=128)
    caps: list[str] = Field(default_factory=list)
    tolerance: int = Field(default=300, ge=0, le=86400)
    squad: str | None = None
    purpose: str = ""
    trust: Trust = "managed"


class RegisterOut(BaseModel):
    node_id: str
    # 明文 token 只在 created=True 或 reactivated=True 的响应中出现一次；重复注册为 None
    token: str | None
    created: bool
    reactivated: bool


class BootstrapCodeOut(BaseModel):
    code: str  # 明文只出现一次
    expires_at: datetime | None


class DevicePatchIn(BaseModel):
    caps: list[str] | None = None
    tolerance: int | None = Field(default=None, ge=0, le=86400)
    squad: str | None = None
    purpose: str | None = None
    trust: Trust | None = None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    trust: str
    purpose: str
    squad: str | None
    tolerance: int
    caps: list[str]
    status: str
    agent_version: str | None
    last_seen_at: datetime | None
    missed_heartbeats: int
    last_event_seq: int
    woke_at: datetime | None
    created_at: datetime | None
    revoked_at: datetime | None
    # 故意不返回 fingerprint / token_hash：指纹可用来撞注册，哈希无用但也不必暴露


class RevokeOut(BaseModel):
    node_id: str
    status: str
    revoked_at: datetime


# =========================================================================
# 3) 上报与指令（I03 / I04 / I05 / I06，随 issue 逐步补充）
# =========================================================================


class HeartbeatOut(BaseModel):
    node_id: str
    status: str
    received_at: datetime
    clock_skew_seconds: int


class DeviceStatusOut(BaseModel):
    node_id: str
    status: str
    effective_status: str
    last_seen_at: datetime | None
    missed_heartbeats: int
    agent_version: str | None
    clock_skew_seconds: int | None
    last_event_seq: int


class EventsIngestOut(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    from_seq: int
    to_seq: int


class EventOut(BaseModel):
    seq: int
    kind: str
    task: str
    model: str
    cost: float
    ms: int
    extra: dict[str, Any]
    occurred_at: int
    received_at: datetime
    is_backfill: bool


class EventsListOut(BaseModel):
    node_id: str
    since: int
    events: list[EventOut]
    next_since: int


class SnapshotOut(BaseModel):
    id: int
    node_id: str
    ts: int
    versions: dict[str, Any]
    sessions: list[dict[str, Any]]
    caps: list[str]
    disk_used_gb: float
    received_at: datetime


class SnapshotIngestOut(BaseModel):
    snapshot_id: int
    caps_added: list[str]
    caps_removed: list[str]
    pruned: int
    woke_at: datetime


# ---- I06 指令下发（白名单定义在 commands.py，本文件只做线格式） ----

from .commands import COMMAND_KINDS, MEDIUM_RISK_KINDS, CommandKind  # noqa: E402, F401


class CommandCreateIn(BaseModel):
    kind: CommandKind  # 白名单之外的 kind 直接 422，永不支持任意 shell
    payload: dict[str, Any] = Field(default_factory=dict)
    approved_by: str | None = None


class CommandOut(BaseModel):
    id: str
    node_id: str
    kind: str
    payload: dict[str, Any]
    approved_by: str | None
    status: str
    detail: str = ""
    created_at: datetime
    delivered_at: datetime | None
    acked_at: datetime | None


class CommandAckIn(BaseModel):
    status: Literal["acked", "failed"]
    detail: str = ""
