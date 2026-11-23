"use client";

import { useEffect, useRef, useState } from "react";
import { api, isRun, type VersionRead } from "@/lib/v2/api";
import { clearPending, explain, getReceipt, newKey, pendingActions, UnknownOutcomeError } from "@/lib/v2/client";
import type { RunSnapshot, SynthesisAccepted } from "@/lib/v2/contracts.generated";
import { matchesSource, rememberSynthesisRun, synthesis, trackedSynthesisRun, type SynthesisRead } from "@/lib/v2/synthesis-api";
import { reasonText, runBadge } from "@/lib/v2/status";
import Prose from "./Prose";

const OBSERVATION: Record<string, string> = { satisfied: "有支持证据", violated: "存在问题", undecidable: "待补证", not_applicable: "不适用" };
const AUTOMATIC = { satisfied: "自动检查满足", needs_repair: "仍需整改", needs_review: "仍需复核" };
const POLL_MS = 5_000;
// Browser resource bound only; this is not a backend/model execution timeout.
const TRACKING_MS = 30 * 60_000;
const READ_TIMEOUT_MS = 15_000;

export default function SynthesisPanel({ taskId, run, read, onNavigate }: {
  taskId: string; run: RunSnapshot; read: VersionRead; onNavigate: (unitId: string) => void;
}) {
  const [value, setValue] = useState<SynthesisRead | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [accepted, setAccepted] = useState<string | null>(null);
  const [operation, setOperation] = useState<RunSnapshot | null>(null);
  const [trackingReady, setTrackingReady] = useState(false);
  const [trackingIssue, setTrackingIssue] = useState("");
  const [refreshRevision, setRefreshRevision] = useState(0);
  const submitting = useRef(false);
  const sourceVersionId = read.version_id;
  const sourceContentHash = read.content_hash;
  const sourceQualityHash = read.quality_hash;
  useEffect(() => {
    const timer = setTimeout(() => {
      const previous = pendingActions().find((p) => p.action_kind === "synthesis" && p.task_id === taskId);
      if (previous) setPending(previous.key);
      const runId = trackedSynthesisRun(taskId, {
        version_id: sourceVersionId, content_hash: sourceContentHash, quality_hash: sourceQualityHash,
      });
      if (runId) setAccepted(runId);
      setTrackingReady(true);
    }, 0);
    return () => clearTimeout(timer);
  }, [sourceVersionId, sourceContentHash, sourceQualityHash, taskId]);

  useEffect(() => {
    const controller = new AbortController();
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout>;
    const schedule = () => { timer = setTimeout(() => void poll(), POLL_MS); };
    async function poll() {
      if (controller.signal.aborted) return;
      if (accepted && Date.now() - started >= TRACKING_MS) {
        setTrackingIssue("已到自动跟踪时限，不表示运行超时。请刷新整合结果继续查询，不会重新提交。");
        return;
      }
      if (document.hidden) { schedule(); return; }
      try {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(READ_TIMEOUT_MS)]);
        let snapshot: RunSnapshot | null = null;
        if (accepted) {
          const response = await api.snapshot(taskId, null, accepted, signal);
          if (controller.signal.aborted) return;
          if (!response.data || !isRun(response.data) || response.data.run_id !== accepted || response.data.task_id !== taskId) {
            throw new Error("整合运行身份不匹配，未采用该状态。");
          }
          if (!["queued", "running", "completed", "failed", "paused", "interrupted", "awaiting_input"].includes(response.data.state)) {
            throw new Error("未识别的整合运行状态，不能确认已结束。");
          }
          snapshot = response.data;
        }
        const result = await synthesis.read(taskId, sourceVersionId, signal);
        if (controller.signal.aborted) return;
        if (!["candidate", "not_generated"].includes(result.status) || result.task_id !== taskId || (result.status === "candidate"
          ? !matchesSource(result, { version_id: sourceVersionId, content_hash: sourceContentHash, quality_hash: sourceQualityHash })
          : result.version_id !== sourceVersionId || result.content_hash !== sourceContentHash)) {
          throw new Error("整合与正文或质量版本不匹配，已停止展示；请重新读取。");
        }
        // Commit together: a terminal snapshot alone does not prove its result
        // was read. A cancelled initial/manual read cannot overwrite this pair.
        if (snapshot) setOperation(snapshot);
        setValue(result); setError(""); setTrackingIssue("");
        if (snapshot && ["queued", "running"].includes(snapshot.state)) schedule();
      } catch (e) {
        if (!controller.signal.aborted) {
          const reason = e instanceof DOMException && e.name === "TimeoutError"
            ? "页面只读查询超过15秒等待上限，不表示模型调用超时" : explain(e);
          if (accepted) setTrackingIssue(`${reason}。请刷新整合结果继续只读查询，不会重新提交。`);
          else setError(explain(e));
        }
      }
    }
    timer = setTimeout(() => void poll(), 0);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [accepted, refreshRevision, sourceVersionId, sourceContentHash, sourceQualityHash, taskId]);

  function refresh() { setRefreshRevision((revision) => revision + 1); }

  async function start() {
    if (submitting.current || pending || !trackingReady || trackingIssue) return;
    const unresolved = pendingActions().find((p) => p.action_kind === "synthesis" && p.task_id === taskId);
    if (unresolved) { setPending(unresolved.key); return; }
    submitting.current = true; setBusy(true); setError("");
    try {
      const preview = await synthesis.preview(taskId, run.run_id, read.version_id);
      if (preview.content_hash !== read.content_hash || preview.quality_hash !== read.quality_hash || preview.version_id !== read.version_id) {
        throw new Error("来源版本已变化，请重新读取教程。");
      }
      if (!window.confirm("将以当前固定版本全部章节为来源，调用 B 整合、C 检查并按需整改，可能产生费用。未专业校准的结果只能作为候选草稿。继续？")) return;
      const result = await synthesis.start(taskId, {
        run_id: preview.run_id, expected_run_revision: preview.state_revision,
        version_id: preview.version_id, content_hash: preview.content_hash, quality_hash: preview.quality_hash,
        source_bundle_hash: preview.source_bundle_hash, expected_synthesis_revision: preview.expected_synthesis_revision,
      }, newKey());
      rememberSynthesisRun(taskId, read, result.run_id);
      setTrackingIssue("");
      setAccepted(result.run_id); setOperation(null);
      setRefreshRevision((revision) => revision + 1);
    } catch (e) {
      if (e instanceof UnknownOutcomeError) setPending(e.key);
      setError(explain(e));
    } finally { submitting.current = false; setBusy(false); }
  }

  async function checkReceipt() {
    if (!pending) return;
    setBusy(true);
    try {
      const receipt = await getReceipt(pending);
      if (receipt.status !== "completed") {
        setError("尚未找到回执，结果仍未确认；请继续查询，不要重复提交。"); return;
      }
      const response = receipt.response as SynthesisAccepted | undefined;
      if (!response || response.task_id !== taskId || response.version_id !== read.version_id || typeof response.run_id !== "string") {
        setError("回执需要在原来源版本核对，未发起新任务。"); return;
      }
      rememberSynthesisRun(taskId, read, response.run_id);
      clearPending(pending); setPending(null); setAccepted(response.run_id); setOperation(null); setError("");
      setRefreshRevision((revision) => revision + 1);
    } catch (e) { setError(explain(e)); } finally { setBusy(false); }
  }

  const result = value && matchesSource(value, read) ? value : null;
  const latest = result?.rounds.at(-1);
  const current = run.version_id === read.version_id && value?.source_is_current !== false;
  const active = ["queued", "running"].includes(run.state) || Boolean(accepted && (!operation || ["queued", "running"].includes(operation.state)));
  const sourceLinks = (ids: string[]) => {
    const unitIds = [...new Set(ids.map((id) => result?.source_index.spans.find((s) => s.source_span_id === id)?.unit_id).filter((id): id is string => Boolean(id)))];
    return <div className="knowledge-map-tools">{unitIds.map((id) => <button key={id} className="btn btn-sm" onClick={() => onNavigate(id)}>
      查看来源：{result?.source_index.units.find((u) => u.unit_id === id)?.title || "来源章节"}
    </button>)}</div>;
  };

  return <section className="learning-tools panel panel-pad" aria-label="跨章知识与方法整合">
    <h2 className="sub">跨章知识与方法整合</h2>
    {error && <p className="err" role="alert">{error}</p>}
    {trackingIssue && <p className="banner banner-warn" role="alert">自动跟踪已暂停：{trackingIssue}</p>}
    {!current && <p className="banner banner-warn">来源已有更新。这里的整合只适用于原固定版本，不能带入新版本交付。</p>}
    {value?.status === "candidate" && !result && <p className="err" role="alert">整合与正文或质量版本不匹配，已停止展示；请重新读取。</p>}
    {value?.status === "not_generated" && <p className="hint">这个版本尚未完成跨章整合。章节目录与逐章知识卡不代表已分析跨章关系。</p>}
    {accepted && <p role="status">{operation ? `整合运行：${runBadge(operation.state).label}${operation.stop_reason ? `；${reasonText(operation.stop_reason)}` : ""}` : "整合请求已接受，执行状态待查询。"}</p>}
    <div className="knowledge-map-tools">
      <button className="btn btn-sm" disabled={busy || !trackingReady || !value || !current || active || Boolean(pending) || Boolean(trackingIssue) || value.status === "candidate" && !result} onClick={() => void start()}>生成或更新整合（付费）</button>
      <button className="btn btn-sm" disabled={busy} onClick={() => void refresh()}>刷新整合结果</button>
      {pending && <button className="btn btn-sm" disabled={busy} onClick={() => void checkReceipt()}>查询整合回执</button>}
    </div>
    {pending && <p role="status">请求结果未知，只允许查询原回执，不会自动重发。</p>}
    {result && latest && <>
      <p className="banner banner-warn">候选整合 · {AUTOMATIC[result.automatic_status]} · 未专业校准，不是最终交付。{result.demo ? "来源包含演示内容。" : ""}</p>
      <h3 className="sub">{latest.proposal.proposal.title}</h3>
      {latest.proposal.proposal.blocks.map((block) => <div key={block.block_id}>
        <h4>{block.heading}</h4><Prose source={block.body} />{sourceLinks(block.source_span_ids)}
      </div>)}
      {latest.proposal.proposal.relations.length > 0 && <details><summary>跨章关系与依据</summary>
        <ul>{latest.proposal.proposal.relations.map((relation, i) => <li key={i}>
          <Prose source={relation.explanation} />{sourceLinks(relation.source_span_ids)}
        </li>)}</ul>
      </details>}
      {latest.proposal.proposal.unintegrated_units.map((unit) => <p key={unit.unit_id} className="hint">未整合章节：{result.source_index.units.find((u) => u.unit_id === unit.unit_id)?.title}。{unit.reason}</p>)}
      {latest.proposal.proposal.limitations.map((limit, i) => <p key={i} className="hint">限制：{limit}</p>)}
      <details><summary>检查证据与整改记录（{result.rounds.length} 轮）</summary>
        {result.rounds.map((round) => <div key={round.round}>
          <h4>第 {round.round} 轮</h4>
          <ul>{round.review.review.checks.map((check) => <li key={check.check_id}>
            {OBSERVATION[check.result]}：{check.reason}{check.evidence_gap && <p>缺口：{check.evidence_gap}</p>}{sourceLinks(check.source_span_ids)}
          </li>)}</ul>
          {round.review.review.optimization_prompt && <><h5>交给 B 的完整整改提示</h5><Prose source={round.review.review.optimization_prompt} /></>}
          <ul>{round.review.review.recheck_conditions.map((condition, i) => <li key={i}>{condition}</li>)}</ul>
        </div>)}
        <p className="hint">专业认证未建立；原教程质量结论不因整合自动提升。</p>
      </details>
    </>}
  </section>;
}
