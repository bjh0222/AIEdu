"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { get, explain } from "@/lib/v2/client";

const KINDS: { id: string; label: string; cols: [string, string][] }[] = [
  { id: "goals", label: "学习目标", cols: [["title", "标题"], ["domain_key", "学科"], ["created_at", "创建时间"]] },
  { id: "tutorials", label: "教程", cols: [["title", "标题"], ["goal_id", "所属目标"], ["created_at", "创建时间"]] },
  { id: "versions", label: "教程版本", cols: [["version", "版本"], ["model_provider", "来源"], ["created_at", "创建时间"]] },
  { id: "materials", label: "资料", cols: [["filename", "文件"], ["extract_status", "解析状态"], ["size", "大小"]] },
  { id: "practice", label: "练习", cols: [["id", "编号"], ["created_at", "创建时间"]] },
  { id: "attempts", label: "作答", cols: [["id", "编号"], ["created_at", "创建时间"]] },
  { id: "concepts", label: "概念", cols: [["name", "概念"], ["created_at", "创建时间"]] },
  { id: "relations", label: "概念关系", cols: [["id", "编号"], ["created_at", "创建时间"]] },
  { id: "frameworks", label: "评价框架", cols: [["name", "名称"], ["created_at", "创建时间"]] },
];

type Row = Record<string, unknown>;
const PAGE = 50;

export default function Legacy({ kind }: { kind: string }) {
  const spec = KINDS.find((k) => k.id === kind) ?? KINDS[0];
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ items: Row[]; total: number } | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      setLoading(true);
      get<{ items: Row[]; total: number }>(`/legacy-assets/${spec.id}?limit=${PAGE}&offset=${offset}`)
        .then((d) => { if (alive) { setData(d); setError(""); } })
        .catch((e) => alive && setError(explain(e)))
        .finally(() => alive && setLoading(false));
    }, 0);
    return () => { alive = false; clearTimeout(t); };
  }, [spec.id, offset]);

  return (
    <>
      <div className="crumbs"><Link href="/library">资源库</Link><span aria-hidden="true">/</span><span aria-current="page">旧数据</span></div>
      <h1 className="title" style={{ marginBottom: 8 }}>旧数据（只读）</h1>
      <div className="banner banner-warn" style={{ marginBottom: 16 }}>
        <div>这里显示 1.0 时期保存的内容，只能查看。它们没有经过 2.0 的质量检查，<b>不会被当作“已通过”</b>，也不能在这里修改或重新生成。旧地址不会被猜测映射到新任务。</div>
      </div>
      <div className="seg" role="group" aria-label="旧数据类型" style={{ marginBottom: 16, flexWrap: "wrap" }}>
        {KINDS.map((k) => <Link key={k.id} href={`/legacy/${k.id}`} className="btn btn-sm" aria-current={k.id === spec.id ? "page" : undefined} style={{ borderRadius: 0, fontWeight: k.id === spec.id ? 700 : 400 }}>{k.label}</Link>)}
      </div>

      {loading && <div className="skeleton" style={{ height: 160 }} aria-busy="true" />}
      {error && <div className="banner banner-danger" role="alert"><div>{error}</div></div>}
      {!loading && !error && data && data.items.length === 0 && <div className="empty panel"><p>没有这类旧数据。</p></div>}
      {!loading && data && data.items.length > 0 && (
        <>
          <div className="panel table-wrap">
            <table className="data cards">
              <thead><tr>{spec.cols.map(([, l]) => <th key={l}>{l}</th>)}</tr></thead>
              <tbody>
                {data.items.map((r, i) => (
                  <tr key={String(r.id ?? i)}>
                    {spec.cols.map(([k, l]) => <td key={k} data-label={l}>{String(r[k] ?? "")}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint" style={{ marginTop: 8 }}>共 {data.total} 条，显示 {offset + 1}–{Math.min(offset + PAGE, data.total)}。</p>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>上一页</button>
            <button className="btn btn-sm" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>下一页</button>
          </div>
        </>
      )}
    </>
  );
}
