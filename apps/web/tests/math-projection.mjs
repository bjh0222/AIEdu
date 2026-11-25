// Synthetic parser/offset regression; real 3001 verification remains separate.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Script } from 'node:vm';
import ts from 'typescript';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkMath from 'remark-math';
import katex from 'katex';

const sourceModule = readFileSync(new URL('../lib/v2/reader-core.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(sourceModule, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
const loaded = { exports: {} };
new Script(`(function(require,module,exports){${js.outputText}\n})`).runInThisContext()(() => ({}), loaded, loaded.exports);
const { projectDisplayMath, projectLatexDelimiters, extractHeadings } = loaded.exports;
const matrix = String.raw`**1.** Calculate
$$D=\begin{vmatrix}
a&b\\
c&d
\end{vmatrix}.$$
Next question.

### Reference answers
The answer is $ad-bc$.`;
const projected = projectDisplayMath(matrix);
const tree = unified().use(remarkParse).use(remarkMath).parse(projected.text);
const math = tree.children.filter(n => n.type === 'math');
assert.equal(math.length, 1);
assert.doesNotMatch(math[0].value, /Next question|Reference answers/);
assert.doesNotThrow(() => katex.renderToString(math[0].value, { throwOnError: true }));
assert.equal(tree.children.filter(n => n.type === 'heading').length, 1);
assert.ok(tree.children.some(n => n.type === 'paragraph' && n.children.some(c => c.value === 'Next question.')));
for (const needle of ['a&b', 'Next question.', '### Reference answers', 'ad-bc']) {
  assert.equal(projected.text.slice(projected.map(matrix.indexOf(needle)), projected.map(matrix.indexOf(needle)) + needle.length), needle);
}
assert.deepEqual(extractHeadings(projected.text).map(h => h.id), extractHeadings(matrix).map(h => h.id));
for (const original of [
  'Inline $x$ and $$x^2$$ only.',
  '```tex\n$$x\n+y$$\n```\n',
  '~~~tex\n$$x\n+y$$\n~~~\n',
  '```tex\n$$x\n+y$$\n',
  '`$$x\n+y$$`', '``$$x\n+y$$``',
  '\\$\\$x\n+y\\$\\$', '$$unclosed\nparagraph',
]) assert.equal(projectDisplayMath(original).text, original);
const crlf = projectDisplayMath(matrix.replaceAll('\n', '\r\n'));
assert.equal(crlf.text.replaceAll('\r\n', '\n'), projected.text);
assert.equal(projectDisplayMath(projected.text).text, projected.text);

// Real generated exercises use LaTeX delimiters alongside $...$ content. The
// render-only projection must convert them without touching code or evidence
// offsets, otherwise inline subscripts are shown as literal text.
const latex = [
  String.raw`设 \(x_0\)，且 \(F'(x)=2\int_0^x(x-t)f(t)\,dt\)。`,
  "",
  String.raw`\[`,
  String.raw`\int_0^{\sin x}\ln(1+t^2)\,dt.`,
  String.raw`\]`,
  "",
  '`\\(x_1\\)` 保持原文。',
  "",
  "```tex",
  String.raw`\(x_2\)`,
  "```",
].join("\n");
const latexProjected = projectLatexDelimiters(latex);
assert.match(latexProjected.text, /\$x_0\$/);
assert.match(latexProjected.text, /\$F'\(x\)=2\\int_0\^x\(x-t\)f\(t\)\\,dt\$/);
assert.match(latexProjected.text, /\$\$\n\\int_0\^\{\\sin x\}\\ln\(1\+t\^2\)\\,dt\.\n\$\$/);
assert.equal(latexProjected.text.match(/\\\(/g)?.length, 2); // inline code and fenced code
assert.doesNotMatch(latexProjected.text, /\\\[/);
for (const [original, rendered] of [
  ["x_0", "$x_0$"],
  ["F'(x)", "$F'(x)=2\\int_0^x(x-t)f(t)\\,dt$"],
  ["\\int_0^{\\sin x}", "$$\n\\int_0^{\\sin x}\\ln(1+t^2)\\,dt.\n$$"],
  ["\\(x_1\\)", "\\(x_1\\)"],
  ["\\(x_2\\)", "\\(x_2\\)"],
]) {
  const at = latexProjected.map(latex.indexOf(original));
  assert.ok(latexProjected.text.slice(at).startsWith(rendered));
}
const latexTree = unified().use(remarkParse).use(remarkMath).parse(latexProjected.text);
assert.equal(latexTree.children.filter(n => n.type === "paragraph" && n.children.some(c => c.type === "inlineMath")).length, 1);
assert.equal(latexTree.children.filter(n => n.type === "math").length, 1);
const inlineMath = latexTree.children.flatMap(n => n.children ?? []).filter(n => n.type === "inlineMath");
assert.deepEqual(inlineMath.map(n => n.value), ["x_0", "F'(x)=2\\int_0^x(x-t)f(t)\\,dt"]);
for (const node of inlineMath) assert.doesNotThrow(() => katex.renderToString(node.value, { throwOnError: true }));
assert.equal(projectLatexDelimiters(latexProjected.text).text, latexProjected.text);

console.log('Math projection: display boundaries, LaTeX delimiters, subscripts, code, evidence offsets, CRLF and idempotence passed');
