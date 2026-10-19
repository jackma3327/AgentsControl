/** 后端 error.code → 用户能读懂的中文。 */
const MAP: Record<string, string> = {
  unauthorized: "登录已过期，请重新登录",
  forbidden: "没有权限执行这个操作",
  not_found: "对象不存在，可能已被删除",
  conflict: "状态已变化，请刷新后重试",
  validation_failed: "填写内容不符合要求",
  rate_limited: "操作太频繁，稍后再试",
  upstream_error: "上游服务不可用，稍后再试",
  not_implemented: "该能力尚未接入（后端模块未合并）",
  network_error: "网络不可用，请检查连接",
};

export function errorText(code: string, fallback?: string): string {
  return MAP[code] ?? fallback ?? "请求失败";
}

/**
 * 限流剩余时间（S1-05）：把 `details.retry_after`（整数秒）渲染成"还需 X 分钟"。
 * 无数据或非法值返回 null，由调用方回退到 MAP 里的兜底文案。
 */
export function rateLimitedHint(details: unknown): string | null {
  const seconds = (details as { retry_after?: unknown } | null | undefined)?.retry_after;
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  if (seconds < 60) return `还需 ${Math.ceil(seconds)} 秒`;
  return `还需 ${Math.ceil(seconds / 60)} 分钟`;
}
