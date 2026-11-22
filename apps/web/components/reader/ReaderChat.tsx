"use client";

import { useEffect, useMemo, useState } from "react";
import type { VersionRead } from "@/lib/v2/api";
import { sendReaderChat, type ChatContextUnit, type ChatMessageView, type ChatScope } from "@/lib/v2/chat-api";

type ChatSession = {
  id: string;
  title: string;
  messages: ChatMessageView[];
};

const SCOPE_LABEL: Record<ChatScope, string> = {
  current_unit: "当前章节",
  whole_document: "整篇",
  selected_units: "指定章节",
};

function unitLabel(unitId: string, read: VersionRead | null): string {
  const unit = read?.content.units.find((u) => u.unit_id === unitId);
  return unit?.title || unitId;
}

export default function ReaderChat({ taskId, read, currentUnitId, open, onOpenChange, width = 400 }: {
  taskId: string;
  currentUnitId: string | null;
  read: VersionRead | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  width?: number;
}) {
  const [sessions, setSessions] = useState<ChatSession[]>(() => [{
    id: "chat-1",
    title: "新会话",
    messages: [],
  }]);
  const [activeId, setActiveId] = useState("chat-1");
  const [scope, setScope] = useState<ChatScope>("current_unit");
  const [selectedUnitIds, setSelectedUnitIds] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [quote, setQuote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const units = useMemo(() => read?.content.units ?? [], [read]);
  const currentUnit = units.find((u) => u.unit_id === currentUnitId) ?? units[0];
  const active = sessions.find((s) => s.id === activeId) ?? sessions[0];


  useEffect(() => {
    const capture = () => {
      const selection = window.getSelection();
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;
      const range = selection.getRangeAt(0);
      const node = range.commonAncestorContainer;
      const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : node as Element;
      if (!element?.closest(".prose")) return;
      const text = selection.toString().replace(/\s+/g, " ").trim();
      if (text) setQuote(text.slice(0, 4000));
    };
    document.addEventListener("selectionchange", capture);
    return () => document.removeEventListener("selectionchange", capture);
  }, []);


  function addMessage(sessionId: string, message: ChatMessageView) {
    setSessions((items) => {
      const next = items.map((s) => {
        if (s.id !== sessionId) return s;
        const title = s.title === "新会话" && message.role === "user" ? message.content.slice(0, 24) : s.title;
        return { ...s, title, messages: [...s.messages, message] };
      });
      return next;
    });
  }

  function toggleUnit(unitId: string) {
    setSelectedUnitIds((ids) => ids.includes(unitId) ? ids.filter((id) => id !== unitId) : [...ids, unitId]);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const prompt = question.trim();
    if (!prompt || busy || !active) return;
    const target = scope === "selected_units" ? units.filter((u) => selectedUnitIds.includes(u.unit_id)) : [];
    if (scope === "selected_units" && target.length === 0) {
      setError("请至少选择一个章节。");
      return;
    }
    const contextUnits: ChatContextUnit[] = scope === "current_unit"
      ? (currentUnit ? [{ unit_id: currentUnit.unit_id, title: currentUnit.title ?? "", body: currentUnit.body }] : [])
      : scope === "whole_document"
        ? units.map((u) => ({ unit_id: u.unit_id, title: u.title ?? "", body: u.body }))
        : target.map((u) => ({ unit_id: u.unit_id, title: u.title ?? "", body: u.body }));

    const sessionId = active.id;
    const sentQuote = quote;
    addMessage(sessionId, { id: crypto.randomUUID(), role: "user", content: prompt, quote: sentQuote || undefined });
    setQuestion(""); setQuote(""); setBusy(true); setError("");
    try {
      const result = await sendReaderChat({
        task_id: taskId,
        artifact_id: read?.version_id ?? null,
        question: prompt,
        scope,
        context_units: contextUnits,
        selected_quote: sentQuote || null,
      });
      addMessage(sessionId, { id: result.assistant.id, role: "assistant", content: result.assistant.content });
    } catch (err) {
      setError(err instanceof Error ? err.message : "聊天请求失败。");
    } finally {
      setBusy(false);
    }
  }

  function newSession() {
    const nextId = `chat-${Date.now()}`;
    setSessions((items) => [...items, { id: nextId, title: "新会话", messages: [] }]);
    setActiveId(nextId);
    setError("");
    setQuote("");
    setQuestion("");
  }

  return (
    <aside className="chat-rail" aria-label="AI 聊天" hidden={!open} style={{
      position: "relative", display: open ? "flex" : "none",
      flexDirection: "column", gap: 12, padding: 14, borderRadius: 14,
      border: "1px solid var(--line)", background: "color-mix(in srgb, var(--surface) 92%, transparent)", minHeight: 0,
    }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <h2 className="chat-title">Tips</h2>
        <button className="btn btn-quiet btn-sm" onClick={() => onOpenChange(false)} aria-label="折叠聊天（Shift+F6）" title="折叠聊天（Shift + F6）">⇥</button>
      </header>

      <div className="panel" style={{ flex: "0 0 auto", padding: 8 }} aria-label="上下文范围">
        <div className="seg" role="group" aria-label="上下文范围">
          {(Object.entries(SCOPE_LABEL) as [ChatScope, string][]).map(([value, label]) => (
            <button key={value} aria-pressed={scope === value} onClick={() => setScope(value)}>{label}</button>
          ))}
        </div>
        {scope === "selected_units" && (
          <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {units.map((u) => (
              <button key={u.unit_id} className="btn btn-sm" aria-pressed={selectedUnitIds.includes(u.unit_id)}
                onClick={() => toggleUnit(u.unit_id)}>
                {selectedUnitIds.includes(u.unit_id) ? "✓ " : ""}{unitLabel(u.unit_id, read)}
              </button>
            ))}
          </div>
        )}
        {quote && <p className="chat-quote-flag">已捕获引用</p>}
      </div>

      {width >= 300 && (
        <div className="panel" style={{ flex: "0 0 auto", maxHeight: 96, overflow: "auto", padding: 7, fontSize: 12 }} aria-label="聊天会话">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <small className="muted">会话</small>
            <button className="btn btn-quiet btn-sm" onClick={newSession}>＋ 新会话</button>
          </div>
          <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 3 }}>
            {sessions.map((s) => (
              <li key={s.id}>
                <button className="btn btn-quiet btn-sm" aria-pressed={s.id === activeId}
                  style={{ width: "100%", justifyContent: "flex-start" }}
                  onClick={() => { setActiveId(s.id); setError(""); }}>
                  {s.title}
                  <span className="muted" style={{ marginLeft: "auto" }}>{s.messages.length}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div style={{ flex: "1 1 auto", minHeight: 0, overflowY: "auto", display: "grid", gap: 8, alignContent: "start", paddingRight: 2 }}>
        {active?.messages.map((m) => (
          <article key={m.id} className="panel panel-pad"
            style={{ borderColor: m.role === "user" ? "var(--primary)" : "var(--line)", padding: 7, fontSize: 12 }} aria-label={m.role === "user" ? "用户消息" : "模型回答"}>
            <b className="muted" style={{ fontSize: 12 }}>{m.role === "user" ? "我" : "助手"}</b>
            {m.quote && <p className="hint" style={{ marginTop: 4 }}>已捕获引用</p>}
            <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{m.content}</div>
          </article>
        ))}
        {busy && <p className="hint" role="status">正在请求模型…</p>}
      </div>

      <form className="panel panel-pad" style={{ flex: "0 0 auto", padding: 8, display: "grid", gap: 6 }} onSubmit={submit}>
        {error && <p className="err" role="alert" style={{ margin: 0 }}>{error}</p>}
        <label className="sr-only" htmlFor="reader-chat-input">聊天输入</label>
        <textarea id="reader-chat-input" value={question} rows={2}
          placeholder="输入问题，Enter 发送" style={{ resize: "vertical", minHeight: 58 }}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit(e);
            }
          }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
          <small className="muted">范围：{SCOPE_LABEL[scope]}{quote ? " · 有引用" : ""}</small>
          <button className="btn btn-primary btn-sm" type="submit" disabled={busy || !question.trim()}>发送</button>
        </div>
      </form>
    </aside>
  );
}



