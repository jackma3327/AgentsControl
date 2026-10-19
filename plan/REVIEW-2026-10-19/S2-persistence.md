# S2 · 落库与事务（intake / goals）

> 状态：REVIEW（2026-10-19，issue #24，分支 `win/s2-persistence`，PR [#25](https://github.com/jackma3327/AgentsControl/pull/25) 待合；fork `chenhe237531/AgentsControl`，因当前账号对上游只读）
> 来自 `docs/PRODUCT-REVIEW-2026-10-19.md` Issue 2（P0）、Issue 4 的后端部分。
> 这是最独立的一条流：只碰两个模块目录，不依赖任何人，也不被任何人阻塞。

## 0. 任务身份

| 项 | 值 |
|---|---|
| 流 | `S2` |
| 名称 | 写操作落库修复 + 全仓事务审计 |
| 窗口 | `W02`（收件箱与双轨目标） |
| 分支 | `win/s2-persistence` |
| 规模 | 3 个任务，约 0.5–1 人日 |
| 阻塞 | 无 |

## 1. 文件边界

**可改**

```
backend/app/modules/intake/**
backend/app/modules/goals/**
backend/tests/test_intake.py
backend/tests/test_goals.py
```

**只读（可看不可改；发现问题开 issue 给对方窗口）**

```
backend/app/core/db.py                     # W01
backend/app/modules/{approvals,devices,ledger,evals,execution,identity,github_sync,tasks}/**
backend/migrations/versions/**             # 本流不需要新迁移
```

**禁改**：`web/**`（S1/S4）、`deploy/**` `Dockerfile` `Makefile`（S3）、`scripts/**`（S3/S5）、`backend/app/api/router.py`（冻结）。

## 2. 冻结契约

| 编号 | 契约 |
|---|---|
| C5 | 事务提交在 **service 层显式 commit**；repository 只 `flush()`，不 commit；`get_db()` 的"业务层不 commit"注释与语义**保持不变** |
| C6 | 不引入 commit-on-success 中间件（`core/` 是冻结区，且会掩盖"故意不提交"的路径） |
| C7 | 写端点错误语义不变：校验失败 422 `validation_failed`、上游失败 502 `upstream_error`、未实现 501 `not_implemented` |

## 3. 任务

### S2-01 补 commit：intake 与 goals 的所有写路径
- **根因**：`core/db.py#get_db()` 明确"业务层不 commit"，但 `intake/service.py#capture`、`goals/service.py#create_goal/patch_goal` 只 flush；其余模块都有 commit。
- **改动**
  - `intake/service.py`：`capture` 与三问结构化写路径末尾 `await session.commit()`。
  - `goals/service.py`：`create_goal`、`patch_goal`（含冻结/降级）末尾 commit。
  - `repository.py`：保持 `flush()` 不动——分层职责不混。
  - 提交放在 service 的**成功路径末尾**，异常路径仍由 `get_db()` 的 `except` 回滚。
- **验收**
  1. `POST /api/v1/intake/items` → 201 → `GET /api/v1/intake/items` 立刻可见。
  2. `POST /api/v1/goals/` → 201 → `GET /goals/?limit=100` 立刻可见。
  3. `PATCH /goals/{id}` 改状态后 GET 反映新值。
  4. `docker compose exec db psql -U afc -d afc -c "select count(*) from intake_items; select count(*) from goals;"` 行数 > 0。
  5. 三问结构化的结果随 intake 一起落库（不是只更新内存）。
- **测试**：每个写端点加一条"POST → GET 可见"断言；三问用 stub 客户端，避免依赖 LLM。
- **反例自检**：故意让 commit 前抛异常，确认整条请求回滚、不留半条数据。

### S2-02 全仓 flush-only 审计
- **改动**：不写业务代码。用 `grep -rn "flush()" backend/app/modules` 逐个核对，产出表附在 PR 描述：

  | 模块 | 写路径 | 是否 commit | 结论 |
  |---|---|---|---|

- **验收**：确认仅 intake、goals 遗漏；若发现其它模块，**开 issue 指派给对应窗口**，不顺手改（`plan/README.md` §4.3）。
- **产出**：审计表 + 一句结论（"写路径提交已全量覆盖"）。

### S2-03 三问失败的错误码核对（只读为主）
- **背景**：前端要按 `error.code` 给"AI 服务未配置"文案，需要后端在未配置 `LITELLM_BASE_URL` 时返回**稳定错误码**，而不是裸 500。
- **改动**
  - 读 `intake/litellm.py`、`intake/service.py`、`intake/router.py`，确认未配置时的实际行为。
  - 若返回 500 → 在 `intake/service.py` 改为 `NotImplementedYetError`（501 `not_implemented`，语义是"能力未接入"）或 `UpstreamError`（502），二选一并写清理由。
  - 保持"绝不编造三问答案"的既有原则。
- **验收**：不配 `LITELLM_BASE_URL` 调三问 → 501/502 + 稳定 code；配了但上游超时 → 502 `upstream_error`。
- **交付给 S4**：在 PR 描述里写一行 `三问未配置 → <code>`，S4 据此写文案。

## 4. 自检

```bash
cd backend && ruff check app tests && pytest tests/test_intake.py tests/test_goals.py -q
./scripts/pr_precheck.sh --local

# 端到端（需 compose 已起）
curl -s -X POST localhost:8080/api/v1/intake/items -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' -d '{"text":"测试一条"}' -o /dev/null -w '%{http_code}\n'
curl -s localhost:8080/api/v1/intake/items -H 'Authorization: Bearer <token>'
```

## 5. 完成定义

- [x] 四个写端点 POST→GET 全部可见（fake 层回归测试锁定；真实库验证见 PR 描述）
- [x] 集成测试覆盖，不依赖外部 LLM（三问走 MockTransport）
- [x] 审计表覆盖 9 个模块，结论明确（见 PR 描述）
- [x] 三问未配置的错误码已确定并写进 PR（502 `upstream_error`，无需改码）

## 6. PR 与提交

单 PR：`[W02] fix: persist intake and goals writes`，提交前缀 `fix(#<issue>): commit-intake-goals`。
不要在本 PR 里顺手改 identity / web / deploy。

## 7. 卡住时

- 如果发现 `get_db()` 本身会吞异常 → 只记录，开 issue 给 W01，不要改 `core/`。
- 如果 goals 的 `patch_goal` 有并发覆盖问题 → 不在本流修，单开 issue（评审未涉及）。
