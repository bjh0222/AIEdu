"use client";

import { useEffect, useState } from "react";
import { act, explain, get, getReceipt, newKey, post, UnknownOutcomeError } from "@/lib/v2/client";
import { defaultPrefs, readPrefs, savePrefs, type Prefs } from "@/lib/prefs";
import { useToast } from "@/components/ui/Toast";
import ResourceActions from "@/components/library/ResourceActions";
import ResourceManager from "@/components/library/ResourceManager";

type Profile = { id: string; name: string; model: string; base_url: string; has_key: boolean; thinking?: string | null; reasoning_effort?: string | null; model_family?: string; output_limit_tokens?: number | null };
type ModelSettings = {
  config: { active_id: string | null; user_profile: string; profiles: Profile[]; max_concurrency?: number; delivery_policy?: { auto_waiver_enabled: boolean; source: string } };
  config_hash: string;
  roles: Record<string, { configured: boolean; model?: string; model_family?: string; reason?: string }>;
  role_assignments?: Record<string, string>;
  role_profiles?: { id: string; name: string; model?: string; model_family?: string }[];
};
const ROLE_TEXT: Record<string, string> = {
  generation: "生成", review: "独立检查", blind_solver: "盲解", secondary_review: "二次复核", benchmark_panel: "基准评审面板", benchmark_refiner: "基准量规精炼",
};

type ProfileForm = { name: string; base_url: string; model: string; api_key: string; model_family: string; thinking: string; reasoning_effort: string; output_limit_tokens: string; reuse_id: string };
const EMPTY_PROFILE: ProfileForm = { name: "", base_url: "", model: "", api_key: "", model_family: "unknown", thinking: "", reasoning_effort: "", output_limit_tokens: "", reuse_id: "" };

function profileId(form: ProfileForm): string {
  const source = (form.model || form.name || "profile").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "profile";
  return `${source}-${crypto.randomUUID().slice(0, 6)}`;
}

export default function Settings() {
  const toast = useToast();
  const [prefs, setPrefs] = useState<Prefs>(defaultPrefs);
  const [models, setModels] = useState<ModelSettings | null>(null);
  const [error, setError] = useState("");
  const [backendDown, setBackendDown] = useState(false);
  const [form, setForm] = useState<ProfileForm>(EMPTY_PROFILE);
  const [fetchedModels, setFetchedModels] = useState<string[] | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [chatRole, setChatRole] = useState("");
  const [background, setBackground] = useState("");
  const [maxConcurrency, setMaxConcurrency] = useState(5);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [managementRevision, setManagementRevision] = useState(0);

  const load = () => {
    get<ModelSettings>("/model-settings").then((m) => { const all = m.role_assignments ?? {}; setAssignments(Object.fromEntries(Object.entries(all).filter(([role]) => role !== "chat"))); setChatRole(all.chat ?? ""); setModels(m); setBackground(m.config.user_profile ?? ""); setMaxConcurrency(m.config.max_concurrency ?? 5); setBackendDown(false); }).catch((e) => { setBackendDown(true); setError(explain(e)); });
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

  async function discoverModels() {
    if (!form.base_url.trim()) { setError("请先填写接口地址，再获取模型列表。"); return; }
    setDiscovering(true); setError("");
    try {
      const r = await post<{ models: string[] }>("/model-settings/discover", {
        base_url: form.base_url.trim(), api_key: form.api_key || null, profile_id: form.reuse_id || null,
      });
      setFetchedModels(r.models);
      toast(`获取到 ${r.models.length} 个模型`);
    } catch (err) { setFetchedModels(null); setError(explain(err)); }
    finally { setDiscovering(false); }
  }

  async function saveProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!models) return;
    try {
      const extraOutput = form.output_limit_tokens.trim() ? Number(form.output_limit_tokens) : null;
      if (extraOutput !== null && (!Number.isSafeInteger(extraOutput) || extraOutput <= 0)) throw new Error("额外输出上限必须是正整数，或留空使用模型容量。");
      const value: Record<string, unknown> = { id: profileId(form), name: (form.name || form.model).trim(), base_url: form.base_url.trim(), model: form.model.trim(), model_family: form.model_family.trim() || "unknown", thinking: form.thinking || null, reasoning_effort: form.reasoning_effort || null, output_limit_tokens: extraOutput };
      if (form.api_key) value.api_key = form.api_key;
      const ok = await writeConfig("profile", value, "模型配置已保存");
      if (ok) { setForm(EMPTY_PROFILE); setFetchedModels(null); } // 密钥保存后立即清空，永不回显
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

  async function saveConcurrency() {
    const value = Number(maxConcurrency);
    if (!Number.isSafeInteger(value) || value < 1 || value > 5) {
      setError("最多并发数必须是 1 到 5 的整数。");
      return;
    }
    await writeConfig("concurrency", { max_concurrency: value }, "并发设置已保存");
  }

  async function select(id: string | null) {
    if (!models) return;
    await writeConfig("select", { profile_id: id }, "已切换");
  }

  return (
    <>
      <h1 className="title" style={{ marginBottom: 20 }}>设置</h1>
      {backendDown && <div className="banner banner-danger" role="alert" style={{ marginBottom: 16 }}><div><b>无法连接后端。</b> 外观设置仍然可以修改。{error}</div></div>}

      <div className="settings-grid">
        {/* 右栏：模型与连接 */}
        <section className="panel panel-pad settings-model" style={{ display: "grid", gap: 16 }} aria-labelledby="s-model">
          <h2 className="sub" id="s-model">模型与连接</h2>
          {!models ? (
            <p className="muted">{backendDown ? "后端不可用时无法读取模型配置。" : "正在读取模型配置…"}</p>
          ) : (
            <>
              <div>
                <h3 className="sub" style={{ marginBottom: 8 }}>已保存的配置</h3>
                {models.config.profiles.length === 0 ? <p className="muted">还没有配置。先在下方添加一个供应商。</p> : (
                  <ul className="profile-list">
                    {models.config.profiles.map((p) => (
                      <li key={p.id} className={`profile-card${models.config.active_id === p.id ? " is-active" : ""}`}>
                        <div className="profile-head">
                          <b>{p.name}</b>
                          {models.config.active_id === p.id && <span className="profile-badge">使用中</span>}
                        </div>
                        <p className="mono profile-meta">{p.model}</p>
                        <p className="muted" style={{ fontSize: 12 }}>{p.model_family || "unknown"} · {p.has_key ? "已保存密钥" : "没有密钥"}{p.output_limit_tokens ? ` · 额外输出上限 ${p.output_limit_tokens}` : ""}</p>
                        <div className="row-actions">
                          <button className="btn btn-sm" disabled={busy || Boolean(pending)} aria-pressed={models.config.active_id === p.id} onClick={() => void select(p.id)}>{models.config.active_id === p.id ? "使用中" : "使用"}</button>
                          <ResourceActions kind="model-profiles" id={p.id} label={p.name} onChanged={() => { load(); setManagementRevision((v) => v + 1); }} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <form onSubmit={saveProfile} style={{ display: "grid", gap: 12, borderTop: "1px solid var(--line)", paddingTop: 16 }}>
                <h3 className="sub">添加供应商配置</h3>
                <div className="field">
                  <label htmlFor="murl">接口地址（Base URL）</label>
                  <input id="murl" type="text" required placeholder="例如 https://api.deepseek.com" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="mkey">密钥</label>
                  <input id="mkey" type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} placeholder="留空则沿用下方所选配置的密钥" />
                  <p className="hint">密钥只发送给后端保存，保存后不会再显示，也不会出现在导出里。</p>
                </div>
                <div className="field">
                  <label htmlFor="mreuse">复用已保存密钥（可选）</label>
                  <select id="mreuse" value={form.reuse_id} onChange={(e) => setForm({ ...form, reuse_id: e.target.value })}>
                    <option value="">不复用，使用上面输入的密钥</option>
                    {models.config.profiles.filter((p) => p.has_key).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.model}</option>)}
                  </select>
                  <p className="hint">所选配置的接口地址必须与上面一致，后端才会复用其密钥。</p>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button className="btn" type="button" disabled={discovering || busy || Boolean(pending)} aria-busy={discovering} onClick={() => void discoverModels()}>获取模型列表</button>
                  {fetchedModels && <span className="hint num">已获取 {fetchedModels.length} 个模型</span>}
                </div>
                <div className="field">
                  <label htmlFor="mmodel">模型</label>
                  <input id="mmodel" type="text" required list="model-list" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="从列表选择，或手动输入模型 ID" />
                  <datalist id="model-list">
                    {(fetchedModels ?? []).map((m) => <option key={m} value={m} />)}
                  </datalist>
                </div>
                <div className="field">
                  <label htmlFor="mname">名称</label>
                  <input id="mname" type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="留空则使用模型 ID" />
                </div>
                <div className="field">
                  <label htmlFor="mfamily">模型族（用于独立性检查）</label>
                  <input id="mfamily" value={form.model_family} onChange={(e) => setForm({ ...form, model_family: e.target.value })} />
                </div>
                <div className="field">
                  <label htmlFor="mthinking">思考模式</label>
                  <select id="mthinking" value={form.thinking} onChange={(e) => setForm({ ...form, thinking: e.target.value })}><option value="">模型默认</option><option value="enabled">启用</option><option value="disabled">关闭</option></select>
                </div>
                <div className="field">
                  <label htmlFor="meffort">推理强度</label>
                  <select id="meffort" value={form.reasoning_effort} onChange={(e) => setForm({ ...form, reasoning_effort: e.target.value })}><option value="">模型默认</option><option value="low">低（low）</option><option value="high">高（high）</option><option value="max">最大（max）</option></select>
                </div>
                <div className="field">
                  <label htmlFor="moutput">额外输出上限（tokens）</label>
                  <input id="moutput" type="number" min={1} step={1} value={form.output_limit_tokens} onChange={(e) => setForm({ ...form, output_limit_tokens: e.target.value })} placeholder="留空：无额外限制（仍受模型容量约束）" />
                  <p className="hint">留空不会套用应用内置的 8k/16k/32k/64k 上限；系统按已登记模型容量派发。填写值不得超过登记容量，容量未知时拒绝调用。</p>
                </div>
                <div><button className="btn btn-primary" type="submit" disabled={busy || Boolean(pending)} aria-busy={busy}>保存配置</button></div>
              </form>

              <div style={{ borderTop: "1px solid var(--line)", paddingTop: 16 }}>
                <h3 className="sub" style={{ marginBottom: 6 }}>接入 AI Edu 任务</h3>
                <ul>
                  {Object.entries(models.roles).map(([r, v]) => (
                    <li key={r} style={{ display: "flex", justifyContent: "space-between", gap: 8, padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                      <span>{ROLE_TEXT[r] ?? r}</span>
                      <span className="muted">{v.configured ? `${v.model}（${v.model_family}）` : "未配置"}</span>
                    </li>
                  ))}
                </ul>
                <p className="hint" style={{ marginTop: 6 }}>同族模型可在隔离上下文中复核，但不具备跨模型族独立验证资格；不会自动替换你的选择。</p>
                <form className="asset-editor" onSubmit={async (e) => { e.preventDefault(); await writeConfig("roles", assignments, "角色分配已保存"); }} style={{ marginTop: 12 }}>
                  {Object.entries(ROLE_TEXT).map(([role, label]) => <div key={role}><label htmlFor={`role-${role}`}>{label}</label><select id={`role-${role}`} value={assignments[role] ?? ""} onChange={(e) => { const next = { ...assignments }; if (e.target.value) next[role] = e.target.value; else delete next[role]; setAssignments(next); }}><option value="">沿用私有角色配置或保留缺口</option>{(models.role_profiles ?? models.config.profiles).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.model} · {p.model_family}</option>)}</select></div>)}
                  <button className="btn btn-sm" disabled={busy || Boolean(pending)}>保存角色分配</button>
                </form>
                <form className="asset-editor" onSubmit={async (e) => { e.preventDefault(); await saveChatRole(chatRole); }} style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
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
                <form onSubmit={(e) => { e.preventDefault(); void saveConcurrency(); }} style={{ marginTop: 12, borderTop: "1px solid var(--line)", paddingTop: 12, display: "grid", gap: 8 }}>
                  <div className="field">
                    <label htmlFor="max-concurrency">最多并发数（1–5）</label>
                    <input id="max-concurrency" type="number" min={1} max={5} step={1} value={maxConcurrency} onChange={(e) => setMaxConcurrency(Number(e.target.value))} />
                  </div>
                  <p className="hint">同时用于章节生成和可并行的独立质检。数值越高越快，但会同时占用更多模型接口额度；默认 5。</p>
                  <div><button className="btn btn-sm" disabled={busy || Boolean(pending)}>保存并发设置</button></div>
                </form>
              </div>
            </>
          )}
          {error && !backendDown && <p className="err" role="alert">{error}</p>}
          {pending && !busy && <div role="status"><p>配置操作结果尚未确认；不会重复提交。</p><button className="btn" onClick={() => void getReceipt(pending).then((r) => { if (r.status === "completed") { setPending(null); setError(""); toast("配置回执已确认"); load(); } else setError("原操作仍未知，请继续查询回执。"); }).catch((e) => setError(explain(e)))}>查询配置回执</button></div>}
        </section>

        {/* 左栏：其它设置 */}
        <div className="settings-side">
          <section className="panel panel-pad" style={{ display: "grid", gap: 16 }} aria-labelledby="s-look">
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

          {models && (
            <section className="panel panel-pad" style={{ display: "grid", gap: 12 }} aria-labelledby="s-bg">
              <h2 className="sub" id="s-bg">我的背景资料</h2>
              <form className="asset-editor" onSubmit={async (e) => { e.preventDefault(); await writeConfig("user_profile", { content: background }, "背景资料已保存"); }}>
                <label htmlFor="user-background">研究当前任务说明（明确自述）</label>
                <textarea id="user-background" value={background} maxLength={4000} onChange={(e) => setBackground(e.target.value)} />
                <p className="hint">背景资料不是掌握程度测量；具体教学偏好和困惑请在阅读页反馈。</p>
                <button className="btn btn-sm" disabled={busy || Boolean(pending)}>保存背景资料</button>
              </form>
            </section>
          )}

          {models && (
            <section className="panel panel-pad" style={{ display: "grid", gap: 12 }} aria-labelledby="s-delivery">
              <h2 className="sub" id="s-delivery">交付与放行</h2>
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={models.config.delivery_policy?.auto_waiver_enabled !== false}
                  disabled={busy || Boolean(pending) || models.config.delivery_policy?.source === "environment_disabled"}
                  onChange={(e) => void writeConfig("delivery_policy", { auto_waiver_enabled: e.target.checked }, e.target.checked ? "最终交付时会询问是否放行专业项" : "最终交付不会自动提示放行")}
                />
                签最终交付时提示专业项放行
              </label>
              <p className="hint">开启只表示允许在软阻塞时询问；只有你实际确认后才会生成授权记录。硬问题、结构、覆盖、材料和校准证书缺口永远不能放行。环境变量关闭时此开关只读。</p>
            </section>
          )}

          <details className="panel panel-pad"><summary>模型回收站与恢复</summary><ResourceManager key={managementRevision} initialKind="model-profiles" initialView="trashed" onChanged={load} /></details>
        </div>
      </div>
    </>
  );
}
