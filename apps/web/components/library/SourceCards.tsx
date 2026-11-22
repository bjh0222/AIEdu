"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { explain, get } from "@/lib/v2/client";

type EvidenceRef = { id?: string; title?: string; url?: string; license?: string; verification_level?: string; source_card?: string };
type Card = {
  card_id: string;
  task_id?: string | null;
  title: string;
  quote: string;
  source_kind?: string;
  source_refs?: EvidenceRef[];
  source: { version_id?: string; unit_id?: string; import_id?: string; import_hash?: string };
};
export default function SourceCards() {
  const [query, setQuery] = useState(""), [search, setSearch] = useState(""), [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ items: Card[]; total: number } | null>(null), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    get<{ items: Card[]; total: number }>(`/knowledge/cards?q=${encodeURIComponent(search)}&offset=${offset}&limit=20`, { signal: controller.signal })
      .then((v) => { if (!controller.signal.aborted) { setData(v); setError(""); } }).catch((e) => { if (!controller.signal.aborted) setError(explain(e)); });
    return () => controller.abort();
  }, [search, offset]);
  return <section className="panel panel-pad" aria-label="已整理知识卡检索"><h2 className="sub">已整理知识卡</h2>
    <p className="hint">仅检索当前版本、已确认且非演示的原文片段。来源更新或撤回后不再用于新任务；整理确认不代表专业认证。</p>
    <form onSubmit={(e) => { e.preventDefault(); setOffset(0); setSearch(query); }}><label>检索知识卡<input value={query} maxLength={1000} onChange={(e) => setQuery(e.target.value)} /></label><button className="btn">搜索卡片</button></form>
    {error && <p role="alert">{error}</p>}
    {data && <><p role="status">{data.total} 张卡片</p><ul>{data.items.map((c) => <li key={c.card_id}><h3 className="sub">{c.title}</h3><blockquote>{c.quote}</blockquote>
      <p className="hint">{c.source_kind === "research_import_card" ? "研究导入卡（AI 研究归纳，未专业校准）" : "固定教程版本中的已确认来源片段"}</p>
      {c.source_refs?.length ? <ul>{c.source_refs.map((ref, index) => <li key={`${ref.id ?? ref.source_card ?? index}`} className="hint break-anywhere">
        {ref.url ? <a href={ref.url} target="_blank" rel="noreferrer">{ref.title ?? ref.id ?? "来源"}</a> : (ref.title ?? ref.id ?? ref.source_card)}
        {ref.license ? ` · 许可：${ref.license}` : ""}{ref.verification_level ? ` · 核验：${ref.verification_level}` : ""}
      </li>)}</ul> : null}
      {c.task_id && c.source.version_id && c.source.unit_id
        ? <Link className="btn btn-sm" href={`/tasks/${c.task_id}/read?version=${c.source.version_id}&unit=${c.source.unit_id}`}>阅读原文</Link>
        : <span className="hint">来源身份：{c.source.import_id ?? "已登记"}</span>}
    </li>)}</ul>
      <button className="btn btn-sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页卡片</button><button className="btn btn-sm" disabled={offset + 20 >= data.total} onClick={() => setOffset(offset + 20)}>下一页卡片</button></>}
  </section>;
}
