import type { Metadata } from "next";
import { Suspense } from "react";
import ImportExternal from "@/components/tasks/ImportExternal";

export const metadata: Metadata = { title: "导入外部任务" };

export default function Page() {
  return (
    <div className="page-narrow">
      <Suspense fallback={<div className="skeleton" style={{ height: 320 }} aria-busy="true" />}>
        <ImportExternal />
      </Suspense>
    </div>
  );
}
