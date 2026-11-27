"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { act, explain, newKey, UnknownOutcomeError } from "@/lib/v2/client";

type ImportAsset = { original_ref: string; resolved_path: string; blob_hash: string | null; status: string };
type ImportResult = {
  task_id: string;
  run_id: string | null;
  artifact_id: string | null;
  version_id: string | null;
  title: string;
  source_hash: string;
  parser: { parser_mode: string; split_level: number; confidence: number; ai_assist_candidate: boolean; ai_used?: boolean; ai_outcome?: string; ai_assumptions?: string[]; warnings?: string[] };
  warnings?: string[];
  assets?: ImportAsset[];
  unit_count: number;
};

const MODE_LABEL: Record<string, string> = {
  atx_headings: "Markdown 标题层级",
  setext_headings: "Setext 标题层级",
  pseudo_headings: "编号 / 加粗标题",
  plain_document: "普通长文",
  ai_assisted_headings: "答疑AI确认标题",
};

export default function ImportExternal() {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unknownKey, setUnknownKey] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [aiAssist, setAiAssist] = useState(true);
  const keyRef = useRef<string>("");

  const markdown = useMemo(() => files.find((f) => /\.(md|markdown)$/i.test(f.name)) ?? null, [files]);
  const attachments = useMemo(() => files.filter((f) => /\.(png|jpe?g|webp|gif|svg)$/i.test(f.name)), [files]);
  const canImport = Boolean(markdown) && !busy && !unknownKey;

  function pick(next: FileList | null) { setFiles(Array.from(next ?? [])); setResult(null); setError(""); }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!markdown || busy) return;
    setBusy(true); setError(""); setResult(null); setUnknownKey(null);
    const key = keyRef.current || newKey();
    keyRef.current = key;
    const form = new FormData();
    form.append("ai_assist", aiAssist ? "true" : "false");
    for (const file of [markdown, ...attachments]) form.append("files", file, file.name);
    try {
      const body = await act<ImportResult>("POST", "/external-imports", null,
        { kind: "external_import", key }, { rawBody: form, bodyForHash: [markdown.name, ...attachments.map(f => f.name)] });
      setResult(body);
      keyRef.current = "";
    } catch (err) {
      if (err instanceof UnknownOutcomeError) {
        setUnknownKey(err.key);
        setError("没有收到服务端响应。导入可能已经完成；请先查询回执，不要重复导入。");
      } else {
        setError(explain(err));
      }
    } finally { setBusy(false); }
  }

  async function checkReceipt() {
    if (!unknownKey) return;
    setBusy(true); setError("");
    try {
      const res = await fetch(`/api/v2/actions/receipt`, { headers: { "Idempotency-Key": unknownKey } });
      const body = await res.json();
      if (body.status !== "completed") throw new Error("回执还没有生成。请稍后重试。");
      setResult(body.response as ImportResult);
      setUnknownKey(null); keyRef.current = "";
    } catch (err) { setError(err instanceof Error ? err.message : explain(err)); }
    finally { setBusy(false); }
  }

  const unresolved = (result?.assets ?? []).filter(a => a.status !== "stored");
  return (
    <>
      <div className="crumbs"><Link href="/">任务</Link><span aria-hidden="true">/</span><span aria-current="page">导入外部任务</span></div>
      <h1 className="title">导入外部任务</h1>
      <p className="muted">把外部 Markdown 保存为只读学习任务，共用现有阅读器。规则先识别结构；低置信度文档可按普通长文阅读。</p>

      <form className="panel panel-pad" style={{ marginTop: 16, display: "grid", gap: 16 }} onSubmit={submit}>
        <div className="field">
          <label htmlFor="external-markdown">Markdown 文件</label>
          <input id="external-markdown" type="file" multiple disabled={busy}
            accept=".md,.markdown,.png,.jpg,.jpeg,.webp,.gif,.svg"
            onChange={(e) => pick(e.target.files)} />
          <p className="hint">选择 1 个 .md 或 .markdown 文件。可同时选择同目录图片附件；原始引用路径不会改写。</p>
          {markdown && <p className="hint">主文件：{markdown.name} · {attachments.length} 个附件</p>}
        </div>

        <div className="field">
          <label>识别与降级</label>
          <ul className="muted" style={{ margin: 0, paddingLeft: 20 }}>
            <li>优先按 Markdown AST 规则识别标题、中文章节、编号和加粗标题。</li>
            <li>原文按切片完整保存，不因目录识别而改写、删除或重排。</li>
            <li>没有明确章节时按普通长文阅读，不强行生成目录。</li>
          </ul>
          <label><input type="checkbox" checked={aiAssist} onChange={(e) => setAiAssist(e.target.checked)} /> 低置信度时让当前答疑模型复核标题行</label>
          <p className="hint">模型只确认原文中已存在的标题行号；标题文本仍取自原文，不会新增或改写内容。</p>
        </div>

        {error && (
          <div className="banner banner-danger" role="alert">
            <div>{error}</div>
            {unknownKey && <button type="button" className="btn btn-sm" onClick={() => void checkReceipt()}>查询回执</button>}
          </div>
        )}

        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn btn-primary" type="submit" disabled={!canImport} aria-busy={busy}>导入并阅读</button>
          <Link className="btn" href="/new">去新建生成任务</Link>
        </div>
      </form>

      {result && (
        <section className="panel panel-pad" style={{ marginTop: 16 }} aria-labelledby="import-result">
          <h2 className="sub" id="import-result">导入结果</h2>
          <dl className="kv" style={{ gridTemplateColumns: "auto 1fr" }}>
            <dt>标题</dt><dd>{result.title}</dd>
            <dt>结构</dt><dd>{MODE_LABEL[result.parser.parser_mode] ?? result.parser.parser_mode}</dd>
            <dt>章节数</dt><dd>{result.unit_count}</dd>
            <dt>规则置信度</dt><dd>{Math.round(result.parser.confidence * 100)}%</dd>
            <dt>原文校验值</dt><dd className="mono">{result.source_hash}</dd>
          </dl>
          {result.parser.ai_assist_candidate && !result.parser.ai_used && (
            <p className="hint" role="note">规则识别置信度较低；{result.parser.ai_outcome ? `答疑模型状态：${result.parser.ai_outcome}。` : ""}本次已按保守结构保存，可先阅读，若结构不合预期再人工拆分或补充标准标题。</p>
          )}
          {(result.warnings ?? []).length > 0 && (
            <ul className="hint"><li>识别警告：{(result.warnings ?? []).join("；")}</li></ul>
          )}
          {unresolved.length > 0 && (
            <div className="banner banner-warn" style={{ marginTop: 12 }}>
              <div><b>有 {unresolved.length} 个本地资源未随文件上传。</b> 原文引用保留，不会静默丢失；补充选择附件后重新导入。</div>
            </div>
          )}
          <div style={{ marginTop: 16, display: "flex", gap: 10, flexWrap: "wrap" }}>
            <Link className="btn btn-primary" href={`/tasks/${result.task_id}/read`}>打开阅读页</Link>
            <Link className="btn" href="/">回到任务列表</Link>
          </div>
        </section>
      )}
    </>
  );
}




