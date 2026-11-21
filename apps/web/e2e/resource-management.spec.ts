import { expect, test, type Page } from "@playwright/test";
import { mockBackend } from "./fixtures/build";

async function resources(page: Page, options: { active?: boolean; referenced?: boolean; fail?: "stale" | "unknown" } = {}) {
  await mockBackend(page);
  const rows = ["代数原文.md", "补充资料.md"].map((label, index) => ({ kind: "materials", id: `fixture-material-${index}`, label, resource_hash: `hash-${index}`, metadata: {}, management: { revision: 0, archived: false, trashed: false }, source_edit: "unsupported_immutable_source" }));
  const requests: Record<string, unknown>[] = [];
  let receiptFound = false;
  await page.route("**/managed-resources/materials**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/impact")) {
      const row = rows.find((r) => r.id === url.pathname.split("/").at(-2))!;
      return route.fulfill({ json: { resource: row, impact_hash: `impact-${row.management.revision}`, active: options.active ?? false,
        references: options.referenced ? [{ kind: "run", label: "固定历史运行", state: "completed" }] : [], purge_allowed: !options.referenced,
        purge_blockers: options.referenced ? [{ code: "RESOURCE_REFERENCED", reason: "仍有固定历史运行引用，不能清除。" }] : [], effects: { purge: "删除本资料及独占原文文件，不级联。" } } });
    }
    const filtered = rows.filter((r) => r.management.trashed === (url.searchParams.get("trashed") === "true") && (r.management.trashed || r.management.archived === (url.searchParams.get("archived") === "true")) && r.label.includes(url.searchParams.get("search") ?? ""));
    return route.fulfill({ json: { items: filtered, total: filtered.length, next_offset: null } });
  });
  await page.route("**/managed-resource-actions", async (route) => {
    const body = route.request().postDataJSON(); requests.push(body);
    expect(route.request().headers()["idempotency-key"]).toBeTruthy(); expect(route.request().headers()["x-csrf-token"]).toBeTruthy();
    expect(body.items.every((x: { expected_hash?: string; impact_hash?: string }) => x.expected_hash && x.impact_hash)).toBe(true);
    if (options.active) return route.fulfill({ status: 409, json: { code: "RESOURCE_IN_ACTIVE_RUN" } });
    if (options.fail === "stale") return route.fulfill({ status: 409, json: { code: "RESOURCE_STALE" } });
    if (options.fail === "unknown") return route.abort("failed");
    for (const item of body.items) {
      const row = rows.find((r) => r.id === item.id)!; row.management.revision++;
      if (body.action === "purge") { expect(body.confirmation_names[row.id]).toBe(row.label); rows.splice(rows.indexOf(row), 1); }
      else if (body.action === "trash" || body.action === "restore_trash") row.management.trashed = body.action === "trash";
      else row.management.archived = body.action === "archive";
    }
    return route.fulfill({ json: { items: [], operation: body.action, receipt_hash: "fixture", cleanup_pending: [], source_bytes_deleted: body.action === "purge" } });
  });
  await page.route("**/actions/receipt", (r) => r.fulfill({ json: { status: receiptFound ? "completed" : "not_found", response: { items: [], cleanup_pending: [] } } }));
  return { rows, requests, found: () => { receiptFound = true; } };
}

test("合并入口、旧深链映射与三子视图保留", async ({ page }) => {
  await resources(page);
  await page.goto("/library");
  const topTabs = page.getByRole("group", { name: "资源类型" }).getByRole("button");
  await expect(topTabs).toHaveCount(4);
  await expect(topTabs.filter({ hasText: "管理·恢复" })).toHaveCount(0);
  await expect(topTabs.filter({ hasText: "回收站" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "旧数据（只读）" })).toHaveCount(0);
  await page.getByRole("button", { name: "管理与回收" }).click();
  const manager = page.getByRole("region", { name: "资源管理" });
  await expect(manager).toBeVisible();
  await expect(page).toHaveURL(/\/library\?tab=manage$/);
  for (const name of ["常规目录", "归档", "回收站"]) await expect(manager.getByRole("button", { name, exact: true })).toBeVisible();
  await page.goto("/library?tab=trash");
  const trashedManager = page.getByRole("region", { name: "资源管理" });
  await expect(trashedManager).toBeVisible();
  await expect(trashedManager.getByRole("button", { name: "回收站", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("资料直接删除、撤销、批量恢复、搜索、勾选确认永久清除", async ({ page }, info) => {
  const backend = await resources(page);
  await page.goto("/library?tab=manage");
  const manager = page.getByRole("region", { name: "资源管理" });
  await manager.getByRole("combobox", { name: "资源类型", exact: true }).selectOption("materials");
  await manager.getByRole("row").filter({ hasText: "代数原文.md" }).getByRole("button", { name: "移入回收站", exact: true }).click();
  await expect(manager.getByRole("checkbox", { name: "选择 代数原文.md" })).toHaveCount(0);
  expect(backend.requests).toHaveLength(1);
  await manager.getByRole("button", { name: "撤销移入回收站" }).click();
  const restoreDialog = page.getByRole("dialog");
  await restoreDialog.getByLabel("我已查看引用和影响").check(); await restoreDialog.getByRole("button", { name: "确认从回收站恢复" }).click();
  await expect(manager.getByRole("checkbox", { name: "选择 代数原文.md" })).toBeVisible();
  await manager.getByRole("checkbox", { name: "选择 代数原文.md" }).check(); await manager.getByRole("checkbox", { name: "选择 补充资料.md" }).check();
  await manager.getByRole("button", { name: "批量移入回收站", exact: true }).click();
  await expect(manager.getByText("当前条件没有资源。")).toBeVisible();
  await manager.getByRole("button", { name: "回收站", exact: true }).click();
  await manager.getByRole("checkbox", { name: "选择 代数原文.md" }).check(); await manager.getByRole("checkbox", { name: "选择 补充资料.md" }).check();
  await manager.getByRole("button", { name: "批量恢复", exact: true }).click();
  const batchRestoreDialog = page.getByRole("dialog");
  await batchRestoreDialog.getByLabel("我已查看引用和影响").check(); await batchRestoreDialog.getByRole("button", { name: "确认从回收站恢复" }).click();
  await manager.getByRole("button", { name: "常规目录", exact: true }).click();
  await manager.getByLabel("搜索名称").fill("代数"); await manager.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(manager.getByRole("checkbox")).toHaveCount(1);
  await manager.getByRole("button", { name: "移入回收站", exact: true }).click();
  await manager.getByRole("button", { name: "回收站", exact: true }).click();
  await manager.getByRole("button", { name: "永久清除", exact: true }).click();
  const purgeDialog = page.getByRole("dialog");
  await expect(purgeDialog).toContainText("已检查 1 项资源 · 0 条引用。");
  await expect(purgeDialog).not.toContainText("固定历史运行引用");
  await expect(purgeDialog.getByRole("checkbox")).toHaveCount(1);
  await expect(purgeDialog.getByRole("button", { name: "确认永久清除" })).toBeDisabled();
  await purgeDialog.getByLabel("我确认永久删除，不可恢复").check();
  await page.screenshot({ path: info.outputPath("purge-confirmation.png"), fullPage: true });
  await purgeDialog.getByRole("button", { name: "确认永久清除" }).click();
  await expect(manager.getByText("当前条件没有资源。")).toBeVisible();
  expect(backend.rows.map((r) => r.label)).toEqual(["补充资料.md"]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
});

for (const mode of ["active", "referenced", "stale", "unknown"] as const) test(`保护与错误：${mode}不误报成功或自动重放`, async ({ page }) => {
  const backend = await resources(page, { active: mode === "active", referenced: mode === "referenced", fail: mode === "stale" || mode === "unknown" ? mode : undefined });
  if (mode === "referenced") backend.rows[0].management.trashed = true;
  await page.goto(mode === "referenced" ? "/library?tab=trash" : "/library?tab=manage");
  const manager = page.getByRole("region", { name: "资源管理" });
  await manager.getByRole("combobox", { name: "资源类型", exact: true }).selectOption("materials");
  await manager.getByRole("row").filter({ hasText: "代数原文.md" }).getByRole("button", { name: mode === "referenced" ? "永久清除" : "移入回收站", exact: true }).click();
  if (mode === "active") {
    await expect(manager.getByRole("alert")).toContainText("有运行中的任务或未确认调用");
    expect(backend.requests).toHaveLength(1);
  } else if (mode === "referenced") {
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("button", { name: /确认/ })).toBeDisabled();
    await expect(dialog).toContainText("固定历史运行引用"); expect(backend.requests).toHaveLength(0);
  } else {
    await expect(manager.getByRole("alert")).toBeVisible();
    if (mode === "stale") expect(backend.requests).toHaveLength(1);
    else {
      await manager.getByRole("button", { name: "查询未确认操作回执" }).click(); await expect(manager).toContainText("结果仍未确认");
      backend.found(); await manager.getByRole("button", { name: "查询未确认操作回执" }).click(); await expect(manager.getByRole("button", { name: "查询未确认操作回执" })).toHaveCount(0);
    }
  }
});

test("任务列表直接移入回收站入口实际提交并刷新", async ({ page }) => {
  await mockBackend(page);
  let trashed = false;
  await page.route("**/api/v2/tasks?*", (r) => r.fulfill({ json: { items: trashed ? [] : [{ task_id: "fixture-task", title: "待删除目标", state: "draft", version_id: null, updated_at: "2026-11-17T00:00:00Z", needs_action: false }], next_cursor: null, read_at: 0 } }));
  await page.route("**/managed-resources/tasks/*/impact", (r) => r.fulfill({ json: { resource: { kind: "tasks", id: "fixture-task", label: "待删除目标", resource_hash: "h", management: { trashed: false, archived: false, revision: 0 }, metadata: {} }, references: [], active: false, impact_hash: "i", purge_allowed: true, purge_blockers: [] } }));
  await page.route("**/managed-resource-actions", (r) => { expect(r.request().postDataJSON().items[0].kind).toBe("tasks"); trashed = true; return r.fulfill({ json: { items: [], operation: "trash", receipt_hash: "r", cleanup_pending: [] } }); });
  await page.goto("/"); await page.getByRole("button", { name: "将待删除目标移入回收站" }).click();
  await expect(page.getByRole("link", { name: "待删除目标", exact: true })).toHaveCount(0); expect(trashed).toBe(true);
  await expect(page.getByRole("link", { name: "回收站", exact: true })).toBeVisible();
});

test("设置已保存模型直接删除、私有回收站恢复与清除", async ({ page }) => {
  await mockBackend(page);
  let trashed = false, purged = false;
  const resource = () => ({ kind: "model-profiles", id: "fixture-model", label: "测试模型", resource_hash: String(trashed), metadata: { model: "offline", has_key: true }, management: { archived: false, trashed, revision: Number(trashed) } });
  await page.route("**/model-settings", (r) => r.fulfill({ json: { config: { active_id: null, user_profile: "", profiles: trashed || purged ? [] : [{ id: "fixture-model", name: "测试模型", model: "offline", has_key: true }] }, config_hash: "config-hash", roles: {}, role_assignments: {}, role_profiles: [] } }));
  await page.route("**/managed-resources/model-profiles**", (r) => r.request().url().endsWith("/impact") ? r.fulfill({ json: { resource: resource(), impact_hash: "i", references: [], active: false, purge_allowed: true, purge_blockers: [] } }) : r.fulfill({ json: { items: trashed && !purged ? [resource()] : [], total: trashed && !purged ? 1 : 0, next_offset: null } }));
  await page.route("**/managed-resource-actions", (r) => {
    const body = r.request().postDataJSON(); expect(body.items[0].kind).toBe("model-profiles");
    if (body.action === "purge") purged = true; else trashed = body.action === "trash";
    return r.fulfill({ json: { items: [], operation: body.action, receipt_hash: "r", cleanup_pending: [] } });
  });
  await page.goto("/settings");
  await page.getByRole("button", { name: "将测试模型移入回收站" }).click();
  await expect(page.getByRole("button", { name: "将测试模型移入回收站" })).toHaveCount(0);
  await page.getByText("模型回收站与恢复", { exact: true }).click();
  const manager = page.getByRole("region", { name: "资源管理" });
  // The management page refreshes independently after opening.
  await manager.getByRole("button", { name: "搜索", exact: true }).click();
  await manager.getByRole("button", { name: "恢复", exact: true }).click();
  const restoreDialog = page.getByRole("dialog"); await restoreDialog.getByLabel("我已查看引用和影响").check(); await restoreDialog.getByRole("button", { name: "确认从回收站恢复" }).click();
  await page.reload(); await expect(page.getByRole("button", { name: "将测试模型移入回收站" })).toBeVisible();
  await page.getByRole("button", { name: "将测试模型移入回收站" }).click();
  await page.getByText("模型回收站与恢复", { exact: true }).click(); await manager.getByRole("button", { name: "搜索", exact: true }).click();
  await manager.getByRole("button", { name: "永久清除", exact: true }).click();
  const purgeDialog = page.getByRole("dialog"); await purgeDialog.getByLabel("我确认永久删除，不可恢复").check(); await purgeDialog.getByRole("button", { name: "确认永久清除" }).click();
  await expect(manager.getByText("当前条件没有资源。")).toBeVisible(); expect(purged).toBe(true);
});

test("文件占用的清理回执跨刷新保留，仅重试已提交的文件清理", async ({ page }) => {
  await mockBackend(page);
  await page.addInitScript(() => { sessionStorage.setItem("aiedu.cleanup.v1", JSON.stringify(["fixture-cleanup-key"])); });
  await page.route("**/managed-resources/tasks?*", (r) => r.fulfill({ json: { items: [], total: 0, next_offset: null } }));
  let calls = 0;
  await page.route("**/managed-resource-cleanup", (r) => { calls++; expect(r.request().headers()["idempotency-key"]).toBe("fixture-cleanup-key"); return r.fulfill({ json: { operation: "purge", items: [], receipt_hash: "r", cleanup_pending: [], source_bytes_deleted: true } }); });
  await page.goto("/library?tab=trash"); await page.getByRole("button", { name: "继续清理未完成文件（1）" }).click();
  await expect(page.getByText("原回执文件清理已完成。")).toBeVisible(); expect(calls).toBe(1);
});
