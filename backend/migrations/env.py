"""Alembic 环境。

迁移纪律（详见 docs/DEV-PLAN-v1.md §8）：
1. **只允许新增 revision，不允许修改已合并的 revision**；
2. 文件命名 `w<窗口号>_<序号>_<slug>.py`，例如 `w03_02_task_lease.py`；
3. `down_revision` 只能指向「自己上一个 revision」或「main 的当前 head」；
4. 合并前必须 `alembic heads` 恰好 1 个 head，出现多头用 `alembic merge` 生成合并 revision；
5. 任何 revision 必须可 downgrade（CI 会跑 upgrade → downgrade → upgrade）。
"""

from __future__ import annotations

import importlib
import pkgutil
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.modules as modules_pkg
from app.core.config import settings
from app.core.db import Base

# 逐个导入模块的 models.py，让 autogenerate 能看到全部表
for info in pkgutil.iter_modules(modules_pkg.__path__):
    try:
        importlib.import_module(f"app.modules.{info.name}.models")
    except ModuleNotFoundError:
        continue

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.sync_dsn)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
