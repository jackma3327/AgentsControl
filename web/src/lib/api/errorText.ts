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
