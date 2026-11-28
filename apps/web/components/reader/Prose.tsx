"use client";

import { Children, isValidElement, useEffect, useId, useMemo, useRef, useState, type ComponentProps, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import katex from "katex";
import "katex/dist/katex.min.css";
import { extractHeadings, projectDisplayMath, projectLatexDelimiters, resolveWikiLinks, type Heading } from "@/lib/v2/reader-core";

export type Pin = { n: number; start: number; end: number; state: "confirmed" | "suspected" };

type MdNode = { type: string; value?: string; depth?: number; children?: MdNode[]; checked?: boolean; position?: { start: { offset?: number }; end: { offset?: number } }; data?: Record<string, unknown>; lang?: string };

// Legacy projections can prepend an answer label to an already labelled final.
// Run after source pins: only presentation changes, never the frozen offsets.
function remarkAnswerLabels() {
  return (tree: MdNode) => {
    let answers = false;
    for (const node of tree.children ?? []) {
      if (node.type === "heading" && (node.depth ?? 0) <= 3) {
        answers = node.children?.map((child) => child.value ?? "").join("") === "参考答案";
      }
      if (!answers || node.type !== "paragraph") continue;
      const ordinal = node.children?.[0];
      if (ordinal?.type !== "strong" || !/^\d+\.$/.test(ordinal.children?.map((child) => child.value ?? "").join("") ?? "")) continue;
      const leaves: MdNode[] = [];
      const collect = (child: MdNode): boolean => {
        if (child.type === "text") { leaves.push(child); return true; }
        if (child.type !== "emphasis" && child.type !== "strong") return false;
        return (child.children ?? []).every(collect);
      };
      // Source pins may wrap either label or split it between text nodes. Keep
      // those wrappers and original positions; remove only the redundant label.
      for (const child of node.children?.slice(1) ?? []) if (!collect(child)) break;
      const text = leaves.map((leaf) => leaf.value ?? "").join("");
      const match = /^(\s*答案\s*[:：]\s*)答案\s*[:：]\s*/.exec(text);
      if (!match) continue;
      let offset = 0;
      for (const leaf of leaves) {
        const value = leaf.value ?? "";
        const start = Math.max(0, match[1].length - offset);
        const end = Math.min(value.length, match[0].length - offset);
        if (start < end) leaf.value = value.slice(0, start) + value.slice(end);
        offset += value.length;
      }
    }
  };
}

// Answer entries also begin with **n.**, so mark question paragraphs only up
// to the answer heading. This keeps the three-line reading gap between prompts
// without adding the same gap between answer explanations.
function remarkQuestionBlocks() {
  return (tree: MdNode) => {
    let inAnswers = false;
    let inQuestion = false;
    for (const node of tree.children ?? []) {
      if (node.type === "heading") {
        const heading = node.children?.map((child) => child.value ?? "").join("").trim();
        inAnswers ||= heading === "参考答案";
        inQuestion = false;
      }
      if (inAnswers) continue;
      if (node.type !== "paragraph") continue;
      const ordinal = node.children?.[0];
      const startsQuestion = ordinal?.type === "strong" &&
        /^\d+\.$/.test(ordinal.children?.map((child) => child.value ?? "").join("") ?? "");
      if (startsQuestion) inQuestion = true;
      if (!inQuestion) continue;
      const classes = ["question-block", ...(startsQuestion ? ["question-start"] : [])];
      node.data = {
        ...(node.data ?? {}),
        hProperties: { ...((node.data?.hProperties as object) ?? {}), className: classes },
      };
    }
  };
}

// Older frozen exercises contain marker-only list items. GFM normally needs
// text after the marker; adapt the AST without changing source bytes/offsets.
function remarkEmptyTasks() {
  return (tree: MdNode) => {
    const visit = (node: MdNode) => {
      const paragraph = node.children?.[0];
      const text = paragraph?.children?.[0];
      const match = node.type === "listItem" && paragraph?.type === "paragraph" && paragraph.children?.length === 1 &&
        text?.type === "text" ? /^\[([ xX])\]$/.exec(text.value ?? "") : null;
      if (match && text) { node.checked = match[1].toLowerCase() === "x"; text.value = ""; }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}

function ReadingCheckbox({ initialChecked }: { initialChecked: boolean }) {
  const [checked, setChecked] = useState(initialChecked);
  return <input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} aria-label="本页阅读勾选" title="本页阅读勾选" />;
}

// ---- remark 插件：==高亮==（只作用于文本节点，围栏/行内代码内的 == 不受影响）----
function remarkMark() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (!node.children || node.type === "code" || node.type === "inlineCode" || node.type === "math" || node.type === "inlineMath") return;
      const next: MdNode[] = [];
      for (const child of node.children) {
        if (child.type === "text" && child.value && child.value.includes("==")) {
          const parts = child.value.split(/(==[^=\n]+?==)/);
          for (const p of parts) {
            if (/^==[^=\n]+==$/.test(p)) {
              next.push({ type: "emphasis", data: { hName: "mark" }, children: [{ type: "text", value: p.slice(2, -2) }] });
            } else if (p) next.push({ type: "text", value: p });
          }
        } else {
          walk(child);
          next.push(child);
        }
      }
      node.children = next;
    };
    walk(tree);
  };
}

// ---- remark 插件：> [!note] callout → aside[data-callout] ----
function remarkCallout() {
  return (tree: MdNode) => {
    const walk = (node: MdNode) => {
      if (node.type === "blockquote" && node.children?.[0]?.type === "paragraph") {
        const first = node.children[0].children?.[0];
        const m = first?.type === "text" && first.value ? /^\[!([A-Za-z]+)\][+-]?[ \t]*/.exec(first.value) : null;
        if (m && first) {
          first.value = first.value!.slice(m[0].length);
          if (!first.value) node.children[0].children!.shift();
          node.data = { hName: "aside", hProperties: { className: ["callout"], dataCallout: m[1].toLowerCase() } };
        }
      }
      node.children?.forEach(walk);
    };
    walk(tree);
  };
}

// ---- remark 插件：问题标记。先落到文本节点；落不进去（如整段代码）就标在所在顶层块上。----
function remarkPins(pins: Pin[]) {
  return () => (tree: MdNode) => {
    for (const pin of pins) {
      let anchored = false;
      const visit = (node: MdNode): boolean => {
        if (!node.children) return false;
        for (let i = 0; i < node.children.length; i++) {
          const child = node.children[i];
          if (child.type === "text" && child.value && child.position?.start.offset !== undefined) {
            const off = child.position.start.offset;
            if (pin.start >= off && pin.start < off + child.value.length && child.position.end.offset === off + child.value.length) {
              const a = pin.start - off;
              const b = Math.min(child.value.length, pin.end - off);
              const wrap: MdNode = {
                type: "emphasis",
                data: { hName: "span", hProperties: { className: ["pin"], dataPin: String(pin.n), dataState: pin.state } },
                children: [{ type: "text", value: child.value.slice(a, b) }],
              };
              const at = (from: number, to: number) => ({ start: { offset: off + from }, end: { offset: off + to } });
              wrap.position = at(a, b);
              const replacement: MdNode[] = [];
              if (a > 0) replacement.push({ type: "text", value: child.value.slice(0, a), position: at(0, a) });
              replacement.push(wrap);
              if (b < child.value.length) replacement.push({ type: "text", value: child.value.slice(b), position: at(b, child.value.length) });
              node.children.splice(i, 1, ...replacement);
              return true;
            }
          } else if (child.type !== "code" && child.type !== "inlineCode" && visit(child)) return true;
        }
        return false;
      };
      anchored = visit(tree);
      if (!anchored) {
        const block = tree.children?.find((c) => c.position?.start.offset !== undefined && c.position.end.offset !== undefined && pin.start >= c.position.start.offset! && pin.start < c.position.end.offset!);
        if (block) block.data = { ...(block.data ?? {}), hProperties: { ...((block.data?.hProperties as object) ?? {}), dataPinBlock: String(pin.n), dataState: pin.state } };
      }
    }
  };
}

const schema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "mark", "aside"],
  attributes: {
    ...defaultSchema.attributes,
    "*": [...(defaultSchema.attributes?.["*"] ?? []), "dataPin", "dataState", "dataPinBlock"],
    aside: ["className", "dataCallout"],
    p: [...(defaultSchema.attributes?.p ?? []), "className"],
    span: [...(defaultSchema.attributes?.span ?? []), "className", "dataPin", "dataState"],
    code: [...(defaultSchema.attributes?.code ?? []), ["className", /^language-[\w-]+$/, "math-inline", "math-display"]],
  },
};

const CALLOUT_LABEL: Record<string, string> = {
  note: "注释", info: "信息", tip: "提示", hint: "提示", important: "重要", warning: "警告", caution: "注意", danger: "危险", error: "错误",
  success: "成功", question: "问题", example: "示例", quote: "引用", abstract: "摘要", summary: "摘要", bug: "缺陷", todo: "待办",
};

function textOf(node: ReactNode): string {
  let out = "";
  Children.forEach(node, (c) => {
    if (typeof c === "string" || typeof c === "number") out += String(c);
    else if (isValidElement<{ children?: ReactNode }>(c)) out += textOf(c.props.children);
  });
  return out;
}

export function useCopy() {
  const [done, setDone] = useState<string | null>(null);
  const copy = async (text: string, id: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(id);
      setTimeout(() => setDone((d) => (d === id ? null : d)), 1800);
    } catch {
      setDone(`fail:${id}`);
    }
  };
  return { done, copy };
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const id = useId();
  const { done, copy } = useCopy();
  return (
    <button type="button" className="copy-btn" onClick={() => copy(text, id)} aria-label={label} data-copy-source>
      {done === id ? "已复制" : done === `fail:${id}` ? "无法复制，请手动选择" : "复制"}
    </button>
  );
}

let mermaidReady: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  if (!mermaidReady) {
    mermaidReady = import("mermaid").then((m) => {
      const dark = document.documentElement.dataset.theme === "dark";
      m.default.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral", fontFamily: "inherit" });
      return m.default;
    });
  }
  return mermaidReady;
}

function MermaidBlock({ source }: { source: string }) {
  const id = "mmd" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    loadMermaid()
      .then(async (mermaid) => {
        await mermaid.parse(source);
        const out = await mermaid.render(id, source);
        if (alive) setSvg(out.svg);
      })
      .catch((e: unknown) => {
        document.getElementById("d" + id)?.remove();
        if (alive) setError(e instanceof Error ? e.message.split("\n")[0] : "图表语法错误");
      });
    return () => {
      alive = false;
    };
  }, [id, source]);
  if (error) {
    return (
      <figure className="code-block mermaid-fallback">
        <figcaption className="code-head">
          <span>mermaid · 图表无法渲染：{error}</span>
          <CopyButton text={source} label="复制图表源代码" />
        </figcaption>
        <div className="code-scroll" tabIndex={0}>
          <pre><code>{source}</code></pre>
        </div>
      </figure>
    );
  }
  // SVG 由 Mermaid strict 模式生成（已净化），仅在此容器内注入。
  return svg ? <div className="mermaid-figure" role="img" aria-label="图表" dangerouslySetInnerHTML={{ __html: svg }} /> : <div className="mermaid-figure" aria-busy="true">图表加载中</div>;
}

function TexMath({ tex, display }: { tex: string; display: boolean }) {
  const id = useId();
  const { done, copy } = useCopy();
  const { html, failed } = useMemo(() => {
    try {
      return { html: katex.renderToString(tex, { displayMode: display, throwOnError: true, trust: false, strict: "ignore", output: "htmlAndMathml" }), failed: false };
    } catch {
      return { html: "", failed: true };
    }
  }, [tex, display]);
  if (failed) {
    return (
      <span className={display ? "math-fail math-block" : "math-fail"} role="note">
        <code>{tex}</code>
        <small>公式无法渲染，已显示原文</small>
      </span>
    );
  }
  return display ? (
    <div className="math-block" tabIndex={0} aria-label={`公式：${tex}`}>
      <span dangerouslySetInnerHTML={{ __html: html }} />
      <button type="button" className="copy-btn math-copy" onClick={() => copy(tex, id)} data-copy-source aria-label="复制原 TeX">{done === id ? "已复制" : "复制 TeX"}</button>
    </div>
  ) : (
    <span className="math-inline" aria-label={`公式：${tex}`} dangerouslySetInnerHTML={{ __html: html }} />
  );
}

export type ProseAssetMap = Map<string, string>;

export type ProseProps = {
  source: string;
  /** 把 [[wikilink]] 目标（unit_id/标题）映射到同一版本内的页内锚点。 */
  wikiTargets?: Map<string, string>;
  assets?: ProseAssetMap;
  pins?: Pin[];
  onPinClick?: (n: number) => void;
  headings?: Heading[];
};

function stripFrontmatter(source: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(source);
  if (!m) return source;
  // 只遮蔽 front matter；保留换行数与后续正文，保证行号/偏移与原文一一对应。
  return source.slice(0, m[0].length).replace(/[^\n]/g, " ") + source.slice(m[0].length);
}

export default function Prose({ source, wikiTargets, assets, pins = [], onPinClick, headings }: ProseProps) {
  const transformed = useMemo(() => {
    const wiki = resolveWikiLinks(stripFrontmatter(source), wikiTargets ?? new Map());
    const latex = projectLatexDelimiters(wiki.text);
    const math = projectDisplayMath(latex.text);
    return { text: math.text, map: (offset: number) => math.map(latex.map(wiki.map(offset))) };
  }, [source, wikiTargets]);
  const rendered = transformed.text;
  const mappedPins = useMemo(() => pins.map((p) => ({ ...p, start: transformed.map(p.start), end: transformed.map(p.end) })), [pins, transformed]);
  const list = useMemo(() => headings ?? extractHeadings(source), [headings, source]);
  const byLine = useMemo(() => {
    const offsets = [0];
    for (let i = 0; i < source.length; i++) if (source[i] === "\n") offsets.push(i + 1);
    return new Map(list.map((h) => [rendered.slice(0, transformed.map(offsets[h.line] ?? 0)).split("\n").length - 1, h]));
  }, [list, source, rendered, transformed]);
  const ref = useRef<HTMLDivElement>(null);

  const components = useMemo<Components>(() => {
    const heading = (Tag: "h1" | "h2" | "h3" | "h4" | "h5" | "h6"): Components["h1"] =>
      function H({ node, children, ...rest }) {
        const line = (node?.position?.start.line ?? 1) - 1;
        const h = byLine.get(line);
        const { ...props } = rest as ComponentProps<"h1">;
        return (
          <Tag {...props} id={h?.id}>
            {children}
            {h ? <a className="anchor" href={`#${h.id}`} aria-label={`本节链接：${h.text}`}>#</a> : null}
          </Tag>
        );
      };
    return {
      h1: heading("h1"), h2: heading("h2"), h3: heading("h3"), h4: heading("h4"), h5: heading("h5"), h6: heading("h6"),
      p: ({ children, className, ...rest }) => <p {...rest} className={className}>{children}</p>,
      table: ({ children }) => (
        <div className="table-scroll" tabIndex={0} role="region" aria-label="表格（可横向滚动）">
          <table>{children}</table>
        </div>
      ),
      // 代码块：真实 <pre><code>；复制取原 source（hast 文本去掉 mdast→hast 加的那一个结尾换行）。
      pre: ({ node, children, ...rest }) => {
        const codeEl = node?.children?.[0] as { properties?: { className?: string[]; [k: string]: unknown }; children?: { value?: string }[] } | undefined;
        const classes = codeEl?.properties?.className ?? [];
        const lang = classes.find((c) => c.startsWith("language-"))?.slice(9) ?? "";
        const raw = (codeEl?.children ?? []).map((c) => c.value ?? "").join("");
        const sourceText = raw.endsWith("\n") ? raw.slice(0, -1) : raw;
        if (lang === "math") {
          return <TexMath tex={sourceText} display />;
        }
        if (lang === "mermaid") return <MermaidBlock source={sourceText} />;
        const pinBlock = ((rest as Record<string, unknown>)["data-pin-block"] ?? (codeEl?.properties as Record<string, unknown> | undefined)?.dataPinBlock) as string | undefined;
        return (
          <figure className="code-block" data-pin-block={pinBlock as string | undefined} data-state={((rest as Record<string, unknown>)["data-state"] ?? (codeEl?.properties as Record<string, unknown> | undefined)?.dataState) as string | undefined}>
            <figcaption className="code-head">
              <span>{lang || "文本"}</span>
              <CopyButton text={sourceText} label={`复制${lang || ""}代码原文`} />
            </figcaption>
            <div className="code-scroll" tabIndex={0} role="region" aria-label="代码（可横向滚动）">
              <pre>{children}</pre>
            </div>
          </figure>
        );
      },
      code: ({ node, className, children, ...rest }) => {
        void node;
        const cls = className ?? "";
        if (cls.includes("language-math") || cls.includes("math-inline")) return <TexMath tex={textOf(children)} display={false} />;
        return <code className={cls || undefined} {...rest}>{children}</code>;
      },
      aside: ({ node, children, ...rest }) => {
        void node;
        const kind = String((rest as Record<string, unknown>)["data-callout"] ?? "note");
        const label = CALLOUT_LABEL[kind] ?? kind;
        return (
          <aside className={`callout callout-${CALLOUT_LABEL[kind] ? kind : "note"}`} data-callout={kind} role="note">
            <p className="callout-title">{label}</p>
            {children}
          </aside>
        );
      },
      span: ({ node, children, className, ...rest }) => {
        void node;
        const n = (rest as Record<string, unknown>)["data-pin"] as string | undefined;
        if (className?.includes("pin") && n) {
          const state = (rest as Record<string, unknown>)["data-state"] as string;
          return (
            <span className="pin" data-pin={n} data-state={state} tabIndex={0} role="button" aria-label={`问题 ${n}（${state === "confirmed" ? "已确认" : "疑似"}），回车查看`}>
              {children}
              <sup className="pin-mark">{state === "confirmed" ? "◆" : "◇"}{n}</sup>
            </span>
          );
        }
        return <span className={className}>{children}</span>;
      },
      a: ({ href, children, node, ...rest }) => {
        void node;
        if (href?.startsWith("#wiki-missing:")) {
          return <span className="wiki-missing">{children}<small>（目标不存在）</small></span>;
        }
        if (href?.startsWith("#wiki:")) {
          const target = decodeURIComponent(href.slice(6));
          return (
            <a className="wiki-link" href={`#${target}`} onClick={(e) => {
              e.preventDefault();
              document.getElementById(target)?.scrollIntoView({ block: "start" });
            }}>{children}</a>
          );
        }
        if (href && /^https?:/i.test(href)) return <a {...rest} href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>;
        if (href && /^mailto:/i.test(href)) return <a {...rest} href={href}>{children}</a>;
        // 页内锚点（含脚注引用/返回）：保留 data-footnote-* 与 id，往返定位才成立。
        if (href?.startsWith("#")) return <a {...rest} href={href}>{children}</a>;
        return <span>{children}</span>;
      },
      // 不加载外部图片（隐私与安全）：显示替代文字。
      img: ({ alt, src, ...rest }) => {
        void rest;
        const original = typeof src === "string" ? src : "";
        const mapped = assets?.get(original) ?? assets?.get(decodeURIComponent(original));
        // eslint-disable-next-line @next/next/no-img-element -- imported immutable assets are same-origin and already hashed
        if (mapped) return <img src={mapped} alt={alt ?? ""} loading="lazy" />;
        return <span className="img-blocked">（图片未加载{alt ? `：${alt}` : ""}）</span>;
      },
      input: ({ checked, type, node }) => (type === "checkbox" ? <ReadingCheckbox key={`${source}:${node?.position?.start.offset ?? 0}`} initialChecked={Boolean(checked)} /> : null),
      mark: ({ children }) => <mark>{children}</mark>,
    };
  }, [assets, byLine, source]);

  useEffect(() => {
    const el = ref.current;
    if (!el || !onPinClick) return;
    const handler = (e: Event) => {
      const t = (e.target as HTMLElement).closest<HTMLElement>("[data-pin]");
      if (t) onPinClick(Number(t.dataset.pin));
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        const t = (e.target as HTMLElement).closest<HTMLElement>("[data-pin]");
        if (t) {
          e.preventDefault();
          onPinClick(Number(t.dataset.pin));
        }
      }
    };
    el.addEventListener("click", handler);
    el.addEventListener("keydown", key);
    return () => {
      el.removeEventListener("click", handler);
      el.removeEventListener("keydown", key);
    };
  }, [onPinClick]);

  const remarkPlugins = useMemo(() => [remarkGfm, remarkEmptyTasks, [remarkMath, { singleDollarTextMath: true }], remarkPins(mappedPins), remarkAnswerLabels, remarkQuestionBlocks, remarkMark, remarkCallout] as never, [mappedPins]);

  return (
    <div className="prose" ref={ref} lang="zh-CN">
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={[[rehypeSanitize, schema]]} components={components} skipHtml={false}>
        {rendered}
      </ReactMarkdown>
    </div>
  );
}
