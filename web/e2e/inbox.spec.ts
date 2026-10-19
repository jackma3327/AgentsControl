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
  await expect.poll(() => calls.some((c) => c.method === "POST" && c.path === "/intake/items")).toBe(true);
  expect((calls.find((c) => c.method === "POST")?.body as { text: string }).text).toBe(
    "把迁移合并头的处理写成脚本",
  );

  await page.getByRole("button", { name: "三问" }).click();
  await expect(page.getByText("做完之后，什么变了？")).toBeVisible();
  await expect(page.getByText(/下一次合并只需跑一条命令/).first()).toBeVisible();

  await page.getByRole("button", { name: /转成目标卡/ }).click();
  await expect
    .poll(() => calls.some((c) => c.method === "POST" && c.path === "/goals/"))
    .toBe(true);
});
