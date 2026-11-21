"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { explain, getConditional } from "@/lib/v2/client";
import type { KnowledgeGraph as Graph, RunSnapshot } from "@/lib/v2/contracts.generated";
import { activeRunStage, runBadge, STAGE_LABEL } from "@/lib/v2/status";

const KIND: Record<string, string> = { chapter: "章节", method: "方法卡", conclusion: "结论卡" };
const STATUS: Record<string, string> = { planned: "已规划，尚未保存", saved: "已保存", candidate: "模型/摘取候选", confirmed: "用户已整理，非专业认证", unknown: "待补证" };

export default function KnowledgeMap({ taskId, runId, revision, versionId, onNavigate, run, stale, sidebar }: {
  taskId: string; runId?: string; revision?: number; versionId?: string; onNavigate?: (unitId: string) => void;
  run?: RunSnapshot; stale?: boolean; sidebar?: ReactNode;
}) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [local, setLocal] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [page, setPage] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [recent, setRecent] = useState<string[]>([]);
  const [recentEdges, setRecentEdges] = useState<string[]>([]);
  const previous = useRef(new Map<string, string>());
  const previousEdges = useRef(new Set<string>());
  const [viewportWidth, setViewportWidth] = useState(780);
  const [transition, setTransition] = useState(false);
  const stage = run ? activeRunStage(run) : undefined;
  const phase = stage ?? run?.state;
  const priorStage = useRef({ runId, phase });
  const etag = useRef<{ path: string; value: string | null }>({ path: "", value: null });
  const canvas = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const changed = priorStage.current.runId === runId && priorStage.current.phase !== undefined && priorStage.current.phase !== phase && Boolean(stage) && !stale;
    priorStage.current = { runId, phase };
    const frame = requestAnimationFrame(() => setTransition(changed));
    const timer = setTimeout(() => setTransition(false), 1100);
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [stage, phase, stale, runId]);
  useEffect(() => {
    const query = new URLSearchParams();
    if (runId) query.set("run_id", runId);
    if (versionId) query.set("version_id", versionId);
    const path = `/tasks/${encodeURIComponent(taskId)}/knowledge-graph?${query}`;
    const controller = new AbortController();
    getConditional<Graph>(path, etag.current.path === path ? etag.current.value : null, controller.signal)
      .then(({ data, etag: value }) => {
        if (controller.signal.aborted) return;
        const samePath = etag.current.path === path;
        etag.current = { path, value };
        if (data) {
          setRecent(samePath && previous.current.size ? data.nodes.filter((n) => n.status !== "planned" && previous.current.get(n.id) !== `${n.status}:${n.source_hash ?? ""}`).map((n) => n.id) : []);
          const edges = data.edges.map((e) => `${e.source}:${e.target}:${e.kind}`);
          setRecentEdges(samePath ? edges.filter((e) => !previousEdges.current.has(e)) : []);
          previousEdges.current = new Set(edges);
          previous.current = new Map(data.nodes.map((n) => [n.id, `${n.status}:${n.source_hash ?? ""}`]));
          setGraph(data);
        }
        setError("");
      })
      .catch((e) => { if (!controller.signal.aborted) setError(explain(e)); });
    return () => controller.abort();
  }, [taskId, runId, revision, versionId, refresh]);
  const hasGraph = Boolean(graph?.nodes.length);
  useEffect(() => {
    if (!canvas.current) return;
    const observer = new ResizeObserver(([entry]) => setViewportWidth(entry.contentRect.width));
    observer.observe(canvas.current);
    return () => observer.disconnect();
  }, [hasGraph]);
  const chosen = graph?.nodes.find((n) => n.id === selected) ?? null;
  const nodes = useMemo(() => {
    if (!graph) return [];
    if (!local || !chosen) return graph.nodes;
    const neighbors = new Set([chosen.id]);
    graph.edges.forEach((e) => { if (e.source === chosen.id) neighbors.add(e.target); if (e.target === chosen.id) neighbors.add(e.source); });
    return graph.nodes.filter((n) => neighbors.has(n.id));
  }, [graph, chosen, local]);
  const pages = Math.max(1, Math.ceil(nodes.length / 60));
  const currentPage = Math.min(page, pages - 1);
  const shown = nodes.slice(currentPage * 60, (currentPage + 1) * 60);
  const columns = Math.max(1, Math.min(3, Math.floor(viewportWidth / 252)));
  const width = Math.max(256, viewportWidth);
  const height = Math.max(180, Math.ceil(shown.length / columns) * 132 + 36);
  const position = new Map(shown.map((n, i) => [n.id, { x: 16 + (i % columns) * (width / columns), y: 24 + Math.floor(i / columns) * 132 }]));
  const activeCall = run?.calls.find((c) => c.status === "sent" || c.status === "authorized");
  const activeTitle = activeCall ? graph?.nodes.find((n) => n.unit_id === activeCall.unit_id)?.title ?? run?.units.find((u) => u.unit_id === activeCall.unit_id)?.body.match(/^#{1,6}\s+(.+)$/m)?.[1] : undefined;

  function moveFocus(event: React.KeyboardEvent, index: number) {
    const delta = ({ ArrowRight: 1, ArrowLeft: -1, ArrowDown: columns, ArrowUp: -columns } as Record<string, number>)[event.key];
    const next = event.key === "Home" ? 0 : event.key === "End" ? shown.length - 1 : delta !== undefined ? Math.min(shown.length - 1, Math.max(0, index + delta)) : null;
    if (next !== null) { event.preventDefault(); canvas.current?.querySelectorAll<HTMLButtonElement>(".knowledge-node")[next]?.focus(); }
  }

  return <section className="knowledge-map panel" aria-label="知识体系">
    <div className="knowledge-map-head">
      <div><h2 className="sub">知识正在成形</h2>{graph?.demo && <span className="demo-tag">演示数据</span>}</div>
      <span className="badge tone-neutral" role="status">{graph ? `${graph.nodes.filter((n) => n.status !== "planned").length} 个已保存节点 / ${graph.nodes.length} 个节点` : "正在读取结构"}</span>
    </div>
    {error && <div className="banner banner-warn" role="alert"><span>结构读取失败：{error}。正文与生成状态不受影响。</span><button className="btn btn-sm" onClick={() => setRefresh((v) => v + 1)}>重试结构</button></div>}
    <div className="knowledge-growth-layout"><div className="knowledge-growth-main">
    <div className="knowledge-map-tools" role="group" aria-label="知识图控制">
      <button className="btn btn-sm" aria-pressed={!local} onClick={() => setLocal(false)}>全局</button>
      <button className="btn btn-sm" aria-pressed={local} disabled={!chosen} onClick={() => { setLocal(true); setPage(0); }}>所选与相邻</button>
      <button className="btn btn-sm" aria-label="缩小知识图" disabled={zoom <= 0.6} onClick={() => setZoom((v) => Math.max(0.6, v - 0.2))}>−</button>
      <button className="btn btn-sm" aria-label="重置知识图缩放" onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
      <button className="btn btn-sm" aria-label="放大知识图" disabled={zoom >= 1.6} onClick={() => setZoom((v) => Math.min(1.6, v + 0.2))}>＋</button>
      <span className="hint">方向键移动，Enter 选择。连线只表示返回的已记录关系。</span>
    </div>
    {!graph || !graph.nodes.length ? <div className="empty"><p>{graph ? "还没有可显示的真实结构。" : "等待读取已保存结构。"}</p></div> : <>
      <div className="knowledge-viewport" ref={canvas} tabIndex={0} aria-label="可横向及纵向滚动的知识图">
        <div style={{ width: width * zoom, height: height * zoom }}><div className="knowledge-canvas" style={{ width, height, transform: `scale(${zoom})`, transformOrigin: "top left" }}>
          <svg width={width} height={height} aria-hidden="true" className="knowledge-edges">
            {graph.edges.map((e) => {
              const a = position.get(e.source), b = position.get(e.target);
              if (!a || !b) return null;
              return <path key={`${e.source}-${e.target}-${e.kind}`} d={`M${a.x + 112},${a.y + 82} C${a.x + 112},${a.y + 115} ${b.x + 112},${b.y - 24} ${b.x + 112},${b.y}`} className={`${e.kind} ${recentEdges.includes(`${e.source}:${e.target}:${e.kind}`) ? "relation-added" : ""}`} />;
            })}
          </svg>
          {shown.map((node, index) => {
            const at = position.get(node.id)!;
            return <button key={node.id} className={`knowledge-node ${node.kind} ${node.status} ${recent.includes(node.id) ? "recently-saved" : ""}`} style={{ left: at.x, top: at.y }} aria-pressed={chosen?.id === node.id}
              onClick={() => setSelected(node.id)} onKeyDown={(e) => moveFocus(e, index)} title={`${node.title}；${STATUS[node.status] ?? node.status}`}>
              <span className="hint">{KIND[node.kind] ?? node.kind}</span><strong>{node.title}</strong><small>{STATUS[node.status] ?? node.status}</small>
            </button>;
          })}
        </div></div>
      </div>
      {pages > 1 && <div className="knowledge-map-tools"><button className="btn btn-sm" disabled={currentPage === 0} onClick={() => setPage((v) => v - 1)}>上一组节点</button><span>第 {currentPage + 1} / {pages} 组；每组最多 60 个，跨组关系保留在数据中。</span><button className="btn btn-sm" disabled={currentPage + 1 >= pages} onClick={() => setPage((v) => v + 1)}>下一组节点</button></div>}
      <div className="knowledge-detail" aria-live="polite">
        {chosen ? <><h3 className="sub">{chosen.title}</h3><p>{chosen.summary || "该单元仍在规划中，没有正文摘要。"}</p><p className="hint">{STATUS[chosen.status] ?? chosen.status}。{chosen.source_hash ? "已绑定保存来源。" : "尚无保存来源。"}</p>
          <ul aria-label="节点关系">{graph.edges.filter((e) => e.source === chosen.id || e.target === chosen.id).map((e) => <li key={`${e.source}:${e.target}:${e.kind}`}>
            {graph.nodes.find((n) => n.id === e.source)?.title} {e.kind === "contains" ? "包含" : "是前置于"} {graph.nodes.find((n) => n.id === e.target)?.title}
          </li>)}</ul>
          {chosen.version_id && chosen.source_hash && (onNavigate ? <button className="btn btn-sm" onClick={() => onNavigate(chosen.unit_id)}>进入对应章节</button> : <Link className="btn btn-sm" href={`/tasks/${taskId}/read?version=${chosen.version_id}&unit=${encodeURIComponent(chosen.unit_id)}`}>进入对应章节</Link>)}</> : <p className="hint">选择节点查看来源和章节入口。候选知识卡不是验证结论。</p>}
      </div>
      <details className="knowledge-detail"><summary>全部已记录关系（{graph.edges.length}）</summary><ul>{graph.edges.map((e) => <li key={`${e.source}:${e.target}:${e.kind}`}>{graph.nodes.find((n) => n.id === e.source)?.title ?? "来源节点缺失"} {e.kind === "contains" ? "包含" : "前置于"} {graph.nodes.find((n) => n.id === e.target)?.title ?? "目标节点缺失"}</li>)}</ul>{!graph.edges.length && <p className="hint">尚无已记录关系，不推测语义联系。</p>}</details>
    </>}
    </div>{run && <aside className="knowledge-growth-side" aria-label="生成状态与用量">
      <div key={stage ?? run.state} className={`growth-stage stage-${stage ?? run.state}${transition ? " stage-transition" : ""}`} data-stage={stage ?? run.state} role="status"><span className="growth-stage-mark" aria-hidden="true" /><h3 className="sub">{stage ? STAGE_LABEL[stage] ?? "运行" : runBadge(run.state).label}</h3>{stage && (STAGE_LABEL[stage] ?? "运行") !== runBadge(run.state, stage).label && <p>{runBadge(run.state, stage).label}</p>}</div>
      {activeCall && <p>活动单元：{activeTitle || "尚无已保存名称"}</p>}
      {run.calls.some((c) => c.status === "unknown") && <p className="warn">存在结果未知的调用，尚不能确认完成或费用。</p>}
      {stale && <p className="warn">连接未刷新，以下是上次读取的状态。</p>}
      <p className="hint">只随已读取的阶段和保存记录更新；运行结束不代表质量通过。</p>
      {sidebar}
    </aside>}</div>
  </section>;
}
