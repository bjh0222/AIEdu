import { expect, test } from "@playwright/test";
import { IDS, SAMPLE, finding, location, mockBackend, runSnapshot, versionRead } from "./fixtures/build";

// 方法标签：fixture（经 page.route 注入后端同形状响应）。只证明前端行为，不是 AT/DEV-G 专业通过证据。
// 对应 AT-03-R01—R13（阅读器能力）与 AT-10 定位/过期。

// Windows 剪贴板读回时把换行变成 CRLF；比较前只归一换行符，其余逐字相等。
const clip = async (page: import("@playwright/test").Page) => {
  const raw = await page.evaluate(() => navigator.clipboard.readText());
  return raw.replace(/\r\n/g, "\n");
};

async function open(page: import("@playwright/test").Page, opts: { findings?: unknown[] } = {}) {
  const v = versionRead(IDS.v2, 2, SAMPLE, opts.findings ?? []);
  const calls = await mockBackend(page, { snapshot: () => runSnapshot({ quality: v.quality }), versions: { [IDS.v2]: v } });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto(`/tasks/${IDS.task}/read`);
  await page.waitForSelector(".prose h1");
  return { calls, errors };
}

test.describe("阅读器 AT-03", () => {
  test("多行矩阵紧贴双美元定界符：不吞后文且标题锚点保真", async ({ page }) => {
    const body = String.raw`# Matrix projection

**1.** Calculate
$$D=\begin{vmatrix}
a&b\\
c&d
\end{vmatrix}.$$
The next question remains readable.

## Reference answers

The answer is $ad-bc$.`;
    const v = versionRead(IDS.v2, 2, body, []);
    const calls = await mockBackend(page, { snapshot: () => runSnapshot({ quality: v.quality }), versions: { [IDS.v2]: v } });
    await page.goto(`/tasks/${IDS.task}/read`);
    await expect(page.locator('.prose .math-block .katex')).toHaveCount(1);
    await expect(page.locator('.prose .math-fail')).toHaveCount(0);
    await expect(page.locator('.prose')).toContainText('The next question remains readable.');
    await expect(page.locator('.prose h2#reference-answers')).toHaveText('Reference answers#');
    // 2026-11-22 起首载会先 POST /session 配对（Agent2 修复首载 401 报错），
    // 故只断言除配对外无任何写请求。
    expect(calls.filter((c) => c.url !== "/session").every((c) => c.method === "GET")).toBe(true);
  });

  test("兼容 \\(...\\) 与 \\[...\\] 公式定界符并保留下标渲染", async ({ page }) => {
    const body = String.raw`# LaTeX 定界符

设 \(x_0\) 与 \(\frac{1}{2}\)，计算：

\[
\int_0^1 x^2\,dx.
\]`;
    const v = versionRead(IDS.v2, 2, body, []);
    await mockBackend(page, { snapshot: () => runSnapshot({ quality: v.quality }), versions: { [IDS.v2]: v } });
    await page.goto(`/tasks/${IDS.task}/read`);
    await expect(page.locator(".prose .math-inline .katex")).toHaveCount(2);
    await expect(page.locator(".prose .math-block .katex")).toHaveCount(1);
    await expect(page.locator(".prose .math-fail")).toHaveCount(0);
    await expect(page.getByLabel("公式：x_0")).toBeVisible();
  });

  test("连续解答题之间保留三行视觉间距", async ({ page }) => {
    const body = String.raw`# 专题

**1.** 第一道解答题。

$$
\int_0^1 x^2\,dx.
$$

**2.** 第二道解答题。

### 参考答案

**1.** 第一题答案。

**2.** 第二题答案。`;
    const v = versionRead(IDS.v2, 2, body, []);
    await mockBackend(page, { snapshot: () => runSnapshot({ quality: v.quality }), versions: { [IDS.v2]: v } });
    await page.goto(`/tasks/${IDS.task}/read`);
    const second = page.locator(".prose p.question-block").nth(1);
    await expect(second).toContainText("第二道");
    const spacing = await second.evaluate((node) => {
      const style = getComputedStyle(node);
      return { gap: Number.parseFloat(style.marginTop), line: Number.parseFloat(style.lineHeight) };
    });
    expect(spacing.gap).toBeGreaterThanOrEqual(spacing.line * 3 - 1);
    await expect(page.locator(".prose p.question-block")).toHaveCount(2);
    await expect(page.locator(".prose p", { hasText: "第一题答案" })).not.toHaveClass(/question-block/);
  });

  test("R01 表格：真实 DOM，宽表局部滚动，整页不溢出", async ({ page }) => {
    await open(page);
    const tables = page.locator(".prose table");
    await expect(tables).toHaveCount(2);
    await expect(tables.first().locator("thead tr")).toHaveCount(1);
    await expect(tables.first().locator("tbody tr")).toHaveCount(2);
    await expect(tables.first().locator("tbody tr").first().locator("td")).toHaveCount(3);
    const wide = page.locator(".table-scroll").nth(1);
    expect(await wide.evaluate((e) => e.scrollWidth > e.clientWidth)).toBe(true); // 宽表在自身容器内滚动
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
  });

  test("R02 代码块：pre/code=2，复制等于原 source", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await open(page);
    await expect(page.locator(".prose pre code")).toHaveCount(2 + 1 /* 含失败的 mermaid 降级块 */);
    const buttons = page.locator(".code-block [data-copy-source]");
    const src1 = 'def riemann(f, a, b, n):\n    dx = (b - a) / n\n    return sum(f(a + i * dx) for i in range(1, n + 1)) * dx   # 右端点';
    await buttons.nth(0).click();
    expect(await clip(page)).toBe(src1);
    await buttons.nth(1).click();
    expect(await clip(page)).toBe("中文  空格   与\n换行");
  });

  test("R03 公式：行内/块级成功，错误公式降级显示原文，可复制原 TeX", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await open(page);
    await expect(page.locator(".math-block .katex-display, .math-block .katex").first()).toBeVisible();
    expect(await page.locator(".math-inline .katex").count()).toBeGreaterThanOrEqual(3);
    await expect(page.locator(".math-fail")).toHaveCount(1);
    await expect(page.locator(".math-fail code")).toContainText("\\frac{1}{");
    await page.locator(".math-block").first().focus();
    await page.locator(".math-copy").first().click();
    expect(await clip(page)).toContain("S_n = \\sum_{i=1}^{n} f(x_i)\\,\\Delta x");
  });

  test("R04 callout / R06 块引用 / R05 任务列表 / R08 高亮", async ({ page }) => {
    await open(page);
    await expect(page.locator("aside.callout")).toHaveCount(1);
    await expect(page.locator("aside.callout .callout-title")).toHaveText("注释");
    await expect(page.locator("aside.callout")).toContainText("为什么取右端点");
    await expect(page.locator(".prose blockquote")).toHaveCount(1);
    await expect(page.locator(".prose blockquote")).toContainText("普通块引用");
    const boxes = page.locator('.prose input[type="checkbox"]');
    await expect(boxes).toHaveCount(2);
    expect(await boxes.nth(0).isChecked()).toBe(true);
    expect(await boxes.nth(1).isChecked()).toBe(false);
    await boxes.nth(1).check();
    await expect(boxes.nth(1)).toBeChecked();
    await boxes.nth(1).uncheck();
    await expect(boxes.nth(1)).not.toBeChecked();
    await expect(page.locator(".prose mark")).toHaveCount(2);
    await expect(page.locator(".prose mark").nth(0)).toHaveText("重点内容");
    await expect(page.locator(".prose pre mark")).toHaveCount(0);
  });

  test("R07 脚注：引用与返回链接各 1，正文出现 1 次", async ({ page }) => {
    await open(page);
    await expect(page.locator("a[data-footnote-ref]")).toHaveCount(1);
    await expect(page.locator("a[data-footnote-backref]")).toHaveCount(1);
    await expect(page.locator("section[data-footnotes]")).toContainText("脚注正文只出现一次");
    expect(await page.locator(".prose").innerText().then((t) => t.split("脚注正文只出现一次").length - 1)).toBe(1);
  });

  test("R09 Mermaid：合法图渲染，错误图降级为可复制源代码", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await open(page);
    await expect(page.locator(".mermaid-figure svg")).toHaveCount(1);
    await expect(page.locator(".mermaid-fallback")).toHaveCount(1);
    await page.locator(".mermaid-fallback [data-copy-source]").click();
    expect(await clip(page)).toBe("graph TD\n  A[这是一个语法错误的图 -->");
  });

  test("R10 标题与目录：4 个唯一锚点，目录项落到对应标题（窄屏经“目录”抽屉）", async ({ page }) => {
    await open(page);
    const ids = await page.locator(".prose h2[id], .prose h3[id]").evaluateAll((els) => els.map((e) => e.id));
    expect(new Set(ids).size).toBe(ids.length);
    const narrow = (page.viewportSize()?.width ?? 1440) < 1024;
    if (narrow) await page.getByRole("button", { name: "目录", exact: true }).click();
    const scope = narrow ? page.getByRole("dialog", { name: "目录" }) : page.locator(".ruler");
    await scope.locator(".tick", { hasText: "用代码验证" }).click();
    await expect(page).toHaveURL(/#.+/);
    const hash = new URL(page.url()).hash.slice(1);
    await expect(page.locator(`#${decodeURIComponent(hash)}`)).toContainText("用代码验证");
    if (narrow) await expect(page.getByRole("dialog", { name: "目录" })).toBeHidden(); // 跳转后抽屉关闭
  });

  test("R11 WikiLink：有效定位同版本标题，缺失目标明确显示，无悬空链接", async ({ page }) => {
    await open(page);
    await expect(page.locator("a.wiki-link")).toHaveCount(1);
    await expect(page.locator(".wiki-missing")).toContainText("目标不存在");
    expect(await page.locator('a[href^="#wiki"]').count()).toBe(0); // 没有残留的占位链接
    await expect(page.locator(".prose")).not.toContainText("[[");   // 悬空双链 = 0
  });

  test("R12 安全：脚本/事件属性/危险协议不执行，#标签不生效", async ({ page }) => {
    const { errors } = await open(page);
    await page.waitForTimeout(500);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
    await expect(page.locator(".prose script")).toHaveCount(0);
    await expect(page.locator('.prose [onerror], .prose img')).toHaveCount(0);
    await expect(page.locator('.prose a[href^="javascript:"]')).toHaveCount(0);
    await expect(page.locator(".prose")).toContainText("#不是标签 的文字");
    expect(errors.filter((e) => !/Parse error|语法|Failed to load resource/.test(e))).toEqual([]);
  });

  test("答案默认盖住，展开后 aria-expanded 同步；刷新后恢复盖住", async ({ page }) => {
    await open(page);
    const btn = page.locator(".answer-toggle");
    await expect(btn).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#answers")).toBeHidden();
    await btn.click();
    await expect(btn).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator("#answers")).toContainText("用黎曼和取极限");
    await page.reload();
    await page.waitForSelector(".prose h1");
    await expect(page.locator(".answer-toggle")).toHaveAttribute("aria-expanded", "false");
  });

  test("R13 整页无横向溢出，键盘可达阅读/下载", async ({ page }) => {
    await open(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
    const download = page.getByRole("button", { name: /下载 v2/ });
    await download.focus();
    await expect(download).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
  });
});

test.describe("问题定位 AT-03-R12 / AT-10", () => {
  for (const quote of ["答案：答案：合成结论", "答案：合成结论"]) {
    test(`旧答案标签跨来源高亮仍只显示一次：${quote}`, async ({ page }) => {
      const body = "# 合成练习\n\n### 参考答案\n\n**1.** 答案：答案：合成结论\n\n### 代码示例\n\n`答案：答案：保留原代码`";
      const issue = finding(IDS.f1, "合成位置标记", location(body, quote, IDS.v2));
      const version = versionRead(IDS.v2, 2, body, [issue]);
      await mockBackend(page, { snapshot: () => runSnapshot({ quality: version.quality }), versions: { [IDS.v2]: version } });
      await page.goto(`/tasks/${IDS.task}/read`);
      const answer = page.locator('.prose p').filter({ hasText: "合成结论" });
      await expect(answer).toContainText("答案：合成结论");
      await expect(answer).not.toContainText("答案：答案：");
      await expect(page.locator('.prose .pin')).toHaveCount(1);
      await expect(page.locator('.prose .pin')).toContainText("合成结论");
      await expect(page.locator('.prose code')).toContainText("答案：答案：保留原代码");
      await page.locator('.prose .pin').click();
      // 2026-11-22 用户决定舍弃问题栏入口：不再断言右栏问题条目高亮。
      await expect(page.locator('#issue-1')).toHaveCount(0);
    });
  }

  test("多字节区间命中原 quote；hash 不符的问题不渲染", async ({ page }) => {
    const ok = finding(IDS.f1, "这里的结论缺少依据", location(SAMPLE, "这里有==重点内容==", IDS.v2));
    const stale = { ...finding(IDS.f3, "过期的问题", location(SAMPLE, "近似面积", IDS.v2)), location: { ...location(SAMPLE, "近似面积", IDS.v2), span_hash: "0".repeat(64) } };
    await open(page, { findings: [ok, stale] });
    const pins = page.locator(".prose .pin");
    await expect(pins).toHaveCount(1);
    await expect(pins.first()).toContainText("这里有");
    await expect(page.locator(".prose .pin", { hasText: "近似面积" })).toHaveCount(0);
    // 2026-11-22 用户决定舍弃问题栏入口：仅断言坏 hash 不渲染、好 pin 可交互。
    await pins.first().click();
  });
});
