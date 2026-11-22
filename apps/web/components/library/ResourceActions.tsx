"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError, clearPending, explain, getReceipt, newKey, pendingActions, UnknownOutcomeError } from "@/lib/v2/client";
import { Dialog } from "@/components/ui/Dialog";
import { management, rememberCleanup, OPERATION, RESOURCE_KINDS, type ManagedImpact, type ManagedOperation, type ManagedReceipt } from "@/lib/v2/management-api";


// 阻塞来源展示：内容逐字来自后端返回（impact.active_blockers[].reason 或
// RESOURCE_IN_ACTIVE_RUN 错误 details.blockers/summary），前端不编写新的文案。
// 旧后端响应无 active_blockers 时，退回渲染引用条目的 label/state。
const ACTIVE_STATES = new Set(["queued", "running", "sent", "authorized", "unknown"]);
type BackendBlocker = { reason?: unknown; kind?: unknown; state?: unknown };
type BackendBlockerList = { blockers?: BackendBlocker[]; summary?: unknown };
export function backendBlockerLines(detail: unknown): string[] {
  const d = detail as BackendBlockerList | undefined;
  const lines = (Array.isArray(d?.blockers) ? d!.blockers! : [])
    .map((b) => (typeof b?.reason === "string" && b.reason.trim()) || "")
    .filter(Boolean);
  if (!lines.length && typeof d?.summary === "string" && d.summary.trim()) return [d.summary];
  return lines;
}
export function activeBlockers(impacts: ManagedImpact[]): string[] {
  return impacts.filter((x) => x.active).flatMap((x) => {
    const listed = (x as unknown as { active_blockers?: BackendBlocker[] }).active_blockers;
    if (Array.isArray(listed) && listed.length) {
      return listed.map((b) => (typeof b?.reason === "string" && b.reason.trim()) || `${String(b?.kind ?? "引用")}（${String(b?.state ?? "")}）`).filter(Boolean);
    }
    return x.references.filter((r) => r.state && ACTIVE_STATES.has(r.state))
      .map((r) => `${r.label || r.kind}${r.state ? `（${r.state}）` : ""}`);
  });
}

export function managementError(error: unknown) {
  if (error instanceof ApiError) {
    const labels: Record<string, string> = {
      RESOURCE_STALE: "资源或引用已变化。请重新执行；系统会重新检查引用。",
      RESOURCE_PURGE_BLOCKED: "仍有来源或历史证据引用，不能永久清除。请查看下面的阻断说明。",
      RESOURCE_IN_ACTIVE_RUN: "该资源有运行中的任务或未确认调用，暂不能修改。",
      MODEL_PROFILE_IN_USE: "模型仍被当前配置、角色或活动调用使用。先解除引用，再重试。",
      RESOURCE_TRASH_REQUIRED: "请先移入回收站，再单独确认永久清除。",
      RESOURCE_CONFIRMATION_REQUIRED: "请先勾选永久删除确认。",
      BUILTIN_SPEC_IMMUTABLE: "内置或默认规范受保护。自定义规范请先取消默认。",
      RESOURCE_MIXED_STORAGE_BATCH: "模型配置与资料使用不同存储，请分开批量操作。",
    };
    if (labels[error.code]) return labels[error.code];
  }
  return explain(error);
}

function impactTargets(targets: { kind: string; id: string }[]) {
  return targets.map((t) => ({ kind: t.kind, id: t.id }));
}

function unresolvedKey() {
  return pendingActions().find((p) => p.action_kind === "resource_management")?.key ?? null;
}

export function ResourceActionDialog({ targets, operation, onClose, onChanged }: {
  targets: { kind: string; id: string }[]; operation: ManagedOperation; onClose: () => void; onChanged: () => void;
}) {
  const [impacts, setImpacts] = useState<ManagedImpact[] | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [ack, setAck] = useState(false);
  const [names, setNames] = useState<Record<string, string>>({}), [label, setLabel] = useState("");
  const [pending, setPending] = useState<string | null>(null), [notice, setNotice] = useState("");
  const [cleanup, setCleanup] = useState<{ key: string; names: Record<string, string>; label: string } | null>(null);
  const [invalid, setInvalid] = useState(false);
  const identity = targets.map((t) => `${t.kind}/${t.id}`).join("|");
  useEffect(() => {
    const abort = new AbortController();
    const values = identity.split("|").map((t) => { const slash = t.indexOf("/"); return { kind: t.slice(0, slash), id: t.slice(slash + 1) }; });
    Promise.all(values.map((t) => management.impact(t.kind, t.id, abort.signal)))
      .then((v) => {
        if (abort.signal.aborted) return;
        setImpacts(v);
        if (operation === "purge") setNames(Object.fromEntries(v.map((x) => [x.resource.id, x.resource.label])));
      })
      .catch((e) => { if (!abort.signal.aborted) setError(managementError(e)); });
    return () => abort.abort();
  }, [identity, operation]);
  const blocked = impacts?.some((x) => x.active || (operation === "purge" && !x.purge_allowed));
  const ready = Boolean(impacts && !blocked && !invalid && ack && !busy && !pending && !cleanup &&
    (operation !== "purge" || impacts.every((x) => names[x.resource.id] === x.resource.label)) &&
    (operation !== "label" || label.trim()));
  function accepted(receipt: ManagedReceipt, key: string) {
    rememberCleanup(key, Boolean(receipt.cleanup_pending?.length));
    if (receipt.cleanup_pending?.length) {
      setCleanup({ key, names: { ...names }, label });
      setNotice("元数据已清除，但原文文件清理尚未完成。保留此回执，不能视为全部清除。");
      return;
    }
    onChanged(); onClose();
  }
  async function submit() {
    if (!ready || !impacts) return;
    const unresolved = unresolvedKey();
    if (unresolved) { setPending(unresolved); setNotice("存在尚未确认的管理操作，先查询原回执。"); return; }
    const key = newKey(); setBusy(true); setError("");
    try { accepted(await management.change(operation, impacts, key, names, label), key); }
    catch (e) { if (e instanceof UnknownOutcomeError) setPending(e.key); if (e instanceof ApiError && e.code === "RESOURCE_STALE") setInvalid(true); setError(managementError(e)); }
    finally { setBusy(false); }
  }
  async function check() {
    if (!pending) return;
    setBusy(true);
    try {
      const value = await getReceipt(pending);
      if (value.status === "completed") {
        const response = value.response as ManagedReceipt | undefined;
        rememberCleanup(pending, Boolean(response?.cleanup_pending?.length));
        clearPending(pending); setPending(null); setError(""); onChanged(); onClose();
      } else setNotice("服务端尚未找到回执，结果仍未确认。请稍后继续查询，不要重复提交。");
    } catch (e) { setError(managementError(e)); } finally { setBusy(false); }
  }
  const referenceCount = impacts?.reduce((sum, x) => sum + x.references.length, 0) ?? 0;
  return <Dialog open title={`${OPERATION[operation]}${targets.length > 1 ? `（${targets.length} 项）` : ""}`} onClose={onClose}>
    <div className="resource-confirm">
      {!impacts && !error && <p aria-busy="true">正在检查资源与引用…</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {cleanup && <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { accepted(await management.cleanup(cleanup.key), cleanup.key); } catch (e) { if (e instanceof UnknownOutcomeError) setPending(e.key); setError(managementError(e)); } finally { setBusy(false); } }}>继续清理原回执文件</button>}
      {operation === "purge" && impacts && <p className="hint">{`已检查 ${targets.length} 项资源 · ${referenceCount} 条引用。`}</p>}
      {operation !== "purge" && impacts?.map((x) => <section key={x.resource.id}>
        <h3 className="sub">{x.resource.label}</h3>
        <p className="hint">{RESOURCE_KINDS[x.resource.kind]}；{x.active ? "有活动任务或未确认调用，暂不可操作" : `${x.references.length} 条引用`}</p>
        <ul className="reference-list">{x.references.map((r, i) => <li key={i}>{r.task_id ? <Link href={`/tasks/${r.task_id}`}>{r.label || "查看相关任务"}</Link> : r.label || RESOURCE_KINDS[r.kind] || "关联记录"}{r.state && <span className="hint">（{r.state}）</span>}</li>)}</ul>
        <p>{x.effects?.[operation] ?? (operation === "restore_trash" ? "恢复后回到移入回收站前的目录；原文与历史版本不变。" : "仅改变目录状态，原文和历史绑定保留。")}</p>
      </section>)}
      {operation === "purge" && impacts?.some((x) => x.purge_blockers?.length) && impacts.flatMap((x) => x.purge_blockers ?? []).map((b, i) => <p key={i} className="err">{b.reason}</p>)}
      {operation === "label" && <label>显示名称<input maxLength={200} value={label} onChange={(e) => setLabel(e.target.value)} /></label>}
      {impacts && <label className="check-label"><input type="checkbox" checked={ack} disabled={Boolean(blocked || pending)} onChange={(e) => setAck(e.target.checked)} />{operation === "purge" ? "我确认永久删除，不可恢复" : "我已查看引用和影响"}</label>}
      <p className="hint">批量操作全部成功或全部拒绝；不会跳过失败项后显示整批成功。</p>
      {pending ? <button className="btn" disabled={busy} onClick={() => void check()}>查询未确认操作回执</button> : <button className={`btn${operation === "purge" ? " btn-danger" : " btn-primary"}`} disabled={!ready} aria-busy={busy} onClick={() => void submit()}>{busy ? "正在提交" : `确认${OPERATION[operation]}`}</button>}
      <button className="btn" disabled={busy} onClick={onClose}>{pending ? "关闭（保留待查回执）" : "取消"}</button>
    </div>
  </Dialog>;
}

async function fetchBlockers(targets: { kind: string; id: string }[]): Promise<string[]> {
  try {
    const impacts = await Promise.all(targets.map((t) => management.impact(t.kind, t.id)));
    return activeBlockers(impacts);
  } catch { return []; }
}

export default function ResourceActions({ kind, id, label, onChanged }: { kind: string; id: string; label: string; onChanged: () => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [blockers, setBlockers] = useState<string[]>([]);
  const [pending, setPending] = useState<string | null>(null), [cleanup, setCleanup] = useState<string | null>(null);
  async function submit() {
    const unresolved = unresolvedKey();
    if (unresolved) { setPending(unresolved); setNotice("存在尚未确认的管理操作，先查询原回执。"); return; }
    setBusy(true); setError(""); setBlockers([]);
    try {
      const impacts = await Promise.all(impactTargets([{ kind, id }]).map((t) => management.impact(t.kind, t.id)));
      const key = newKey();
      const receipt = await management.change("trash", impacts, key, {}, "");
      if (receipt.cleanup_pending?.length) {
        rememberCleanup(key, true); setCleanup(key); setNotice("元数据已清除，但原文文件清理尚未完成。");
        return;
      }
      onChanged();
    } catch (e) {
      if (e instanceof UnknownOutcomeError) setPending(e.key);
      if (e instanceof ApiError && e.code === "RESOURCE_IN_ACTIVE_RUN") {
        const lines = backendBlockerLines(e.details);
        setBlockers(lines.length ? lines : await fetchBlockers([{ kind, id }]));
      }
      setError(managementError(e));
    } finally { setBusy(false); }
  }
  async function check() {
    if (!pending) return;
    setBusy(true);
    try {
      const value = await getReceipt(pending);
      if (value.status === "completed") {
        const response = value.response as ManagedReceipt | undefined;
        rememberCleanup(pending, Boolean(response?.cleanup_pending?.length));
        clearPending(pending); setPending(null); setError(""); onChanged();
      } else setNotice("服务端尚未找到回执，结果仍未确认。请稍后继续查询，不要重复提交。");
    } catch (e) { setError(managementError(e)); } finally { setBusy(false); }
  }
  return <>
    <button className="btn btn-sm" aria-label={`将${label}移入回收站`} aria-busy={busy} disabled={busy} onClick={() => void submit()}>移入回收站</button>
    {error && <p className="err" role="alert">{error}</p>}
    {blockers.length > 0 && <ul className="err" role="alert">{blockers.map((b, i) => <li key={i}>{b}</li>)}</ul>}
    {notice && <p role="status">{notice}</p>}
    {cleanup && <button className="btn btn-sm" disabled={busy} onClick={async () => { setBusy(true); try { const receipt = await management.cleanup(cleanup); rememberCleanup(cleanup, Boolean(receipt.cleanup_pending?.length)); setCleanup(null); setNotice(receipt.cleanup_pending?.length ? "原文文件仍被占用，清理未完成。" : "原回执文件清理已完成。"); } catch (e) { if (e instanceof UnknownOutcomeError) setPending(e.key); setError(managementError(e)); } finally { setBusy(false); } }}>继续清理原回执文件</button>}
    {pending && <button className="btn btn-sm" disabled={busy} onClick={() => void check()}>查询未确认操作回执</button>}
  </>;
}
