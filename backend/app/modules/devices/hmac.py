"""设备 HMAC 验签依赖（W04-I02）。

线协议（与 Go 设备端约定）：
- 请求头：`X-Afc-Node`（node_id）、`X-Afc-Timestamp`（epoch 秒）、`X-Afc-Signature`（hex）；
- 签名：`hex(hmac_sha256(key, f"{ts}." + raw_body))`，用 `core.security.verify_hmac` 校验，
  时间窗 300s（settings.device_hmac_window_seconds）——**不在此重复实现密码学**；
- 密钥派生：`key = sha256(明文 token) = 库里的 token_hash`。明文 token 只在注册响应出现一次，
  设备端自行做同样的 sha256 派生。吊销后 token_hash 置 NULL，验签自然失败。

安全语义：
- 所有失败（节点不存在 / 已吊销 / 签名错 / 时间窗超）返回同一个 401 响应体，**防枚举**；
- 每 node 失败 10 次/分钟 → 429（Redis 计数，core.redis.incr_with_ttl，60s 自动过期）；
  Redis 故障时降级放行（限流组件不可用不应拒绝合法设备上报）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from fastapi import Header, Request, Response

from app.core.errors import RateLimitedError, UnauthorizedError
from app.core.redis import incr_with_ttl
from app.core.security import verify_hmac

from .deps import DeviceServiceDep

logger = logging.getLogger("afc.devices.hmac")

#: 每 node 签名失败限流（次/分钟），超过即 429
HMAC_FAIL_LIMIT_PER_MINUTE = 10
_FAIL_KEY_PREFIX = "device:hmac_fail:"

#: 节点不存在时的哑密钥：照常做一次 compare_digest，使"节点不存在"与"签名错"耗时一致
_DUMMY_SECRET = "afc-hmac-dummy-secret-for-unknown-node"


@dataclass(frozen=True, slots=True)
class DevicePrincipal:
    """通过 HMAC 验签的设备身份。"""

    node_id: str
    trust: str
    status: str


async def _bump_failure_counter(node_id: str) -> None:
    try:
        count = await incr_with_ttl(f"{_FAIL_KEY_PREFIX}{node_id}", 60)
    except Exception:  # noqa: BLE001 - Redis 故障降级：不因限流组件挂掉而 5xx
        logger.warning("hmac rate-limit backend unavailable, degraded", extra={"node": node_id})
        return
    if count >= HMAC_FAIL_LIMIT_PER_MINUTE:
        raise RateLimitedError("签名失败次数过多，请稍后重试")


async def verify_device_signature(
    request: Request,
    service: DeviceServiceDep,
    x_afc_node: str = Header(min_length=1, max_length=64),
    x_afc_timestamp: int = Header(),
    x_afc_signature: str = Header(min_length=64, max_length=64),
) -> DevicePrincipal:
    body = await request.body()
    device = await service.get_device_for_auth(x_afc_node)

    secret = device.token_hash if (device and device.token_hash) else _DUMMY_SECRET
    signature_ok = verify_hmac(secret, body, x_afc_signature, x_afc_timestamp)
    device_ok = (
        device is not None
        and bool(device.token_hash)
        and device.status != "revoked"
    )
    if not (signature_ok and device_ok):
        await _bump_failure_counter(x_afc_node)
        # 统一 401 文案：不区分"节点不存在 / 已吊销 / 签名错 / 时间窗超"，防枚举
        raise UnauthorizedError("未认证或凭据已过期")

    assert device is not None
    return DevicePrincipal(node_id=device.node_id, trust=device.trust, status=device.status)


async def long_poll(service: DeviceServiceDep, node_id: str, *, wait: int) -> Response | dict:
    """指令长轮询：每 0.5s 拉一次，最长 wait 秒；无指令返回 204（不用 WebSocket）。"""
    deadline = time.monotonic() + wait
    while True:
        command = await service.pull_command(node_id)
        if command is not None:
            return command
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Response(status_code=204)
        await asyncio.sleep(min(0.5, remaining))
