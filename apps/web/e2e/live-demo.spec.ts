import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";

// 方法标签：live-demo。打真实的隔离后端（演示模式，不调用模型、不付费），
// 验证前端 → 同源代理 → 会话/CSRF/幂等键 → 快照 → 导出 → 下载 的真实链路。
// 前提：后端以 AIEDU_ALLOW_PAID=0 在 127.0.0.1:8010 运行，使用 scratch 库。
test.describe("真实后端（演示模式）", () => {
  test.skip(({ isMobile }) => isMobile, "链路与视口无关，仅在桌面宽度跑一次");

  test("创建 → 工作台 → 阅读 → 固定版本下载（字节与 hash 一致）", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (r) => r.method() === "POST" && posts.push(new URL(r.url()).pathname));

    await page.goto("/new");
    await page.getByLabel("需求").fill("给我一份定积分入门教程，包含几道练习题 " + Date.now());
    await expect(page.getByRole("button", { name: "真实生成" })).toBeDisabled();         // 后端未允许付费 → 不能静默选真实
    await expect(page.getByRole("button", { name: /演示/ })).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: "开始生成" }).click();

    await page.waitForURL(/\/tasks\/[0-9a-f-]{36}$/);
    await expect(page.getByText("演示", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("运行已结束")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("受阻：必需检查未完成").first()).toBeVisible();                   // 运行结束 ≠ 质量通过
    if ((page.viewportSize()?.width ?? 1440) < 1024) await page.getByRole("tab", { name: "用量" }).click(); // 窄屏用分段标签切换分区
    await expect(page.getByText("演示（不调用模型）").first()).toBeVisible();

    const taskStarts = posts.filter((p) => p.endsWith("/task-starts"));
    expect(taskStarts).toHaveLength(1);                                                   // 一次启动，不是三次点击

    await page.reload();                                                                  // 刷新只读
    await expect(page.getByText("运行已结束")).toBeVisible();
    expect(posts.filter((p) => p.endsWith("/task-starts"))).toHaveLength(1);

    await page.getByRole("link", { name: "阅读草稿" }).click();
    await expect(page.locator(".prose h1").first()).toBeVisible();
    await expect(page.locator(".answer-toggle")).toHaveAttribute("aria-expanded", "false");

    await page.getByRole("button", { name: /^下载 v/ }).click();
    await page.getByRole("button", { name: "生成下载包" }).click();
    await expect(page.getByText("下载包已生成")).toBeVisible();
    await expect(page.getByRole("dialog").getByText("演示")).toBeVisible();

    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("dialog").getByRole("button", { name: "下载" }).first().click()]);
    const path = await download.path();
    const bytes = (await import("node:fs")).readFileSync(path!);
    expect(bytes.length).toBeGreaterThan(0);
    // 同一文件再次下载，字节必须完全一致（固定版本，零模型调用）。
    const [again] = await Promise.all([page.waitForEvent("download"), page.getByRole("dialog").getByRole("button", { name: "下载" }).first().click()]);
    const bytes2 = (await import("node:fs")).readFileSync((await again.path())!);
    expect(createHash("sha256").update(bytes2).digest("hex")).toBe(createHash("sha256").update(bytes).digest("hex"));
  });

  test("任务出现在列表中；列表请求不逐行读取 snapshot", async ({ page }) => {
    const snapshots: string[] = [];
    page.on("request", (r) => r.url().includes("/snapshot") && snapshots.push(r.url()));
    await page.goto("/");
    await expect(page.locator("table.data tbody tr").first()).toBeVisible();
    expect(snapshots).toHaveLength(0);
  });

  test("中文材料上传、选择、生成与服务端版本目录", async ({ page }) => {
    const uploads: string[] = [];
    page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/materials")) uploads.push(r.headers()["x-filename-utf8"]); });
    await page.goto("/new");
    await page.getByLabel("需求").fill("只根据所附合成资料生成练习及答案，不计分");
    await page.locator("summary").click();
    await expect(page.getByLabel("参考资料（可选）")).toBeEnabled();
    await page.getByLabel("参考资料（可选）").setInputFiles({ name: "基础代数_合成资料.md", mimeType: "text/markdown", buffer: Buffer.from("# 合成资料\n一元方程 x+1=2。", "utf8") });
    await page.getByRole("button", { name: "开始生成" }).click();
    await page.waitForURL(/\/tasks\/[0-9a-f-]{36}$/);
    await expect(page.getByText("运行已结束")).toBeVisible({ timeout: 30_000 });
    expect(uploads).toEqual([encodeURIComponent("基础代数_合成资料.md")]);
    await page.getByRole("link", { name: "阅读草稿" }).click();
    await expect(page.locator("#ver option").first()).toBeAttached();
    await expect(page.getByText(/版本目录暂不可用/)).toHaveCount(0);
    await expect(page.locator(".prose")).toBeVisible();
  });
});
