import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import "./globals.css";
import Shell from "@/components/shell/Shell";
import Splash, { splashBootstrap } from "@/components/shell/Splash";
import { prefsBootstrap } from "@/lib/prefs";

export const metadata: Metadata = {
  title: { default: "AI Edu", template: "%s · AI Edu" },
  description: "说出需求，生成并检查教程、练习与试卷。过程可见、可暂停，质量依据与缺口如实展示。",
  icons: { icon: "/ai-edu-mark.svg", apple: "/icons/icon-192.png" },
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "default", title: "AI Edu" },
};
// 2026-11-27 移动端问答 sheet：Android Chrome 键盘收缩布局视口；iOS 由 visualViewport 补偿（Reader.tsx）。
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#000000" }, { media: "(prefers-color-scheme: light)", color: "#1d5c6e" }], interactiveWidget: "resizes-content" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
          <script dangerouslySetInnerHTML={{ __html: prefsBootstrap }} />
          <script dangerouslySetInnerHTML={{ __html: splashBootstrap }} />
        </head>
      <body>
        <Splash />
        <Suspense fallback={null}>
          <Shell>{children}</Shell>
        </Suspense>
      </body>
    </html>
  );
}

