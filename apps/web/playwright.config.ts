import { defineConfig } from "@playwright/test";
const port = process.env.AIEDU_E2E_PORT ?? "3001";

// E2E 用本机已安装的 Edge（channel: msedge），不下载浏览器。
// 两类测试：
//   fixture   —— 经 page.route 注入“与后端同形状”的响应，验证前端行为（阅读器/暂停竞态/错误态）。
//   live-demo —— 打真实的隔离后端（演示模式，不调用模型），验证创建→快照→导出→下载链路。
// 二者都只证明前端行为，不是 AT/DEV-G 的专业通过证据。
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: process.env.AIEDU_E2E_OUTPUT_DIR ?? "test-results",
  reporter: [["list"], ["json", { outputFile: "e2e/results/results.json" }]],
  // 由 Playwright 管理前端生命周期：先构建再启动，避免手动起停导致的旧构建/端口占用。
  // 前端经同源代理访问隔离后端（API_BACKEND_URL，默认 127.0.0.1:8010）。
  webServer: {
    command: `pnpm run build && pnpm exec next start -p ${port} --hostname 127.0.0.1`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 300_000,
    env: { API_BACKEND_URL: process.env.API_BACKEND_URL ?? "http://127.0.0.1:8010", AIEDU_NEXT_DIST_DIR: process.env.AIEDU_NEXT_DIST_DIR ?? ".next" },
  },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: process.env.AIEDU_BROWSER_EXECUTABLE ? undefined : "msedge",
    launchOptions: process.env.AIEDU_BROWSER_EXECUTABLE ? { executablePath: process.env.AIEDU_BROWSER_EXECUTABLE } : undefined,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "w1440", use: { viewport: { width: 1440, height: 900 } } },
    { name: "w768", use: { viewport: { width: 768, height: 1024 } } },
    { name: "w390", use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } },
  ],
});
