import { expect, test } from "@playwright/test";
import { IDS, mockBackend } from "./fixtures/build";

test("上传入口保留原话、范围、研究和有界优化策略，不另建生成任务", async ({ page }, info) => {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let uploaded: Buffer = Buffer.alloc(0);
  await mockBackend(page, { onWrite: async (route, path) => {
    if (path.endsWith("/materials")) {
      uploaded = route.request().postDataBuffer() ?? Buffer.alloc(0);
      await route.fulfill({ json: { source_id: "selected-source" } });
      return true;
    }
    const body = route.request().postDataJSON() as Record<string, unknown>;
    writes.push({ path, body });
    if (path === "/tasks") await route.fulfill({ json: { task_id: IDS.task, input_revision: 1, context_hash: "h1", conflicts: [] } });
    else if (path.endsWith("/input")) await route.fulfill({ json: { task_id: IDS.task, input_revision: 2, context_hash: "h2", task_state: "draft" } });
    else if (path.endsWith("/confirm")) await route.fulfill({ json: { task_id: IDS.task, input_revision: 2, context_hash: "h2", task_state: "confirmed" } });
    else if (path.endsWith("/runs")) await route.fulfill({ json: { run_id: IDS.run } });
    else return false;
    return true;
  } });
  await page.route("**/api/v2/capabilities", (r) => r.fulfill({ json: {
    scenes: ["exercise"], taxonomy: [], paid_calls_enabled: true, materials: { resource_policy_status: "ready" },
  } }));
  await page.goto("/new");
  const raw = "  根据附件，完整生成大模块练习；不要改写原文。\n保留尾部空格  ";
  const material = "# 合成材料\n原文😀保留。";
  await page.getByLabel("需求").fill(raw);
  await page.locator("summary").click();
  await page.getByLabel("学习范围", { exact: true }).selectOption("system");
  await page.getByLabel("参考资料（可选）").setInputFiles({ name: "资料.md", mimeType: "text/markdown", buffer: Buffer.from(material) });
  await page.getByLabel("整条任务模型调用上限").fill("24");
  await expect(page.getByLabel("启用 Criterion 评估")).toBeChecked();
  await expect(page.getByLabel("联网查阅教育文献，建立资料理解与逐维量规")).toBeChecked();
  await page.screenshot({ path: info.outputPath("new-task-policy.png"), fullPage: true });
  await page.getByRole("button", { name: "开始生成" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${IDS.task}$`));
  expect(uploaded.toString()).toBe(material);
  expect(writes.filter((w) => w.path === "/tasks")).toHaveLength(1);
  expect(writes.some((w) => w.path === "/task-starts")).toBe(false);
  const input = writes.find((w) => w.path.endsWith("/input"))!.body.input as Record<string, unknown>;
  expect(input).toMatchObject({ raw_input: raw, generation_scope: "system", material_ids: ["selected-source"],
    research_request: { enabled: true }, execution_policy: { structured_arrangement: true, quality_loop: true, criterion_assessment: true,
      task_adaptive_format: true,
      optimization_policy: { root_call_budget: 24, max_repair_rounds_per_run: 8, max_attempts_per_finding: 6 } } });
  expect(input).not.toHaveProperty("mode");
});

test("真实生成默认不限调用，保留安全护栏与软提醒", async ({ page }) => {
  let submitted: Record<string, unknown> | undefined;
  await mockBackend(page, { onWrite: async (route, path) => {
    if (path !== "/task-starts") return false;
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { task_id: IDS.task, run_id: IDS.run } });
    return true;
  } });
  await page.route("**/api/v2/capabilities", (r) => r.fulfill({ json: {
    scenes: ["default_tutorial"], taxonomy: [], paid_calls_enabled: true, materials: { resource_policy_status: "ready" },
  } }));
  await page.goto("/new");
  await page.getByLabel("需求").fill("真实生成教程");
  await expect(page.getByLabel("整条任务模型调用上限")).toBeEmpty();
  await expect(page.getByText("留空表示不限", { exact: false })).toBeVisible();
  await expect(page.getByText(/无进展、取消和未知回执仍受保护/)).toBeVisible();
  await expect(page.getByLabel("启用 Criterion 评估")).toBeChecked();
  await page.getByRole("button", { name: "开始生成" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${IDS.task}$`));
  expect(submitted?.execution_policy).toMatchObject({
    structured_arrangement: true, quality_loop: true,
    task_adaptive_format: true,
    optimization_policy: { policy_version: "aiedu.repair.v2", enabled: true,
      root_call_budget: null, max_repair_rounds_per_run: 8, max_attempts_per_finding: 6 },
  });
});

test("演示入口不发送付费研究或优化策略", async ({ page }) => {
  let submitted: Record<string, unknown> | undefined;
  await mockBackend(page, { onWrite: async (route, path) => {
    if (path !== "/task-starts") return false;
    submitted = route.request().postDataJSON();
    await route.fulfill({ json: { task_id: IDS.task, run_id: IDS.run } });
    return true;
  } });
  await page.goto("/new");
  await page.getByLabel("需求").fill("演示教程");
  await page.getByRole("button", { name: "开始生成" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${IDS.task}$`));
  expect(submitted).toEqual({ raw_input: "演示教程", mode: "demo" });
});

test("回执未找到不被解释为未执行，也不开放重复提交", async ({ page }) => {
  await mockBackend(page);
  await page.route("**/api/v2/task-starts", (r) => r.abort());
  await page.route("**/api/v2/actions/receipt", (r) => r.fulfill({ json: { status: "not_found" } }));
  await page.goto("/new");
  await page.getByLabel("需求").fill("演示教程");
  await page.getByRole("button", { name: "开始生成" }).click();
  await page.getByRole("button", { name: "查询回执" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "仍不能断定未执行" })).toBeVisible();
  await expect(page.getByRole("button", { name: "开始生成" })).toBeDisabled();
});
