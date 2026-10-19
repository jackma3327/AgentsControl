# S1 · 认证全链路（后端 Cookie + 登录页）

> 来自 `docs/PRODUCT-REVIEW-2026-10-19.md` Issue 1（P0）、Issue 6（P1）、Issue 7（P2，文案与诊断）。
> 本流是**垂直切片**：一个 Agent 同时改 `backend/app/modules/identity/` 与 `web/` 的认证相关文件。
> 只要不改本文「禁改」清单里的文件，就与其他 4 个流零冲突。

## 0. 任务身份

| 项 | 值 |
|---|---|
| 流 | `S1` |
| 名称 | 认证全链路：refresh 走 HttpOnly Cookie + 登录页 + 限流提示 |
| 窗口归属 | 后端 `W01`、前端 `W10`（**跨窗口，见 §6 PR 规则**） |
| 分支建议 | `win/s1-auth-backend` + `win/s1-auth-web`（两个） |
| 规模 | 7 个任务，约 1.5–2 人日 |
| 阻塞 | 无。本流是其他流的运行期前置，必须最先启动 |

## 1. 文件边界

**可改（仅这些）**

```
backend/app/modules/identity/**           # router/service/schemas/ratelimit 等
backend/tests/test_identity.py
docs/adr/**                               # 需要记录 cookie 契约时
web/src/app/login/**                      # 新建
web/src/features/auth/**                  # 新建
web/src/lib/api/client.ts
web/src/lib/api/errorText.ts
web/src/components/ui/index.tsx
web/src/components/AsyncBoundary.tsx      # 仅当 401 错误态需要改
web/e2e/auth.spec.ts                      # 新建
web/e2e/helpers.ts                        # 仅加认证 fixture
```

**禁改（别人正在写）**

```
backend/app/core/**                       # W01 内核，本流不动（get_db 语义保持不变）
backend/app/api/router.py                 # 冻结：login 路由已注册，无需加
backend/app/modules/intake/** goals/**    # S2
backend/app/modules/{tasks,devices,...}/  # 其他窗口
backend/Dockerfile  deploy/**  Makefile  README.md   # S3
web/src/app/(console)/**                  # S4
web/src/features/{goals,inbox,today,...}/ # S4
web/src/config/nav.ts                     # 冻结，归 S4 的 R14 单独 PR
web/src/lib/api/{hooks.ts,types.ts}       # S4
scripts/**                                # S3 / S5
```

## 2. 冻结契约（改之前先看，别自创第二套）

| 编号 | 契约 |
|---|---|
| C1 | refresh Cookie 名 `refresh_token`；`Path=/api/v1/auth`；`HttpOnly`；`SameSite=Lax`；`Secure` 由现有 `settings.app_env == "prod"` 决定（本地 http 不带 Secure，否则登录不了）；`Max-Age=REFRESH_TOKEN_TTL_SECONDS` |
| C2 | `/auth/refresh` **优先读 Cookie，回退 body**；body 传参保留（小程序/设备场景），不得删除 `RefreshRequest.refresh_token` 字段，只改为可选 |
| C3 | 错误响应结构不变：`{error:{code,message,request_id,details?}}`；`rate_limited` 额外带 `details.retry_after`（整数秒）+ 响应头 `Retry-After` |
| C4 | access token 仍只驻内存，不进 localStorage / 不进 URL；`credentials: "include"` 不变 |
| C5 | 前端 API base 缺省为 `""`（同源相对路径），最终请求形如 `/api/v1/...`；不硬编码 `localhost:8000` |

C4/C5 与 S3 的 compose 注入是一对，别单方面改语义。

## 3. 任务

### S1-01 后端：login/refresh/logout 走 Cookie
- **改动**
  - `identity/router.py`：`/login` 与 `/refresh` 成功后 `response.set_cookie(...)`；`/logout` `delete_cookie`；三个端点签名加 `response: Response`。
  - `identity/schemas.py`：`RefreshRequest.refresh_token: str | None = None`。
  - `identity/service.py`：`refresh()` 接受 `refresh_token` 为空时的处理（由 router 从 Cookie 取值后传入，service 保持纯函数式入参，不读 Request）。
  - cookie 属性集中为模块常量（名字、path、max-age），不要散落。
- **验收**
  1. `curl -D - -X POST localhost:8080/api/v1/auth/login -H 'Content-Type: application/json' -d '{"subject":"owner@example.com","password":"<pwd>"}'` → 响应头含 `set-cookie: refresh_token=...; HttpOnly; Path=/api/v1/auth; SameSite=lax`。
  2. 只带 Cookie、**空 body** 的 `POST /api/v1/auth/refresh` → 200 且返回新 `access_token`。
  3. 带 body 的 refresh 仍 200（兼容性回归）。
  4. `POST /auth/logout` 后，同一 Cookie 再 refresh → 401 `unauthorized`。
- **测试**：`test_identity.py` 加 3 例（写 cookie / 空 body 刷新 / 登出失效）。
- **注意**：Cookie `Path=/api/v1/auth` 意味着 `/api/v1/me` 之类的接口收不到它——这是故意的，refresh 只在 auth 路径用。

### S1-02 后端：限流返回剩余时间
- **改动**
  - `identity/service.py`：触发 `RateLimitedError` 时带 `details={"retry_after": <剩余秒>}`。
  - `identity/ratelimit.py`：加 `ttl(key) -> int`（读 Redis TTL，异常降级返回窗口长度）。
  - 响应头 `Retry-After`：在 `RateLimitedError` 的 handler 里统一加（`core/errors.py` 属禁改 —— 改为在 identity router 层捕获后设置，或让 `AppError` 支持 `headers` 字段；若必须动 `core/`，先找 W01 开 shared-registry，**不要直接改**）。
- **验收**：连续失败第 6 次 → 429，body `details.retry_after` 在 (0, 900]，响应头 `Retry-After` 存在；等窗口过期可再次登录。
- **不做**：双维度限流、owner 豁免（评审 D2，安全策略变更需单独 ADR）。

### S1-03 前端：`/login` 页与 401 恢复
- **改动**
  - 新建 `web/src/app/login/page.tsx`（**在 `(console)` 之外**，独立 layout，不挂侧边栏）+ `web/src/features/auth/LoginForm.tsx`。
  - `client.ts`：刷新失败 → 跳 `/login?next=<当前路径>`；加 `redirecting` 标志防并发跳转；`/login` 页自身不跳（防循环）；首屏无 token 时先静默 refresh 一次再决定。
  - 登录成功：`setAccessToken` + `router.replace(next || "/today")`。
- **验收**
  - 清 Cookie 访问 `/today` → `/login?next=%2Ftoday`；登录后回跳。
  - 清空内存 token（DevTools 无法直接清，用刷新页面模拟）→ 静默续期，页面无错误态闪烁。
  - 登出后回 `/login`，再访问 `/today` 仍回登录页。
- **不要**：不要改 `web/src/config/nav.ts`；登录页不放进侧边栏。

### S1-04 前端：API base 缺省同源
- **改动**：`client.ts#apiBaseUrl()` 缺省返回 `""`，删除 `http://localhost:8000/api/v1` 兜底；保留 env 覆盖能力。
- **验收**：本地 Caddy（:8080）下所有请求是 `/api/v1/*` 同源；`npm run build` 后产物里搜不到 `localhost:8000`。
- **依赖**：S3-03 会在 compose 注入 `/api/v1`，两者语义一致（C5）。

### S1-05 前端：登录页显示剩余锁定时间
- **改动**：`LoginForm` 读 `ApiError.details.retry_after`，展示"还需 X 分钟"；`errorText` 对 `rate_limited` 保留兜底文案。
- **验收**：触发限流后页面显示具体分钟数；到点后可重试。
- **注意**：`ApiError` 已带 `details`，无需改类结构。

### S1-06 前端：request_id 折叠 + unauthorized 可操作
- **改动**
  - `components/ui/index.tsx`：错误态主区只留一句人话；`request_id=xxx` 收进"复制诊断信息"按钮（`navigator.clipboard`，失败降级为可选中文本）。
  - `errorText.ts`：`unauthorized` 文案保持"登录已过期，请重新登录"；在 `AsyncBoundary`/错误态给"去登录"按钮（跳 `/login?next=`）。
- **验收**：任意数据页断网/401 时，首屏只看到一句提示 + 一个动作按钮；诊断信息一键复制。
- **注意**：`components/ui/index.tsx` 是 S1 独占文件，S4 不会动它。

### S1-07 前端：认证 e2e
- **改动**：`web/e2e/auth.spec.ts`（用现有 Playwright 配置，`helpers.ts` 可加 fixture）。
- **用例**：① 错误密码提示；② 登录进 `/today` 有数据；③ 刷新页面仍保持登录（静默续期）；④ 登出后回登录页。
- **验收**：`cd web && npm run test:e2e -- auth.spec.ts` 全绿；失败有截图。

## 4. 自检

```bash
cd backend && ruff check app tests && pytest tests/test_identity.py -q
cd web && npm run typecheck && npm run test:e2e -- auth.spec.ts
./scripts/pr_precheck.sh --local
```

手工四项：无 Cookie 打开首页 / 登录回跳 / 令牌过期无感 / 登出。

## 5. 完成定义

- [x] `curl -D -` 能看到 `Set-Cookie`（TestClient 全 ASGI 栈断言：HttpOnly/Path=/api/v1/auth/SameSite=lax/Secure 仅 prod/Max-Age=TTL；真机 curl 待有 PG+Redis 的环境补）
- [x] 空 body refresh 200（Cookie 优先，body 回退；两者皆无明确 401）
- [x] 浏览器全链路可用，且全站无"登录已过期"死路（e2e 5 例：错误密码/回跳/刷新后续期无感/登出/限流分钟）
- [x] 限流响应带 `retry_after`（body details + Retry-After 头）
- [x] 两个 PR 的 CI 均绿：PR-A [#27](https://github.com/jackma3327/AgentsControl/pull/27) `[W01] auth: refresh token via HttpOnly cookie`；PR-B [#28](https://github.com/jackma3327/AgentsControl/pull/28) `[W10] auth: login page and 401 recovery`（依赖 #27，先合 A 再合 B）；跟踪 issue #26；两 PR 均 8/8 检查通过（backend / web·typecheck+build / web·e2e / migrations / plan / agent / secret scan / gate）

## 5.1 执行记录（2026-10-19）

| 任务 | 状态 | 落地 |
|---|---|---|
| S1-01 | DONE | `identity/cookies.py` 契约集中；login/refresh 写、logout/revoke-all 清；refresh Cookie 优先；schemas 改可选 |
| S1-02 | DONE | service 429 带 `details.retry_after`；`ratelimit.ttl()`；Retry-After 头在 identity router 层补（core/errors.py 冻结） |
| S1-03 | DONE | `app/login/page.tsx` + `features/auth/LoginForm.tsx`；client.ts 401→静默 refresh→跳 `/login?next=` |
| S1-04 | DONE | apiBaseUrl 缺省同源 `/api/v1`。**偏差**：文档写缺省 `""`，实际必须带 `/api/v1`——调用方 path 不含前缀（hooks.ts 归 S4 冻结），空串会打到站点根路径；C5 行为不变 |
| S1-05 | DONE | `errorText.rateLimitedHint()` → "还需 X 分钟" |
| S1-06 | DONE | ErrorState 一句人话 + 复制诊断信息（剪贴板失败降级可选中文本）+ unauthorized「去登录」 |
| S1-07 | DONE | `e2e/auth.spec.ts` 5 例全绿；helpers.ts 认证 fixture |

**给 S4 的交接**：侧边栏登出入口在 `components/shell/`（S1 禁改清单内），需 S4 补一个调用 `POST /auth/logout` 的按钮；补完后 e2e ④ 可改为 UI 驱动。

## 6. PR 与提交

本流跨两个窗口，**开两个 PR**（这是对"一窗口一 PR"的有意例外，启动前让 Marshal 确认）：

| PR | 目录 | 标题 | 提交前缀 |
|---|---|---|---|
| PR-A | `backend/app/modules/identity/`、`backend/tests/test_identity.py` | `[W01] auth: refresh token via HttpOnly cookie` | `fix(#<issue>): auth-cookie` |
| PR-B | `web/src/app/login/`、`web/src/features/auth/`、`web/src/lib/api/client.ts`、`errorText.ts`、`web/src/components/ui/`、`AsyncBoundary.tsx`、`web/e2e/{auth.spec.ts,helpers.ts}` | `[W10] auth: login page and 401 recovery` | `fix(#<issue>): login-page` |

先合 PR-A 再合 PR-B；PR-B 的描述里贴 PR-A 的链接。

## 7. 卡住时

- 需要 `core/errors.py` 加 `headers` 支持 → 找 W01 开 shared-registry，**不要自己改**。
- 发现 `router.py` 缺路由 → 同样走 shared-registry，本流不需要新增路由。
- 前端 e2e 因后端未合而红 → 在 PR 里标注 `depends: PR-A`，先交 Draft。
