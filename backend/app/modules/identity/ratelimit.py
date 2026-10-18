"""限流器：协议 + Redis 实现。

设计：
- Redis 只做限流计数（key 一律带 TTL，见 core/redis.py 的约束）；
- Redis 不可达时**降级放行**（fail-open）并告警——与 /ready 的降级哲学一致：
  限流是加固项，不是可用性前提；单用户系统宁可放行也不把登录打挂。
  取舍记录在 docs/adr/0002-auth-model.md。
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.core.redis import cache_delete, get_redis, incr_with_ttl

logger = logging.getLogger("afc.identity.ratelimit")

_PREFIX = "rl:"


class RateLimiter(Protocol):
    async def count(self, key: str) -> int: ...

    async def incr(self, key: str, window_seconds: int) -> int: ...

    async def reset(self, key: str) -> None: ...


class RedisRateLimiter:
    """Redis 固定窗口计数器。key 由调用方拼维度（subject / ip / 路径桶）。"""

    async def count(self, key: str) -> int:
        try:
            value = await get_redis().get(_PREFIX + key)
            return int(value) if value else 0
        except Exception:  # noqa: BLE001 - 限流降级：Redis 故障不阻塞主流程
            logger.warning("rate limiter degraded (count)", extra={"key": key})
            return 0

    async def incr(self, key: str, window_seconds: int) -> int:
        try:
            return await incr_with_ttl(_PREFIX + key, window_seconds)
        except Exception:  # noqa: BLE001
            logger.warning("rate limiter degraded (incr)", extra={"key": key})
            return 0

    async def reset(self, key: str) -> None:
        try:
            await cache_delete(_PREFIX + key)
        except Exception:  # noqa: BLE001
            logger.warning("rate limiter degraded (reset)", extra={"key": key})
