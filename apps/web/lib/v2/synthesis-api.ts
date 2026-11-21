import { act, get } from "./client";
import type { SynthesisAccepted, SynthesisMissing, SynthesisPreview, SynthesisRequest, SynthesisResult } from "./contracts.generated";
import type { VersionRead } from "./api";

export type SynthesisRead = SynthesisMissing | SynthesisResult;
type SourceIdentity = Pick<VersionRead, "version_id" | "content_hash" | "quality_hash">;
const TRACKED_RUNS = "aiedu.synthesis-runs.v1";
type TrackedRun = SourceIdentity & { task_id: string; run_id: string };

function trackedRuns(): TrackedRun[] {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(TRACKED_RUNS) || "[]");
    return Array.isArray(value) ? value.filter((v): v is TrackedRun =>
      Boolean(v) && ["task_id", "run_id", "version_id", "content_hash", "quality_hash"].every(
        (key) => typeof v[key] === "string" && v[key].length > 0)) : [];
  } catch { return []; }
}

/** Session storage is only a lookup hint; execution state is always read from the API. */
export function trackedSynthesisRun(taskId: string, source: SourceIdentity): string | null {
  return trackedRuns().find((v) => v.task_id === taskId && v.version_id === source.version_id &&
    v.content_hash === source.content_hash && v.quality_hash === source.quality_hash)?.run_id ?? null;
}

export function rememberSynthesisRun(taskId: string, source: SourceIdentity, runId: string): void {
  try {
    const previous = trackedRuns().filter((v) => v.task_id !== taskId || v.version_id !== source.version_id);
    sessionStorage.setItem(TRACKED_RUNS, JSON.stringify([...previous,
      { task_id: taskId, version_id: source.version_id, content_hash: source.content_hash,
        quality_hash: source.quality_hash, run_id: runId }].slice(-50)));
  } catch { /* The backend ACTIVE_RUN/CAS guard remains effective without browser storage. */ }
}

export const synthesis = {
  read: (taskId: string, versionId: string, signal?: AbortSignal) =>
    get<SynthesisRead>(`/tasks/${taskId}/versions/${versionId}/synthesis`, { signal }),
  preview: (taskId: string, runId: string, versionId: string) =>
    get<SynthesisPreview>(`/tasks/${taskId}/synthesis-source?${new URLSearchParams({ run_id: runId, version_id: versionId })}`),
  start: (taskId: string, body: SynthesisRequest, key: string) =>
    act<SynthesisAccepted>("POST", `/tasks/${taskId}/syntheses`, body,
      { kind: "synthesis", taskId, expectedRevision: body.expected_run_revision, key }),
};

export function matchesSource(value: SynthesisRead, read: Pick<VersionRead, "version_id" | "content_hash" | "quality_hash">): value is SynthesisResult {
  return value.status === "candidate" && value.source_version_id === read.version_id &&
    value.source_content_hash === read.content_hash && value.source_quality_hash === read.quality_hash;
}

export function exportSelection(value: SynthesisRead | null, read: Pick<VersionRead, "version_id" | "content_hash" | "quality_hash">) {
  return value && matchesSource(value, read) && value.source_is_current
    ? { revision: value.revision, result_hash: value.result_hash } : null;
}
