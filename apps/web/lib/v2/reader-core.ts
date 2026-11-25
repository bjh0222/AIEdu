// 阅读器纯函数：来源保真的切分、标题锚点、问题位置校验。无 DOM，可被单元测试直接调用。
import { sha256Hex } from "./client";

export type Heading = { level: number; text: string; id: string; line: number };

/** 与后端 slug 无关：仅用于页内导航。重名标题以 -2、-3 消歧。 */
export function slugify(text: string): string {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/[\s]+/g, "-")
    .replace(/[^\p{L}\p{N}\-_]/gu, "")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return base || "section";
}

/** 围栏感知地扫描标题（不把代码块里的 # 当标题）。返回的 id 在整篇内唯一。 */
export function extractHeadings(source: string): Heading[] {
  const out: Heading[] = [];
  const seen = new Map<string, number>();
  let fence: string | null = null;
  const lines = source.split("\n");
  lines.forEach((raw, index) => {
    const line = raw.replace(/\r$/, "");
    const f = /^\s*(`{3,}|~{3,})/.exec(line);
    if (f) {
      if (fence === null) fence = f[1];
      else if (f[1][0] === fence[0] && f[1].length >= fence.length) fence = null;
      return;
    }
    if (fence !== null) return;
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) return;
    const text = m[2].replace(/[*_`~=]/g, "").trim();
    const base = slugify(text);
    const n = (seen.get(base) ?? 0) + 1;
    seen.set(base, n);
    out.push({ level: m[1].length, text, id: n === 1 ? base : `${base}-${n}`, line: index });
  });
  return out;
}

/** 在“# 参考答案”处把正文切成题面/答案两段（与后端 render_items 的约定一致）。无此标题则不切分。 */
export function splitAnswers(source: string): { body: string; answers: string | null } {
  let fence: string | null = null;
  const lines = source.split("\n");
  let offset = 0;
  for (const raw of lines) {
    const line = raw.replace(/\r$/, "");
    const f = /^\s*(`{3,}|~{3,})/.exec(line);
    if (f) {
      if (fence === null) fence = f[1];
      else if (f[1][0] === fence[0] && f[1].length >= fence.length) fence = null;
    } else if (fence === null && /^#\s+参考答案\s*$/.test(line)) {
      return { body: source.slice(0, offset), answers: source.slice(offset) };
    }
    offset += raw.length + 1;
  }
  return { body: source, answers: null };
}

const FENCE_OR_CODE = /(^\s*`{3,}[^\n]*\n[\s\S]*?^\s*`{3,}\s*$|^\s*~{3,}[^\n]*\n[\s\S]*?^\s*~{3,}\s*$|`[^`\n]+`)/m;
const WIKI = /!?\[\[([^\]\n]+)\]\]/g;

export type Transformed = { text: string; map: (origOffset: number) => number };

/** Render-only whitespace projection for multiline $$...$$ with attached TeX.
 * CommonMark math flow requires a closing delimiter on its own line. Without
 * this projection a valid matrix ending `\\end{vmatrix}.$$` swallows later prose.
 * Never edits stored bytes; offset mapping keeps original evidence authoritative.
 */
export function projectDisplayMath(source: string): Transformed {
  const insertions = new Set<number>();
  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const escaped = (at: number) => {
    let slashes = 0;
    while (at > 0 && source[--at] === "\\") slashes++;
    return slashes % 2 === 1;
  };
  const delimiter = (at: number) => source.slice(at, at + 2) === "$$" &&
    source[at - 1] !== "$" && source[at + 2] !== "$" && !escaped(at);
  let at = 0;
  while (at < source.length) {
    if (at === 0 || source[at - 1] === "\n") {
      const lineEnd = source.indexOf("\n", at);
      const fence = /^ {0,3}(`{3,}|~{3,})/.exec(source.slice(at, lineEnd < 0 ? undefined : lineEnd));
      if (fence) {
        const close = new RegExp("^ {0,3}" + fence[1][0] + "{" + fence[1].length + ",}[ \\t]*\\r?$");
        at = lineEnd < 0 ? source.length : lineEnd + 1;
        while (at < source.length) {
          const end = source.indexOf("\n", at);
          const line = source.slice(at, end < 0 ? undefined : end);
          at = end < 0 ? source.length : end + 1;
          if (close.test(line)) break;
        }
        continue;
      }
    }
    if (source[at] === "`" && !escaped(at)) {
      const run = /^`+/.exec(source.slice(at))![0];
      let end = source.indexOf(run, at + run.length);
      while (end >= 0 && (source[end - 1] === "`" || source[end + run.length] === "`")) end = source.indexOf(run, end + run.length);
      at = end < 0 ? at + run.length : end + run.length;
      continue;
    }
    if (!delimiter(at)) { at++; continue; }
    let end = source.indexOf("$$", at + 2);
    while (end >= 0 && !delimiter(end)) end = source.indexOf("$$", end + 2);
    if (end < 0) break; // Do not invent a closing delimiter for malformed input.
    const content = source.slice(at + 2, end);
    // A code fence is a boundary, never a source of a far-away math closer.
    if (content.includes("\n") && !/^ {0,3}(?:`{3,}|~{3,})/m.test(content)) {
      if (source.slice(source.lastIndexOf("\n", at - 1) + 1, at).trim()) insertions.add(at);
      if (!/[\r\n]/.test(source[at + 2] ?? "")) insertions.add(at + 2);
      if (source[end - 1] !== "\n") insertions.add(end);
      if (end + 2 < source.length && !/[\r\n]/.test(source[end + 2])) insertions.add(end + 2);
    }
    at = end + 2;
  }
  const offsets = [...insertions].sort((a, b) => a - b);
  let text = "", start = 0;
  for (const offset of offsets) { text += source.slice(start, offset) + newline; start = offset; }
  text += source.slice(start);
  return { text, map: (offset: number) => offset + offsets.filter((at) => at <= offset).length * newline.length };
}

/** Render-only support for LaTeX \(...\) and \[...\] delimiters.
 *
 * Generated math should use $...$, but compatible models occasionally emit the
 * LaTeX forms. Convert them outside code spans so formulas and subscripts still
 * render. Stored bytes and evidence offsets remain unchanged; the returned map
 * projects original offsets through the render-only replacement.
 */
export function projectLatexDelimiters(source: string): Transformed {
  const parts = source.split(FENCE_OR_CODE);
  const edits: { start: number; end: number; len: number }[] = [];
  let original = 0;
  const escaped = (text: string, at: number) => {
    let slashes = 0;
    while (at > 0 && text[--at] === "\\") slashes++;
    return slashes % 2 === 1;
  };
  const pattern = /\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)/g;
  const rendered = parts.map((piece, index) => {
    const base = original;
    original += piece.length;
    if (index % 2) return piece;
    let cursor = 0;
    let output = "";
    for (const match of piece.matchAll(pattern)) {
      const at = match.index ?? 0;
      const isDisplay = match[1] !== undefined;
      const closingAt = at + match[0].length - 2;
      if (escaped(piece, at) || escaped(piece, closingAt)) continue;
      output += piece.slice(cursor, at);
      const body = isDisplay ? match[1] : match[2];
      const lead = isDisplay && !/^\r?\n/.test(body) ? "\n" : "";
      const trail = isDisplay && !/\r?\n$/.test(body) ? "\n" : "";
      const replacement = isDisplay ? `$$${lead}${body}${trail}$$` : `$${body}$`;
      output += replacement;
      edits.push({ start: base + at, end: base + at + match[0].length, len: replacement.length });
      cursor = at + match[0].length;
    }
    return output + piece.slice(cursor);
  }).join("");
  const map = (offset: number) => {
    let delta = 0;
    for (const edit of edits) {
      if (edit.end <= offset) delta += edit.len - (edit.end - edit.start);
      else if (edit.start < offset) return edit.start + delta;
      else break;
    }
    return offset + delta;
  };
  return { text: rendered, map };
}

/**
 * 把 `[[目标|别名]]` 转成内部链接占位；`targets` 是 unit_id/标题 → 锚点。无目标则降级为带标记的文本。
 * 返回 map：原文偏移 → 变换后偏移（问题标记用原文偏移定位，渲染前必须换算）。
 */
export function resolveWikiLinks(source: string, targets: Map<string, string>): Transformed {
  const parts = source.split(FENCE_OR_CODE);
  const edits: { start: number; end: number; len: number }[] = [];
  let origPos = 0;
  const out = parts.map((piece, i) => {
    const base = origPos;
    origPos += piece.length;
    if (i % 2) return piece;
    return piece.replace(WIKI, (m: string, inner: string, at: number) => {
      const [target, alias] = inner.split("|");
      const [page, heading] = target.split("#");
      const label = (alias || page || heading || "").trim().replace(/[[\]]/g, "");
      const anchor = targets.get(page.trim()) ?? (heading ? targets.get(heading.trim()) : undefined);
      const rep = anchor ? `[${label}](#wiki:${encodeURIComponent(anchor)})` : `[${label}](#wiki-missing:${encodeURIComponent(label)})`;
      edits.push({ start: base + at, end: base + at + m.length, len: rep.length });
      return rep;
    });
  });
  const map = (o: number) => {
    let delta = 0;
    for (const e of edits) {
      if (e.end <= o) delta += e.len - (e.end - e.start);
      else if (e.start < o) return e.start + delta; // 落在被替换的链接内：贴到链接起点
      else break;
    }
    return o + delta;
  };
  return { text: out.join(""), map };
}

// ---- 问题（finding）定位 ----
export type Location = {
  unit_id: string;
  version_id: string;
  unit_hash: string;
  start_byte: number;
  end_byte: number;
  span_hash: string;
};
export type PinResult =
  | { ok: true; start: number; end: number; quote: string }
  | { ok: false; reason: "version" | "unit_hash" | "range" | "span_hash" };

/**
 * UTF-8 字节区间 → UTF-16 下标。逐步校验：版本、unit hash、区间合法、span hash。
 * 任一不符就拒绝（调用方不得渲染标记，也不得据此授权修复）。
 */
export async function locateFinding(body: string, unitHash: string, versionId: string, loc: Location): Promise<PinResult> {
  if (loc.version_id !== versionId) return { ok: false, reason: "version" };
  const bytes = new TextEncoder().encode(body);
  if ((await sha256Hex(bytes)) !== unitHash || loc.unit_hash !== unitHash) return { ok: false, reason: "unit_hash" };
  if (!Number.isInteger(loc.start_byte) || !Number.isInteger(loc.end_byte) || loc.start_byte < 0 || loc.end_byte > bytes.length || loc.start_byte >= loc.end_byte) {
    return { ok: false, reason: "range" };
  }
  const span = bytes.slice(loc.start_byte, loc.end_byte);
  if ((await sha256Hex(span)) !== loc.span_hash) return { ok: false, reason: "span_hash" };
  // 区间必须落在字符边界上，否则解码会产生替换字符：按“位置无效”处理。
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    const before = decoder.decode(bytes.slice(0, loc.start_byte));
    const quote = decoder.decode(span);
    return { ok: true, start: before.length, end: before.length + quote.length, quote };
  } catch {
    return { ok: false, reason: "range" };
  }
}

export const PIN_REASON: Record<string, string> = {
  version: "这个问题属于另一个版本",
  unit_hash: "正文已经变化，位置已过期",
  range: "位置区间无效",
  span_hash: "该位置的原文与检查时不一致",
};

// ---- 简单的行级对比（LCS），用于版本对比 ----
export type DiffRow = { kind: "same" | "add" | "del"; text: string };
export function diffLines(a: string, b: string, cap = 4000): DiffRow[] | null {
  const A = a.split("\n");
  const B = b.split("\n");
  if (A.length > cap || B.length > cap) return null;
  const dp: number[][] = Array.from({ length: A.length + 1 }, () => new Array(B.length + 1).fill(0));
  for (let i = A.length - 1; i >= 0; i--) for (let j = B.length - 1; j >= 0; j--) dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) {
      rows.push({ kind: "same", text: A[i++] });
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) rows.push({ kind: "del", text: A[i++] });
    else rows.push({ kind: "add", text: B[j++] });
  }
  while (i < A.length) rows.push({ kind: "del", text: A[i++] });
  while (j < B.length) rows.push({ kind: "add", text: B[j++] });
  return rows;
}
