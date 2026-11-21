import type { Metadata } from "next";
import { Suspense } from "react";
import Workbench from "@/components/tasks/Workbench";

export const metadata: Metadata = { title: "工作台" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="page">
      <Suspense fallback={<div className="skeleton" style={{ height: 320 }} aria-busy="true" />}>
        <Workbench taskId={id} />
      </Suspense>
    </div>
  );
}
