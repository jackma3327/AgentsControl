#!/usr/bin/env bash
# 合并前预检：本地与 CI 都能跑，秒级完成，只读不写。
#
# 用法
#   ./scripts/pr_precheck.sh                    # 全量（CI 模式）
#   ./scripts/pr_precheck.sh --local            # 本地：不依赖 gh，允许未提交改动
#   ./scripts/pr_precheck.sh --window W03       # 校验改动是否越界到 W03 之外
#   ./scripts/pr_precheck.sh --json             # 机器可读输出
#
# 退出码 0 通过 / 1 阻断 / 2 只有警告
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

MODE=ci
WINDOW=""
JSON=0
for arg in "$@"; do
  case "$arg" in
    --local) MODE=local ;;
    --json) JSON=1 ;;
    --window) shift ;;
    W0*|W1*) WINDOW="$arg" ;;
  esac
done
# 支持 --window W03 的写法
while [[ $# -gt 0 ]]; do
  case "$1" in
    --window) WINDOW="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

ERRORS=0
WARNINGS=0
report() {
  local level="$1"; shift
  local msg="$*"
  if [[ "$level" == "E" ]]; then
    ERRORS=$((ERRORS + 1)); printf 'ERROR  %s\n' "$msg" >&2
  else
    WARNINGS=$((WARNINGS + 1)); printf 'WARN   %s\n' "$msg" >&2
  fi
}

# 选一个装了 PyYAML 的解释器：优先 $PYTHON，其次当前 venv，最后系统 python
pick_python() {
  for cand in "${PYTHON:-}" python3 python; do
    [[ -z "$cand" ]] && continue
    if "$cand" -c "import yaml" >/dev/null 2>&1; then echo "$cand"; return 0; fi
  done
  return 1
}
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
PYTHON="$(pick_python || true)"
if [[ -z "$PYTHON" ]]; then
  echo "WARN   未找到带 PyYAML 的 python，跳过边界检查（pip install pyyaml 或在 venv 中运行）" >&2
  PYTHON="$(command -v python3 || command -v python)"
  SKIP_BOUNDARY=1
fi

# ---------------------------------------------------------------- 1. 冲突标记
if git diff --cached --name-only | grep -q .; then
  DIFF=$(git diff --cached)
else
  DIFF=$(git diff HEAD)
fi
if printf '%s' "$DIFF" | grep -qE '^\+(<<<<<<<|>>>>>>>)'; then
  report E "存在未解决的冲突标记"
fi

# ---------------------------------------------------------------- 2. 密钥文件
if git status --porcelain | awk '{print $2}' | grep -qE '(^|/)\.env$|\.pem$|\.key$'; then
  report E "疑似暂存了真实密钥文件（.env / .pem / .key）"
fi

# ---------------------------------------------------------------- 3. 计划与边界
if [[ "${SKIP_BOUNDARY:-0}" != "1" && -f scripts/check_boundaries.py ]]; then
  ARGS=(--changed)
  [[ -n "$WINDOW" ]] && ARGS+=(--window "$WINDOW")
  [[ "$MODE" == "ci" ]] && ARGS+=(--base "${BASE_REF:-origin/main}")
  OUT=$("$PYTHON" scripts/check_boundaries.py "${ARGS[@]}" 2>&1)
  CODE=$?
  printf '%s\n' "$OUT" | sed -n 's/^ERROR/ERROR/;s/^WARN/WARN/;p' >&2
  if [[ $CODE -eq 1 ]]; then ERRORS=$((ERRORS + 1)); fi
  if [[ $CODE -eq 2 ]]; then WARNINGS=$((WARNINGS + 1)); fi
else
  report W "缺少 scripts/check_boundaries.py，跳过边界检查（该脚本由 W09-I05 交付）"
fi

# ---------------------------------------------------------------- 4. 迁移单头
if [[ -d backend/migrations/versions ]]; then
  # heads = 全部 revision − 已被 down_revision 引用的节点（含 merge 节点的元组引用）。
  # 旧启发式（比较引用计数）在存在 merge revision 时会误报，已改为集合差（W01-I05）。
  REVS=$(grep -rhoE "^revision: str = ['\"][^'\"]+" backend/migrations/versions/*.py 2>/dev/null | sed "s/.*['\"]//" | sort -u)
  REFS=$(grep -rhoE "^down_revision:.*" backend/migrations/versions/*.py 2>/dev/null | grep -oE "['\"][^'\"]+['\"]" | tr -d "'\"" | sort -u)
  HEADS_LIST=$(comm -23 <(printf '%s\n' "$REVS" | sed '/^$/d') <(printf '%s\n' "$REFS" | sed '/^$/d'))
  HEADS=$(printf '%s' "$HEADS_LIST" | grep -c . || true)
  if [[ "$HEADS" -gt 1 ]]; then
    report E "迁移出现多头（$HEADS 个: $(printf '%s' "$HEADS_LIST" | tr '\n' ' ')），请用 alembic merge 合并"
  fi
  for f in backend/migrations/versions/*.py; do
    [[ -f "$f" ]] || continue
    if ! grep -q "def downgrade" "$f"; then
      report E "迁移 $(basename "$f") 缺少 downgrade()，无法回滚"
    fi
  done
fi

# ---------------------------------------------------------------- 5. 提交消息规范
if [[ "$MODE" == "ci" ]]; then
  BASE="${BASE_REF:-origin/main}"
  if git rev-parse --verify "$BASE" >/dev/null 2>&1; then
    BAD=$(git log --format=%s "$BASE"..HEAD | grep -vE '^(feat|fix|chore|test|docs|refactor|perf|build|ci)(\(#[0-9A-Za-z-]+\)|\(scope\))?: ' | head -5)
    if [[ -n "$BAD" ]]; then
      report W "以下提交不符合 '<type>(#issue): 描述' 规范："
      printf '        %s\n' "$BAD" >&2
    fi
  fi
fi

# ---------------------------------------------------------------- 6. Python 静态检查（有则跑）
if [[ -d backend ]]; then
  if "$PYTHON" -c "import ruff" >/dev/null 2>&1; then
    if ! "$PYTHON" -m ruff check backend/app >/dev/null 2>&1; then
      report E "ruff 检查未通过（cd backend && ruff check app）"
    fi
  else
    report W "未安装 ruff，跳过 lint（pip install ruff）"
  fi
fi

# ---------------------------------------------------------------- 7. Go 静态检查（有则跑）
if [[ -d agent ]] && command -v go >/dev/null 2>&1; then
  if [[ -n "$(gofmt -l agent 2>/dev/null)" ]]; then
    report E "gofmt 未通过：$(gofmt -l agent | tr '\n' ' ')"
  fi
else
  [[ -d agent ]] && report W "未找到 go，跳过 agent 检查"
fi

# ---------------------------------------------------------------- 8. 前端占位符扫描
if [[ -d web/src ]]; then
  if grep -rn "localhost:8000" web/src --include=*.tsx --include=*.ts 2>/dev/null | grep -v "lib/api/client.ts" | grep -q .; then
    report W "web/src 中存在硬编码 API 地址（应走 NEXT_PUBLIC_API_BASE_URL）"
  fi
  if grep -rn "TODO:" web/src --include=*.tsx 2>/dev/null | grep -q .; then
    report W "web/src 中仍有 TODO 注释，确认是否属于本 issue"
  fi
fi

# ---------------------------------------------------------------- 汇总
if [[ "$JSON" == "1" ]]; then
  printf '{"errors":%d,"warnings":%d}\n' "$ERRORS" "$WARNINGS"
else
  printf '\n预检结果：%d error, %d warning\n' "$ERRORS" "$WARNINGS"
fi

if [[ $ERRORS -gt 0 ]]; then exit 1; fi
if [[ $WARNINGS -gt 0 ]]; then exit 2; fi
exit 0
