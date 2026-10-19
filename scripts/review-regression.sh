#!/usr/bin/env bash
# 评审回归脚本（S5-01 / R18）
#
# 把 `docs/PRODUCT-REVIEW-2026-10-19.md` 里的手工证据固化成 7 条可重复断言。
# 不改既有数据；A4/A5 会各留一条 `[review-test]` 前缀记录（无 DELETE 端点，
# 靠返回 id 精确匹配，重复执行不会累积失败）。
#
# 用法：
#   BASE_URL=http://localhost:8080 \
#   OWNER_EMAIL=owner@example.com OWNER_PASSWORD='...' \
#   bash scripts/review-regression.sh
#
# 契约（plan/REVIEW-2026-10-19/S5-regression-gate.md）：
#   C1  Cookie 名 refresh_token、Path=/api/v1/auth、HttpOnly
#   C3  错误体 {error:{code,message,request_id,details?}}；rate_limited 带 details.retry_after
#   C11 入口 bash scripts/review-regression.sh；环境变量 BASE_URL / OWNER_EMAIL / OWNER_PASSWORD
#   C12 退出码 0 全绿 / 1 有断言失败 / 2 前置条件缺失
#
# 设计取舍：
#   - 不用 `set -e`：单条断言失败不能中断整轮，否则看不到全貌；
#   - 不引入 Python 依赖：bash + curl + jq，CI 与本地一致；
#   - A7 用一次性 probe subject + 文档保留段 IP（203.0.113.0/24），
#     避免把 owner 账号或本机 IP 锁 15 分钟，脚本可连续重跑。

set -uo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
BASE_URL="${BASE_URL%/}"
API="${BASE_URL}/api/v1"

PASS=0
FAIL=0
SKIP=0
START_TS=$(date +%s)

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/review-regression.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT

# ---------------------------------------------------------------- 基础函数

# 前置条件缺失 → 退出码 2（不是断言失败）
precondition_failed() {
  printf 'PRECONDITION: %s\n' "$*" >&2
  exit 2
}

# print_row <编号> <断言> <结果> <实际值>
# 断言含中文（终端按双宽渲染），用 printf 补空格对齐会错位；
# 改为「编号 + 结果」定宽、断言与实际值用 ` · ` 分隔，任何终端都稳定。
print_row() {
  printf '%-3s %-5s %s · %s\n' "$1" "$3" "$2" "$4"
}

# row <编号> <断言> <PASS|FAIL|SKIP> <实际值>
row() {
  print_row "$1" "$2" "$3" "$4"
  case "$3" in
    PASS) PASS=$((PASS + 1)) ;;
    FAIL) FAIL=$((FAIL + 1)) ;;
    SKIP) SKIP=$((SKIP + 1)) ;;
  esac
}

# request <out> <headers> <method> <url> [curl 额外参数...] → 打印 HTTP 状态码
request() {
  local out=$1 headers=$2 method=$3 url=$4
  shift 4
  curl -sS --connect-timeout 3 --max-time 20 \
    -o "$out" -D "$headers" -w '%{http_code}' \
    -X "$method" "$url" "$@" 2>/dev/null || true
}

# 从错误体里取 code；非 JSON / 空体返回空串
error_code() {
  jq -r '.error.code // empty' "$1" 2>/dev/null || true
}

# 取字符串字段；缺失返回空串
json_str() {
  jq -r "$2 // empty" "$1" 2>/dev/null || true
}

command -v curl >/dev/null 2>&1 || precondition_failed "缺少 curl"
command -v jq >/dev/null 2>&1 || precondition_failed "缺少 jq（脚本只依赖 curl + jq）"
[ -n "${OWNER_EMAIL:-}" ] || precondition_failed "缺少 OWNER_EMAIL（例：OWNER_EMAIL=owner@example.com）"
[ -n "${OWNER_PASSWORD:-}" ] || precondition_failed "缺少 OWNER_PASSWORD"

printf 'review-regression · %s · 目标 %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$BASE_URL"
printf '%-3s %-5s %s\n' '#' '结果' '断言 · 实际值'
printf '%s\n' '-------------------------------------------------------------------------------'

# ---------------------------------------------------------------- 前置：健康检查 + owner 登录
# 这两步失败说明环境没准备好，继续跑断言只会输出一串假红，按 C12 直接退出 2。

HEALTH_CODE=$(request "$TMP_DIR/health.json" "$TMP_DIR/health.h" GET "$BASE_URL/health")
if [ "$HEALTH_CODE" != "200" ]; then
  precondition_failed "服务不可用：GET $BASE_URL/health → ${HEALTH_CODE}（先 docker compose up）"
fi
row A1 "GET /health → 200" PASS "200"

LOGIN_BODY=$(jq -nc --arg subject "$OWNER_EMAIL" --arg password "$OWNER_PASSWORD" \
  '{subject: $subject, password: $password}')
LOGIN_CODE=$(request "$TMP_DIR/login.json" "$TMP_DIR/login.h" POST "$API/auth/login" \
  -c "$TMP_DIR/cookies.txt" \
  -H 'Content-Type: application/json' \
  --data "$LOGIN_BODY")
if [ "$LOGIN_CODE" != "200" ]; then
  precondition_failed "owner 登录失败：HTTP $LOGIN_CODE $(error_code "$TMP_DIR/login.json")（检查 OWNER_EMAIL/OWNER_PASSWORD；429 表示该来源已被限流）"
fi
ACCESS_TOKEN=$(json_str "$TMP_DIR/login.json" '.access_token')
[ -n "$ACCESS_TOKEN" ] || precondition_failed "登录返回 200 但响应体没有 access_token"

# ---------------------------------------------------------------- A2 Cookie 契约（C1）
COOKIE_LINE=$(grep -i '^set-cookie: *refresh_token=' "$TMP_DIR/login.h" | head -1 || true)
if [ -z "$COOKIE_LINE" ]; then
  row A2 "login 写 refresh_token Cookie" FAIL "无 Set-Cookie: refresh_token"
else
  COOKIE_MASKED=$(printf '%s' "$COOKIE_LINE" | sed -E 's/(refresh_token=)[^;]*/\1<redacted>/I')
  COOKIE_LOWER=$(printf '%s' "$COOKIE_LINE" | tr '[:upper:]' '[:lower:]')
  COOKIE_ATTRS_OK=1
  case "$COOKIE_LOWER" in
    *httponly*) ;;
    *) COOKIE_ATTRS_OK=0 ;;
  esac
  case "$COOKIE_LOWER" in
    *"path=/api/v1/auth"*) ;;
    *) COOKIE_ATTRS_OK=0 ;;
  esac
  if [ "$COOKIE_ATTRS_OK" = "1" ]; then
    row A2 "login 写 refresh_token Cookie" PASS "$COOKIE_MASKED"
  else
    row A2 "login 写 refresh_token Cookie" FAIL "${COOKIE_MASKED}（缺 HttpOnly 或 Path=/api/v1/auth）"
  fi
fi

# ---------------------------------------------------------------- A3 空 body + Cookie 刷新（C1）
# 不传 Content-Type / body：模拟浏览器 fetch(credentials: 'include') 的真实请求形状。
REFRESH_CODE=$(request "$TMP_DIR/refresh.json" "$TMP_DIR/refresh.h" POST "$API/auth/refresh" \
  -b "$TMP_DIR/cookies.txt" -c "$TMP_DIR/cookies.txt")
REFRESH_TOKEN=$(json_str "$TMP_DIR/refresh.json" '.access_token')
if [ "$REFRESH_CODE" = "200" ] && [ -n "$REFRESH_TOKEN" ]; then
  row A3 "空 body 带 Cookie 刷新 → 新 access" PASS "200 · access_token ${#REFRESH_TOKEN} 字符"
elif [ "$REFRESH_CODE" = "200" ]; then
  row A3 "空 body 带 Cookie 刷新 → 新 access" FAIL "200 但响应体无 access_token"
else
  row A3 "空 body 带 Cookie 刷新 → 新 access" FAIL "HTTP $REFRESH_CODE · $(error_code "$TMP_DIR/refresh.json")"
fi

# ---------------------------------------------------------------- A4 intake 写入可见（S2-01）
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
INTAKE_TEXT="[review-test] intake ${STAMP} $$"
INTAKE_CODE=$(request "$TMP_DIR/intake.json" "$TMP_DIR/intake.h" POST "$API/intake/items" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc --arg text "$INTAKE_TEXT" '{text: $text, source: "review"}')")
INTAKE_ID=$(json_str "$TMP_DIR/intake.json" '.id')
if [ "$INTAKE_CODE" != "201" ] || [ -z "$INTAKE_ID" ]; then
  row A4 "POST intake → GET 能查到该 id" FAIL "POST HTTP $INTAKE_CODE · $(error_code "$TMP_DIR/intake.json")"
else
  INTAKE_LIST_CODE=$(request "$TMP_DIR/intake-list.json" "$TMP_DIR/intake-list.h" GET \
    "$API/intake/items?limit=100" -H "Authorization: Bearer $ACCESS_TOKEN")
  INTAKE_HITS=$(jq --arg id "$INTAKE_ID" '[.items[]? | select(.id == $id)] | length' \
    "$TMP_DIR/intake-list.json" 2>/dev/null || echo 0)
  if [ "$INTAKE_LIST_CODE" = "200" ] && [ "$INTAKE_HITS" -ge 1 ]; then
    row A4 "POST intake → GET 能查到该 id" PASS "id=${INTAKE_ID:0:8}… · 命中 $INTAKE_HITS"
  else
    row A4 "POST intake → GET 能查到该 id" FAIL "POST 201 但 GET 未命中（HTTP ${INTAKE_LIST_CODE}）"
  fi
fi

# ---------------------------------------------------------------- A5 goals 写入可见（S2-01）
GOAL_TITLE="[review-test] goal ${STAMP} $$"
GOAL_CODE=$(request "$TMP_DIR/goal.json" "$TMP_DIR/goal.h" POST "$API/goals/" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  --data "$(jq -nc --arg title "$GOAL_TITLE" \
    '{track: "growth", title: $title, acceptance: "GET /goals/ 能查到该 id", horizon: "1d", budget_hours: 1}')")
GOAL_ID=$(json_str "$TMP_DIR/goal.json" '.id')
if [ "$GOAL_CODE" != "201" ] || [ -z "$GOAL_ID" ]; then
  row A5 "POST goals → GET 能查到该 id" FAIL "POST HTTP $GOAL_CODE · $(error_code "$TMP_DIR/goal.json")"
else
  GOAL_LIST_CODE=$(request "$TMP_DIR/goal-list.json" "$TMP_DIR/goal-list.h" GET \
    "$API/goals/?limit=100" -H "Authorization: Bearer $ACCESS_TOKEN")
  GOAL_HITS=$(jq --arg id "$GOAL_ID" '[.[]? | select(.id == $id)] | length' \
    "$TMP_DIR/goal-list.json" 2>/dev/null || echo 0)
  if [ "$GOAL_LIST_CODE" = "200" ] && [ "$GOAL_HITS" -ge 1 ]; then
    row A5 "POST goals → GET 能查到该 id" PASS "id=${GOAL_ID:0:8}… · 命中 $GOAL_HITS"
  else
    row A5 "POST goals → GET 能查到该 id" FAIL "POST 201 但 GET 未命中（HTTP ${GOAL_LIST_CODE}）"
  fi
fi

# ---------------------------------------------------------------- A6 未认证 → 401 unauthorized
# 用 -H 'Authorization:' 显式清空，防止 curl 从环境（netrc）带凭据。
UNAUTH_CODE=$(request "$TMP_DIR/unauth.json" "$TMP_DIR/unauth.h" GET "$API/goals/" \
  -H 'Authorization:')
UNAUTH_ERROR=$(error_code "$TMP_DIR/unauth.json")
if [ "$UNAUTH_CODE" = "401" ] && [ "$UNAUTH_ERROR" = "unauthorized" ]; then
  row A6 "未认证 GET /goals/ → 401" PASS "401 · unauthorized"
else
  row A6 "未认证 GET /goals/ → 401" FAIL "HTTP $UNAUTH_CODE · ${UNAUTH_ERROR:-非标准错误体}"
fi

# ---------------------------------------------------------------- A7 登录失败限流（S1-02）
# 一次性 subject：绝不使用 owner 邮箱，否则会把真人锁 15 分钟。
# X-Forwarded-For 用 203.0.113.0/24（RFC 5737 文档保留段）：让每轮脚本的
# IP 失败计数互相隔离，连续重跑不会累积到 20 次/15 分钟的 IP 锁。
PROBE_SUBJECT="probe+${STAMP}-$$-${RANDOM}@example.invalid"
PROBE_IP="203.0.113.$(( (RANDOM % 200) + 1 ))"
PROBE_BODY=$(jq -nc --arg subject "$PROBE_SUBJECT" '{subject: $subject, password: "not-the-password"}')
LIMIT_ATTEMPTS=0
LIMIT_CODE=""
while [ "$LIMIT_ATTEMPTS" -lt 6 ]; do
  LIMIT_ATTEMPTS=$((LIMIT_ATTEMPTS + 1))
  LIMIT_CODE=$(request "$TMP_DIR/limit.json" "$TMP_DIR/limit.h" POST "$API/auth/login" \
    -H 'Content-Type: application/json' \
    -H "X-Forwarded-For: $PROBE_IP" \
    --data "$PROBE_BODY")
  [ "$LIMIT_CODE" = "429" ] && break
done
LIMIT_ERROR=$(error_code "$TMP_DIR/limit.json")
RETRY_AFTER=$(jq -r '.error.details.retry_after // empty' "$TMP_DIR/limit.json" 2>/dev/null || true)
RETRY_HEADER=$(grep -i '^retry-after:' "$TMP_DIR/limit.h" | head -1 | tr -d '\r' | sed 's/^[Rr]etry-[Aa]fter: *//' || true)
if [ "$LIMIT_CODE" = "429" ] && [ "$LIMIT_ERROR" = "rate_limited" ] &&
  [ -n "$RETRY_AFTER" ] && [ "$RETRY_AFTER" -gt 0 ] 2>/dev/null; then
  row A7 "连续失败登录 → 429 + retry_after" PASS \
    "第 ${LIMIT_ATTEMPTS} 次 429 · retry_after=${RETRY_AFTER}s${RETRY_HEADER:+ · Retry-After: ${RETRY_HEADER}}"
elif [ "$LIMIT_CODE" = "401" ]; then
  # 兼容"先查用户再计数"的实现：账号不存在时永远 401，限流无法从外部触发。
  row A7 "连续失败登录 → 429 + retry_after" SKIP \
    "${LIMIT_ATTEMPTS} 次仍 401：后端先校验用户再计数，未触发限流"
else
  row A7 "连续失败登录 → 429 + retry_after" FAIL \
    "第 ${LIMIT_ATTEMPTS} 次 HTTP $LIMIT_CODE · ${LIMIT_ERROR:-非标准错误体} · retry_after=${RETRY_AFTER:-缺失}"
fi

# ---------------------------------------------------------------- 汇总
ELAPSED=$(( $(date +%s) - START_TS ))
printf '%s\n' '-------------------------------------------------------------------------------'
printf '汇总：PASS %d · FAIL %d · SKIP %d · 耗时 %ds\n' "$PASS" "$FAIL" "$SKIP" "$ELAPSED"
if [ "$FAIL" -gt 0 ]; then
  printf '结果：FAIL（退出码 1）\n'
  exit 1
fi
printf '结果：PASS（退出码 0）\n'
exit 0
