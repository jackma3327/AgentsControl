#!/usr/bin/env python3
"""边界检查：把「文档承诺的边界」变成「可执行的断言」。

用法
    python scripts/check_boundaries.py                     # 全量自检（CI）
    python scripts/check_boundaries.py --window W03        # 只查某窗口
    python scripts/check_boundaries.py --changed           # 只查本次改动（PR 模式）
    python scripts/check_boundaries.py --changed --base origin/main

检查项
    [E] 计划文件完整性：10 个窗口、issue 号唯一、wave ∈ 1..4、commit 前缀含 (#issue)
    [E] 边界：窗口 allow 路径必须存在；不同窗口不得互相包含
    [E] 越界：改动文件必须落在该窗口 allow 内（--changed / --window 时生效）
    [E] 冻结：改动冻结文件必须带 label x-shared-registry
    [E] 冲突标记 <<<<<<< / >>>>>>> 不得提交
    [E] 密钥文件不得入库（.env、*.pem、*.key）
    [W] 迁移：heads 是否恰好 1 个（需要 DB，缺失时降级为静态扫描）
    [W] 模块目录：每个 backend/app/modules/<m>/ 必须有 README.md 写明 owner

退出码   0 通过 / 1 阻断 / 2 只有警告
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "plan" / "windows.yaml"

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout


def load_plan() -> dict:
    if not PLAN.exists():
        err(f"缺少计划文件 {PLAN}")
        return {}
    with PLAN.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_plan_shape(plan: dict) -> None:
    windows = plan.get("windows") or []
    if len(windows) != 10:
        err(f"窗口数应为 10，实际 {len(windows)}")

    seen_issue: dict[str, str] = {}
    seen_win: set[str] = set()

    for w in windows:
        wid = w.get("id", "?")
        if wid in seen_win:
            err(f"窗口 id 重复: {wid}")
        seen_win.add(wid)

        for key in ("name", "branch", "worktree", "allow", "issues"):
            if not w.get(key):
                err(f"{wid}: 缺少字段 {key}")
        if not str(w.get("branch", "")).startswith("win/"):
            err(f"{wid}: branch 必须以 win/ 开头")

        for p in w.get("allow") or []:
            rel = p.rstrip("/")
            if not (ROOT / rel).exists():
                err(f"{wid}: allow 路径不存在 {p}")

        for it in w.get("issues") or []:
            iid = it.get("id", "?")
            if iid in seen_issue:
                err(f"issue id 重复: {iid}（{seen_issue[iid]} 与 {wid}）")
            seen_issue[iid] = wid
            if not str(iid).startswith(wid + "-"):
                err(f"{iid}: issue id 必须以 {wid}- 开头")
            wave = it.get("wave")
            if not isinstance(wave, int) or not 1 <= wave <= 4:
                err(f"{iid}: wave 非法 {wave}")
            if "(#" not in str(it.get("commit", "")):
                err(f"{iid}: commit 前缀必须包含 (#<issue>)")

    # 依赖引用必须存在
    for w in windows:
        for it in w.get("issues") or []:
            for dep in it.get("depends_on") or []:
                if dep not in seen_issue:
                    err(f"{it.get('id')}: depends_on 引用了不存在的 issue {dep}")

    # 目录互相包含
    claimed: list[tuple[str, str]] = []
    for w in windows:
        for p in w.get("allow") or []:
            claimed.append((p.rstrip("/"), w["id"]))
    for a, oa in claimed:
        for b, ob in claimed:
            if oa == ob or a == b:
                continue
            if b.startswith(a + "/"):
                err(f"目录嵌套冲突：{a}({oa}) 包含 {b}({ob})")


def check_frozen(plan: dict, changed: list[str], labels: set[str]) -> None:
    frozen = [f["path"].rstrip("/") for f in plan.get("frozen") or []]
    exempt = "x-shared-registry" in labels
    for path in changed:
        for fz in frozen:
            if path == fz or path.startswith(fz + "/"):
                if not exempt:
                    err(f"改动了冻结文件 {path} 但 PR 未带 label: x-shared-registry")


def window_of(path: str, plan: dict) -> str | None:
    best: tuple[int, str] | None = None
    for w in plan.get("windows") or []:
        for p in w.get("allow") or []:
            base = p.rstrip("/")
            if path == base or path.startswith(base + "/"):
                if best is None or len(base) > best[0]:
                    best = (len(base), w["id"])
    return best[1] if best else None


def check_scope(changed: list[str], plan: dict, only_window: str | None) -> None:
    # 共享前缀：任何窗口改动都不告警
    # （迁移目录按 w<NN>_ 前缀防冲突，单一 head 由迁移检查与 CI alembic heads 保证）
    SHARED_PREFIXES = (
        "docs/",
        "plan/",
        "scripts/",
        ".github/",
        ".fleet/",
        "backend/migrations/versions/",
        "backend/openapi.snapshot.json",
    )
    for path in changed:
        owner = window_of(path, plan)
        if owner is None:
            # 共享基础设施（CI、docs、plan、根配置）允许任何窗口改，但要提示
            if not path.startswith(SHARED_PREFIXES):
                warn(f"{path} 不属于任何窗口的 allow 列表（共享文件），请确认无冲突")
            continue
        if only_window and owner != only_window:
            err(f"{path} 属于 {owner}，不属于本次申报的 {only_window}")


def check_diff_hygiene(changed: list[str]) -> None:
    diff = git("diff", "--unified=0", "origin/main...HEAD")
    if not diff:
        diff = git("diff", "--unified=0", "HEAD~1")
    for marker in ("<<<<<<<", ">>>>>>>"):
        if any(line.startswith(marker) for line in diff.splitlines()):
            err(f"发现未解决的冲突标记 {marker}")
            break

    for path in changed:
        if Path(path).name.startswith(".env") and not path.endswith(".example"):
            err(f"疑似提交了真实环境文件 {path}")
        if path.endswith((".pem", ".key", ".p12")):
            err(f"疑似提交了密钥文件 {path}")


def check_migrations() -> None:
    versions = ROOT / "backend" / "migrations" / "versions"
    if not versions.exists():
        return
    revs: dict[str, str | None] = {}
    pattern = re.compile(r"^revision:\s*str\s*=\s*[\"']([^\"']+)[\"']", re.M)
    down = re.compile(r"^down_revision:\s*str\s*\|\s*None\s*=\s*(.+)$", re.M)

    for f in sorted(versions.glob("*.py")):
        text = f.read_text(encoding="utf-8")
        m = pattern.search(text)
        if not m:
            warn(f"迁移文件 {f.name} 未找到 revision 声明")
            continue
        revs[m.group(1)] = f.name
        if not re.search(r"def downgrade\(\)", text):
            err(f"迁移 {f.name} 缺少 downgrade()")

    heads = set(revs)
    for f in versions.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        d = down.search(text)
        if d:
            for r in re.findall(r"[\"']([^\"']+)[\"']", d.group(1)):
                heads.discard(r)

    if len(heads) > 1:
        err(f"迁移出现多头 head: {sorted(heads)}（需 alembic merge 生成合并 revision）")
    elif not heads:
        err("未找到任何迁移 head，检查 migrations/versions 命名规范")


def check_module_readme() -> None:
    mods = ROOT / "backend" / "app" / "modules"
    for d in sorted(p for p in mods.iterdir() if p.is_dir()):
        readme = d / "README.md"
        if not readme.exists():
            warn(f"模块 {d.name} 缺少 README.md（应写明 owner 窗口与端点清单）")
        elif "归属窗口" not in readme.read_text(encoding="utf-8"):
            warn(f"模块 {d.name} 的 README.md 未写明归属窗口")


def changed_files(base: str) -> list[str]:
    out = git("diff", "--name-only", f"{base}...HEAD")
    if not out.strip():
        out = git("diff", "--name-only", "HEAD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window")
    ap.add_argument("--changed", action="store_true")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--labels", default=os.getenv("PR_LABELS", ""))
    args = ap.parse_args()

    plan = load_plan()
    if not plan:
        return 1

    check_plan_shape(plan)
    check_migrations()
    check_module_readme()

    if args.changed or args.window:
        files = changed_files(args.base)
        labels = {x.strip() for x in args.labels.split(",") if x.strip()}
        check_scope(files, plan, args.window)
        check_frozen(plan, files, labels)
        check_diff_hygiene(files)
        print(f"检查改动文件 {len(files)} 个")

    total_issues = sum(len(w.get("issues") or []) for w in plan.get("windows") or [])
    for w in errors:
        print(f"ERROR   {w}")
    for w in warnings:
        print(f"WARN    {w}")
    print(f"\n{len(plan.get('windows') or [])} 个窗口 / {total_issues} 条 issue")
    print(f"结果：{len(errors)} error, {len(warnings)} warning")

    if errors:
        return 1
    return 2 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
