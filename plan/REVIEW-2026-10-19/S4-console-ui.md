# S4 · 控制台页面（目标 / 收件箱 / 导航 / 性能）

> 来自 `docs/PRODUCT-REVIEW-2026-10-19.md` Issue 4（P1，前端部分）、Issue 5、Issue 8、Issue 9、Issue 11（UI 部分）。
> 只改 `(console)` 页面、业务 feature 与导航；认证相关共享文件归 S1，不要碰。

> **状态：DONE（2026-10-19）**。S4-01…06 全部落地；`nav.ts` 为单文件改动，按计划应单独 PR（`x-shared-registry`）。
> 验证：`npm run typecheck` / `npm run build` 通过；Playwright 12/12（新增三问失败、目标直建两例）；表单在 375/1024px 无横向溢出；冷启动 37 次循环 `_rsc` 请求 333 → 37，整页跳转中断 5 → 0。

## 0. 任务身份

| 项 | 值 |
|---|---|
| 流 | `S4` |
| 名称 | 目标直建、提交反馈、导航状态、预取收敛、快捷键帮助 |
| 窗口 | `W10`（前端控制台） |
| 分支 | `win/s4-console-ux` |
| 规模 | 6 个任务，约 1.5 人日 |
| 阻塞 | 运行期依赖 S2-01（数据必须真的落库才能验"提交后还在"），文件零交叉 |

## 1. 文件边界

**可改**

```
web/src/app/(console)/**                  # 各页路由
web/src/features/goals/**                 # GoalsView 等
web/src/features/inbox/**                 # InboxView 等
web/src/features/{today,overview,projects}/**
web/src/components/shell/**               # Sidebar（导航徽标、prefetch）
web/src/config/nav.ts                     # 冻结文件，单独 PR（仅 S4-04）
web/src/lib/api/hooks.ts                  # 本流拥有；加 hook 时只追加
web/src/lib/api/types.ts                  # 本流拥有；加字段时只追加
web/e2e/inbox.spec.ts                     # 可扩展，不要动 helpers.ts
```

**禁改**

```
web/src/lib/api/client.ts                 # S1
web/src/lib/api/errorText.ts              # S1（本流只调用，不修改）
web/src/components/ui/index.tsx           # S1（错误态/Toast 原语）
web/src/components/AsyncBoundary.tsx      # S1
web/src/app/login/**                      # S1
web/src/features/auth/**                  # S1
backend/**                                # S1 / S2
deploy/** Dockerfile Makefile README.md   # S3
```

**已在库里的能力（别重复造）**：`useCreateGoal`（`hooks.ts:88`）、`GoalCreate` 类型（`types.ts:68`）、`Button/Card/Badge/EmptyState` 原语、`today` 页的 J/K/Enter/X 快捷键实现——照着复用。

## 2. 冻结契约

| 编号 | 契约 |
|---|---|
| C8 | 导航状态语义：`stub: false` = 页面已完整实现；`stub: true` = 仍是占位页。**不新增字段**（避免与 S1/S3 的解释漂移） |
| C9 | 错误文案一律 `errorText(err.code, fallback)`；不在页面里写死后端错误猜测 |
| C10 | 演示数据标记统一用 `[demo]` 前缀（与 S3-04 seed 脚本一致）；页面只读标记，不判断"是不是演示环境" |
| — | 不改 API 请求路径与方法；不改 `nav.ts` 的 `href` |

## 3. 任务

### S4-01 目标页直接创建表单
- **现象**：`GoalsView` 只有空态引导"先去收件箱"，没有新建入口；三问依赖未配置的 LLM，等于无路可走。
- **改动**
  - `features/goals/GoalCreateForm.tsx`（新建）：字段 `track`（赚钱/进化，二选一）、`title`、`acceptance`（必填）、`template`（可选）、`horizon`（可选，日期或"14 天"）、`budget_money` / `budget_hours`（按 track 显示其一）。
  - `GoalsView` 头部右侧加"新建目标"按钮（`Button`），点击展开行内表单或对话框；成功走 `useCreateGoal` 自带 invalidate。
  - 空态文案改为"直接新建，或去收件箱记一条再用三问变成契约"；表单保留三问作为**增强入口**，不是唯一入口。
- **验收**
  - 不配置 `LITELLM_BASE_URL` 也能建成目标；提交后列表立刻出现（依赖 S2-01）。
  - 必填校验在前端可见（`acceptance` 为空时按钮禁用 + 提示），不靠后端 422。
  - 表单在 1024px 与 375px 两档不溢出。
- **注意**：`useCreateGoal` 已存在，直接复用；若后端 schema 缺 `horizon`/`budget_*`，先看 `types.ts` 是否已有，缺则只加前端可选字段，不要改后端。

### S4-02 收件箱：删掉写死的失败文案
- **现象**：`InboxView` L214 固定输出"创建失败：验收标准不能为空"，与真实原因（AI 未配置）无关。
- **改动**
  - 该处改为 `errorText(create.error.code, "创建失败，请重试")`。
  - 三问失败（`upstream_error` / `not_implemented`）展示为"AI 服务未配置或不可用，请检查 `LITELLM_BASE_URL`"——文案放在**调用点**，不改 `errorText.ts`。
  - 后端错误码以 S2-03 的 PR 结论为准（`三问未配置 → not_implemented` 或 `upstream_error`）。
- **验收**：断开 LLM 后点三问，看到的是配置提示，不是"验收标准不能为空"。

### S4-03 收件箱：提交成功/失败反馈
- **改动**
  - `onSuccess`（`InboxView` L59-63）加一次性内联反馈：按钮文案 2 秒内变"已记下 ✓"，或新条目高亮 2 秒；随后恢复。不要引入 toast 库。
  - 失败：区分 `unauthorized`（"登录已过期"）/ 网络错误（"网络不可用"）/ 其它，统一走 `errorText`。
- **验收**：提交后不切页即可确认成功；失败时文案能指向原因；连续提交两条，反馈不叠加错乱（timer 清理）。

### S4-04 导航「占位」徽标对齐实现
- **现象**：`nav.ts` 10 项全 `stub: true`，Sidebar 全显示"占位"，但 today/inbox/goals/execution/team/ledger 已完整实现。
- **改动**
  - 逐项核对页面是否有真实实现（`features/*` 有 View 且非 `PagePlaceholder`）→ 已实现的改 `stub: false`。
  - 仍是占位的（如 arsenal/assets 若为占位）保留 `true`。
  - Sidebar 只在 `stub` 时渲染徽标（逻辑已存在，无需改）。
- **验收**：侧边栏只剩真正未完成的页面带"占位"；截图对比。
- **注意**：`nav.ts` 是**冻结文件**（owner W10）→ **单独 PR**，PR 里贴 `x-shared-registry` label；不要与 S4-01/03 混在一个 PR。

### S4-05 RSC 预取 `ERR_ABORTED` 收敛
- **现象**：Console 大量 `/today?_rsc` 等请求 `net::ERR_ABORTED`，侧边栏 10 项默认预取 + 401 重试可能放大。
- **改动**
  - `Sidebar` 的 `<Link>` 加 `prefetch={false}`（先全关，观察收益；若导航体感变差，再改为 hover 预取）。
  - 顺手确认：`providers.tsx` 的 401 重试不会对 RSC 请求生效（客户端 fetch 才走 client.ts）。
- **验收**：给出改前/改后同一路径切换 30 秒内的 `_rsc` 中断计数（DevTools 过滤 `_rsc`）；页面切换无白屏、无明显延迟。
- **不做**：不改 Next 配置里的全局 prefetch 策略（影响面大，收益不确定）。

### S4-06 快捷键帮助与示例标注统一
- **改动**
  - 新增全局 `?` 呼出的快捷键面板（`components/shell/ShortcutHelp.tsx` 或 `features/today/`），列 today 页 J/K/Enter/X 与 Esc 关闭；在 `(console)/layout.tsx` 挂载。
  - `projects` 页的"示例数据 · W06 接入后替换"与 overview 的示例标签统一文案与样式（都用 `Badge tone="warn"` + 同一句说明），并在数据源已可用时优先接真实接口；不可用则保留标注并写清原因。
- **验收**：任意页按 `?` 出现面板、Esc 关闭、不与输入框冲突（焦点在 input/textarea 时不触发）；两页示例标注观感一致。
- **依赖**：示例数据由 `make seed-demo`（S3-04）提供，标记前缀 `[demo]`。

## 4. 自检

```bash
cd web && npm run typecheck && npm run lint 2>/dev/null || true
cd web && npm run build
cd web && npm run test:e2e -- inbox.spec.ts
./scripts/pr_precheck.sh --local
```

手工：空态建目标 / 三问失败文案 / 提交反馈 / 侧边栏徽标 / `?` 面板 / Console 里 `_rsc` 计数。

## 5. 完成定义

- [x] 不配 LLM 也能创建目标，且创建后列表可见（表单走 `POST /goals/`，成功即 invalidate `goals`；S2 已修 commit）
- [x] 三问失败文案指向配置项（e2e：502 `upstream_error` → `LITELLM_BASE_URL`）
- [x] 提交有一次性反馈，失败文案区分原因（按钮 2s "已记下 ✓"，401/网络分别映射）
- [x] 侧边栏只剩真实占位项带徽标（10 页均为真实 View，徽标数 0）
- [x] `_rsc` 中断数量有前后对比数据（37 次冷启动：333 → 37 请求；整页跳转 5 → 0 中断）
- [x] `?` 面板可用，示例标注统一（Esc 关闭、输入框内不触发；projects/overview 统一 `示例数据 · 待接入真实接口`）

## 6. PR 与提交

| PR | 范围 | 标题 |
|---|---|---|
| PR-1 | 页面与 feature（S4-01/02/03/05/06） | `[W10] feat: goal create entry and inbox feedback` |
| PR-2 | `web/src/config/nav.ts` 单项（S4-04） | `[W10] chore: align nav stub flags`（`x-shared-registry`） |

提交前缀：`feat(#<issue>): goals-create` / `fix(#<issue>): inbox-feedback` / `chore(#<issue>): nav-status`。

## 7. 卡住时

- 需要改 `errorText.ts` 或 `components/ui/index.tsx` → 找 S1，在 PR 评论里写清需要的 key / 原语，不要自己改。
- 目标创建报 422 且字段对不上 → 先读 `backend/app/modules/goals/schemas.py`（只读），把差异写进 issue 给 S2，不要改后端。
- 评测数据源（projects）实际不可用 → 保留示例标注，不要为了"看起来真"造假数据。
