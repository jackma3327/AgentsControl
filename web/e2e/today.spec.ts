import { expect, test } from "@playwright/test";

import { mockApi } from "./helpers";

test("今日页：3 个数字、8 张决策卡、J/K/Enter 可用", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/today");

  await expect(page.getByRole("heading", { name: "今日" })).toBeVisible();
  await expect(page.getByText("决策负载")).toBeVisible();
  await expect(page.getByText("3/12")).toBeVisible();

  // 后端返回 10 条，页面只渲染 8 条，其余折叠成一句话
  await expect(page.getByRole("button", { name: "通过" })).toHaveCount(8);
  await expect(page.getByText("还有 2 条，明天再说。")).toBeVisible();

  // J 移到第二张，Enter 通过 → 必须发出 decide 请求
  await page.keyboard.press("j");
  await page.keyboard.press("Enter");
  await expect.poll(() => calls.filter((c) => c.method === "POST").length).toBe(1);
  const decide = calls.find((c) => c.method === "POST");
  expect(decide?.path).toBe("/approvals/a2/decide");
  expect((decide?.body as { decision: string }).decision).toBe("approved");
});
