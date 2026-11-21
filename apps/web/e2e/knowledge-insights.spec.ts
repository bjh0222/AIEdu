import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import type { UnderstandingPage } from "../lib/v2/contracts.generated";
import { mockBackend } from "./fixtures/build";

// Same UnderstandingAsset/content fields used by the research contract tests;
// fixture text is explicitly test data, never supplied as a product result.
const sampleQuote = "解方程 2x+3=7";
const sampleQuoteHash = createHash("sha256").update(sampleQuote, "utf8").digest("hex");
const sample = { source_id: "fixture-source", source_hash: sampleQuoteHash, quote: sampleQuote, interpretation: "两步逆运算的可观察步骤负担", start_byte: 0, end_byte: Buffer.byteLength(sampleQuote, "utf8"), quote_hash: sampleQuoteHash };
const fixture: UnderstandingPage = { items: [{ id: "fixture-profile", version: 2, hash: "c".repeat(64), calibration_status: "not_calibrated", aggregate_score: null, threshold: null, content: {
  profile_id: "fixture-profile", version: 2, title: "基础代数材料理解", material_refs: [{ source_id: sample.source_id, source_hash: sample.source_hash }],
  knowledge: { structure: ["等式性质与逆运算"], difficulty_features: ["需要协调两步变形"], quality_features: ["条件与解答一致"], representative_samples: [sample], limitations: ["未覆盖二次方程"] },
  proposition: { structure: ["从单步题到两步题"], difficulty_features: ["可观察步骤负担"], quality_features: ["题面与答案一致"], representative_samples: [sample], limitations: ["没有学习者实测数据"] },
  limitations: ["实际难度未知，未专业校准"],
} }], total: 1, inference_triggered: false };

test("AI整体理解、原文证据、材料身份时间、失效和刷新错误保留", async ({ page }, info) => {
  const calls = await mockBackend(page);
  await page.route("**/knowledge/assets/**", (r) => r.fulfill({ json: { items: [], total: 0 } }));
  await page.route("**/evaluation-frameworks", (r) => r.fulfill({ json: { items: [] } }));
  let mode = "current";
  await page.route("**/import-understanding", (r) => mode === "error" ? r.fulfill({ status: 503, json: { code: "SOURCE_UNAVAILABLE" } }) : r.fulfill({ json: fixture }));
  await page.route("**/managed-understanding-context/**", (r) => r.fulfill({ json: { profile_id: "fixture-profile", version: 2, hash: "c".repeat(64), status: mode === "stale" ? "stale" : "current", updated_at: "2026-11-16T10:20:30Z", inference_triggered: false,
    materials: [{ source_id: sample.source_id, filename: "基础代数测试资料.md", task_id: "fixture-task", source_hash: sample.source_hash, current_hash: sample.source_hash, status: mode === "stale" ? "trashed" : "current" }] } }));
  await page.goto("/library?tab=knowledge");
  const region = page.getByRole("region", { name: "AI 整体理解" });
  await expect(region).toContainText("基础代数材料理解（版本 2）");
  for (const text of ["等式性质与逆运算", "需要协调两步变形", "题面与答案一致", sampleQuote, `UTF-8 字节 0–${sample.end_byte}`, "没有学习者实测数据", "材料版本一致", "更新时间", "2026", "可修订的 AI 归纳"]) await expect(region).toContainText(text);
  await expect(region.getByRole("link", { name: "基础代数测试资料.md" }).first()).toHaveAttribute("href", "/tasks/fixture-task");
  await expect(region).not.toContainText(sample.source_hash); await expect(region).not.toContainText("fixture-profile");
  mode = "stale"; await region.getByRole("button", { name: "刷新理解" }).click();
  await expect(region).toContainText("旧理解需重新核验"); await expect(region).toContainText("在回收站");
  await page.screenshot({ path: info.outputPath("understanding-stale.png"), fullPage: true });
  mode = "error"; await region.getByRole("button", { name: "刷新理解" }).click();
  await expect(region.getByRole("alert")).toContainText("可能已过期"); await expect(region).toContainText("等式性质与逆运算");
  expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
  await info.attach("understanding-api-fixture.json", { body: JSON.stringify(fixture, null, 2), contentType: "application/json" });
});

test("理解空态、错误态不造结果，未知更新时间不造日期", async ({ page }) => {
  await mockBackend(page);
  let mode = "empty";
  await page.route("**/import-understanding", (r) => mode === "error" ? r.fulfill({ status: 503, json: { code: "NOT_AVAILABLE" } }) : r.fulfill({ json: mode === "empty" ? { items: [], total: 0, inference_triggered: false } : fixture }));
  await page.route("**/managed-understanding-context/**", (r) => r.fulfill({ json: { profile_id: "fixture-profile", version: 2, hash: "c".repeat(64), status: "unknown", updated_at: null, materials: [], inference_triggered: false } }));
  await page.goto("/library?tab=knowledge");
  const region = page.getByRole("region", { name: "AI 整体理解" });
  await expect(region).toContainText("尚无已生成的整体理解"); await expect(region.locator("article")).toHaveCount(0);
  mode = "error"; await region.getByRole("button", { name: "刷新理解" }).click(); await expect(region.getByRole("alert")).toBeVisible(); await expect(region.locator("article")).toHaveCount(0);
  mode = "unknown"; await region.getByRole("button", { name: "刷新理解" }).click();
  await expect(region).toContainText("材料版本状态未知"); await expect(region).toContainText("更新时间：未知");
});

test("实际导入题面字段与原生题号保留，提供直接回收站操作", async ({ page }) => {
  await mockBackend(page);
  let hidden = false;
  await page.route("**/import-understanding", (r) => r.fulfill({ json: { items: [], total: 0 } }));
  await page.route("**/knowledge/assets/**", (r) => {
    const items = r.request().url().includes("questions") && !hidden ? [{ id: "fixture-question", raw_stem: sampleQuote, question_number: "第3题", parse_status: "ready" }] : [];
    return r.fulfill({ json: { items, total: items.length } });
  });
  await page.route("**/managed-resources/questions/*/impact", (r) => r.fulfill({ json: { resource: { kind: "questions", id: "fixture-question", label: "第3题 解方程 2x+3=7", resource_hash: "h", management: { archived: false, trashed: false, revision: 0 } }, references: [], active: false, impact_hash: "i", purge_allowed: true, purge_blockers: [] } }));
  await page.route("**/managed-resource-actions", (r) => { expect(r.request().postDataJSON().items[0].kind).toBe("questions"); hidden = true; return r.fulfill({ json: { operation: "trash", items: [], receipt_hash: "r" } }); });
  await page.goto("/library?tab=knowledge");
  const trashQuestion = page.getByRole("button", { name: "将第3题 解方程 2x+3=7移入回收站" });
  await expect(page.getByRole("listitem").filter({ has: trashQuestion })).toContainText("第3题 解方程 2x+3=7");
  await trashQuestion.click();
  const dialog = page.getByRole("dialog"); await dialog.getByLabel("我已查看引用和影响").check(); await dialog.getByRole("button", { name: "确认移入回收站" }).click();
  await expect(page.getByRole("button", { name: "将第3题 解方程 2x+3=7移入回收站" })).toHaveCount(0);
});
