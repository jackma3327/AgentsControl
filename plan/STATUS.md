# 窗口进度（自动生成）

> 生成时间：2026-10-19 15:43　请勿手工编辑，运行 `./scripts/window-status.sh --write` 重建。

### W01 · 身份、权限与工程内核

- 分支：`win/w01-identity-kernel`　worktree：`.worktrees/w01-identity-kernel`　**未创建**
- PR：PR #1 (MERGED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W01-I01 | 登录与令牌轮换 | 1 | DONE | `—` |
| W01-I02 | RBAC 与 scope 校验 | 2 | DONE | `—` |
| W01-I03 | 会话与设备吊销联动 | 3 | DONE | `—` |
| W01-I04 | 速率限制与安全响应头加固 | 3 | DONE | `—` |
| W01-I05 | 工程内核补齐（错误码表与 OpenAPI 快照） | 4 | DONE | `—` |

**进度 5/5（100%）**

### W02 · 收件箱与双轨目标

- 分支：`win/w02-intake-goals`　worktree：`.worktrees/w02-intake-goals`　**未创建**
- PR：无 PR

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W02-I01 | Inbox 捕获 | 1 | DONE | `—` |
| W02-I02 | AI 三问结构化 | 2 | DONE | `—` |
| W02-I03 | 目标卡（双轨） | 2 | DONE | `—` |
| W02-I04 | 模板库与止损判定 | 3 | DONE | `—` |
| W02-I05 | 兴趣池与自动归档 | 3 | DONE | `—` |
| W02-I06 | 目标看板聚合接口 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W03 · 任务队列与租约

- 分支：`win/w03-task-queue`　worktree：`.worktrees/w03-task-queue`　**未创建**
- PR：PR #8 (MERGED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W03-I01 | 任务模型与状态机 | 1 | DONE | `—` |
| W03-I02 | 租约与心跳回收 | 2 | DONE | `—` |
| W03-I03 | 能力匹配与派活约束 | 2 | DONE | `—` |
| W03-I04 | 预算阀门 | 3 | DONE | `—` |
| W03-I05 | 优先级与公平调度 | 3 | DONE | `—` |
| W03-I06 | 死信与重试策略 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W04 · 设备接入

- 分支：`win/w04-device-ingest`　worktree：`.worktrees/w04-device-ingest`　**未创建**
- PR：PR #6 (Draft, checks: —)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W04-I01 | 设备注册与 token | 1 | DONE | `—` |
| W04-I02 | HMAC 验签中间件 | 2 | DONE | `—` |
| W04-I03 | 心跳与设备状态机 | 2 | DONE | `—` |
| W04-I04 | 增量事件（幂等 + 游标） | 2 | DONE | `—` |
| W04-I05 | 快照与能力变更 | 3 | DONE | `—` |
| W04-I06 | 指令下发白名单 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W05 · Go 设备 Agent

- 分支：`win/w05-go-agent`　worktree：`.worktrees/w05-go-agent`　**未创建**
- PR：PR #7 (CLOSED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W05-I01 | 心跳与指标采集（macOS 优先） | 1 | DONE | `—` |
| W05-I02 | SQLite 事件缓冲与游标续传 | 2 | DONE | `—` |
| W05-I03 | 服务安装（LaunchAgent/systemd/Windows Service） | 3 | DONE | `—` |
| W05-I04 | 快照与唤醒后先快照再增量 | 3 | DONE | `—` |
| W05-I05 | 打包发布（GoReleaser） | 4 | DONE | `—` |
| W05-I06 | 断网与本地队列降级 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W06 · 执行面（Worker 协议与 Orca）

- 分支：`win/w06-execution`　worktree：`.worktrees/w06-execution`　**未创建**
- PR：PR #9 (MERGED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W06-I01 | Worker 协议（claim/heartbeat/complete/fail） | 1 | DONE | `—` |
| W06-I02 | Orca probe 与能力自检 | 2 | DONE | `—` |
| W06-I03 | worktree 生命周期与 .fleet-task.json | 2 | DONE | `—` |
| W06-I04 | 会话 spawn/send/steer/kill | 3 | DONE | `—` |
| W06-I05 | harvest：产物与 trace 归集 | 3 | DONE | `—` |
| W06-I06 | 会话视图数据接口 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W07 · 账本、闸门与带宽熔断

- 分支：`win/w07-ledger-approvals`　worktree：`.worktrees/w07-ledger-approvals`　**未创建**
- PR：PR #2 (MERGED, checks: ✗)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W07-I01 | 账本模型与成本归集 | 1 | DONE | `—` |
| W07-I02 | 注意力账本（min/outcome） | 2 | DONE | `—` |
| W07-I03 | 闸门与决策分级 | 2 | DONE | `—` |
| W07-I04 | 带宽熔断 | 3 | DONE | `—` |
| W07-I05 | 订阅摊销与配额消耗率 | 3 | DONE | `—` |
| W07-I06 | 账本对账与导出 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W08 · 资产面（模型雷达 / skill / 认知卡）

- 分支：`win/w08-assets-radar`　worktree：`.worktrees/w08-assets-radar`　**未创建**
- PR：PR #11 (MERGED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W08-I01 | 题库与运行框架 | 1 | DONE | `—` |
| W08-I02 | 8 维评分与雷达聚合 | 2 | DONE | `—` |
| W08-I03 | 能力上限标记与立项校验 | 2 | DONE | `—` |
| W08-I04 | 漂移预警 | 3 | DONE | `—` |
| W08-I05 | skill 与 harness 版本 | 3 | DONE | `—` |
| W08-I06 | 认知卡库 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W09 · GitHub 验收总线与 CI 工具链

- 分支：`win/w09-github-oracle`　worktree：`.worktrees/w09-github-oracle`　**未创建**
- PR：PR #17 (MERGED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W09-I01 | Webhook 接收与验签 | 1 | DONE | `—` |
| W09-I02 | PR 与 checks 状态同步 | 2 | DONE | `—` |
| W09-I03 | Issue 镜像（仅单向） | 2 | DONE | `—` |
| W09-I04 | 熔断与转人工 | 3 | DONE | `—` |
| W09-I05 | 合并前预检脚本 | 1 | DONE | `—` |
| W09-I06 | CI 工作流整合与门禁 | 4 | DONE | `—` |

**进度 6/6（100%）**

### W10 · 前端控制台与可视化

- 分支：`win/w10-console-ui`　worktree：`.worktrees/w10-console-ui`　**未创建**
- PR：PR #13 (MERGED, checks: ✓)

| Issue | 标题 | Wave | 状态 | 最近提交 |
|---|---|---|---|---|
| W10-I01 | 设计令牌与应用骨架收口 | 1 | DONE | `—` |
| W10-I02 | 今日页 | 1 | DONE | `—` |
| W10-I03 | 收件箱与目标页 | 2 | DONE | `—` |
| W10-I04 | 执行页与团队页 | 3 | DONE | `—` |
| W10-I05 | 账本与兵工厂页 | 3 | DONE | `—` |
| W10-I06 | 总视图与项目视图（React Flow） | 4 | DONE | `—` |
| W10-I07 | E2E 冒烟与可访问性 | 4 | DONE | `—` |

**进度 7/7（100%）**
