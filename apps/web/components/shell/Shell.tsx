"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type ReactNode } from "react";
import { applyPrefs, readPrefs } from "@/lib/prefs";
import { ToastProvider } from "@/components/ui/Toast";

const NAV = [
  { href: "/", label: "任务", match: (p: string) => p === "/" || p.startsWith("/tasks") },
  { href: "/new", label: "新建任务", match: (p: string) => p.startsWith("/new") },
  { href: "/library", label: "资源库", match: (p: string) => p.startsWith("/library") || p.startsWith("/legacy") },
  { href: "/settings", label: "设置", match: (p: string) => p.startsWith("/settings") },
];

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const external = useRef<HTMLElement>(null);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey || !event.shiftKey) return;
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]')) return;
      const name = event.key === "F8" ? "ChatGPT" : event.key === "F9" ? "DeepSeek" : null;
      if (!name) return;
      event.preventDefault();
      external.current?.querySelector<HTMLAnchorElement>(`a[data-brand="${name}"]`)?.click();
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
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
      <div className="app">
        <aside className="sidebar">
          <Link className="brand" href="/">
            <Image src="/ai-edu-mark.svg" alt="" width={30} height={30} unoptimized />
            <span>AI Edu</span>
          </Link>
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
