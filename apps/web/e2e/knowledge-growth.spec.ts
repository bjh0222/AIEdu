import { expect, test } from "@playwright/test";
import { IDS, mockBackend, runSnapshot, versionRead, SAMPLE } from "./fixtures/build";

function graph(count: number, saved = false) {
  return { demo: true, status: "running", version_id: IDS.v2, graph_hash: String(count)+saved, logical_relations: "unknown",
    nodes: Array.from({ length: count }, (_, i) => ({ id: `growth-${i}`, unit_id: i ? `unit-${i}` : IDS.unit, kind: "chapter", title: `代数章节 ${i + 1}`, summary: "冻结计划中的章节", status: saved || i === 0 ? "saved" : "planned", source_hash: saved || i === 0 ? "a".repeat(64) : null, version_id: saved || i === 0 ? IDS.v2 : null })),
    edges: Array.from({ length: count - 1 }, (_, i) => ({ source: `growth-${i}`, target: `growth-${i+1}`, kind: "contains", evidence: "frozen-plan" })) };
}

test("真实阶段驱动一次动画、新节点生长、侧边用量、键盘缩放及章节跳转", async ({ page }, info) => {
  let phase = "understanding", revision = 1, saved = false;
  const writes = await mockBackend(page, { snapshot: () => runSnapshot({ state: "running", state_revision: revision, stages: { [phase]: "running" }, calls: [], stop_reason: null }), versions: { [IDS.v2]: versionRead(IDS.v2, 2, SAMPLE, []) } });
  await page.route("**/knowledge-graph?*", (r) => r.fulfill({ json: graph(saved ? 8 : 6, saved), headers: { ETag: `"g-${revision}"` } }));
  await page.goto(`/tasks/${IDS.task}`);
  const map = page.getByRole("region", { name: "知识体系", exact: true });
  await expect(map.locator(".knowledge-node")).toHaveCount(6);
  await expect(map.locator(".recently-saved")).toHaveCount(0);
  await expect(map.getByRole("complementary", { name: "生成状态与用量" })).toContainText("模型调用");
  for (const [next, animation] of [["planning", "growth-plan"], ["generating", "growth-write"], ["checking", "growth-check"], ["repairing", "growth-repair"], ["verifying", "growth-verify"], ["exporting", "growth-export"]]) {
    phase = next; revision++;
    if (next === "generating") saved = true;
    await expect(map.locator(`.stage-${next}`)).toBeVisible();
    await expect(map.locator(`.stage-${next} .growth-stage-mark`)).toHaveCSS("animation-name", animation);
    if (next === "generating") {
      await expect(map.locator(".knowledge-node")).toHaveCount(8);
      await expect(map.locator(".recently-saved")).toHaveCount(7);
      await expect(map.locator(".knowledge-edges .relation-added")).toHaveCount(2);
    }
  }
  await expect(map.locator(".knowledge-node.saved")).toHaveCount(8);
  await map.locator(".knowledge-node").first().focus(); await page.keyboard.press("ArrowRight"); await expect(map.locator(".knowledge-node").nth(1)).toBeFocused();
  await page.keyboard.press("Enter"); await map.getByRole("button", { name: "所选与相邻" }).click(); await expect(map.locator(".knowledge-node")).toHaveCount(3);
  await map.getByRole("button", { name: "全局", exact: true }).click();
  await map.getByRole("button", { name: "放大知识图" }).click(); await expect(map.getByRole("button", { name: "重置知识图缩放" })).toHaveText("120%");
  await map.getByRole("button", { name: "重置知识图缩放" }).click();
  await map.getByText("全部已记录关系（7）", { exact: true }).click(); await expect(map.locator("details").filter({ hasText: "全部已记录关系" }).getByText("代数章节 1 包含 代数章节 2", { exact: true })).toBeVisible();
  await map.locator(".knowledge-node").first().click();
  await page.screenshot({ path: info.outputPath("knowledge-growth.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
  expect(writes.filter((c) => c.method !== "GET")).toHaveLength(0);
  await map.getByRole("link", { name: "进入对应章节" }).click(); await expect(page).toHaveURL(new RegExp(`unit=${IDS.unit}`));
});

test("大图减动高对比、分页、失败重试、空图与暂停未知回退", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce", forcedColors: "active", colorScheme: "dark" });
  await mockBackend(page, { snapshot: () => runSnapshot({ state: "paused", calls: [{ call_id: "unknown-test", unit_id: IDS.unit, status: "unknown", usage: null }] }) });
  let mode = "large";
  await page.route("**/knowledge-graph?*", (r) => mode === "error" ? r.fulfill({ status: 503, json: { code: "SOURCE_UNAVAILABLE" } }) : r.fulfill({ json: mode === "empty" ? { ...graph(1), nodes: [], edges: [] } : graph(125) }));
  await page.goto(`/tasks/${IDS.task}`);
  const map = page.getByRole("region", { name: "知识体系", exact: true });
  await expect(map.locator(".knowledge-node")).toHaveCount(60); await expect(map).toContainText("存在结果未知的调用"); await expect(map).toContainText("已暂停");
  await map.getByRole("button", { name: "下一组节点" }).click(); await expect(map).toContainText("第 2 / 3 组");
  await expect(map.locator(".knowledge-node").first()).toHaveCSS("animation-name", "none");
  mode = "error"; await page.reload(); await expect(map.getByRole("alert")).toContainText("结构读取失败");
  mode = "empty"; await map.getByRole("button", { name: "重试结构" }).click(); await expect(map).toContainText("还没有可显示的真实结构");
});
