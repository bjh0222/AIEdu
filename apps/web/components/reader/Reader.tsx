"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isRun, type VersionRead, type VersionUnit } from "@/lib/v2/api";
import { explain } from "@/lib/v2/client";
import { useSnapshot } from "@/lib/v2/use-snapshot";
import { extractHeadings, locateFinding, splitAnswers, PIN_REASON, type Location } from "@/lib/v2/reader-core";
import { FINDING_STATUS } from "@/lib/v2/status";
import { rememberVersion } from "@/lib/v2/seen-versions";
import { readPrefs, savePrefs, type Prefs, defaultPrefs } from "@/lib/prefs";
import { Drawer } from "@/components/ui/Drawer";
import ExportDialog from "@/components/tasks/ExportDialog";
import KnowledgeMap from "@/components/tasks/KnowledgeMap";
import LearningTools from "./LearningTools";
import SynthesisPanel from "./SynthesisPanel";
import Prose, { type Pin } from "./Prose";
import ReaderChat from "./ReaderChat";

type Finding = { finding_id: string; claim: string; status: string; severity_class: string; location: Location; verification_result?: string; category?: string; context_conflict?: boolean; location_status?: string; evidence?: unknown };
type PinState = { finding: Finding; n: number; ok: boolean; reason?: string; start?: number; end?: number };

const ROLE: Record<string, string> = { tutorial: "教程", exercise: "练习", answer: "参考答案", paper: "试卷", cheatsheet: "速查", chapter: "章节", book_level: "全书层" };

function useStablePrefs() {
  const [prefs, setPrefs] = useState<Prefs>(defaultPrefs);
  useEffect(() => {
    const t = setTimeout(() => setPrefs(readPrefs()), 0);
    const sync = () => setPrefs(readPrefs());
    window.addEventListener("aiedu-prefs", sync);
    return () => {
      clearTimeout(t);
      window.removeEventListener("aiedu-prefs", sync);
    };
  }, []);
  return [prefs, (p: Prefs) => { setPrefs(p); savePrefs(p); }] as const;
}

export default function Reader({ taskId }: { taskId: string }) {
  const params = useSearchParams();
  const wantVersion = params.get("version");
  const { snapshot, error: snapError, refresh } = useSnapshot(taskId);
  const run = snapshot && isRun(snapshot) ? snapshot : null;
  const [prefs, setPrefs] = useStablePrefs();

  const versionId = wantVersion ?? run?.version_id ?? null;
  const [read, setRead] = useState<VersionRead | null>(null);
  const [readError, setReadError] = useState("");
  const [loadingVersion, setLoadingVersion] = useState(false);
  const [unitId, setUnitId] = useState<string | null>(() => params.get("unit"));
  const [focus, setFocus] = useState(false);
  const [active, setActive] = useState<string>("");
  const [progress, setProgress] = useState(0);
  const [answersOpen, setAnswersOpen] = useState(false);
  const [activePin, setActivePin] = useState<number | null>(null);
  const [drawer, setDrawer] = useState<"toc" | "issues" | "tech" | null>(null);
  const [chatOpen, setChatOpen] = useState(true);
  const [exportOpen, setExportOpen] = useState(false);
  // 2026-11-22 轮3：文内位置树与 AI 聊天为 VSCode 式 resizable sidebar。
  const [rulerWidth, startRulerResize] = usePanelWidth("aiedu.reader.rulerWidth.v1", 240, 160, 480, 1);
  const [chatWidth, startChatResize] = usePanelWidth("aiedu.reader.chatWidth.v1", 340, 280, 560, -1);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [pins, setPins] = useState<PinState[]>([]);
  const sheetRef = useRef<HTMLDivElement>(null);

  const artifactId = run?.artifact_id ?? null;

  useEffect(() => {
    if (!artifactId || !versionId) return;
    let alive = true;
    const t = setTimeout(() => {
      setLoadingVersion(true);
      setReadError("");
      api.version(artifactId, versionId)
        .then((v) => {
          if (!alive) return;
          setRead(v);
          rememberVersion(artifactId, { version_id: v.version_id, version: v.version, at: Date.now() });
        })
        .catch((e) => alive && setReadError(explain(e)))
        .finally(() => alive && setLoadingVersion(false));
    }, 0);
    return () => { alive = false; clearTimeout(t); };
  }, [artifactId, versionId]);

  const units = useMemo<VersionUnit[]>(() => read?.content.units ?? [], [read]);
  const unit = units.find((u) => u.unit_id === unitId) ?? units[0];
  const split = useMemo(() => (unit ? splitAnswers(unit.body) : { body: "", answers: null }), [unit]);
  const documentAnswers = useMemo(() => {
    const value = read?.document_end_answers;
    return typeof value === "string" && value.trim() ? value : null;
  }, [read]);
  const isLastUnit = Boolean(unit && units.length > 0 && unit.unit_id === units[units.length - 1].unit_id);
  const showDocumentAnswers = documentAnswers !== null && isLastUnit;
  const bodyText = split.body;
  const headings = useMemo(() => extractHeadings(bodyText), [bodyText]);
  const wikiTargets = useMemo(() => {
    const m = new Map<string, string>();
    for (const h of headings) m.set(h.text, h.id);
    units.forEach((u) => { if (u.unit_id !== unit?.unit_id) m.set(u.unit_id, `unit-${u.unit_id}`); });
    return m;
  }, [headings, units, unit]);

  // 问题定位：版本 + unit hash + span hash 全部通过才渲染；否则只在右栏标“位置已过期”。
  const findings = useMemo<Finding[]>(() => (read?.quality.findings as Finding[] | undefined) ?? [], [read]);
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!read || !unit) return setPins([]);
      const mine = findings.filter((f) => f.location?.unit_id === unit.unit_id);
      const out: PinState[] = [];
      for (let i = 0; i < mine.length; i++) {
        const f = mine[i];
        const r = await locateFinding(unit.body, unit.content_hash, read.version_id, f.location);
        out.push(r.ok ? { finding: f, n: i + 1, ok: true, start: r.start, end: r.end } : { finding: f, n: i + 1, ok: false, reason: PIN_REASON[r.reason] });
      }
      if (alive) setPins(out);
    })();
    return () => { alive = false; };
  }, [read, unit, findings]);

  const renderPins = useMemo<Pin[]>(
    () => pins.filter((p) => p.ok && p.start !== undefined && p.end !== undefined && p.start < bodyText.length)
      .map((p) => ({ n: p.n, start: p.start!, end: p.end!, state: p.finding.status === "confirmed" ? "confirmed" as const : "suspected" as const })),
    [pins, bodyText],
  );

  // 滚动 → 目录高亮与进度
  useEffect(() => {
    const onScroll = () => {
      const el = document.documentElement;
      const max = el.scrollHeight - el.clientHeight;
      setProgress(max > 0 ? Math.min(100, Math.round((el.scrollTop / max) * 100)) : 0);
      let cur = "";
      for (const h of headings) {
        const node = document.getElementById(h.id);
        if (node && node.getBoundingClientRect().top <= 120) cur = h.id;
      }
      setActive(cur);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [headings, answersOpen]);

  useEffect(() => {
    if (!focus) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && setFocus(false);
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [focus]);

  // Shift+F7 已改由 Shell 折叠最左侧应用导航；Shift+F6 仍折叠 AI 聊天。
  useEffect(() => {
    const toggleChat = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey || !event.shiftKey) return;
      if (event.key === "F6") {
        event.preventDefault(); setChatOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", toggleChat);
    return () => window.removeEventListener("keydown", toggleChat);
  }, []);

  const gotoPin = useCallback((n: number) => {
    setActivePin(n);
    document.querySelectorAll(".is-active").forEach((e) => e.classList.remove("is-active"));
    const el = document.querySelector<HTMLElement>(`[data-pin="${n}"], [data-pin-block="${n}"]`);
    el?.classList.add("is-active");
    el?.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    document.getElementById(`issue-${n}`)?.scrollIntoView({ block: "nearest" });
  }, []);

  const isCurrent = !run?.version_id || read?.version_id === run.version_id;

  if (!run && !snapError) return <div className="skeleton" style={{ height: 480 }} aria-busy="true" aria-label="正在加载" />;
  if (!run) return <div className="empty"><p>{snapError || "无法读取任务。"}</p><Link className="btn" href={`/tasks/${taskId}`}>回到工作台</Link></div>;
  if (!versionId) {
    return (
      <div className="empty"><h1 className="title">尚无已保存产物</h1><p>写好第一个单元后就可以阅读。</p><Link className="btn" href={`/tasks/${taskId}`}>回到工作台</Link></div>
    );
  }

  const versionLabel = read ? `v${read.version}` : "…";
  const serif = prefs.serif;
  const stale = pins.filter((p) => !p.ok);
  const issuesCount = pins.length;
  const title = (run.resolved_task as { title?: string } | undefined)?.title ?? "阅读";

  const ruler = (
    <nav aria-label="目录">
      <h2>文内位置</h2>
      {headings.length === 0 ? <p className="hint">这份内容没有标题。</p> : (
        <div className="ruler-track">
          {headings.map((h) => {
            const hasIssue = false;
            return (
              <a key={h.id} href={`#${h.id}`} className={`tick l${Math.min(h.level, 3)}`} aria-current={active === h.id ? "true" : undefined}
                onClick={() => setDrawer(null)}>
                {h.text}{hasIssue && <span className="tick-issue" aria-hidden="true">◇</span>}
              </a>
            );
          })}
        </div>
      )}
      {units.length > 1 && (
        <div style={{ marginTop: 20 }}>
          <h2>章节</h2>
          <ul>
            {units.map((u) => (
              <li key={u.unit_id}>
                <button className="btn btn-quiet btn-sm" style={{ width: "100%", justifyContent: "flex-start" }} aria-pressed={u.unit_id === unit?.unit_id}
                  onClick={() => { setUnitId(u.unit_id); setAnswersOpen(false); window.scrollTo({ top: 0 }); }}>
                  {u.unit_id === unit?.unit_id ? "● " : "○ "}{u.title || ROLE[u.artifact_role] || u.artifact_role}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="hint" style={{ marginTop: 16 }}>已读到 {progress}%</p>
    </nav>
  );

  const issuePanel = (
    <section aria-label="问题">
      <h2>问题（{issuesCount}）</h2>
      {issuesCount === 0 && <p className="hint">这一份内容没有记录到具体位置的问题。质量总体情况不在此页展示，未评价不等于通过。</p>}
      {pins.map((p) => (
        <button key={p.finding.finding_id} id={`issue-${p.n}`} className={`issue${p.ok ? "" : " stale"}`} aria-current={activePin === p.n ? "true" : undefined}
          onClick={() => { if (p.ok) gotoPin(p.n); }} aria-disabled={!p.ok}>
          <span><span className="n">{p.finding.status === "confirmed" ? "◆" : "◇"}{p.n}</span> {FINDING_STATUS[p.finding.status] ?? p.finding.status}</span>
          <span>{p.finding.claim}</span>
          {!p.ok && <span className="hint">位置已过期：{p.reason}。</span>}
        </button>
      ))}
    </section>
  );

  return (
    <div className="reader-shell">
      <div className="progress-line" style={{ width: `${progress}%` }} aria-hidden="true" />
      <div className="reader-top">
        <nav className="crumbs grow" aria-label="位置" style={{ margin: 0 }}>
          <Link href="/">任务</Link><span aria-hidden="true">/</span>
          <Link href={`/tasks/${taskId}`}>{title}</Link><span aria-hidden="true">/</span>
          <span aria-current="page">阅读</span>
        </nav>
        <div className="reader-tools" role="group" aria-label="阅读设置">
          <button className="btn btn-sm" onClick={() => setPrefs({ ...prefs, size: Math.max(14, prefs.size - 1) })} aria-label="减小字号">A−</button>
          <button className="btn btn-sm" onClick={() => setPrefs({ ...prefs, size: Math.min(22, prefs.size + 1) })} aria-label="增大字号">A+</button>
          <button className="btn btn-sm" aria-pressed={focus} onClick={() => setFocus(!focus)}>{focus ? "退出专注" : "专注"}</button>
          <button className="btn btn-sm chat-toggle-btn" aria-keyshortcuts="Shift+F6" onClick={() => setChatOpen(!chatOpen)} title={chatOpen ? "折叠聊天（Shift + F6）" : "展开聊天（Shift + F6）"}>{chatOpen ? "折叠聊天" : "展开聊天"}</button>
          <button className="btn btn-sm only-narrow issues-btn" onClick={() => setDrawer("issues")}>问题 {issuesCount}</button>
          <button className="btn btn-primary btn-sm" onClick={() => setExportOpen(true)} disabled={!read}>下载 {versionLabel}</button>
        </div>
      </div>

      {readError && <div className="banner banner-danger" role="alert" style={{ marginBottom: 12 }}><div>{readError}</div></div>}
      {!isCurrent && read && (
        <div className="banner banner-warn banner-historic" role="status">
          <div><b>只读 · 历史版本。</b> 你正在看 v{read.version}，当前版本更新。这里不提供修复或生成。 <Link href={`/tasks/${taskId}/read`}>回到当前版本</Link></div>
        </div>
      )}
      {run.demo && <p style={{ marginBottom: 12 }}><span className="demo-tag">演示</span> <span className="muted">内容由演示模式生成，不是真实模型输出。</span></p>}

      <details className="reader-knowledge disclosure" style={{ marginBottom: 20 }} onToggle={(e) => setKnowledgeOpen(e.currentTarget.open)}>
        <summary>探索知识体系与方法卡</summary>
        {knowledgeOpen && <KnowledgeMap taskId={taskId} versionId={versionId} onNavigate={(id) => { setUnitId(id); setAnswersOpen(false); }} />}
        {knowledgeOpen && read && <LearningTools key={read.version_id} taskId={taskId} read={read} onNavigate={(id) => { setUnitId(id); setAnswersOpen(false); }} />}
        {knowledgeOpen && read && <SynthesisPanel key={`${read.version_id}:${read.quality_hash}`} taskId={taskId} run={run} read={read} onNavigate={(id) => { setUnitId(id); setAnswersOpen(false); }} />}
      </details>
      <div className={`reader-grid${focus ? " focus" : ""}`} style={{ gridTemplateColumns: focus ? "minmax(0, 1fr)" : `${rulerWidth}px minmax(0, 1fr)${chatOpen ? ` ${chatWidth}px` : ""}` }}>
        <aside className="ruler" aria-label="目录与章节" style={{ width: rulerWidth }}>
          {ruler}
          <ResizeHandle side="right" label="拖拽调整文内位置面板宽度" onPointerDown={startRulerResize} />
        </aside>

        <article className={`sheet-wrap${serif ? " serif" : ""}`}>
          {loadingVersion && !read ? (
            <div className="sheet" aria-busy="true"><div className="skeleton" style={{ height: 36, width: "60%" }} /><div className="skeleton" style={{ height: 14, marginTop: 24 }} /><div className="skeleton" style={{ height: 14, marginTop: 10 }} /><div className="skeleton" style={{ height: 14, marginTop: 10, width: "80%" }} /></div>
          ) : (
            <div className={`sheet${isCurrent ? "" : " historic"}`} ref={sheetRef}>
              <header className="sheet-head">
                <span>{title}</span>
                <span>固定版本 {versionLabel}{read && (read.quality.tqi_verdict ? "" : "")}</span>
              </header>
              {units.length === 0 && read && <p className="muted">这个版本没有可显示的内容。</p>}
              {unit ? (
                <>
                  <Prose key={`${unit.unit_id}-${read?.version_id}`} source={bodyText} headings={headings} wikiTargets={wikiTargets} pins={renderPins} onPinClick={gotoPin} />
                  {split.answers && (
                    <div className="answer-gate">
                      <button className="answer-toggle" aria-expanded={answersOpen} aria-controls="answers" onClick={() => setAnswersOpen(!answersOpen)}>
                        <span>{answersOpen ? "收起参考答案" : "显示参考答案"}</span><span aria-hidden="true">{answersOpen ? "▲" : "▼"}</span>
                      </button>
                      <div id="answers" hidden={!answersOpen}>{answersOpen && <Prose source={split.answers} />}</div>
                    </div>
                  )}
                  {showDocumentAnswers && <DocumentAnswersGate source={documentAnswers!} />}
                </>
              ) : null}
              <footer className="sheet-foot">
                <span>已读到 {progress}%</span>
                <button className="btn btn-quiet btn-sm" onClick={() => setDrawer("tech")}>技术信息</button>
              </footer>
            </div>
          )}
          {stale.length > 0 && <p className="hint" style={{ marginTop: 8 }}>有 {stale.length} 个问题的位置已过期，没有标在正文里。</p>}
        </article>

        {chatOpen && !focus && (
          <div className="chat-cell" style={{ width: chatWidth }}>
            <ReaderChat taskId={taskId} read={read} currentUnitId={unit?.unit_id ?? null} open={chatOpen} onOpenChange={setChatOpen} />
            <ResizeHandle side="left" label="拖拽调整 AI 聊天面板宽度" onPointerDown={startChatResize} />
          </div>
        )}
      </div>

      <Drawer open={drawer === "toc"} onClose={() => setDrawer(null)} title="目录" side="bottom">{ruler}</Drawer>
      <Drawer open={drawer === "issues"} onClose={() => setDrawer(null)} title="问题" side="bottom">{issuePanel}</Drawer>
      <Drawer open={drawer === "tech"} onClose={() => setDrawer(null)} title="技术信息" side="right">
        {read && (
          <dl className="kv" style={{ gridTemplateColumns: "auto 1fr" }}>
            <dt>版本编号</dt><dd className="mono" style={{ textAlign: "left" }}>{read.version_id}</dd>
            <dt>正文校验值</dt><dd className="mono" style={{ textAlign: "left" }}>{read.content_hash}</dd>
            <dt>质量校验值</dt><dd className="mono" style={{ textAlign: "left" }}>{read.quality_hash}</dd>
            <dt>单元校验值</dt><dd className="mono" style={{ textAlign: "left" }}>{unit?.content_hash}</dd>
          </dl>
        )}
      </Drawer>

      {read && artifactId && (
        <ExportDialog key={read.version_id} open={exportOpen} onClose={() => setExportOpen(false)} artifactId={artifactId} versionId={read.version_id} versionLabel={versionLabel} revision={run.state_revision} taskId={taskId} refresh={refresh} />
      )}
    </div>
  );
}


// VSCode 式 resizable sidebar：面板边缘拖拽调宽，min/max 限界，宽度持久到 localStorage。
function usePanelWidth(key: string, initial: number, min: number, max: number, grow: 1 | -1): [number, (e: React.PointerEvent<HTMLDivElement>) => void] {
  const [width, setWidth] = useState(initial);
  const latest = useRef(initial);
  useEffect(() => {
    // 水合后异步读取持久宽度，避免 effect 内同步 setState（react-hooks/set-state-in-effect）。
    const id = requestAnimationFrame(() => {
      try {
        const v = Number(localStorage.getItem(key));
        if (Number.isFinite(v) && v >= min && v <= max) { setWidth(v); latest.current = v; }
      } catch { /* 无 localStorage 时使用默认宽度 */ }
    });
    return () => cancelAnimationFrame(id);
  }, [key, min, max]);
  const start = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = latest.current;
    const move = (ev: PointerEvent) => {
      const next = Math.min(max, Math.max(min, Math.round(startW + grow * (ev.clientX - startX))));
      latest.current = next;
      setWidth(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try { localStorage.setItem(key, String(latest.current)); } catch { /* 忽略持久化失败 */ }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, [key, min, max, grow]);
  return [width, start];
}

function ResizeHandle({ side, label, onPointerDown }: { side: "left" | "right"; label: string; onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void }) {
  return <div className={`panel-resize ${side}`} role="separator" aria-orientation="vertical" aria-label={label} onPointerDown={onPointerDown} />;
}

function DocumentAnswersGate({ source }: { source: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="answer-gate">
      <button className="answer-toggle" aria-expanded={open} aria-controls="document-answers" onClick={() => setOpen(!open)}>
        <span>{open ? "收起统一参考答案" : "显示统一参考答案"}</span><span aria-hidden="true">{open ? "▲" : "▼"}</span>
      </button>
      <div id="document-answers" hidden={!open}>{open && <Prose source={source} />}</div>
    </div>
  );
}






