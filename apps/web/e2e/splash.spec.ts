import { expect, test } from "@playwright/test";

test("cold start plays one splash, keyboard skips, refresh does not replay", async ({ page }) => {

  await page.goto("/");

  const splash = page.locator('section[aria-label="AI Edu 开屏"]');
  await expect(splash).toBeVisible();
  await expect(splash.locator("p")).toHaveCount(1);
  await expect(splash.locator("p")).toContainText(/愿你有天蓝可往|愿你踏遍山川|愿你始终自由/);
  await expect(splash.locator("img[alt='']")).toHaveCount(1);

  await page.waitForTimeout(500);
  await page.keyboard.press("Escape");
  await expect(splash).toHaveCount(0);

  await page.reload();
  await expect(page.locator('section[aria-label="AI Edu 开屏"]')).toBeHidden();
});


