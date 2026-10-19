# S5 · 回归门禁与验收

> 来自 `docs/PRODUCT-REVIEW-2026-10-19.md` 的「附：评审证据摘录」与「建议处理顺序」。
> 本流不修业务代码：把评审时的 6 条手工证据固化成可重复执行的脚本，先红后绿。
> **可以第一个开工**——脚本写完时其它流还没合，正好用它验收。

> **状态：DONE（2026-10-19）**。S5-01/02/03 全部落地。
> 验证：`main` 基线（无 S1/S2）跑出 A2–A5/A7 红、A1/A6 绿，退出码 1；
> 叠加 S1 后端（Cookie / 限流）与 S2（写路径 commit）后 7/7 绿，退出码 0，墙钟 0.84s。
> `shellcheck` 0 error；`actionlint` 对本 workflow 0 error；连续重跑 3 次均 7/7 绿。

## 0. 任务身份

| 项 | 值 |
|---|---|
| 流 | `S5` |
| 名称 | 评审证据脚本化 + 可选 CI 入口 + 验收汇总 |
| 窗口 | `W09`（CI 工具链） |
| 分支 | `win/s5-regression-gate` |
| 规模 | 3 个任务，约 0.5 人日 |
| 阻塞 | 无（脚本先写，断言会随 S1/S2 合入转绿） |

## 1. 文件边界

**可改**

```
scripts/review-regression.sh              # 新建
.github/workflows/review-regression.yml   # 新建（独立 workflow，不动 ci.yml）
plan/REVIEW-2026-10-19-fixes.md           # 仅 S5 在最后回填状态列
```

**禁改**

```
backend/**            # S1 / S2
web/**                # S1 / S4
.github/workflows/ci.yml                  # 冻结，W09-I06 独有；本流不放松任何门禁
Makefile README.md deploy/**              # S3
scripts/{pr_precheck.sh,check_boundaries.py,window-status.sh}   # 既有脚本，不动
```

## 2. 冻结契约

| 编号 | 契约 |
|---|---|
| C1 | Cookie 名 `refresh_token`，`Path=/api/v1/auth`，`HttpOnly`；脚本按此断言，不猜测其它名字 |
| C3 | 错误体 `{error:{code,message,request_id,details?}}`；`rate_limited` 带 `details.retry_after` |
| C11 | 脚本入口固定：`bash scripts/review-regression.sh`，环境变量 `BASE_URL`（默认 `http://localhost:8080`）、`OWNER_EMAIL`、`OWNER_PASSWORD`；**不改 Makefile**（S3 拥有），需要 `make review-check` 时由 S3 加一行调用 |
| C12 | 脚本退出码：0 全绿；1 有断言失败；2 前置条件缺失（未登录/服务未起）。输出固定 6 行结果表 + 末尾汇总 |

## 3. 任务

### S5-01 评审证据脚本化
- **改动**：新建 `scripts/review-regression.sh`（bash + curl + jq；不引入 Python 依赖）。
- **断言清单**（对应评审证据表）

  | # | 断言 | 修前预期 | 修后 |
  |---|---|---|---|
  | A1 | `GET /health` → 200 | 绿 | 绿 |
  | A2 | `POST /api/v1/auth/login` 响应头有 `Set-Cookie: refresh_token=...; HttpOnly` | **红** | 绿（S1-01） |
  | A3 | 仅带 Cookie、空 body 的 `POST /auth/refresh` → 200 + `access_token` | **红** | 绿（S1-01） |
  | A4 | `POST /intake/items` 201 → `GET /intake/items` 能查到该 id | **红** | 绿（S2-01） |
  | A5 | `POST /goals/` 201 → `GET /goals/` 能查到该 id | **红** | 绿（S2-01） |
  | A6 | 未认证 `GET /goals/` → 401 `unauthorized` | 绿 | 绿 |
  | A7 | 连续失败登录 ≥5 次 → 429，body `details.retry_after` > 0 | **红** | 绿（S1-02） |

- **实现要点**
  - 用临时 cookie jar（`mktemp`），结束 `trap` 清理。
  - A4/A5 用 `[review-test]` 前缀造数据，断言后**不删**（删除需要 DELETE 端点，未必有）；脚本可重复执行不因残留失败（按返回 id 精确匹配）。
  - A7 用 `probe+$(date +%s)@example.invalid` 作 subject，**不要用 owner 邮箱**——否则会把真人锁 15 分钟；若后端"先查用户再计数"，此断言会一直是 401，脚本输出 `SKIP` 并注明原因，不算失败。
  - 每个断言打印 `PASS/FAIL/SKIP + 实际值`，失败继续跑完（不要 `set -e` 中断），最后汇总。
- **验收**
  - 修前跑一遍：A2–A5、A7 红，A1/A6 绿，退出码 1。
  - 全修完后跑一遍：全绿，退出码 0，耗时 < 30s。
  - `shellcheck scripts/review-regression.sh` 无 error。

### S5-02 独立 CI 入口（可选但建议）
- **改动**：`.github/workflows/review-regression.yml`，`workflow_dispatch` 手动触发，输入 `base_url`；步骤：checkout → `shellcheck` → 跑脚本（`OWNER_PASSWORD` 从 secrets 取）。
- **验收**：workflow 语法通过 `actionlint`；手动触发能跑；**不接入 PR 必过门禁**（依赖环境，避免阻塞别人）。
- **注意**：不要改 `ci.yml`（冻结）；不要把密码写进 workflow 文件。

### S5-03 验收汇总与状态回填
- **改动**：全部流合并后，在 `plan/REVIEW-2026-10-19-fixes.md` 回填 18 条状态（该文件由 S5 独占写，避免多流冲突），并在本文 §5 记录最终脚本输出。
- **验收**：总表状态与 GitHub issue 一致；脚本输出贴进 PR 描述；「完成判据」6 条逐项打勾。

## 4. 自检

```bash
shellcheck scripts/review-regression.sh
BASE_URL=http://localhost:8080 OWNER_EMAIL=owner@example.com OWNER_PASSWORD=... \
  bash scripts/review-regression.sh; echo "exit=$?"
./scripts/pr_precheck.sh --local
```

## 5. 完成定义

- [x] 脚本可在 30 秒内跑完 7 条断言，输出可读
- [x] 修前红、修后绿，两次输出都留档在 PR（见 §5.1；PR 未开时先留在此处）
- [x] 不锁死 owner 账号（A7 用一次性 probe subject + 文档保留段 IP）
- [x] workflow 可选、可手动触发、不阻塞主 CI（`workflow_dispatch`，无 `pull_request` 触发）
- [x] 总表状态回填完成（`plan/REVIEW-2026-10-19-fixes.md`，S5 独占写）

### 5.1 修前 / 修后输出

**修前**（`main` 基线，未合 S1/S2；仅用 S3 的 Dockerfile 让 `create_user.py` 可运行）：

```
review-regression · 2026-10-19T11:06:59Z · 目标 http://127.0.0.1:18000
#   结果 断言 · 实际值
-------------------------------------------------------------------------------
A1  PASS  GET /health → 200 · 200
A2  FAIL  login 写 refresh_token Cookie · 无 Set-Cookie: refresh_token
A3  FAIL  空 body 带 Cookie 刷新 → 新 access · HTTP 422 · validation_failed
A4  FAIL  POST intake → GET 能查到该 id · POST 201 但 GET 未命中（HTTP 200）
A5  FAIL  POST goals → GET 能查到该 id · POST 201 但 GET 未命中（HTTP 200）
A6  PASS  未认证 GET /goals/ → 401 · 401 · unauthorized
A7  FAIL  连续失败登录 → 429 + retry_after · 第 6 次 HTTP 429 · rate_limited · retry_after=缺失
-------------------------------------------------------------------------------
汇总：PASS 2 · FAIL 5 · SKIP 0 · 耗时 0s
结果：FAIL（退出码 1）
```

**修后**（S1 后端 + S2 已合入，S3 镜像 / 编排）：

```
review-regression · 2026-10-19T11:07:45Z · 目标 http://127.0.0.1:18000
#   结果 断言 · 实际值
-------------------------------------------------------------------------------
A1  PASS  GET /health → 200 · 200
A2  PASS  login 写 refresh_token Cookie · set-cookie: refresh_token=<redacted>; HttpOnly; Max-Age=1209600; Path=/api/v1/auth; SameSite=lax
A3  PASS  空 body 带 Cookie 刷新 → 新 access · 200 · access_token 732 字符
A4  PASS  POST intake → GET 能查到该 id · id=90630be0… · 命中 1
A5  PASS  POST goals → GET 能查到该 id · id=aa2060b9… · 命中 1
A6  PASS  未认证 GET /goals/ → 401 · 401 · unauthorized
A7  PASS  连续失败登录 → 429 + retry_after · 第 6 次 429 · retry_after=900s · Retry-After: 900
-------------------------------------------------------------------------------
汇总：PASS 7 · FAIL 0 · SKIP 0 · 耗时 1s
结果：PASS（退出码 0）
```

- 墙钟 0.84s（`time` 实测）；连续重跑 3 次均全绿（A7 用一次性 subject + `203.0.113.0/24`，不累积 IP 锁）。
- 退出码 2 的三类前置已验：缺 `OWNER_PASSWORD`、owner 密码错、服务不可达。
- `shellcheck scripts/review-regression.sh` → 0；`actionlint .github/workflows/review-regression.yml` → 0。
  全仓 `actionlint` 仍有 `agent-release.yml` 的既有告警，属 W05 文件，本流不改。

### 5.2 说明

- C12 写「固定 6 行结果表」，但 S5-01 断言清单与完成定义均为 7 条（A1–A7）；以断言清单为准，脚本输出 7 行。
- A4/A5 会留下 `[review-test]` 前缀记录（无 DELETE 端点），按返回 id 精确匹配，重复执行不影响结果。

## 6. PR 与提交

单 PR：`[W09] test: review regression script`，提交前缀 `test(#<issue>): regression-script`。
PR 描述必须贴：修前输出（红）+ 修后输出（绿）+ 运行耗时。

## 7. 卡住时

- 服务未起 / 无 owner → 退出码 2 并打印缺失项，不要假装通过。
- 断言涉及的行为尚未实现 → 保留 FAIL（这是脚本的价值），在 PR 里标 `blocked-by: S1/S2`。
- 需要 `make review-check` → 在 S3 的 PR 下留言加一行，不要自己改 Makefile。
