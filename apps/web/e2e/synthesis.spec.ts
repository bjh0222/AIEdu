import { expect, test, type Page } from "@playwright/test";
import { IDS, mockBackend, runSnapshot, versionRead } from "./fixtures/build";
import type { SynthesisResult } from "../lib/v2/contracts.generated";

// Fixture-only UI evidence; no real model, professional calibration or 3001 acceptance.
const body = "# 实验与论证\n\n对照实验可支持因果解释，但仍需区分证据与结论。";
const fixed = versionRead(IDS.v2, 2, body, []);
const missing = { task_id: IDS.task, version_id: IDS.v2, content_hash: fixed.content_hash, status: "not_generated", revision: 0, source_is_current: true };
const accepted = { task_id: IDS.task, run_id: "synthesis-run", parent_run_id: IDS.run, version_id: IDS.v2, source_bundle_hash: "a".repeat(64), status: "queued", paid: true };
const preview = { task_id: IDS.task, run_id: IDS.run, state_revision: 5, version_id: IDS.v2,
  content_hash: fixed.content_hash, quality_hash: fixed.quality_hash, source_bundle_hash: "a".repeat(64),
  expected_synthesis_revision: 0, coverage: {}, status: "source_ready_not_executed" };
function candidate(): SynthesisResult {
  return { task_id: IDS.task, run_id: "synthesis-run", revision: 2, method_version: "fixture",
    source_version_id: IDS.v2, source_content_hash: fixed.content_hash, source_quality_hash: fixed.quality_hash,
    source_bundle_hash: "a".repeat(64), source_plan_hash: "b".repeat(64), result_hash: "c".repeat(64),
    source_index: { units: [{ unit_id: IDS.unit, title: "实验推理", content_hash: fixed.content.units[0].content_hash }],
      spans: [{ source_span_id: "span", unit_id: IDS.unit, unit_hash: fixed.content.units[0].content_hash, start_byte: 0, end_byte: 8, quote_hash: "d".repeat(64) }] },
    source_is_current: true, source_demo: false, demo: false, provenance: "fixture", check_plan: {},
    rounds: [{ round: 1, proposal: { proposal: { source_bundle_hash: "a".repeat(64), title: "从实验到解释",
      blocks: [{ block_id: "block", heading: "证据与适用条件", body: "实验中的变量控制与历史论证中的来源批判都要求交代解释边界，但不是同一种验证方法。", source_span_ids: ["span"] }],
      relations: [{ source_unit_id: IDS.unit, target_unit_id: IDS.unit, kind: "contrast", explanation: "科学对照与人文来源解释不能互相代签。", source_span_ids: ["span"] }],
      unintegrated_units: [], limitations: ["未测量学习迁移"] }, proposal_hash: "p", coverage: {} },
      research: {}, review: { review: { source_bundle_hash: "a".repeat(64), proposal_hash: "p", checks: [
        { check_id: "relation", result: "violated", reason: "应进一步明确论证的适用边界", block_ids: ["block"], source_span_ids: ["span"], evidence_gap: null, applicability_reason: null }],
        optimization_prompt: "保留来源定位，补充科学和历史方法各自的适用条件，然后重新核查。", repair_block_ids: ["block"], preserve_block_ids: [], recheck_conditions: ["适用条件有来源定位"] }, review_hash: "r", automatic_status: "needs_repair" },
      generation_audit: {}, review_audit: {}, automatic_status: "needs_repair" }],
    latest_proposal_hash: "p", automatic_status: "needs_repair", stop_reason: "fixture", status: "candidate",
    calibration_status: "not_calibrated", professional_certification: "not_established", source_quality_promotion: false, final_delivery_eligible: false };
}

async function setup(page: Page, result: unknown = missing) {
  const calls = await mockBackend(page, { snapshot: () => runSnapshot({ demo: false, quality: fixed.quality }), versions: { [IDS.v2]: fixed } });
  await page.route("**/api/v2/**/synthesis", (route) => route.fulfill({ json: result }));
  await page.route("**/api/v2/**/synthesis-source?*", (route) => route.fulfill({ json: preview }));
  await page.goto(`/tasks/${IDS.task}/read`);
  await page.getByText("探索知识体系与方法卡", { exact: true }).click();
  return { panel: page.getByRole("region", { name: "跨章知识与方法整合" }), calls };
}

test("只读展示跨学科整合、来源、限制与完整C整改提示，不自动付费", async ({ page }) => {
  const { panel, calls } = await setup(page, candidate());
  await expect(panel.getByText("从实验到解释", { exact: true })).toBeVisible();
  await expect(panel).toContainText("不是最终交付");
  await expect(panel).toContainText("未测量学习迁移");
  await panel.getByText("跨章关系与依据", { exact: true }).click();
  await expect(panel).toContainText("科学对照与人文来源解释不能互相代签");
  await panel.getByText("检查证据与整改记录（1 轮）", { exact: true }).click();
  await expect(panel).toContainText("保留来源定位，补充科学和历史方法各自的适用条件，然后重新核查。");
  await panel.getByRole("button", { name: "查看来源：实验推理" }).first().click();
  expect(calls.every((c) => c.method === "GET")).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
});

test("显式确认后才携带源版本、双hash与CAS创建整合", async ({ page }) => {
  const { panel } = await setup(page);
  await page.route("**/api/v2/tasks/*/snapshot?run_id=synthesis-run", (route) => route.fulfill({ json: synthesisSnapshot() }));
  const writes: { body: unknown; headers: Record<string, string> }[] = [];
  await page.route("**/api/v2/**/syntheses", async (route) => {
    writes.push({ body: route.request().postDataJSON(), headers: route.request().headers() });
    await route.fulfill({ status: 202, json: accepted });
  });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("整合运行：运行中");
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toEqual({ run_id: IDS.run, expected_run_revision: 5, version_id: IDS.v2,
    content_hash: fixed.content_hash, quality_hash: fixed.quality_hash, source_bundle_hash: "a".repeat(64), expected_synthesis_revision: 0 });
  expect(writes[0].headers["idempotency-key"]).toBeTruthy();
  expect(writes[0].headers["x-csrf-token"]).toBe("test-csrf");
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
});

test("已接受的整合在页面重载后按当前来源恢复跟踪，不重复付费", async ({ page }) => {
  const { panel } = await setup(page);
  const snapshots: string[] = [];
  let writes = 0;
  await page.route("**/api/v2/tasks/*/snapshot**", async (route) => {
    const url = route.request().url();
    snapshots.push(url);
    await route.fulfill({ json: runSnapshot({ demo: false, quality: fixed.quality,
      ...(url.includes("run_id=synthesis-run") ? { run_id: accepted.run_id, state: "running", stop_reason: null } : {}) }) });
  });
  await page.route("**/api/v2/**/syntheses", (route) => { writes++; return route.fulfill({ status: 202, json: accepted }); });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("整合运行：运行中");
  await page.reload();
  await page.getByText("探索知识体系与方法卡", { exact: true }).click();
  await expect(page.getByRole("region", { name: "跨章知识与方法整合" })).toContainText("整合运行");
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
  expect(writes).toBe(1);
  expect(snapshots.some((url) => url.includes("run_id=synthesis-run"))).toBe(true);
});

test("取消付费确认不写入", async ({ page }) => {
  const { panel, calls } = await setup(page);
  page.once("dialog", (dialog) => dialog.dismiss());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeEnabled();
  expect(calls.every((c) => c.method === "GET")).toBe(true);
});

test("响应未知不重发，重载后仍只查原回执", async ({ page }) => {
  const { panel } = await setup(page);
  const keys: string[] = [];
  await page.route("**/api/v2/**/syntheses", (route) => { keys.push(route.request().headers()["idempotency-key"]); return route.abort("failed"); });
  await page.route("**/api/v2/actions/receipt", (route) => {
    expect(route.request().headers()["idempotency-key"]).toBe(keys[0]);
    return route.fulfill({ json: { status: "not_found" } });
  });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("请求结果未知");
  await page.reload();
  await page.getByText("探索知识体系与方法卡", { exact: true }).click();
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
  await panel.getByRole("button", { name: "查询整合回执" }).click();
  await expect(panel).toContainText("尚未找到回执");
  expect(keys).toHaveLength(1);
});

test("错版本整合不展示、不允许再生成", async ({ page }) => {
  const wrong = { ...candidate(), source_quality_hash: "0".repeat(64) };
  const { panel } = await setup(page, wrong);
  await expect(panel).toContainText("整合与正文或质量版本不匹配");
  await expect(panel.getByText("从实验到解释", { exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
});

test("合并导出明确选择整合revision/hash，候选只能草稿且不调用模型", async ({ page }) => {
  const { calls } = await setup(page, candidate());
  const exports: Record<string, unknown>[] = [];
  await page.route("**/api/v2/artifacts/*/export-sets", (route) => {
    exports.push(route.request().postDataJSON());
    return route.fulfill({ json: { bundle_id: "bundle", version_id: IDS.v2, delivery_status: "draft", demo: false,
      profile_binding: { status: "current" }, missing_required: [], quality: fixed.quality, files: [] } });
  });
  await page.getByRole("button", { name: "下载 v2", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("checkbox", { name: "将此版本的知识与方法整合并入同一文件（未校准，仅草稿）" }).check();
  await dialog.getByRole("button", { name: "下载已有草稿", exact: true }).click();
  await expect(dialog).toContainText("草稿交付");
  expect(exports).toEqual([{ expected_revision: 5, version_id: IDS.v2, kind: "available_draft",
    options: { synthesis: { revision: 2, result_hash: "c".repeat(64) } } }]);
  expect(calls.filter((c) => c.method !== "GET").every((c) => c.url === "/session")).toBe(true);
});

async function trackRun(page: Page, snapshot: () => unknown) {
  await page.route("**/api/v2/tasks/*/snapshot**", (route) => route.fulfill({ json:
    new URL(route.request().url()).searchParams.get("run_id") === accepted.run_id
      ? snapshot() : runSnapshot({ demo: false, quality: fixed.quality }) }));
  let writes = 0;
  await page.route("**/api/v2/**/syntheses", (route) => {
    writes++;
    return route.fulfill({ status: 202, json: accepted });
  });
  return () => writes;
}

const synthesisSnapshot = (over: Record<string, unknown> = {}) => runSnapshot({
  run_id: accepted.run_id, demo: false, state: "running", stop_reason: null, quality: fixed.quality, ...over,
});

test("只读跟踪：接受后自动读取终态和候选并停止，不再次付费", async ({ page }) => {
  const { panel } = await setup(page);
  let reads = 0;
  const writes = await trackRun(page, () => synthesisSnapshot({ state: ++reads > 1 ? "completed" : "running" }));
  await page.route("**/api/v2/**/synthesis", (route) => route.fulfill({ json: reads > 1 ? candidate() : missing }));
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("整合运行：");
  await expect(panel.getByText("从实验到解释", { exact: true })).toBeVisible();
  await page.clock.install();
  await page.clock.fastForward(20_000);
  expect(reads).toBe(2);
  expect(writes()).toBe(1);
  await expect(panel).toContainText("不是最终交付");
});

test("只读跟踪：读取失败暂停，显式刷新恢复且不重发", async ({ page }) => {
  const { panel } = await setup(page);
  let reads = 0;
  const writes = await trackRun(page, () => { reads++; return synthesisSnapshot({ state: "completed" }); });
  let broken = true;
  await page.route("**/api/v2/tasks/*/snapshot**", (route) => {
    if (!route.request().url().includes("run_id=synthesis-run")) return route.fallback();
    if (broken) { reads++; return route.abort("failed"); }
    return route.fallback();
  });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("自动跟踪已暂停");
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
  await page.clock.install();
  await page.clock.fastForward(20_000);
  expect(reads).toBe(1);
  broken = false;
  await panel.getByRole("button", { name: "刷新整合结果" }).click();
  await page.clock.runFor(100);
  await expect(panel).not.toContainText("自动跟踪已暂停");
  expect(reads).toBe(2);
  expect(writes()).toBe(1);
});

test("只读跟踪：错run不接受，恢复读取后显示实际失败原因", async ({ page }) => {
  const { panel } = await setup(page);
  let correct = false;
  let reads = 0;
  const writes = await trackRun(page, () => { reads++; return synthesisSnapshot(correct
    ? { state: "failed", stop_reason: "SYNTHESIS_SOURCE_CHANGED" } : { run_id: IDS.run }); });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("运行身份不匹配");
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
  correct = true;
  await panel.getByRole("button", { name: "刷新整合结果" }).click();
  await expect(panel).toContainText("SYNTHESIS_SOURCE_CHANGED");
  await page.clock.install();
  await page.clock.fastForward(20_000);
  expect(reads).toBe(2);
  expect(writes()).toBe(1);
});

test("只读跟踪：自动跟踪窗耗尽不宣称运行超时，不继续请求", async ({ page }) => {
  const { panel } = await setup(page);
  let reads = 0;
  const writes = await trackRun(page, () => { reads++; return synthesisSnapshot(); });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("整合运行：");
  await page.clock.install();
  await page.clock.fastForward(30 * 60_000 + 1);
  await expect(panel).toContainText("自动跟踪时限");
  await expect(panel).toContainText("不表示运行超时");
  await page.clock.fastForward(20_000);
  expect(reads).toBe(1);
  expect(writes()).toBe(1);
});

for (const over of [{ task_id: "different-task" }, { state: "unrecognized" }]) {
  test(`只读跟踪：无效身份或未知状态暂停 ${JSON.stringify(over)}`, async ({ page }) => {
    const { panel } = await setup(page);
    let reads = 0;
    const writes = await trackRun(page, () => { reads++; return synthesisSnapshot(over); });
    page.once("dialog", (dialog) => dialog.accept());
    await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
    await expect(panel).toContainText("自动跟踪已暂停");
    await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
    await page.clock.install();
    await page.clock.fastForward(20_000);
    expect(reads).toBe(1);
    expect(writes()).toBe(1);
  });
}

test("只读跟踪：终态结果读取失败不能冒充完成，刷新后取得结果", async ({ page }) => {
  const { panel } = await setup(page);
  const writes = await trackRun(page, () => synthesisSnapshot({ state: "completed" }));
  let broken = true;
  await page.route("**/api/v2/**/synthesis", (route) => broken
    ? route.abort("failed") : route.fulfill({ json: candidate() }));
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("自动跟踪已暂停");
  await expect(panel).not.toContainText("整合运行：运行已结束");
  broken = false;
  await panel.getByRole("button", { name: "刷新整合结果" }).click();
  await expect(panel.getByText("从实验到解释", { exact: true })).toBeVisible();
  await expect(panel).toContainText("整合运行：运行已结束");
  expect(writes()).toBe(1);
});

test("只读跟踪：隐藏或卸载不继续读取，重新可见后恢复", async ({ page }) => {
  const { panel } = await setup(page);
  let reads = 0;
  const writes = await trackRun(page, () => { reads++; return synthesisSnapshot(); });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("整合运行：运行中");
  await page.clock.install();
  await page.evaluate(() => Object.defineProperty(document, "hidden", { configurable: true, get: () => true }));
  await page.clock.fastForward(20_000);
  expect(reads).toBe(1);
  await page.evaluate(() => Object.defineProperty(document, "hidden", { configurable: true, get: () => false }));
  await page.clock.runFor(5_100);
  await expect.poll(() => reads).toBe(2);
  await page.getByText("探索知识体系与方法卡", { exact: true }).click();
  await expect(panel).toHaveCount(0);
  await page.clock.fastForward(20_000);
  expect(reads).toBe(2);
  expect(writes()).toBe(1);
});

test("只读跟踪：刷新取消旧读取，迟返回不能覆盖新候选", async ({ page }) => {
  const { panel } = await setup(page);
  const writes = await trackRun(page, () => synthesisSnapshot());
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("整合运行：运行中");
  let reads = 0;
  let release!: () => void;
  const blocked = new Promise<void>((resolve) => { release = resolve; });
  const latest = candidate();
  latest.rounds[0].proposal.proposal.title = "刷新后的候选";
  await page.route("**/api/v2/**/synthesis", async (route) => {
    if (++reads === 1) {
      await blocked;
      // The original browser fetch was explicitly aborted by refresh.
      await route.fulfill({ json: candidate() }).catch(() => {});
    } else await route.fulfill({ json: latest });
  });
  await panel.getByRole("button", { name: "刷新整合结果" }).click();
  await expect.poll(() => reads).toBe(1);
  await panel.getByRole("button", { name: "刷新整合结果" }).click();
  await expect(panel.getByText("刷新后的候选", { exact: true })).toBeVisible();
  release();
  await page.clock.install();
  await page.clock.runFor(100);
  await expect(panel.getByText("从实验到解释", { exact: true })).toHaveCount(0);
  expect(reads).toBe(2);
  expect(writes()).toBe(1);
});

test("只读跟踪：未知动作取得原成功回执后跟踪，不重新提交", async ({ page }) => {
  const { panel } = await setup(page);
  let writes = 0;
  await trackRun(page, () => synthesisSnapshot({ state: "completed" }));
  await page.route("**/api/v2/**/syntheses", (route) => { writes++; return route.abort("failed"); });
  await page.route("**/api/v2/actions/receipt", (route) => route.fulfill({ json: { status: "completed", response: accepted } }));
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("请求结果未知");
  await page.route("**/api/v2/**/synthesis", (route) => route.fulfill({ json: candidate() }));
  await panel.getByRole("button", { name: "查询整合回执" }).click();
  await expect(panel.getByText("从实验到解释", { exact: true })).toBeVisible();
  await expect(panel).not.toContainText("请求结果未知");
  expect(writes).toBe(1);
});

test("只读跟踪：卡住的页面查询有界停止，不冒充模型超时或自动重试", async ({ page }) => {
  const { panel } = await setup(page);
  let reads = 0;
  const writes = await trackRun(page, () => synthesisSnapshot());
  await page.route("**/api/v2/tasks/*/snapshot?run_id=synthesis-run", () => { reads++; });
  page.once("dialog", (dialog) => dialog.accept());
  await panel.getByRole("button", { name: "生成或更新整合（付费）" }).click();
  await expect(panel).toContainText("页面只读查询超过15秒等待上限", { timeout: 20_000 });
  await expect(panel).toContainText("不表示模型调用超时");
  await expect(panel.getByRole("button", { name: "生成或更新整合（付费）" })).toBeDisabled();
  await page.clock.install();
  await page.clock.fastForward(20_000);
  expect(reads).toBe(1);
  expect(writes()).toBe(1);
});
