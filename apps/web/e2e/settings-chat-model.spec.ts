import { expect, test, type Page } from "@playwright/test";

test("settings persists four user-facing workflow model choices", async ({ page }: { page: Page }) => {
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
        { id: "planner", name: "GLM plan", model: "glm-5.3-flash", has_key: true, model_family: "glm" },
        { id: "generation", name: "DeepSeek", model: "DeepSeek-V4.1-Flash-HS", has_key: true, model_family: "deepseek" },
        { id: "quality", name: "Qwen", model: "qwen3.8-flash", has_key: true, model_family: "qwen" },
        { id: "chat-profile", name: "GLM chat", model: "glm-5.3-flash", has_key: true, model_family: "glm" },
      ] },
      config_hash: "fixture-hash",
      roles: {},
      role_assignments: {},
      role_profiles: [
        { id: "planner", name: "GLM plan", model: "glm-5.3-flash", model_family: "glm" },
        { id: "generation", name: "DeepSeek", model: "DeepSeek-V4.1-Flash-HS", model_family: "deepseek" },
        { id: "quality", name: "Qwen", model: "qwen3.8-flash", model_family: "qwen" },
        { id: "chat-profile", name: "GLM chat", model: "glm-5.3-flash", model_family: "glm" },
      ],
      workflow_role_assignments: {
        understanding_planning: "planner",
        generation_repair: "generation",
        quality: "quality",
        chat: "chat-profile",
      },
      workflow_roles: {
        understanding_planning: { configured: true, profile_id: "planner", model: "glm-5.3-flash", model_family: "glm", mixed: false },
        generation_repair: { configured: true, profile_id: "generation", model: "DeepSeek-V4.1-Flash-HS", model_family: "deepseek", mixed: false },
        quality: { configured: true, profile_id: "quality", model: "qwen3.8-flash", model_family: "qwen", mixed: false },
        chat: { configured: true, profile_id: "chat-profile", model: "glm-5.3-flash", model_family: "glm", mixed: false },
      },
    } });
    return route.fulfill({ json: {}, status: 404 });
  });
  await page.goto("/settings");
  for (const label of ["理解与规划", "生成与修复", "质检", "答疑"]) {
    await expect(page.getByRole("combobox", { name: label, exact: true })).toBeVisible();
  }
  await expect(page.getByText(/模型 ?A|模型 ?B|模型 ?C/)).toHaveCount(0);
  await page.getByRole("combobox", { name: "质检", exact: true }).selectOption("generation");
  await page.getByRole("button", { name: "保存模型分工" }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests).toEqual([{
    expected_config_hash: "fixture-hash",
    action: "workflow_roles",
    value: {
      understanding_planning: "planner",
      generation_repair: "generation",
      quality: "generation",
      chat: "chat-profile",
    },
  }]);
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




