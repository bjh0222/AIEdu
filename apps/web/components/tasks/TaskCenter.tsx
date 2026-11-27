"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/v2/api";
import type { TaskListItem } from "@/lib/v2/contracts.generated";
import { explain } from "@/lib/v2/client";
import { EXAMPLES } from "./examples";
import TaskCards from "./TaskCards";

function timeValue(value: string | undefined): number {
  const parsed = Date.parse(value ?? "");
  return Number.isNaN(parsed) ? 0 : parsed;
}

export default function TaskCenter() {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<TaskListItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [more, setMore] = useState(false);
  const [slow, setSlow] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async (reset: boolean, q: string, from: string | null) => {
    const my = ++seq.current;
    setMore(!reset);
    try {
      const page = await api.listTasks(reset ? null : from, q);
      if (my !== seq.current) return;
      setItems((old) => {
        const merged = reset ? page.items : [...old, ...page.items];
        const seen = new Set<string>();
        const unique = merged.filter((t) => (seen.has(t.task_id) ? false : (seen.add(t.task_id), true)));
        return unique.sort((a, b) => timeValue(b.updated_at) - timeValue(a.updated_at) || a.task_id.localeCompare(b.task_id));
      });
      setCursor(page.next_cursor);
      setState("ready");
      setError("");
    } catch (e) {
      if (my !== seq.current) return;
      setError(explain(e));
      setState((s) => (s === "ready" ? s : "error"));
    } finally {
      if (my === seq.current) setMore(false);
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => void load(true, search, null), search ? 300 : 0);
    return () => clearTimeout(t);
  }, [search, load]);

  useEffect(() => {
    if (state !== "loading") {
      const t = setTimeout(() => setSlow(false), 0);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setSlow(true), 3000);
    return () => clearTimeout(t);
  }, [state]);

  const shown = useMemo(() => items, [items]);
  const attention = items.filter((t) => t.needs_action);

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="title">任务</h1>
        </div>
        <div className="row-actions"><Link className="btn" href="/library?tab=trash">回收站</Link><Link className="btn" href="/import">导入外部任务</Link><Link className="btn btn-primary" href="/new">新建任务</Link></div>
      </div>

      {error && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: 16 }}>
          <div>
            <b>无法读取任务列表。</b> {error}
            {items.length > 0 && <span> 下面显示的是上次读取的内容，可能已过期。</span>}
          </div>
          <button className="btn btn-sm" onClick={() => void load(true, search, null)}>重试</button>
        </div>
      )}

      {attention.length > 0 && (
        <div className="banner banner-warn" style={{ marginBottom: 16 }}>
          <div>
            有 {attention.length} 个任务需要你处理：
            {attention.slice(0, 3).map((t) => (
              <span key={t.task_id}> <Link href={`/tasks/${t.task_id}`}>{t.title}</Link></span>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        <div className="field" style={{ minWidth: 220 }}>
          <label className="sr-only" htmlFor="task-search">按标题搜索</label>
          <input id="task-search" type="search" placeholder="按标题搜索" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={200} />
        </div>
      </div>

      {state === "loading" && (
        <div aria-busy="true" aria-label="正在加载任务">
          {Array.from({ length: 4 }, (_, i) => <div key={i} className="skeleton" style={{ height: 104, marginBottom: 12 }} />)}
          {slow && <p className="muted">仍在加载…</p>}
        </div>
      )}

      {state === "error" && items.length === 0 && (
        <div className="empty"><p>还没有读到任务。后端可能没有启动。</p></div>
      )}

      {state === "ready" && items.length === 0 && !search && (
        <div className="empty panel">
          <h2 className="sub">还没有任务</h2>
          <p>描述你想学的或想出的内容，系统会自动选配规范、生成并检查。下面的例子可以直接改。</p>
          <ul className="chips">
            {EXAMPLES.map((e) => (
              <li key={e.label}>
                <button className="chip" onClick={() => router.push(`/new?example=${encodeURIComponent(e.text)}`)}>
                  <b>{e.label}</b><br /><span className="muted">{e.text}</span>
                </button>
              </li>
            ))}
          </ul>
          <Link className="btn btn-primary" href="/new">新建任务</Link>
        </div>
      )}

      {state === "ready" && items.length > 0 && shown.length === 0 && search && (
        <div className="empty"><p>没有标题包含“{search}”的任务。</p></div>
      )}

      {state === "ready" && shown.length > 0 && (
        <TaskCards items={shown} onChanged={() => void load(true, search, null)} />
      )}

      {cursor && (
        <div style={{ textAlign: "center", marginTop: 16 }}>
          <button className="btn" aria-busy={more} disabled={more} onClick={() => void load(false, search, cursor)}>加载更多</button>
        </div>
      )}
    </>
  );
}
