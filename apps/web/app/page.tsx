import type { Metadata } from "next";
import { Suspense } from "react";
import TaskCenter from "@/components/tasks/TaskCenter";

export const metadata: Metadata = { title: "任务" };

export default function Page() {
  return (
    <div className="page">
      <Suspense fallback={<div className="skeleton" style={{ height: 240 }} aria-busy="true" />}>
        <TaskCenter />
      </Suspense>
    </div>
  );
}
