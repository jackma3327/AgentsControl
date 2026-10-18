#!/usr/bin/env python3
"""从 app/core/errors.py 自动生成 docs/ERROR-CODES.md（W01-I05）。

错误码表是跨窗口契约：前端按 error.code 做文案映射，绝不解析 message。
本文件由脚本生成，**手工编辑会在 CI 的 no-diff 断言中被打回**
（断言由 W09 在 ci.yml 接线：`python backend/scripts/gen_error_codes.py --check`）。

用法：
    python backend/scripts/gen_error_codes.py            # 生成/更新 docs/ERROR-CODES.md
    python backend/scripts/gen_error_codes.py --check    # CI：与文件不一致则退出 1
"""

from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND = ROOT / "backend"
DOC = ROOT / "docs" / "ERROR-CODES.md"

sys.path.insert(0, str(BACKEND))

from app.core import errors  # noqa: E402


def collect() -> list[type[errors.AppError]]:
    out = []
    for _, obj in vars(errors).items():
        if inspect.isclass(obj) and issubclass(obj, errors.AppError) and obj is not errors.AppError:
            out.append(obj)
    return sorted(out, key=lambda c: (c.status_code, c.code))


def render(classes: list[type[errors.AppError]]) -> str:
    lines = [
        "# 错误码表（自动生成，勿手改）",
        "",
        "> 由 `backend/scripts/gen_error_codes.py` 从 `app/core/errors.py` 生成。",
        "> 手工编辑会被 CI no-diff 断言打回；新增错误码请改 errors.py 后重新生成。",
        "> 客户端只读 `error.code` 做行为映射，不解析 `message`。",
        "",
        "| code | HTTP | 默认 message | 说明 |",
        "|---|---|---|---|",
    ]
    for cls in classes:
        doc = (cls.__doc__ or "").strip().splitlines()  # 只取子类自己的 docstring，不继承基类
        first = doc[0].strip() if doc else "—"
        lines.append(f"| `{cls.code}` | {cls.status_code} | {cls.message} | {first} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="与现存文件不一致则退出 1（CI 门禁）")
    args = parser.parse_args()

    content = render(collect())

    if args.check:
        if not DOC.exists():
            print(f"缺少 {DOC.relative_to(ROOT)}，先运行生成模式", file=sys.stderr)
            return 1
        if DOC.read_text(encoding="utf-8") != content:
            print("docs/ERROR-CODES.md 与 app/core/errors.py 不一致：请运行生成模式并提交", file=sys.stderr)
            return 1
        print("docs/ERROR-CODES.md 与错误定义一致")
        return 0

    DOC.write_text(content, encoding="utf-8")
    print(f"已生成 {DOC.relative_to(ROOT)}（{len(collect())} 个错误码）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
