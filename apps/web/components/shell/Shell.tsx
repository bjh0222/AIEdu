"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { applyPrefs, readPrefs } from "@/lib/prefs";
import { ToastProvider } from "@/components/ui/Toast";

const NAV = [
  { href: "/", label: "任务", match: (p: string) => p === "/" || p.startsWith("/tasks") },
  { href: "/new", label: "新建任务", match: (p: string) => p.startsWith("/new") },
  { href: "/library", label: "资源库", match: (p: string) => p.startsWith("/library") || p.startsWith("/legacy") },
  { href: "/settings", label: "设置", match: (p: string) => p.startsWith("/settings") },
];

const NAV_OPEN_KEY = "aiedu.nav.open.v1";
// 2026-11-22 轮3：最左侧应用导航栏可折叠（Shift+F7 / 顶部按钮），状态持久到 localStorage。
const navListeners = new Set<() => void>();
function subscribeNav(listener: () => void) { navListeners.add(listener); return () => { navListeners.delete(listener); }; }
function navClientSnapshot(): boolean {
  try { return localStorage.getItem(NAV_OPEN_KEY) !== "0"; } catch { return true; }
}
function navServerSnapshot(): boolean { return true; }

const NAV_WIDTH_KEY = "aiedu.nav.width.v1";
const NAV_WIDTH_MIN = 170;
const NAV_WIDTH_MAX = 320;
const NAV_WIDTH_DEFAULT = 216;

function useNavWidth(): [number, (event: React.PointerEvent<HTMLDivElement>) => void] {
  const [navWidth, setNavWidth] = useState(NAV_WIDTH_DEFAULT);
  const latestWidth = useRef(NAV_WIDTH_DEFAULT);

  useEffect(() => {
    // 水合后异步读取持久宽度，避免 effect 内同步 setState。
    const id = requestAnimationFrame(() => {
      try {
        const value = Number(localStorage.getItem(NAV_WIDTH_KEY));
        if (Number.isFinite(value) && value >= NAV_WIDTH_MIN && value <= NAV_WIDTH_MAX) {
          setNavWidth(value);
          latestWidth.current = value;
        }
      } catch { /* 无 localStorage 时使用默认宽度 */ }
    });
    return () => cancelAnimationFrame(id);
  }, []);

  const startNavResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = latestWidth.current;
    const move = (moveEvent: PointerEvent) => {
      const next = Math.min(NAV_WIDTH_MAX, Math.max(NAV_WIDTH_MIN, Math.round(startWidth + moveEvent.clientX - startX)));
      latestWidth.current = next;
      setNavWidth(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      try { localStorage.setItem(NAV_WIDTH_KEY, String(latestWidth.current)); } catch { /* 忽略持久化失败 */ }
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }, []);

  return [navWidth, startNavResize];
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const external = useRef<HTMLElement>(null);
  const navOpen = useSyncExternalStore(subscribeNav, navClientSnapshot, navServerSnapshot);
  const [navWidth, startNavResize] = useNavWidth();
  const navCollapsed = !navOpen;
  const toggleNav = useCallback(() => {
    try { localStorage.setItem(NAV_OPEN_KEY, navClientSnapshot() ? "0" : "1"); } catch { /* 忽略持久化失败 */ }
    navListeners.forEach((l) => l());
  }, []);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey || !event.shiftKey) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]')) return;
      if (event.key === "F7") { event.preventDefault(); toggleNav(); return; }
      const name = event.key === "F8" ? "ChatGPT" : event.key === "F9" ? "DeepSeek" : null;
      if (!name) return;
      event.preventDefault();
      external.current?.querySelector<HTMLAnchorElement>(`a[data-brand="${name}"]`)?.click();
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [toggleNav]);
  useEffect(() => {
    const sync = () => applyPrefs(readPrefs());
    sync();
    const media = matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", sync);
    window.addEventListener("storage", sync);
    window.addEventListener("aiedu-prefs", sync);
    return () => {
      media.removeEventListener("change", sync);
      window.removeEventListener("storage", sync);
      window.removeEventListener("aiedu-prefs", sync);
    };
  }, []);
  return (
    <ToastProvider>
      <a className="skip" href="#main">跳到正文</a>
      <div
        className={`app${navCollapsed ? " nav-collapsed" : ""}`}
        style={{ "--sidebar": navCollapsed ? "64px" : `${navWidth}px` } as CSSProperties}
      >
        <aside className="sidebar" data-collapsed={navOpen ? undefined : "true"}>
          <div className="brand-row">
            <Link className="brand" href="/">
              <Image src="/ai-edu-mark.svg" alt="" width={30} height={30} unoptimized />
              <span>AI Edu</span>
            </Link>
            <button
              type="button"
              className="btn btn-sm nav-toggle"
              aria-pressed={navOpen}
              aria-keyshortcuts="Shift+F7"
              title={navOpen ? "折叠导航（Shift + F7）" : "展开导航（Shift + F7）"}
              onClick={toggleNav}
            >{navOpen ? "⟨" : "⟩"}</button>
          </div>
          <nav className="nav" aria-label="主导航">
            {NAV.map((n) => (
              <Link key={n.href} href={n.href} aria-current={n.match(path) ? "page" : undefined}>{n.label}</Link>
            ))}
          </nav>
          <nav className="external-nav" aria-label="外部提问" ref={external}>
            <a href="https://chatgpt.com" target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" data-brand="ChatGPT" aria-keyshortcuts="Shift+F8" title="打开 ChatGPT（Shift + F8，新标签页）">
              <Image src="/brands/chatgpt.svg" alt="ChatGPT 官方标志" width={24} height={24} unoptimized /><span>ChatGPT</span><kbd>Shift F8</kbd>
            </a>
            <a href="https://chat.deepseek.com" target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" data-brand="DeepSeek" aria-keyshortcuts="Shift+F9" title="打开 DeepSeek（Shift + F9，新标签页）">
              <Image src="/brands/deepseek.svg" alt="DeepSeek 官方标志" width={24} height={24} unoptimized /><span>DeepSeek</span><kbd>Shift F9</kbd>
            </a>
          </nav>
          {navOpen && (
            <div
              className="panel-resize"
              role="separator"
              aria-orientation="vertical"
              aria-label="拖拽调整导航栏宽度"
              onPointerDown={startNavResize}
            />
          )}
        </aside>
        <main className="main" id="main" tabIndex={-1}>{children}</main>
      </div>
      <nav className="tabbar" aria-label="主导航（窄屏）">
        {NAV.map((n) => (
          <Link key={n.href} href={n.href} aria-current={n.match(path) ? "page" : undefined}>{n.label}</Link>
        ))}
      </nav>
    </ToastProvider>
  );
}
