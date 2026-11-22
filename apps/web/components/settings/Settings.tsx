"use client";

import { useEffect, useState } from "react";
import {act, explain, get, getReceipt, newKey, UnknownOutcomeError} from "@/lib/v2/client";
import { api, type Capabilities } from "@/lib/v2/api";
import { defaultPrefs, readPrefs, savePrefs, type Prefs } from "@/lib/prefs";
import { useToast } from "@/components/ui/Toast";
import ResourceActions from "@/components/library/ResourceActions";
import ResourceManager from "@/components/library/ResourceManager";

type Profile = { id: string; name: string; model: string; base_url: string; has_key: boolean; thinking?: string | null; reasoning_effort?: string | null; model_family?: string };
type ModelSettings = {
  config: { active_id: string | null; user_profile: string; profiles: Profile[] };
  config_hash: string;
  roles: Record<string, { configured: boolean; model?: string; model_family?: string; reason?: string }>;
  role_assignments?: Record<string, string>;
  role_profiles?: { id: string; name: string; model?: string; model_family?: string }[];
};
const ROLE_TEXT: Record<string, string> = {
  generation: "生成", review: "独立检查", blind_solver: "盲解", secondary_review: "二次复核", benchmark_panel: "基准评审面板", benchmark_refiner: "基准量规精炼",
};

export default function Settings() {
  const toast = useToast();
  const [prefs, setPrefs] = useState<Prefs>(defaultPrefs);
  const [models, setModels] = useState<ModelSettings | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");
  const [backendDown, setBackendDown] = useState(false);
  const [form, setForm] = useState({ id: "", name: "", base_url: "", model: "", api_key: "", model_family: "unknown", thinking: "", reasoning_effort: "" });
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [chatRole, setChatRole] = useState("");
  const [background, setBackground] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [managementRevision, setManagementRevision] = useState(0);

  const load = () => {
    get<ModelSettings>("/model-settings").then((m) => { const all = m.role_assignments ?? {}; setAssignments(Object.fromEntries(Object.entries(all).filter(([role]) => role !== "chat"))); setChatRole(all.chat ?? ""); setModels(m); setBackground(m.config.user_profile ?? ""); setBackendDown(false); }).catch((e) => { setBackendDown(true); setError(explain(e)); });
    api.capabilities().then(setCaps).catch(() => {});
  };
  useEffect(() => {
    const t = setTimeout(() => { setPrefs(readPrefs()); load(); }, 0);
    return () => clearTimeout(t);
  }, []);

  const update = (p: Prefs) => { setPrefs(p); savePrefs(p); };
  async function writeConfig(action: string, value: Record<string, unknown>, message: string) {
    if (!models || pending || busy) return false;
    const key = newKey(); setPending(key); setBusy(true); setError("");
    try {
      await act("PUT", "/model-settings", { expected_config_hash: models.config_hash, action, value }, { kind: "model_settings", key });
      setPending(null); toast(message); load(); return true;
    } catch (err) { if (!(err instanceof UnknownOutcomeError)) setPending(null); setError(explain(err)); return false; }
    finally { setBusy(false); }
  }

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!models) return;
    try {
      const value: Record<string, unknown> = { id: form.id.trim(), name: form.name.trim(), base_url: form.base_url.trim(), model: form.model.trim(), model_family: form.model_family.trim(), thinking: form.thinking || null, reasoning_effort: form.reasoning_effort || null };
      if (form.api_key) value.api_key = form.api_key;
      await writeConfig("profile", value, "模型配置已保存");
      setForm({ id: "", name: "", base_url: "", model: "", api_key: "", model_family: "unknown", thinking: "", reasoning_effort: "" }); // 密钥保存后立即清空，永不回显
    } catch (err) { setError(explain(err)); }
  }

  async function saveChatRole(profileId: string) {
    if (!models || pending || busy) return;
    setBusy(true); setError("");
    try {
      // v2 CAS 写入：action=roles 局部合并，chat:null 表示清除；不触碰其它角色选择。
      await act("PUT", "/model-settings", {
        expected_config_hash: models.config_hash,
        action: "roles",
        value: { chat: profileId || null },
      }, { kind: "model_settings", key: newKey() });
      toast("聊天模型已保存"); load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "聊天模型保存失败。");
    } finally { setBusy(false); }
  }

  async function select(id: string | null) {
    if (!models) return;
    await writeConfig("select", { profile_id: id }, "已切换");
  }

  return (
    <>
      <h1 className="title" style={{ marginBottom: 20 }}>设置</h1>
      {backendDown && <div className="banner banner-danger" role="alert" style={{ marginBottom: 16 }}><div><b>无法连接后端。</b> 外观设置仍然可以修改。{error}</div></div>}

      <section className="panel panel-pad" style={{ display: "grid", gap: 16, marginBottom: 24 }} aria-labelledby="s-look">
        <h2 className="sub" id="s-look">外观与阅读</h2>
        <div className="field">
          <span className="label">主题</span>
          <div className="seg" role="group" aria-label="主题">
            {([["system", "跟随系统"], ["light", "浅色"], ["dark", "深色"]] as const).map(([k, l]) => <button key={k} aria-pressed={prefs.theme === k} onClick={() => update({ ...prefs, theme: k })}>{l}</button>)}
          </div>
        </div>
        <div className="field">
          <span className="label">阅读字体</span>
          <div className="seg" role="group" aria-label="阅读字体">
            <button aria-pressed={prefs.serif} onClick={() => update({ ...prefs, serif: true })}>衬线（宋体系）</button>
            <button aria-pressed={!prefs.serif} onClick={() => update({ ...prefs, serif: false })}>无衬线</button>
          </div>
        </div>
        <div className="field">
          <label htmlFor="size">正文字号：{prefs.size}px</label>
          <input id="size" type="range" min={14} max={22} step={1} value={prefs.size} onChange={(e) => update({ ...prefs, size: Number(e.target.value) })} />
        </div>
        <div className={prefs.serif ? "serif" : ""} style={{ borderLeft: "3px solid var(--primary)", paddingLeft: 12, fontSize: prefs.size, fontFamily: prefs.serif ? "var(--font-serif)" : "var(--font-ui)", lineHeight: prefs.serif ? 1.95 : 1.85 }}>
          把知识用于实践，让每一次学习都有收获。
        </div>
        <div><button className="btn btn-sm" onClick={() => update(defaultPrefs)}>恢复默认外观</button></div>
      </section>

      <section className="panel panel-pad" style={{ display: "grid", gap: 12, marginBottom: 24 }} aria-labelledby="s-cap">
        <h2 className="sub" id="s-cap">当前能力</h2>
        {!caps ? <p className="muted">未读取到。</p> : (
          <dl className="kv" style={{ gridTemplateColumns: "auto 1fr" }}>
            <dt>真实模型调用</dt><dd style={{ textAlign: "left" }}>{caps.paid_calls_enabled ? "后端已允许" : "后端未允许（只能演示）"}</dd>
            <dt>检查器校准</dt><dd style={{ textAlign: "left" }}>{caps.calibration_status === "not_calibrated" ? "未校准" : caps.calibration_status}</dd>
            <dt>PowerShell 5.1 执行</dt><dd style={{ textAlign: "left" }}>{caps.ps51_sandbox === "not_run" ? "没有经验证的隔离执行器，不会执行宿主脚本" : caps.ps51_sandbox}</dd>
            <dt>写入 Obsidian</dt><dd style={{ textAlign: "left" }}>{caps.obsidian_delivery ? "可用" : "未启用。请下载文件后手动导入"}</dd>
          </dl>
        )}
      </section>

      <details className="panel panel-pad disclosure" open>
        <summary>模型与连接（高级）</summary>
        <div style={{ display: "grid", gap: 16, marginTop: 12 }}>
          {models && (
            <>
              <form className="asset-editor" onSubmit={async (e) => { e.preventDefault(); await writeConfig("user_profile", { content: background }, "背景资料已保存"); }}>
                <label htmlFor="user-background">我的背景资料（明确自述）</label><textarea id="user-background" value={background} maxLength={4000} onChange={(e) => setBackground(e.target.value)} />
                <p className="hint">背景资料不是掌握程度测量；具体教学偏好和困惑请在阅读页反馈。</p><button className="btn btn-sm" disabled={busy || Boolean(pending)}>保存背景资料</button>
              </form>
              <div>
                <h3 className="sub" style={{ marginBottom: 6 }}>角色</h3>
                <ul>
                  {Object.entries(models.roles).map(([r, v]) => (
                    <li key={r} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                      <span>{ROLE_TEXT[r] ?? r}</span>
                      <span className="muted">{v.configured ? `${v.model}（${v.model_family}）` : "未配置"}</span>
                    </li>
                  ))}
                </ul>
                <p className="hint" style={{ marginTop: 6 }}>同族模型可在隔离上下文中复核，但不具备跨模型族独立验证资格；不会自动替换你的选择。</p>
                <form className="asset-editor" onSubmit={async (e) => { e.preventDefault(); await saveChatRole(chatRole); }}>
                  <div className="field">
                    <label htmlFor="role-chat">阅读页聊天</label>
                    <select id="role-chat" value={chatRole} onChange={(e) => setChatRole(e.target.value)}>
                      <option value="">默认主生成模型（deepseek-generation）</option>
                      {(models.role_profiles ?? models.config.profiles).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.model} · {p.model_family}</option>)}
                    </select>
                  </div>
                  <p className="hint">只修改聊天角色，保留所选配置的完整模型参数；不显示或更改密钥。</p>
                  <button className="btn btn-sm" disabled={busy || Boolean(pending)}>保存聊天模型</button>
                </form>
                <form className="asset-editor" onSubmit={async (e) => { e.preventDefault(); await writeConfig("roles", assignments, "角色分配已保存"); }}>
                  {Object.entries(ROLE_TEXT).map(([role,label]) => <div key={role}><label htmlFor={`role-${role}`}>{label}</label><select id={`role-${role}`} value={assignments[role] ?? ""} onChange={(e) => { const next = { ...assignments }; if (e.target.value) next[role] = e.target.value; else delete next[role]; setAssignments(next); }}><option value="">沿用私有角色配置或保留缺口</option>{(models.role_profiles ?? models.config.profiles).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.model} · {p.model_family}</option>)}</select></div>)}
                  <button className="btn btn-sm" disabled={busy || Boolean(pending)}>保存角色分配</button>
                </form>
              </div>
              <div>
                <h3 className="sub" style={{ marginBottom: 6 }}>已保存的配置</h3>
                {models.config.profiles.length === 0 ? <p className="muted">还没有配置。</p> : (
                  <ul>
                    {models.config.profiles.map((p) => (
                      <li key={p.id} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)", alignItems: "center", flexWrap: "wrap" }}>
                        <span><b>{p.name}</b> <span className="muted">{p.model} · {p.has_key ? "已保存密钥" : "没有密钥"}</span></span>
                        <div className="row-actions"><button className="btn btn-sm" disabled={busy || Boolean(pending)} aria-pressed={models.config.active_id === p.id} onClick={() => void select(p.id)}>{models.config.active_id === p.id ? "使用中" : "使用"}</button><ResourceActions kind="model-profiles" id={p.id} label={p.name} onChanged={() => { load(); setManagementRevision((v) => v + 1); }} /></div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <form onSubmit={saveProfile} style={{ display: "grid", gap: 12 }}>
                <h3 className="sub">添加或更新配置</h3>
                <div className="field"><label htmlFor="mid">标识（字母、数字、-、_）</label><input id="mid" type="text" required pattern="[a-zA-Z0-9_\-]{1,80}" value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} /></div>
                <div className="field"><label htmlFor="mname">名称</label><input id="mname" type="text" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
                <div className="field"><label htmlFor="murl">接口地址</label><input id="murl" type="text" required value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></div>
                <div className="field"><label htmlFor="mmodel">模型</label><input id="mmodel" type="text" required value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} /></div>
                <div className="field"><label htmlFor="mfamily">模型族（用于独立性检查）</label><input id="mfamily" value={form.model_family} onChange={(e) => setForm({ ...form, model_family: e.target.value })} /></div>
                <div className="field"><label htmlFor="mthinking">思考模式</label><select id="mthinking" value={form.thinking} onChange={(e) => setForm({ ...form, thinking: e.target.value })}><option value="">模型默认</option><option value="enabled">启用</option><option value="disabled">关闭</option></select></div>
                <div className="field"><label htmlFor="meffort">推理强度</label><select id="meffort" value={form.reasoning_effort} onChange={(e) => setForm({ ...form, reasoning_effort: e.target.value })}><option value="">模型默认</option>{["low", "high", "max"].map((v) => <option key={v}>{v}</option>)}</select></div>
                <div className="field">
                  <label htmlFor="mkey">密钥</label>
                  <input id="mkey" type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="留空则沿用已保存的密钥" />
                  <p className="hint">密钥只发送给后端保存，保存后不会再显示，也不会出现在导出里。</p>
                </div>
                <div><button className="btn btn-primary" type="submit" disabled={busy || Boolean(pending)} aria-busy={busy}>保存配置</button></div>
              </form>
              {error && !backendDown && <p className="err" role="alert">{error}</p>}
              {pending && !busy && <div role="status"><p>配置操作结果尚未确认；不会重复提交。</p><button className="btn" onClick={() => void getReceipt(pending).then((r) => { if (r.status === "completed") { setPending(null); setError(""); toast("配置回执已确认"); load(); } else setError("原操作仍未知，请继续查询回执。"); }).catch((e) => setError(explain(e)))}>查询配置回执</button></div>}
            </>
          )}
        </div>
      </details>
      <details className="panel panel-pad"><summary>模型回收站与恢复</summary><ResourceManager key={managementRevision} initialKind="model-profiles" initialView="trashed" onChanged={load} /></details>
    </>
  );
}



