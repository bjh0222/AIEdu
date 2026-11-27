"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

// 2026-11-27 移动端阅读优先：bottom 形态升级为 modal bottom sheet（M3 规范：
// 28px 顶角圆角、drag handle 触控目标 ≥48px、辅助内容可关闭回主内容）。
// sheetHeights=true 时支持两档高度（默认 70vh / 上滑 92vh）与下滑关闭；
// right/普通 bottom 行为不变（无把手、无高度档、焦点陷阱与 Esc/遮罩关闭保留）。
export function Drawer({ open, onClose, title, side = "right", sheetHeights = false, children }: {
  open: boolean;
  onClose: () => void;
  title: string;
  side?: "right" | "bottom";
  sheetHeights?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);
  const [expanded, setExpanded] = useState(false);
  const dragStart = useRef<{ y: number; expanded: boolean } | null>(null);

  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement;
    ref.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab" && ref.current) {
        const f = ref.current.querySelectorAll<HTMLElement>("a[href],button:not(:disabled),input,select,textarea,[tabindex]:not([tabindex='-1'])");
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onClose]);

  // sheet 打开期间锁定页面滚动，关闭后恢复。
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  // 2026-11-27 打开时重置高度档：render 期条件调整（避免 effect 内同步 setState）。
  const [prevOpen, setPrevOpen] = useState(false);
  if (open !== prevOpen) { setPrevOpen(open); if (open) setExpanded(false); }

  if (!open) return null;

  const onHandlePointerDown = (e: React.PointerEvent) => {
    if (!sheetHeights) return;
    dragStart.current = { y: e.clientY, expanded };
  };
  const onHandlePointerUp = (e: React.PointerEvent) => {
    const start = dragStart.current;
    dragStart.current = null;
    if (!start) return;
    const dy = e.clientY - start.y;
    if (dy < -60) setExpanded(true);
    else if (dy > 60) { if (start.expanded) setExpanded(false); else onClose(); }
  };

  const isSheet = side === "bottom" && sheetHeights;
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div
        ref={ref}
        className={`drawer drawer-${side}${isSheet ? ` drawer-sheet${expanded ? " drawer-sheet-expanded" : ""}` : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        {isSheet && (
          <div
            className="drawer-grab"
            role="button"
            aria-label="拖动调整高度；上滑放大，下滑收起或关闭"
            onPointerDown={onHandlePointerDown}
            onPointerUp={onHandlePointerUp}
            onDoubleClick={() => setExpanded((v) => !v)}
          ><span aria-hidden="true" /></div>
        )}
        {!isSheet && (
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h2 className="sub">{title}</h2>
            <button className="btn btn-quiet btn-sm" onClick={onClose}>关闭</button>
          </div>
        )}
        {isSheet && (
          <div className="drawer-sheet-head">
            <h2 className="sub">{title}</h2>
            <button className="btn btn-quiet btn-sm" onClick={onClose}>关闭</button>
          </div>
        )}
        <div className="drawer-sheet-body">{children}</div>
      </div>
    </>
  );
}

