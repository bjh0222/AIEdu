// 后端没有“列出某产物全部版本”的接口（见 Visual_and_Reader_Expansion D-19）。
// 这里只在本机记住“已经读过的版本”：仅存 UUID、序号、时间，不存正文。
export type SeenVersion = { version_id: string; version: number; at: number };
const key = (artifactId: string) => `aiedu.seen.${artifactId}`;

export function seenVersions(artifactId: string): SeenVersion[] {
  try {
    const list = JSON.parse(localStorage.getItem(key(artifactId)) || "[]") as SeenVersion[];
    return list.sort((a, b) => b.version - a.version);
  } catch {
    return [];
  }
}
export function rememberVersion(artifactId: string, v: SeenVersion) {
  try {
    const list = seenVersions(artifactId).filter((x) => x.version_id !== v.version_id);
    localStorage.setItem(key(artifactId), JSON.stringify([...list, v].slice(-30)));
  } catch {
    /* 存储不可用：版本选择只显示当前版本 */
  }
}
