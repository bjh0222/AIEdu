import type { Metadata } from "next";
import { Suspense } from "react";
import Library from "@/components/library/Library";

export const metadata: Metadata = { title: "资源库" };

export default function Page() {
  return (
    <div className="page">
      <Suspense fallback={<div className="skeleton" style={{ height: 320 }} aria-busy="true" />}>
        <Library />
      </Suspense>
    </div>
  );
}
