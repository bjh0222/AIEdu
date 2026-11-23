// v2 API 客户端。后端是唯一事实源：这里只负责会话/CSRF、幂等键、回执与错误翻译，不合成任何状态。
// 浏览器只访问同源 /api/v2/*（Next 反向代理到本机后端）。

const BASE = "/api/v2";

export class ApiError extends Error {
  status: number;
  code: string;
  retryable: boolean;
  currentRevision: number | null;
  details: Record<string, unknown>;
  constructor(status: number, code: string, details: Record<string, unknown> = {}, retryable = false, currentRevision: number | null = null) {
    super(code);
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryable = retryable;
    this.currentRevision = currentRevision;
  }
}

/** 请求已发出但没有拿到响应：动作可能已生效，只能凭同一个 key 查只读回执，不能当作“未执行”。 */
export class UnknownOutcomeError extends Error {
  key: string;
  constructor(key: string) {
    super("RESPONSE_UNKNOWN");
    this.key = key;
  }
}

let csrf: string | null = null;
let pairing: Promise<void> | null = null;

async function pair(force = false): Promise<void> {
  if (csrf && !force) return;
  if (!pairing || force) {
    pairing = (async () => {
      const res = await fetch(`${BASE}/session`, { method: "POST", cache: "no-store" });
      if (!res.ok) throw await toApiError(res);
      csrf = (await res.json()).csrf_token as string; // 仅存内存，不落盘
    })().finally(() => {
      pairing = null;
    });
  }
  await pairing;
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> = {};
  try {
    body = await res.json();
  } catch {
    /* 非 JSON 错误体 */
  }
  const detail = body.detail as Record<string, unknown> | string | undefined;
  const code = (body.code as string) ?? (typeof detail === "object" && detail ? (detail.code as string) : undefined) ?? `HTTP_${res.status}`;
  return new ApiError(
    res.status,
    code,
    ((body.details as Record<string, unknown>) ?? (typeof detail === "object" ? detail : {})) ?? {},
    Boolean(body.retryable),
    typeof body.current_revision === "number" ? body.current_revision : null,
  );
}

export function newKey(): string {
  return crypto.randomUUID();
}

export async function sha256Hex(data: string | Uint8Array): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${canonical(o[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// ---- 会话内动作记录：只存 action_kind/task_id/key/request_hash/expected_revision，不存正文/密钥 ----
export type PendingAction = {
  action_kind: string;
  task_id: string | null;
  key: string;
  request_hash: string;
  expected_revision: number | null;
  at: number;
};
const PENDING = "aiedu.pending.v1";

export function pendingActions(): PendingAction[] {
  try {
    return JSON.parse(sessionStorage.getItem(PENDING) || "[]");
  } catch {
    return [];
  }
}
function savePending(list: PendingAction[]) {
  try {
    sessionStorage.setItem(PENDING, JSON.stringify(list.slice(-50)));
  } catch {
    /* 存储不可用时仍可正常使用 */
  }
}
function trackPending(item: PendingAction) {
  savePending([...pendingActions().filter((p) => p.key !== item.key), item]);
}
export function clearPending(key: string) {
  savePending(pendingActions().filter((p) => p.key !== key));
}

type ReqOptions = {
  body?: unknown;
  rawBody?: BodyInit;
  headers?: Record<string, string>;
  key?: string;
  etag?: string;
  signal?: AbortSignal;
};

async function send(method: string, path: string, opts: ReqOptions, attempt = 0): Promise<Response> {
  const write = method !== "GET" && method !== "HEAD";
  // Every v2 route is owner-scoped. Establish the local session before the
  // first GET as well, otherwise a normal page load logs a preventable 401
  // before the retry path pairs and repeats the request.
  await pair();
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (write && csrf) headers["X-CSRF-Token"] = csrf;
  if (opts.key) headers["Idempotency-Key"] = opts.key;
  if (opts.etag) headers["If-None-Match"] = opts.etag;
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: opts.rawBody ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    cache: "no-store",
    signal: opts.signal,
  });
  // 401：会话过期。重新配对后对**同一 key、同一 body**重放一次（后端按幂等键去重）。
  if (res.status === 401 && attempt === 0) {
    await pair(true);
    return send(method, path, opts, 1);
  }
  return res;
}

export async function get<T>(path: string, opts: Pick<ReqOptions, "signal" | "headers"> = {}): Promise<T> {
  const res = await send("GET", path, opts);
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

/** 带 ETag 的只读轮询：304 返回 null，调用方保留已显示内容。 */
export async function getConditional<T>(path: string, etag: string | null, signal?: AbortSignal): Promise<{ data: T | null; etag: string | null }> {
  const res = await send("GET", path, { etag: etag ?? undefined, signal });
  if (res.status === 304) return { data: null, etag };
  if (!res.ok) throw await toApiError(res);
  return { data: (await res.json()) as T, etag: res.headers.get("ETag") };
}

export type ActionMeta = { kind: string; taskId?: string | null; expectedRevision?: number | null; key?: string };

/** 写动作：必带 Idempotency-Key。网络层失败 → UnknownOutcomeError（不自动重放）。 */
export async function act<T>(method: "POST" | "PUT" | "PATCH", path: string, body: unknown, meta: ActionMeta, extra: { headers?: Record<string, string>; rawBody?: BodyInit; bodyForHash?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const key = meta.key ?? newKey();
  trackPending({
    action_kind: meta.kind,
    task_id: meta.taskId ?? null,
    key,
    request_hash: await sha256Hex(canonical(extra.bodyForHash ?? body ?? null)),
    expected_revision: meta.expectedRevision ?? null,
    at: Date.now(),
  });
  let res: Response;
  try {
    res = await send(method, path, { body: extra.rawBody ? undefined : body ?? {}, rawBody: extra.rawBody, headers: extra.headers, key, signal: extra.signal });
  } catch {
    throw new UnknownOutcomeError(key);
  }
  if (!res.ok) {
    clearPending(key); // 服务端明确拒绝 = 未执行，可以用新 key 重新决策
    throw await toApiError(res);
  }
  clearPending(key);
  return (await res.json()) as T;
}

export type Receipt = { status: "completed" | "not_found"; [k: string]: unknown };
export function getReceipt(key: string): Promise<Receipt> {
  return send("GET", "/actions/receipt", { key }).then(async (res) => {
    if (!res.ok) throw await toApiError(res);
    return (await res.json()) as Receipt;
  });
}

export async function downloadBlob(path: string): Promise<{ blob: Blob; name: string }> {
  const res = await send("GET", path, {});
  if (!res.ok) throw await toApiError(res);
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "download";
  return { blob: await res.blob(), name };
}

/** 后端错误码 → 用户语言（说明发生了什么与下一步，不道歉）。未知码如实显示原码。 */
const ERRORS: Record<string, string> = {
  SESSION_REQUIRED: "连接已过期。页面会重新建立连接，请再试一次。",
  CSRF_DENIED: "请求校验没有通过。请刷新页面后重试。",
  ORIGIN_DENIED: "这个来源不在本机允许列表内，请通过 127.0.0.1 或 localhost 打开。",
  LOCAL_SESSION_ONLY: "只能在本机访问。",
  PAID_RUN_NOT_AUTHORIZED: "后端没有开启真实模型调用。可以改用演示模式，或由操作者用允许付费的方式启动后端。",
  MODEL_NOT_CONFIGURED: "还没有配置生成模型。到“设置”里添加，或改用演示模式。",
  MODEL_ROLE_UNCONFIGURED: "这个模型角色还没有配置。",
  CONTEXT_STALE: "任务输入已经变化。请重新读取后再确认。",
  REVISION_STALE: "任务状态已更新。已读取最新状态，请再试一次。",
  ACTIVE_RUN: "这个任务已经有一次运行在进行。",
  IDEMPOTENCY_CONFLICT: "同一操作的内容与之前不同。请重新发起。",
  IDEMPOTENCY_KEY_REQUIRED: "请求缺少幂等标识。",
  UNRESOLVED_CONFLICT: "输入里有尚未解决的冲突，需要你先选择。",
  MATERIAL_PREFLIGHT_FAILED: "有资料还没有解析完成或不属于这个任务。",
  MATERIAL_INCOMPLETE: "资料解析不完整，不能声称覆盖全部内容。",
  RESOURCE_POLICY_UNKNOWN: "操作者还没有配置资料解析的资源策略（resource-policy.json），所以暂不能上传资料。",
  UNSUPPORTED_MATERIAL_TYPE: "不支持这种资料类型。支持 txt、md、csv、带文字层的 pdf、docx、epub。",
  PARSER_BACKPRESSURE: "资料解析正忙，请稍后再传。",
  RESOURCE_EXHAUSTED: "本机资源不足，资料没有解析。",
  DISK_LOW: "磁盘空间不足，资料没有保存。",
  TASK_NOT_FOUND: "找不到这个任务。",
  RUN_NOT_FOUND: "找不到这次运行。",
  RUN_NOT_PAUSABLE: "这次运行现在不能暂停，可能已经结束或已经暂停。请刷新页面查看最新状态。",
  CHECKPOINT_STALE: "续跑所依据的进度已过期。请先读取最新状态。",
  INPUT_CHANGED: "任务输入已改变，需要回到确认页重新确认。",
  UNKNOWN_CALL: "有模型调用的结果不明，需要你逐项选择处理方式。",
  UNKNOWN_QUARANTINE_REQUIRED: "先隔离结果不明的调用，再单独继续生成。",
  CALL_NOT_QUARANTINED: "这个调用还没有完成隔离，不能作为续跑依据。",
  RETRY_REQUIRES_DUPLICATE_COST_ACK: "重做可能再次计费，需要明确确认。",
  FINAL_WAIVER_CONFIRM_REQUIRED: "专业项尚未完成校准。确认专业边界后，才可按当前固定版本继续。",
  DELIVERY_WAIVER_HARD_BLOCKED: "仍有结构、覆盖、材料或绑定问题，不能通过专业项放行。",
  DELIVERY_WAIVER_ITEM_MISMATCH: "需要放行的专业项已经变化，请重新读取页面后再确认。",
  CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED: "单项质检请求仍未收到完整响应，已停止继续拆分或自动重试。请检查模型配置或执行策略；旧调用结果和费用仍待核实。",
  PROVIDER_STATUS_UNSUPPORTED: "供应商不提供查询，这次调用会继续保留为“结果不明”。",
  UNKNOWN_DEPENDENCY_BLOCKED: "需要先确认可能重复计费，才能把它作为新尝试。",
  EMPTY_DRAFT: "还没有已保存的内容，暂时不能下载。",
  INCOMPLETE_REQUEST: "原请求还没有全部完成，完整下载包不可用。可以改下载“已有草稿”。",
  VERSION_MISMATCH: "这个版本不属于该产物。",
  VERSION_NOT_FOUND: "找不到这个版本。",
  BUNDLE_NOT_FOUND: "下载包不存在，请重新生成（不会换版本）。",
  FILE_NOT_FOUND: "下载包里没有这个文件。",
  BLOB_MISSING: "文件缓存缺失，请重新生成下载包。",
  EXPORT_OPTION_INVALID: "下载选项无效。",
  FINDING_STALE: "这个问题所属的版本已经更新。",
  FINDING_NOT_FOUND: "找不到这个问题。",
  NOT_ELIGIBLE: "这个问题暂不符合自动修复条件。",
  NOT_LOCATABLE: "这个问题缺少可靠位置，不能修复。",
  UNSUPPORTED_REPAIR_CATEGORY: "这类问题目前没有受支持的修复方式。",
  STRUCTURED_ITEM_REPAIR_UNSUPPORTED: "结构化题目的修复暂不支持。",
  INDEPENDENT_VERIFIER_REQUIRED: "目前没有独立的补证服务，不能凭一段意见把问题改成“已确认”。",
  SPEC_VERSION_STALE: "这份规范已被修改。请重新读取后再保存。",
  SPEC_NOT_FOUND: "找不到这份规范。",
  MODEL_CONFIG_STALE: "模型配置已被修改。请重新读取后再保存。",
  MODEL_PROFILE_INVALID: "模型配置格式不正确。",
  MODEL_ROLE_INVALID: "角色分配无效。",
  INVALID_CURSOR: "列表位置已失效，请刷新。",
  INVALID_PAGINATION: "分页参数无效。",
  KNOWLEDGE_SOURCE_STALE: "题目原文已更新，请重新读取。",
  OUTPUT_CAPACITY_EXHAUSTED: "模型已经用完了允许的最大输出长度，仍没有生成完整内容。继续重试很可能得到同样的结果，请改用输出上限更高的模型，或把这一步拆小。",
  VERIFIER_OUTPUT_CAPACITY_EXHAUSTED: "检查请求已经用到模型允许的最大输出长度，仍无法一次返回完整结果。请改用输出上限更高的模型，或把检查范围拆小。",
  CAPACITY_UNKNOWN: "当前模型没有登记可用的上下文和输出容量，系统不会猜测，因此没有发起调用。请在设置里换一个已登记容量的模型。",
  CALL_CAPACITY_EXCEEDED: "这次请求的输入加上完整回答已经超过模型上下文容量。请减少一次处理的材料，或换用上下文更大的模型。",
  MODEL_OUTPUT_LIMIT_INVALID: "设置里的额外输出上限无效：它必须大于 0，且不能超过模型本身的最大输出。",
  MODEL_OUTPUT_REQUEST_INVALID: "这次调用的输出预算参数无效。可以重试；如果反复出现，请检查模型设置。",
  CAPACITY_PARAMETER_INVALID: "模型容量登记里的输出参数名无效，系统没有发起调用。请检查模型配置。",
  PROVIDER_CALL_FAILED: "模型服务返回了失败结果，这次运行已停止。已保存的内容会保留，可以直接继续生成。",
  PROVIDER_OUTPUT_INVALID_OR_TRUNCATED: "模型输出没有形成完整内容，可能是长度上限、格式错误或被内容过滤。这次运行已停止，已保存的内容会保留。",
  UNKNOWN_QUARANTINE_REQUIRES_STOPPED_RUN: "这次运行还在进行中，不能隔离调用。请先点“暂停并保留已有内容”，等页面显示已暂停后再试。",
  UNKNOWN_QUARANTINE_REQUIRES_RISK_ACK: "隔离可能重复计费，需要先勾选费用确认。",
  CALL_NOT_UNKNOWN: "这个调用已经有明确结果，不需要按“结果不明”处理。请刷新页面查看最新状态。",
  CALL_SENT_RECEIPT_PRESENT: "这个调用已经拿到了服务商回执，不能标记为结果不明。请刷新页面查看最新状态。",
  CALL_REQUEST_MISMATCH: "要隔离的请求和记录不一致，为避免误隔离，系统没有执行。请刷新页面后重试。",
  CALL_NOT_FOUND: "找不到要隔离的调用记录。请刷新页面后重试。",
  FENCED: "这次运行已经停止或换了执行者，旧操作不能继续。请刷新页面查看最新状态。",
  RESOURCE_BACKPRESSURE: "本机正在处理另一项生成任务，请等它结束或先暂停，再继续。",
  RUN_NOT_DISPATCHABLE: "这次运行当前不能继续。请刷新页面查看最新状态。",
  OUTPUT_LENGTH_LIMIT: "模型的输出达到长度上限，内容被截断。",
  PROVIDER_TRANSPORT_UNKNOWN: "和服务商的连接中断，无法确认对方是否已经处理。请先查询回执，不要直接重复提交。",
};

function statusPhrase(status: number): string {
  if (status === 400) return "请求内容有误，请检查输入后重试。";
  if (status === 401 || status === 403) return "当前连接没有操作权限，请刷新页面后重试。";
  if (status === 404) return "找不到对应的内容，可能已经被删除或移动。";
  if (status === 409 || status === 422) return "当前状态不允许这个操作，请刷新页面后按最新状态重试。";
  if (status === 429) return "操作太频繁或额度用尽，请稍后再试。";
  if (status >= 500) return "服务器处理时出错，请稍后重试。";
  return "操作没有完成，请稍后重试。";
}

export function explain(error: unknown): string {
  if (error instanceof UnknownOutcomeError) return "没有收到服务端的响应，操作可能已经生效。请先查询回执，不要直接重复提交。";
  if (error instanceof ApiError) {
    const known = ERRORS[error.code];
    if (known) return known;
    // 服务商 HTTP 错误码动态生成（例如 PROVIDER_HTTP_429）。
    const provider = /^PROVIDER_HTTP_(\d{3})$/.exec(error.code);
    if (provider) {
      const status = Number(provider[1]);
      if (status === 401 || status === 403) return "模型服务拒绝了这次请求：密钥或权限无效。请到“设置”检查模型配置。";
      if (status === 404) return "模型服务找不到这个模型或接口。请到“设置”检查模型名称和地址。";
      if (status === 413) return "这次请求太大，模型服务拒绝接收。请减少一次处理的材料。";
      if (status === 429) return "模型服务限流或额度不足，请稍后再试，或检查账户余额。";
      if (status >= 500) return "模型服务暂时不可用，请稍后重试。";
      return "模型服务拒绝了这次请求。请检查模型配置后重试。";
    }
    // 给普通用户先给结论，再保留一个不显眼的错误码，方便排查时对照日志。
    return `${statusPhrase(error.status)}（错误码：${error.code}）`;
  }
  if (error instanceof TypeError) return "无法连接后端。请确认后端已在本机启动，或稍后重试。";
  if (error instanceof Error && error.message) return error.message;
  return "操作没有完成，请稍后重试。";
}
