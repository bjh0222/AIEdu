"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Capabilities } from "@/lib/v2/api";
import { ApiError, UnknownOutcomeError, explain, getReceipt, newKey, act } from "@/lib/v2/client";

type Conflict = { code?: string; artifact_role?: string; scene?: string; [k: string]: unknown };

const SCENE_TEXT: Record<string, string> = {
  default_tutorial: "通用教程", knowledge_tutorial: "知识讲解", practical_tutorial: "实践操作", reading_guide: "阅读指引",
  exercise: "练习", exam_paper: "试卷", exam: "试卷",
};

function conflictText(c: Conflict): string {
  if (c.code === "SPEC_CONFLICT") return `产物“${c.artifact_role ?? "tutorial"}”同时匹配了多个互相冲突的规范，系统不会替你选择。`;
  if (c.code === "UNSUPPORTED_SCENE") return `不认识的模式“${c.scene}”。`;
  if (c.code === "ANSWER_REQUIRES_QUESTIONS") return "要求了答案，但没有要求题目。";
  return `需要你确认：${c.code ?? "未命名的冲突"}`;
}

export default function NewTask() {
  const router = useRouter();
  // 2026-11-22 轮3：需求框在任何触发下都不自动填充；仅保留灰色占位提示。
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [capsError, setCapsError] = useState("");
  const [domain, setDomain] = useState("");
  const [scene, setScene] = useState("");
  const [scope, setScope] = useState<"" | "focused" | "system">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unknownKey, setUnknownKey] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<{ taskId: string; list: Conflict[] } | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [research, setResearch] = useState(true);
  const [callBudget, setCallBudget] = useState<number | null>(null);
  const keyRef = useRef<string>("");
  const bodyRef = useRef<string>("");

  useEffect(() => {
    api.capabilities().then(setCaps).catch((e) => setCapsError(explain(e)));
  }, []);

  const paidOk = caps?.paid_calls_enabled ?? false;
  const materialsReady = caps?.materials.resource_policy_status === "ready";
  // S10: 用户端只有真实生成。paid_calls_enabled=false 时显示无法开始，
  // 不再自动降级 demo；demo 只保留给 AI 测试/基准/内部验证。
  const canStart = !!caps && paidOk && text.trim().length > 0 && !busy && !unknownKey
    && (files.length === 0 || materialsReady)
    && (callBudget === null || Number.isInteger(callBudget) && callBudget > 0);
  const scenes = useMemo(() => caps?.scenes.filter((s) => s !== "exam") ?? [], [caps]);

  function bodyOf() {
    const body: Record<string, unknown> = { raw_input: text, mode: "configured" };
    if (title.trim()) body.title = title.trim();
    if (domain) body.domain_key = domain;
    if (scope) body.generation_scope = scope;
    if (scene) body.spec_bindings = [{ artifact_role: scene === "exam_paper" ? "paper" : scene === "exercise" ? "exercise" : "tutorial", scene }];
    {
      body.execution_policy = { structured_arrangement: true, quality_loop: true,
        task_adaptive_format: true,
        quality_profile: "correctness_only.v1",
        criterion_assessment: false,
        quality_layers: { correctness: true, completeness: false, quality_specific: false },
        optimization_policy: { policy_version: "aiedu.repair.v2", enabled: true,
          max_attempts_per_finding: 2, max_repair_rounds_per_run: 2, root_call_budget: callBudget } };
      if (research) {
        // 研究请求只携带可公开的结构化标签。用户原话可能包含私有资料、
        // 学习背景或内部标识，不能被当作公开检索主题发送给模型/索引服务。
        body.research_request = {
          enabled: true,
          public_topic: {
            domain_key: domain || null,
            scene: scene || null,
            requested_artifacts: scene
              ? [scene === "exam_paper" ? "paper" : scene === "exercise" ? "exercise" : "tutorial"]
              : ["tutorial"],
          },
        };
      }
    }
    return body;
  }

  async function start() {
    setBusy(true);
    setError("");
    setConflicts(null);
    try {
      const body = bodyOf();
      // 同一份输入重试复用同一个幂等键；内容变了才换新键。
      const fileHashes = await Promise.all(files.map(async (f) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await f.arrayBuffer()))).map((b) => b.toString(16).padStart(2, "0")).join("")));
      const signature = JSON.stringify({ body, files: files.map((f, i) => ({ name: f.name, hash: fileHashes[i] })) });
      if (!keyRef.current || bodyRef.current !== signature) {
        keyRef.current = newKey();
        bodyRef.current = signature;
      }
      if (files.length > 0) await startWithMaterials(body);
      else {
        const result = await api.taskStart(body as never, keyRef.current);
        await afterStart(result);
      }
    } catch (e) {
      if (e instanceof UnknownOutcomeError) setUnknownKey(e.key);
      setError(explain(e));
    } finally {
      setBusy(false);
    }
  }

  async function afterStart(result: { task_id: string; run_id: string | null; conflicts?: unknown[] }) {
    if (result.conflicts && result.conflicts.length) {
      setConflicts({ taskId: result.task_id, list: result.conflicts as Conflict[] });
      return;
    }
    router.push(`/tasks/${result.task_id}`);
  }

  // 有资料：必须走“创建 → 上传 → 选择 → 确认 → 启动”。资料不会被自动选中，也不伪造解析成功。
  async function startWithMaterials(body: Record<string, unknown>) {
    const k = keyRef.current;
    const input = { ...Object.fromEntries(Object.entries(body).filter(([key]) => key !== "mode")), title: (body.title as string) ?? "新学习任务" };
    const created = await api.createTask(input as never, `${k}:create`);
    const ids: string[] = [];
    let revision = created.input_revision ?? 1;
    for (const [index, f] of files.entries()) {
      const bytes = await f.arrayBuffer();
      const contentHash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map((b) => b.toString(16).padStart(2, "0")).join("");
      const res = await act<{ source_id: string }>("POST", `/tasks/${created.task_id}/materials`, null, { kind: "upload_material", taskId: created.task_id, expectedRevision: revision, key: `${k}:m:${index}:${contentHash}` }, {
        rawBody: bytes, headers: { "X-Filename-UTF8": encodeURIComponent(f.name), "X-Expected-Revision": String(revision), "Content-Type": "application/octet-stream" },
        bodyForHash: { name: f.name, sha256: contentHash },
      });
      ids.push(res.source_id);
    }
    const patched = await api.patchInput(created.task_id, { expected_revision: revision, input: { ...(input as object), material_ids: ids } as never }, `${k}:patch`);
    revision = patched.input_revision ?? revision + 1;
    if (patched.task_state === "awaiting_input") {
      setConflicts({ taskId: created.task_id, list: (patched.conflicts as Conflict[]) ?? [] });
      return;
    }
    const confirmed = await api.confirm(created.task_id, { expected_revision: revision, context_hash: patched.context_hash! }, `${k}:confirm`);
    await api.startRun(created.task_id, { expected_revision: confirmed.input_revision ?? revision, confirmed_context_hash: confirmed.context_hash!, mode: "configured" }, `${k}:run`);
    router.push(`/tasks/${created.task_id}`);
  }

  async function checkReceipt() {
    if (!unknownKey) return;
    try {
      const r = await getReceipt(unknownKey);
      if (r.status === "completed") {
        const id = (r.task_id as string | undefined) ?? undefined;
        setError(id ? "已确认：上一次提交已生效。" : "已确认：上一次提交已生效，请到任务列表查看。");
        setUnknownKey(null);
        router.push(id ? `/tasks/${id}` : "/");
      } else setError("暂未查到回执，仍不能断定未执行。请继续查询，勿重复提交。");
    } catch (e) {
      setError(explain(e));
    }
  }

  return (
    <>
      <div className="crumbs"><Link href="/">任务</Link><span aria-hidden="true">/</span><span aria-current="page">新建任务</span></div>
      <div className="page-head">
        <div>
          <h1 className="title">新建任务</h1>
        </div>
      </div>

      {capsError && <div className="banner banner-danger" role="alert" style={{ marginBottom: 16 }}><div><b>无法读取后端能力。</b> {capsError}</div></div>}

      <form className="compose" onSubmit={(e) => { e.preventDefault(); if (canStart) void start(); }} style={{ display: "grid", gap: 20 }}>
        <div className="field">
          <label htmlFor="raw">需求</label>
          <textarea id="raw" value={text} onChange={(e) => setText(e.target.value)} placeholder="可按需说明学习目标、学习时间、目标难度、输出模式、题型和题量，不必逐项填写。" />
        </div>

        <details className="disclosure panel panel-pad">
          <summary>自动匹配与高级选项</summary>
          <div style={{ display: "grid", gap: 16, marginTop: 12 }}>
            <p className="hint">不选就由后端根据原话自动匹配；这里的选择会被当作你的明确要求。</p>
            <div className="field">
              <label htmlFor="title">任务标题（可选）</label>
              <input id="title" type="text" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="domain">学科</label>
              <select id="domain" value={domain} onChange={(e) => setDomain(e.target.value)}>
                <option value="">自动匹配</option>
                {caps?.taxonomy.map((t) => <option key={t.domain_key} value={t.domain_key}>{t.display_name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="scene">模式</label>
              <select id="scene" value={scene} onChange={(e) => setScene(e.target.value)}>
                <option value="">自动匹配</option>
                {scenes.map((s) => <option key={s} value={s}>{SCENE_TEXT[s] ?? s}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="scope">学习范围</label>
              <select id="scope" value={scope} onChange={(e) => setScope(e.target.value as typeof scope)}>
                <option value="">根据需求自动判断</option>
                <option value="focused">小专题：一个方法或考点</option>
                <option value="system">大模块：完整学习体系</option>
              </select>
              <p className="hint">你的明确选择优先。模板约束排版，内容仍由需求与材料决定。</p>
            </div>
            <div className="field">
              <label htmlFor="files">参考资料（可选）</label>
              <input ref={fileInputRef} id="files" type="file" multiple disabled={!materialsReady || busy} accept=".txt,.md,.markdown,.csv,.pdf,.docx,.epub" onChange={(e) => { const incoming = Array.from(e.target.files ?? []); setFiles((current) => [...current, ...incoming]); if (fileInputRef.current) fileInputRef.current.value = ""; }} />
              {!materialsReady && <p className="hint">资料上传暂不可用：{caps?.materials.resource_policy_reason ?? "资源策略尚未确认"}。仍可创建无资料任务。</p>}
              <p className="hint">
                支持 txt、md、csv、带文字层的 pdf、docx、epub；扫描件和旧版 doc/ppt 不支持。
                {files.length > 0 && <> 已选 {files.length} 个文件</>}
              </p>
              {files.length > 0 && (
                <ul style={{ margin: "8px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 6 }}>
                  {files.map((file, index) => (
                    <li key={`${file.name}-${file.lastModified}-${index}`} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{file.name}</span>
                      <button type="button" className="btn btn-sm" aria-label={`移除 ${file.name}`} onClick={() => { setFiles((current) => current.filter((_, i) => i !== index)); if (fileInputRef.current) fileInputRef.current.value = ""; }}>移除</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="field">
              <label htmlFor="call-budget">模型调用上限</label>
              <input id="call-budget" type="number" min={1} step={1} placeholder="不限" value={callBudget === null ? "" : callBudget} onChange={(e) => setCallBudget(Number.isNaN(e.target.valueAsNumber) ? null : e.target.valueAsNumber)} />
              <p className="hint">理解、研究、生成、检查和优化共用此上限；留空表示不限。若设置上限，预算耗尽会停止并保留最佳草稿；无进展、取消和未知回执仍受保护。</p>
            </div>
            <div className="field">
              <label><input type="checkbox" checked={research} onChange={(e) => setResearch(e.target.checked)} /> 联网查阅教育文献，建立任务适用的逐维量规</label>
              <p className="hint">{research ? "研究会增加模型调用；仅把需求主题用于公共教育检索，不发送附件原文。" : "不联网检索；按任务已有绑定与已导入资料质检。"}</p>
            </div>
          </div>
        </details>

        {caps && !paidOk && (
          <p className="hint" role="note">无法开始：后端未开启真实模型调用；演示入口已下线，不会自动降级为演示。</p>
        )}

        {conflicts && (
          <div className="banner banner-warn" role="alert">
            <div>
              <b>开始前需要你确认：</b>
              <ul style={{ marginTop: 6 }}>
                {conflicts.list.map((c, i) => <li key={i}>{conflictText(c)}</li>)}
              </ul>
              <p style={{ marginTop: 6 }}>请在上面的“自动匹配与高级选项”里明确选择模式后再开始。任务 <Link href={`/tasks/${conflicts.taskId}`}>已保存为草稿</Link>。</p>
            </div>
          </div>
        )}

        {error && (
          <div className="banner banner-danger" role="alert">
            <div>{error}</div>
            {unknownKey && <button type="button" className="btn btn-sm" onClick={() => void checkReceipt()}>查询回执</button>}
          </div>
        )}

        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn btn-primary" type="submit" disabled={!canStart} aria-busy={busy}>开始生成</button>
        </div>
      </form>
    </>
  );
}

export { ApiError };
