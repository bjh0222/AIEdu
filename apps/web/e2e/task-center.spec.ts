import { expect, test } from "@playwright/test";

const task = (id: string, title: string, over: Record<string, unknown> = {}) => ({
  task_id: id, title, run_id: null, state: "completed", needs_action: false,
  state_revision: 1, version_id: null, updated_at: "2026-11-20T00:00:00Z",
  quality: { tqi_verdict: "blocked", calibration_status: "not_calibrated", quality_status: "needs_review" }, ...over,
});

const ids = [
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
  "33333333-3333-4333-8333-333333333333",
];

async function mockTasks(page: import("@playwright/test").Page, items: unknown[]) {
  await page.route("**/api/v2/tasks**", (route) => route.fulfill({ json: { items, next_cursor: null, read_at: 0 } }));
}

test.describe("任务中心卡片", () => {
  test.use({ viewport: { width: 1280, height: 900 } });

  test("卡片保留最少状态与全部操作，隐藏筛选/状态/更新列", async ({ page }) => {
    const items = [
      task(ids[0], "需要处理的任务", { needs_action: true, state: "paused" }),
      task(ids[1], "可以阅读的任务", { version_id: "v1" }),
      task(ids[2], "演示任务", { demo: true }),
    ];
    await mockTasks(page, items);
    await page.goto("/");
    await expect(page.getByRole("article", { name: "需要处理的任务" })).toContainText("需要你处理");
    await expect(page.getByRole("article", { name: "可以阅读的任务" })).toContainText("可阅读");
    await expect(page.getByRole("article", { name: "演示任务" })).toContainText("演示");
    await expect(page.getByRole("article", { name: "需要处理的任务" }).getByRole("link", { name: "去处理" })).toBeVisible();
    await expect(page.getByRole("article", { name: "可以阅读的任务" }).getByRole("link", { name: "阅读", exact: true })).toBeVisible();
    await expect(page.getByRole("article", { name: "演示任务" }).getByRole("button", { name: "将演示任务移入回收站" })).toBeVisible();

    await expect(page.getByRole("group", { name: "筛选" })).toHaveCount(0);
    for (const label of ["全部", "待处理", "进行中", "已结束"]) {
      await expect(page.getByRole("button", { name: label, exact: true })).toHaveCount(0);
    }
    await expect(page.locator("table.data")).toHaveCount(0);
    await expect(page.getByText("运行已结束", { exact: true })).toHaveCount(0);
    await expect(page.getByText("受阻", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("group", { name: /运行.*质量.*交付/ })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "回收站", exact: true })).toHaveAttribute("href", "/library?tab=trash");
  });

  test("三张卡片先填满左列再进右列，默认按更新时间倒序", async ({ page }) => {
    const items = [
      task(ids[0], "较旧", { updated_at: "2026-11-20T00:00:00Z" }),
      task(ids[1], "最新", { updated_at: "2026-11-21T00:00:00Z", version_id: "v1" }),
      task(ids[2], "中间", { updated_at: "2026-11-20T12:00:00Z" }),
    ];
    await mockTasks(page, items);
    await page.goto("/");
    const grid = page.getByLabel("任务卡片");
    await expect(grid).toBeVisible();
    const style = await grid.evaluate((el) => {
      const css = getComputedStyle(el);
      return { columns: css.gridTemplateColumns.split(" "), flow: css.gridAutoFlow, rows: css.gridTemplateRows.split(" ") };
    });
    expect(style.columns).toHaveLength(2);
    expect(style.flow).toBe("column");
    expect(style.rows).toHaveLength(2);
    const boxes = [];
    for (const title of ["最新", "中间", "较旧"]) {
      boxes.push(await page.getByRole("article", { name: title }).boundingBox());
    }
    expect(boxes.every(Boolean)).toBe(true);
    expect(Math.abs(boxes[0]!.x - boxes[1]!.x)).toBeLessThan(4);
    expect(Math.abs(boxes[2]!.x - boxes[0]!.x)).toBeGreaterThan(100);
  });

  test("通知和标题搜索保留", async ({ page }) => {
    const allItems = [
      task(ids[0], " Alpha", { needs_action: true }),
      task(ids[1], "Beta", { version_id: "v1" }),
    ];
    await page.route("**/api/v2/tasks**", (route) => {
      const search = new URL(route.request().url()).searchParams.get("search") ?? "";
      const filtered = search ? allItems.filter((item) => item.title.includes(search)) : allItems;
      return route.fulfill({ json: { items: filtered, next_cursor: null, read_at: 0 } });
    });
    await page.goto("/");
    await expect(page.getByText("有 1 个任务需要你处理：")).toBeVisible();
    await page.getByLabel("按标题搜索").fill("Beta");
    await expect(page.getByRole("article", { name: "Beta" })).toBeVisible();
    await expect(page.getByRole("article", { name: " Alpha" })).toHaveCount(0);
  });
});

test.describe("窄屏任务卡片", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("窄屏退化为单列", async ({ page }) => {
    await mockTasks(page, [task(ids[0], "第一"), task(ids[1], "第二"), task(ids[2], "第三")]);
    await page.goto("/");
    const grid = page.getByLabel("任务卡片");
    const style = await grid.evaluate((el) => {
      const css = getComputedStyle(el);
      return { columns: css.gridTemplateColumns.split(" "), flow: css.gridAutoFlow, rows: css.gridTemplateRows.split(" ") };
    });
    expect(style.columns).toHaveLength(1);
    expect(style.flow).toBe("row");
    const first = await page.getByRole("article", { name: "第一" }).boundingBox();
    const third = await page.getByRole("article", { name: "第三" }).boundingBox();
    expect(Math.abs(first!.x - third!.x)).toBeLessThan(4);
    expect(third!.y).toBeGreaterThan(first!.y);
  });
});

test.describe("新建任务", () => {
  test("选择格式规范后需求文本框保持空白", async ({ page }) => {
    await page.route("**/api/v2/capabilities", (route) => route.fulfill({ json: {
      scenes: ["default_tutorial", "exercise", "exam_paper", "knowledge_tutorial", "practical_tutorial", "reading_guide"],
      paid_calls_enabled: false, obsidian_delivery: false, calibration_status: "not_calibrated", ps51_sandbox: "not_run",
      verifiers: {}, taxonomy: [], repair_policy: {}, role_configuration: {}, generation: { available: true, requires_paid_permit: true },
      materials: { supported: [], unsupported: [], resource_policy_required: true },
    } }));
    await page.goto("/new");
    await page.getByText("自动匹配与高级选项").click();
    const raw = page.getByLabel("需求");
    await expect(raw).toHaveValue("");
    await page.getByLabel("模式").selectOption("exercise");
    await expect(raw).toHaveValue("");
    await page.getByLabel("模式").selectOption("");
    await expect(raw).toHaveValue("");
  });

  test("?example= 仍作为用户主动示例填充", async ({ page }) => {
    await page.goto("/new?example=" + encodeURIComponent("示例需求原话"));
    await expect(page.getByLabel("需求")).toHaveValue("示例需求原话");
  });
});

test.describe("质量报告移除", () => {
  test("旧质量路由重定向到工作台", async ({ page }) => {
    await page.route("**/api/v2/tasks/*/snapshot**", (route) => route.fulfill({ json: {
      schema_version: "aiedu.task-run.v2", task_id: ids[0], run_id: ids[1], root_job_id: ids[2], state_revision: 1,
      state: "draft", demo: true, artifact_id: ids[2], version_id: null, content_hash: null, units: [], stages: {},
      quality: { tqi_verdict: "blocked", calibration_status: "not_calibrated", quality_status: "not_evaluated" },
      budget: {}, calls: [], accounting_revision: "0", cost_final: true, next_actions: [],
    } }));
    await page.goto(`/tasks/${ids[0]}/quality`);
    await expect(page).toHaveURL(new RegExp(`/tasks/${ids[0]}$`));
    await expect(page.getByRole("link", { name: "质量报告" })).toHaveCount(0);
    await expect(page.getByText("检查进度：")).toHaveCount(0);
    await expect(page.getByRole("group", { name: /运行、质量/ })).toHaveCount(0);
  });
});
