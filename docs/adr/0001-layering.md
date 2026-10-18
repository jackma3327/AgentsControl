# ADR 0001 · 三层架构与依赖方向

- 状态：Accepted
- 日期：2026-10-18
- 关联：W01-I05（本 ADR 是 10 窗口并行的分层契约）

## 1. 决策

后端采用**单后端 monolith + 模块化三层**，每个业务模块（`app/modules/<m>/`）内部固定四层：

```
router.py     →  service.py  →  repository.py  →  models.py
(HTTP 适配)      (业务规则)      (数据访问)         (ORM 定义)
```

依赖方向**只能从左向右**，禁止反向：

| 层 | 允许依赖 | 禁止 |
|---|---|---|
| router | 本模块 service、`app.core.*`、pydantic schemas | 直接写 SQL / 碰 ORM |
| service | 本模块 repository、其它模块 service 的**公开函数**、`app.core.*` | 依赖 Request/Response（保持可单测） |
| repository | 本模块 models、SQLAlchemy | import 其它模块的任何东西 |
| models | `app.core.db.Base` | import 任何业务层 |

## 2. 为什么这样切

1. **10 窗口并行**：窗口只改自己的模块目录，跨模块协作只有两种合法形式——
   调对方 service 公开函数、或只读 SQL（见 plan/README.md §4.3）。
   层边界越清晰，冲突面越小；
2. **可测试性**：service 不依赖 Request/Response，单测可以像 W01 的 `tests/test_identity.py`
   一样用内存替身替换 repository/limiter，CI 无需真实 PG/Redis；
3. **事务边界明确**：repository 不 commit，service 显式提交（`core.db.get_db` 出错回滚兜底），
   多步写入要么全成要么全不成。

## 3. 共享内核（app/core/）

`core/` 是全模块的公共依赖：config / db / redis / security / deps / errors / middleware / logging。
owner 是 W01；其它窗口**只读**，修改走 shared-registry 变更请求。
新错误类型在 `core/errors.py` 加 `AppError` 子类，`docs/ERROR-CODES.md` 由
`backend/scripts/gen_error_codes.py` 重新生成（CI no-diff 断言）。

## 4. 契约快照

- `backend/openapi.snapshot.json` 是接口契约快照（路径+方法+参数+响应码），
  由 `scripts/openapi_snapshot.py --write` 刷新；CI `--check` 对比，
  删除/修改操作视为 breaking change，必须在 PR 描述说明影响面；
- 迁移文件命名 `w<NN>_<seq>_<slug>.py`，`down_revision` 串成单链，
  CI 断言恰好 1 个 head 且可 upgrade → downgrade → upgrade。

## 5. 后果

- 新端点的标准动作：models → 迁移 → repository → service → router → 快照 → 审计（audit_scopes）；
- 禁止 router 直查 ORM 的诱惑（"就一行"），破坏分层会让单测替身失效；
- 模块间禁止互碰目录，需要新接口写进自己的 issue 描述，由对方窗口排期。
