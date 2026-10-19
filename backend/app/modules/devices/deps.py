"""设备模块的 FastAPI 依赖装配。

单独成文件的原因：router.py 与 hmac.py 都需要 DeviceServiceDep，
放 router 里会造成环导入（router → hmac → router）。
测试通过 app.dependency_overrides[get_device_service] 注入内存仓库。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db

from .repository import DeviceRepository
from .service import DeviceService

SessionDep = Annotated[AsyncSession, Depends(get_db)]


def get_device_service(session: SessionDep) -> DeviceService:
    return DeviceService(DeviceRepository(session))


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]
