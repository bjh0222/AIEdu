import { expect, test, type Page } from "@playwright/test";
import { IDS, SAMPLE, mockBackend, runSnapshot, versionRead } from "./fixtures/build";

// 2026-11-27 移动端阅读优先（A+B 混排）：手机竖屏（390×844）阅读页行为。
// 方法标签：fixture（经 page.route 注入后端同形状响应）。只证明前端行为，不是专业校准或质量通过证据。

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

async function openReader(page: Page) {
  const v = versionRead(IDS.v2, 2, SAMPLE, []);
  await mockBackend(page, { snapshot: () => runSnapshot({ quality: v.quality }), versions: { [IDS.v2]: v } });
  await page.goto(`/tasks/${IDS.task}/read`);
  await page.waitForSelector(".prose h1");
}

test("R-P1 手机阅读页：全局底栏隐藏、返回可达、无整页横向溢出", async ({ page }) => {
  await openReader(page);
  await expect(page.locator(".tabbar")).toHaveCount(0);
  await expect(page.locator(".app")).toHaveClass(/reader-route/);
  await expect(page.getByRole("link", { name: "返回任务页" })).toBeVisible();
  const overflow = await page.evaluate(() =>
    (document.scrollingElement?.scrollWidth ?? 0) - (document.scrollingElement?.clientWidth ?? 0));
  expect(overflow).toBeLessThanOrEqual(0);
  await page.getByRole("link", { name: "返回任务页" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${IDS.task}$`));
});

test("R-P2 手机目录 sheet：打开、当前章节信息可见、跳转后关闭", async ({ page }) => {
  await openReader(page);
  await page.getByRole("button", { name: "目录", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "目录" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".drawer-grab")).toBeVisible();
  const firstTick = dialog.locator(".tick").first();
  await firstTick.click();
  await expect(dialog).toBeHidden();
});

test("R-P3 手机问答 sheet：打开、发送、Markdown 回答、引用捕获与删除", async ({ page }) => {
  await openReader(page);
  await page.route("**/api/v2/chat", async (route) => {
    return route.fulfill({ json: {
      user: { id: "u", role: "user", content: "x" },
      assistant: { id: "a", role: "assistant", content: "## 已知事实与缺口\n\n基于当前章节的助手回答。" },
      demo: false,
      model_call: { provider: "openai-compatible", model: "fixture-model", usage: { total_tokens: 1, usage_source: "provider" } },
    } });
  });
  await page.getByRole("button", { name: "问答", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "AI 聊天" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[aria-label="聊天会话"]')).toBeVisible(); // 手机档折叠 details
  await page.getByLabel("聊天输入").fill("当前章节的重点是什么？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(dialog.locator(".chat-prose h2").first()).toHaveText(/已知事实与缺口#?/);

  // 选中文本 → 引用 chip → 可删除
  await page.evaluate(() => {
    const node = document.querySelector(".prose p");
    if (!node) throw new Error("prose paragraph not found");
    const range = document.createRange();
    range.selectNodeContents(node);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
  });
  await expect(dialog.getByText("已捕获引用", { exact: false }).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

