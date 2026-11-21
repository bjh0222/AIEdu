import { act, get } from "./client";

import type { ManagedAction, ManagedPage, ManagedImpact, ManagedReceipt } from "./contracts.generated";
export type { ManagedResource, ManagedPage, ManagedImpact, ManagedReceipt, UnderstandingContext } from "./contracts.generated";
export type ManagedOperation = ManagedAction["action"];
export const RESOURCE_KINDS: Record<string, string> = {
  tasks: "任务与目标", materials: "资料", specs: "格式规范", batches: "命题批次", questions: "题目", evaluations: "外部评价",
  principles: "设计原理", results: "生成结果", versions: "固定版本", "quality-reports": "质量报告", exports: "导出集", "model-profiles": "模型配置",
};
export const OPERATION: Record<ManagedOperation, string> = {
  archive: "归档", restore: "恢复归档", label: "修改显示名称", trash: "移入回收站", restore_trash: "从回收站恢复", purge: "永久清除",
};
export const management = {
  cleanup(key: string) { return act<ManagedReceipt>("POST", "/managed-resource-cleanup", {}, { kind: "resource_management", key }); },
  catalog(kind: string, query: URLSearchParams, signal?: AbortSignal) { return get<ManagedPage>(`/managed-resources/${kind}?${query}`, { signal }); },
  impact(kind: string, id: string, signal?: AbortSignal) { return get<ManagedImpact>(`/managed-resources/${kind}/${encodeURIComponent(id)}/impact`, { signal }); },
  change(operation: ManagedOperation, impacts: ManagedImpact[], key: string, confirmation_names: Record<string, string>, label: string) {
    return act<ManagedReceipt>("POST", "/managed-resource-actions", {
      action: operation, acknowledge_references: true, confirmation_names, label: operation === "label" ? label : null,
      items: impacts.map((x) => ({ kind: x.resource.kind, id: x.resource.id, expected_hash: x.resource.resource_hash, impact_hash: x.impact_hash })),
    }, { kind: "resource_management", key });
  },
};

// Keys only: no content, model profiles or credentials are persisted here.
export function cleanupKeys(): string[] { try { return JSON.parse(sessionStorage.getItem("aiedu.cleanup.v1") || "[]"); } catch { return []; } }
export function rememberCleanup(key: string, unfinished: boolean) {
  try { sessionStorage.setItem("aiedu.cleanup.v1", JSON.stringify([...cleanupKeys().filter((k) => k !== key), ...(unfinished ? [key] : [])])); } catch { /* original receipt remains on the server */ }
}
