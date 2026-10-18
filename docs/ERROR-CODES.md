# 错误码表（自动生成，勿手改）

> 由 `backend/scripts/gen_error_codes.py` 从 `app/core/errors.py` 生成。
> 手工编辑会被 CI no-diff 断言打回；新增错误码请改 errors.py 后重新生成。
> 客户端只读 `error.code` 做行为映射，不解析 `message`。

| code | HTTP | 默认 message | 说明 |
|---|---|---|---|
| `unauthorized` | 401 | 未认证或凭据已过期 | — |
| `forbidden` | 403 | 无权执行该操作 | — |
| `not_found` | 404 | 对象不存在 | — |
| `conflict` | 409 | 状态冲突，请刷新后重试 | — |
| `validation_failed` | 422 | 参数校验失败 | — |
| `rate_limited` | 429 | 请求过于频繁 | — |
| `not_implemented` | 501 | 该能力尚未实现 | 模块占位：窗口尚未实现该端点。便于并行开发期区分「没实现」与「坏了」。 |
| `upstream_error` | 502 | 上游服务不可用 | — |
