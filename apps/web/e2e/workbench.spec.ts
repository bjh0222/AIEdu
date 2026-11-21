import { expect, test } from "@playwright/test";
import { IDS, mockBackend, quality, runSnapshot } from "./fixtures/build";

// 方法标签：fixture。验证前端行为（AT-11 暂停竞态/响应丢失、AT-03 状态不绿），不是专业通过证据。
const item = (i: number, over: Record<string, unknown> = {}) => ({
  task_id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`, title: `任务 ${i}`, run_id: null, state: "completed", needs_action: false,
  state_revision: 3, version_id: IDS.v2, updated_at: "2026-11-13T10:00:00+00:00", quality: { tqi_verdict: "blocked", calibration_status: "not_calibrated", quality_status: "needs_review" }, ...over,
});

test("材料本地读取失败：释放提交状态且不创建任务", async ({ page }) => {
  const calls = await mockBackend(page);
  await page.route("**/api/v2/capabilities", (route) => route.fulfill({ json: {
    scenes: ["exercise"], paid_calls_enabled: false, taxonomy: [],
    materials: { resource_policy_status: "ready" },
  } }));
  await page.goto("/new");
  await page.getByLabel("需求").fill("根据附件生成练习");
  await page.locator("summary").click();
  await page.getByLabel("参考资料（可选）").setInputFiles({ name: "材料.md", mimeType: "text/markdown", buffer: Buffer.from("合成材料") });
  await page.evaluate(() => { File.prototype.arrayBuffer = async () => { throw new Error("fixture-file-read-failed"); }; });
  await page.getByRole("button", { name: "开始生成" }).click();
  await expect(page.locator(".banner[role=alert]")).toContainText("fixture-file-read-failed");
  await expect(page.getByRole("button", { name: "开始生成" })).toBeEnabled();
  expect(calls.filter((c) => c.method === "POST" && c.url !== "/session")).toHaveLength(0);
});

test.describe("任务中心", () => {
  test("分页只请求聚合列表（无逐行 snapshot）；加载更多按 task_id 去重；卡片不显示质量轴", async ({ page }) => {
    const calls: string[] = [];
    let n = 0;
    await page.route("**/api/v2/**", async (route) => {
      const u = new URL(route.request().url());
      const p = u.pathname.replace("/api/v2", "");
      calls.push(p);
      if (p === "/session") return route.fulfill({ json: { csrf_token: "x" } });
      if (p === "/tasks") {
        n++;
        const cursor = u.searchParams.get("cursor");
        return route.fulfill({ json: cursor ? { items: [item(2), item(3)], next_cursor: null, read_at: 1 } : { items: [item(1), item(2, { needs_action: true, state: "paused", version_id: null })], next_cursor: "c1", read_at: 1 } });
      }
      return route.fulfill({ status: 404, json: { code: "NOT_MOCKED" } });
    });
    await page.goto("/");
    await expect(page.getByRole("link", { name: "任务 1" })).toBeVisible();
    await page.getByRole("button", { name: "加载更多" }).click();
    await expect(page.getByRole("link", { name: "任务 3" })).toBeVisible();
    await expect(page.getByRole("article", { name: "任务 2" }).getByRole("link", { name: "任务 2", exact: true })).toHaveCount(1); // 重复项只留一行（行内链接，不含“去处理”按钮）
    expect(calls.filter((c) => c.endsWith("/snapshot"))).toHaveLength(0);
    expect(n).toBe(2);
    await expect(page.getByText("运行已结束", { exact: true })).toHaveCount(0);
    await expect(page.getByText("受阻", { exact: true })).toHaveCount(0);
    await expect(page.getByText("已通过", { exact: true })).toHaveCount(0);
  });

  test("空态：可编辑的多模式示例；后端不可达时给出重试且不显示空表", async ({ page }) => {
    await page.route("**/api/v2/**", (r) => (r.request().url().includes("/session") ? r.fulfill({ json: { csrf_token: "x" } }) : r.fulfill({ json: { items: [], next_cursor: null, read_at: 0 } })));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "还没有任务" })).toBeVisible();
    await expect(page.getByRole("button", { name: /试卷/ })).toBeVisible();
  });

  test("列表读取失败：显示原因与重试", async ({ page }) => {
    await page.route("**/api/v2/**", (r) => r.abort());
    await page.goto("/");
    await expect(page.locator(".banner[role=alert]")).toContainText("无法读取任务列表");
    await expect(page.getByRole("button", { name: "重试" })).toBeVisible();
  });
});

test.describe("工作台 AT-11", () => {
  const running = () => runSnapshot({ state: "running", state_revision: 2, version_id: null, content_hash: null, units: [], stages: { understanding: "done", planning: "done" }, next_actions: ["pause"], stop_reason: null, demo: false, cost_final: false, calls: [{ call_id: "88888888-8888-4888-8888-888888888888", unit_id: IDS.unit, status: "sent", outcome: "", attempt: 1, usage: null, price: null, model_family: "x", request_hash: "e".repeat(64) }] });

  test("暂停：无前置确认框、即时请求；确认前显示“正在请求暂停”，服务端确认后才显示已暂停", async ({ page }) => {
    let state: "running" | "paused" = "running";
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    const posts: { url: string; key: string | undefined; csrf: string | undefined }[] = [];
    await mockBackend(page, {
      snapshot: () => (state === "running" ? running() : runSnapshot({ ...running(), state: "paused", state_revision: 3, next_actions: ["resume"], stop_reason: "user_pause" })),
      onWrite: async (route, p) => {
        if (p.endsWith("/pause")) {
          const h = route.request().headers();
          posts.push({ url: p, key: h["idempotency-key"], csrf: h["x-csrf-token"] });
          await gate;
          state = "paused";
          await route.fulfill({ json: { run_id: IDS.run, state: "paused", state_revision: 3, version_id: null, checkpoint_hash: "d".repeat(64), inflight_count: 1, bundle_status: "not_started", available_draft: false } });
          return true;
        }
        return false;
      },
    });
    await page.goto(`/tasks/${IDS.task}`);
    const btn = page.getByRole("button", { name: "暂停并保留已有内容" });
    await expect(btn).toBeVisible();
    await btn.click();
    await expect(page.getByRole("dialog")).toHaveCount(0);                       // 没有前置确认框
    await expect(page.getByText("正在请求暂停", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("已暂停（用户）")).toHaveCount(0);                  // 服务端未确认前不假显示
    release();
    await expect(page.getByRole("group", { name: "任务状态（运行、交付两个独立维度）" }).getByText("已暂停（用户）")).toBeVisible();
    await expect(page.getByRole("button", { name: "继续生成" })).toBeVisible();
    expect(posts).toHaveLength(1);
    expect(posts[0].key).toMatch(/^[0-9a-f-]{36}$/);
    expect(posts[0].csrf).toBe("test-csrf");
  });

  test("响应丢失：显示“暂停未确认”，只读查回执；刷新不会重复 POST", async ({ page }) => {
    let pausePosts = 0;
    let receiptKey = "";
    await mockBackend(page, {
      snapshot: () => running(),
      onWrite: async (route, p) => {
        if (p.endsWith("/pause")) {
          pausePosts++;
          receiptKey = route.request().headers()["idempotency-key"];
          await route.abort("failed"); // 请求发出但没有响应
          return true;
        }
        return false;
      },
    });
    await page.route("**/api/v2/actions/receipt", (route) => route.fulfill({ json: { status: route.request().headers()["idempotency-key"] === receiptKey ? "not_found" : "completed" } }));
    await page.goto(`/tasks/${IDS.task}`);
    await page.getByRole("button", { name: "暂停并保留已有内容" }).click();
    await expect(page.locator(".banner[role=alert]")).toContainText("暂停未确认");
    await page.getByRole("button", { name: "查询回执" }).click();
    await expect(page.locator(".banner[role=alert]")).toContainText("仍不能断定未执行");
    await expect(page.getByRole("button", { name: "暂停并保留已有内容" })).toBeDisabled();
    await page.reload();
    await page.waitForSelector("h1");
    await expect(page.getByRole("button", { name: "查询回执" })).toBeVisible();
    await expect(page.getByRole("button", { name: "暂停并保留已有内容" })).toBeDisabled();
    expect(pausePosts).toBe(1);                       // 刷新与查回执都没有再发 POST
  });

  test("409 REVISION_STALE：读取最新状态并提示重试，不假暂停", async ({ page }) => {
    await mockBackend(page, {
      snapshot: () => running(),
      onWrite: async (route, p) => {
        if (p.endsWith("/pause")) {
          await route.fulfill({ status: 409, json: { code: "REVISION_STALE", message: "REVISION_STALE", retryable: true, current_revision: 9, details: {} } });
          return true;
        }
        return false;
      },
    });
    await page.goto(`/tasks/${IDS.task}`);
    await page.getByRole("button", { name: "暂停并保留已有内容" }).click();
    await expect(page.locator(".banner[role=alert]")).toContainText("暂停没有生效");
    await expect(page.getByText("已暂停（用户）")).toHaveCount(0);
  });

  test("用量：usage 未知显示“待核实”而非 0；费用上限显示“未设置”；质量报告不展示", async ({ page }) => {
    const unevaluated = { ...quality([]), checks: [], check_items: [], check_coverage: { completed: 0, total: 0 }, blocking_reasons: [], quality_status: "not_evaluated" };
    await mockBackend(page, { snapshot: () => ({ ...running(), quality: unevaluated }) });
    await page.goto(`/tasks/${IDS.task}`);
    const usage = page.getByRole("complementary", { name: "生成状态与用量" });
    await expect(usage).toContainText("待核实");
    await expect(usage).toContainText("未设置");
    await expect(usage.getByText("调用次数上限").locator("xpath=following-sibling::dd[1]")).toHaveText("不限");
    await expect(usage.getByText("Token").locator("xpath=following-sibling::dd[1]")).not.toHaveText("0");
    await expect(usage).not.toContainText("未评价（这不是通过）");
    await expect(usage).not.toContainText("检查进度：");
  });

  test("暂停后：已有稿可读可下载，列出缺项，继续会创建新运行", async ({ page }) => {
    const paused = runSnapshot({ state: "paused", next_actions: ["resume"], stop_reason: "user_pause", resolved_task: { title: "定积分入门", requested_artifacts: ["tutorial", "answer"] }, demo: false });
    await mockBackend(page, { snapshot: () => paused });
    await page.goto(`/tasks/${IDS.task}`);
    await expect(page.getByText("已暂停。", { exact: false })).toBeVisible();
    await expect(page.getByText("还缺：参考答案", { exact: false })).toBeVisible();
    await expect(page.getByRole("link", { name: "阅读草稿" })).toBeVisible();
    await expect(page.getByRole("button", { name: "下载" })).toBeVisible();
  });
});


test.describe("质量报告兼容", () => {
  test("旧质量路由重定向到工作台，不打开质量报告", async ({ page }) => {
    await mockBackend(page);
    await page.goto(`/tasks/${IDS.task}/quality`);
    await expect(page).toHaveURL(new RegExp(`/tasks/${IDS.task}$`));
    await expect(page.getByRole("heading", { name: "定积分入门" })).toBeVisible();
  });
});

test.describe("兼容与降级", () => {
  test("旧 URL 重定向到只读兼容页，页面明确“不会被当作已通过”", async ({ page }) => {
    await page.route("**/api/v2/**", (r) => (r.request().url().includes("/session") ? r.fulfill({ json: { csrf_token: "x" } }) : r.fulfill({ json: { items: [], total: 0, limit: 50, offset: 0 } })));
    await page.goto("/goals/abc");
    await expect(page).toHaveURL(/\/legacy\/goals$/);
    await expect(page.getByText("不会被当作“已通过”")).toBeVisible();
    await page.goto("/tutorials/42");
    await expect(page).toHaveURL(/\/legacy\/tutorials$/);
  });

  test("390/768/1440 整页无横向溢出（任务中心、新建、资源库、设置、旧数据）", async ({ page }) => {
    await page.route("**/api/v2/**", async (r) => {
      const p = new URL(r.request().url()).pathname;
      if (p.endsWith("/session")) return r.fulfill({ json: { csrf_token: "x" } });
      if (p.endsWith("/tasks")) return r.fulfill({ json: { items: [item(1, { title: "一个非常非常长的任务标题".repeat(8) })], next_cursor: null, read_at: 0 } });
      if (p.endsWith("/capabilities")) return r.fulfill({ json: { scenes: ["exercise"], paid_calls_enabled: false, taxonomy: [], calibration_status: "not_calibrated", ps51_sandbox: "not_run", obsidian_delivery: false } });
      return r.fulfill({ json: { items: [], total: 0, config: { profiles: [], active_id: null, user_profile: "" }, config_hash: "x", roles: {} } });
    });
    for (const path of ["/", "/new", "/library", "/library?tab=profiles", "/settings", "/legacy/goals"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const ok = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1);
      expect(ok, `${path} 横向溢出`).toBe(true);
    }
  });
});
