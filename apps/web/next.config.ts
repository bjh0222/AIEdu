import type { NextConfig } from "next";

// 旧 1.0 地址的兼容映射：不携带也不猜测旧 ID，统一落到只读兼容页（AT-14）。
const legacyRedirects = [
  { source: "/goals", destination: "/legacy/goals" },
  { source: "/goals/:path*", destination: "/legacy/goals" },
  { source: "/tutorials/:path*", destination: "/legacy/tutorials" },
  { source: "/knowledge", destination: "/legacy/concepts" },
  { source: "/proposition", destination: "/library?tab=knowledge" },
  { source: "/training", destination: "/legacy/practice" },
];

const nextConfig: NextConfig = {
  distDir: process.env.AIEDU_NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  experimental: { proxyTimeout: 3_600_000 },
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async redirects() {
    return legacyRedirects.map((r) => ({ ...r, permanent: false }));
  },
  // 浏览器只访问同源 /api/*，由 Next 服务端转发到本机 v2 后端（默认 127.0.0.1:8010）。
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_BACKEND_URL ?? "http://127.0.0.1:8010"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
