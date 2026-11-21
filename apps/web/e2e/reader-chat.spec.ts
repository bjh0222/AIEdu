import { expect, test, type Page } from "@playwright/test";
import { IDS, mockBackend, unit, versionRead } from "./fixtures/build";

const BODIES = [
  "# 定积分入门\n\n这是第一章的完整内容，包含核心概念与例子。",
  "# 第二章\n\n这是第二章的完整内容。",
  "# 第三章\n\n这是第三章的完整内容。",
];

function threeUnitRead() {
  const base = versionRead(IDS.v2, 2, BODIES[0], []);
  return {
    ...base,
    content: {
      ...base.content,
      units: BODIES.map((body, index) => ({
        ...unit(body),
        unit_id: `unit-${index + 1}`,
        title: `章节 ${index + 1}`,
      })),
    },
  };
}

test("reader sidebars collapse and chat context scope is assembled", async ({ page }: { page: Page }) => {
  const chatRequests: Record<string, unknown>[] = [];
  await mockBackend(page, { versions: { [IDS.v2]: threeUnitRead() } });
  await page.route("**/api/chat", async (route) => {
    chatRequests.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ json: {
      user: { id: "u", role: "user", content: "x" },
      assistant: { id: "a", role: "assistant", content: "基于当前章节的助手回答。" },
      demo: false,
      model_call: { provider: "openai-compatible", model: "fixture-model", usage: { total_tokens: 1, usage_source: "provider" } },
    } });
  });

  await page.goto(`/tasks/${IDS.task}/read`);
  await expect(page.getByRole("complementary", { name: "AI 聊天" })).toBeVisible();
  await expect(page.getByText("当前章节已注入。选中正文片段会作为引用；要问整段或全书，用下方范围切换。")).toBeVisible();
  await expect(page.getByText("查看完整质量报告")).toHaveCount(0);

  await page.keyboard.press("Shift+F7");
  await expect(page.getByRole("complementary", { name: "目录与章节" })).toBeHidden();
  await expect(page.getByRole("button", { name: "展开目录" })).toBeVisible();
  await page.keyboard.press("Shift+F7");
  await expect(page.getByRole("complementary", { name: "目录与章节" })).toBeVisible();

  await page.keyboard.press("Shift+F6");
  await expect(page.getByRole("complementary", { name: "AI 聊天" })).toBeHidden();
  await expect(page.getByRole("button", { name: "展开聊天" })).toBeVisible();
  await page.keyboard.press("Shift+F6");
  await expect(page.getByRole("complementary", { name: "AI 聊天" })).toBeVisible();

  await page.getByLabel("聊天输入").fill("当前章节的重点是什么？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("基于当前章节的助手回答。").first()).toBeVisible();
  expect(chatRequests[0]).toMatchObject({ context_scope: "current_unit" });
  expect((chatRequests[0].context_units as { unit_id: string; body: string }[]).map((u) => u.unit_id)).toEqual(["unit-1"]);
  expect((chatRequests[0].context_units as { unit_id: string; body: string }[] | undefined)?.[0]?.body).toContain("第一章的完整内容");

  await page.getByRole("button", { name: "整篇" }).click();
  await page.getByLabel("聊天输入").fill("整篇的核心主线是什么？");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("基于当前章节的助手回答。").first()).toBeVisible();
  expect(chatRequests[1]).toMatchObject({ context_scope: "whole_document" });
  expect((chatRequests[1].context_units as unknown[]).length).toBe(3);

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
  await expect(page.getByText("已捕获引用")).toBeVisible();
  await page.getByRole("button", { name: "指定章节" }).click();
  await page.getByRole("button", { name: "章节 2", exact: true }).click();
  await page.getByLabel("聊天输入").fill("结合引用说明");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("基于当前章节的助手回答。").first()).toBeVisible();
  expect(chatRequests[2]).toMatchObject({
    context_scope: "selected_units",
    selected_quote: expect.stringContaining("第一章的完整内容"),
  });
  expect((chatRequests[2].context_units as { unit_id: string }[]).map((u) => u.unit_id)).toEqual(["unit-2"]);
});






