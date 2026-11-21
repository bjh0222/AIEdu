import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

test.skip(process.env.AIEDU_E2E_LIVE_ISOLATED !== "1", "requires owned 18103 demo instance, never default service");

test("真实隔离代理：中文上传→完成→固定合并草稿下载，重复下载不触发模型", async ({ page }, info) => {
  const submitted: string[] = [];
  page.on("request", (r) => { if (r.method() === "POST") submitted.push(new URL(r.url()).pathname); });
  await page.goto("/new");
  await expect(page.getByRole("button", { name: "真实生成" })).toBeDisabled();
  await page.getByLabel("需求").fill("根据合成资料生成入门知识教程及答案，不冒充真实模型输出");
  await page.locator("summary").click();
  await page.getByLabel("学习范围", { exact: true }).selectOption("system");
  await page.getByLabel("参考资料（可选）").setInputFiles({ name: "中文合成资料.md", mimeType: "text/markdown",
    buffer: Buffer.from("# 合成资料\n## 单元甲\n变量与常数。\n## 单元乙\n等式两边的运算。") });
  await page.getByRole("button", { name: "开始生成" }).click();
  await expect(page).toHaveURL(/\/tasks\/[0-9a-f-]{36}$/);
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
  expect(submitted.filter((p) => p.endsWith("/runs"))).toHaveLength(1);
});
