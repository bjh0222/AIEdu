import { expect, test } from "@playwright/test";

test("cold start plays one splash, auto-closes, refresh does not replay", async ({ page }) => {

  await page.goto("/");

  const splash = page.locator('section[aria-label="AI Edu 开屏"]');
  await expect(splash).toBeVisible();
  await expect(splash.locator("p")).toHaveCount(1);
  await expect(splash.locator("p")).toContainText(/愿你有天蓝可往|愿你踏遍山川|愿你始终自由/);
  await expect(splash.locator('img[src*="/splash/sky.webp"]')).toHaveCount(1);
  await expect(splash.locator('img[src*="ai-edu-mark"]')).toHaveCount(1);

  await page.waitForTimeout(3600);
  await expect(splash).toBeHidden();

  await page.reload();
  await expect(page.locator('section[aria-label="AI Edu 开屏"]')).toBeHidden();
});

