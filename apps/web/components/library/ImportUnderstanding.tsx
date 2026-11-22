"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { get, explain } from "@/lib/v2/client";
import type { UnderstandingAsset, UnderstandingPage } from "@/lib/v2/contracts.generated";
import type { UnderstandingContext } from "@/lib/v2/management-api";
import { formatTime } from "@/lib/v2/status";

const LABEL: Record<string, string> = {
  kind: "材料类型", knowledge: "知识整体理解", proposition: "命题整体理解", concepts: "核心概念", structure: "知识结构",
  prerequisites: "先修关系", prerequisite_relations: "先修关系", difficulty_features: "难度层级与特征",
  difficulty_model: "难度理解", quality_features: "高质量特征", quality_model: "质量理解",
  cognitive_operations: "认知操作", step_burden: "步骤负担", explicit_constraints: "显式约束",
  coverage: "理解覆盖", coverage_gaps: "覆盖缺口", evidence_gaps: "证据缺口", limitations: "限制与未知",
  representative_samples: "代表性例题与知识片段", source_refs: "来源依据", confidence: "置信限制",
  knowledge_understanding: "知识整体理解", proposition_understanding: "命题整体理解",
  quote: "原文片段", interpretation: "AI 归纳依据", unknown: "未知", unknowns: "未知",
  research_context_examples: "文献背景示例（非导入例题）", difficulty: "难度", quality: "质量", features: "特征",
  knowledge_model: "知识结构理解", prerequisite_model: "先修理解",
  common_misconceptions: "常见误解", evidence_boundary: "证据与未知边界",
  organization: "组织形式", knowledge_chain: "知识链要求", question_design: "题型与题量",
  quantity_policy: "数量与难度策略", difficulty_policy: "难度目标策略", output_design: "输出设计",
};
const INTERNAL = new Set(["id", "profile_id", "rubric_ref", "hash", "source_hash", "quote_hash", "binding_hash", "source_id", "version",
  "calibration_status", "aggregate_score", "threshold", "material_refs", "title", "normalization", "difficulty_type", "start_byte", "end_byte"]);
function StructuredValue({ value, sources }: { value: unknown; sources: UnderstandingContext["materials"] }) {
  if (value == null) return <span className="muted">未知</span>;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return <span className="break-anywhere">{String(value)}</span>;
  if (Array.isArray(value)) return value.length ? <ul className="understanding-list">{value.map((v, i) => <li key={i}><StructuredValue value={v} sources={sources} /></li>)}</ul> : <span className="muted">未提供</span>;
  const object = value as Record<string, unknown>;
  const source = sources.find((s) => s.source_id === object.source_id);
  return <div className="understanding-value">
    {typeof object.source_id === "string" && <p className="hint">来源：{source?.task_id ? <Link href={`/tasks/${source.task_id}`}>{source.filename || "已导入材料"}</Link> : source?.filename || "材料身份已绑定，名称暂不可核实"}
      {typeof object.start_byte === "number" && typeof object.end_byte === "number" && `；原文 UTF-8 字节 ${object.start_byte}–${object.end_byte}`}</p>}
    <dl>{Object.entries(object).filter(([k]) => !INTERNAL.has(k)).map(([key, item]) => <div key={key}><dt>{LABEL[key] ?? key}</dt><dd><StructuredValue value={item} sources={sources} /></dd></div>)}</dl>
  </div>;
}

function UnderstandingItem({ asset, refresh }: { asset: UnderstandingAsset; refresh: number }) {
  const [context, setContext] = useState<UnderstandingContext | null>(null), [contextError, setContextError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    get<UnderstandingContext>(`/managed-understanding-context/${encodeURIComponent(asset.id)}?version=${asset.version}`, { signal: abort.signal })
      .then((v) => { if (!abort.signal.aborted) { setContext(v); setContextError(""); } })
      .catch((e) => { if (!abort.signal.aborted) setContextError(explain(e)); });
    return () => abort.abort();
  }, [asset.id, asset.version, asset.hash, refresh]);
  const content = asset.content, sources = context?.materials ?? [];
  return <article className="understanding-asset panel panel-pad">
    <h3 className="sub">{String(content.title ?? "导入材料整体理解")}（版本 {asset.version}）</h3>
    <p className="hint">可修订的 AI 归纳，供生成安排与逐维质检使用；不是模型训练或专业校准。实测难度与学习收益未知。</p>
    <div className="understanding-binding" role="status">
      <p>{contextError ? "材料版本状态读取失败，不能确认是否仍适用。" : !context ? "正在核对材料版本…" : context.status === "current" ? "材料版本一致" : context.status === "stale" ? "材料或理解版本已变化，旧理解需重新核验" : "材料版本状态未知"}</p>
      <p>更新时间：{context?.updated_at ? formatTime(context.updated_at) : "未知（接口无可核验时间）"}
        {context?.updated_at_source && context.updated_at_source !== "unknown" ? `（${context.updated_at_source === "asset_wrapper" ? "资产记录" : context.updated_at_source === "research_result" ? "研究结果回执" : "运行记录"}）` : ""}
      </p>
      {sources.map((s) => <p key={s.source_id}>材料：{s.task_id ? <Link href={`/tasks/${s.task_id}`}>{s.filename || "已导入资料"}</Link> : s.filename || "原材料不可用"}{s.status !== "current" && <span className="err">（{s.status === "missing" ? "缺失" : s.status === "trashed" ? "在回收站" : "版本已变化"}）</span>}</p>)}
      {contextError && <p className="hint">{contextError}</p>}
    </div>
    {Object.entries(content).filter(([key]) => !INTERNAL.has(key)).map(([key, value]) => <section key={key} className="understanding-section">
      <h4>{LABEL[key] ?? key}</h4><StructuredValue value={value} sources={sources} />
    </section>)}
  </article>;
}

type SubjectAsset = {
  kind: string;
  id: string;
  version: number;
  hash: string;
  domain_key: string;
  display_name?: string;
  source_kind: string;
  source_refs: string[];
  updated_at: string | null;
  calibration_status: string;
  content: Record<string, unknown>;
};

type SubjectPage = {
  schema_version: string;
  version: number;
  updated_at: string | null;
  calibration_status: string;
  professional_certification: false;
  general: SubjectAsset;
  subjects: SubjectAsset[];
  tutorial_planning_version: number;
  tutorial_planning_hash: string;
};

function SubjectBlock({ asset, open = false }: { asset: SubjectAsset; open?: boolean }) {
  const title = asset.display_name ?? (asset.domain_key === "general" ? "通用理解层" : asset.domain_key);
  return <details className="understanding-asset panel panel-pad" open={open}>
    <summary><b>{title}</b>（v{asset.version}）</summary>
    <p className="hint">来源：{asset.source_kind}；工程整理，未专业校准。实测难度与学习收益未测量。</p>
    {Object.entries(asset.content)
      .filter(([key]) => !INTERNAL.has(key) && !["source_kind", "source_refs"].includes(key))
      .map(([key, value]) => <section key={key} className="understanding-section">
        <h4>{LABEL[key] ?? key}</h4><StructuredValue value={value} sources={[]} />
      </section>)}
    {asset.source_refs.length > 0 && <p className="hint break-anywhere">依据：{asset.source_refs.join("；")}</p>}
  </details>;
}

export default function ImportUnderstanding({ revision = 0 }: { revision?: number }) {
  const [page, setPage] = useState<UnderstandingPage | null>(null), [error, setError] = useState(""), [refresh, setRefresh] = useState(0);
  const [subjects, setSubjects] = useState<SubjectPage | null>(null), [subjectError, setSubjectError] = useState("");
  const [reading, setReading] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    get<UnderstandingPage>("/import-understanding", { signal: abort.signal }).then((v) => {
      if (!abort.signal.aborted) { setPage(v); setError(""); setReading(false); }
    }).catch((e) => { if (!abort.signal.aborted) { setError(explain(e)); setReading(false); } });
    return () => abort.abort();
  }, [revision, refresh]);
  useEffect(() => {
    const abort = new AbortController();
    get<SubjectPage>("/subject-understanding", { signal: abort.signal }).then((value) => {
      if (!abort.signal.aborted) { setSubjects(value); setSubjectError(""); }
    }).catch((e) => {
      if (!abort.signal.aborted) setSubjectError(explain(e));
    });
    return () => abort.abort();
  }, [revision, refresh]);
  return <section aria-label="AI 整体理解">
    <div className="row-actions"><h2 className="sub">AI 对知识、难度与质量的理解</h2><button className="btn" disabled={reading} onClick={() => { setReading(true); setRefresh((v) => v + 1); }}>刷新理解</button></div>
    <section aria-label="通用与学科理解">
      <h3 className="sub">通用理解层</h3>
      {subjectError && <p className="err" role="alert">通用与学科理解读取失败：{subjectError}</p>}
      {!subjects && !subjectError ? <p aria-busy="true">正在读取通用与学科理解…</p> : subjects && <>
        <p className="hint">版本 {subjects.version} · 教程规划量规 {subjects.tutorial_planning_version} · 更新时间：{subjects.updated_at ? formatTime(subjects.updated_at) : "未知"}。AI 归纳与研究素材整理，不是专业校准。</p>
        <SubjectBlock asset={subjects.general} open />
        <h3 className="sub" style={{ marginTop: 20 }}>逐学科理解（{subjects.subjects.length}）</h3>
        {subjects.subjects.map((asset) => <SubjectBlock key={asset.id} asset={asset} />)}
      </>}
    </section>
    <h3 className="sub" style={{ marginTop: 24 }}>材料整体理解</h3>
    {error && <p className="err" role="alert">理解结果读取失败：{error}{page ? " 下方是上次读取的归纳，可能已过期。" : ""}</p>}
    {!page && !error ? <p aria-busy="true">正在读取理解结果…</p> : page && !page.items.length ? <p className="muted">尚无已生成的整体理解。导入题目不等于已分析难度与质量。</p> :
      page?.items.map((asset) => <UnderstandingItem key={`${asset.id}:${asset.version}:${asset.hash}`} asset={asset} refresh={refresh} />)}
  </section>;
}
