import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "@playwright/test";

function args() {
  const values = { expect: [] };
  for (let i = 2; i < process.argv.length; i += 1) {
    const key = process.argv[i];
    const value = process.argv[i + 1];
    if (!key.startsWith("--") || value === undefined) {
      throw new Error(`Invalid argument: ${key}`);
    }
    if (key === "--expect") {
      values.expect.push(value);
    } else {
      values[key.slice(2)] = value;
    }
    i += 1;
  }
  for (const required of ["base", "task", "output"]) {
    if (!values[required]) throw new Error(`Missing --${required}`);
  }
  return values;
}

const options = args();
const outputDir = path.resolve(options.output);
await mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const results = [];

for (const viewport of [
  { name: "w1440", width: 1440, height: 900, isMobile: false },
  { name: "w390", width: 390, height: 844, isMobile: true },
]) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    isMobile: viewport.isMobile,
    hasTouch: viewport.isMobile,
  });
  const page = await context.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const failedResponses = [];
  const successfulResponses = new Set();
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      failedResponses.push({ status: response.status(), url: response.url() });
    } else {
      successfulResponses.add(response.url());
    }
  });
  const url = `${options.base.replace(/\/$/, "")}/tasks/${options.task}/read`;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.waitForSelector(".prose", { timeout: 60_000 });
  await page.waitForTimeout(500);
  const prose = await page.locator(".prose").innerText();
  let body = await page.locator("body").innerText();
  let drawerText = "";
  if (viewport.isMobile && options.expect.some((value) => !body.includes(value))) {
    await page.getByRole("button", { name: "目录", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "目录" });
    await drawer.waitFor({ state: "visible" });
    drawerText = await drawer.innerText();
    await page.screenshot({ path: path.join(outputDir, `toc-${viewport.name}.png`), fullPage: true });
  }
  const title = await page.title();
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  );
  const expected = Object.fromEntries(
    options.expect.map((value) => [value, body.includes(value) || drawerText.includes(value)]),
  );
  const relevantFailedResponses = failedResponses.filter((row) => {
    if (row.status === 401 && successfulResponses.has(row.url)) return false;
    if (row.status === 404 && /\/favicon\.ico(?:$|\?)/.test(row.url)) return false;
    return true;
  });
  const screenshot = path.join(outputDir, `reader-${viewport.name}.png`);
  await page.screenshot({ path: screenshot, fullPage: true });
  results.push({
    viewport: viewport.name,
    url,
    title,
    proseCharacters: prose.length,
    bodyCharacters: body.length,
    expected,
    allExpectedVisible: Object.values(expected).every(Boolean),
    horizontalOverflow,
    consoleErrors,
    pageErrors,
    failedResponses,
    relevantFailedResponses,
    screenshot,
  });
  await context.close();
}

await browser.close();
const report = {
  task_id: options.task,
  base_url: options.base,
  checked_at: new Date().toISOString(),
  results,
  passed: results.every(
    (row) =>
      row.proseCharacters > 0 &&
      row.allExpectedVisible &&
      !row.horizontalOverflow &&
      row.pageErrors.length === 0 &&
      row.relevantFailedResponses.length === 0,
  ),
};
const reportPath = path.join(outputDir, "results.json");
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ reportPath, passed: report.passed }));
if (!report.passed) process.exitCode = 1;
