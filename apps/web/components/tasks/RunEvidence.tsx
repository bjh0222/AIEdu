"use client";
import { useState } from "react";
import { explain, get } from "@/lib/v2/client";

export default function RunEvidence({ taskId, runId }: { taskId: string; runId: string }) {
  const [value, setValue] = useState<Record<string, unknown> | null>(null), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function load() { setBusy(true); try { setValue(await get(`/tasks/${taskId}/runs/${runId}/manifest`)); setError(""); } catch (e) { setError(explain(e)); } finally { setBusy(false); } }
  return <details className="panel panel-pad run-evidence"><summary>技术审计与运行清单</summary><p className="hint">含内部标识、固定输入及调用证据；只读，不会重新调用模型。</p>
    <button className="btn btn-sm" disabled={busy} onClick={() => void load()}>读取运行清单</button>{error && <p role="alert" className="err">{error}</p>}
    {value && <><button className="btn btn-sm" onClick={() => { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = `manifest-${runId}.json`; a.click(); URL.revokeObjectURL(url); }}>下载运行清单</button><pre className="resource-json">{JSON.stringify(value, null, 2)}</pre></>}
  </details>;
}
