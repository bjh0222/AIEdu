import { expect, test } from "@playwright/test";
import { mockBackend } from "./fixtures/build";

test("外链官方标志、顺序、安全打开、底部可见及键盘输入边界", async ({ page }, info) => {
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
    await expect(link).toBeInViewport();
  }
  const first = (await links.nth(0).boundingBox())!, second = (await links.nth(1).boundingBox())!;
  expect(first.y).toBeLessThan(second.y);
  expect(second.y + second.height).toBeGreaterThan(page.viewportSize()!.height - 40);
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
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "F8", shiftKey: true, ctrlKey: true }));
    const event = new KeyboardEvent("keydown", { key: "F8", shiftKey: true, cancelable: true }); event.preventDefault(); window.dispatchEvent(event);
  });
  await expect(nav).toHaveAttribute("data-clicks", "4");
  await expect(input).toHaveValue("我的原话：两小时学代数");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
  await page.screenshot({ path: info.outputPath("external-links.png"), fullPage: true });
});
