# devices · 设备接入

- **归属窗口**：W04
- **路由前缀**：`/devices`
- **职责**：注册与 token 哈希、心跳/增量/快照三通道、(node_id,seq) 幂等、suspect/offline 状态机、能力标签、指令下发白名单。
- **状态**：已实现（W04-I01…I06 全部落地；`/_status`、`/_ping` 为骨架自检端点，登记在 openapi.snapshot.json，保留勿删）。

## 端点清单

### Owner（Bearer JWT）
- `POST /bootstrap-codes` —— 人工签发一次性引导码（明文只出现一次）
- `POST /register` —— 设备注册（凭引导码；同指纹同 node 幂等；指纹冲突 409；吊销后可重激活）
- `GET /` —— 设备列表（`?status=&limit=&offset=`）
- `PATCH /{node_id}` —— 更新 caps / tolerance / squad / purpose / trust
- `POST /{node_id}/revoke` —— 吊销（token_hash 置空，即刻失效）
- `GET /{node_id}/status` —— 实时状态（含 effective_status / missed_heartbeats / 时钟偏差）
- `GET /{node_id}/events?since=` —— 按游标查事件
- `GET /{node_id}/snapshots` —— 快照历史（最多 30 份）
- `POST /{node_id}/commands` —— 下发指令（白名单五种；中风险需 `approved_by`；third_party 403）

### Device（HMAC：`X-Afc-Node` / `X-Afc-Timestamp` / `X-Afc-Signature`）
- `POST /heartbeat` —— 心跳（60s，UPSERT 单行；偏差 >300s 打标 + 告警）
- `POST /events` —— 增量批量（30s；`(node_id,seq)` 唯一幂等；未知 kind 计 rejected；`dropped>0` 告警；上限 500）
- `POST /snapshot` —— 快照（60min，唤醒先发；保留 30 份；能力变更写 device_cap_events；标记 woke_at）
- `GET /{node_id}/commands?wait=25` —— 指令长轮询（超时 204，不用 WebSocket）
- `POST /commands/{command_id}/ack` —— 指令回执

## 安全语义

- 设备 token 只存 sha256（`token_hash`）；明文只在注册/重激活响应出现一次。
- **HMAC 签名密钥 = sha256(明文 token) = 库里的 token_hash**（设备端对 raw token 做同样派生）。
- 验签失败统一 401 文案（不区分节点不存在/已吊销/签名错/时间窗超，防枚举）；每 node 失败 10 次/分钟 → 429。
- 时间以服务端 `received_at` 为准；设备 `occurred_at`/`ts` 只用于排序与偏差告警（>5min 告警）。

## 边界

本目录之外的文件不要改。需要改共享文件时，走 `x-shared-registry` 变更请求（见 `docs/DEV-PLAN-v1.md` §7）。
迁移文件：`w04_01_devices`（devices/引导码/心跳/告警）、`w04_02_events`、`w04_03_snapshots`、`w04_04_commands`，单头链。
后台巡检脚本：`scripts/sweep_devices.py`（suspect=丢 3 次、offline=丢 5 分钟；offline 只发 lease_reclaim 事件，不改 tasks 表）。
