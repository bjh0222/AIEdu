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
const { projectDisplayMath, extractHeadings } = loaded.exports;
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
console.log('Display math: matrix/prose boundary, KaTeX, headings, evidence offsets, code, escapes, CRLF and idempotence passed');
