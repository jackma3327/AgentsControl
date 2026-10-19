import { defineConfig, devices } from "@playwright/test";

/**
 * E2E 冒烟：不启动后端，全部 API 用 route mock（plan/W10.md I07）。
 *
 * 为什么用 `next start` 而不是 dev：
 * dev 首次访问要按需编译 10 个页面，CI 里经常超过 3 分钟；build 一次后 start 更稳定。
 */

const PORT = 3107;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npx next start -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}/today`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
