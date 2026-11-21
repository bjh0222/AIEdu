"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, clearPending, getReceipt, newKey, pendingActions, UnknownOutcomeError } from "@/lib/v2/client";
import { management, cleanupKeys, rememberCleanup, RESOURCE_KINDS, type ManagedPage, type ManagedOperation } from "@/lib/v2/management-api";
import { managementError, ResourceActionDialog } from "./ResourceActions";

type View = "active" | "archived" | "trashed";
const STATE: Record<string, string> = { completed: "已结束", running: "运行中", queued: "排队中", paused: "已暂停", failed: "失败", ready: "可用", parsed: "已导入", pending: "待处理", confirmed: "已整理", proposed: "候选", active: "已启用" };

export default function ResourceManager({ initialKind = "tasks", initialView = "active", onChanged }: { initialKind?: string; initialView?: View; onChanged?: () => void }) {
  const [kind, setKind] = useState(initialKind), [view, setView] = useState<View>(initialView);
  const [search, setSearch] = useState(""), [query, setQuery] = useState(""), [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ManagedPage | null>(null), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true), [reload, setReload] = useState(0), [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<ManagedOperation | null>(null), [targets, setTargets] = useState<string[]>([]);
  const [pending, setPending] = useState<string[]>([]);
  const [trashBusy, setTrashBusy] = useState(false);
  const [undo, setUndo] = useState<{ kind: string; ids: string[] } | null>(null);
  const [cleanups, setCleanups] = useState<string[]>([]);
  useEffect(() => {
    const abort = new AbortController();
    const queryString = new URLSearchParams({ search: query, offset: String(offset), limit: "25", archived: String(view === "archived"), trashed: String(view === "trashed") });
    management.catalog(kind, queryString, abort.signal).then((v) => {
      if (abort.signal.aborted) return;
      setPage(v); setSelected([]); setError(""); setLoading(false);
      setPending(pendingActions().filter((p) => p.action_kind === "resource_management").map((p) => p.key));
      setCleanups(cleanupKeys());
    }).catch((e) => { if (!abort.signal.aborted) { setError(managementError(e)); setLoading(false); } });
    return () => abort.abort();
  }, [kind, query, offset, view, reload]);
  function reset() { setPage(null); setLoading(true); setSelected([]); setOffset(0); }
  function choose(action: ManagedOperation, identities = selected) {
    const unresolved = pendingActions().filter((p) => p.action_kind === "resource_management");
    if (action === "trash") { void trashNow(identities); return; }
    if (unresolved.length) { setPending(unresolved.map((p) => p.key)); setError("有操作尚未确认，请先查询回执。"); return; }
    setTargets(identities); setOperation(action);
  }
  async function trashNow(identities = selected) {
    if (trashBusy || !identities.length) return;
    const unresolved = pendingActions().filter((p) => p.action_kind === "resource_management");
    if (unresolved.length) { setPending(unresolved.map((p) => p.key)); setError("有操作尚未确认，请先查询回执。"); return; }
    setTrashBusy(true); setError("");
    try {
      const impacts = await Promise.all(identities.map((resourceId) => management.impact(kind, resourceId)));
      const key = newKey();
      const receipt = await management.change("trash", impacts, key, {}, "");
      rememberCleanup(key, Boolean(receipt.cleanup_pending?.length)); setCleanups(cleanupKeys());
      setUndo({ kind, ids: [...identities] });
      setNotice(receipt.cleanup_pending?.length ? "元数据已清除，但原文文件清理尚未完成。" : "已移入回收站。");
      setSelected([]); setReload((v) => v + 1); onChanged?.();
    } catch (e) {
      if (e instanceof UnknownOutcomeError) setPending((v) => v.includes(e.key) ? v : [...v, e.key]);
      if (e instanceof ApiError && e.code === "RESOURCE_STALE") setNotice("");
      setError(managementError(e));
    } finally { setTrashBusy(false); }
  }
  async function check(key: string) {
    try {
      const receipt = await getReceipt(key);
      if (receipt.status === "completed") {
        clearPending(key); setPending((v) => v.filter((k) => k !== key));
        const result = receipt.response as { cleanup_pending?: unknown[] } | undefined;
        rememberCleanup(key, Boolean(result?.cleanup_pending?.length)); setCleanups(cleanupKeys());
        setNotice(result?.cleanup_pending?.length ? "元数据已清除，原文文件清理仍未完成。请保留回执，由管理者处理。" : "已确认操作结果。");
        setReload((v) => v + 1);
      } else setNotice("尚未找到回执，结果仍未确认。请稍后查询，不要重复提交。");
    } catch (e) { setError(managementError(e)); }
  }
  return <section className="panel panel-pad resource-manager" aria-label="资源管理">
    <h2 className="sub">资源与回收站</h2>
    <div className="seg" role="group" aria-label="资源目录">
      {([["active", "常规目录"], ["archived", "归档"], ["trashed", "回收站"]] as const).map(([id, name]) => <button key={id} aria-pressed={view === id} onClick={() => { reset(); setView(id); }}>{name}</button>)}
    </div>
    <p className="hint">{view === "trashed" ? "原文仍保留，可恢复。永久清除是独立操作，有历史引用时会拒绝。" : "归档与删除分开；移入回收站后可恢复，不改变已有历史证据。"}</p>
    <form className="resource-filters" onSubmit={(e) => { e.preventDefault(); reset(); setQuery(search); setReload((v) => v + 1); }}>
      <label>资源类型<select value={kind} onChange={(e) => { reset(); setKind(e.target.value); }}>{Object.entries(RESOURCE_KINDS).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label>搜索名称<input value={search} maxLength={200} onChange={(e) => setSearch(e.target.value)} /></label><button className="btn">搜索</button>
    </form>
    {error && <p className="err" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {undo && <button className="btn" onClick={() => { reset(); setKind(undo.kind); setTargets(undo.ids); setOperation("restore_trash"); setUndo(null); }}>撤销移入回收站</button>}
    {pending.map((key, i) => <button key={key} className="btn" onClick={() => void check(key)}>查询未确认操作回执{pending.length > 1 ? `（第${i + 1}项）` : ""}</button>)}
    {cleanups.map((key, i) => <button key={key} className="btn" onClick={async () => { try { const r = await management.cleanup(key); rememberCleanup(key, Boolean(r.cleanup_pending?.length)); setCleanups(cleanupKeys()); setNotice(r.cleanup_pending?.length ? "原文文件仍被占用，清理未完成。" : "原回执文件清理已完成。"); } catch (e) { setError(managementError(e)); setReload((v) => v + 1); } }}>继续清理未完成文件（{i + 1}）</button>)}
    {loading && <p aria-busy="true">正在读取资源…</p>}
    <div className="table-wrap"><table className="data cards"><thead><tr><th>选择</th><th>资源</th><th>状态</th><th>操作</th></tr></thead><tbody>
      {page?.items.map((r) => <tr key={r.id}>
        <td data-label="选择"><input type="checkbox" aria-label={`选择 ${r.label}`} checked={selected.includes(r.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, r.id] : selected.filter((x) => x !== r.id))} /></td>
        <td data-label="资源">{r.label}{typeof r.metadata.version === "number" && <span className="hint">（版本 {r.metadata.version}）</span>}</td>
        <td data-label="状态">{view === "trashed" ? "回收站" : view === "archived" ? "已归档" : STATE[String(r.metadata.status ?? r.metadata.state ?? r.metadata.extract_status)] ?? "已保存"}</td>
        <td data-label="操作"><div className="row-actions">
          {view === "trashed" ? <><button className="btn btn-sm" onClick={() => choose("restore_trash", [r.id])}>恢复</button><button className="btn btn-sm btn-danger" onClick={() => choose("purge", [r.id])}>永久清除</button></> :
            <><button className="btn btn-sm" aria-busy={trashBusy} disabled={trashBusy} onClick={() => choose("trash", [r.id])}>移入回收站</button><button className="btn btn-sm" onClick={() => choose(view === "archived" ? "restore" : "archive", [r.id])}>{view === "archived" ? "恢复归档" : "归档"}</button></>}
          <button className="btn btn-sm" onClick={() => choose("label", [r.id])}>查看引用 / 改名</button>
          {kind === "tasks" && <Link href={`/tasks/${r.id}`}>打开工作台</Link>}
          {typeof r.metadata.goal_id === "string" && <Link href={`/tasks/${r.metadata.goal_id}`}>所属任务</Link>}
          {kind === "model-profiles" && <Link href="/settings">管理角色引用</Link>}
        </div></td>
      </tr>)}
    </tbody></table></div>
    {page?.total === 0 && <p className="empty">当前条件没有资源。</p>}
    <div className="row-actions">
      <button className="btn" disabled={offset === 0 || loading} onClick={() => { setLoading(true); setOffset(Math.max(0, offset - 25)); }}>上一页</button>
      <span>{page?.total ?? 0} 项 · 第 {Math.floor(offset / 25) + 1} 页</span>
      <button className="btn" disabled={page?.next_offset == null || loading} onClick={() => { setLoading(true); setOffset(page!.next_offset!); }}>下一页</button>
      <button className="btn" disabled={!selected.length || Boolean(pending.length) || trashBusy} aria-busy={trashBusy} onClick={() => choose(view === "trashed" ? "restore_trash" : "trash")}>{view === "trashed" ? "批量恢复" : trashBusy ? "正在移入回收站" : "批量移入回收站"}</button>
    </div>
    {operation && <ResourceActionDialog key={`${operation}:${targets.join(",")}`} targets={targets.map((id) => ({ kind, id }))} operation={operation}
      onClose={() => setOperation(null)} onChanged={() => { setNotice(`${operation === "purge" ? "已完成永久清除" : "操作已保存"}。`); setReload((v) => v + 1); onChanged?.(); }} />}
  </section>;
}
