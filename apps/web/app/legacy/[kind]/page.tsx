import type { Metadata } from "next";
import { Suspense } from "react";
import Legacy from "@/components/library/Legacy";

export const metadata: Metadata = { title: "旧数据（只读）" };

export default async function Page({ params }: { params: Promise<{ kind: string }> }) {
  const { kind } = await params;
  return (
    <div className="page">
      <Suspense fallback={<div className="skeleton" style={{ height: 320 }} aria-busy="true" />}>
        <Legacy kind={kind} />
      </Suspense>
    </div>
  );
}
