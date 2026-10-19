#!/usr/bin/env python3
"""设备状态巡检（W04-I03）：suspect(丢 3 次心跳) → offline(丢 5 分钟)。

- offline 触发租约回收：只写 device_alerts(kind=lease_reclaim) 事件，
  **不直接改 tasks 表** —— W03 订阅该事件后自行回收租约；
- 用法：
    python scripts/sweep_devices.py --once   # 单次巡检（给 cron / systemd timer）
    python scripts/sweep_devices.py          # 常驻循环，默认 30s 一轮
- 需要环境变量 DATABASE_URL（读取 backend/.env 或进程环境）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.core.db import get_sessionmaker  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.modules.devices.repository import DeviceRepository  # noqa: E402
from app.modules.devices.service import DeviceService  # noqa: E402

logger = logging.getLogger("afc.sweep-devices")

DEFAULT_INTERVAL_SECONDS = 30


async def sweep_once() -> dict:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        service = DeviceService(DeviceRepository(session))
        return await service.sweep()


async def main(interval: int, once: bool) -> None:
    setup_logging()
    while True:
        try:
            result = await sweep_once()
            transitions = result.get("transitions", {})
            if any(transitions.values()):
                logger.info("sweep done", extra=result)
        except Exception:  # noqa: BLE001 - 巡检失败不退出循环，下一轮重试
            logger.exception("sweep failed")
        if once:
            return
        await asyncio.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="只巡检一次（cron 模式）")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="常驻循环间隔秒数")
    parser.add_argument("--quiet", action="store_true", help="只在有状态迁移时输出")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger("afc").setLevel(logging.WARNING)
    start = time.monotonic()
    asyncio.run(main(args.interval, args.once))
    if args.once:
        print(f"sweep finished in {time.monotonic() - start:.2f}s")
