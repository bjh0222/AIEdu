import { expect, test, type Route } from "@playwright/test";
import { IDS, SAMPLE, mockBackend, runSnapshot, versionRead } from "./fixtures/build";

const draftManifest = {
  bundle_id: "99999999-9999-4999-8999-999999999999",
  version_id: IDS.v2,
  kind: "available_draft",
  delivery_status: "draft",
  renderer_version: "markdown.safe.merged.v6",
  bundle_hash: "1".repeat(64),
  demo: true,
  profile_binding: { status: "current", stale_refs: [] },
  quality_gate: {
    method: "strict_final_delivery.v1", tqi_verdict_required: "pass",
    calibration_required: "calibrated", profile_binding_required: "current", passed: false,
  },
  legacy_role_projections: [],
  files: [
    { file_id: "main", name: "draft-main.md", sha256: "2".repeat(64), size: 1024, artifact_role: "document" },
    { file_id: "bundle", name: "draft-bundle.zip", sha256: "3".repeat(64), size: 4096 },
  ],
  missing_required: [],
  original_required: ["tutorial"],
  quality: { tqi_verdict: "blocked", quality_status: "needs_review", calibration_status: "not_calibrated" },
};

const finalManifest = {
  ...draftManifest,
  kind: "complete",
  delivery_status: "final",
  files: [
    { file_id: "main", name: "main.md", sha256: "a".repeat(64), size: 1024, artifact_role: "document" },
    { file_id: "bundle", name: "bundle.zip", sha256: "b".repeat(64), size: 4096 },
  ],
  delivery_calibration_status: "waiver_authorized",
  quality: { tqi_verdict: "not_calibrated", quality_status: "needs_review", calibration_status: "not_calibrated" },
};

test("未通过质量门时拒绝 final，只提供明确草稿和合并主文档", async ({ page }, testInfo) => {
  const calls = await mockBackend(page, {
    versions: { [IDS.v2]: versionRead(IDS.v2, 2, SAMPLE, []) },
    onWrite: async (route: Route, path: string, method: string) => {
      if (method === "POST" && path.startsWith("/artifacts/") && path.endsWith("/export-sets")) {
        const kind = (route.request().postDataJSON() as { kind?: string }).kind;
        if (kind === "complete") {
          await route.fulfill({ status: 422, json: {
            code: "FINAL_EXPORT_NOT_PASSED", message: "FINAL_EXPORT_NOT_PASSED", retryable: false, details: {},
          } });
          return true;
        }
        if (kind === "available_draft") {
          await route.fulfill({ json: draftManifest });
          return true;
        }
      }
      return false;
    },
  });
  await page.goto(`/tasks/${IDS.task}`);
  await page.getByRole("button", { name: "下载" }).click();
  await expect(page.getByRole("dialog")).toContainText("下载 当前版本");
  await page.getByRole("button", { name: "生成下载包" }).click();
  await expect(page.getByText("不能作为最终交付。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下载已有草稿" }).click();
  await expect(page.getByText("草稿交付。")).toBeVisible();
  await expect(page.getByText("请勿当作已完成教程使用。")).toBeVisible();
  await expect(page.getByText("全部章节（一份 Markdown）")).toBeVisible();
  await expect(page.getByText("全部文件（zip）")).toBeVisible();
  await expect(page.getByRole("dialog").locator("li", { hasText: "定积分" })).toHaveCount(0);
  expect(calls.filter((call) => call.method === "POST" && call.url.endsWith("/export-sets"))).toHaveLength(2);
  await page.screenshot({ path: testInfo.outputPath("draft-delivery.png"), fullPage: true });
});

test("软阻塞只显示用户可理解的专业项；确认后先写授权再单独导出", async ({ page }) => {
  let completeAttempts = 0;
  const writes: string[] = [];
  const snapshot = runSnapshot({
    delivery_decision: {
      state: "waiver_required",
      final_allowed: false,
      hard_blockers: [],
      soft_blockers: [
        { item_id: "C3.correctness", result: "undecidable" },
        { item_id: "G2.knowledge_integration", result: "undecidable" },
      ],
      soft_item_ids: ["C3.correctness", "G2.knowledge_integration"],
      waiver_auto_enabled: true,
      waiver_status: "not_present",
      certificate_status: "calibrated",
      delivery_calibration_status: "not_calibrated",
    },
  });
  await mockBackend(page, {
    snapshot: () => snapshot,
    versions: { [IDS.v2]: versionRead(IDS.v2, 2, SAMPLE, []) },
    onWrite: async (route: Route, path: string, method: string) => {
      writes.push(path);
      if (path.endsWith("/delivery-waivers")) {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        expect(body.action).toBe("accept");
        expect(body.soft_item_ids).toEqual(["C3.correctness", "G2.knowledge_integration"]);
        await route.fulfill({ json: {
          run_id: IDS.run,
          action: "accept",
          state_revision: 6,
          waiver: { status: "active", waiver_hash: "c".repeat(64) },
          delivery_decision: { state: "eligible", final_allowed: true, delivery_calibration_status: "waiver_authorized" },
        } });
        return true;
      }
      if (method === "POST" && path.startsWith("/artifacts/") && path.endsWith("/export-sets")) {
        completeAttempts++;
        if (completeAttempts === 1) {
          await route.fulfill({ status: 409, json: {
            code: "FINAL_WAIVER_CONFIRM_REQUIRED",
            message: "FINAL_WAIVER_CONFIRM_REQUIRED",
            retryable: false,
            details: { delivery_decision: {
              hard_blockers: [],
              soft_item_ids: ["C3.correctness", "G2.knowledge_integration"],
            } },
          } });
        } else {
          await route.fulfill({ json: finalManifest });
        }
        return true;
      }
      return false;
    },
  });
  await page.goto(`/tasks/${IDS.task}`);
  await page.getByRole("button", { name: "下载" }).click();
  await page.getByRole("button", { name: "生成下载包" }).click();
  await expect(page.getByText("专业正确性：无法判定")).toBeVisible();
  await expect(page.getByText("知识整合：仍需人工确认")).toBeVisible();
  await expect(page.getByText("C3.correctness")).toHaveCount(0);
  const accept = page.getByRole("button", { name: "放行并生成最终交付" });
  await expect(accept).toBeDisabled();
  await page.getByLabel("我理解上述专业限制，并要求按当前固定版本生成最终交付").check();
  await accept.click();
  await expect.poll(() => writes.filter((p) => p.endsWith("/delivery-waivers")).length).toBe(1);
  await expect(page.getByText("全部章节（一份 Markdown）")).toBeVisible();
  await expect(page.getByText("草稿交付。")).toHaveCount(0);
  expect(completeAttempts).toBe(2);
  expect(writes.findIndex((p) => p.endsWith("/delivery-waivers"))).toBeLessThan(
    writes.lastIndexOf(writes.filter((p) => p.endsWith("/export-sets")).at(-1) ?? ""),
  );
});

test("答案角色保留为一份参考答案投影，不变成章节文件", async ({ page }) => {
  const manifest = {
    ...draftManifest,
    kind: "available_draft",
    files: [
      { file_id: "main", name: "draft-main.md", sha256: "4".repeat(64), size: 2048, artifact_role: "document" },
      { file_id: "answers", name: "draft-answers.md", sha256: "5".repeat(64), size: 1024, artifact_role: "answer" },
      { file_id: "bundle", name: "draft-bundle.zip", sha256: "6".repeat(64), size: 8192 },
    ],
    legacy_role_projections: [
      { file_id: `${IDS.unit}-exercise`, unit_id: IDS.unit, name: `${IDS.unit}-exercise.md`, artifact_role: "exercise", sha256: "7".repeat(64), size: 1024, downloadable: false },
      { file_id: `${IDS.unit}-answer`, unit_id: IDS.unit, name: `${IDS.unit}-answer.md`, artifact_role: "answer", sha256: "8".repeat(64), size: 512, downloadable: false },
    ],
    original_required: ["exercise", "answer"],
  };
  await mockBackend(page, {
    snapshot: () => runSnapshot({ resolved_task: { requested_artifacts: ["exercise", "answer"] } }),
    versions: { [IDS.v2]: versionRead(IDS.v2, 2, SAMPLE, []) },
    onWrite: async (route: Route, path: string, method: string) => {
      if (method === "POST" && path.startsWith("/artifacts/") && path.endsWith("/export-sets")) {
        await route.fulfill({ json: manifest });
        return true;
      }
      return false;
    },
  });
  await page.goto(`/tasks/${IDS.task}`);
  await page.getByRole("button", { name: "下载" }).click();
  await page.getByRole("button", { name: "生成下载包" }).click();
  await expect(page.getByText("全部章节（一份 Markdown）")).toBeVisible();
  await expect(page.getByText("参考答案（一份 Markdown）")).toBeVisible();
  await expect(page.locator("li", { hasText: `${IDS.unit}-exercise` })).toHaveCount(0);
  await expect(page.locator("li", { hasText: `${IDS.unit}-answer` })).toHaveCount(0);
});

test("统一答案消费根级固定版本投影，不从章节正文或错误content字段重建", async ({ page }) => {
  const body = "## 方法章节\n\n这里只包含题面。";
  const read = { ...versionRead(IDS.v2, 2, body, []), document_end_answers: "## 统一参考答案\n\n合成固定投影答案", answer_projection: {
    method_version: "task-adaptive.v1", source_version_id: IDS.v2, sha256: "f".repeat(64) } };
  await mockBackend(page, { versions: { [IDS.v2]: read } });
  await page.goto(`/tasks/${IDS.task}/read`);
  const button = page.getByRole("button", { name: "显示统一参考答案", exact: true });
  await expect(button).toBeVisible();
  await expect(page.getByText("合成固定投影答案", { exact: true })).toHaveCount(0);
  await button.click();
  await expect(page.getByText("合成固定投影答案", { exact: true })).toBeVisible();
});
