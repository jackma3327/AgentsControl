"""设备接入模块 ORM 模型 —— owner: W04

表与迁移的对应关系（迁移链：0001_baseline → w04_01 → w04_02 → w04_03 → w04_04）：
- w04_01_devices:   devices、device_bootstrap_codes、device_heartbeats（I01；heartbeats 供 I03 使用）
- w04_02_events:    device_events、device_alerts（I04）
- w04_03_snapshots: device_snapshots、device_cap_events（I05）
- w04_04_commands:  device_commands（I06）

硬约束（TECH-ARCHITECTURE §4.2/§4.3）：
- 幂等基础是 device_events 的 UNIQUE(node_id, seq)，不是应用层去重；
- 设备 token 只存 sha256（token_hash），明文不落库；
- 时钟以服务端 received_at 为准，设备时间只用于排序。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

#: 设备状态机取值（I03）
DEVICE_STATUSES = ("online", "suspect", "offline", "revoked")
#: 信任级别：third_party 只能上报数据，永不接收指令
TRUST_LEVELS = ("managed", "third_party")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    # 设备自报名（与 agent 端 protocol.Heartbeat.node 一致），owner 控制台也用它寻址
    node_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # 机器指纹（agent 端汇总硬件特征生成）。注册幂等与冲突判定都以它为准
    fingerprint: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # sha256(明文 token)。明文只在注册/重激活响应里出现一次。
    # HMAC 签名密钥 = 该哈希值（设备端对 raw token 做同样的 sha256 派生），吊销后置 NULL。
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trust: Mapped[str] = mapped_column(String(16), nullable=False, default="managed",
                                       server_default="managed")
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    squad: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 可容忍离线秒数（笔记本休眠类设备可以给大值）
    tolerance: Mapped[int] = mapped_column(Integer, nullable=False, default=300, server_default="300")
    # 能力标签（gui/orca/browser/container/always_on…），快照变更时联动更新
    caps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                       server_default=text("'[]'::jsonb"))

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="offline",
                                        server_default="offline")
    agent_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    missed_heartbeats: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                   server_default="0")
    # 增量事件游标（该节点已上报的最大 seq）
    last_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0,
                                                server_default="0")
    # 唤醒标记：收到快照即刷新（唤醒后先快照再增量的容错依赖它）
    woke_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeviceBootstrapCode(Base):
    """人工签发的引导码：没有它不允许自助注册。同样只存哈希。"""

    __tablename__ = "device_bootstrap_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_node: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class DeviceHeartbeat(Base):
    """心跳最新值（一节点一行，UPSERT 覆盖，不是追加日志 —— 行数不随时间膨胀）。"""

    __tablename__ = "device_heartbeats"

    node_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.node_id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    # load/mem/disk/battery/sessions/queue/agent_ver（未知为 -1）
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                          server_default=text("'{}'::jsonb"))
    # 设备时钟与 received_at 的偏差（秒），>300 触发 clock_skew 告警
    clock_skew_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                    server_default="0")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())


class DeviceAlert(Base):
    """设备告警（只追加）：buffer_dropped / clock_skew / lease_reclaim。

    offline 触发的租约回收也走这里（只发事件，不直接改 tasks 表）。
    """

    __tablename__ = "device_alerts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                         server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class DeviceEvent(Base):
    """增量事件（追加日志）。

    幂等基础：PRIMARY KEY (node_id, seq) —— 重复投递靠数据库唯一约束去重，
    ON CONFLICT DO NOTHING，对客户端无害（返回 200 + duplicates 计数）。
    seq 由批次 from_seq + 批内下标推导；occurred_at 只用于排序，时钟以 received_at 为准。
    """

    __tablename__ = "device_events"

    node_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.node_id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    model: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                        server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())
    # 唤醒容错：occurred_at 早于最近一次快照的事件打回填标，不打乱当前状态视图
    is_backfill: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                              server_default="false")

    __table_args__ = (Index("ix_device_events_node_received", "node_id", "received_at"),)


class DeviceSnapshot(Base):
    """设备快照（60min 一次，唤醒后先发）：每节点保留最近 30 份，超限删最旧。"""

    __tablename__ = "device_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.node_id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                           server_default=text("'{}'::jsonb"))
    sessions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                           server_default=text("'[]'::jsonb"))
    caps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                       server_default=text("'[]'::jsonb"))
    disk_used_gb: Mapped[float] = mapped_column(Float, nullable=False, default=-1,
                                                server_default="-1")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())

    __table_args__ = (Index("ix_device_snapshots_node_id_desc", "node_id", "id"),)


class DeviceCapEvent(Base):
    """能力标签变更事件（W10 的"偏离"视图数据源）。"""

    __tablename__ = "device_cap_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    added: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                        server_default=text("'[]'::jsonb"))
    removed: Mapped[list] = mapped_column(JSONB, nullable=False, default=list,
                                          server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())


class DeviceCommand(Base):
    """下发给设备的指令（白名单五种，永不支持任意 shell）。

    生命周期：pending →(设备长轮询领取) delivered →(ack) acked/failed。
    设备在 NAT 后只能主动拉，长轮询超时 204。
    """

    __tablename__ = "device_commands"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    node_id: Mapped[str] = mapped_column(
        Text, ForeignKey("devices.node_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict,
                                          server_default=text("'{}'::jsonb"))
    # 中风险指令（restart_agent / run_task）必须记录审批人
    approved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending",
                                        server_default="pending")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_device_commands_node_status", "node_id", "status", "created_at"),)
