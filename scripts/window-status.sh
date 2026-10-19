#!/usr/bin/env bash
# 窗口进度看板：从 git + gh 重建 plan/STATUS.md 并打印摘要。
#
# 权威状态永远是 git/PR；本脚本只是「把权威状态渲染成人能读的东西」。
# 因此它不维护任何自己的状态文件——随时跑都是最新真相。
#
# 用法
#   ./scripts/window-status.sh              # 全部窗口
#   ./scripts/window-status.sh W03          # 单个窗口
#   ./scripts/window-status.sh --watch      # 每 30 秒刷新
#   ./scripts/window-status.sh --write      # 同时写 plan/STATUS.md
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

# 需要一个带 PyYAML 的解释器；找不到就明确报错退出（绝不静默出一份空状态）
PYTHON="${PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
  for cand in python3 python "${VIRTUAL_ENV:-/nonexistent}/bin/python"       "/Users/nitai/.workbuddy/binaries/python/envs/default/bin/python"; do
    [[ -z "$cand" ]] && continue
    if "$cand" -c "import yaml" >/dev/null 2>&1; then PYTHON="$cand"; break; fi
  done
fi
if [[ -z "$PYTHON" ]]; then
  echo "未找到带 PyYAML 的 python。请 pip install pyyaml 或用 PYTHON=<path> 指定。" >&2
  exit 1
fi

WATCH=0
WRITE=0
ONLY=""
for arg in "$@"; do
  case "$arg" in
    --watch) WATCH=1 ;;
    --write) WRITE=1 ;;
    W*) ONLY="$arg" ;;
  esac
done

render() {
  "$PYTHON" - "$ONLY" "$WRITE" <<'PY'
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    print("需要 PyYAML：pip install pyyaml", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path.cwd()
plan = yaml.safe_load((ROOT / "plan" / "windows.yaml").read_text(encoding="utf-8"))
only = sys.argv[1] if len(sys.argv) > 1 else ""
do_write = (sys.argv[2] if len(sys.argv) > 2 else "0") == "1"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def gh_prs() -> dict[str, dict]:
    """分支 → PR 摘要。没有 gh 或未登录时安静降级。"""
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", "50",
             "--json", "number,headRefName,title,isDraft,state,mergeable,statusCheckRollup"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=20,
        ).stdout
        prs = json.loads(out or "[]")
    except Exception:
        return {}

    result: dict[str, dict] = {}
    for pr in prs:
        rollup = pr.get("statusCheckRollup") or []
        states = {c.get("conclusion") or c.get("state") for c in rollup if isinstance(c, dict)}
        if not states:
            checks = "—"
        elif states <= {"SUCCESS"}:
            checks = "✓"
        elif states & {"FAILURE", "CANCELLED", "TIMED_OUT"}:
            checks = "✗"
        else:
            checks = "…"
        result[pr.get("headRefName", "")] = {
            "number": pr.get("number"),
            "draft": pr.get("isDraft"),
            "state": pr.get("state"),
            "checks": checks,
            "mergeable": pr.get("mergeable"),
        }
    return result


def plan_statuses(wid: str) -> dict[str, str]:
    """从 plan/Wxx.md 读每个 issue 的声明状态（DONE/REVIEW/TODO）。

    看板以计划状态为准：合并后由窗口 owner 把 REVIEW 改 DONE；
    脚本只负责渲染，不猜"代码看起来写完了没有"。
    """
    path = ROOT / "plan" / f"{wid}.md"
    if not path.exists():
        return {}
    statuses: dict[str, str] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^###\s+(W\d{2}-I\d{2})\b", line)
        if m:
            current = m.group(1)
            continue
        if current:
            m = re.match(r"^\|\s*状态\s*\|\s*([A-Za-z]+)", line)
            if m:
                statuses[current] = m.group(1).upper()
                current = None
    return statuses


def done_issues(branch: str) -> dict[str, str]:
    """从分支提交消息里找 issue 号，仅用于展示"最近提交"，不参与状态判定。"""
    ref = f"origin/{branch}"
    if subprocess.run(["git", "rev-parse", "--verify", ref], cwd=ROOT,
                      capture_output=True).returncode != 0:
        ref = branch
    log = git("log", "--format=%h|%s", f"origin/main..{ref}") if git(
        "rev-parse", "--verify", "origin/main") else git("log", "--format=%h|%s", ref)

    found: dict[str, str] = {}
    for line in log.splitlines():
        if "|" not in line:
            continue
        sha, subject = line.split("|", 1)
        m = re.search(r"\(#([0-9A-Za-z-]+)\)", subject)
        if m:
            # 同一 issue 多次提交时保留最后一次
            found.setdefault(m.group(1), sha)
            found[m.group(1)] = sha
    return found


prs = gh_prs()
rows: list[str] = []
md: list[str] = []
now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")

for w in plan.get("windows") or []:
    wid = w["id"]
    if only and wid != only:
        continue

    branch = w["branch"]
    pr = prs.get(branch)
    done = done_issues(branch)
    declared = plan_statuses(wid)

    if pr:
        pr_label = f"PR #{pr['number']} ({'Draft' if pr['draft'] else pr['state']}, checks: {pr['checks']})"
    else:
        pr_label = "无 PR"

    exists = (ROOT / w["worktree"]).exists()
    header = f"{wid} {w['name']}"
    rows.append(f"\n{header}  分支 {branch}   {pr_label}   worktree {'✓' if exists else '✗'}")
    md.append(f"### {wid} · {w['name']}\n")
    md.append(f"- 分支：`{branch}`　worktree：`{w['worktree']}`　{'存在' if exists else '**未创建**'}")
    md.append(f"- PR：{pr_label}\n")
    md.append("| Issue | 标题 | Wave | 状态 | 最近提交 |")
    md.append("|---|---|---|---|---|")

    finished = 0
    review = 0
    for it in w.get("issues") or []:
        iid = it["id"]
        sha = done.get(iid) or done.get(iid.split("-")[-1])
        state = declared.get(iid, "TODO")
        if state == "DONE":
            finished += 1
        elif state == "REVIEW":
            review += 1
        rows.append(f"  {iid:<9} {it['title']:<24} {state:<6} {(sha or ''):<8}")
        md.append(f"| {iid} | {it['title']} | {it['wave']} | {state} | `{sha or '—'}` |")

    total = len(w.get("issues") or [])
    pct = int(finished / total * 100) if total else 0
    bar = "█" * (pct // 10) + "·" * (10 - pct // 10)
    stale = ""
    if pr and pr.get("state") == "MERGED" and (finished + review) < total:
        stale = "  ⚠ 计划状态滞后（PR 已合并，plan 仍有 TODO）"
    rows.append(f"  进度 {finished}/{total}  {bar} {pct}%" + (f"  待合并 {review}" if review else "") + stale)
    md.append(f"\n**进度 {finished}/{total}（{pct}%）**" + (f"，另有 {review} 条待合并" if review else "") + f"{stale}\n")

print("\n".join(rows))
print(f"\n生成于 {now}　权威状态见 git 与 PR；本视图可用 ./scripts/window-status.sh 随时重建。")

if do_write:
    out = ROOT / "plan" / "STATUS.md"
    body = [f"# 窗口进度（自动生成）\n", f"> 生成时间：{now}　请勿手工编辑，运行 `./scripts/window-status.sh --write` 重建。", ""]
    body.extend(md)
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"\n已写入 {out.relative_to(ROOT)}")
PY
}

if [[ "$WATCH" == "1" ]]; then
  while true; do
    clear
    render
    sleep 30
  done
else
  render
fi
