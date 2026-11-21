import { expect, test } from "@playwright/test";
import { IDS, SAMPLE, mockBackend, versionRead } from "./fixtures/build";

function graph(count: number, state = "completed") {
  return { demo: true, status: state, version_id: IDS.v2, graph_hash: "graph-hash", logical_relations: "unknown",
    nodes: Array.from({ length: count }, (_, i) => ({ id: `node-${i}`, unit_id: i ? `unit-${i}` : IDS.unit, kind: i > 2 ? "method" : "chapter", title: `来源节点 ${i}`, summary: `已保存来源 ${i}`, status: i === 2 ? "planned" : "saved", source_hash: i === 2 ? null : "a".repeat(64), version_id: i === 2 ? null : IDS.v2 })),
    edges: Array.from({ length: Math.max(0, count - 1) }, (_, i) => ({ source: `node-${i}`, target: `node-${i+1}`, kind: i % 2 ? "contains" : "prerequisite", evidence: "fixture" })) };
}

test("知识图少量/多层：键盘、局部视图、缩放、真实章节跳转与刷新恢复", async ({ page }, testInfo) => {
  await mockBackend(page, { versions: { [IDS.v2]: versionRead(IDS.v2, 2, SAMPLE, []) } });
  await page.route("**/knowledge-graph?*", (route) => route.fulfill({ json: graph(6), headers: { ETag: '"graph-1"' } }));
  await page.goto(`/tasks/${IDS.task}`);
  const map = page.getByRole("region", { name: "知识体系", exact: true });
  await expect(map.locator(".knowledge-node")).toHaveCount(6);
  await page.screenshot({ path: testInfo.outputPath("knowledge-light.png"), fullPage: true });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
  await page.screenshot({ path: testInfo.outputPath("knowledge-dark.png"), fullPage: true });
  await page.evaluate(() => { delete document.documentElement.dataset.theme; });
  await map.locator(".knowledge-node").first().focus();
  await page.keyboard.press("ArrowRight"); await expect(map.locator(".knowledge-node").nth(1)).toBeFocused();
  await page.keyboard.press("Enter"); await map.getByRole("button", { name: "所选与相邻" }).click();
  await expect(map.locator(".knowledge-node")).toHaveCount(3);
  await map.getByRole("button", { name: "全局", exact: true }).click();
  await map.getByRole("button", { name: "放大知识图" }).click(); await expect(map.getByRole("button", { name: "重置知识图缩放" })).toHaveText("120%");
  await map.locator(".knowledge-node").first().click();
  await map.getByRole("link", { name: "进入对应章节" }).click();
  await expect(page).toHaveURL(new RegExp(`unit=${IDS.unit}`));
  await page.reload(); await expect(page.getByRole("heading", { name: "定积分入门" }).first()).toBeVisible();
});

test("知识图大图/失败与空态：分组保留、减动、深色和高对比", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce", colorScheme: "dark", forcedColors: "active" });
  await mockBackend(page);
  let state = "large";
  await page.route("**/knowledge-graph?*", (route) => state === "failed" ? route.fulfill({ status: 503, json: { code: "SOURCE_UNAVAILABLE" } }) : route.fulfill({ json: graph(state === "empty" ? 0 : 125, "interrupted") }));
  await page.goto(`/tasks/${IDS.task}`);
  const map = page.getByRole("region", { name: "知识体系", exact: true });
  await expect(map.locator(".knowledge-node")).toHaveCount(60);
  await map.getByRole("button", { name: "下一组节点" }).click(); await expect(map).toContainText("第 2 / 3 组");
  await expect(map.locator(".knowledge-node").first()).toHaveCSS("animation-name", "none");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
  expect(overflow).toBe(false);
  state = "failed"; await page.reload(); await expect(map.getByRole("alert")).toContainText("结构读取失败");
  state = "empty"; await map.getByRole("button", { name: "重试结构" }).click(); await expect(map).toContainText("还没有可显示的真实结构");
});

test("资源管理：搜索、引用确认、归档与恢复都保留来源", async ({ page }, testInfo) => {
  await mockBackend(page);
  let archived = false;
  const resource = () => ({ kind: "tasks", id: IDS.task, label: "可恢复任务", resource_hash: archived ? "after" : "before", metadata: { status: "completed" }, management: { archived, revision: archived ? 1 : 0 }, source_edit: "versioned_endpoint" });
  await page.route("**/managed-resources/tasks**", (route) => route.request().url().endsWith("/impact") ? route.fulfill({ json: { resource: resource(), references: [{ kind: "run", task_id: IDS.task, run_id: IDS.run, state: "completed" }], impact_hash: "impact", active: false } }) : route.fulfill({ json: { items: String(archived) === new URL(route.request().url()).searchParams.get("archived") ? [resource()] : [], total: String(archived) === new URL(route.request().url()).searchParams.get("archived") ? 1 : 0, next_offset: null } }));
  await page.route("**/managed-resource-actions", async (route) => { const request = route.request().postDataJSON(); expect(request.acknowledge_references).toBe(true); expect(request.items[0].impact_hash).toBe("impact"); archived = request.action === "archive"; await route.fulfill({ json: { receipt_hash: "receipt", source_bytes_deleted: false } }); });
  await page.goto("/library?tab=manage");
  const manager = page.getByRole("region", { name: "资源管理" });
  const dialog = page.getByRole("dialog");
  await manager.getByRole("row").filter({ hasText: "可恢复任务" }).getByRole("button", { name: "归档", exact: true }).click();
  await expect(dialog.getByRole("link", { name: "查看相关任务" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认归档", exact: true })).toBeDisabled();
  await dialog.getByLabel("我已查看引用和影响").check(); await dialog.getByRole("button", { name: "确认归档", exact: true }).click();
  await expect(page.getByText("当前条件没有资源。")).toBeVisible();
  await manager.getByRole("group", { name: "资源目录" }).getByRole("button", { name: "归档", exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath("resource-restore.png"), fullPage: true });
  await manager.getByRole("button", { name: "恢复归档", exact: true }).click();
  await expect(dialog.getByRole("link", { name: "查看相关任务" })).toBeVisible();
  await dialog.getByLabel("我已查看引用和影响").check(); await dialog.getByRole("button", { name: "确认恢复归档", exact: true }).click();
  await expect(page.getByText("当前条件没有资源。")).toBeVisible();
});

test("设计原理修订：版本保护、来源检索和新版本回执", async ({ page }) => {
  await mockBackend(page);
  await page.route("**/knowledge/assets/**", (route) => route.fulfill({ json: { items: route.request().url().includes("principles") ? [{ id: "principle-fixture", version: 3, statement: "原理原文", mechanism: "机制", guidance: "指导", status: "proposed" }] : [], total: 1 } }));
  let saved = false;
  await page.route("**/knowledge/principles/principle-fixture", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.expected_version).toBe(3); expect(body.statement).toBe("修订后的原理"); expect(body.status).toBe("proposed");
    saved = true; await route.fulfill({ json: { id: "principle-fixture", version: 4, calibration_status: "not_calibrated" } });
  });
  await page.goto("/library?tab=knowledge");
  await expect(page.getByRole("region", { name: "已整理知识卡检索" })).toContainText("0 张卡片");
  await page.getByRole("button", { name: "修订原理" }).click();
  await page.getByLabel("原理", { exact: true }).fill("修订后的原理");
  await page.getByRole("button", { name: "保存新版本" }).click();
  await expect(page.getByRole("dialog")).toBeHidden(); expect(saved).toBe(true);
});

test("模型角色：可选择已保存的模型并以配置hash保存", async ({ page }) => {
  await mockBackend(page);
  let saved = false;
  await page.route("**/model-settings", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON(); expect(body.expected_config_hash).toBe("model-hash"); expect(body.action).toBe("roles"); expect(body.value.review).toBe("review-fixture");
      saved = true; return route.fulfill({ json: { applied: true, applied_revision: 2 } });
    }
    return route.fulfill({ json: { config: { profiles: [], active_id: null, user_profile: "" }, config_hash: "model-hash", roles: {}, role_assignments: {}, role_profiles: [{ id: "review-fixture", name: "独立配置", model: "fixture", model_family: "glm" }] } });
  });
  await page.goto("/settings");
  await page.getByLabel("独立检查", { exact: true }).selectOption("review-fixture");
  await page.getByRole("button", { name: "保存角色分配" }).click();
  await expect(page.getByText("角色分配已保存", { exact: true })).toBeVisible(); expect(saved).toBe(true);
});
