import { expect, test, type Page } from "@playwright/test";

import { mockApi } from "./helpers";

/** 相对亮度 + 对比度，按 WCAG 2.1 计算（阈值 4.5:1）。 */
async function textContrast(page: Page): Promise<number> {
  return page.evaluate(() => {
    const el = document.querySelector("main p") ?? document.body;
    const fg = getComputedStyle(el).color;
    const bg = getComputedStyle(document.body).backgroundColor;
    const parse = (value: string): [number, number, number] => {
      const [r = 0, g = 0, b = 0] = (value.match(/[0-9.]+/g) ?? []).slice(0, 3).map(Number);
      return [r, g, b];
    };
    const luminance = (rgb: [number, number, number]): number => {
      const channel = (c: number) => {
        const v = c / 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
    };
    const l1 = luminance(parse(fg));
    const l2 = luminance(parse(bg));
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  });
}

const PAGES = ["/today", "/inbox", "/goals", "/execution", "/arsenal", "/assets", "/ledger"];

for (const path of PAGES) {
  test(`可访问性与控制台：${path}`, async ({ page }) => {
    await mockApi(page);
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && !msg.text().includes("favicon")) {
        errors.push(msg.text());
      }
    });
    page.on("pageerror", (err) => errors.push(err.message));

    await page.goto(path);
    await expect(page.locator("main")).toBeVisible();

    expect(errors, `控制台出现错误：${errors.join(" | ")}`).toEqual([]);

    const ratio = await textContrast(page);
    expect(ratio, `${path} 正文对比度 ${ratio.toFixed(2)} 低于 4.5`).toBeGreaterThanOrEqual(4.5);
  });
}
