"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, isRun } from "@/lib/v2/api";
import type { RunSnapshot, CallSnapshot } from "@/lib/v2/contracts.generated";
import { ApiError, UnknownOutcomeError, clearPending, explain, getReceipt, newKey, pendingActions } from "@/lib/v2/client";
import { useSnapshot } from "@/lib/v2/use-snapshot";
import { STAGE_LABEL, STAGE_ORDER, activeRunStage, callCostsText, deliveryBadge, limitText, reasonText, runBadge, seconds, stageText } from "@/lib/v2/status";
import { BadgeView } from "@/components/ui/Badge";
import { useToast } from "@/components/ui/Toast";
import ExportDialog from "./ExportDialog";
import KnowledgeMap from "./KnowledgeMap";
import RunEvidence from "./RunEvidence";

type PauseUi = "idle" | "requesting" | "unconfirmed";
const ROLE: Record<string, string> = { tutorial: "教程", exercise: "练习", answer: "参考答案", paper: "试卷", cheatsheet: "速查", chapter: "章节", book_level: "全书层" };

export default function Workbench({ taskId }: { taskId: string }) {
  const toast = useToast();
  const { snapshot, error, stale, loading, refresh, followRun } = useSnapshot(taskId);
  const [selected, setSelected] = useState<string | null>(null);
  const [pauseUi, setPauseUi] = useState<PauseUi>("idle");
  const [pauseKey, setPauseKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [resumeBusy, setResumeBusy] = useState(false);
  const [resolutions, setResolutions] = useState<Record<string, "retry_as_new_attempt" | "keep_quarantined" | "verify_provider_status">>({});
  const [ack, setAck] = useState(false);
  const [unknownCalls, setUnknownCalls] = useState<string[] | null>(null);

  useEffect(() => {
    // Restore browser-only session state after hydration; cancel on task change.
    const timer = window.setTimeout(() => {
      const pending = pendingActions().find((p) => p.action_kind === "pause" && p.task_id === taskId);
      if (pending) {
        setPauseKey(pending.key);
        setPauseUi("unconfirmed");
        setActionError("暂停未确认：请查询原回执，不要重复提交。");
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [taskId]);

  const run = snapshot && isRun(snapshot) ? snapshot : null;
  const task = snapshot ? (snapshot as { task?: { title?: string; raw_input?: string } }).task ?? run?.resolved_task as { title?: string; raw_input?: string } | undefined : undefined;
  const title = (run?.resolved_task as { title?: string } | undefined)?.title ?? task?.title ?? "任务";

  const units = useMemo(() => run?.units ?? [], [run]);
  const unit = units.find((u) => u.unit_id === selected) ?? units[0];

  async function pause() {
    if (!run || pauseKey) return;
    setActionError("");
    setPauseUi("requesting");
    const key = newKey();
    setPauseKey(key);
    try {
      const r = await api.pause(taskId, run.run_id, { expected_revision: run.state_revision, reason: "user_pause" }, key);
      toast("已暂停");
      setPauseKey(null);
      void r;
      await refresh();
      setPauseUi("idle");
    } catch (e) {
      if (e instanceof ApiError && e.code === "REVISION_STALE") {
        // 明确 409：读取最新 revision，用新 key 重试一次。
        await refresh();
        setPauseUi("unconfirmed");
        setPauseKey(null);
        setActionError("任务状态刚刚更新过，暂停没有生效。已读取最新状态，请再点一次“暂停并保留已有内容”。");
      } else if (e instanceof UnknownOutcomeError) {
        setPauseUi("unconfirmed");
        setActionError("暂停未确认：没有收到服务端响应。可以先“查询回执”，不要直接重复提交。");
      } else {
        setPauseUi("unconfirmed");
        setActionError(`暂停未确认。${explain(e)}`);
        setPauseKey(null);
      }
    }
  }

  async function checkPauseReceipt() {
    if (!pauseKey) return;
    try {
      const r = await getReceipt(pauseKey);
      if (r.status === "completed") {
        setActionError("回执显示：暂停已经生效。");
        clearPending(pauseKey);
        setPauseKey(null);
        await refresh();
        setPauseUi("idle");
      } else setActionError("暂未查到暂停回执，仍不能断定未执行。请继续查询，勿重复提交。");
    } catch (e) {
      setActionError(explain(e));
    }
  }

  async function resume(withResolutions = false) {
    if (!run) return;
    setResumeBusy(true);
    setActionError("");
    try {
      const unknown_resolutions = withResolutions
        ? Object.entries(resolutions).map(([call_id, action]) => ({ call_id, action, acknowledge_possible_duplicate_cost: action === "retry_as_new_attempt" && ack }))
        : [];
      const r = await api.resume(taskId, run.run_id, { expected_revision: run.state_revision, checkpoint_hash: run.checkpoint_hash, unknown_resolutions }, newKey());
      toast("已创建续跑");
      setUnknownCalls(null);
      followRun(r.run_id); // 旧 run 保持 paused；轮询切到新 run
    } catch (e) {
      if (e instanceof ApiError && e.code === "UNKNOWN_CALL") {
        setUnknownCalls(run.calls.filter((c) => c.status === "unknown" || c.status === "sent").map((c) => c.call_id));
      } else setActionError(explain(e));
    } finally {
      setResumeBusy(false);
    }
  }

  if (loading && !snapshot) return <div className="skeleton" style={{ height: 320 }} aria-busy="true" aria-label="正在加载" />;
  if (!snapshot) {
    return (
      <div className="empty"><p>{error || "找不到这个任务。"}</p><Link className="btn" href="/">回到任务列表</Link></div>
    );
  }

  // 仅有草稿输入（尚未启动 run）。
  if (!run) {
    const t = (snapshot as { task: { raw_input?: string; title?: string } }).task;
    return (
      <>
        <div className="crumbs"><Link href="/">任务</Link><span aria-hidden="true">/</span><span aria-current="page">{t.title ?? "任务"}</span></div>
        <h1 className="title">{t.title ?? "任务"}</h1>
        <div className="banner banner-warn" style={{ margin: "16px 0" }}><div><b>这个任务还没有开始生成。</b> 它保存为草稿，可能正在等你确认冲突。</div></div>
        <section className="panel panel-pad">
          <h2 className="sub">你的原话</h2>
          <p style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{t.raw_input}</p>
        </section>
        <p style={{ marginTop: 16 }}><Link className="btn btn-primary" href={`/new?example=${encodeURIComponent(t.raw_input ?? "")}`}>改写后重新开始</Link></p>
      </>
    );
  }

  const stage = activeStage(run);
  const rb = runBadge(run.state, stage);

  const db = deliveryBadge(run, false, run.state === "paused" && run.units.length > 0 && !isComplete(run));
  const canPause = run.next_actions.includes("pause");
  const canResume = run.next_actions.includes("resume");
  const hasDraft = Boolean(run.version_id);
  const pausing = pauseUi === "requesting" && run.state !== "paused";
  const missing = missingRoles(run);
  const inflight = run.calls.filter((c) => c.status === "sent" || c.status === "authorized").length;

  return (
    <>
      <div className="crumbs"><Link href="/">任务</Link><span aria-hidden="true">/</span><span aria-current="page">{title}</span></div>
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <h1 className="title">{title}{run.demo && <> <span className="demo-tag">演示</span></>}</h1>
          <div className="axes" style={{ marginTop: 10 }} role="group" aria-label="任务状态（运行、交付两个独立维度）">
            <span className="axis"><span className="axis-name">运行</span><BadgeView b={rb} axis="运行状态" /></span>
            <span className="axis"><span className="axis-name">交付</span><BadgeView b={db} axis="交付状态" /></span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {canPause && <button className="btn" onClick={() => void pause()} disabled={pausing || Boolean(pauseKey)} aria-busy={pausing}>{pausing ? "正在请求暂停" : "暂停并保留已有内容"}</button>}
          {canResume && <button className="btn btn-primary" onClick={() => void resume()} disabled={resumeBusy} aria-busy={resumeBusy}>继续生成</button>}
          {hasDraft && <Link className="btn" href={`/tasks/${taskId}/read?version=${run.version_id}`}>阅读草稿</Link>}

          {hasDraft && <button className="btn" onClick={() => setExportOpen(true)}>下载</button>}
        </div>
      </div>

      {error && stale && <div className="banner banner-danger" role="alert" style={{ marginBottom: 12 }}><div><b>无法刷新状态。</b> {error} 下面是上次读取的内容，可能已过期。</div><button className="btn btn-sm" onClick={() => void refresh()}>重试</button></div>}
      {pausing && <div className="banner banner-warn" role="status" style={{ marginBottom: 12 }}><div><b>正在请求暂停。</b> 已保存的内容会保留；已经发出的模型调用仍会结算。服务端确认后才会显示“已暂停”。</div></div>}
      {actionError && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: 12 }}>
          <div>{actionError}</div>
          {pauseKey && <button className="btn btn-sm" onClick={() => void checkPauseReceipt()}>查询回执</button>}
        </div>
      )}
      {run.state === "paused" && (
        <div className="banner banner-warn" role="status" style={{ marginBottom: 12 }}>
          <div>
            <b>已暂停。</b> 已有内容可以阅读和下载。
            {run.units.length > 0 && <> 已有：{[...new Set(run.units.map((u) => ROLE[u.artifact_role] ?? u.artifact_role))].join("、")}。</>}
            {missing.length > 0 && <> 还缺：{missing.join("、")}。</>}
            {inflight > 0 && <> 在途调用结算中：{inflight}。</>}
            {" "}继续会创建一次新的运行，复用已完成的单元，这次运行不会回到“运行中”。
          </div>
        </div>
      )}
      {run.state === "completed" && run.stop_reason && <p className="hint" style={{ marginBottom: 12 }}>结束原因：{run.stop_reason === "demo_complete" ? "演示运行完成" : reasonText(run.stop_reason)}。运行结束不代表质量通过。</p>}
      {(run.state === "failed" || run.state === "interrupted") && run.stop_reason && (
        <div className="banner banner-warn" role="status" style={{ marginBottom: 12 }}>
          <div><b>本次运行已停止。</b> {reasonText(run.stop_reason)}。已保存的内容会保留，但不能据此认定质检已通过。</div>
          {run.stop_reason === "CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED" && <Link className="btn btn-sm" href="/settings">检查模型配置</Link>}
        </div>
      )}

      {unknownCalls && (
        <section className="panel panel-pad" style={{ marginBottom: 16 }} aria-labelledby="unk">
          <h2 className="sub" id="unk">有 {unknownCalls.length} 个调用结果不明</h2>
          <p className="muted">这些调用已发出但没有确认结果。系统不会替你重发。请逐项选择：</p>
          <ul>
            {unknownCalls.map((id, index) => (
              <li key={id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                <span>待核实请求 {index + 1}</span>
                <div className="seg" style={{ marginLeft: 12 }} role="group" aria-label={`待核实请求 ${index + 1} 的处理方式`}>
                  <button type="button" aria-pressed={resolutions[id] === "keep_quarantined"} onClick={() => setResolutions({ ...resolutions, [id]: "keep_quarantined" })}>保留，不重做</button>
                  <button type="button" aria-pressed={resolutions[id] === "retry_as_new_attempt"} onClick={() => setResolutions({ ...resolutions, [id]: "retry_as_new_attempt" })}>重做为新尝试</button>
                  <button type="button" aria-pressed={resolutions[id] === "verify_provider_status"} onClick={() => setResolutions({ ...resolutions, [id]: "verify_provider_status" })}>向供应商核实</button>
                </div>
              </li>
            ))}
          </ul>
          <label style={{ display: "flex", gap: 8, margin: "12px 0", minHeight: 44, alignItems: "center" }}>
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            我知道“重做为新尝试”可能产生重复计费
          </label>
          <button className="btn btn-primary" disabled={resumeBusy || unknownCalls.some((id) => !resolutions[id])} onClick={() => void resume(true)}>按所选方式继续</button>
        </section>
      )}

      <KnowledgeMap taskId={taskId} runId={run.run_id} revision={run.state_revision} run={run} stale={stale} sidebar={<>
        <details className="growth-timeline"><summary>阶段记录</summary>
          <ol className="timeline">
            {STAGE_ORDER.filter((s) => s in run.stages || s === activeStage(run)).concat(STAGE_ORDER.filter((s) => !(s in run.stages) && s !== activeStage(run) && ["generating", "checking"].includes(s))).sort((a, b) => STAGE_ORDER.indexOf(a) - STAGE_ORDER.indexOf(b)).map((s) => {
              const v = run.stages[s];
              const current = s === stage && run.state === "running";
              const shape = v === "done" ? "solid-square" : v === "deterministic_only" ? "half-square" : v === "not_run" || v === "not_applicable" ? "dashed-circle" : current ? "solid-circle" : "hollow-circle";
              return (
                <li key={s}>
                  <span className={`shape shape-${shape}`} aria-hidden="true" style={{ marginTop: 5, color: current ? "var(--primary)" : v === "done" ? "var(--ok)" : "var(--neutral)" }} />
                  <div><div className="t-name">{STAGE_LABEL[s]}</div><div className="t-note">{stageText(v)}</div></div>
                </li>
              );
            })}
          </ol>
        </details><UsagePanel run={run} />
      </>} />
      <section className="panel panel-pad saved-draft" aria-labelledby="h-draft">
          <h2 className="sub" id="h-draft">草稿</h2>
          {units.length === 0 ? (
            <div className="empty panel"><p>{run.state === "queued" || run.state === "running" ? "尚无草稿。写好一个单元就可以先读。" : "没有已保存的内容。"}</p></div>
          ) : (
            <>
              <ul className="unit-list" role="listbox" aria-label="已保存的单元" style={{ marginBottom: 12 }}>
                {units.map((u) => (
                  <li key={u.unit_id}>
                    <button className="unit-btn" role="option" aria-selected={u.unit_id === unit?.unit_id} onClick={() => setSelected(u.unit_id)}>
                      <span className="shape shape-solid-square" aria-hidden="true" style={{ color: "var(--ok)" }} />
                      <span>{u.body.match(/^#{1,6}\s+(.+)$/m)?.[1] ?? ROLE[u.artifact_role] ?? "已保存内容"}</span>
                      <span className="muted" style={{ marginLeft: "auto" }}>已保存</span>
                    </button>
                  </li>
                ))}
              </ul>
              {unit && <div className="draft-view" tabIndex={0} role="region" aria-label="草稿预览"><pre>{unit.body}</pre></div>}
            </>
          )}
      </section>
      <RunEvidence taskId={taskId} runId={run.run_id} />

      {hasDraft && (
        <ExportDialog open={exportOpen} onClose={() => setExportOpen(false)} artifactId={run.artifact_id} versionId={run.version_id!} versionLabel="当前版本" revision={run.state_revision} taskId={taskId} refresh={refresh} />
      )}
    </>
  );
}

function activeStage(run: RunSnapshot): string | undefined {
  return activeRunStage(run);
}
function isComplete(run: RunSnapshot): boolean {
  return run.state === "completed";
}
function missingRoles(run: RunSnapshot): string[] {
  const roles = (run.resolved_task as { requested_artifacts?: string[] } | undefined)?.requested_artifacts ?? [];
  const have = new Set(run.units.flatMap((u) => u.artifact_roles ?? [u.artifact_role]));
  return roles.filter((r) => !have.has(r)).map((r) => ROLE[r] ?? r);
}

function UsagePanel({ run }: { run: RunSnapshot }) {
  const calls = run.calls as CallSnapshot[];
  const inflight = calls.filter((c) => c.status === "sent" || c.status === "authorized").length;
  const unresolved = calls.some((c) => c.status === "unknown" || c.outcome === "unknown");
  const total = calls.reduce((n, c) => n + ((c.elapsed_seconds as number | null | undefined) ?? 0), 0);
  const anyUnknownUsage = calls.some((c) => c.usage === null && c.status !== "not_sent");
  const tokens = calls.some((c) => c.usage && typeof c.usage.total_tokens === "number")
    ? calls.reduce((n, c) => n + (typeof c.usage?.total_tokens === "number" ? c.usage.total_tokens : 0), 0)
    : null;

  const budget = run.budget as Record<string, unknown>;
  return (
    <>
      <dl className="kv">
        <dt>模型调用</dt><dd>{calls.length}{inflight > 0 && `（执行中 ${inflight}）`}</dd>
        <dt>调用耗时合计</dt><dd>{calls.length ? seconds(total) : "—"}</dd>
        <dt>Token</dt><dd>{run.demo ? "演示（不调用模型）" : tokens === null ? "待核实" : `${tokens.toLocaleString("zh-CN")}${anyUnknownUsage ? "（含待核实）" : ""}`}</dd>
        <dt>费用</dt><dd>{callCostsText(calls, run.demo)}{!run.cost_final && !run.demo ? "（总费用未确认）" : ""}</dd>
        <dt>费用上限</dt><dd>{limitText(budget.amount)}</dd>
        <dt>调用次数上限</dt><dd>{budget.calls === null || budget.calls === undefined ? "不限" : String(budget.calls)}</dd>
      </dl>
      {!run.cost_final && !run.demo && <p className="hint" style={{ marginTop: 8 }}>{inflight > 0
        ? "仍有调用执行中，用量与费用尚未完整。"
        : unresolved ? "有调用结果或用量仍待核实，不能确定总费用。"
          : "调用结果已记录；仍缺提供方价格或费用回执，不能确定总费用。"}费用更新不会改动已冻结的正文。</p>}
    </>
  );
}

