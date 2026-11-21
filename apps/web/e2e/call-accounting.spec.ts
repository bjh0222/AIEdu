import { expect, test } from "@playwright/test";
import { IDS, mockBackend, runSnapshot } from "./fixtures/build";
import { callCostsText } from "../lib/v2/status";

// Synthetic accounting regression, never a real cost or provider receipt.
test("费用按币种累加，不把第一条价格当总价、不把缺价当零", () => {
  expect(callCostsText([
    { status: "settled", price: { amount: 0.1, currency: "CNY" } },
    { status: "settled", price: { amount: 0.2, currency: "CNY" } },
    { status: "late", price: { total: 1, currency: "USD" } },
  ], false)).toBe("CNY 0.3；USD 1");
  expect(callCostsText([{ status: "settled", price: { amount: 5 } }], false)).toContain("待核实");
  expect(callCostsText([
    { status: "settled", price: { amount: 0, currency: "CNY" } },
    { status: "quarantined", price: null },
    { status: "not_sent", price: null },
  ], false)).toBe("已知 CNY 0；1 次金额待核实");
  expect(callCostsText([], true)).toBe("不适用（演示）");
});

test("调用已settled但缺价：不声称调用仍执行或账务已结算", async ({ page }) => {
  const original = runSnapshot();
  await mockBackend(page, { snapshot: () => runSnapshot({ demo: false, cost_final: false,
    calls: original.calls.map(c => ({ ...c, usage: { total_tokens: 12 }, price: null })) }) });
  await page.goto(`/tasks/${IDS.task}`);
  const usage = page.getByRole("complementary", { name: "生成状态与用量" });
  await expect(usage).toContainText("调用结果已记录；仍缺提供方价格或费用回执");
  await expect(usage).toContainText("总费用未确认");
  await expect(usage).not.toContainText("仍有调用没有结清");
  await expect(usage).not.toContainText("执行中");
});

test("quarantined unknown保留结果与用量未知，不因执行隔离声称结清", async ({ page }) => {
  const original = runSnapshot();
  await mockBackend(page, { snapshot: () => runSnapshot({ demo: false, cost_final: false,
    calls: original.calls.map(c => ({ ...c, status: "quarantined", outcome: "unknown", usage: null, price: null })) }) });
  await page.goto(`/tasks/${IDS.task}`);
  const usage = page.getByRole("complementary", { name: "生成状态与用量" });
  await expect(usage).toContainText("有调用结果或用量仍待核实");
  await expect(usage).not.toContainText("调用结果已记录；");
});
