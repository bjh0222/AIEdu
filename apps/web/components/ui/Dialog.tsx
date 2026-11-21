"use client";

import { useEffect, useRef, type ReactNode } from "react";

/** 原生 <dialog>：浏览器负责焦点圈定、Esc 关闭；关闭后焦点回到触发元素。 */
export function Dialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      opener.current = document.activeElement;
      el.showModal();
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);
  return (
    <dialog
      ref={ref}
      className="dlg"
      aria-label={title}
      onClose={() => {
        onClose();
        (opener.current as HTMLElement | null)?.focus?.();
      }}
    >
      <div className="dlg-body">
        <h2 className="sub">{title}</h2>
        {open ? children : null}
      </div>
    </dialog>
  );
}
