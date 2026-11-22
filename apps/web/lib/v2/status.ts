// 三轴状态与检查结果的展示映射。只做“翻译”，不做归约：质量总态只来自后端 reducer。
import type { RunSnapshot } from "./contracts.generated";

export type Shape = "hollow-circle" | "solid-circle" | "pause" | "solid-square" | "hollow-square" | "diamond" | "hollow-diamond" | "dashed-circle" | "hatched-square" | "half-square";
export type Tone = "neutral" | "primary" | "warn" | "ok" | "danger";
export type Badge = { label: string; shape: Shape; tone: Tone; pulse?: boolean; hint?: string };

export const STAGE_LABEL: Record<string, string> = {
  researching: "研究量规",
  understanding: "理解需求",
  planning: "规划结构",
  generating: "撰写",
  checking: "检查",
  repairing: "修复",
  verifying: "复验",
  exporting: "整理与导出",
  postprocess: "整理与导出",
  benchmark: "基准构建",
  knowledge: "知识分析",
};
export const STAGE_ORDER = ["researching", "understanding", "planning", "generating", "checking", "repairing", "verifying", "exporting"];

export function activeRunStage(run: Pick<RunSnapshot, "state" | "stages" | "calls">): string | undefined {
  if (run.state !== "running" && run.state !== "queued") return undefined;
  const aliases: Record<string, string> = { arrangement_alignment: "planning", generating_source_continuation: "generating", blind_solving: "checking", item_reviewing: "checking", checking_perturbation: "checking", selective_review: "checking", rubric_reviewing: "checking", import_understanding: "understanding", optimizing: "repairing", optimization_prompt: "repairing", optimization: "repairing", optimization_acceptance: "verifying", knowledge_cards: "knowledge" };
  const active = run.calls.find((c) => c.status === "sent" || c.status === "authorized");
  if (typeof active?.stage === "string") return active.stage.startsWith("research_") ? "researching" : aliases[active.stage] ?? active.stage;
  return [...STAGE_ORDER].reverse().find((s) => run.stages[s] === "running");
}

/** stage 取值的用户语言；`done` 只表示“这一步跑完”，不带质量含义。 */
export function stageText(value: string | undefined): string {
  switch (value) {
    case undefined:
    case "":
      return "尚未开始";
    case "done":
      return "这一步已跑完（不代表质量通过）";
    case "running":
      return "进行中";
    case "deterministic_only":
      return "只做了确定性检查（没有独立语义检查）";
    case "not_run":
      return "尚未执行";
    case "not_applicable":
      return "这次不需要";
    case "stopped":
      return "已停止，保留最佳草稿";
    default:
      return value;
  }
}

export function runBadge(state: string | undefined, stage?: string): Badge {
  switch (state) {
    case "queued":
      return { label: "排队中", shape: "hollow-circle", tone: "neutral" };
    case "running":
      return { label: stage ? `${STAGE_LABEL[stage] ?? "运行"}中` : "运行中", shape: "solid-circle", tone: "primary", pulse: true };
    case "paused":
      return { label: "已暂停（用户）", shape: "pause", tone: "warn" };
    case "completed":
      return { label: "运行已结束", shape: "solid-square", tone: "ok", hint: "只表示运行结束，不表示质量通过" };
    case "failed":
      return { label: "运行失败", shape: "diamond", tone: "danger" };
    case "interrupted":
      return { label: "已中断（可恢复）", shape: "hollow-diamond", tone: "danger" };
    case "awaiting_input":
      return { label: "需要你确认", shape: "hollow-square", tone: "warn" };
    case "draft":
      return { label: "草稿（未启动）", shape: "hollow-square", tone: "neutral" };
    case "confirmed":
      return { label: "已确认（未启动）", shape: "hollow-circle", tone: "neutral" };
    default:
      return { label: state ? `状态：${state}` : "未知", shape: "dashed-circle", tone: "neutral" };
  }
}

type Q = { tqi_verdict?: string | null; calibration_status?: string | null; quality_status?: string | null } | null | undefined;

/** 质量轴：以 TQI-E 五态为主；旧 quality_status 仅在没有五态时兜底（不反推校准）。 */
export function qualityBadge(q: Q): Badge {
  const v = q?.tqi_verdict;
  switch (v) {
    case "pass":
      return { label: "已通过", shape: "solid-square", tone: "ok" };
    case "conditional_pass":
      return { label: "有条件通过", shape: "half-square", tone: "warn" };
    case "needs_repair":
      return { label: "需要修复", shape: "diamond", tone: "danger" };
    case "blocked":
      return { label: "受阻：必需检查未完成", shape: "hatched-square", tone: "warn", hint: "必需项未执行或无法自动验证，不是通过" };
    case "not_calibrated":
      return { label: "未校准参考", shape: "hollow-square", tone: "warn", hint: "检查已完成，但检查器尚未校准，不能签优秀" };
  }
  switch (q?.quality_status) {
    case "not_evaluated":
      return { label: "未评价（不是通过）", shape: "dashed-circle", tone: "neutral" };
    case "needs_review":
      return { label: "待复核", shape: "hollow-square", tone: "warn" };
    case "failed":
      return { label: "未通过", shape: "diamond", tone: "danger" };
    case "passed":
      return { label: "已通过（旧口径）", shape: "solid-square", tone: "ok" };
  }
  return { label: "未评价（不是通过）", shape: "dashed-circle", tone: "neutral" };
}

export function calibrationText(status: string | null | undefined): string {
  if (!status || status === "not_calibrated") return "检查器未校准：结论只作工程参考，不能据此认定优秀";
  if (status === "calibrated") return "已校准（仅限证书声明的学科/模式范围）";
  return `校准状态：${status}`;
}

type DeliverySnapshot = Pick<RunSnapshot, "version_id" | "state"> & { delivery_state?: RunSnapshot["delivery_state"] };
export function deliveryBadge(snapshot: DeliverySnapshot | null | undefined, exportReady?: boolean, partial?: boolean): Badge {
  switch (snapshot?.delivery_state) {
    case "final_ready":
      return { label: "final 就绪", shape: "solid-square", tone: "ok", hint: "固定版本满足最终交付门" };
    case "quality_checking":
      return { label: "质检进行中", shape: "solid-circle", tone: "primary", pulse: true, hint: "草稿已可读；耗时来自复核或整改" };
    case "quality_blocked":
      return { label: "质检受阻", shape: "hatched-square", tone: "warn", hint: "草稿可读，但最终交付门尚未满足" };
    case "draft_ready":
      return { label: "草稿已就绪", shape: "hollow-square", tone: "neutral", hint: "可阅读和下载草稿，不等于质量通过" };
    case "not_started":
      return { label: "尚无草稿", shape: "dashed-circle", tone: "neutral" };
  }
  if (exportReady) return { label: "固定下载包可用", shape: "solid-square", tone: "ok", hint: "绑定固定版本，不等于质量通过" };
  if (partial) return { label: "部分可用", shape: "half-square", tone: "warn" };
  if (snapshot?.version_id) return { label: "已保存（可读）", shape: "hollow-square", tone: "neutral" };
  return { label: "尚无已保存内容", shape: "dashed-circle", tone: "neutral" };
}

// ---- 分项检查 ----
export type CheckView = { label: string; shape: Shape; tone: Tone };
export function checkView(result: string | undefined, reason?: string): CheckView {
  switch (result) {
    case "satisfied":
      return { label: "通过", shape: "solid-square", tone: "ok" };
    case "violated":
      return { label: "未通过", shape: "diamond", tone: "danger" };
    case "undecidable":
      return reason === "no_method" || reason === "needs_learner" || reason === "TARGET_RUNTIME_ISOLATION_UNVERIFIED"
        ? { label: "无法自动验证", shape: "hatched-square", tone: "neutral" }
        : { label: "待复核", shape: "hollow-square", tone: "warn" };
    case "not_run":
      return { label: "尚未执行", shape: "dashed-circle", tone: "neutral" };
    case "error":
      return { label: "检查出错", shape: "hollow-diamond", tone: "danger" };
    case "not_applicable":
      return { label: "不适用", shape: "hollow-circle", tone: "neutral" };
    default:
      return { label: result ? `未知结果：${result}` : "尚未执行", shape: "dashed-circle", tone: "neutral" };
  }
}

export const REASON_TEXT: Record<string, string> = {
  optimization_completed_uncalibrated: "有界优化及原条件复验已结束，专业质量仍未认证",
  diagnostic_regression: "候选出现逐维退化，已保留此前最佳草稿",
  regression_or_unresolved: "候选存在回归或未解决问题，已保留此前版本",
  optimization_acceptance_unresolved: "候选未满足原验收条件，已保留此前版本",
  optimization_round_limit: "优化达到轮次上限，保留最佳草稿",
  optimization_attempt_limit: "同一问题达到尝试上限，保留最佳草稿",
  no_progress: "当前策略没有可验证进展，保留最佳草稿",
  CALL_BUDGET_EXHAUSTED: "模型调用预算已用尽，保留已保存内容",
  independent_verification_required: "生成检查已结束，仍需独立质量证据",
  "independent verifier unavailable": "没有可用的独立验证器",
  PENDING_AUTOMATED_ASSESSMENT: "条款复核待执行",
  CRITERION_ASSESSMENT_NOT_STARTED: "条款复核尚未开始",
  ASSESSMENT_BINDING_INCOMPLETE: "条款复核结果未通过绑定校验",
  ASSESSMENT_ROWS_MISSING: "条款复核结果缺少绑定行",
  INPUT_HASH_MISMATCH: "检查依据的正文与当前版本不一致",
  no_method: "目前没有对应的自动验证方法",
  needs_learner: "需要真实学习者验证",
  TARGET_RUNTIME_ISOLATION_UNVERIFIED: "目标运行环境没有经验证的隔离执行器，不会执行宿主脚本",
  PERTURBATION_DISAGREEMENT: "多次独立判断不一致",
  LOCATION_OR_SCHEMA_INVALID: "检查器给出的位置或格式无效",
  model_candidate_is_not_professional_certification: "模型意见不等于专业认证",
  external_defect_verified: "已由外部证据核实",
};
export function reasonText(code?: string | null): string {
  if (!code) return "";
  if (code === "CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED") return "单项质检请求仍未收到完整响应，已停止继续拆分或自动重试；需要检查模型配置或执行策略，旧调用结果与费用仍待核实";
  if (code === "UNKNOWN_CALL") return "模型调用已发出但结果不明，系统不会自动重发，需要先核实或显式隔离";
  if (code === "PROVIDER_CALL_FAILED") return "模型提供方明确拒绝或返回无效结果；具体原因保留在调用记录中";
  return REASON_TEXT[code] ?? code;
}

export const FINDING_STATUS: Record<string, string> = {
  confirmed: "已确认",
  suspected: "疑似（待复核）",
  unsupported: "证据不足",
  escalated: "已升级复核",
  resolved: "已解决",
  rejected: "已驳回",
  unresolved: "仍存在",
};
export const SEVERITY_TEXT: Record<string, string> = { blocker: "必须解决", major: "重要", minor: "次要" };

// ---- 用量/费用：unknown 不是 0 ----
export function tokenText(usage: Record<string, unknown> | null | undefined): string {
  if (!usage) return "待核实";
  if (usage.usage_source === "not_applicable_demo") return "演示（不调用模型）";
  const total = usage.total_tokens;
  return typeof total === "number" ? total.toLocaleString("zh-CN") : "待核实";
}
export function amountText(price: Record<string, unknown> | null | undefined, demo: boolean): string {
  if (demo) return "不适用（演示）";
  if (!price) return "待核实（缺少价格）";
  const amount = price.amount ?? price.total;
  return typeof amount === "number" ? `${amount}` : "待核实";
}
export function callCostsText(calls: Array<{ status: string; price?: Record<string, unknown> | null }>, demo: boolean): string {
  if (demo) return "不适用（演示）";
  const totals = new Map<string, number>();
  let missing = 0;
  for (const call of calls) {
    if (call.status === "not_sent") continue;
    const amount = call.price?.amount ?? call.price?.total;
    const currency = call.price?.currency;
    if (typeof amount !== "number" || !Number.isFinite(amount) || amount < 0 || typeof currency !== "string" || !currency.trim()) {
      missing++;
      continue;
    }
    const key = currency.trim().toUpperCase();
    totals.set(key, (totals.get(key) ?? 0) + amount);
  }
  if (!totals.size) return "待核实（缺少价格或币种）";
  const known = [...totals].map(([currency, amount]) => `${currency} ${Number(amount.toPrecision(12))}`).join("；");
  return missing ? `已知 ${known}；${missing} 次金额待核实` : known;
}
export function limitText(v: unknown): string {
  return v === null || v === undefined ? "未设置" : String(v);
}
export function seconds(v: number | null | undefined): string {
  if (v === null || v === undefined) return "待核实";
  return v < 1 ? `${Math.round(v * 1000)} 毫秒` : v < 120 ? `${v.toFixed(1)} 秒` : `${Math.round(v / 60)} 分钟`;
}
export function formatTime(iso: string | undefined | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("zh-CN", { hour12: false });
}

export const NEXT_ACTION = { pause: "pause", resume: "resume" } as const;
