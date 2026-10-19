# 产品评审 · Agent Fleet Control 控制台（2026-10-19）

- 评审对象：http://localhost:8080/（Next.js web + FastAPI api + Go agent，Caddy 同源反代，本地 Docker 部署）
- 仓库：`jackma3327/AgentsControl`，评审基线 `main`
- 方法：浏览器实测（含注入认证后的带数据走查）+ 代码核对 + API 复现
- 结论摘要：**2 个 P0 阻断问题**（认证链路断裂、intake/goals 写入静默丢失），修复前产品端到端不可用；另有 4 个 P1、6 个 P2。

> 说明：为完成带数据评审，评审过程中向本地库插入了演示数据（2 goals / 4 intake / 5 tasks / 4 approvals / 10 ledger / 4 devices）并创建/重置了 `owner@example.com` 用户（密码 `Review2026pass`）。清库重置：`docker compose down -v` 后重新初始化。

## 汇总表

| # | 标题 | 级别 | 类型 | 模块 |
|---|------|------|------|------|
| 1 | 控制台无登录页且 refresh 链路断裂：令牌过期后全站不可用且无法恢复 | P0 | bug | auth / web |
| 2 | intake/goals 写操作不落库：返回 201 但数据静默丢失 | P0 | bug | intake / goals |
| 3 | 全新部署无法创建首个用户：create_user.py 未随镜像分发且无引导 | P1 | bug | deploy / identity |
| 4 | 目标页缺少直接创建入口；"三问"失败文案误导 | P1 | bug/ux | goals / intake |
| 5 | 收件箱提交成功无任何反馈提示 | P1 | ux | inbox |
| 6 | 登录限流缺少剩余时间提示与自助解锁途径 | P1 | enhancement | identity |
| 7 | 错误态直接暴露 request_id；unauthorized 文案与实际能力不符 | P2 | ux | web/ui |
| 8 | 侧边栏"占位"徽标与实际实现状态脱节 | P2 | bug | web/nav |
| 9 | 浏览器 Console 大量 RSC 请求 net::ERR_ABORTED | P2 | bug | web |
| 10 | web 容器未注入 NEXT_PUBLIC_API_BASE_URL，回退值与部署拓扑耦合 | P2 | bug | deploy |
| 11 | 首次使用全空态：缺演示数据、全局快捷键帮助与新手引导 | P2 | enhancement | web |

---

## P0 · 阻断

### Issue 1 控制台无登录页且 refresh 链路断裂：令牌过期后全站不可用且无法恢复

**标签建议**：`bug`、`P0`、`auth`

**现象（实测复现）**
1. 打开 http://localhost:8080/，任意页面数据区显示"登录已过期，请重新登录"（`AsyncBoundary` 错误态）。
2. 全站没有任何登录页/登录表单：`web/src` 中无 login UI；`app/(console)` 无登录路由。
3. `POST /api/v1/auth/login` 成功（200，返回 token 对），但响应头**没有 Set-Cookie**（`curl -D -` 验证）。
4. 15 分钟后 access token 过期，前端静默刷新失败，所有查询 401，产品不可用，用户无任何恢复途径。

**根因（三方不一致）**
- 前端 `web/src/lib/api/client.ts`（L59-83、L103-108）：设计假设"刷新令牌走 httpOnly Cookie"，`refreshOnce()` 发送**无 body** 的 `POST /auth/refresh` + `credentials: include`；`setAccessToken` 除 client 内部外无任何调用方。
- 后端 `backend/app/modules/identity/router.py`（L121-130）：`/login` 只在 JSON body 返回 `refresh_token`，**不写 Cookie**；`/refresh` 要求 body 里显式传 `refresh_token`（`schemas.py` L13-15 `RefreshRequest`）。
- 结果：前端预期"Cookie 自动带上"的机制从未成立，refresh 必然失败。

**建议修复**
1. 后端：`/login` 与 `/refresh` 成功时设置 `refresh_token` 为 `HttpOnly; Secure; SameSite=Lax` Cookie，`/refresh` 支持从 Cookie 读取（保留 body 传参兼容小程序场景）；`/logout` 清除 Cookie。
2. 前端：新增 `/login` 页面（受 `OWNER_OPENIDS`/单用户白名单约束，仅账号+密码），401 刷新失败时跳转登录页。
3. 补 e2e：登录 → 过期 → 静默续期 → 登出全链路。

---

### Issue 2 intake/goals 写操作不落库：返回 201 但数据静默丢失

**标签建议**：`bug`、`P0`、`data-loss`

**现象（实测复现）**
```
POST /api/v1/intake/items  ×4 → 201；随后 GET /intake/items → {"items":[]}
POST /api/v1/goals/        → 201；随后 GET /goals/?limit=100 → []
psql: intake_items=0 行，goals=0 行
```
UI 层表现：收件箱"记下来"显示成功并清空输入框，切走再切回记录消失；"转成目标卡"同样假成功。

**根因**
- `backend/app/core/db.py`（L57-64）`get_db()` 注释明确"业务层不 commit，由服务层显式提交事务"，但请求结束**不 commit**。
- `backend/app/modules/intake/repository.py`（L130）与 `backend/app/modules/goals/repository.py`（L59）只 `flush()`；`intake/service.py`（`capture`）、`goals/service.py`（`create_goal`/`patch_goal`）均无 commit。
- 对比：approvals、devices、evals、execution、github_sync、identity、ledger 各模块均有 `commit()`，仅 intake / goals 两个模块遗漏。

**影响面**：收件箱捕获、三问结构化结果、目标创建、目标冻结/降级（`GoalsView` 的 `patch.mutate`）全部不生效。

**建议修复**
1. 对齐其他模块：在 intake/goals 的写路径（router 或 service）显式 commit；或引入请求级 `commit-on-success` 中间件统一兜底。
2. 补集成测试：每个写端点断言"POST → GET 可见"。
3. 排查其余仅 `flush()` 的写路径（可 grep `flush()` 逐个核对）。

---

## P1 · 重要

### Issue 3 全新部署无法创建首个用户：create_user.py 未随镜像分发且无引导

**标签建议**：`bug`、`P1`、`deploy`、`onboarding`

**现象（实测复现）**
- 全新 compose 环境中 `users` 表为 0 行；无注册端点（单用户白名单设计）、无引导页、无 seed。
- `backend/scripts/create_user.py` 存在于仓库但**未复制进 api 镜像**（容器内 `/app` 下无 `scripts/`），必须在宿主机找到源码 + 本地 Python 依赖才能执行；README 无 quickstart 指引。

**建议修复**
1. Dockerfile 将 `scripts/` 与 `migrations/` 一并纳入镜像；README 增加"首次启动三步"（migrate → create_user → login）。
2. 可选：提供 `docker compose exec api python scripts/create_user.py ...` 的 make 目标，或 `/setup` 一次性引导页（首启时允许设置 owner 密码，之后关闭）。

### Issue 4 目标页缺少直接创建入口；"三问"失败文案误导

**标签建议**：`bug`、`P1`、`ux`

**现象（实测复现）**
- `GoalsView` 没有任何"新建目标"入口，空态文案引导"先去收件箱记一条，再用三问把它变成契约"；但"三问"依赖 `LITELLM_BASE_URL`（本环境未配置），点击必然失败。
- 失败后右侧仅显示写死文案"创建失败：验收标准不能为空"（`InboxView` L213-215），与真实错误（上游 AI 未配置）无关，误导排障方向。

**建议修复**
1. 目标页提供直接创建表单（track/template/title/acceptance/预算/期限），三问流程作为增强而非唯一入口。
2. 错误文案按 `error.code` 映射（`not_implemented`/`upstream_error` → "AI 服务未配置，请先设置 LITELLM_BASE_URL"），移除写死文案。

### Issue 5 收件箱提交成功无任何反馈提示

**标签建议**：`P1`、`ux`

**现象（实测复现）**：提交成功后仅清空输入框并聚焦（`InboxView` `onSuccess` L59-63），无 toast、无列表高亮、无"已记下"提示；提交失败也只显示笼统的"记录失败，请重试"（未区分 401 / 网络错误）。在 Issue 2 存在的情况下，用户会误以为数据已保存。

**建议修复**：成功后给一次性内联反馈（如按钮短暂变"已记下 ✓"或新条目高亮 2s）；失败文案复用 `errorText(code)`。

### Issue 6 登录限流缺少剩余时间提示与自助解锁途径

**标签建议**：`P1`、`enhancement`、`security`

**现象（实测复现）**：连续失败触发 `rate_limited`："登录失败次数过多，请 15 分钟后再试"；响应中无剩余等待秒数；因无登录页（Issue 1），用户连"等 15 分钟"的操作界面都没有，只能手工清 Redis（`rl:login:fail:*`）。另：限流按 subject 键控，可被用于恶意锁死 owner 邮箱（值得安全评估）。

**建议修复**：响应携带 `retry_after`；错误文案显示剩余分钟数；评估按 IP+subject 双维度限流与锁定豁免策略。

---

## P2 · 体验

### Issue 7 错误态直接暴露 request_id；unauthorized 文案与实际能力不符

**标签建议**：`P2`、`ux`

- `components/ui/index.tsx`（L139）在错误态直接渲染 `request_id=xxx`。对单用户产品是噪音，建议折叠进"复制诊断信息"或灰显小字。
- `errorText.ts` 将 `unauthorized` 映射为"登录已过期，请重新登录"，但全站无重新登录途径（Issue 1），文案承诺了不存在的能力；修复 Issue 1 后该文案才成立，届时应同时提供跳转登录的按钮。

### Issue 8 侧边栏"占位"徽标与实际实现状态脱节

**标签建议**：`P2`、`bug`

`config/nav.ts` 冻结注册表中 10 项 `stub: true` 全部为真，`Sidebar` 据此显示"占位"徽标；但实际 today/inbox/goals/execution/team/ledger 等页面已是完整实现。冻结文件的元数据与实际进度不同步，会持续给用户"这是半成品"的错误信号。建议：增加真实进度标记（CI 校验 `stub` 与页面实现一致性），或改为"beta"徽标。

### Issue 9 浏览器 Console 大量 RSC 请求 net::ERR_ABORTED

**标签建议**：`P2`、`bug`

Console 出现大量 `/today?_rsc`、`/inbox?_rsc` 等请求被中止（Next.js 客户端路由预取打断）。多为良性，但数量偏大需排查：是否 prefetch 策略过激（`<Link>` 默认预取 + 侧边栏 10 项）、是否与 401 重试逻辑叠加放大。建议限制预取或改 `prefetch={false}` 观察。

### Issue 10 web 容器未注入 NEXT_PUBLIC_API_BASE_URL，回退值与部署拓扑耦合

**标签建议**：`P2`、`bug`、`deploy`

`deploy/docker-compose.yml` 声明 `NEXT_PUBLIC_API_BASE_URL` 必填，但实际 web 容器 env 为空，前端回退到硬编码 `http://localhost:8000/api/v1`（`client.ts` L39）。当前本地 Caddy 已提供 `/api/*` 同源反代，正确做法是注入**同源相对路径**（或空串 + 相对前缀），避免部署到任何非 localhost 域名时浏览器直连 :8000 打破同源与 HTTPS 假设。

### Issue 11 首次使用全空态：缺演示数据、全局快捷键帮助与新手引导

**标签建议**：`P2`、`enhancement`

- 新用户首启所有页面空态，无演示数据/示例任务；overview 与 projects 页却带"示例"数据标签，与真实数据页观感不一致。
- today 页快捷键（J/K/Enter/X）提示仅存在于该页头部，无全局 `?` 呼出的快捷键面板。
- 建议：提供 `make seed-demo`（评审即用其思路造数）；统一示例数据的开关与标注；增加全局快捷键帮助。

---

## 附：评审证据摘录

| 证据 | 结果 |
|------|------|
| `curl -D -` 检查 `POST /api/v1/auth/login` | 200，响应无 `Set-Cookie` |
| `POST /intake/items` ×4 / `POST /goals/` | 均 201；DB `intake_items`、`goals` 0 行 |
| 登录失败 5 次 | `rate_limited`："登录失败次数过多，请 15 分钟后再试" |
| 浏览器走查（未认证） | 全部数据页 unauthorized 错误态；today 显示"决策额度 0/12 · 加载中" |
| 浏览器走查（注入 token 后） | today/inbox/execution/ledger 数据正常渲染；inbox 提交成功但数据消失；goals 无创建入口 |
| Console | 大量 `net::ERR_ABORTED`（RSC 预取） |

## 建议处理顺序

1. Issue 1（认证）+ Issue 2（落库）：修完这两条产品才算端到端可用。
2. Issue 3（首启引导）：让新环境 10 分钟内跑起来。
3. Issue 4/5（目标入口与反馈）：核心工作流补全。
4. 其余 P2 按批次清理。
