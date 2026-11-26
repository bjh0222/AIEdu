import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";

// 方法标签：live-demo。打真实的隔离后端（演示模式，不调用模型、不付费），
// 验证 前端快照/工作台/导出/下载 与 会话/CSRF/幂等键 的真实链路。
// S10：新建任务页已移除演示入口（demo 只保留给 AI 测试/基准/内部验证），
// 因此内部验证改为通过同源 API 直接启动演示任务，再回到前端验证工作台。
// 前提：后端以 AIEDU_ALLOW_PAID=0 在 127.0.0.1:8010 运行，使用 scratch 库。
const PORT = process.env.AIEDU_E2E_PORT ?? "3001";
const ORIGIN = `http://127.0.0.1:${PORT}`;

async function pair(page: import("@playwright/test").Page) {
  const res = await page.request.post(`${ORIGIN}/api/v2/session`, { data: {} });
  expect(res.status()).toBe(200);
  const body = (await res.json()) as { csrf_token: string };
  return body.csrf_token;
}

function writeHeaders(csrf: string) {
  return { Origin: ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": crypto.randomUUID(),
    "Content-Type": "application/json" };
}

/** 内部验证专用：不经 UI 入口，直接以 demo 模式启动任务。 */
async function startDemoTask(page: import("@playwright/test").Page, raw: string, material?: { name: string; content: string }) {
  const csrf = await pair(page);
  const headers = writeHeaders(csrf);
  let revision = 1;
  let taskId: string;
  if (material) {
    const created = await page.request.post(`${ORIGIN}/api/v2/tasks`, { data: { raw_input: raw }, headers });
    expect(created.status()).toBe(201);
    const createdBody = (await created.json()) as { task_id: string; input_revision: number };
    taskId = createdBody.task_id;
    revision = createdBody.input_revision ?? 1;
    const uploaded = await page.request.post(`${ORIGIN}/api/v2/tasks/${taskId}/materials`, {
      headers: { ...headers, "Content-Type": "application/octet-stream", "X-Filename-UTF8": encodeURIComponent(material.name),
        "X-Expected-Revision": String(revision) },
      data: Buffer.from(material.content, "utf8"),
    });
    expect(uploaded.status()).toBe(201);
    const uploadedBody = (await uploaded.json()) as { source_id: string };
    const patched = await page.request.patch(`${ORIGIN}/api/v2/tasks/${taskId}/input`, {
      data: { expected_revision: revision, input: { raw_input: raw, material_ids: [uploadedBody.source_id] } }, headers });
    expect(patched.status()).toBe(200);
    const patchedBody = (await patched.json()) as { input_revision: number; context_hash: string; task_state: string };
    revision = patchedBody.input_revision ?? revision + 1;
    expect(patchedBody.task_state).not.toBe("awaiting_input");
    const confirmed = await page.request.post(`${ORIGIN}/api/v2/tasks/${taskId}/confirm`, {
      data: { expected_revision: revision, context_hash: patchedBody.context_hash }, headers });
    expect(confirmed.status()).toBe(200);
  } else {
    const res = await page.request.post(`${ORIGIN}/api/v2/task-starts`, { data: { raw_input: raw, mode: "demo" }, headers });
    expect(res.status()).toBe(202);
    const body = (await res.json()) as { task_id: string; run_id: string };
    return body;
  }
  const run = await page.request.post(`${ORIGIN}/api/v2/tasks/${taskId}/runs`, {
    data: { expected_revision: revision, confirmed_context_hash: (await (await page.request.get(
      `${ORIGIN}/api/v2/tasks/${taskId}/snapshot`)).json() as { context_hash?: string }).context_hash, mode: "demo" }, headers });
  expect(run.status()).toBe(202);
  const runBody = (await run.json()) as { run_id: string };
  return { task_id: taskId, run_id: runBody.run_id };
}

test.describe("真实后端（演示模式，内部验证）", () => {
  test.skip(({ isMobile }) => isMobile, "链路与视口无关，仅在桌面宽度跑一次");

  test("新建任务页没有演示入口，付费未开启时无法开始", async ({ page }) => {
    await page.goto("/new");
    await page.getByLabel("需求").fill("给我一份定积分入门教程，包含几道练习题 " + Date.now());
    await expect(page.getByRole("button", { name: /演示/ })).toHaveCount(0);   // S10：用户端无演示入口
    await expect(page.getByText(/无法开始：后端没有开启真实模型调用/)).toBeVisible();
    await expect(page.getByRole("button", { name: "开始生成" })).toBeDisabled();
  });

  test("内部验证：demo 任务 → 工作台 → 阅读 → 固定版本下载（字节与 hash 一致）", async ({ page }) => {
    const posts: string[] = [];
    page.on("request", (r) => r.method() === "POST" && posts.push(new URL(r.url()).pathname));

    const created = await startDemoTask(page, "给我一份定积分入门教程，包含几道练习题 " + Date.now());
    await page.goto(`/tasks/${created.task_id}`);
    await expect(page.getByText("演示", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("运行已结束")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("受阻：必需检查未完成").first()).toBeVisible();                   // 运行结束 ≠ 质量通过
    if ((page.viewportSize()?.width ?? 1440) < 1024) await page.getByRole("tab", { name: "用量" }).click(); // 窄屏用分段标签切换分区
    await expect(page.getByText("演示（不调用模型）").first()).toBeVisible();

    const taskStarts = posts.filter((p) => p.endsWith("/task-starts"));
    expect(taskStarts).toHaveLength(0);   // UI 不再提供演示入口；本次启动走内部 API 验证

    await page.reload();                                                                  // 刷新只读
    await expect(page.getByText("运行已结束")).toBeVisible();

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

  test("内部验证：中文材料上传、选择、生成与服务端版本目录", async ({ page }) => {
    const uploads: string[] = [];
    page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/materials")) uploads.push(decodeURIComponent(r.headers()["x-filename-utf8"] ?? "")); });
    const created = await startDemoTask(page, "只根据所附合成资料生成练习及答案，不计分",
      { name: "基础代数_合成资料.md", content: "# 合成资料\n一元方程 x+1=2。" });
    await page.goto(`/tasks/${created.task_id}`);
    await expect(page.getByText("运行已结束")).toBeVisible({ timeout: 30_000 });
    expect(uploads).toEqual(["基础代数_合成资料.md"]);
    await page.getByRole("link", { name: "阅读草稿" }).click();
    await expect(page.locator("#ver option").first()).toBeAttached();
    await expect(page.getByText(/版本目录暂不可用/)).toHaveCount(0);
    await expect(page.locator(".prose")).toBeVisible();
  });
});
