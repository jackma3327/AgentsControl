import { expect, test } from "@playwright/test";

import { mockApi } from "./helpers";

test("收件箱：自动聚焦、Cmd/Ctrl+Enter 提交、三问、转目标", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/inbox");

  const input = page.getByLabel("记一条");
  await expect(input).toBeFocused();

  await input.fill("把迁移合并头的处理写成脚本");
  await page.keyboard.press("Control+Enter");

  await expect(page.getByText("把迁移合并头的处理写成脚本")).toBeVisible();
  await expect(page.getByRole("button", { name: "已记下 ✓" })).toBeVisible();
  await expect.poll(() => calls.some((c) => c.method === "POST" && c.path === "/intake/items")).toBe(true);
  expect((calls.find((c) => c.method === "POST")?.body as { text: string }).text).toBe(
    "把迁移合并头的处理写成脚本",
  );

  // 一次性反馈：2 秒后恢复，不留下常驻状态
  await expect(page.getByRole("button", { name: "记下来" })).toBeVisible({ timeout: 4000 });

  await page.getByRole("button", { name: "三问" }).click();
  await expect(page.getByText("做完之后，什么变了？")).toBeVisible();
  await expect(page.getByText(/下一次合并只需跑一条命令/).first()).toBeVisible();

  await page.getByRole("button", { name: /转成目标卡/ }).click();
  await expect
    .poll(() => calls.some((c) => c.method === "POST" && c.path === "/goals/"))
    .toBe(true);
});

test("收件箱：三问失败时文案指向配置项，而不是写死的验收标准", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/v1/intake/items/*/structure", (route) =>
    route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "upstream_error", message: "LiteLLM 未配置", request_id: "req-1" },
      }),
    }),
  );

  await page.goto("/inbox");
  await page.getByLabel("记一条").fill("没配置 LLM 时记一条");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText("没配置 LLM 时记一条")).toBeVisible();

  await page.getByRole("button", { name: "三问" }).click();
  await expect(page.getByText("AI 服务未配置或不可用，请检查 LITELLM_BASE_URL")).toBeVisible();
});

test("目标页：不依赖三问也能直接创建目标", async ({ page }) => {
  const { calls } = await mockApi(page);
  await page.goto("/goals");

  await page.getByRole("button", { name: "新建目标" }).click();
  await expect(page.getByRole("heading", { name: "新建目标" })).toBeVisible();

  await page.getByLabel("标题").fill("两周内跑通评审回归脚本");
  const submit = page.getByRole("button", { name: "创建目标" });
  await expect(submit).toBeDisabled();

  await page.getByLabel(/验收标准/).fill("scripts/review-regression.sh 在干净环境一次跑绿");
  await expect(submit).toBeEnabled();
  await submit.click();

  await expect
    .poll(() => calls.find((c) => c.method === "POST" && c.path === "/goals/")?.body)
    .toMatchObject({
      track: "growth",
      title: "两周内跑通评审回归脚本",
      acceptance: "scripts/review-regression.sh 在干净环境一次跑绿",
    });
  await expect(page.getByRole("heading", { name: "新建目标" })).toBeHidden();
});
