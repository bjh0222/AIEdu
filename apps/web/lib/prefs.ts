// 本机外观/阅读偏好（仅 localStorage；不含任何任务数据）。
export type Prefs = { theme: "light" | "dark" | "system"; serif: boolean; size: number };
export const defaultPrefs: Prefs = { theme: "system", serif: true, size: 17 };
export const PREFS_KEY = "aiedu.prefs.v2";

export function readPrefs(): Prefs {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    return {
      theme: ["light", "dark", "system"].includes(p.theme) ? p.theme : defaultPrefs.theme,
      serif: typeof p.serif === "boolean" ? p.serif : defaultPrefs.serif,
      size: Number.isInteger(p.size) && p.size >= 14 && p.size <= 22 ? p.size : defaultPrefs.size,
    };
  } catch {
    return defaultPrefs;
  }
}

export function applyPrefs(p: Prefs) {
  const root = document.documentElement;
  root.dataset.theme = p.theme === "system" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : p.theme;
  root.style.setProperty("--reading-size", `${p.size}px`);
  root.dataset.serif = p.serif ? "1" : "0";
}

export function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(p));
  } catch {
    /* 无法保存时本次仍生效 */
  }
  applyPrefs(p);
  window.dispatchEvent(new Event("aiedu-prefs"));
}

// 在首帧绘制前执行，避免深色模式刷新时闪白。
export const prefsBootstrap = `(()=>{try{var p=JSON.parse(localStorage.getItem('${PREFS_KEY}')||'{}');var r=document.documentElement;var m=['light','dark','system'].indexOf(p.theme)>=0?p.theme:'system';r.dataset.theme=m==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):m;r.dataset.serif=p.serif===false?'0':'1';if(Number.isInteger(p.size)&&p.size>=14&&p.size<=22)r.style.setProperty('--reading-size',p.size+'px')}catch(e){}})()`;
