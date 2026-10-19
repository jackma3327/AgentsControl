/**
 * 类型化 API 客户端。
 *
 * 硬约定（与 docs/DEV-PLAN-v1.md §7 一致）：
 * 1. Base URL 只来自 `NEXT_PUBLIC_API_BASE_URL`，缺省同源相对路径，禁止硬编码；
 * 2. 访问令牌**不放 localStorage / 不放 URL**：短期令牌驻内存，长期刷新令牌走 httpOnly Cookie；
 * 3. 4xx 不重试（可重试的只有 408/429），5xx 交给上层 Query 重试（≤3 次）；
 * 4. 后端错误统一映射为 `ApiError`，UI 只读 `code` 做文案映射，不解析 message；
 * 5. 401 → 静默刷新一次并重放；刷新仍 401/403（会话已死）→ 跳 `/login?next=<当前路径>`，
 *    网络错误不跳（去登录解决不了断网）。并发 401 只跳一次，/login 自身不跳。
 */

export interface ApiErrorBody {
  error?: { code?: string; message?: string; request_id?: string; details?: unknown };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly details?: unknown;

  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }

  get retryable(): boolean {
    return this.status >= 500 || this.status === 408 || this.status === 429;
  }
}

export function apiBaseUrl(): string {
  // 缺省同源（S1-04 / 契约 C5）：请求形如 `/api/v1/*`，由 Caddy 反代到后端。
  // 不再兜底 localhost:8000——构建产物里出现它意味着"上线打到开发机"。
  // 注意缺省值带 `/api/v1` 而不是空串：各调用方传的 path 不含前缀（如 `/goals/`，
  // 约定见 lib/api/hooks.ts，该文件归 S4 冻结），空串会把请求打到站点根路径。
  // 需要直连后端时用 NEXT_PUBLIC_API_BASE_URL 覆盖（完整 base，含 /api/v1）。
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  return base ? base.replace(/\/$/, "") : "/api/v1";
}

// 访问令牌只驻内存：刷新页面即重新换取，减少 XSS 的持久化面
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

type FetchOptions = Omit<RequestInit, "body"> & { body?: unknown; json?: boolean };

/** 静默刷新的三种结局：成功换令牌 / 会话确实死了（401·403）/ 别的失败（网络等）。 */
type RefreshOutcome = "ok" | "unauthorized" | "failed";

let refreshInFlight: Promise<RefreshOutcome> | null = null;
let redirectingToLogin = false;

async function refreshOnce(): Promise<RefreshOutcome> {
  refreshInFlight ??= (async () => {
    try {
      // 空 body：refresh_token 走 HttpOnly Cookie（credentials: "include" 同源携带）
      const res = await fetch(`${apiBaseUrl()}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      });
      // 401/403 = Cookie 无效或已登出：这是"去登录"的信号，不是网络故障
      if (res.status === 401 || res.status === 403) return "unauthorized";
      if (!res.ok) return "failed";
      const body = (await res.json()) as { access_token?: string };
      if (body.access_token) {
        setAccessToken(body.access_token);
        return "ok";
      }
      return "failed";
    } catch {
      // 网络错误：不跳登录页，让上层显示网络错误态（去登录也解决不了断网）
      return "failed";
    } finally {
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();
  return refreshInFlight;
}

function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  // 并发 401 只跳一次；/login 自身不跳（防循环）
  if (redirectingToLogin || window.location.pathname.startsWith("/login")) return;
  redirectingToLogin = true;
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.assign(`/login?next=${next}`);
}

export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { body, json = true, headers, ...rest } = options;

  const send = async (): Promise<Response> =>
    fetch(`${apiBaseUrl()}${path}`, {
      ...rest,
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

  let res = await send();

  // 401 只重试一次：先尝试静默刷新（Cookie 渠道），再重放原请求。
  // 首屏无内存 token 时同样走这条路——第一次请求 401 → 静默换令牌 → 重放成功，
  // 用户看到的一直是骨架屏而不是错误态（"令牌过期无感"）。
  if (res.status === 401 && !path.startsWith("/auth/")) {
    const outcome = await refreshOnce();
    if (outcome === "ok") {
      res = await send();
    } else if (outcome === "unauthorized") {
      // 会话确实死了：去登录，并把当前路径作为 next 带回
      redirectToLogin();
    }
  }

  if (!res.ok) {
    let parsed: ApiErrorBody = {};
    try {
      parsed = (await res.json()) as ApiErrorBody;
    } catch {
      // 上游返回非 JSON（如 Caddy 502 页面）
    }
    throw new ApiError(
      res.status,
      parsed.error?.code ?? `http_${res.status}`,
      parsed.error?.message ?? `请求失败（HTTP ${res.status}）`,
      parsed.error?.request_id,
      parsed.error?.details,
    );
  }

  if (res.status === 204) return undefined as T;
  return (json ? await res.json() : (await res.text())) as T;
}

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "POST", body }),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: "PATCH", body }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: "DELETE" }),
};
