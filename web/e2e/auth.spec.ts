import { expect, test, type Page } from "@playwright/test";

import { TEST_PASSWORD, TEST_SUBJECT, mockApi } from "./helpers";

/**
 * 认证全链路 e2e（S1-07 / R03）：登录 → 401 恢复 → 静默续期 → 登出。
 * 全部 API 走 route mock（helpers 的 auth fixture），不依赖真实后端。
 *
 * 覆盖 S1 的验收：
 * ① 错误密码有明确提示；② 未登录访问受保护页 → 登录页 → 登录后回跳；
 * ③ 刷新页面（内存 token 丢失）→ 静默续期，无错误态闪烁；④ 登出后回登录页且无法恢复；
 * ⑤ 连续失败触发限流 → 页面显示真实剩余分钟（S1-05）。
 */

async function login(page: Page, password: string = TEST_PASSWORD) {
  await page.getByLabel("账号").fill(TEST_SUBJECT);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
}

test("① 错误密码：提示账号或密码错误，停留在登录页", async ({ page }) => {
  await mockApi(page, { auth: true });
  await page.goto("/login");
  await login(page, "wrong-password");
  // 注意用 getByText 而非 getByRole("alert")：Next 的路由 announcer 也带 role=alert
  await expect(page.getByText("主体或密码错误")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});

test("② 未登录访问 /today：落到登录页并带回 next，登录后回跳且有数据", async ({ page }) => {
  await mockApi(page, { auth: true });
  await page.goto("/today");
  // 无会话：数据请求 401 → 静默 refresh 失败 → 跳登录（next 带回当前路径）
  await expect(page).toHaveURL(/\/login\?next=%2Ftoday/);

  await login(page);
  // 登录成功 → 回跳 /today，决策卡有数据
  await expect(page).toHaveURL(/\/today/);
  await expect(page.getByRole("heading", { name: "今日" })).toBeVisible();
  await expect(page.getByText("决策负载")).toBeVisible();
  await expect(page.getByRole("button", { name: "通过" })).toHaveCount(8);
});

test("③ 刷新页面：内存 token 丢失后静默续期，无错误态", async ({ page }) => {
  const { calls } = await mockApi(page, { auth: true });
  await page.goto("/login");
  await login(page);
  await expect(page.getByRole("heading", { name: "今日" })).toBeVisible();

  await page.reload();
  // 首屏无内存 token：第一次数据请求 401 → 静默 refresh 换令牌 → 重放成功。
  // 用户视角：页面正常渲染，不出现"登录已过期"，也不闪错误态。
  await expect(page.getByRole("heading", { name: "今日" })).toBeVisible();
  await expect(page.getByText("登录已过期")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "去登录" })).toHaveCount(0);
  const refreshes = calls.filter((c) => c.method === "POST" && c.path === "/auth/refresh");
  expect(refreshes.length).toBeGreaterThan(0);
});

test("④ 登出后回到登录页，且刷新无法恢复会话", async ({ page }) => {
  await mockApi(page, { auth: true });
  await page.goto("/login");
  await login(page);
  await expect(page.getByRole("heading", { name: "今日" })).toBeVisible();

  // 登出入口在侧边栏（components/shell 归 S4 流）；本用例经页面内 fetch 走 mock，
  // 验证登出的 API 契约：登出后 refresh 失效、受保护页重新回到登录页。
  await page.evaluate(() =>
    fetch("/api/v1/auth/logout", { method: "POST", credentials: "include" }),
  );

  await page.goto("/today");
  await expect(page).toHaveURL(/\/login\?next=%2Ftoday/);
});

test("⑤ 连续登录失败触发限流：显示真实剩余分钟", async ({ page }) => {
  await mockApi(page, { auth: true });
  await page.goto("/login");
  for (let i = 0; i < 5; i++) {
    await login(page, "wrong-password");
    await expect(page.getByText("主体或密码错误")).toBeVisible();
  }
  // 第 6 次即使密码正确也 429；页面显示 details.retry_after 换算出的分钟数
  await login(page);
  await expect(page.getByText("还需 13 分钟")).toBeVisible();
  await expect(page).toHaveURL(/\/login/);
});
