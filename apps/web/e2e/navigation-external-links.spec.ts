import { expect, test } from "@playwright/test";
import { mockBackend } from "./fixtures/build";

// 2026-11-27 移动端阅读优先（D2）：手机档外链条移入设置页；桌面/平板侧栏入口保留。
// 2026-11-27 既有缺陷修复：轮3 起导航默认折叠，测试显式持久化为展开，保证角色可见性断言确定性。
test("外链官方标志、顺序、安全打开与键盘输入边界（桌面/平板）", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 1440) < 768, "手机档外链入口见下一用例（D2）");
  await page.addInitScript(() => localStorage.setItem("aiedu.nav.open.v1", "1"));
  await mockBackend(page);
  await page.route("**/format-specs", (r) => r.fulfill({ json: { items: [] } }));
  await page.goto("/new");
  const nav = page.getByRole("navigation", { name: "外部提问" });
  const links = nav.locator("a");
  await expect(links).toHaveCount(2);
  for (const [i, brand, href, shortcut] of [[0, "ChatGPT", "https://chatgpt.com", "Shift+F8"], [1, "DeepSeek", "https://chat.deepseek.com", "Shift+F9"]] as const) {
    const link = links.nth(i);
    await expect(link).toHaveAttribute("href", href);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
    await expect(link).toHaveAttribute("aria-keyshortcuts", shortcut);
    await expect(link).toHaveAttribute("title", new RegExp(brand));
    await expect.poll(() => link.getByRole("img", { name: `${brand} 官方标志` }).evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  }
  const first = (await links.nth(0).boundingBox())!, second = (await links.nth(1).boundingBox())!;
  expect(first.y).toBeLessThan(second.y);
  // Prevent real external navigation, while testing anchor activation.
  await nav.evaluate((el) => { el.setAttribute("data-clicks", "0"); el.addEventListener("click", (e) => { e.preventDefault(); el.setAttribute("data-clicks", String(Number(el.getAttribute("data-clicks")) + 1)); }); });
  await links.nth(0).click(); await links.nth(1).click();
  await expect(nav).toHaveAttribute("data-clicks", "2");
  await page.locator("h1").click();
  await page.keyboard.press("Shift+F8"); await page.keyboard.press("Shift+F9");
  await expect(nav).toHaveAttribute("data-clicks", "4");
  const input = page.locator("textarea").first();
  for (const part of [/目标/, /2 小时/, /难度/, /教程/, /选择题/, /计算题/, /10 道/]) await expect(input).toHaveAttribute("placeholder", part);
  await input.fill("我的原话：两小时学代数"); await page.keyboard.press("Shift+F8");
  await page.evaluate(() => {
    for (const tag of ["input", "select", "div"]) {
      const el = document.createElement(tag); if (tag === "div") el.contentEditable = "true";
      document.body.append(el); el.dispatchEvent(new KeyboardEvent("keydown", { key: "F9", shiftKey: true, bubbles: true })); el.remove();
    }
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "F9", shiftKey: true, isComposing: true, bubbles: true }));
  });
  await expect(nav).toHaveAttribute("data-clicks", "4"); // 组合键/编辑态边界不触发
});

test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
test("手机档：侧栏外链隐藏（D2），入口在设置页", async ({ page }) => {
  await mockBackend(page);
  await page.goto("/new");
  await expect(page.getByRole("navigation", { name: "外部提问" })).toHaveCount(0);
  await expect(page.locator(".external-nav")).toBeHidden();
  await page.goto("/settings");
  const section = page.getByRole("region", { name: "外部提问" });
  await expect(section).toBeVisible();
  const links = section.locator("a");
  await expect(links).toHaveCount(2);
  await expect(links.nth(0)).toHaveAttribute("href", "https://chatgpt.com");
  await expect(links.nth(1)).toHaveAttribute("href", "https://chat.deepseek.com");
});

