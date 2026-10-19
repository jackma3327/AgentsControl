import { expect, test } from "@playwright/test";

import { mockApi } from "./helpers";

test("执行页：默认只看异常，正常任务不占屏", async ({ page }) => {
  await mockApi(page);
  await page.goto("/execution");

  await expect(page.getByRole("heading", { name: "执行" })).toBeVisible();
  await expect(page.getByText("异常任务")).toBeVisible();

  await expect(page.getByText("抓取竞品价格卡住 20 分钟")).toBeVisible();
  await expect(page.getByText("生成周报失败：模型超时")).toBeVisible();
  await expect(page.getByText("已完成任务 0")).toHaveCount(0);

  // 异常 2 条：一个 stuck、一个 failed
  await expect(page.getByText("stuck", { exact: true })).toBeVisible();
  await expect(page.getByText("failed", { exact: true })).toBeVisible();
});
