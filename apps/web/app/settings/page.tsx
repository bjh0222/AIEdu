import type { Metadata } from "next";
import { Suspense } from "react";
import Settings from "@/components/settings/Settings";

export const metadata: Metadata = { title: "设置" };

export default function Page() {
  return (
    <div className="page">
      <Suspense fallback={<div className="skeleton" style={{ height: 320 }} aria-busy="true" />}>
        <Settings />
      </Suspense>
    </div>
  );
}
