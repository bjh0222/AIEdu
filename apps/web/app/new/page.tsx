import type { Metadata } from "next";
import { Suspense } from "react";
import NewTask from "@/components/tasks/NewTask";

export const metadata: Metadata = { title: "新建任务" };

export default function Page() {
  return (
    <div className="page-narrow">
      <Suspense fallback={<div className="skeleton" style={{ height: 320 }} aria-busy="true" />}>
        <NewTask />
      </Suspense>
    </div>
  );
}
