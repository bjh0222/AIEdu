"use client";

import Link from "next/link";

// The backend owns the report. Narrow unknown fields before rendering, so
// historic or partial reports never turn missing values into a positive score.
function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function rows(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((v) => v !== null && typeof v === "object" && !Array.isArray(v)).map(record) : [];
}
function text(value: unknown, fallback = "未记录"): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : fallback;
}
const RESULT: Record<string, string> = { satisfied: "达到已冻结判据", violated: "未达到判据，需整改", undecidable: "检查证据不完整", not_applicable: "不适用" };
const AXIS: Record<string, string> = { prerequisites: "先修知识", cognitive_operations: "认知操作", nonroutine_operations: "非套路操作", concept_interactions: "概念联动", steps: "推理依赖" };
const METRIC: Record<string, string> = { ...AXIS, dependency_depth: "推理依赖深度" };

function Evidence({ value }: { value: unknown }) {
  return rows(value).map((span, i) => <blockquote className="break-anywhere" key={i}>
    {text(span.quote)}
    <footer className="hint">{text(span.field)} · UTF-8 {text(span.start_byte)}–{text(span.end_byte)}</footer>
  </blockquote>);
}

export function AssessmentEvidence({ taskId, versionId, quality }: { taskId: string; versionId: string | null; quality: unknown }) {
  const report = record(quality), difficulty = record(report.difficulty_review), automatic = record(report.automated_assessment);
  const difficultyItems = rows(difficulty.items), criteria = rows(automatic.criteria), coverage = record(automatic.coverage), q4 = record(automatic.Q4);
  const locate = (unit: unknown) => `/tasks/${taskId}/read?version=${encodeURIComponent(versionId ?? "")}&unit=${encodeURIComponent(text(unit, ""))}`;
  return <>
    {Object.keys(difficulty).length > 0 && <section className="panel panel-pad" aria-label="逐题先验难度" style={{ marginBottom: 20 }}>
      <h2 className="sub">逐题先验难度</h2>
      <p>{RESULT[text(difficulty.result)] ?? "尚无检查结论"}。记录 {difficultyItems.length} 题。</p>
      <p className="hint">这是对固定题目的先验认知需求分析，不是实测难度、区分度或专业认证。计算负担单列，不作为难度的替代指标；每题都须达到目标。</p>
      {difficultyItems.map((item, index) => <details key={`${text(item.unit_id)}:${text(item.item_id, String(index))}`}>
        <summary>题目 {text(item.ordinal)}：{RESULT[text(item.result)] ?? "结论未知"}</summary>
        <dl className="kv">{rows(item.comparisons).map((comparison) => <div key={text(comparison.metric)}>
          <dt>{METRIC[text(comparison.metric)] ?? text(comparison.metric)}</dt>
          <dd>实际 {text(comparison.observed)} / 目标至少 {text(comparison.required)}；{RESULT[text(comparison.result)] ?? "未知"}</dd>
        </div>)}</dl>
        <p>计算负担：{({ light: "轻", moderate: "中", heavy: "重" } as Record<string, string>)[text(record(item.computation_load).level)] ?? "未知"}；不代表认知难度。</p>
        <p className="hint">证据绑定：{item.input_binding_complete === true ? "完整" : "未完整，不能通过"}。</p>
        {Object.entries(record(item.dimensions)).map(([axis, entries]) => <details key={axis}>
          <summary>{AXIS[axis] ?? axis}（{rows(entries).length}）</summary>
          {rows(entries).map((entry, n) => <div key={n}><p>{text(entry.reason)}</p>
            {Array.isArray(entry.depends_on) && <p className="hint">依赖：{entry.depends_on.length ? entry.depends_on.map((d) => text(d)).join("、") : "起始步骤"}</p>}
            <Evidence value={entry.evidence} /></div>)}
        </details>)}
        <Link className="btn btn-sm" href={locate(item.unit_id)}>阅读本题所在章节</Link>
        <details><summary>固定目标与调用依据</summary><pre className="evidence" tabIndex={0}>{JSON.stringify({ target: item.target, audit: item.audit, fields_hash: item.item_fields_hash, method_version: item.method_version }, null, 2)}</pre></details>
      </details>)}
    </section>}
    {Object.keys(automatic).length > 0 && <section className="panel panel-pad" aria-label="逐条内容评价" style={{ marginBottom: 20 }}>
      <h2 className="sub">逐条内容评价</h2>
      <p>{RESULT[text(automatic.result)] ?? "尚无检查结论"}。已记录 {text(coverage.recorded)} / {text(coverage.expected)}，有结论 {text(coverage.decided)}。</p>
      <p>典型性与迁移：中位数 {text(q4.observed_median, "未知")} / 3；要求中位数至少 {text(q4.required_median)}，每题至少 {text(q4.per_item_minimum)}。</p>
      <p className="hint">逐项 AI 观察与专业质量认证分开。未专业校准；缺失检查不计通过，格式通过不代表内容通过。</p>
      {criteria.map((criterion, index) => <details key={`${text(criterion.unit_id)}:${text(criterion.check_id)}:${text(criterion.item_id, String(index))}`}>
        <summary>{text(criterion.check_id)}{criterion.ordinal != null ? ` · 题目 ${text(criterion.ordinal)}` : ""}：{RESULT[text(criterion.result)] ?? "未知"}{typeof criterion.rating === "number" ? `（${criterion.rating} / 3）` : ""}</summary>
        {rows(criterion.clauses).map((clause, n) => <div key={n}><p>{text(clause.clause_id)}：{RESULT[text(clause.result)] ?? "未知"}。{text(clause.reason)}</p><Evidence value={clause.evidence} /></div>)}
        <Link className="btn btn-sm" href={locate(criterion.unit_id)}>阅读对应章节</Link>
        <details><summary>判据与调用依据</summary><pre className="evidence" tabIndex={0}>{JSON.stringify({ criterion_hash: criterion.criterion_hash, audit: criterion.audit }, null, 2)}</pre></details>
      </details>)}
    </section>}
  </>;
}
