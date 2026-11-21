// Pure rendering regression; does NOT substitute for a real browser/API check.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { Script } from "node:vm";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const localRequire = createRequire(import.meta.url);
const source = readFileSync(new URL("../components/tasks/AssessmentEvidence.tsx", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } });
const loaded = { exports: {} };
const requireFixture = (id) => id === "next/link" ? { default: (props) => React.createElement("a", props) } : localRequire(id);
new Script(`(function(require,module,exports){${output.outputText}\n})`).runInThisContext()(requireFixture, loaded, loaded.exports);
const render = (quality) => renderToStaticMarkup(React.createElement(loaded.exports.AssessmentEvidence, { taskId: "t", versionId: "v", quality }));
assert.equal(render(undefined), "");
const unknown = render({ difficulty_review: { status: "present", items: null } });
assert.match(unknown, /尚无检查结论/);
assert.doesNotMatch(unknown, /达到已冻结判据/);
const html = render({ difficulty_review: { result: "violated", items: [{ unit_id: "u", item_id: "i", ordinal: 2, result: "violated",
  input_binding_complete: true, comparisons: [{ metric: "nonroutine_operations", observed: 0, required: 1, result: "violated" }],
  computation_load: { level: "heavy" }, dimensions: { steps: [{ reason: "synthetic reason", evidence: [{ field: "answer.reasoning", quote: "<script>untrusted</script>", start_byte: 0, end_byte: 26 }] }] } }] },
  automated_assessment: { result: "violated", coverage: { expected: 2, recorded: 1, decided: 0 }, Q4: { observed_median: null, required_median: 2, per_item_minimum: 1 }, criteria: [] } });
assert.match(html, /实际 0 \/ 目标至少 1/);
assert.match(html, /未达到判据，需整改/);
assert.match(html, /计算负担：重/);
assert.match(html, /中位数 未知 \/ 3/);
assert.match(html, /version=v&amp;unit=u/);
assert.match(html, /&lt;script&gt;untrusted&lt;\/script&gt;/);
assert.doesNotMatch(html, /<script>/);
console.log("AssessmentEvidence: missing/negative/zero/unknown/links/escaping assertions passed (pure render only)");
