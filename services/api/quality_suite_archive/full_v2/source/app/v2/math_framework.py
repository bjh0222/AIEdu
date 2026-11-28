"""Independent math evidence framework.

This module evaluates only caller-supplied structured formal assertions. It does
not translate model prose, execute programs, or certify pedagogy, difficulty or
professional correctness. Supported arithmetic is exact and resource-bounded.
"""
from __future__ import annotations

from fractions import Fraction
import ast

from .contracts import DomainError, digest, canonical, sha

FRAMEWORK_ID = "independent-math.v2"
FRAMEWORK_VERSION = 2
DOMAIN_KEY = "math_logic"
ASSERTION_METHODS = (
    "polynomial_identity",
    "matrix_equality",
    "matrix_nonsingular",
    "matrix_inverse",
    "linear_system_solution",
)
DIMENSION_IDS = {"premises", "reasoning", "transfer", "difficulty"}
RUBRIC_DIMENSIONS = {
    "premises": "domain, quantifier, satisfiability and degeneracy conditions",
    "reasoning": "derivation and final answer agreement under stated premises",
    "transfer": "mechanism, counterexample and transfer boundary rather than number substitution",
    "difficulty": "prerequisite, cognitive operation and workload; empirical difficulty stays unknown",
}
SCOPE = {
    "literal_structured_data_only": True,
    "model_prose_translation": "unsupported",
    "professional_correctness": "not_run",
    "empirical_difficulty": "not_measured",
    "program_execution": "not_run",
    "applicable_domain_keys": [DOMAIN_KEY],
    "supported_assertions": list(ASSERTION_METHODS),
    "resource_limits": {"matrix_rows": 8, "matrix_cols": 8, "polynomial_max_size": 1024},
}
FRAMEWORK_BINDING = {
    "item_assertion_field": "formal_assertions",
    "transfer_assertion_field": "transfer_assertions",
    "dimension_id_field": "dimension_ids",
    "uncovered_clauses": "remain undecidable",
    "result_semantics": {
        "satisfied": "the literal assertion passed; not a whole-dimension or professional certificate",
        "violated": "the literal assertion failed with exact arithmetic evidence",
        "undecidable": "unsupported, malformed, resource-limited, or no formal evidence",
    },
}


def framework_for_domain(domain_key):
    """Return the engineering framework for the supported math domain, else None."""
    if domain_key != DOMAIN_KEY:
        return None
    return {
        "framework_id": FRAMEWORK_ID,
        "version": FRAMEWORK_VERSION,
        "status": "engineering_formal_only",
        "domain_key": DOMAIN_KEY,
        "rubric_dimensions": RUBRIC_DIMENSIONS,
        "assertion_methods": list(ASSERTION_METHODS),
        "scope": SCOPE,
        "binding": FRAMEWORK_BINDING,
        "framework_hash": digest(FRAMEWORK_BINDING),
    }


def build_math_framework():
    standard_rules = [
        ("premises", "前提与定义域", "只对显式结构化断言判定；定义域、量词、可满足条件和退化条件未覆盖时保持 undecidable。", "hard"),
        ("reasoning", "推理与答案一致", "支持多项式恒等、矩阵相等/非奇异/逆矩阵和线性方程组代入解；模型自评与数题不是证据。", "hard"),
        ("transfer", "迁移与反例", "transfer_assertions 中的形式断言映射 transfer；仅数字替换不构成迁移证据。", "core"),
        ("difficulty", "难度保持未知", "先修、认知操作和算量可描述，但没有实测数据不得给分、签通过或宣称学习收益。", "core"),
    ]
    source_text = ("工程编制独立数学形式评价 v2。只接受结构化形式断言；模型自评、数题和自然语言改写不是证据。\n" + "\n".join(f"{index+1}. {rule}" for index, (_, _, rule, _) in enumerate(standard_rules)))
    standards = [{"id": sid, "name": name, "rule": rule, "scope": {"domains": [DOMAIN_KEY], "scenes": ["exercise", "exam_paper"]}, "priority": index, "strength": strength, "source_quote": rule, "source_ref": f"independent-math.v2#standard-{sid}", "origin": "engineering_authored_uncalibrated"} for index, (sid, name, rule, strength) in enumerate(standard_rules)]
    record = {"id": None, "created_at": None, "title": "AI Edu 独立数学形式评价 v2", "domain_key": DOMAIN_KEY, "version": FRAMEWORK_VERSION, "source_text": source_text, "source_hash": digest(source_text), "standards": standards, "source_map": {"source_type": "engineering_authored_uncalibrated", "source_refs": ["authored:independent-math.v2"], "assertion_methods": list(ASSERTION_METHODS), "binding": FRAMEWORK_BINDING, "scope": SCOPE, "calibration_status": "not_calibrated", "professional_correctness": "not_run"}, "status": "proposed", "extraction_method": "engineering", "warnings": [], "framework_id": FRAMEWORK_ID, "framework_version": FRAMEWORK_VERSION, "assertion_methods": list(ASSERTION_METHODS), "calibration_status": "not_calibrated", "professional_correctness": "not_run"}
    record["framework_hash"] = digest(record)
    record["record_hash"] = digest(record)
    return record


def bind_framework(candidate, *, domain_key=DOMAIN_KEY):
    """Bind only an explicit independent framework; DB prose standards are not auto-trusted."""
    value = candidate
    if isinstance(value, dict) and isinstance(value.get("independent_framework"), dict):
        value = value["independent_framework"]
    contract = framework_for_domain(domain_key)
    if value == contract:
        return {"status": "bound", "framework_id": FRAMEWORK_ID, "version": FRAMEWORK_VERSION,
                "domain_key": domain_key, "framework_hash": digest(value)}
    authoritative = build_math_framework()
    if (domain_key == DOMAIN_KEY and isinstance(value, dict)
            and value.get("framework_id") == FRAMEWORK_ID
            and value.get("framework_version") == FRAMEWORK_VERSION
            and value.get("source_hash") == authoritative["source_hash"]
            and value.get("framework_hash") == authoritative["framework_hash"]):
        return {"status": "bound", "framework_id": FRAMEWORK_ID, "version": FRAMEWORK_VERSION,
                "domain_key": domain_key, "framework_hash": value["framework_hash"]}
    if value is None:
        return {"status": "unbound", "reason": "no_independent_framework_supplied"}
    return {"status": "not_bound", "reason": "framework_is_not_independent_math_contract"}


def _fraction(value):
    if isinstance(value, bool) or not isinstance(value, (int, str, Fraction)):
        raise DomainError("UNSUPPORTED_MATRIX_VALUE", 422)
    try:
        result = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise DomainError("UNSUPPORTED_MATRIX_VALUE", 422) from exc
    if result.numerator.bit_length() > 512 or result.denominator.bit_length() > 512:
        raise DomainError("VERIFIER_RESOURCE_LIMIT", 422)
    return result


def _matrix(value, name):
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise DomainError("UNSUPPORTED_MATRIX_SHAPE", 422)
    width = None
    matrix = []
    for row in value:
        if not isinstance(row, list) or len(row) < 1 or len(row) > 8:
            raise DomainError("UNSUPPORTED_MATRIX_SHAPE", 422)
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise DomainError("UNSUPPORTED_MATRIX_SHAPE", 422)
        matrix.append([_fraction(cell) for cell in row])
    if len(matrix) * width > 64:
        raise DomainError("VERIFIER_RESOURCE_LIMIT", 422)
    if name == "square" and len(matrix) != width:
        raise DomainError("UNSUPPORTED_MATRIX_SHAPE", 422)
    return matrix


def _multiply(left, right):
    if len(left[0]) != len(right):
        raise DomainError("UNSUPPORTED_MATRIX_SHAPE", 422)
    return [[sum((left[i][k] * right[k][j] for k in range(len(right))), Fraction(0))
             for j in range(len(right[0]))] for i in range(len(left))]


def _determinant(matrix):
    n = len(matrix)
    values = [list(row) for row in matrix]
    sign = 1
    for pivot in range(n):
        exchange = next((r for r in range(pivot, n) if values[r][pivot]), None)
        if exchange is None:
            return Fraction(0)
        if exchange != pivot:
            values[pivot], values[exchange] = values[exchange], values[pivot]
            sign *= -1
        for row in range(pivot + 1, n):
            factor = values[row][pivot] / values[pivot][pivot]
            for col in range(pivot, n):
                values[row][col] -= factor * values[pivot][col]
    result = sign
    for diag in range(n):
        result *= values[diag][diag]
    return result


def _polynomial(assertion):
    from .verifiers import polynomial_identity
    required = {"assertion", "left", "right", "variables"}
    if set(assertion) - required - {"dimension_ids"}:
        raise DomainError("UNSUPPORTED_ASSERTION_FIELDS", 422)
    left = assertion["left"].replace("^", "**") if isinstance(assertion["left"], str) else assertion["left"]
    right = assertion["right"].replace("^", "**") if isinstance(assertion["right"], str) else assertion["right"]
    evidence = polynomial_identity(left, right, assertion["variables"], digest(assertion))
    return evidence["result"], evidence


def _matrix_equality(assertion):
    required = {"assertion", "left", "right"}
    if set(assertion) - required - {"dimension_ids"} or not (set(assertion) - {"dimension_ids"}).issubset(required):
        raise DomainError("UNSUPPORTED_ASSERTION_FIELDS", 422)
    left, right = _matrix(assertion["left"], "rectangular"), _matrix(assertion["right"], "rectangular")
    result = "satisfied" if left == right else "violated"
    raw = {"left": left, "right": right, "scope": "literal matrix equality over exact rationals"}
    return result, raw


def _matrix_nonsingular(assertion):
    required = {"assertion", "matrix"}
    if set(assertion) - required - {"dimension_ids"} or not (set(assertion) - {"dimension_ids"}).issubset(required):
        raise DomainError("UNSUPPORTED_ASSERTION_FIELDS", 422)
    matrix = _matrix(assertion["matrix"], "square")
    determinant = _determinant(matrix)
    result = "satisfied" if determinant else "violated"
    raw = {"determinant": determinant, "scope": "exact rational determinant; no row/numerical stability convention"}
    return result, raw


def _matrix_inverse(assertion):
    required = {"assertion", "matrix", "inverse"}
    if set(assertion) - required - {"dimension_ids"} or not (set(assertion) - {"dimension_ids"}).issubset(required):
        raise DomainError("UNSUPPORTED_ASSERTION_FIELDS", 422)
    matrix = _matrix(assertion["matrix"], "square")
    inverse = _matrix(assertion["inverse"], "square")
    product = _multiply(matrix, inverse)
    identity = [[Fraction(int(i == j)) for j in range(len(matrix))] for i in range(len(matrix))]
    result = "satisfied" if product == identity and _determinant(matrix) else "violated"
    raw = {"product": product, "scope": "supplied pair product equals identity over exact rationals"}
    return result, raw


def _linear_system(assertion):
    required = {"assertion", "matrix", "constants", "solution"}
    if set(assertion) - required - {"dimension_ids"} or not (set(assertion) - {"dimension_ids"}).issubset(required):
        raise DomainError("UNSUPPORTED_ASSERTION_FIELDS", 422)
    matrix = _matrix(assertion["matrix"], "rectangular")
    if not isinstance(assertion["constants"], list) or len(assertion["constants"]) != len(matrix):
        raise DomainError("UNSUPPORTED_LINEAR_SYSTEM_SHAPE", 422)
    if not isinstance(assertion["solution"], list) or len(assertion["solution"]) != len(matrix[0]):
        raise DomainError("UNSUPPORTED_LINEAR_SYSTEM_SHAPE", 422)
    constants, solution = [_fraction(x) for x in assertion["constants"]], [_fraction(x) for x in assertion["solution"]]
    residual = [sum((row[j] * solution[j] for j in range(len(solution))), Fraction(0)) - constants[i]
                for i, row in enumerate(matrix)]
    result = "satisfied" if all(value == 0 for value in residual) else "violated"
    raw = {"residual": residual, "scope": "supplied solution substitution only; uniqueness not asserted"}
    return result, raw


def evaluate_assertion(assertion):
    """Evaluate one supplied formal assertion and return raw evidence plus hashes."""
    base = {
        "framework_id": FRAMEWORK_ID, "framework_version": FRAMEWORK_VERSION,
        "method_version": "independent-math-assertion.v2", "domain_key": DOMAIN_KEY,
        "input_hash": digest(assertion), "professional_correctness": "not_run",
        "scope": "literal structured mathematical assertion only",
        "unsupported_reason": None,
    }
    if not isinstance(assertion, dict) or len(str(assertion)) > 16000:
        return {**base, "result": "undecidable", "unsupported_reason": "unsupported_or_resource_limit",
                "tool_raw_output_hash": digest({**base, "result": "undecidable"})}
    kind = assertion.get("assertion")
    try:
        if kind == "polynomial_identity":
            result, raw = _polynomial(assertion)
        elif kind == "matrix_equality":
            result, raw = _matrix_equality(assertion)
        elif kind == "matrix_nonsingular":
            result, raw = _matrix_nonsingular(assertion)
        elif kind == "matrix_inverse":
            result, raw = _matrix_inverse(assertion)
        elif kind == "linear_system_solution":
            result, raw = _linear_system(assertion)
        else:
            result, raw = "undecidable", {"unsupported_assertion": kind if kind is None or isinstance(kind, str) else "non_string"}
            base["unsupported_reason"] = "unsupported_assertion"
    except (DomainError, ValueError, SyntaxError, RecursionError, TypeError, KeyError):
        result, raw = "undecidable", {"unsupported_reason": "verifier_rejected_or_resource_limit"}
        base["unsupported_reason"] = "verifier_rejected_or_resource_limit"
    def serializable(value):
        if isinstance(value, Fraction):
            return str(value)
        if isinstance(value, dict):
            return {key: serializable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [serializable(item) for item in value]
        return value
    evidence = {**base, "result": result, "raw_output": serializable(raw)}
    evidence["tool_raw_output_hash"] = digest(evidence)
    return evidence


def _dimension_ids(assertion):
    if not isinstance(assertion, dict):
        return []
    declared = assertion.get("dimension_ids", [])
    if not isinstance(declared, list) or any(item not in DIMENSION_IDS for item in declared):
        return []
    if declared:
        return list(dict.fromkeys(declared))
    kind = assertion.get("assertion")
    if kind == "polynomial_identity":
        return ["reasoning"]
    if kind in {"matrix_equality", "matrix_inverse", "linear_system_solution"}:
        return ["reasoning"]
    if kind == "matrix_nonsingular":
        return ["premises", "reasoning"]
    return []


def assertion_source(item, assertion):
    """Bind exact literal formal data/equation; never trust a prose translation."""
    if not isinstance(assertion, dict):
        return None
    core = {k: v for k, v in assertion.items() if k not in {"dimension_ids", "anchor"}}
    literals = [canonical(core).decode()]
    if core.get("assertion") == "polynomial_identity" and all(isinstance(core.get(k), str) for k in ("left", "right")):
        literals.append(core["left"] + "=" + core["right"])
    for field, text in (("stem_markdown", item.get("stem_markdown", "")),
        ("answer.final", item.get("answer", {}).get("final", "")),
        ("answer.reasoning", item.get("answer", {}).get("reasoning", ""))):
        raw = text.encode()
        for literal in literals:
            needle = literal.encode()
            if raw.count(needle) != 1:
                continue
            start = raw.index(needle)
            anchor = {"field": field, "field_hash": sha(raw), "start_byte": start,
                "end_byte": start + len(needle), "span_hash": sha(needle), "quote": literal}
            if "anchor" not in assertion or assertion["anchor"] == anchor:
                return anchor
    return None


def freeze_framework(db, context):
    from ..models import EvaluationFramework
    from .runtime import load
    if not any(p.get("domain_key") == DOMAIN_KEY for p in context.get("quality_profiles", [])):
        return context
    expected = build_math_framework()
    for row in db.query(EvaluationFramework).filter_by(domain_key=DOMAIN_KEY, status="active", version=FRAMEWORK_VERSION):
        if row.source_hash == expected["source_hash"] and load(row.standards) == expected["standards"] and load(row.source_map) == expected["source_map"]:
            return {**context, "evaluation_framework": framework_for_domain(DOMAIN_KEY),
                "evaluation_framework_source": {"id": row.id, "source_hash": row.source_hash, "version": row.version}}
    return context


def evaluate_item_quality(state, units, *, framework=None):
    """Per-item evidence aggregation. Missing formal evidence remains undecidable."""
    profiles = [p for p in state.get("context", {}).get("quality_profiles", [])
                if p.get("domain_key") == DOMAIN_KEY and p.get("item_quality_rubric")]
    rubric = profiles[0]["item_quality_rubric"] if profiles else None
    binding = bind_framework(framework if framework is not None else state.get("context", {}).get("evaluation_framework"))
    results = []
    for unit in units:
        for item in unit.get("items", []):
            evidence = []
            assertion_fields = ("formal_assertions", "transfer_assertions")
            for field in assertion_fields:
                values = item.get(field, [])
                if not isinstance(values, list):
                    continue
                for assertion in values:
                    source = assertion_source(item, assertion)
                    core = {k: v for k, v in assertion.items() if k != "anchor"} if isinstance(assertion, dict) else assertion
                    record = evaluate_assertion(core)
                    record["source_binding"] = {"status": "bound" if source else "unbound", "anchor": source}
                    record["literal_assertion_result"] = record["result"]
                    if not source or binding["status"] != "bound":
                        record["result"] = "undecidable"
                    dimensions = _dimension_ids(assertion)
                    if field == "transfer_assertions":
                        dimensions = ["transfer"]
                    record["dimension_ids"] = dimensions
                    record["assertion_field"] = field
                    evidence.append(record)
            dimensions = {}
            for dimension_id in DIMENSION_IDS:
                relevant = [record for record in evidence if dimension_id in record["dimension_ids"]]
                if any(record["result"] == "violated" for record in relevant):
                    result = "violated"
                elif not relevant or all(record["result"] == "undecidable" for record in relevant):
                    result = "undecidable"
                elif any(record["result"] == "undecidable" for record in relevant):
                    result = "undecidable"
                else:
                    result = "satisfied"
                dimensions[dimension_id] = {
                    "result": "undecidable" if dimension_id in {"transfer", "difficulty"} else result,
                    "formal_assertion_result": result,
                    "evidence_count": len(relevant),
                    "coverage": "assertion_only_not_whole_dimension",
                    "evidence_refs": [record["tool_raw_output_hash"] for record in relevant],
                }
            results.append({
                "item_id": item.get("item_id"), "unit_id": unit.get("unit_id"),
                "framework_binding": binding, "rubric_version": rubric.get("version") if rubric else None,
                "score": None, "aggregate_score": None, "calibration_status": "not_calibrated",
                "professional_correctness": "not_run", "dimensions": dimensions, "evidence": evidence,
                "difficulty_status": "not_measured",
            })
    return {
        "method_version": "independent-math-item-evaluation.v2", "domain_key": DOMAIN_KEY,
        "framework_binding": binding, "rubric_version": rubric.get("version") if rubric else None,
        "item_results": results, "semantic_score": None, "aggregate_score": None,
        "calibration_status": "not_calibrated", "difficulty_status": "not_measured",
        "scope": SCOPE,
    }




evaluate_math_items = evaluate_item_quality
