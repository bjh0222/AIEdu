// 针对每个 v2 端点的薄封装，类型来自后端生成的契约（lib/v2/contracts.generated.ts，勿手改）。
import { act, get, getConditional } from "./client";
import type {
  Confirmation, ExportRequest, InputPatch, Pause, QuarantineUnknown, RepairRequest, Resume, RunSnapshot, DraftSnapshot, StartRun, TaskInput, TaskPage, TaskStart, UserReview, VersionPage,
} from "./contracts.generated";

export type Snapshot = RunSnapshot | DraftSnapshot;
export const isRun = (s: Snapshot): s is RunSnapshot => typeof (s as RunSnapshot).run_id === "string";

export type Capabilities = {
  scenes: string[];
  paid_calls_enabled: boolean;
  obsidian_delivery: boolean;
  calibration_status: string;
  ps51_sandbox: string;
  verifiers: Record<string, string>;
  taxonomy: { domain_key: string; display_name: string }[];
  repair_policy: Record<string, unknown>;
  role_configuration: Record<string, { configured: boolean; model?: string; reason?: string }>;
  generation: { available: boolean; requires_paid_permit: boolean };
  materials: { supported: string[]; unsupported: string[]; resource_policy_required: boolean; resource_policy_status?: string; resource_policy_reason?: string | null };
};

export type StartResult = { run_id: string | null; task_id: string; snapshot_url: string; conflicts?: unknown[]; task_state?: string; input_revision?: number; context_hash?: string };
export type PauseResult = { run_id: string; state: string; state_revision: number; version_id: string | null; checkpoint_hash: string; inflight_count: number; bundle_status: string; available_draft: boolean };
export type VersionUnit = { unit_id: string; artifact_role: string; artifact_roles?: string[]; body: string; content_hash: string; title?: string; items?: { ordinal: number; stem_markdown: string; score?: number; answer: { final: string; reasoning: string } }[] };
export type VersionRead = { version_id: string; version: number; content_hash: string; quality_hash: string;
  export_binding?: { run_id: string; task_id: string; state_revision: number; version_id: string; content_hash: string };
  content: { units: VersionUnit[]; artifacts?: string[] }; document_end_answers?: string;
  answer_projection?: { method_version: string; sha256: string; source_version_id: string }; quality: RunSnapshot["quality"] };
export type ExportManifest = {
  bundle_id: string; version_id: string; kind: string; renderer_version: string; bundle_hash: string; demo: boolean;
  delivery_status: "final" | "draft"; profile_binding: { status: string; stale_refs: unknown[] };
  quality_gate: { method: string; tqi_verdict_required: string; calibration_required: string; profile_binding_required: string; passed: boolean };
  legacy_role_projections: { file_id: string; unit_id: string; name: string; artifact_role: string; sha256: string; size: number; downloadable: boolean }[];
  files: { file_id: string; name: string; sha256: string; size: number; artifact_role?: string }[];
  missing_required: string[]; original_required: string[]; quality: { tqi_verdict: string; quality_status: string; calibration_status: string };
};

export const api = {
  capabilities: () => get<Capabilities>("/capabilities"),
  listTasks: (cursor: string | null, search: string, limit = 30, signal?: AbortSignal) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (cursor) q.set("cursor", cursor);
    if (search) q.set("search", search);
    return get<TaskPage>(`/tasks?${q}`, { signal });
  },
  snapshot: (taskId: string, etag: string | null, runId?: string | null, signal?: AbortSignal) =>
    getConditional<Snapshot>(`/tasks/${taskId}/snapshot${runId ? `?run_id=${runId}` : ""}`, etag, signal),
  taskStart: (body: TaskStart, key: string) => act<StartResult>("POST", "/task-starts", body, { kind: "task_start", key }),
  createTask: (body: TaskInput, key: string) => act<StartResult>("POST", "/tasks", body, { kind: "create_task", key }),
  patchInput: (taskId: string, body: InputPatch, key: string) => act<StartResult>("PATCH", `/tasks/${taskId}/input`, body, { kind: "patch_input", taskId, expectedRevision: body.expected_revision, key }),
  confirm: (taskId: string, body: Confirmation, key: string) => act<StartResult>("POST", `/tasks/${taskId}/confirm`, body, { kind: "confirm", taskId, expectedRevision: body.expected_revision, key }),
  startRun: (taskId: string, body: StartRun, key: string) => act<StartResult>("POST", `/tasks/${taskId}/runs`, body, { kind: "start_run", taskId, expectedRevision: body.expected_revision, key }),
  pause: (taskId: string, runId: string, body: Pause, key: string) => act<PauseResult>("POST", `/runs/${runId}/pause`, body, { kind: "pause", taskId, expectedRevision: body.expected_revision, key }),
  quarantineUnknown: (taskId: string, runId: string, body: QuarantineUnknown, key: string) => act<{ run_id: string; state: string; state_revision: number; checkpoint_hash: string; call_ids: string[]; scheduled: false; provider_settlement: "unresolved" }>("POST", `/runs/${runId}/unknown-quarantines`, body, { kind: "quarantine_unknown", taskId, expectedRevision: body.expected_revision, key }),
  resume: (taskId: string, runId: string, body: Resume, key: string) => act<{ run_id: string; parent_run_id: string }>("POST", `/runs/${runId}/resume`, body, { kind: "resume", taskId, expectedRevision: body.expected_revision, key }),
  version: (artifactId: string, versionId: string) => get<VersionRead>(`/artifacts/${artifactId}/versions/${versionId}`),
  versions: (taskId: string, artifactId: string, before?: number) => get<VersionPage>(`/tasks/${taskId}/versions?artifact_id=${encodeURIComponent(artifactId)}${before ? `&before_version=${before}` : ""}`),
  createExport: (artifactId: string, body: ExportRequest, key: string, taskId?: string) => act<ExportManifest>("POST", `/artifacts/${artifactId}/export-sets`, body, { kind: "export", taskId, expectedRevision: body.expected_revision, key }),
  userReview: (findingId: string, body: UserReview, key: string) => act<Record<string, unknown>>("POST", `/findings/${findingId}/user-reviews`, body, { kind: "user_review", expectedRevision: body.expected_revision, key }),
  repair: (findingId: string, body: RepairRequest, key: string) => act<{ run_id: string; finding_id: string }>("POST", `/findings/${findingId}/repairs`, body, { kind: "repair", expectedRevision: body.expected_revision, key }),
};
