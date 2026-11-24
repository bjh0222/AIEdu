import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import "./globals.css";
import Shell from "@/components/shell/Shell";
import { prefsBootstrap } from "@/lib/prefs";

export const metadata: Metadata = {
  title: { default: "AI Edu", template: "%s · AI Edu" },
  description: "说出需求，生成并检查教程、练习与试卷。过程可见、可暂停，质量依据与缺口如实展示。",
  icons: { icon: "/ai-edu-mark.svg", apple: "/icons/icon-192.png" },
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, statusBarStyle: "default", title: "AI Edu" },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#1d5c6e" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: prefsBootstrap }} /></head>
      <body>
        <Suspense fallback={null}>
          <Shell>{children}</Shell>
        </Suspense>
      </body>
    </html>
  );
}
