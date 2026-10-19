# 评审修复任务拆分 · 2026-10-19

> 来源：`docs/PRODUCT-REVIEW-2026-10-19.md`（2×P0 / 4×P1 / 6×P2，共 11 条）
> 拆成 **18 个可独立验收的任务（R01–R18）**，按现有 `plan/windows.yaml` 的窗口边界归属。
> 本文只做拆分与排期建议，**不改 `windows.yaml`**（冻结文件，由 Merge Marshal 在波次边界改）。
> 状态列取值同其他计划：`TODO` / `DOING` / `REVIEW` / `DONE` / `BLOCKED`。

## 分派：5 个文件零交叉的执行流

18 个任务拆成 5 份**自包含**文档，供 5 个 Agent 并行执行。每份文档都写清了自己的可改/禁改路径、冻结契约、验收命令与 PR 规则。R09 / R16 / R17 因横跨前后端被拆到两条流，实际展开为 23 个执行单元（S1-01 … S5-03）。

| 流 | 文档 | 范围 | 任务 | 窗口 | 启动条件 |
|---|---|---|---|---|---|
| S1 | [`REVIEW-2026-10-19/S1-auth-vertical.md`](REVIEW-2026-10-19/S1-auth-vertical.md) | 认证全链路：Cookie + 登录页 + 限流提示 | R01 R02 R03 R11 R12 R13 R16(前端半) | W01 + W10 | 立即（关键路径） |
| S2 | [`REVIEW-2026-10-19/S2-persistence.md`](REVIEW-2026-10-19/S2-persistence.md) | intake/goals 落库 + 事务审计 | R04 R05 R09(后端码) | W02 | 立即 |
| S3 | [`REVIEW-2026-10-19/S3-deploy-bootstrap.md`](REVIEW-2026-10-19/S3-deploy-bootstrap.md) | 镜像、首启三步、同源注入、seed | R06 R07 R16(部署半) R17(seed) | 待定（D1） | Marshal 授权后 |
| S4 | [`REVIEW-2026-10-19/S4-console-ui.md`](REVIEW-2026-10-19/S4-console-ui.md) | 目标直建、提交反馈、导航、预取、快捷键 | R08 R09(前端) R10 R14 R15 R17(UI) | W10 | 立即（与 S1 同窗并行需授权） |
| S5 | [`REVIEW-2026-10-19/S5-regression-gate.md`](REVIEW-2026-10-19/S5-regression-gate.md) | 评审证据脚本化 + CI 入口 + 状态回填 | R18 | W09 | 立即（先写红脚本） |

**文件交叉核对（零重叠）**

```
S1  backend/app/modules/identity/**  +  web: login / features/auth / lib/api/{client,errorText} / components/ui
S2  backend/app/modules/{intake,goals}/**  +  对应 tests
S3  backend/Dockerfile  deploy/**  Makefile  README.md  .env.example  backend/scripts/seed_demo.py
S4  web: app/(console)/**  features/{goals,inbox,today,overview,projects}  components/shell  config/nav.ts  lib/api/{hooks,types}
S5  scripts/review-regression.sh  .github/workflows/review-regression.yml  （仅本文件的状态回填）
```

**运行期依赖（不影响并行开工）**

- S4 验收"提交后数据还在"需要 S2-01 已合 → 未合时先交付 UI，PR 标 `blocked-by: S2`。
- S3-03 与 S1-04 共用「同源 `/api/v1`」契约（C4/C5），两边各自实现，互不等待。
- S5 脚本先红后绿：红是预期，不是失败。
- 唯一需要人拍的是 **D1**（`Dockerfile` / `deploy/` 根 / `Makefile` / README 无窗口归属），S3 在获批前只能写 `seed_demo.py`。

**总表状态只由 S5 回填**，其它流不要改本文件，避免 5 个 Agent 互相覆盖。


## 0. 拆法说明（为什么不是 11 条 1:1）

| 原则 | 结果 |
|---|---|
| 一个任务 = 一次可独立验收的改动 | Issue 1 拆成后端 cookie / 前端登录页 / e2e 三段（跨窗口，且能分段验） |
| 同一根因合并 | Issue 4 的"无创建入口"与 Issue 5 的"无反馈"不合并（不同页面、不同验收） |
| 冻结文件单独走 PR | `nav.ts`（R14）不与其它 UI 改动同 PR |
| 无归属目录先挂决策 | `backend/Dockerfile`、`deploy/` 根、根 `README.md` **不在任何窗口 allow 内** → 决策项 D1 |
| 顺手审计升级为任务 | Issue 2 的"排查其余 flush()"独立成 R05（W01 持有 `core/`，有全局视角） |

## 1. 任务总表

| ID | 任务 | 级别 | 窗口 | 依赖 | 对应 Issue | 状态 |
|---|---|---|---|---|---|---|
| R01 | 后端 refresh 改走 HttpOnly Cookie | P0 | W01 | — | 1 | DONE |
| R02 | 前端 `/login` 页与 401 恢复路径 | P0 | W10 | R01 | 1 | TODO |
| R03 | 认证全链路 e2e（登录→过期→续期→登出） | P0 | W10 | R02 | 1 | TODO |
| R04 | intake/goals 写路径显式事务提交 | P0 | W02 | — | 2 | DONE |
| R05 | 全仓 flush-only 写路径审计 | P0 | W01 | R04 | 2 | DONE |
| R06 | api 镜像纳入 `scripts/` 与 `migrations/` | P1 | **D1** | — | 3 | DONE |
| R07 | 首启三步 + make 目标 | P1 | **D1** | R06 | 3 | DONE |
| R08 | 目标页直接创建表单 | P1 | W10 | — | 4 | DONE |
| R09 | 三问失败文案按 `error.code` 映射 | P1 | W10 | 错误码确认（W02） | 4 | DONE |
| R10 | 收件箱提交成功/失败反馈 | P1 | W10 | R04 | 5 | DONE |
| R11 | 限流 `retry_after` 与解锁提示数据 | P1 | W01 | — | 6 | DONE |
| R12 | 登录页展示剩余锁定时间 | P1 | W10 | R02, R11 | 6 | TODO |
| R13 | request_id 折叠 + unauthorized 动作化 | P2 | W10 | R02 | 7 | TODO |
| R14 | nav「占位」徽标与实现状态对齐 | P2 | W10 | — | 8 | DONE |
| R15 | RSC 预取 `ERR_ABORTED` 收敛 | P2 | W10 | — | 9 | DONE |
| R16 | API Base URL 注入与同源相对路径 | P2 | W10 + **D1** | — | 10 | DOING |
| R17 | `make seed-demo` + 示例标注 + 快捷键帮助 | P2 | W10 | R04 | 11 | DONE |
| R18 | 评审回归脚本（证据表 → 可执行断言） | P2 | W09 | R01–R04 | 验收 | DONE |

进度：`13/18 DONE`（R16 DOING；R02 / R03 / R12 / R13 TODO——均属 S1 前端，登录页未落地）

## 2. 依赖与排期

```
P0 双轨（两条互不依赖，必须并行）
  W01: R01 ──────────────► R05
  W10:      └► R02 ─► R03
  W02: R04 ──────────────────────► (R09 错误码确认)

P1
  W10: R03 ─► R08 ─► R09 ─► R10 ─► R12
  W01: R05 ─► R11
  D1 : R06 ─► R07

P2（可在 P1 空档插入，不与上面抢同一文件）
  W10: R13、R14、R15、R16（前端半）、R17
  W09: R18
```

**三个排期结论**

1. **P0 阶段开两条泳道**：W01（认证后端）与 W02（落库）毫无文件交集，必须并行，否则 P0 修复排队 2 天。
2. **W10 是后续瓶颈**：18 条里 10 条落在 `web/`。建议 R13–R17 合成一个 "体验清理" 分支连续做，不逐条开 PR；若想加速，需 Marshal 额外授权一个前端窗口（W10 同窗口不并行）。
3. **`R01` 未合入前不要做 `R02`**：cookie 契约没定，前端写完必返工。

## 3. 任务卡

### P0

#### R01 后端 refresh 改走 HttpOnly Cookie · W01
- **现象**：`POST /auth/login` 200 但无 `Set-Cookie`；`/refresh` 只接受 body 里的 `refresh_token`，前端却发空 body + `credentials: include`（`web/src/lib/api/client.ts`）。
- **改**
  - `backend/app/modules/identity/router.py`：`/login`、`/refresh` 成功时写 `refresh_token` Cookie（`HttpOnly; SameSite=Lax; Path=/api/v1/auth; Max-Age=<refresh ttl>`，`Secure` 由环境开关控制，本地 http 不能带）；`/logout` 清除；`/refresh` **优先读 Cookie，回退 body**。
  - `backend/app/modules/identity/schemas.py`：`RefreshRequest.refresh_token` 改可选（保留小程序/设备用 body 的场景）。
  - Cookie 名与 TTL 收敛到 `identity` 模块常量，不要散落在 router。
- **验收**
  - `curl -D - -X POST localhost:8080/api/v1/auth/login -d '{"subject":"...","password":"..."}' -H 'Content-Type: application/json'` → 响应头含 `Set-Cookie: refresh_token=...; HttpOnly; SameSite=Lax`，且 body **不再**必须暴露 `refresh_token`（可保留字段做兼容，但前端不依赖）。
  - 无 body 的 `POST /auth/refresh` 带 Cookie → 200 新 access token。
  - `POST /auth/refresh` 带 body → 仍 200（回归：小程序场景不破）。
  - logout 后再 refresh → 401 `unauthorized`。
- **测试**：`backend/tests/test_identity.py` 加 cookie 三例（登录写 cookie / 空 body 刷新 / 登出失效）。
- **注意**：`modules/identity/` 在 W01 allow 内，无需 shared-registry；但 **不要动** `backend/app/api/router.py`。

#### R02 前端 `/login` 页与 401 恢复路径 · W10
- **改**
  - 新增 `web/src/app/login/page.tsx`（在 `(console)` 之外，独立 layout，不挂侧边栏）+ `web/src/features/auth/LoginForm.tsx`。
  - `web/src/lib/api/client.ts`：刷新失败（非网络错误）→ `location.assign('/login?next=' + encodeURIComponent(path))`；避免并发多次跳转（标志位）。
  - 登录成功：写内存 token + `router.replace(next ?? '/today')`；首屏无 token 时先试一次静默 refresh，再决定是否跳登录。
- **验收**
  - 清 Cookie 访问 `/today` → 落到 `/login?next=/today`，登录后回跳。
  - 令牌人为过期（改短 TTL 或清内存 token）→ 页面不报错、静默续期成功。
  - 登出入口可见，登出后回 `/login` 且刷新无法恢复。
- **不要**：不要为登录页改 `web/src/config/nav.ts`（冻结文件，且登录页不在侧边栏）。

#### R03 认证全链路 e2e · W10
- **改**：`web/e2e/auth.spec.ts`。
- **用例**：① 错误密码 → 显示 `validation_failed`/`unauthorized` 文案；② 正确登录 → `/today` 有数据；③ 失效 access token → 自动续期，用户无感（无错误态闪烁）；④ 登出 → 再访问回登录页。
- **验收**：`pnpm e2e`（或仓库现有 e2e 命令）全绿；失败时截图可定位。

#### R04 intake/goals 写路径显式事务提交 · W02
- **根因**：`backend/app/core/db.py#get_db()` 明示"业务层不 commit"，但 `intake/service.py#capture`、`goals/service.py#create_goal/patch_goal` 只 `flush()`；approvals/devices/ledger 等模块都 commit。
- **改**：在 service 写路径末尾显式 `await session.commit()`（对齐既有模块），**不改 `get_db()` 全局语义**（不引入 commit-on-success 中间件——那会让"故意不提交"的路径失去表达力，且属 `core/` 冻结区）。
- **验收**
  - `POST /intake/items` → 201 → `GET /intake/items` 立刻可见；`psql -c "select count(*) from intake_items"` > 0。
  - `POST /goals/` 同上；`PATCH /goals/{id}`（冻结/降级）后 GET 反映新状态。
  - 三问结构化结果随 intake 一起落库。
- **测试**：`backend/tests/test_intake.py`、`test_goals.py` 各加"POST→GET 可见"断言（每个写端点一条）。

#### R05 全仓 flush-only 写路径审计 · W01
- **改**：审计 9 个后端模块的写路径，产出表（模块 / 写方法 / 是否 commit / 结论）附在 PR 描述。
- **验收**：确认仅 intake、goals 遗漏；发现其它遗漏 → **开 issue 给对应窗口**，不顺手改（边界规则 §4.3）。
- **产出**：可考虑把结论补进 `docs/ERROR-CODES.md` 附近的内核说明，或单开 ADR snippet（W01 有 `docs/adr/`）。

### P1

#### R06 api 镜像纳入 `scripts/` 与 `migrations/` · 窗口待定（D1）
- **改**：`backend/Dockerfile` 复制 `scripts/`、`migrations/`；核对 `backend/.dockerignore` 未排除。
- **验收**：全新 `docker compose up` 后 `docker compose exec api ls /app/scripts/create_user.py` 存在；`docker compose exec api python scripts/create_user.py --help` 可运行。
- **阻塞**：`backend/Dockerfile` 不在 W01–W10 任何 allow 内 → 见 D1。

#### R07 首启三步 + make 目标 · 窗口待定（D1）
- **改**：根 `README.md` 增加"首次启动三步"（migrate → create_user → login）；`Makefile` 增加 `make bootstrap` / `make create-owner`（包装 `docker compose exec`）。
- **验收**：清空 volume 后仅按 README 三条命令，10 分钟内能登录进控制台；命令可复制粘贴即用（含示例邮箱/密码占位）。
- **不做**：`/setup` 首启引导页（见 D3）。

#### R08 目标页直接创建表单 · W10
- **改**：`web/src/app/(console)/goals/` + `web/src/features/goals/GoalCreateForm.tsx`，字段 track / template / title / acceptance / budget / deadline，走既有 `POST /goals/`。
- **验收**：未配置 `LITELLM_BASE_URL` 也能建目标（不依赖三问）；建完列表立即出现；空态文案从"先去收件箱"改为"直接新建 / 从收件箱三问"。
- **前置确认**：`POST /goals/` 是否接受 budget/deadline；若 schema 缺字段 → 开 W02 issue（跨窗口只读，勿自改）。

#### R09 三问失败文案按 `error.code` 映射 · W10
- **改**：删除 `InboxView` 里写死的"创建失败：验收标准不能为空"，改走 `web/src/lib/api/errorText.ts`；对 `upstream_error` / `not_implemented` 给专用文案（"AI 服务未配置，请设置 LITELLM_BASE_URL"）。
- **前置确认**（W02，只读核对）：未配置 LiteLLM 时三问端点抛的是 `UpstreamError`(502) 还是裸 500。若是 500 → 开 W02 issue 改成 `NotImplementedYetError`，前端才能稳定映射。
- **验收**：断开 LLM 配置后点三问 → 文案指向配置项而非"验收标准为空"。

#### R10 收件箱提交成功/失败反馈 · W10
- **改**：`InboxView` 的 `onSuccess` 加一次性内联反馈（按钮文案短暂变"已记下 ✓"或新条目高亮 2s）；失败走 `errorText(code)`，区分 401 / 网络错误。
- **验收**：提交后不切页即可确认成功；`R04` 未合入时应能看出"数据其实没落库"（联调时手动验证）。

#### R11 限流 `retry_after` 与解锁提示数据 · W01
- **改**：`RateLimitedError` 抛出处带 `details={"retry_after": <秒>}`，并在响应头加 `Retry-After`；`identity/service.py` 从固定窗口算剩余秒数（`ratelimit.py` 的 TTL 已存在，补一个 `ttl()` 读取）。
- **验收**：连续失败第 6 次 → 429，body `details.retry_after > 0`，响应头 `Retry-After` 存在；等待过期后可再次登录。
- **不做**：双维度限流与锁定豁免（安全策略变更，见 D2）。

#### R12 登录页展示剩余锁定时间 · W10
- **改**：`errorText`/登录表单支持读取 `retry_after`，显示"还需 X 分钟"，替代"请 15 分钟后再试"。
- **验收**：触发限流后页面显示具体剩余时间；倒计时结束可重试。

### P2

#### R13 request_id 折叠 + unauthorized 动作化 · W10
- **改**：`web/src/components/ui/index.tsx` 错误态把 `request_id=xxx` 收进"复制诊断信息"或灰显小字；`unauthorized` 文案旁提供"去登录"按钮（跳 `/login?next=`）。
- **验收**：错误态主信息只有一句人话；诊断信息一键可复制。

#### R14 nav「占位」徽标与实现状态对齐 · W10
- **改**：`web/src/config/nav.ts` 的 `stub` 改为真实状态（或改成 `beta` 语义），Sidebar 渲染随之调整。
- **验收**：已完整实现的 today/inbox/goals/execution/team/ledger 不再显示"占位"。
- **注意**：`nav.ts` 是**冻结文件**（owner W10）→ 单独 PR + `x-shared-registry` label，不与其它改动混。
- **可选**：CI 校验 `stub` 与页面实现一致性 → 由 W09 单开 issue（`ci.yml` 归 W09）。

#### R15 RSC 预取 `ERR_ABORTED` 收敛 · W10
- **改**：侧边栏 `<Link>` 设 `prefetch={false}` 或用 `prefetch` 策略按可见性触发；确认非 401 重试逻辑放大。
- **验收**：Console 中 `?_rsc` 中断数量显著下降（给出改前/改后计数）；页面切换无白屏。

#### R16 API Base URL 注入与同源相对路径 · W10 + D1
- **改**：`web/src/lib/api/client.ts#apiBaseUrl()` 缺省返回 `""`（同源相对路径 `/api/v1` 语义），删除硬编码 `http://localhost:8000/api/v1`；构建期注入改为可选（compose build arg 传 `/api/v1` 或留空）。
- **验收**：`docker compose up` 后浏览器 Network 里请求都是同源 `/api/v1/*`；换非 localhost 域名访问不直连 :8000。
- **注意**：compose 侧改动归 D1 窗口，前端侧归 W10 —— 一个任务两个 owner，**PR 分两个**。

#### R17 `make seed-demo` + 示例标注 + 快捷键帮助 · W10
- **改**
  - `scripts/seed-demo.*`（属 `scripts/` 共享区，发起窗口拥有）：复刻评审时造的 2 goals / 4 intake / 5 tasks / 4 approvals / 10 ledger / 4 devices。
  - Makefile 目标 `make seed-demo`。
  - UI：overview/projects 的"示例"标签与真实数据页统一；新增全局 `?` 快捷键面板（复用 today 页 J/K/Enter/X）。
- **验收**：空库执行 `make seed-demo` 后各页有数据；`?` 在任何页呼出快捷键面板；示例数据有明确标注且可一键清除。
- **依赖**：需 R04 先修（否则 seed 写不进 intake/goals）。

#### R18 评审回归脚本 · W09
- **改**：把评审"证据摘录"表变成 `scripts/review-regression.sh`：断言 login 有 `Set-Cookie`、POST→GET 可见、6 次失败返回 429+`retry_after`、受保护页 401。
- **验收**：脚本在修前全红、修后全绿；可作为后续修复的固定门禁。

## 4. 决策项（需人拍，不要自行假设）

| ID | 事项 | 建议 |
|---|---|---|
| **D1** | `backend/Dockerfile`、`deploy/` 根、根 `README.md`、`Makefile` **无窗口归属**（`deploy/` 只有 `deploy/agent/` 归 W05） | 新增 **W13 部署与首启**（allow: `deploy/`、`backend/Dockerfile`、根 `README.md`、`Makefile`），在 Wave 边界由 Marshal 登记；否则 R06/R07/R16(部署半) 无处落地 |
| **D2** | 限流按 subject 键控可被恶意锁死 owner 邮箱（评审 Issue 6 附带的安全问题） | 不在 R11 里顺手改。单开 issue + ADR：IP+subject 双维度、owner 豁免、锁定观察窗口 |
| **D3** | 是否做 `/setup` 首启引导页 | P1 只做 CLI 三步（R07）；`/setup` 涉及"首启开放写 owner 密码"的暴露面，留 P2 并需 ADR |
| **D4** | W10 单窗口承接 10 条前端任务的吞吐 | R13–R17 打包成一个分支连续做；若需并行，由 Marshal 授权第二个前端窗口 |

## 5. 每个任务的收尾动作（沿用 `plan/README.md` §3）

```
1. 本文对应行状态 → DOING
2. git pull --rebase origin main
3. 写代码 + 测试 + ./scripts/pr_precheck.sh --local
4. 刷新 .fleet/handoff/W<NN>.md（≤10 行）
5. commit: fix(#<issue>): <描述>   ← issue 号指向 GitHub issue，不要把 R01 写进 commit
6. PR 描述 + 本文状态 → REVIEW / DONE
```

## 6. 完成判据（对照评审结论）

- [ ] 未注入 token 的浏览器能完成"登录 → 使用 → 过期后被续期 → 登出"（R01–R03）
  —— 后端 Cookie 链路已由 S5 A2/A3 验证；登录页（R02/R03）未落地，浏览器侧仍走不通。
- [x] 收件箱"记下来"刷新后仍在，psql 行数增加（R04）
  —— S5 A4/A5 在真实库验证 POST→GET 可见（`[review-test]` 记录 3 轮均命中）。
- [x] 清库重启后 10 分钟内能登录（R06–R07）
  —— S3 用空 volume 全量构建，migrate → `make create-owner` → 登录 200。
- [x] 不配 LLM 也能创建目标，三问失败给的是正确原因（R08–R09）
  —— S4 目标直建表单 + `error.code` 映射；Playwright 覆盖。
- [ ] 提交有反馈、限流有剩余时间（R10–R12）
  —— R10 已做（"已记下 ✓"）；R11 后端 `retry_after` 已由 S5 A7 验证（900s）；
  R12 登录页展示剩余时间随 R02 一起待做。
- [x] Console 无成片 `ERR_ABORTED`，请求全同源（R15–R16）
  —— S4 实测 `_rsc` 333 → 37、整页跳转中断 5 → 0；S3 实测全部请求为同源 `/api/v1`。
  R16 的前端兜底（`client.ts` 删除 `localhost:8000` 硬编码）仍待 S1，故 R16 记 DOING。

**回填说明（S5，2026-10-19）**

- 状态依据：S1 后端（`win/s1-auth-backend` 727fecf）、S2（8fde0d8 / PR #25）、
  S3 / S4 工作区改动、本流脚本实测。
- 回归脚本修前红（A2–A5/A7）→ 修后绿（7/7），输出见
  [`REVIEW-2026-10-19/S5-regression-gate.md`](REVIEW-2026-10-19/S5-regression-gate.md) §5.1。
- S1 前端（登录页 / auth e2e / 登录页限流倒计时 / 错误态折叠）未落地，
  对应 R02、R03、R12、R13 保持 TODO；R16 前端兜底未改，记 DOING。
