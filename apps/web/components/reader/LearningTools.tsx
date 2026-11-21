"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { act, explain, get, newKey } from "@/lib/v2/client";
import type { VersionRead } from "@/lib/v2/api";
import { api, type Capabilities } from "@/lib/v2/api";
import Prose from "./Prose";

type Card = { card_id: string; title: string; kind: string; quote: string; status: string; revision: number;
  conditions?: unknown; steps?: unknown; boundaries?: unknown; example?: unknown; related_claims?: unknown;
  source: { unit_id: string; quote_hash: string } };
type Cards = { cards: Card[]; revision: number; coverage: string; source_current_in_run?: boolean };
type Profile = { revision: number; active: { event_id: string; kind: string; text: string }[]; learning_gain: string };

function CardValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "" || Array.isArray(value) && !value.length) return <p className="hint">原文未提供，待补充核验。</p>;
  if (typeof value === "string") return <Prose source={value} />;
  if (Array.isArray(value)) return <ol>{value.map((item, index) => <li key={index}><CardValue value={item} /></li>)}</ol>;
  if (typeof value === "object") return <dl>{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd><CardValue value={item} /></dd></div>)}</dl>;
  return <p>{String(value)}</p>;
}

export default function LearningTools({ taskId, read, onNavigate }: { taskId: string; read: VersionRead; onNavigate: (unit: string) => void }) {
  const [cards, setCards] = useState<Cards | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [kind, setKind] = useState("confusion");
  const identity = useRef(new Map<string, string>());
  const eventId = useRef(newKey());
  const refresh = useCallback(async () => {
    const [c, p] = await Promise.all([get<Cards>(`/tasks/${taskId}/versions/${read.version_id}/cards`), get<Profile>("/learning-profile")]);
    setCards(c); setProfile(p);
  }, [taskId, read.version_id]);
  useEffect(() => { let active = true; api.capabilities().then((v) => { if (active) setCapabilities(v); }).catch(() => {}); return () => { active = false; }; }, []);
  useEffect(() => { const controller = new AbortController();
    Promise.all([get<Cards>(`/tasks/${taskId}/versions/${read.version_id}/cards`, { signal: controller.signal }), get<Profile>("/learning-profile", { signal: controller.signal })])
      .then(([c, p]) => { if (!controller.signal.aborted) { setCards(c); setProfile(p); } })
      .catch((e) => { if (!controller.signal.aborted) setError(explain(e)); });
    return () => controller.abort();
  }, [taskId, read.version_id]);

  async function write(path: string, body: unknown, method: "POST" | "PUT" = "POST") {
    const signature = JSON.stringify([path, body]);
    const key = identity.current.get(signature) ?? newKey();
    identity.current.set(signature, key);
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await act<{ run_id?: string }>(method, path, body, { kind: "learning_tools", taskId, key });
      setMessage(result.run_id ? "已创建独立分析任务；请在工作台查看调用与结果，刷新这里读取知识卡。" : "已保存（不改变教程质量结论）。");
      await refresh();
      return true;
    } catch (e) { setError(explain(e)); return false; }
    finally { setBusy(false); }
  }

  return <section className="learning-tools panel panel-pad" aria-label="知识整理与学习反馈">
    <h2 className="sub">把这次学习留下来</h2>
    <p className="hint">知识卡绑定当前固定版本。模型只能提出候选，确认只表示你的整理；学习增益仍未测量。</p>
    {error && <p className="err" role="alert">{error} <button className="btn btn-sm" onClick={() => void refresh().then(() => setError("")).catch((e) => setError(explain(e)))}>重新读取</button></p>}
    {message && <p role="status">{message}</p>}
    <div className="knowledge-map-tools">
      <button className="btn btn-sm" onClick={() => { const url = URL.createObjectURL(new Blob([JSON.stringify({ cards, profile }, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = `learning-${read.version_id}.json`; a.click(); URL.revokeObjectURL(url); }}>导出知识卡与反馈</button>
      <button className="btn btn-sm" disabled={busy || Boolean(cards?.revision)} onClick={() => void write(`/tasks/${taskId}/card-extractions`, { version_id: read.version_id, content_hash: read.content_hash, expected_revision: cards?.revision ?? 0, mode: "extractive" })}>摘取明确方法/结论（不调用模型）</button>
      <button className="btn btn-sm" disabled={busy || Boolean(cards?.revision) || !capabilities?.paid_calls_enabled || !capabilities.role_configuration.review?.configured} onClick={() => { if (window.confirm("将对当前固定版本创建真实模型分析任务，可能产生费用；不会自动重发或确认卡片。继续？")) void write(`/tasks/${taskId}/card-extractions`, { version_id: read.version_id, content_hash: read.content_hash, expected_revision: cards?.revision ?? 0, mode: "configured" }); }}>模型归纳知识卡（付费）</button>
      <button className="btn btn-sm" disabled={busy} onClick={() => void refresh().catch((e) => setError(explain(e)))}>刷新知识卡</button>
    </div>
    {!cards?.cards.length && <p className="hint">{cards?.coverage === "not_run" ? "尚未整理。" : "没有取得可回源的卡片；不是已完整归纳。"}</p>}
    <ul className="learning-card-list">
      {cards?.cards.map((card) => <li key={card.card_id}>
        <h3 className="sub">{card.kind === "method" ? "方法" : "结论"} · {card.title}</h3>
        <p className="hint">状态：{({ candidate: "候选", confirmed: "已整理确认", rejected: "已拒绝", retracted: "已撤回", unknown: "未知" } as Record<string, string>)[card.status] ?? card.status}{cards.source_current_in_run === false ? "；来源已有后续版本，请复核" : ""}</p>
        <blockquote><Prose source={card.quote} /></blockquote>
        <details>
          <summary>查看归纳详情</summary>
          <p className="hint">以下是来源片段的归纳，尚未经专业核验；缺失信息保持待补充。</p>
          <dl>
            <div><dt>适用条件</dt><dd><CardValue value={card.conditions} /></dd></div>
            <div><dt>通用步骤</dt><dd><CardValue value={card.steps} /></dd></div>
            <div><dt>反例与边界</dt><dd><CardValue value={card.boundaries} /></dd></div>
            <div><dt>典型例</dt><dd><CardValue value={card.example} /></dd></div>
            <div><dt>相关命题</dt><dd><CardValue value={card.related_claims} /></dd></div>
          </dl>
        </details>
        <div className="knowledge-map-tools">
          <button className="btn btn-sm" onClick={() => onNavigate(card.source.unit_id)}>回到来源章节</button>
          {(["confirmed", "rejected", "retracted", "unknown"] as const).map((status) => <button key={status} className="btn btn-sm" disabled={busy || status === card.status || status === "confirmed" && cards.source_current_in_run === false}
            onClick={() => void write(`/tasks/${taskId}/versions/${read.version_id}/cards/${card.card_id}`, { expected_revision: card.revision, quote_hash: card.source.quote_hash, status, reason: "用户显式整理" }, "PUT")}>{({ confirmed: "确认整理", rejected: "拒绝", retracted: "撤回", unknown: "标为未知" })[status]}</button>)}
        </div>
      </li>)}
    </ul>
    <form className="learning-feedback" onSubmit={async (e) => { e.preventDefault(); if (!profile || !text.trim()) return; const ok = await write("/learning-feedback", {
      expected_revision: profile.revision, event_id: eventId.current, kind, text, task_id: taskId, version_id: read.version_id, content_hash: read.content_hash,
    }); if (ok) { setText(""); eventId.current = newKey(); } }}>
      <h3 className="sub">记录我的学习反馈</h3>
      <label htmlFor="learning-feedback-kind">反馈类型</label>
      <select id="learning-feedback-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="confusion">困惑点（影响后续任务）</option><option value="preference">教学偏好（影响后续任务）</option><option value="bookmark">收藏</option><option value="correction">纠错线索（不是事实认证）</option><option value="mastery_self_report">自报掌握（不是能力测量）</option>
      </select>
      <label htmlFor="learning-feedback-text">反馈内容</label><textarea id="learning-feedback-text" rows={3} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} />
      <button className="btn btn-sm" disabled={busy || !profile || !text.trim()}>保存反馈</button>
    </form>
    <details><summary>生效中的显式反馈（可撤回）</summary><ul>{profile?.active.map((event) => <li key={event.event_id}>{event.text} <button className="btn btn-sm" disabled={busy} onClick={() => void write("/learning-feedback", { expected_revision: profile.revision, event_id: `retract:${event.event_id}`, kind: "retract", target_event_id: event.event_id })}>撤回这条反馈</button></li>)}</ul></details>
  </section>;
}
