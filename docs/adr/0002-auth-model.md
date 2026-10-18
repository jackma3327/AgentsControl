# ADR 0002 · 认证模型（登录 / 令牌轮换 / 吊销 / 限流）

- 状态：Accepted
- 日期：2026-10-18
- 关联：W01-I01 / I02 / I03 / I04

## 1. 背景与约束

单用户白名单体系（不开放注册），控制面部署在 Caddy 反代之后。
其它 9 个窗口共享三样东西：认证依赖（`core.deps`）、错误结构（`core.errors`）、request_id。
认证模型必须一次定型，避免每个窗口自己造一套鉴权。

## 2. 令牌设计

| 项 | access token | refresh token |
|---|---|---|
| TTL | 15 min（`ACCESS_TOKEN_TTL_SECONDS=900`） | 14 天（`REFRESH_TOKEN_TTL_SECONDS=1209600`） |
| 状态 | 无状态（验签即用） | 有状态（`refresh_tokens` 表，**只存 SHA-256 哈希**） |
| Claims | `sub` / `typ` / `role` / `scopes` / `sid` | `sub` / `typ` / `sid` |
| 传输 | `Authorization: Bearer` | 请求体（不落 cookie，前端安全存储） |

`sid`（session_id）是会话链标识：同一登录会话轮换出的所有 refresh token 共享同一 `sid`，
这是"重放即吊销整条链"的基础。

## 3. 轮换与重放（关键决策）

- **每次刷新必轮换**：旧 refresh token 立即置 `revoked_at`，`replaced_by` 指向新哈希；
- **重放 = 盗窃推定**：已轮换的旧令牌再次出现，立即吊销该 `sid` 链上的全部令牌并写入
  `auth_events(event_type="replay_detected")`。正常客户端丢弃旧令牌不会触发；
- **并发窗口取舍**：同一旧令牌的两个并发刷新请求存在竞态（都读到未吊销状态）。
  单用户系统下概率极低且后果有限（多发一对令牌），不做 `SELECT ... FOR UPDATE` 串行化；
  若未来引入多租户再收紧。

## 4. 吊销语义（跨窗口约定）

- 统一用 `revoked_at` 列：`NULL = 有效`，非空 = 吊销时间。**W04 的设备 token 遵循同一约定**；
- `POST /auth/logout`：吊销当前 `sid` 链的全部 refresh token（自服务操作，不挂 scope）；
- `POST /auth/revoke-all`：吊销该主体全部会话（**需要 `auth:manage` scope**，即 owner）；
- 事件流水统一写 `auth_events`（只追加）：`login_success` / `login_failure` /
  `token_refresh` / `replay_detected` / `logout` / `revoke_all`。
  W04 的设备吊销事件也写本表（event_type 前缀 `device_`）。

### 已知取舍：吊销后旧 access token 仍可用 ≤15 min

access token 无状态，吊销不召回。登出/吊销后，旧 access token 在剩余有效期内仍能通过
`get_current_owner`。这是**有意的设计取舍**：换全部端点实时查库校验（每请求 +1 次 DB 往返）
不值得。缓解：① access TTL 只有 15 min；② 高危操作集中在需要 `auth:manage` 的写端点；
③ 写操作的重要对象（任务、审批）有自己的状态机防护。
单测已固化该行为（`test_logout_revokes_session_chain` / `test_revoke_all_kills_every_session`）。

## 5. 登录与限流

- 凭据：`users` 表（PBKDF2-HMAC-SHA256，200k 迭代），行由 `backend/scripts/create_user.py`
  维护，无注册端点；`OWNER_OPENIDS` 白名单在 `core.deps` 兜底（令牌主体不在白名单 → 403）；
- **登录失败限流**：subject 维度 5 次 / 15 分钟 + IP 维度 20 次 / 15 分钟（跨主体爆破防护），
  Redis `incr_with_ttl` 固定窗口，锁定检查先于密码校验；成功登录清零 subject 计数；
- **/auth/* 独立请求桶**：单 IP 30 次 / 分钟（`router` 级依赖 `auth_ip_bucket`），
  与登录失败计数互相独立（不同 key 空间），防止慢速枚举与接口滥用；
- **限流降级**：Redis 不可达时 fail-open（放行 + 告警日志）。理由：限流是加固项，
  不是可用性前提；单用户系统宁可短暂放开限流也不能把登录打挂。若将来暴露公网多用户，改为 fail-closed。

## 6. RBAC

- 三档角色：`owner`（全部 scope）/ `operator`（读写，无会话管理）/ `viewer`（只读）；
- scope 白名单唯一权威来源：`app/modules/identity/rbac.py`，命名 `<module>:<action>`；
- 写端点强制挂 scope：`Depends(require_scope("<module>:write"))`；
  `backend/scripts/audit_scopes.py` 扫描全路由，未挂认证/未挂 scope 的端点会被列出
  （依赖标记 `__required_scope__`，`--strict` 供 CI 门禁）。

## 7. 安全响应头与传输层（W01-I04）

应用层 `SecurityHeadersMiddleware` 已带 `X-Content-Type-Options` / `X-Frame-Options` /
`Referrer-Policy` / `COOP`，`/api/*` 一律 `Cache-Control: no-store`；
生产环境 `/docs`、`/redoc`、`/openapi.json` 在 `create_app` 中直接禁用。

Caddy 侧建议（走 shared-registry 变更请求，本窗口不改 `deploy/`）：

```caddy
# 建议追加到生产 Caddyfile 的站点块
header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
    Content-Security-Policy "default-src 'self'; frame-ancestors 'none'"
    X-Forwarded-For 的信任由 Caddy 终结，应用取 XFF 首段作为客户端 IP
}
reverse_proxy 127.0.0.1:8000
```

应用层取客户端 IP 的信任假设：**只信任 Caddy 注入的 X-Forwarded-For 首段**
（应用不可直接暴露公网，否则 XFF 可伪造，IP 维度限流失效）。

## 8. 后果

- 所有后端窗口统一使用 `OwnerDep` + `require_scope`，不再自造鉴权；
- 前端（W10）需要实现：令牌对存储、401 时静默 refresh、refresh 失败跳登录；
- 设备端（W04/W05）走独立的 HMAC 验签通道，与用户令牌体系隔离。
