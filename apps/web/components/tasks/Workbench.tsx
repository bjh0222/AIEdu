"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, isRun } from "@/lib/v2/api";
import type { RunSnapshot, CallSnapshot } from "@/lib/v2/contracts.generated";
import { ApiError, UnknownOutcomeError, clearPending, explain, getReceipt, newKey, pendingActions } from "@/lib/v2/client";
import { useSnapshot } from "@/lib/v2/use-snapshot";
import { STAGE_LABEL, STAGE_ORDER, activeRunStage, callCostsText, callFailureText, deliveryBadge, limitText, reasonText, runBadge, seconds, stageText } from "@/lib/v2/status";
import { BadgeView } from "@/components/ui/Badge";
import { useToast } from "@/components/ui/Toast";
import ExportDialog from "./ExportDialog";
import KnowledgeMap from "./KnowledgeMap";
import RunEvidence from "./RunEvidence";

type PauseUi = "idle" | "requesting" | "unconfirmed";
type UnknownCall = CallSnapshot & { started_at?: number | null; unknown_age_warning?: boolean };
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
  const [quarantineBusy, setQuarantineBusy] = useState("");
  const [quarantineKey, setQuarantineKey] = useState<string | null>(null);
  const [pendingRetryCalls, setPendingRetryCalls] = useState<{ call_id: string; request_hash: string }[]>([]);
  const [ack, setAck] = useState(false);
  const [unknownCalls, setUnknownCalls] = useState<UnknownCall[] | null>(null);

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
  // 「结果不明」只指 unknown；sent 是仍在途，不能在运行中当作未知结果要求用户隔离。
  const detectedUnknownCalls = (run?.calls.filter((call) => call.status === "unknown") ?? []) as UnknownCall[];
  const visibleUnknownCalls = unknownCalls ?? detectedUnknownCalls;
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
        setActionError(explain(e));
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

  async function resume(acknowledgeProtocolUpgrade = false) {
    if (!run) return;
    setResumeBusy(true);
    setActionError("");
    try {
      const unknown_resolutions = pendingRetryCalls.map((call) => ({
        ...call,
        action: "retry_as_new_attempt" as const,
        acknowledge_possible_duplicate_cost: true,
      }));
      const r = await api.resume(taskId, run.run_id, {
        expected_revision: run.state_revision,
        checkpoint_hash: run.checkpoint_hash,
        unknown_resolutions,
        ...(acknowledgeProtocolUpgrade ? { quality_protocol_upgrade: true } : {}),
      }, newKey());
      toast(acknowledgeProtocolUpgrade ? "已创建续跑（已显式采用新质量协议）" : "已创建续跑");
      setUnknownCalls(null);
      setPendingRetryCalls([]);
      followRun(r.run_id); // 旧 run 保持 paused；轮询切到新 run
    } catch (e) {
      if (e instanceof ApiError && e.code === "QUALITY_PROTOCOL_UPGRADE_REQUIRED") {
        const frozen = String(e.details.frozen_manifest_hash ?? "");
        const current = String(e.details.current_manifest_hash ?? "");
        const ok = window.confirm(
          `服务端质量协议已更新（${frozen.slice(0, 12)}… → ${current.slice(0, 12)}…）。` +
          "继续将显式采用新协议，并在续跑状态中记录升级事实；旧调用与结果保留，不会重放。是否继续？",
        );
        if (ok) {
          setResumeBusy(false);
          return void resume(true);
        }
        setActionError("已取消：未采用新质量协议，续跑未创建。");
      } else if (e instanceof ApiError && (e.code === "UNKNOWN_CALL" || e.code === "UNKNOWN_QUARANTINE_REQUIRED")) {
        setUnknownCalls(run.calls.filter((c) => c.status === "unknown") as UnknownCall[]);
      } else setActionError(explain(e));
    } finally {
      setResumeBusy(false);
    }
  }

  async function quarantine(call: UnknownCall, retry: boolean) {
    if (!run || !ack) return;
    if (!["paused", "interrupted", "failed"].includes(run.state)) {
      setActionError("这次运行还在进行中，不能隔离调用。请先点“暂停并保留已有内容”，等页面显示已暂停后再试。");
      return;
    }
    setQuarantineBusy(call.call_id);
    setActionError("");
    const key = newKey();
    setQuarantineKey(key);
    try {
      await api.quarantineUnknown(taskId, run.run_id, {
        expected_revision: run.state_revision,
        checkpoint_hash: run.checkpoint_hash,
        calls: [{ call_id: call.call_id, request_hash: call.request_hash }],
        acknowledge_possible_duplicate_cost: true,
      }, key);
      if (retry) {
        setPendingRetryCalls((current) => current.some((item) => item.call_id === call.call_id)
          ? current
          : [...current, { call_id: call.call_id, request_hash: call.request_hash }]);
      }
      setUnknownCalls((current) => (current ?? detectedUnknownCalls).filter((item) => item.call_id !== call.call_id));
      setAck(false);
      setQuarantineKey(null);
      toast(retry ? "调用已隔离；继续生成时会作为新尝试" : "调用已隔离，未自动续跑");
      await refresh();
    } catch (e) {
      if (e instanceof ApiError && e.code === "REVISION_STALE") {
        await refresh();
        setActionError("任务状态刚刚更新，隔离没有生效。已读取最新状态，请再次确认。");
        setQuarantineKey(null);
      } else if (e instanceof UnknownOutcomeError) {
        setActionError("隔离请求未确认：没有收到服务端响应。可以先查询回执，不要直接重复提交。");
      } else {
        setActionError(explain(e));
        setQuarantineKey(null);
      }
    } finally {
      setQuarantineBusy("");
    }
  }

  async function checkQuarantineReceipt() {
    if (!quarantineKey) return;
    try {
      const receipt = await getReceipt(quarantineKey);
      if (receipt.status === "completed") {
        clearPending(quarantineKey);
        setQuarantineKey(null);
        setActionError("回执显示：调用已隔离。继续生成仍是单独动作。");
        await refresh();
      } else {
        setActionError("暂未查到隔离回执，仍不能断定未执行。请继续查询，勿重复提交。");
      }
    } catch (e) {
      setActionError(explain(e));
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
  const runStopped = ["paused", "interrupted", "failed"].includes(run.state);
  // 失败/结果不明的原因只取本次运行；祖先 run 的旧失败（例如上一轮的 8k 截断）
  // 不能拿来解释本轮的停止原因。
  const currentCalls = run.calls.filter((c) => c.current_run !== false);
  // 中间合同纠正失败可能已被后续成功覆盖；停止横幅应显示最后一次失败/不明调用，
  // 而不是最早一次失败。
  const failedCalls = currentCalls.filter((c) => c.status === "settled" && c.outcome === "failed" && c.failure_code);
  const unknownCurrentCalls = currentCalls.filter((c) => c.status === "unknown" || c.outcome === "unknown");
  const failedCall = failedCalls[failedCalls.length - 1];
  const unknownCall = unknownCurrentCalls[unknownCurrentCalls.length - 1];
  const failureDetail = failedCall ? callFailureText(failedCall) : unknownCall ? callFailureText(unknownCall) : "";
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
          {run.delivery_detail && <p className="hint" style={{ marginTop: 8 }}>{run.delivery_detail}</p>}
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {canPause && <button className="btn" onClick={() => void pause()} disabled={pausing || Boolean(pauseKey)} aria-busy={pausing}>{pausing ? "正在请求暂停" : "暂停并保留已有内容"}</button>}
          {canResume && <button className="btn btn-primary" onClick={() => void resume()} disabled={resumeBusy || visibleUnknownCalls.length > 0} aria-busy={resumeBusy}>{pendingRetryCalls.length ? `继续生成（含 ${pendingRetryCalls.length} 项重做）` : "继续生成"}</button>}
          {hasDraft && <Link className="btn" href={`/tasks/${taskId}/read?version=${run.version_id}`}>阅读草稿</Link>}

          {hasDraft && <button className="btn" onClick={() => setExportOpen(true)}>下载</button>}
        </div>
      </div>

      {error && stale && <div className="banner banner-danger" role="alert" style={{ marginBottom: 12 }}><div><b>无法刷新状态。</b> {error} 下面是上次读取的内容，可能已过期。</div><button className="btn btn-sm" onClick={() => void refresh()}>重试</button></div>}
      {run.research_degradation && (
        <div className="banner banner-warn" role="status" style={{ marginBottom: 12 }}>
          <div><b>公开研究未达到证据门槛。</b> 本次已降级为仅使用已绑定的本地材料继续；未取得全文的来源不会被当作已检索证据，也不会伪造引用。</div>
        </div>
      )}
      {pausing && <div className="banner banner-warn" role="status" style={{ marginBottom: 12 }}><div><b>正在请求暂停。</b> 已保存的内容会保留；已经发出的模型调用仍会结算。服务端确认后才会显示“已暂停”。</div></div>}
      {actionError && (
        <div className="banner banner-danger" role="alert" style={{ marginBottom: 12 }}>
          <div>{actionError}</div>
          {(pauseKey || quarantineKey) && <button className="btn btn-sm" onClick={() => void (pauseKey ? checkPauseReceipt() : checkQuarantineReceipt())}>查询回执</button>}
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
          <div>
            <div><b>本次运行已停止。</b> {reasonText(run.stop_reason)}。已保存的内容会保留，但不能据此认定质检已通过。</div>
            {failureDetail && <p className="hint" style={{ marginTop: 6 }}>{failureDetail}</p>}
            {["PROVIDER_CALL_FAILED", "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "OUTPUT_CAPACITY_EXHAUSTED"].includes(run.stop_reason) && (
              <p className="hint" style={{ marginTop: 6 }}>点“继续生成”会复用已经成功的步骤；失败或未完成的步骤才会重新调用。</p>
            )}
            {run.stop_reason === "CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED" && <Link className="btn btn-sm" href="/settings">检查模型配置</Link>}
          </div>
        </div>
      )}

      {visibleUnknownCalls.length > 0 && (
        <section className="panel panel-pad" style={{ marginBottom: 16 }} aria-labelledby="unk">
          <h2 className="sub" id="unk">有 {visibleUnknownCalls.length} 个调用结果不明</h2>
          <p className="muted">这些调用已发出但没有确认结果。先逐项隔离；隔离不会自动续跑，也不会替你重发。继续生成是另一个动作。</p>
          <ul>
            {visibleUnknownCalls.map((call, index) => (
              <li key={call.call_id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                <span>待核实请求 {index + 1}</span>
                {call.unknown_age_warning && <p className="hint" role="alert">这次调用结果不明已超过 6 小时；系统没有自动隔离、重发或恢复。</p>}
                {runStopped ? (
                  <div className="seg" style={{ marginLeft: 12 }} role="group" aria-label={`待核实请求 ${index + 1} 的处理方式`}>
                    <button type="button" disabled={!ack || Boolean(quarantineBusy)} aria-busy={quarantineBusy === call.call_id} onClick={() => void quarantine(call, false)}>保留，不重做</button>
                    <button type="button" disabled={!ack || Boolean(quarantineBusy)} onClick={() => void quarantine(call, true)}>隔离并准备重做</button>
                  </div>
                ) : (
                  <p className="hint" role="status" style={{ marginTop: 6 }}>这次运行还在进行中。请先点顶部“暂停并保留已有内容”，等服务端确认已暂停后，再选择如何处理。</p>
                )}
              </li>
            ))}
          </ul>
          {runStopped && (
            <>
              <label style={{ display: "flex", gap: 8, margin: "12px 0", minHeight: 44, alignItems: "center" }}>
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                我了解结果不明的调用可能已经产生费用；“重做”可能再次计费
              </label>
              <p className="hint">完成隔离后，页面顶部的“继续生成”才会可用。</p>
            </>
          )}
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
        <ExportDialog open={exportOpen} onClose={() => setExportOpen(false)} artifactId={run.artifact_id} versionId={run.version_id!} versionLabel="当前版本" revision={run.state_revision} taskId={taskId} runId={run.run_id} checkpointHash={run.checkpoint_hash} deliveryDecision={run.delivery_decision} refresh={refresh} />
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
  const metrics = run.run_metrics as {
    tokens_known?: number;
    token_unknown_calls?: number;
    price_known_calls?: number;
    price_missing_calls?: number;
    retry_source_counts?: Record<string, number>;
    stage_distribution?: Record<string, { calls?: number; elapsed_seconds?: number; tokens_known?: number; token_unknown_calls?: number }>;
  } | undefined;
  const groupedStages = new Map<string, { calls: number; elapsed: number }>();
  for (const [stage, value] of Object.entries(metrics?.stage_distribution ?? {})) {
    if ((value.calls ?? 0) <= 0) continue;
    const key = usageStageGroup(stage);
    const current = groupedStages.get(key) ?? { calls: 0, elapsed: 0 };
    current.calls += value.calls ?? 0;
    current.elapsed += value.elapsed_seconds ?? 0;
    groupedStages.set(key, current);
  }
  const stageRows = [...groupedStages.entries()];
  const retryRows = Object.entries(metrics?.retry_source_counts ?? {}).filter(([, value]) => value > 0);

  const budget = run.budget as Record<string, unknown>;
  return (
    <>
      <dl className="kv">
        <dt>模型调用</dt><dd>{calls.length}{inflight > 0 && `（执行中 ${inflight}）`}</dd>
        <dt>调用耗时合计</dt><dd>{calls.length ? seconds(total) : "—"}</dd>
        <dt>Token</dt><dd>{run.demo ? "演示（不调用模型）" : tokens === null ? "待核实" : `${tokens.toLocaleString("zh-CN")}${anyUnknownUsage ? "（含待核实）" : ""}`}</dd>
        <dt>费用</dt><dd>{callCostsText(calls, run.demo)}{!run.cost_final && !run.demo ? "；总费用未确认" : ""}</dd>
        <dt>阶段分布</dt><dd>{stageRows.length ? stageRows.map(([stage, value]) => `${stage} ${value.calls} 次 / ${seconds(value.elapsed)}`).join("；") : "—"}</dd>
        <dt>重试来源</dt><dd>{retryRows.length ? retryRows.map(([source, value]) => `${source === "initial" ? "首次" : source === "stage_reattempt" ? "阶段重试" : source === "ancestor_resume" ? "祖先续跑" : source === "explicit_retry" ? "显式重试" : source} ${value}`).join("；") : "—"}</dd>
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

function usageStageGroup(stage: string): string {
  if (stage === "researching" || stage.startsWith("research_")) return "研究与证据";
  if (stage === "understanding" || stage === "import_understanding") return "理解需求";
  if (stage === "planning" || stage === "arrangement_alignment") return "规划结构";
  if (stage === "generating" || stage.startsWith("generating_")) return "撰写";
  if (stage === "checking" || stage === "checking_perturbation"
    || stage === "item_reviewing" || stage === "blind_solving"
    || stage === "selective_review") return "质量检查";
  if (stage.startsWith("criterion_") || stage.startsWith("difficulty_")
    || stage === "optimizing" || stage === "optimization"
    || stage === "optimization_acceptance") return "质检与整改";
  if (stage.startsWith("rubric_")) return "研究量规";
  if (stage.startsWith("synthesis_")) return "跨章综合";
  if (stage === "repairing") return "内容修复";
  if (stage === "verifying") return "复验";
  if (stage === "exporting" || stage === "postprocess") return "整理与导出";
  return "其它";
}

