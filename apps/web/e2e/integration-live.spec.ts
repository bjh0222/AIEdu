import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// S10：新建任务页已移除演示入口（demo 只保留给 AI 测试/基准/内部验证）。
// 真实隔离代理链路改为经同源 API 直接启动 demo 任务，再回前端验证上传→完成→下载。
test.skip(process.env.AIEDU_E2E_LIVE_ISOLATED !== "1", "requires owned 18103 demo instance, never default service");

const PORT = process.env.AIEDU_E2E_PORT ?? "3001";
const ORIGIN = `http://127.0.0.1:${PORT}`;

async function startDemoTaskWithMaterial(page: import("@playwright/test").Page, raw: string, material: { name: string; content: string }) {
  const session = await page.request.post(`${ORIGIN}/api/v2/session`, { data: {} });
  expect(session.status()).toBe(200);
  const { csrf_token } = (await session.json()) as { csrf_token: string };
  const headers = { Origin: ORIGIN, "X-CSRF-Token": csrf_token, "Idempotency-Key": crypto.randomUUID(),
    "Content-Type": "application/json" };
  const created = await page.request.post(`${ORIGIN}/api/v2/tasks`, { data: { raw_input: raw }, headers });
  expect(created.status()).toBe(201);
  const createdBody = (await created.json()) as { task_id: string; input_revision: number };
  const revision = createdBody.input_revision ?? 1;
  const uploaded = await page.request.post(`${ORIGIN}/api/v2/tasks/${createdBody.task_id}/materials`, {
    headers: { ...headers, "Content-Type": "application/octet-stream", "X-Filename-UTF8": encodeURIComponent(material.name),
      "X-Expected-Revision": String(revision) },
    data: Buffer.from(material.content, "utf8"),
  });
  expect(uploaded.status()).toBe(201);
  const uploadedBody = (await uploaded.json()) as { source_id: string };
  const patched = await page.request.patch(`${ORIGIN}/api/v2/tasks/${createdBody.task_id}/input`, {
    data: { expected_revision: revision, input: { raw_input: raw, material_ids: [uploadedBody.source_id] } }, headers });
  expect(patched.status()).toBe(200);
  const patchedBody = (await patched.json()) as { input_revision: number; context_hash: string; task_state: string };
  expect(patchedBody.task_state).not.toBe("awaiting_input");
  const confirmed = await page.request.post(`${ORIGIN}/api/v2/tasks/${createdBody.task_id}/confirm`, {
    data: { expected_revision: patchedBody.input_revision ?? revision + 1, context_hash: patchedBody.context_hash }, headers });
  expect(confirmed.status()).toBe(200);
  const run = await page.request.post(`${ORIGIN}/api/v2/tasks/${createdBody.task_id}/runs`, {
    data: { expected_revision: patchedBody.input_revision ?? revision + 1, confirmed_context_hash: patchedBody.context_hash, mode: "demo" }, headers });
  expect(run.status()).toBe(202);
  return createdBody.task_id;
}

test("真实隔离代理：中文上传→完成→固定合并草稿下载，重复下载不触发模型", async ({ page }, info) => {
  const submitted: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST") submitted.push(new URL(r.url()).pathname); });
  await page.goto("/new");
  await expect(page.getByRole("button", { name: /演示/ })).toHaveCount(0);   // S10：用户端无演示入口
  const taskId = await startDemoTaskWithMaterial(page, "根据合成资料生成入门知识教程及答案，不冒充真实模型输出",
    { name: "中文合成资料.md", content: "# 合成资料\n## 单元甲\n变量与常数。\n## 单元乙\n等式两边的运算。" });
  await page.goto(`/tasks/${taskId}`);
  await expect(page.getByRole("group", { name: "任务状态（运行、质量、交付三个独立维度）" })
    .getByText("运行已结束", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("演示", { exact: true }).first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("live-workbench.png"), fullPage: true });
  await page.getByRole("link", { name: "阅读草稿" }).click();
  await expect(page.locator(".prose")).toBeVisible();
  await page.getByRole("button", { name: /^下载 v/ }).click();
  const dialog = page.getByRole("dialog");
  const generate = dialog.getByRole("button", { name: "生成下载包" });
  await generate.click();
  await expect(dialog).toContainText("不能作为最终交付");
  await dialog.getByRole("button", { name: "下载已有草稿" }).click();
  const downloadButton = dialog.getByRole("button", { name: "下载", exact: true }).first();
  await expect(downloadButton).toBeVisible();
  const [first] = await Promise.all([page.waitForEvent("download"), downloadButton.click()]);
  const bytes = readFileSync((await first.path())!);
  expect(first.suggestedFilename()).toMatch(/^draft-/);
  const [again] = await Promise.all([page.waitForEvent("download"), downloadButton.click()]);
  expect(createHash("sha256").update(readFileSync((await again.path())!)).digest("hex"))
    .toBe(createHash("sha256").update(bytes).digest("hex"));
  expect(submitted.filter((p) => p.endsWith("/runs"))).toHaveLength(0); // UI 不再提供演示启动入口
});
