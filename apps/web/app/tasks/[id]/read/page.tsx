import type { Metadata } from "next";
import { Suspense } from "react";
import Reader from "@/components/reader/Reader";

export const metadata: Metadata = { title: "阅读" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense fallback={<div className="skeleton" style={{ height: 480 }} aria-busy="true" />}>
      <Reader taskId={id} />
    </Suspense>
  );
}
