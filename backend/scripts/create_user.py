#!/usr/bin/env python3
"""创建/更新登录用户（单用户白名单体系，无注册端点）。

用法（在 backend/ 目录下执行）：

    python scripts/create_user.py --subject owner@example.com --role owner
    python scripts/create_user.py --subject ops@example.com --role operator --password '...'

- 密码缺省时交互输入（推荐，避免进 shell 历史）；
- 已存在的 subject 会更新密码与角色（幂等）；
- role 必须是 owner / operator / viewer 之一（见 app/modules/identity/rbac.py）。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.modules.identity.models import User  # noqa: E402
from app.modules.identity.rbac import VALID_ROLES  # noqa: E402
from app.modules.identity.service import hash_password  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True, help="登录主体（如邮箱 / openid）")
    parser.add_argument("--role", default="owner", choices=sorted(VALID_ROLES))
    parser.add_argument("--password", default="", help="留空则交互输入")
    parser.add_argument("--deactivate", action="store_true", help="停用该用户（不删除行）")
    args = parser.parse_args()

    password = args.password or getpass.getpass(f"为 {args.subject} 设置密码: ")
    if len(password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 2

    engine = create_engine(settings.sync_dsn)
    with Session(engine) as session:
        user = session.get(User, args.subject)
        if args.deactivate:
            if user is None:
                print(f"用户不存在: {args.subject}", file=sys.stderr)
                return 1
            user.is_active = False
            action = "已停用"
        elif user is None:
            session.add(
                User(
                    subject=args.subject,
                    password_hash=hash_password(password),
                    role=args.role,
                    is_active=True,
                )
            )
            action = "已创建"
        else:
            user.password_hash = hash_password(password)
            user.role = args.role
            user.is_active = True
            action = "已更新"
        session.commit()

    print(f"{action}: {args.subject} (role={args.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
