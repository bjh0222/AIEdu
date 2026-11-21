import type { Badge as B } from "@/lib/v2/status";

export function BadgeView({ b, axis }: { b: B; axis?: string }) {
  return (
    <span className={`badge tone-${b.tone}`} title={b.hint} aria-label={axis ? `${axis}：${b.label}` : b.label}>
      <span className={`shape shape-${b.shape}${b.pulse ? " pulse" : ""}`} aria-hidden="true" />
      <span className="badge-text">{b.label}</span>
    </span>
  );
}

/** 运行｜质量｜交付 三轴并排，顺序固定，不合并、不省略。 */
export function StatusAxes({ run, quality, delivery }: { run: B; quality: B; delivery: B }) {
  return (
    <div className="axes" role="group" aria-label="任务状态（运行、质量、交付三个独立维度）">
      <span className="axis"><span className="axis-name">运行</span><BadgeView b={run} axis="运行状态" /></span>
      <span className="axis"><span className="axis-name">质量</span><BadgeView b={quality} axis="质量状态" /></span>
      <span className="axis"><span className="axis-name">交付</span><BadgeView b={delivery} axis="交付状态" /></span>
    </div>
  );
}
