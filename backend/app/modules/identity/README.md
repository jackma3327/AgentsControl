# identity · 身份与权限

- **归属窗口**：W01
- **路由前缀**：`/auth`
- **职责**：登录/刷新/登出、RBAC 判定、会话与令牌吊销；单用户白名单，不开放注册。

## 端点清单

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/login` | 登录（subject + password；失败限流 5 次/15 分钟）；成功写 HttpOnly Cookie |
| POST | `/auth/refresh` | 刷新令牌：每次轮换，旧令牌立即失效；重放即吊销整条会话链；**优先读 Cookie，回退 body** |
| POST | `/auth/logout` | 登出：吊销当前会话链的刷新令牌，清除 Cookie |
| POST | `/auth/revoke-all` | 吊销全部会话（全端下线；需 `auth:manage` scope） |
| GET | `/auth/me` | 当前主体（subject / role / scopes） |
| GET | `/auth/scopes` | scope 白名单自省（角色 → scope 完整映射） |

## refresh_token Cookie（S1-01，契约见 `cookies.py`）

- 名字 `refresh_token`；`Path=/api/v1/auth`（只在 auth 路径生效）；`HttpOnly`；`SameSite=Lax`；
  `Secure` 仅 `APP_ENV=prod`；`Max-Age` = refresh TTL；
- `/auth/refresh` 优先读 Cookie，回退 body——body 渠道保留给小程序 / 设备（`RefreshRequest.refresh_token` 为可选字段）；
- 429 限流响应带 `details.retry_after`（秒）与 `Retry-After` 头（S1-02）；登录锁定消息不再写死"15 分钟"。

## 关键约定（详见 docs/adr/0002-auth-model.md）

- access token 15 min（无状态）；refresh token 14 天（服务端只存 SHA-256 哈希）；
- **登出不吊销 access token**（最多再活 15 min）——有意的取舍，ADR 有论证；
- 吊销语义统一为 `revoked_at`（NULL = 有效）；W04 设备 token 遵循同一约定；
- 用户由 `backend/scripts/create_user.py` 维护（无注册端点）；角色三档
  owner / operator / viewer，scope 白名单集中在本模块 `rbac.py`。

## 边界

本目录之外的文件不要改。需要改共享文件时，走 `x-shared-registry` 变更请求（见 `docs/DEV-PLAN-v1.md` §7）。
