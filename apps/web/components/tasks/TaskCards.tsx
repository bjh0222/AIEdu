"use client";

import Link from "next/link";
import type { CSSProperties } from "react";
import type { TaskListItem } from "@/lib/v2/contracts.generated";
import ResourceActions from "@/components/library/ResourceActions";
import styles from "./TaskCards.module.css";

type Tone = "primary" | "warn" | "ok" | "danger";

function status(t: TaskListItem): { text: string; tone: Tone } {
  if (t.needs_action) return { text: "需要你处理", tone: "warn" };
  if (t.state === "queued" || t.state === "running") return { text: "进行中", tone: "primary" };
  if (t.state === "paused") return { text: "已暂停", tone: "warn" };
  if (t.state === "failed" || t.state === "interrupted") return { text: "已中断", tone: "danger" };
  if (t.state === "completed") return { text: t.version_id ? "可阅读" : "已结束", tone: "ok" };
  return { text: "未启动", tone: "primary" };
}

function summary(t: TaskListItem): string {
  // 后端 summary 为 raw_input 服务端截断摘要；raw_input 仅兼容旧后端响应。
  const raw = t.summary ?? t.raw_input;
  const value = (typeof raw === "string" && raw.trim()) || t.title.trim();
  return value.length > 140 ? `${value.slice(0, 140)}…` : value;
}

function TaskCard({ task, onChanged }: { task: TaskListItem; onChanged: () => void }) {
  const finished = task.state === "completed";
  const href = finished && task.version_id ? `/tasks/${task.task_id}/read` : `/tasks/${task.task_id}`;
  const hint = status(task);
  const actionLabel = task.needs_action ? "去处理" : finished && task.version_id ? "阅读" : "打开";
  const text = summary(task);

  return (
    <article className={`panel panel-pad ${styles.card}`} aria-label={task.title}>
      <div className={styles.cardHead}>
        <Link className={styles.title} href={href}>{task.title}</Link>
        {task.demo === true && <span className="badge">演示</span>}
      </div>
      <p className={styles.summary}>{text}</p>
      <div className={styles.cardFoot}>
        <span className={styles.status} data-tone={hint.tone}>{hint.text}</span>
        <div className={styles.actions}>
          <Link className="btn btn-sm" href={href}>{actionLabel}</Link>
          <ResourceActions kind="tasks" id={task.task_id} label={task.title} onChanged={onChanged} />
        </div>
      </div>
    </article>
  );
}

export default function TaskCards({ items, onChanged }: { items: TaskListItem[]; onChanged: () => void }) {
  const rowCount = Math.ceil(items.length / 2);
  return (
    <div
      className={styles.grid}
      style={{ "--task-grid-rows": rowCount } as CSSProperties}
      aria-label="任务卡片"
    >
      {items.map((task) => <TaskCard key={task.task_id} task={task} onChanged={onChanged} />)}
    </div>
  );
}
