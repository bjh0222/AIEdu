import { expect, test, type Page } from "@playwright/test";

test("settings persists the reader chat model through the model settings API", async ({ page }: { page: Page }) => {
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v2/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v2", "");
    if (path === "/session") return route.fulfill({ json: { csrf_token: "test-csrf", owner: "task_owner", expires_in: 28800 } });
    if (path === "/capabilities") return route.fulfill({ json: { scenes: ["default_tutorial"], paid_calls_enabled: false, obsidian_delivery: false, calibration_status: "not_calibrated", ps51_sandbox: "not_run", verifiers: {}, taxonomy: [], repair_policy: {}, generation: { available: true, requires_paid_permit: true }, materials: { supported: [], unsupported: [], resource_policy_required: true } } });
    if (path === "/model-settings" && route.request().method() === "PUT") {
      requests.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: { ok: true } });
    }
    if (path === "/model-settings") return route.fulfill({ json: {
      config: { active_id: "generation", user_profile: "", profiles: [
        { id: "generation", name: "主生成", model: "deepseek-generation", has_key: true, model_family: "deepseek" },
        { id: "chat-profile", name: "聊天配置", model: "chat-model", has_key: true, model_family: "chat-family" },
      ] },
      config_hash: "fixture-hash",
      roles: { generation: { configured: true, model: "deepseek-generation", model_family: "deepseek" } },
      role_assignments: { generation: "generation" },
      role_profiles: [
        { id: "generation", name: "主生成", model: "deepseek-generation", model_family: "deepseek" },
        { id: "chat-profile", name: "聊天配置", model: "chat-model", model_family: "chat-family" },
      ],
    } });
    return route.fulfill({ json: {}, status: 404 });
  });
  await page.goto("/settings");
  await expect(page.getByLabel("阅读页聊天")).toBeVisible();
  const options = await page.getByLabel("阅读页聊天").locator("option").allTextContents();
  expect(options[0]).toContain("默认主生成模型");
  await page.getByLabel("阅读页聊天").selectOption("chat-profile");
  await page.getByRole("button", { name: "保存聊天模型" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests).toEqual([{ expected_config_hash: "fixture-hash", action: "roles", value: { chat: "chat-profile" } }]);
});

test("delivery waiver preference is server-persisted through the CAS settings action", async ({ page }: { page: Page }) => {
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/v2/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v2", "");
    if (path === "/session") return route.fulfill({ json: { csrf_token: "test-csrf", owner: "task_owner", expires_in: 28800 } });
    if (path === "/capabilities") return route.fulfill({ json: { scenes: ["default_tutorial"], paid_calls_enabled: false, obsidian_delivery: false, calibration_status: "not_calibrated", ps51_sandbox: "not_run", verifiers: {}, taxonomy: [], repair_policy: {}, generation: { available: true, requires_paid_permit: true }, materials: { supported: [], unsupported: [], resource_policy_required: true } } });
    if (path === "/model-settings" && route.request().method() === "PUT") {
      requests.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: { applied_revision: 1, applied: true } });
    }
    if (path === "/model-settings") return route.fulfill({ json: {
      config: {
        active_id: null,
        user_profile: "",
        profiles: [],
        delivery_policy: { auto_waiver_enabled: true, source: "config_default" },
      },
      config_hash: "delivery-fixture-hash",
      roles: {},
      role_assignments: {},
      role_profiles: [],
    } });
    return route.fulfill({ json: {}, status: 404 });
  });
  await page.goto("/settings");
  await expect(page.getByLabel("额外输出上限（tokens）")).toHaveAttribute(
    "placeholder", "留空：无额外限制（仍受模型容量约束）",
  );
  const toggle = page.getByLabel("签最终交付时提示专业项放行");
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0]).toEqual({
    expected_config_hash: "delivery-fixture-hash",
    action: "delivery_policy",
    value: { auto_waiver_enabled: false },
  });
});




