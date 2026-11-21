"use client";

import { useEffect, useState } from "react";
import { api, type ExportManifest } from "@/lib/v2/api";
import { ApiError, downloadBlob, explain, newKey } from "@/lib/v2/client";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { calibrationText } from "@/lib/v2/status";
import { synthesis, exportSelection, type SynthesisRead } from "@/lib/v2/synthesis-api";

type Props = { open: boolean; onClose: () => void; artifactId: string; versionId: string; versionLabel: string; revision: number; taskId: string; refresh?: () => Promise<void> };

const ROLE = { tutorial: "教程", exercise: "练习", answer: "参考答案", paper: "试卷", cheatsheet: "速查", chapter: "章节", book_level: "全书层" } as Record<string, string>;
const fmtSize = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

/** 固定版本下载：创建 export-set 零模型调用，再逐文件下载；文件字节与 hash 由后端固定。 */
export default function ExportDialog({ open, onClose, artifactId, versionId, versionLabel, revision, taskId, refresh }: Props) {
  return <FixedExportDialog key={`${artifactId}:${versionId}`} {...{ open, onClose, artifactId, versionId, versionLabel, revision, taskId, refresh }} />;
}

function FixedExportDialog({ open, onClose, artifactId, versionId, versionLabel, revision, taskId, refresh }: Props) {
  const toast = useToast();
  const [manifest, setManifest] = useState<ExportManifest | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [missing, setMissing] = useState<string[] | null>(null);
  const [finalRejected, setFinalRejected] = useState(false);
  const [key, setKey] = useState(() => newKey());
  const [integration, setIntegration] = useState<SynthesisRead | null>(null);
  const [includeIntegration, setIncludeIntegration] = useState(false);
  const [integrationError, setIntegrationError] = useState("");

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    synthesis.read(taskId, versionId, controller.signal).then((v) => {
      if (!controller.signal.aborted) { setIntegration(v); setIntegrationError(""); }
    }).catch((e) => { if (!controller.signal.aborted) setIntegrationError(explain(e)); });
    return () => controller.abort();
  }, [open, taskId, versionId]);

  async function build(kind: "complete" | "available_draft") {
    setBusy(true);
    setError("");
    if (kind === "complete") setFinalRejected(false);
    try {
      const fixed = await api.version(artifactId, versionId);
      const binding = fixed.export_binding;
      if (fixed.version_id !== versionId || binding && (binding.task_id !== taskId || binding.version_id !== versionId ||
          binding.content_hash !== fixed.content_hash || !Number.isSafeInteger(binding.state_revision) || binding.state_revision < 0)) {
        throw new Error("固定版本的导出绑定无效，请重新读取版本。");
      }
      // Old API/fixture compatibility uses the existing revision. New servers
      // return the VERSION-owning run's CAS, not the currently executing child.
      const exportRevision = binding?.state_revision ?? revision;
      const selected = exportSelection(integration, fixed);
      if (includeIntegration && !selected) throw new Error("整合与当前固定版本不匹配，请重新读取后选择。");
      const m = await api.createExport(artifactId, { expected_revision: exportRevision, version_id: versionId, kind,
        ...(includeIntegration ? { options: { synthesis: selected } } : {}) }, key, taskId);
      setManifest(m);
      setMissing(null);
      setFinalRejected(false);
      toast("下载包已生成");
    } catch (e) {
      if (e instanceof ApiError && e.code === "INCOMPLETE_REQUEST") {
        setMissing(((e.details.missing_required as string[]) ?? []).map((r) => ROLE[r] ?? r));
      } else if (e instanceof ApiError && e.code === "FINAL_EXPORT_NOT_PASSED") {
        setFinalRejected(true);
        setError("质量门未通过或尚未校准，不能作为最终交付。可以先下载明确标注的草稿。");
      } else if (e instanceof ApiError && e.code === "PROFILE_BINDING_STALE") {
        setFinalRejected(true);
        setError("导入理解 profile 已更新，不能继续作为最终交付。可以先下载明确标注的草稿。");
      } else if (e instanceof ApiError && e.code === "REVISION_STALE") {
        await refresh?.();
        setError(explain(e));
      } else setError(explain(e));
      setKey(newKey()); // 服务端明确拒绝 = 未执行，下一次是新决策
    } finally {
      setBusy(false);
    }
  }

  async function download(fileId: string) {
    if (!manifest) return;
    try {
      const { blob, name } = await downloadBlob(`/export-sets/${manifest.bundle_id}/files/${fileId}`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (e) {
      setError(explain(e));
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={`下载 ${versionLabel}`}>
      <p className="muted">下载的是固定版本，字节和校验值不会变化，也不会调用模型。</p>
      {!manifest && (
        <>
          {missing && (
            <div className="banner banner-warn" role="alert">
              <div><b>原请求尚未完整。</b> 还缺：{missing.join("、") || "部分产物"}。可以先下载已有草稿。</div>
            </div>
          )}
          {finalRejected && (
            <div className="banner banner-warn" role="alert">
              <div><b>不能作为最终交付。</b> 质量门未通过或绑定已过期。可以先下载明确标注的草稿。</div>
            </div>
          )}
          {error && <div className="banner banner-danger" role="alert"><div>{error}</div></div>}
          {integration?.status === "candidate" && integration.source_is_current && <label>
            <input type="checkbox" checked={includeIntegration} disabled={busy} onChange={(e) => { setIncludeIntegration(e.target.checked); setKey(newKey()); }} />
            将此版本的知识与方法整合并入同一文件（未校准，仅草稿）
          </label>}
          {integrationError && <p className="hint">未能读取整合：{integrationError}。仍可仅导出已保存章节。</p>}
          <div className="dlg-actions">
            <button className="btn" onClick={onClose}>取消</button>
            {missing || finalRejected || includeIntegration ? (
              <button className="btn btn-primary" aria-busy={busy} disabled={busy} onClick={() => void build("available_draft")}>下载已有草稿</button>
            ) : (
              <button className="btn btn-primary" aria-busy={busy} disabled={busy} onClick={() => void build("complete")}>生成下载包</button>
            )}
          </div>
        </>
      )}
      {manifest && (
        <>
          {manifest.demo && <p><span className="demo-tag">演示</span> 文件由演示模式生成，不是真实模型输出。</p>}
          {manifest.delivery_status === "draft" && (
            <div className="banner banner-warn"><div><b>草稿交付。</b> 这不是质量通过的最终版本，请勿当作已完成教程使用。</div></div>
          )}
          {manifest.profile_binding?.status === "stale" && (
            <div className="banner banner-warn"><div><b>Profile 绑定已过期。</b> 只能作为历史草稿查看，不能当作最终交付。</div></div>
          )}
          {manifest.missing_required.length > 0 && (
            <div className="banner banner-warn"><div><b>缺项：</b>{manifest.missing_required.map((r) => ROLE[r] ?? r).join("、")}。原请求尚未完整。</div></div>
          )}
          <p className="hint">质量：{manifest.quality.tqi_verdict} · {calibrationText(manifest.quality.calibration_status)}。下载包可用不等于质量通过。</p>
          <ul>
            {manifest.files.map((f) => (
              <li key={f.file_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                <span>
                  {f.file_id === "bundle" ? "全部文件（zip）" : f.file_id === "main" ? "全部章节（一份 Markdown）" : f.file_id === "answers" ? "参考答案（一份 Markdown）" : ROLE[f.artifact_role ?? ""] ?? f.name}
                  <span className="hint num"> {fmtSize(f.size)}</span>
                </span>
                <button className="btn btn-sm" onClick={() => void download(f.file_id)}>下载</button>
              </li>
            ))}
          </ul>
          {error && <div className="banner banner-danger" role="alert"><div>{error}</div></div>}
          <div className="dlg-actions"><button className="btn" onClick={onClose}>完成</button></div>
        </>
      )}
    </Dialog>
  );
}
