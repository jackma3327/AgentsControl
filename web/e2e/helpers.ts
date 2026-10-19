import type { Page, Route } from "@playwright/test";

/** 记录 UI 实际发出的请求，用于断言"点了之后真的调了接口"。 */
export type Call = { method: string; path: string; body: unknown };

const NOW = "2026-10-19T05:00:00Z";

// --- 认证 fixture 常量（与后端默认策略对齐：5 次失败锁 15 分钟）---
export const TEST_SUBJECT = "owner@example.com";
export const TEST_PASSWORD = "correct-password-1";
export const LOGIN_FAILURE_LIMIT = 5;
const RETRY_AFTER_SECONDS = 780; // 13 分钟：让"还需 X 分钟"可确定性断言

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

const approval = (i: number) => ({
  id: `a${i}`,
  level: i === 1 ? "L0" : "L1",
  title: i === 1 ? "批准把 W04 发布到生产" : `待决策事项 ${i}`,
  payload: null,
  task_id: null,
  goal_id: null,
  status: "pending",
  visible_at: NOW,
  created_at: NOW,
  decided_at: null,
  decision: null,
  decider: null,
  reason: null,
  held_reason: null,
  held_at: null,
});

const goal = {
  id: "g1",
  code: "G-20261019-001",
  owner_subject: "owner-1",
  track: "growth",
  template: "",
  title: "把迁移合并头的处理写成脚本",
  acceptance: "下一次并行窗口合并时不再手写 merge revision",
  horizon: "2 周",
  horizon_at: null,
  budget_money: null,
  budget_hours: 4,
  attention_minutes_per_day: 30,
  north_star: null,
  guardrails: null,
  deliverables: null,
  status: "active",
  intake_item_id: "i1",
  created_at: NOW,
  updated_at: NOW,
  achieved_at: null,
  frozen_at: null,
  degraded_at: null,
  stop_loss_triggered_at: null,
};

/**
 * 拦截所有 /api/v1 请求。返回 calls，供断言。
 * 刻意不依赖真实后端：E2E 只验证"视图 + 交互 + 请求形状"。
 *
 * `auth: true` 时额外挂一套认证 fixture（auth.spec.ts 用）：
 * - POST /auth/login  凭据正确 → 200 签发令牌；错误 → 401；连续 5 次失败 → 429 + retry_after；
 * - POST /auth/refresh 有存活会话 → 200 轮换签发；无会话 → 401（模拟 Cookie 失效）；
 * - POST /auth/logout 200 并清空全部会话；
 * - 其余数据端点要求请求头带有效 Bearer 令牌，否则 401（模拟未登录/会话过期）。
 * 这样"401 → 静默续期 → 重放成功"与"401 → 跳登录"两条前端路径都能被驱动。
 */
export async function mockApi(
  page: Page,
  options: { auth?: boolean } = {},
): Promise<{ calls: Call[] }> {
  const calls: Call[] = [];
  const intakeItems: Record<string, unknown>[] = [];

  // --- 认证 fixture 状态（模拟服务端会话）---
  const authState = options.auth
    ? { tokenSeq: 0, validTokens: new Set<string>(), loginFailures: 0 }
    : null;
  const issueToken = (): string => {
    authState!.tokenSeq += 1;
    return `tok-${authState!.tokenSeq}`;
  };
  const TOKEN_TTL = 900;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    calls.push({ method, path, body });

    if (authState) {
      if (method === "POST" && path === "/auth/login") {
        const { subject, password } = (body ?? {}) as { subject?: string; password?: string };
        // 与后端语义一致：进入登录先查锁定（失败计数 >= 5 即 429），失败才计数。
        // 即前 5 次失败都是 401，第 6 次尝试（哪怕密码正确）才是 429。
        if (authState.loginFailures >= LOGIN_FAILURE_LIMIT) {
          return json(route, 429, {
            error: {
              code: "rate_limited",
              message: "登录失败次数过多，请稍后再试",
              request_id: "req-login-429",
              details: { retry_after: RETRY_AFTER_SECONDS },
            },
          });
        }
        if (subject === TEST_SUBJECT && password === TEST_PASSWORD) {
          const token = issueToken();
          authState.validTokens.clear();
          authState.validTokens.add(token);
          authState.loginFailures = 0;
          return json(route, 200, {
            access_token: token,
            refresh_token: `rt-${token}`,
            token_type: "bearer",
            expires_in: TOKEN_TTL,
            subject: TEST_SUBJECT,
            role: "owner",
            scopes: ["auth:manage"],
          });
        }
        authState.loginFailures += 1;
        return json(route, 401, {
          error: { code: "unauthorized", message: "主体或密码错误", request_id: "req-login" },
        });
      }

      if (method === "POST" && path === "/auth/refresh") {
        // 空 body + Cookie：mock 无法读 HttpOnly Cookie，用"会话是否存活"等价模拟
        if (authState.validTokens.size === 0) {
          return json(route, 401, {
            error: { code: "unauthorized", message: "刷新令牌无效或已过期", request_id: "req-refresh" },
          });
        }
        const rotated = issueToken();
        authState.validTokens.clear();
        authState.validTokens.add(rotated);
        return json(route, 200, {
          access_token: rotated,
          refresh_token: `rt-${rotated}`,
          token_type: "bearer",
          expires_in: TOKEN_TTL,
          subject: TEST_SUBJECT,
          role: "owner",
          scopes: ["auth:manage"],
        });
      }

      if (method === "POST" && path === "/auth/logout") {
        authState.validTokens.clear();
        return json(route, 200, { revoked: 1 });
      }

      // 数据端点：无有效 Bearer 令牌一律 401（前端据此走静默续期或跳登录）
      const bearer = (request.headers()["authorization"] ?? "").replace(/^Bearer /, "");
      if (!authState.validTokens.has(bearer)) {
        return json(route, 401, {
          error: { code: "unauthorized", message: "未认证或凭据已过期", request_id: "req-data-401" },
        });
      }
    }

    if (method === "GET" && path === "/approvals/queue") {
      return json(route, 200, {
        items: Array.from({ length: 10 }, (_, i) => approval(i + 1)),
        count: 10,
      });
    }

    if (method === "GET" && path === "/approvals/bandwidth") {
      return json(route, 200, {
        config: {
          daily_decision_limit: 12,
          duration_window_days: 14,
          duration_rise_pct: 20,
          dispatch_cut_pct: 50,
          veto_floor_pct: 90,
          veto_ceiling_pct: 10,
          review_sample: 1,
          night_start_hour: 23,
          night_end_hour: 7,
          night_tz_offset_hours: 8,
        },
        input: {
          decisions_today: 3,
          daily_avg_minutes: [12, 15, 11],
          veto_rate_recent: 0.1,
          veto_rate_baseline: 0.1,
          now_hour: 14,
        },
        verdict: {
          defer_non_critical: false,
          dispatch_factor: 1,
          fatigue_suspected: false,
          review_sample: 0,
          night_hold_l0: false,
          reasons: [],
        },
      });
    }

    if (method === "GET" && path === "/ledger/summary") {
      return json(route, 200, {
        period: "2026-10",
        currency: "CNY",
        categories: {
          subscription: 60,
          tools: 30,
          device: 20,
          rework: 10,
          qc: 5,
          labor: 3.5,
        },
        total: 128.5,
        attention: { period: "2026-10", minutes: 95, outcomes: 4, min_per_outcome: 23.75 },
      });
    }

    if (method === "GET" && path === "/goals/") {
      return json(route, 200, [goal]);
    }

    if (method === "GET" && path === "/ledger/budget-status") {
      return json(route, 200, {
        goal_id: "g1",
        budget: 4,
        spent: 1.5,
        remaining: 2.5,
        frozen: false,
      });
    }

    if (method === "GET" && path === "/evals/radar") {
      return json(route, 200, { week: "2026-W42", models: [], dimensions: [], generated_at: NOW });
    }

    if (method === "GET" && path === "/evals/ceiling") {
      return json(route, 200, { week: "2026-W42", dimensions: [], blocked: [], risky: [] });
    }

    if (method === "GET" && ["/evals/skills", "/evals/cards", "/evals/harness", "/evals/drift"].includes(path)) {
      return json(route, 200, []);
    }

    if (method === "POST" && /^\/approvals\/[^/]+\/decide$/.test(path)) {
      return json(route, 200, { approval: approval(1), held: false });
    }

    if (method === "GET" && path === "/intake/items") {
      return json(route, 200, { items: intakeItems, next_cursor: null });
    }

    if (method === "POST" && path === "/intake/items") {
      const text = (body as { text?: string })?.text ?? "";
      const item = {
        id: "i1",
        text,
        source: "web",
        status: "raw",
        created_at: NOW,
        structured_at: null,
        structured: null,
        structure_cost_usd: null,
        promoted_goal_id: null,
      };
      intakeItems.unshift(item);
      return json(route, 201, item);
    }

    if (method === "POST" && path === "/intake/items/i1/structure") {
      const item = intakeItems.find((i) => i.id === "i1");
      const structured = {
        acceptance: "下一次合并只需跑一条命令，不再手写 merge revision",
        horizon: "2 周",
        budget: "4 小时",
        suggest_interest: false,
      };
      if (item) {
        item.status = "structured";
        item.structured = structured;
      }
      return json(route, 200, {
        item_id: "i1",
        structured,
        model: "fake-model",
        cost_usd: 0.0012,
      });
    }

    if (method === "POST" && path === "/goals/") {
      return json(route, 201, goal);
    }

    if (method === "GET" && path === "/tasks/") {
      const tasks = [
        ...Array.from({ length: 5 }, (_, i) => ({
          id: `t-done-${i}`,
          title: `已完成任务 ${i}`,
          state: "done",
          device_id: null,
          goal_id: null,
          created_at: NOW,
          updated_at: NOW,
        })),
        {
          id: "t-stuck",
          title: "抓取竞品价格卡住 20 分钟",
          state: "stuck",
          device_id: "d1",
          goal_id: null,
          created_at: NOW,
          updated_at: NOW,
        },
        {
          id: "t-failed",
          title: "生成周报失败：模型超时",
          state: "failed",
          device_id: "d2",
          goal_id: null,
          created_at: NOW,
          updated_at: NOW,
        },
      ];
      return json(route, 200, { items: tasks, count: tasks.length });
    }

    return json(route, 404, { error: { code: "not_found", message: `未 mock 的路径 ${path}` } });
  });

  return { calls };
}
