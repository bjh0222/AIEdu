// 生成与后端同形状的 fixture 响应（形状来自 schema/task-run-v2.generated.ts 与隔离库的真实快照）。
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Page, Route } from "@playwright/test";

export const SAMPLE = readFileSync(path.join(__dirname, "sample.md"), "utf-8");
const sha = (b: Buffer | string) => createHash("sha256").update(b).digest("hex");

export const IDS = {
  task: "11111111-1111-4111-8111-111111111111",
  run: "22222222-2222-4222-8222-222222222222",
  artifact: "33333333-3333-4333-8333-333333333333",
  v1: "44444444-4444-4444-8444-444444444441",
  v2: "44444444-4444-4444-8444-444444444442",
  unit: "55555555-5555-4555-8555-555555555555",
  f1: "66666666-6666-4666-8666-666666666661",
  f2: "66666666-6666-4666-8666-666666666662",
  f3: "66666666-6666-4666-8666-666666666663",
};

export function location(body: string, quote: string, versionId: string) {
  const bytes = Buffer.from(body, "utf-8");
  const start = bytes.indexOf(Buffer.from(quote, "utf-8"));
  if (start < 0) throw new Error("quote not found: " + quote);
  const end = start + Buffer.byteLength(quote, "utf-8");
  return { unit_id: IDS.unit, version_id: versionId, unit_hash: sha(bytes), start_byte: start, end_byte: end, span_hash: sha(bytes.subarray(start, end)) };
}

export function finding(id: string, claim: string, loc: ReturnType<typeof location>, status = "confirmed") {
  return {
    finding_id: id, item_id: "C2.structure", check_plan_hash: "c".repeat(64), system: "general", severity_class: "blocker", location: loc,
    claim, evidence: { rule: "示例证据", quote: claim }, verification_method: "markdown.v1", verification_result: status === "confirmed" ? "violated" : "undecidable",
    method_version: "markdown.v1", attempt: 1, status, reviewed_by: null, review_evidence: [], context_conflict: false, category: "structure",
  };
}

export function quality(findings: unknown[] = [], verdict = "blocked") {
  const items = [
    { item_id: "C1.coverage", required: true, dimension: "C1.coverage", pass_condition: "所有必需单元都已保存", verifier_binding: "coverage.v1", severity_class: "blocker", applicability_reason: "general frozen task requirement" },
    { item_id: "C3.correctness", required: true, dimension: "C3.correctness", pass_condition: "没有已确认的严重事实错误", verifier_binding: "independent_oracle.v1", severity_class: "blocker", applicability_reason: "general frozen task requirement" },
    { item_id: "X.ps51", required: false, dimension: "PS5.1 执行", pass_condition: "在隔离环境中执行", verifier_binding: "ps51_sandbox.v1", severity_class: "major", applicability_reason: "仅 PowerShell 5.1 实践教程适用" },
  ];
  return {
    tqi_verdict: verdict, calibration_status: "not_calibrated", quality_status: verdict === "pass" ? "passed" : "needs_review", benchmark_build_status: "not_run",
    check_coverage: { completed: 1, total: 3 }, blocking_reasons: [{ item_id: "C3.correctness", result: "not_run" }], check_plan_hash: "c".repeat(64),
    check_items: items, checks: [{ item_id: "C1.coverage", result: "satisfied", method_version: "coverage.v1" }, { item_id: "C3.correctness", result: "not_run", reason: "independent verifier unavailable" }, { item_id: "X.ps51", result: "not_applicable" }],
    findings, repair_policy: { max_attempts_per_finding: 3, max_repair_rounds_per_run: 5 },
  };
}

export function unit(body: string) {
  return { unit_id: IDS.unit, artifact_role: "tutorial", artifact_roles: ["tutorial"], depends_on: [], body, content_hash: sha(Buffer.from(body, "utf-8")), blob_hash: sha(Buffer.from(body, "utf-8")), generation_state: "draft_ready", title: "定积分入门" };
}

export function runSnapshot(over: Record<string, unknown> = {}) {
  const u = unit(SAMPLE);
  const value = {
    schema_version: "aiedu.task-run.v2", task_id: IDS.task, input_revision: 1, run_id: IDS.run, root_job_id: "77777777-7777-4777-8777-777777777777", state_revision: 5,
    state: "completed", cancel_epoch: 0, demo: true, artifact_id: IDS.artifact, version_id: IDS.v2, content_hash: sha(SAMPLE), context_hash: "a".repeat(64), plan_hash: "b".repeat(64), checkpoint_hash: "d".repeat(64),
    resolved_task: { title: "定积分入门", raw_input: "给我一份定积分入门教程", requested_artifacts: ["tutorial"] },
    units: [u], stages: { generating: "done", checking: "deterministic_only", repairing: "not_applicable", verifying: "not_run" },
    quality: quality([]), budget: { calls: null, tokens: null, amount: null, seconds: null }, stop_reason: "demo_complete",
    calls: [{ call_id: "88888888-8888-4888-8888-888888888888", unit_id: IDS.unit, status: "settled", outcome: "success", attempt: 1, usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, usage_source: "not_applicable_demo" }, price: null, model_family: "demo", request_hash: "e".repeat(64), elapsed_seconds: 0.004 }],
    accounting_revision: "f".repeat(64), cost_final: true, next_actions: [], delivery_state: "not_started",
    delivery_detail: "固定草稿可读；这不表示质量已通过或可以最终交付。",
    delivery_decision: {
      state: "blocked", final_allowed: false, hard_blockers: [], soft_blockers: [],
      soft_item_ids: [], waiver_auto_enabled: true, waiver_status: "not_present",
      certificate_status: "not_present", delivery_calibration_status: "not_calibrated",
    },
    run_metrics: {
      method_version: "run-accounting-summary.v1", calls: 1, calls_started_or_settled: 1,
      tokens_known: 0, token_unknown_calls: 0, price_known_calls: 0, price_missing_calls: 0,
      elapsed_seconds: 0.004, retry_source_counts: { initial: 1 },
      stage_distribution: { generating: { calls: 1, tokens_known: 0, token_unknown_calls: 0, elapsed_seconds: 0.004 } },
    },
    ...over,
  };
  if (!("delivery_state" in over)) {
    const stage = Object.entries((value.stages ?? {}) as Record<string, string>).find(([, state]) => state === "running")?.[0];
    value.delivery_state = !value.version_id ? "not_started" :
      value.state === "running" && ["checking", "repairing", "verifying", "optimizing"].includes(stage ?? "") ? "quality_checking" :
      value.state === "completed" || value.state === "failed" || value.state === "interrupted" ? "quality_blocked" : "draft_ready";
  }
  return value;
}

export function versionRead(versionId: string, version: number, body: string, findings: unknown[]) {
  const u = unit(body);
  return { version_id: versionId, version, content_hash: sha(body), quality_hash: sha("q" + version), content: { artifacts: ["tutorial"], units: [u] }, quality: quality(findings) };
}

/** 注入后端基础路由：会话、能力、snapshot（含 ETag）、版本。 */
export async function mockBackend(page: Page, opts: { snapshot?: () => unknown; versions?: Record<string, unknown>; onWrite?: (route: Route, url: string, method: string) => Promise<boolean> | boolean } = {}) {
  const calls: { method: string; url: string; headers: Record<string, string> }[] = [];
  await page.route("**/api/v2/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname.replace("/api/v2", "");
    calls.push({ method: req.method(), url: p + url.search, headers: req.headers() });
    const json = (body: unknown, status = 200, headers: Record<string, string> = {}) => route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
    if (p === "/session") return json({ csrf_token: "test-csrf", owner: "task_owner", expires_in: 28800 });
    if (opts.onWrite && req.method() !== "GET" && (await opts.onWrite(route, p, req.method()))) return;
    if (p === "/capabilities") return json({ scenes: ["default_tutorial", "exercise", "exam_paper", "knowledge_tutorial", "practical_tutorial", "reading_guide"], paid_calls_enabled: false, obsidian_delivery: false, calibration_status: "not_calibrated", ps51_sandbox: "not_run", verifiers: {}, taxonomy: [{ domain_key: "math_logic", display_name: "数学、逻辑与统计" }], repair_policy: {}, role_configuration: {}, generation: { available: true, requires_paid_permit: true }, materials: { supported: [], unsupported: [], resource_policy_required: true } });
    if (p.endsWith("/knowledge-graph")) {
      const snapshot = (opts.snapshot ?? (() => runSnapshot()))() as ReturnType<typeof runSnapshot>;
      return json({ nodes: snapshot.units.map((u) => ({ id: u.unit_id, unit_id: u.unit_id, title: u.title, kind: "chapter", summary: null, status: "saved", version_id: snapshot.version_id, source_hash: u.content_hash })), edges: [], version_id: snapshot.version_id, demo: snapshot.demo, status: snapshot.state });
    }
    if (p === "/learning-profile") return json({ revision: 0, active: [], events: [], learning_gain: "not_measured" });
    if (p === "/knowledge/cards") return json({ items: [], total: 0, limit: 20, offset: 0 });
    if (p.endsWith("/cards")) return json({ cards: [], revision: 0, coverage: "not_run" });
    const synthesisVersion = /\/tasks\/[^/]+\/versions\/([^/]+)\/synthesis$/.exec(p);
    if (synthesisVersion) {
      const v = opts.versions?.[synthesisVersion[1]] as ReturnType<typeof versionRead> | undefined;
      return json({ task_id: IDS.task, version_id: synthesisVersion[1], content_hash: v?.content_hash ?? "",
        status: "not_generated", revision: 0, source_is_current: true });
    }
    if (p.endsWith("/snapshot")) {
      const body = JSON.stringify((opts.snapshot ?? (() => runSnapshot()))());
      const etag = `"${sha(body)}"`;
      if (req.headers()["if-none-match"] === etag) return route.fulfill({ status: 304, headers: { ETag: etag } });
      return route.fulfill({ status: 200, contentType: "application/json", headers: { ETag: etag }, body });
    }
    const m = /\/artifacts\/[^/]+\/versions\/([^/]+)$/.exec(p);
    if (/\/tasks\/[^/]+\/versions$/.test(p)) {
      const items = Object.values(opts.versions ?? {}).map((v) => ({ ...(v as ReturnType<typeof versionRead>), artifact_id: IDS.artifact })).sort((a, b) => b.version - a.version);
      return json({ items, next_before_version: null });
    }
    if (m && opts.versions?.[m[1]]) return json(opts.versions[m[1]]);
    if (p === "/tasks") return json({ items: [], next_cursor: null, read_at: 0 });
    return json({ code: "NOT_MOCKED", message: p }, 404);
  });
  return calls;
}
