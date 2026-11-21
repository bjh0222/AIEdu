import { test } from "@playwright/test";
import { IDS, SAMPLE, finding, location, mockBackend, runSnapshot, versionRead } from "../e2e/fixtures/build";

// 目检用截图，不属于验收套件（不在 e2e/ 下，默认 `playwright test` 不会运行）。
// 运行：npx playwright test --config=tools/shots.config.ts
test("visual shots", async ({ page }, info) => {
  const f1 = finding(IDS.f1, "示例：这里的结论缺少依据", location(SAMPLE, "这里有==重点内容==", IDS.v2));
  const f2 = finding(IDS.f2, "示例：疑似的表述问题", location(SAMPLE, "用黎曼和取极限即可得到。", IDS.v2), "suspected");
  const v2 = versionRead(IDS.v2, 2, SAMPLE, [f1, f2]);
  await mockBackend(page, { snapshot: () => runSnapshot({ quality: v2.quality }), versions: { [IDS.v2]: v2 } });
  const name = info.project.name;
  await page.goto(`/tasks/${IDS.task}/read`);
  await page.waitForSelector(".prose h2");
  await page.locator(".prose h2").first().scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollTo({ top: 380 }));
  await page.waitForTimeout(800);
  await page.screenshot({ path: `e2e/results/final-reader-${name}.png` });
  if (name === "w1440") {
    await page.evaluate(() => { localStorage.setItem("aiedu.prefs.v2", JSON.stringify({ theme: "dark", serif: true, size: 17 })); });
    await page.reload();
    await page.waitForSelector(".prose h2");
    await page.evaluate(() => window.scrollTo({ top: 1500 }));
    await page.waitForTimeout(800);
    await page.screenshot({ path: `e2e/results/final-reader-dark.png` });
    await page.getByRole("button", { name: "专注" }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `e2e/results/final-reader-focus.png` });
  }
});
