# 代码评审 · 2026-10-19 评审修复

> 评审对象：S1–S5 五条流的实现（3 个 git 引用 + 主工作区未提交改动）。
> 方法：逐文件 diff 走查 + 静态检查 + 单测 + 构建 + e2e + 对运行中环境做 API 复现。
> 结论：**单流实现基本到位，但现在不能整体合并**——1 个运行期 P1、1 个开发链路 P1、1 个交付状态 P1。

## 0. 结论摘要

| 维度 | 结论 |
|---|---|
| 功能完成度 | 5 条流的目标都实现了；P0 两条（Cookie 链路、写入落库）代码到位 |
| 自动化验证 | 后端全量 pytest 通过、ruff 干净、`tsc --noEmit` 通过、两处 `next build` 通过、e2e 8 例通过 |
| 可合并性 | **暂不可整体合并**：见 P1-1 / P1-2 / P1-3 |
| 单流合并建议 | S2 修一处测试后即可；S1 可合（缺真实集成验证）；S3 需 D1 授权 + 两个小修；S4 修 P1-1 后可合；S5 改 workflow 默认值后可合 |

## 1. 评审对象

| 流 | 代码位置 | 形态 |
|---|---|---|
| S1 后端 | `.worktrees/s1-auth-backend` · `da0f1f0` | 已提交，1 commit |
| S1 前端 | `.worktrees/s1-auth-web` · `53fdb62` | 已提交，1 commit |
| S2 | `win/s2-persistence` · `8fde0d8` + `42216c8` | 已提交，2 commits |
| S3 / S4 / S5 | 主工作区 | **未提交**（18 modified + 14 untracked，含本报告） |
| 计划文档 | 主工作区 + S2 分支 | 部分未跟踪 |

合并冲突预检（`git merge-tree`）：S1 后端 × S1 前端、S1 后端 × S2、S1 前端 × S2 **均无冲突**。S3/S4/S5 未提交，无法预演。

## 2. 验证记录

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端全量单测 | `pytest -q`（`--basetemp` 绕开 tmp 权限） | **exit 0**，全绿 |
| 认证单测 | `pytest tests/test_identity.py`（S1 工作区） | **32 passed** |
| 收件箱/目标单测 | `pytest tests/test_intake.py tests/test_goals.py` | **通过** |
| 后端 lint | `ruff check app tests scripts` | **All checks passed** |
| 前端类型 | `npm run typecheck` | **通过** |
| 前端构建（主树） | `NODE_OPTIONS= npm run build` | **成功**，14 路由 |
| 前端构建（S1） | 同上（S1 工作区） | **成功**，`/login` 为动态路由 |
| e2e · 收件箱 | `playwright test inbox.spec.ts` | **3 passed** |
| e2e · 认证 | `playwright test auth.spec.ts` | **5 passed**（全 mock） |
| 回归脚本（旧容器） | `review-regression.sh` | **2 PASS / 5 FAIL, exit 1**（符合"修前应红"） |

说明：本机 8080 上跑的是**改造前的镜像**（login 无 Set-Cookie、空 body refresh 422），所以脚本红是预期；它也证明 S5 的断言确实能抓住原问题。

## 3. P1 · 合并前必须处理

### P1-1 `GET /goals/` 被单条脏数据打成 500，目标页/总视图不可用

**复现（运行中环境）**

```
POST /api/v1/auth/login              → 200（无 Set-Cookie，旧镜像）
GET  /api/v1/goals/?limit=100        → 500 internal_error
GET  /api/v1/devices/?limit=50       → 401（未带 token）
psql: select json_typeof(guardrails), json_typeof(deliverables) from goals;
      → array / array（2 行）
```

**日志根因**

```
goals/repository.py:143 to_out → GoalOut(...)
pydantic ValidationError:
  guardrails  Input should be a valid dictionary [input_value=['不碰灰产渠道'], input_type=list]
  deliverables Input should be a valid dictionary [input_value=['20 篇内容'], input_type=list]
```

`GoalOut.guardrails` / `deliverables` 声明为 `dict[str, Any] | None`（`goals/schemas.py:79-80`），但库里这两行的 JSON 是数组。**一行坏数据 → 整个列表 500**，`useGoals()` 同时被 `/goals` 与 `/overview` 使用，两个页面一起挂。

**性质**：坏数据是 2026-10-19 评审时手工插库留下的（2 行，正好对上评审说明），不是应用写入路径产生的；但新代码没有做任何容错，且 S3 的 `seed-demo --reset` 只清 `[demo]` 前缀，清不掉这两行。任何"历史数据里混进数组"的部署都会踩到。

**建议**（二选一，推荐同时做）

1. `to_out` 做归一化：`list → {"items": value}`，`None` 保留；保持 `GoalOut` 契约不变，前端无感。
2. 提供一次性清理：`scripts/fix_legacy_goal_json.py` 或迁移，把数组包成对象；并给 `to_out` 加一条"数组不炸"的单测。
3. 本机验收前先 `docker compose down -v` 或跑清理脚本，否则 S4 的目标页/总视图无法验收。

### P1-2 本地开发链路被打断：默认 base 改同源，README 没让配 `.env.local`

- `web/src/lib/api/client.ts:35-44`：`apiBaseUrl()` 缺省返回 `/api/v1`（生产正确）。
- `README.md` 「本地开发」仍是 `cd web && npm install && npm run dev`，没有 `cp web/.env.example web/.env.local`。
- 结果：`:3000` 上的 `next dev` 没有反代，所有请求打到 `http://localhost:3000/api/v1/*` → 404，登录页也拿不到后端。
- `web/.env.example` 里已有 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`，只是文档没接上。

**建议**：README 开发步骤加一行 `cp web/.env.example web/.env.local`；或给 `next.config.js` 加 dev-only rewrite。两者取其一，不要靠记忆。

### P1-3 交付状态：代码散在 3 棵树、32 项未提交、评审源文档未跟踪

```
主工作区  branch=win/s2-persistence（S2 的提交）+ 18 modified + 14 untracked（S3/S4/S5）
          ?? docs/PRODUCT-REVIEW-2026-10-19.md
          ?? plan/REVIEW-2026-10-19-fixes.md
          ?? scripts/review-regression.sh
          ?? web/src/features/goals/GoalCreateForm.tsx
          ?? web/src/components/shell/ShortcutHelp.tsx
          ?? web/package-lock.json
S1        .worktrees/s1-auth-backend（已提交）、.worktrees/s1-auth-web（已提交）
          两者另各有 next-env.d.ts / package-lock.json 等未跟踪的生成物
```

**风险**：主工作区分支叫 `win/s2-persistence`，却躺着 S3/S4/S5 的改动；一次 `git commit -a` 就会把三条流混进 S2 的 PR（`plan/README.md` §2 明令禁止）。评审源文档与拆分文档未跟踪，换机器/清 worktree 即丢失。

**建议**：合并前先把 S3/S4/S5 各自落到自己的分支（`win/s3-deploy-bootstrap` / `win/w10-console-ux` / `win/w09-regression-gate`），逐个提交；`docs/PRODUCT-REVIEW-2026-10-19.md` 单独 docs 提交。不要在主工作区直接 `commit -a`。

## 4. 按流结论

| 流 | 结论 | 首要问题 | 合并条件 |
|---|---|---|---|
| **S1 后端** | 可合并 | P2-2（`refresh_token` 仍回 body）、P3-1（返回类型注解） | 补一条真实 Cookie 集成验证（S5 的 A2/A3 已覆盖，重建后跑一遍） |
| **S1 前端** | 可合并 | P1-2（本地开发文档）、P2-3（e2e 全 mock） | 改 README 一行；可选补真实后端 smoke |
| **S2** | 修一处测试后可合 | P2-1（structure 双提交 + 测试断言无效） | 去掉一次提交，改断言为 `+1`；其余无需动 |
| **S3** | 修两个小项后可合 | P2-4（web Dockerfile 默认值、lock 未跟踪）、P2-5（D1 未授权） | Marshal 授权 D1；改 `web/Dockerfile` ARG 默认值并提交 lock |
| **S4** | 修 P1-1 后可合 | 依赖 goals 列表 | 归一化脏数据后跑 goals/inbox e2e；P3-3 可顺手改 |
| **S5** | 改默认值后可合 | P2-6（workflow 默认 localhost） | 把 `base_url` 设为必填或注明 self-hosted |

## 5. P2 · 应修

### P2-1 S2：structure 写路径双提交，回归测试抓不到

- `intake/repository.py:236-263` 的 `save_structure` **内部已经 commit**；`intake/service.py`（`structure` 末尾）又 commit 一次。功能无害，但违反"提交权在 service"的契约，也让事务边界变模糊。
- 对应测试 `tests/test_intake.py:160-169` 让 `FakeIntakeRepo.save_structure` 自己 `commit_calls += 1`（注释写"与 Sql 实现对齐"），而断言是 `assert fake_repo.commit_calls > before`（L826）。**把 service 的 commit 删掉，这条测试仍然绿**——它测的是假仓库的内部行为，不是被测代码。
- **建议**：`save_structure` 改回只 flush，由 service 唯一提交；Fake 不模拟内部 commit；断言改 `== before + 1`。同时把 `capture` 的 `>= 1` 收紧为 `== 1`。

### P2-2 S1：`refresh_token` 仍随登录响应体返回

`identity/router.py#_pair_response` 保留 `refresh_token` 字段（为小程序/设备兼容）。浏览器侧 JS 仍能读到它，HttpOnly 的收益被削掉一半。**建议**：按调用方区分（如 `X-Client: browser` 时不返回），或至少在 ADR 里写清"body 字段只为非浏览器客户端保留"。

### P2-3 S1：认证 e2e 全 mock，不能证明 Cookie 链路真的成立

`auth.spec.ts` 顶部已注明"全部 API 走 route mock"。它验证了前端状态机（跳转、静默续期、限流文案），但 Set-Cookie/轮换/登出失效只能靠后端单测与 S5 脚本。**建议**：合并重建后跑 `review-regression.sh` 的 A2/A3/A4/A5/A7，作为发布前必过项；e2e 保持 mock 定位即可。

### P2-4 S3：镜像默认值仍是 localhost；lock 未跟踪导致 `npm ci` 永远走 fallback

- `web/Dockerfile:13`：`ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`。compose 会覆盖，所以生产没事；但任何直接 `docker build web` 的路径仍会打出"上线打开发机"的镜像——正是评审 Issue 10 的同类问题，只修了一半。
- `web/package-lock.json` 未被跟踪（`git ls-files` 为空），Dockerfile 的 `npm ci || npm install` 永远走后者，构建不可复现。
- **建议**：ARG 默认改 `/api/v1`（与 C5 一致）；提交 `package-lock.json`，CI 加 `npm ci` 校验。

### P2-5 S3：D1 仍未授权，部署流在法律上"无主"

`backend/Dockerfile`、`deploy/` 根、`Makefile`、根 `README.md` 仍不在任何窗口 allow 内。改动已在工作区，但一旦开 PR 会被边界检查拦下。**建议**：合并前由 Marshal 登记 W13（或临时白名单），否则 S3 永远只能算"草稿"。

### P2-6 S5：workflow 默认 `http://localhost:8080` 在 GitHub runner 上必然失败

`review-regression.yml` 的 `base_url` 默认值是 runner 自己的 localhost，那里没有这套 compose。手动触发如果忘了改，得到的是"服务不可用"而不是回归结果。**建议**：去掉默认值（required 且无 default），或在 description 明写"必须是可达地址；GitHub 托管 runner 需 self-hosted 或内网穿透"。

## 6. P3 · 建议

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| P3-1 | `identity/router.py` login/refresh | `-> Response` 注解与 `response_model=TokenPairResponse` 不一致，靠运行时兜底 | 注解改 `TokenPairResponse`，用 `response: Response` 注入写 Cookie |
| P3-2 | `identity/router.py#_rate_limited_response` | 复制了全局错误体构造；依赖层（IP 桶）抛的 429 没有 `Retry-After` 头 | 把 header 能力下沉到 `AppError`（走 shared-registry），或统一在中间件补 |
| P3-3 | `InboxView.tsx:146` | 成功后 `justSaved=true` 使按钮在空文本时仍可点，点了没反应 | `disabled={!text.trim() || create.isPending}`，反馈只改文案 |
| P3-4 | `GoalCreateForm.tsx` | `horizon` 是自由文本，不写 `horizon_at`，目标卡"剩余天数"永远显示 `—` | 标注为"期限描述"，或解析 `N 天/日期` 写入 `horizon_at` |
| P3-5 | `login/page.tsx` | `next` 只拦 `//`，未拦 `/\evil.com`（部分浏览器把 `\` 当归一化斜杠） | 改为 `raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\")` |
| P3-6 | `ShortcutHelp.tsx` | 弹窗无焦点管理/焦点陷阱，键盘用户 Tab 会跑到背景 | 打开时 focus 到关闭按钮，关闭时还原 |
| P3-7 | `deploy/Caddyfile` | `:8080` 明文站点也发 HSTS（浏览器在 http 下忽略，但语义错误） | 把 HSTS 从共用 snippet 挪到域名站点 |
| P3-8 | `seed_demo.py` | 演示 goals 用字符串 id，而 ledger/approvals 的 `goal_id` 是 UUID 列；跨表比较依赖 PostgreSQL 隐式 cast，当前能跑但类型不统一 | 统一 id 表示（建议都存 hex 字符串并显式 cast），避免 join 与索引隐患 |
| P3-9 | `docs/PRODUCT-REVIEW-2026-10-19.md` | 证据表写 `GET /goals/ → []`，实际库里有 2 行且返回 500 | 证据表补一句"后续插入了 2 条 goals，列表 500"或修数据后重跑 |

## 7. 合并顺序建议

```
0) 处理 P1-1（归一化/清数据）→ P1-2（README）→ P1-3（分分支提交）
1) S2   （最小、无依赖）        → 跑 pytest
2) S1   （后端 → 前端，两个 PR） → 跑 identity + auth e2e
3) S3   （获 D1 授权后）        → docker compose build + 清库首启
4) S4   （前端页面）            → 跑 inbox/goals e2e
5) S5   （回归脚本）            → 重建容器后跑 7 条断言
```

**验收命令（合并重建后）**

```bash
docker compose -f deploy/docker-compose.yml --env-file .env up -d --build
make create-owner EMAIL=owner@example.com PASSWORD='...'
BASE_URL=http://localhost:8080 OWNER_EMAIL=owner@example.com OWNER_PASSWORD='...' \
  bash scripts/review-regression.sh
# 期望：PASS 7 · FAIL 0 · SKIP 0，exit 0
```

## 8. 做得好的地方（建议保留）

1. **Cookie 契约集中在 `identity/cookies.py`**，端点不散字面量；`Path=/api/v1/auth` 的收敛是有意设计，不是抄默认值。
2. **S2 没有动 `get_db()` 语义**，也没有引入 commit-on-success 中间件——最小改动，符合冻结区约束。
3. **S3 的 compose 改动质量高**：`api` 等 `migrate` 完成、8080 只绑回环、`.env.example` 补齐变量并写清构建期语义。
4. **S4 的错误文案是"按 code 分支"而不是再写死一句**；`GoalCreateForm` 复用了已有的 `useCreateGoal`，没有造第二套请求。
5. **S5 的脚本确实有用**：在旧环境上准确红出 5 条，且 A7 用 probe subject + 文档保留 IP，不锁真人。

## 9. 本次未覆盖

- 未重建容器验证"新代码 + 真 Cookie"的端到端（当前 8080 是旧镜像）；建议合并后按 §7 跑一次。
- 未做负载/并发下的 refresh 轮换压测（重放检测依赖 DB 状态）。
- 未评审 `agent/` 与 `backend` 其他模块（本次只覆盖 5 条流的改动面）。

---

## 10. 修复回填（2026-10-19 晚，交付 PR 系列）

| 项 | 状态 | 落地位置 |
|---|---|---|
| P1-1 goals 脏数据 500 | **已修** | PR #25 追加提交 `34edb70`（issue #37）：`to_out` 出口归一化（数组 → `{"items": […]}`，标量 → `{"value": …}`，None/dict 原样，GoalOut 契约不变）+ 模板 D `guardrails` set 笔误改为 dict + 4 条回归测试（撤掉归一化即红）。S4「修 P1-1 后可合」的前置解除 |
| P1-2 本地开发链路 | **已修** | PR #33 追加提交：README「本地开发」补 `cp web/.env.example web/.env.local` |
| P1-3 交付状态 | **已解决** | PR 系列：#25（S2，含 #24/#37）、#27/#28（S1 前后端，#26）、#33（S3，#30）、#34（S4 主体，#31）、#35（S4 nav 单文件，#32，冻结文件待补 `x-shared-registry` label）、#36（S5，#29）；计划文档（评审原文 / 任务总表 / 本文 / S1 流文档）由 `chore/review-docs` 分支提交 |

遗留（不在本次范围）：`goals create_goal` 的 ConflictError retry 在真实 session 下需先 rollback 才能重试（极低概率，建议单开 issue）；compose 端到端 psql 行数验证需部署环境（§9 的意见仍然有效，建议合并后按 §7 跑一次）。
