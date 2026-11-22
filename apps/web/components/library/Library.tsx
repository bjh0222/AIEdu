"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { get, explain } from "@/lib/v2/client";
import { specGroupLabel, specPreviewMarkdown } from "@/lib/specs";
import type { FormatSpec } from "@/lib/types";
import Prose from "@/components/reader/Prose";
import { Drawer } from "@/components/ui/Drawer";
import ResourceManager from "./ResourceManager";
import SourceCards from "./SourceCards";
import ImportUnderstanding from "./ImportUnderstanding";
import { AssetImport, SpecEditor, PrincipleEditor } from "./AssetEditor";
import ResourceActions from "./ResourceActions";

type Tab = "specs" | "knowledge" | "profiles" | "materials" | "manage" | "trash";
const TABS: { id: Tab; label: string }[] = [
  { id: "specs", label: "格式规范" },
  { id: "knowledge", label: "知识与命题" },
  { id: "profiles", label: "质量量规" },
  { id: "materials", label: "资料" },
];

type Page<T> = { items: T[]; total?: number };
type Profile = { domain_key: string; display_name: string; tutorial: ProfileBody; exercise: ProfileBody };
type PlanningGuidance = {
  rubric_id: string;
  version: number;
  hash: string;
  status: string;
  organization?: string[];
  knowledge_chain?: string[];
  question_design?: string[];
  quantity_policy?: string[];
  difficulty_policy?: string[];
  output_design?: string[];
  source_refs?: string[];
};
type ProfileBody = { status: string; calibration_status: string; planning_guidance?: PlanningGuidance; rules: { rule_id: string; criterion: string; required: boolean; decision_type: string; evidence_method_refs: string[] }[] };
type Question = {
  id: string;
  raw_stem?: string;
  question_number?: string;
  parse_status?: string;
  stem?: string;
  question_text?: string;
  status?: string;
  source_hash?: string;
  domain_key?: string;
  branch?: string;
  source_map?: { source_identity?: { attribution?: string; license?: string; url?: string; verification_level?: string } };
};
function questionTitle(question: Question) { return [question.question_number, question.raw_stem || question.stem || question.question_text || "题面暂不可用"].filter(Boolean).join(" "); }
type Principle = { id: string; status: string; statement?: string; version?: number; mechanism?: string; guidance?: string; };

export default function Library() {
  const router = useRouter();
  const params = useSearchParams();
  const tab = (params.get("tab") as Tab) || "specs";
  // 旧深链 /library?tab=trash 继续映射到合并视图的回收站子视图；tab=manage 是合并视图入口。
  const manageOpen = tab === "manage" || tab === "trash";
  const initialManageView = tab === "trash" ? "trashed" : "active";
  const setTab = (t: Tab) => router.replace(`/library?tab=${t}`);
  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="title">资源库</h1>
        </div>
        <button className="btn" aria-pressed={manageOpen} onClick={() => setTab("manage")}>管理与回收</button>
      </div>
      <div className="seg" role="group" aria-label="资源类型" style={{ marginBottom: 16, flexWrap: "wrap" }}>
        {TABS.map((t) => <button key={t.id} aria-pressed={!manageOpen && tab === t.id} onClick={() => setTab(t.id)}>{t.label}</button>)}
      </div>
      {manageOpen ? (
        <ResourceManager key={initialManageView} initialView={initialManageView} />
      ) : (<>
        {tab === "specs" && <Specs />}
        {tab === "knowledge" && <Knowledge />}
        {tab === "profiles" && <Profiles />}
        {tab === "materials" && <Materials />}
      </>)}
    </>
  );
}function useAsset<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    setLoading(true);
    get<T>(path).then((d) => { setData(d); setError(""); }).catch((e) => setError(explain(e))).finally(() => setLoading(false));
  }, [path]);
  useEffect(() => { const t = setTimeout(load, 0); return () => clearTimeout(t); }, [load]);
  return { data, error, loading, reload: load };
}

function State({ loading, error, reload, empty, children }: { loading: boolean; error: string; reload: () => void; empty?: string | null; children: React.ReactNode }) {
  if (loading) return <div className="skeleton" style={{ height: 160 }} aria-busy="true" />;
  if (error) return <div className="banner banner-danger" role="alert"><div>{error}</div><button className="btn btn-sm" onClick={reload}>重试</button></div>;
  if (empty) return <div className="empty panel"><p>{empty}</p></div>;
  return <>{children}</>;
}

function Specs() {
  const { data, error, loading, reload } = useAsset<{ items: FormatSpec[] }>("/format-specs");
  const [open, setOpen] = useState<FormatSpec | null>(null);
  const [create, setCreate] = useState(false);
  const items = data?.items ?? [];
  return (
    <>
    <div className="knowledge-map-tools"><button className="btn" onClick={() => setCreate(true)}>新建规范</button></div>
    <AssetImport kind="specs" onSaved={reload} />
    <Drawer open={create} onClose={() => setCreate(false)} title="新建格式规范"><SpecEditor onSaved={reload} /></Drawer>
    <State loading={loading} error={error} reload={reload} empty={items.length === 0 ? "还没有格式规范。" : null}>
      <div className="panel table-wrap">
        <table className="data cards">
          <thead><tr><th>名称</th><th>适用</th><th>版本</th><th>来源</th></tr></thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id}>
                <td data-label="名称"><button className="btn btn-quiet" style={{ padding: 0, fontWeight: 600, minHeight: 32 }} onClick={() => setOpen(s)}>{s.name}</button><div className="hint">{s.description}</div></td>
                <td data-label="适用">{specGroupLabel(s)}</td>
                <td data-label="版本" className="num">v{s.version}</td>
                <td data-label="来源">{s.is_builtin ? "内置" : "自定义"}{s.is_default ? "（默认）" : ""}{!s.is_builtin && <ResourceActions kind="specs" id={s.id} label={s.name} onChanged={reload} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Drawer open={Boolean(open)} onClose={() => setOpen(null)} title={open?.name ?? "规范"}>
        {open && (
          <>
            <SpecEditor key={`${open.id}:${open.version}`} source={open} onSaved={reload} />
            <Prose source={specPreviewMarkdown(open)} />
          </>
        )}
      </Drawer>
    </State>
    </>
  );
}

function Profiles() {
  const { data, error, loading, reload } = useAsset<{ items: Profile[]; calibration_status: string }>("/quality-profiles");
  const [open, setOpen] = useState<Profile | null>(null);
  const items = data?.items ?? [];
  return (
    <State loading={loading} error={error} reload={reload} empty={items.length === 0 ? "没有量规。" : null}>
      <p className="hint" style={{ marginBottom: 12 }}>每个学科都有教程和练习两套量规。状态为“已编写，未校准”：规则有来源，但没有经过独立校准，不能据此认定专业质量。</p>
      <div className="panel table-wrap">
        <table className="data cards">
          <thead><tr><th>学科</th><th>教程量规</th><th>练习量规</th></tr></thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.domain_key}>
                <td data-label="学科"><button className="btn btn-quiet" style={{ padding: 0, fontWeight: 600, minHeight: 32 }} onClick={() => setOpen(p)}>{p.display_name}</button></td>
                <td data-label="教程">{statusText(p.tutorial)}</td>
                <td data-label="练习">{statusText(p.exercise)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Drawer open={Boolean(open)} onClose={() => setOpen(null)} title={open?.display_name ?? "量规"}>
        {open && (["tutorial", "exercise"] as const).map((k) => (
          <section key={k} style={{ marginBottom: 20 }}>
            <h3 className="sub">{k === "tutorial" ? "教程" : "练习"}（{statusText(open[k])}）</h3>
            <ul>
              {open[k].rules.map((r) => (
                <li key={r.rule_id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                  <div>{r.criterion}</div>
                  <div className="hint">{r.required ? "必需" : "可选"} · 方法：{r.evidence_method_refs.join("、") || "—"}</div>
                </li>
              ))}
            </ul>
            {open[k].planning_guidance && <section style={{ marginTop: 16 }}>
              <h4>规划指导（v{open[k].planning_guidance!.version}，已编写未校准）</h4>
              {([
                ["organization", "组织形式"],
                ["knowledge_chain", "知识链"],
                ["question_design", "题型与题量"],
                ["quantity_policy", "数量策略"],
                ["difficulty_policy", "难度目标"],
                ["output_design", "输出设计"],
              ] as const).map(([field, label]) => {
                const rows = open[k].planning_guidance![field];
                return rows?.length ? <div key={field} style={{ marginTop: 10 }}>
                  <b>{label}</b><ul>{rows.map((row) => <li key={row}>{row}</li>)}</ul>
                </div> : null;
              })}
              <p className="hint break-anywhere">量规 hash：{open[k].planning_guidance!.hash}。它不是文献阈值或专业认证。</p>
            </section>}
          </section>
        ))}
      </Drawer>
    </State>
  );
}
function statusText(p: ProfileBody) {
  const s = p.status === "authored_uncalibrated" ? "已编写，未校准" : p.status === "inherited_only" ? "仅继承通用量规" : p.status;
  return `${s}（${p.rules.length} 条规则）`;
}

function Knowledge() {
  const [editing, setEditing] = useState<Principle | null>(null);
  const [understandingRevision, setUnderstandingRevision] = useState(0);
  const q = useAsset<Page<Question>>("/knowledge/assets/questions?limit=50");
  const p = useAsset<Page<Principle>>("/knowledge/assets/principles?limit=50");
  const b = useAsset<Page<{ id: string; filename: string; status: string; counts?: Record<string, number>; source_identity?: { license?: string; url?: string; attribution?: string; verification_level?: string } }>>("/knowledge/assets/batches?limit=50");
  return (
    <div style={{ display: "grid", gap: 24 }}>
      <ImportUnderstanding revision={understandingRevision} />
      <EvaluationFrameworks />
      <Drawer open={Boolean(editing)} onClose={() => setEditing(null)} title="修订设计原理">{editing && <PrincipleEditor key={`${editing.id}:${editing.version}`} source={editing} onSaved={() => { p.reload(); setEditing(null); }} />}</Drawer>
      <AssetImport kind="knowledge" onSaved={() => { q.reload(); p.reload(); b.reload(); setUnderstandingRevision((v) => v + 1); }} />
      <SourceCards />
      <section>
        <h2 className="sub" style={{ marginBottom: 8 }}>导入批次</h2>
        <State loading={b.loading} error={b.error} reload={b.reload} empty={(b.data?.items.length ?? 0) === 0 ? "还没有命题知识。导入历年试题后，会在这里出现，原文和外部评价都会保留。" : null}>
          <div className="panel table-wrap"><table className="data cards"><thead><tr><th>文件</th><th>状态</th><th>题数</th></tr></thead><tbody>
            {b.data?.items.map((x) => <tr key={x.id}><td data-label="文件">{x.filename}<ResourceActions kind="batches" id={x.id} label={x.filename} onChanged={b.reload} />{x.source_identity?.attribution && <div className="hint break-anywhere">{x.source_identity.attribution}{x.source_identity.license ? ` · ${x.source_identity.license}` : ""}</div>}</td><td data-label="状态">{x.status === "parsed" ? "已导入" : x.status}</td><td data-label="题数" className="num">{Object.values(x.counts ?? {}).reduce((n, v) => n + (typeof v === "number" ? v : 0), 0)}</td></tr>)}
          </tbody></table></div>
        </State>
      </section>
      <section>
        <h2 className="sub" style={{ marginBottom: 8 }}>题目（{q.data?.total ?? 0}）</h2>
        <State loading={q.loading} error={q.error} reload={q.reload} empty={(q.data?.items.length ?? 0) === 0 ? "没有题目。" : null}>
          <ul className="panel">{q.data?.items.map((x) => <li key={x.id} style={{ padding: 12, borderBottom: "1px solid var(--line)" }}>
            {questionTitle(x).slice(0, 180)}
            <div className="hint">{x.parse_status === "ready" ? "已解析原文（不代表质量通过）" : x.parse_status === "failed" ? "原文解析失败" : x.status ?? "状态未知"}</div>
            {x.source_map?.source_identity && <div className="hint break-anywhere">
              {x.source_map.source_identity.attribution ?? x.source_map.source_identity.url}
              {x.source_map.source_identity.license ? ` · ${x.source_map.source_identity.license}` : ""}
              {x.source_map.source_identity.verification_level ? ` · ${x.source_map.source_identity.verification_level}` : ""}
            </div>}
            <ResourceActions kind="questions" id={x.id} label={questionTitle(x).slice(0, 40)} onChanged={q.reload} />
          </li>)}</ul>
        </State>
      </section>
      <section>
        <h2 className="sub" style={{ marginBottom: 8 }}>设计原理（{p.data?.total ?? 0}）</h2>
        <p className="hint" style={{ marginBottom: 8 }}>候选（proposed）、已驳回、已撤回的原理不会被当作可用指导。</p>
        <State loading={p.loading} error={p.error} reload={p.reload} empty={(p.data?.items.length ?? 0) === 0 ? "没有原理。" : null}>
          <ul className="panel">{p.data?.items.map((x) => <li key={x.id} style={{ padding: 12, borderBottom: "1px solid var(--line)" }}>{x.statement ?? "原理正文暂不可用"}<div className="hint">{x.status === "confirmed" ? "已确认" : x.status === "proposed" ? "候选（不作为指导）" : x.status === "rejected" ? "已驳回" : x.status === "retracted" ? "已撤回" : x.status}</div><div className="row-actions"><button className="btn btn-sm" onClick={() => setEditing(x)}>修订原理</button><ResourceActions kind="principles" id={x.id} label={x.statement ?? "设计原理"} onChanged={p.reload} /></div></li>)}</ul>
        </State>
      </section>
    </div>
  );
}

function EvaluationFrameworks() {
  const f = useAsset<Page<{ id: string; title: string; domain_key: string; version: number; status: string; source_hash: string; standards: { id: string; name: string; rule: string; source_ref?: string; origin?: string }[]; warnings?: string[]; evidence_refs?: { title?: string; url?: string; license?: string; verification_level?: string }[] }>>("/evaluation-frameworks");
  return <section aria-label="已安装评价体系">
    <h2 className="sub">已安装评价体系</h2>
    <p className="hint">评价体系与导入题目分别保存。形式验证只判定已绑定的断言；独立 AI 观察、专业校准和实测难度分别记录。</p>
    <State loading={f.loading} error={f.error} reload={f.reload} empty={!f.data?.items.length ? "尚未安装评价体系。" : null}>
      {f.data?.items.map((x) => <details key={x.id} className="panel panel-pad"><summary>{x.title} · v{x.version}</summary>
        <p className="hint">{x.domain_key} · {x.status === "active" ? "已启用，尚未专业校准" : x.status}</p>
        <ul>{x.standards.map((s) => <li key={s.id}><b>{s.name}</b>：{s.rule}</li>)}</ul>
        {x.evidence_refs?.length ? <div><b>来源与许可</b><ul>{x.evidence_refs.map((ref, index) => <li key={`${ref.title ?? index}`} className="hint break-anywhere">{ref.url ? <a href={ref.url} target="_blank" rel="noreferrer">{ref.title ?? ref.url}</a> : ref.title}{ref.license ? ` · ${ref.license}` : ""}{ref.verification_level ? ` · ${ref.verification_level}` : ""}</li>)}</ul></div> : null}
      </details>)}
    </State>
  </section>;
}

function Materials() {
  return <><p className="hint">资料在新建任务时上传并保留原文；这里管理 2.0 任务资料。解析不完整会标明。</p><ResourceManager initialKind="materials" /></>;
}


