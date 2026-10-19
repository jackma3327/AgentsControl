"""设备接入模块路由 —— owner: W04

端点分两类认证：
- Owner（Bearer JWT，经 core.deps.OwnerDep）：注册引导码签发、设备管理、状态/事件/快照查询、指令下发；
- Device（HMAC 签名，经 hmac.verify_device_signature）：heartbeat / events / snapshot 上报、指令长轮询与 ack。

骨架期的 /_status、/_ping 保留：它们在 openapi.snapshot.json 契约快照中登记，
删除或改名属于契约删除（CI 会拒绝）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from app.core.deps import OwnerDep
from app.core.errors import ConflictError, ForbiddenError

from . import hmac as device_hmac
from .deps import DeviceServiceDep
from .schemas import (
    BootstrapCodeOut,
    CommandAckIn,
    CommandCreateIn,
    CommandOut,
    DeviceOut,
    DevicePatchIn,
    DeviceStatusOut,
    EventBatchIn,
    EventsIngestOut,
    EventsListOut,
    HeartbeatIn,
    HeartbeatOut,
    RegisterIn,
    RegisterOut,
    RevokeOut,
    SnapshotIn,
    SnapshotIngestOut,
    SnapshotOut,
)

MODULE = "devices"
OWNER_WINDOW = "W04"

router = APIRouter(prefix="/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# 骨架自检端点（契约已登记进 openapi.snapshot.json，保持不变）
# ---------------------------------------------------------------------------


@router.get("/_status", summary="模块占位状态（骨架自检用）")
async def module_status() -> dict[str, str]:
    return {"module": MODULE, "owner_window": OWNER_WINDOW, "state": "active"}


@router.get("/_ping", summary="需要认证的占位端点（验证认证链路）")
async def module_ping(_: OwnerDep) -> dict[str, str]:
    return {"module": MODULE, "owner_window": OWNER_WINDOW, "state": "active"}


# ---------------------------------------------------------------------------
# I01 · 注册与设备管理（owner 认证，除 register 凭引导码外）
# ---------------------------------------------------------------------------


@router.post("/register", response_model=RegisterOut, summary="设备注册（凭一次性引导码）")
async def register_device(payload: RegisterIn, service: DeviceServiceDep) -> RegisterOut:
    result = await service.register(payload)
    return RegisterOut(
        node_id=result.node_id,
        token=result.token,
        created=result.created,
        reactivated=result.reactivated,
    )


@router.post("/bootstrap-codes", response_model=BootstrapCodeOut, summary="人工签发注册引导码")
async def issue_bootstrap_code(_: OwnerDep, service: DeviceServiceDep) -> BootstrapCodeOut:
    code, expires_at = await service.issue_bootstrap_code()
    return BootstrapCodeOut(code=code, expires_at=expires_at)


@router.get("/", response_model=list[DeviceOut], summary="设备列表")
async def list_devices(
    _: OwnerDep,
    service: DeviceServiceDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[DeviceOut]:
    records = await service.list_devices(status=status, limit=limit, offset=offset)
    return [DeviceOut.model_validate(r) for r in records]


@router.patch("/{node_id}", response_model=DeviceOut, summary="更新设备（caps/tolerance/squad/purpose/trust）")
async def patch_device(
    node_id: str, payload: DevicePatchIn, _: OwnerDep, service: DeviceServiceDep
) -> DeviceOut:
    record = await service.update_device(node_id, patch=payload.model_dump())
    return DeviceOut.model_validate(record)


@router.post("/{node_id}/revoke", response_model=RevokeOut, summary="吊销设备（token 立即失效）")
async def revoke_device(node_id: str, _: OwnerDep, service: DeviceServiceDep) -> RevokeOut:
    record = await service.revoke_device(node_id)
    if record.revoked_at is None:  # 理论不可达，防御类型检查
        raise ConflictError("吊销失败")
    return RevokeOut(node_id=record.node_id, status=record.status, revoked_at=record.revoked_at)


# ---------------------------------------------------------------------------
# I03 · 心跳（device HMAC 认证）
# ---------------------------------------------------------------------------


@router.post("/heartbeat", response_model=HeartbeatOut, summary="心跳上报（60s 一次）")
async def heartbeat(
    payload: HeartbeatIn,
    service: DeviceServiceDep,
    principal: device_hmac.DevicePrincipal = Depends(device_hmac.verify_device_signature),
) -> HeartbeatOut:
    if principal.node_id != payload.node:
        raise ForbiddenError("心跳与签名节点不一致")
    result = await service.heartbeat(payload)
    return HeartbeatOut(**result)


@router.get("/{node_id}/status", response_model=DeviceStatusOut, summary="设备状态（实时推导）")
async def device_status(node_id: str, _: OwnerDep, service: DeviceServiceDep) -> DeviceStatusOut:
    return DeviceStatusOut(**await service.device_status(node_id))


# ---------------------------------------------------------------------------
# I04 · 增量事件（device HMAC 认证）
# ---------------------------------------------------------------------------


@router.post("/events", response_model=EventsIngestOut, summary="增量事件批量上报（30s，幂等）")
async def ingest_events(
    payload: EventBatchIn,
    service: DeviceServiceDep,
    principal: device_hmac.DevicePrincipal = Depends(device_hmac.verify_device_signature),
) -> EventsIngestOut:
    if principal.node_id != payload.node:
        raise ForbiddenError("事件批次与签名节点不一致")
    return EventsIngestOut(**await service.ingest_events(payload))


@router.get("/{node_id}/events", response_model=EventsListOut, summary="按游标查询事件")
async def list_events(
    node_id: str,
    _: OwnerDep,
    service: DeviceServiceDep,
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> EventsListOut:
    return EventsListOut(**await service.list_events(node_id, since=since, limit=limit))


# ---------------------------------------------------------------------------
# I05 · 快照（device HMAC 认证）
# ---------------------------------------------------------------------------


@router.post("/snapshot", response_model=SnapshotIngestOut, summary="快照上报（60min，唤醒先发）")
async def ingest_snapshot(
    payload: SnapshotIn,
    service: DeviceServiceDep,
    principal: device_hmac.DevicePrincipal = Depends(device_hmac.verify_device_signature),
) -> SnapshotIngestOut:
    if principal.node_id != payload.node:
        raise ForbiddenError("快照与签名节点不一致")
    return SnapshotIngestOut(**await service.ingest_snapshot(payload))


@router.get("/{node_id}/snapshots", response_model=list[SnapshotOut], summary="快照历史（最近 N 份）")
async def list_snapshots(
    node_id: str,
    _: OwnerDep,
    service: DeviceServiceDep,
    limit: int = Query(default=30, ge=1, le=30),
) -> list[SnapshotOut]:
    data = await service.list_snapshots(node_id, limit=limit)
    return [SnapshotOut(**row) for row in data["snapshots"]]


# ---------------------------------------------------------------------------
# I06 · 指令下发（下发 = owner 认证；长轮询/ack = device HMAC 认证）
# ---------------------------------------------------------------------------


@router.post("/{node_id}/commands", response_model=CommandOut, summary="下发指令（白名单五种）")
async def create_command(
    node_id: str, payload: CommandCreateIn, _: OwnerDep, service: DeviceServiceDep
) -> CommandOut:
    command = await service.create_command(
        node_id,
        kind=payload.kind,
        payload=payload.payload,
        approved_by=payload.approved_by,
    )
    return CommandOut(**command)


@router.get("/{node_id}/commands", summary="指令长轮询（设备拉取，超时 204）")
async def pull_commands(
    node_id: str,
    service: DeviceServiceDep,
    wait: int = Query(default=0, ge=0, le=25),
    principal: device_hmac.DevicePrincipal = Depends(device_hmac.verify_device_signature),
) -> Response:
    if principal.node_id != node_id:
        raise ForbiddenError("长轮询节点与签名节点不一致")
    if principal.trust == "third_party":
        raise ForbiddenError("第三方设备不接收指令")
    return await device_hmac.long_poll(service, node_id, wait=wait)


@router.post("/commands/{command_id}/ack", response_model=CommandOut, summary="指令回执")
async def ack_command(
    command_id: str,
    payload: CommandAckIn,
    service: DeviceServiceDep,
    principal: device_hmac.DevicePrincipal = Depends(device_hmac.verify_device_signature),
) -> CommandOut:
    command = await service.ack_command(
        command_id, node_id=principal.node_id, status=payload.status, detail=payload.detail
    )
    return CommandOut(**command)
