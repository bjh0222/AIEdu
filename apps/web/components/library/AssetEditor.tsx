"use client";

import { useRef, useState } from "react";
import type { FormatSpec } from "@/lib/types";
import { act, canonical, explain, newKey, sha256Hex, UnknownOutcomeError, getReceipt } from "@/lib/v2/client";

const TEXT_FIELDS: Record<string, string> = { name: "名称", description: "说明", target_reader: "目标读者", output_goal: "输出目标", content_structure: "内容结构", format_rules: "排版规则", required_rules: "必需规则", forbidden_rules: "禁止规则", suggested_rules: "建议规则", flexible_rules: "灵活规则", acceptance_criteria: "验收标准", reference_examples: "参考示例", custom_markdown: "自定义 Markdown" };

export function PrincipleEditor({ source, onSaved }: { source: { id: string; version?: number; status: string; statement?: string; mechanism?: string; guidance?: string }; onSaved: () => void }) {
  const [statement, setStatement] = useState(source.statement ?? ""), [mechanism, setMechanism] = useState(source.mechanism ?? ""), [guidance, setGuidance] = useState(source.guidance ?? ""), [status, setStatus] = useState(source.status === "auto_accepted" ? "proposed" : source.status), [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [pending, setPending] = useState<string | null>(null);
  const keys = useRef(new Map<string, string>());
  async function save() {
    const body = { expected_version: source.version, statement, mechanism, guidance, status, rationale: reason };
    const sig = canonical(body), key = keys.current.get(sig) ?? newKey(); keys.current.set(sig, key); setPending(key); setBusy(true); setError("");
    try { await act("PUT", `/knowledge/principles/${source.id}`, body, { kind: "principle_write", key }); setPending(null); onSaved(); }
    catch (e) { if (!(e instanceof UnknownOutcomeError)) setPending(null); setError(explain(e)); } finally { setBusy(false); }
  }
  return <section className="asset-editor"><p className="hint">修订保留历史并使旧引用待复核。确认是人工整理；后续生成资格仍需独立复用证据。</p>
    <label htmlFor="principle-statement">原理</label><textarea id="principle-statement" value={statement} onChange={(e) => setStatement(e.target.value)} />
    <label htmlFor="principle-mechanism">机制</label><textarea id="principle-mechanism" value={mechanism} onChange={(e) => setMechanism(e.target.value)} />
    <label htmlFor="principle-guidance">生成指导</label><textarea id="principle-guidance" value={guidance} onChange={(e) => setGuidance(e.target.value)} />
    <label htmlFor="principle-status">状态</label><select id="principle-status" value={status} onChange={(e) => setStatus(e.target.value)}>{[["proposed", "候选"], ["confirmed", "确认"], ["rejected", "驳回"], ["retracted", "撤回"]].map(([k,l]) => <option key={k} value={k}>{l}</option>)}</select>
    <label htmlFor="principle-reason">修订理由</label><textarea id="principle-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
    <button className="btn btn-primary" disabled={busy || !statement.trim() || !source.version || Boolean(pending)} onClick={() => void save()}>保存新版本</button>
    {pending && !busy && <button className="btn" onClick={() => void getReceipt(pending).then((r) => { if (r.status === "completed") { setPending(null); onSaved(); } else setError("保存尚未确认，请继续查询原回执，不要重复提交。"); }).catch((e) => setError(explain(e)))}>查询保存回执</button>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

export function SpecEditor({ source, onSaved }: { source?: FormatSpec; onSaved: () => void }) {
  const initial = source ? Object.fromEntries(Object.entries(source).filter(([k]) => !["id", "version", "is_builtin", "created_at", "updated_at"].includes(k))) : {
    name: "新规范", description: "", scene: "knowledge_tutorial", is_default: false, applicable_tasks: "", applicable_intents: [], excluded_scenarios: [], excluded_intents: [], trigger_keywords: [], excluded_keywords: [], allow_keyword_trigger: false, allow_semantic_recommendation: true, allow_explicit_selection: true, target_reader: "", output_goal: "", deliverables: [], content_structure: "", format_rules: "", required_rules: "", forbidden_rules: "", suggested_rules: "", flexible_rules: "", acceptance_criteria: "", reference_examples: "", custom_markdown: "",
  };
  const [value, setValue] = useState<Record<string, unknown>>(initial), [copy, setCopy] = useState(Boolean(source?.is_builtin)), [advanced, setAdvanced] = useState(JSON.stringify(initial, null, 2)), [error, setError] = useState(""), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false);
  const identity = useRef(new Map<string, string>()), [pending, setPending] = useState<string | null>(null);
  async function save() {
    const spec = { ...value, is_builtin: false, ...(copy ? { name: `${value.name}（副本）`, is_default: false } : {}) };
    const path = source && !copy ? `/format-specs/${source.id}` : "/format-specs";
    const payload = { expected_version: source && !copy ? source.version : null, spec };
    const sig = canonical([path, payload]); const key = identity.current.get(sig) ?? newKey(); identity.current.set(sig, key); setPending(key);
    setBusy(true); setError("");
    try { await act(source && !copy ? "PUT" : "POST", path, payload, { kind: "spec_write", key }); setPending(null); setSaved(true); onSaved(); }
    catch (e) { if (!(e instanceof UnknownOutcomeError)) setPending(null); setError(explain(e)); } finally { setBusy(false); }
  }
  return <section className="asset-editor"><p className="hint">保存使用版本保护。内置规范可复制；已有任务保留冻结版本。高级字段可完整编辑并保留原值。</p>
    {source && <label><input type="checkbox" checked={copy} disabled={source.is_builtin} onChange={(e) => setCopy(e.target.checked)} />创建自定义副本</label>}
    {Object.entries(TEXT_FIELDS).map(([key, label]) => <label key={key}>{label}<textarea rows={key === "name" ? 1 : 3} value={String(value[key] ?? "")} onChange={(e) => { const next = { ...value, [key]: e.target.value }; setValue(next); setAdvanced(JSON.stringify(next, null, 2)); setSaved(false); }} /></label>)}
    <label>适用场景 <select value={String(value.scene)} onChange={(e) => { const next = { ...value, scene: e.target.value }; setValue(next); setAdvanced(JSON.stringify(next, null, 2)); }}>{["knowledge_tutorial", "practical_tutorial", "reading_guide", "exercise", "exam_paper", "general"].map((s) => <option key={s}>{s}</option>)}</select></label>
    <details><summary>全部字段（JSON）</summary><textarea aria-label="规范全部字段 JSON" rows={18} value={advanced} onChange={(e) => setAdvanced(e.target.value)} /><button className="btn btn-sm" onClick={() => { try { const parsed: unknown = JSON.parse(advanced); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw Error(); setValue(parsed as Record<string, unknown>); setError(""); setSaved(false); } catch { setError("请输入完整的 JSON 对象。"); } }}>应用高级字段</button></details>
    {error && <p className="err" role="alert">{error}</p>}{saved && <p role="status">规范已保存。</p>}
    <button className="btn btn-primary" disabled={busy || saved || Boolean(pending)} onClick={() => void save()}>保存规范</button>
    {pending && <button className="btn" onClick={async () => { try { const r = await getReceipt(pending!); if (r.status === "completed") { setPending(null); setSaved(true); onSaved(); } else setError("操作尚未确认，请继续查询回执。"); } catch (e) { setError(explain(e)); } }}>查询保存回执</button>}
  </section>;
}

export function AssetImport({ kind, onSaved }: { kind: "specs" | "knowledge"; onSaved: () => void }) {
  const [text, setText] = useState(""), [filename, setFilename] = useState("questions.md"), [domain, setDomain] = useState("general"), [error, setError] = useState(""), [message, setMessage] = useState(""), [busy, setBusy] = useState(false);
  const identity = useRef(new Map<string, string>());
  const [pending, setPending] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setError(""); setMessage("");
    try {
      const records: unknown = kind === "specs" ? JSON.parse(text) : null;
      if (kind === "specs" && !Array.isArray(records)) throw new Error("规范导入需要完整记录数组（含原 ID、版本和时间戳）。");
      const payload = kind === "specs" ? { records, source_hash: await sha256Hex(canonical(records)) } : { filename, markdown: text, domain_key: domain };
      const signature = canonical(payload); const key = identity.current.get(signature) ?? newKey(); identity.current.set(signature, key);
      setPending(key);
      await act("POST", kind === "specs" ? "/format-spec-imports" : "/knowledge/batches/import", payload, { kind: "asset_import", key });
      setPending(null);
      setMessage(kind === "specs" ? "规范已导入，原始版本与字段保留。" : "原文已导入。整体理解尚未生成；已有理解只对应其绑定的原材料版本。"); onSaved();
    } catch (e) { if (!(e instanceof UnknownOutcomeError)) setPending(null); setError(e instanceof SyntaxError ? "JSON 格式不正确。" : explain(e)); } finally { setBusy(false); }
  }
  return <details className="panel panel-pad"><summary>{kind === "specs" ? "导入完整规范快照" : "导入题目原文"}</summary>
    <p className="hint">{kind === "specs" ? "插入型导入：保留全部字段、ID、版本和时间戳；同 ID 内容不同会拒绝。" : "保留原文；不自动分析或调用模型。独立分析请新建命题知识任务。"}</p>
    {kind === "knowledge" && <><label>文件名 <input value={filename} onChange={(e) => setFilename(e.target.value)} /></label><label>学科标识 <input value={domain} onChange={(e) => setDomain(e.target.value)} /></label></>}
    <label>从文件读取 <input type="file" accept={kind === "specs" ? ".json" : ".md,.txt"} onChange={async (e) => { const file = e.target.files?.[0]; if (file) { setText(await file.text()); setFilename(file.name); } }} /></label>
    <label>{kind === "specs" ? "完整记录数组 JSON" : "Markdown 原文"}<textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} /></label>
    <button className="btn" disabled={busy || Boolean(pending) || !text.trim()} onClick={() => void submit()}>导入并保留来源</button>{pending && !busy && <button className="btn" onClick={() => void getReceipt(pending).then((r) => { if (r.status === "completed") { setPending(null); setError(""); setMessage("回执已确认导入成功。"); onSaved(); } else setError("导入结果未知，请继续查询原回执，不会自动重放。"); }).catch((e) => setError(explain(e)))}>查询导入回执</button>}{error && <p className="err" role="alert">{error}</p>}{message && <p className="hint break-anywhere" role="status">{message}</p>}
  </details>;
}
