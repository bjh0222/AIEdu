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
  RUN_NOT_PAUSABLE: "这次运行已经不能暂停了。",
  CHECKPOINT_STALE: "续跑所依据的进度已过期。请先读取最新状态。",
  INPUT_CHANGED: "任务输入已改变，需要回到确认页重新确认。",
  UNKNOWN_CALL: "有模型调用的结果不明，需要你逐项选择处理方式。",
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
};

export function explain(error: unknown): string {
  if (error instanceof UnknownOutcomeError) return "没有收到服务端的响应，操作可能已经生效。请先查询回执，不要直接重复提交。";
  if (error instanceof ApiError) {
    return ERRORS[error.code] ?? `后端返回 ${error.code}（${error.status}）。`;
  }
  if (error instanceof TypeError) return "无法连接后端。请确认后端已在本机启动。";
  return error instanceof Error ? error.message : String(error);
}
