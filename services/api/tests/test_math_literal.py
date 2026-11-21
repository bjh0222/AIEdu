import hashlib
import json

import pytest

from app.v2.math_literal import analyze_math_literals, MathLiteralInputError


def bound_fields(stem: str, final: str, reasoning: str) -> dict[str, dict[str, str]]:
    def item(text: str) -> dict[str, str]:
        return {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
    return {
        "stem_markdown": item(stem),
        "answer.final": item(final),
        "answer.reasoning": item(reasoning),
    }


def test_matrix_equality_is_verified_or_contradicted():
    result = analyze_math_literals(
        "unit-a",
        "item-a",
        bound_fields(
            "$\\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix} = \\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix}$",
            "$\\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix} = \\begin{bmatrix} 1 & 0 \\\\ 3 & 4 \\end{bmatrix}$",
            "",
        ),
    )
    assert result["coverage"]["supported_count"] == 1
    assert result["coverage"]["contradicted_count"] == 1
    assert result["coverage"]["whole_item_status"] == "not_signed"
    assert result["results"][0]["claim_kind"] == "matrix_equality"
    assert result["results"][1]["claim_kind"] == "matrix_equality"


def test_numeric_determinant_and_rank_are_verified():
    result = analyze_math_literals(
        "unit-b",
        "item-b",
        bound_fields(
            "$\\det\\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix} = -2$",
            "$\\operatorname{rank}\\begin{bmatrix} 1 & 2 \\\\ 2 & 4 \\end{bmatrix} = 1$",
            "",
        ),
    )
    kinds = {row["claim_kind"] for row in result["results"]}
    assert kinds == {"determinant", "rank"}
    assert result["coverage"]["unsupported_count"] == 0


def test_scalar_equation_roots_and_identity():
    result = analyze_math_literals(
        "unit-c",
        "item-c",
        bound_fields(
            "$x^2 - 5x + 6 = 0$",
            "$(x-2)(x-3) = x^2 - 5x + 6$",
            "",
        ),
    )
    assert result["coverage"]["supported_count"] == 1
    assert result["results"][0]["verification_result"] == "unsupported"
    assert result["results"][0]["evidence"]["conditional_solution_set_only"]
    roots = result["results"][0]["evidence"]["roots"]
    assert roots == ["2", "3"]


def test_unsupported_proof_is_not_signed():
    result = analyze_math_literals(
        "unit-d",
        "item-d",
        bound_fields(
            "$A$ is invertible because the determinant is nonzero.",
            "This is a general proof.",
            "",
        ),
    )
    assert result["coverage"]["supported_count"] == 0
    assert all(row["claim_status"] == "not_covered" for row in result["results"])


def test_hash_mismatch_is_rejected():
    fields = bound_fields("$x = 1$", "$x = 1$", "")
    fields["stem_markdown"]["sha256"] = "0" * 64
    with pytest.raises(MathLiteralInputError):
        analyze_math_literals("unit-e", "item-e", fields)


def test_output_has_no_private_expression_text():
    result = analyze_math_literals(
        "unit-f",
        "item-f",
        bound_fields("$x = 1$", "$x = 1$", ""),
    )
    serialized = json.dumps(result)
    assert "x = 1" not in serialized
    assert result["coverage"]["item_verdict"] is None



def test_output_contract_shape_is_stable():
    result = analyze_math_literals(
        "unit-g",
        "item-g",
        bound_fields("$x = 1$", "$x = 1$", ""),
    )
    assert set(result) == {"method_version", "scope", "unit_id", "item_id", "fields", "results", "coverage"}
    assert set(result["coverage"]) == {
        "candidate_count", "supported_count", "contradicted_count",
        "unsupported_count", "whole_item_status", "item_verdict",
    }
    assert set(result["fields"]["stem_markdown"]) == {"sha256", "byte_length", "candidate_count"}
    assert set(result["results"][0]) == {
        "field", "start_byte", "end_byte", "span_hash", "span_kind",
        "expression_hash", "claim_kind", "verification_result",
        "claim_status", "evidence", "unsupported_reasons", "tool_raw_output_hash",
    }


def test_missing_or_extra_bound_field_is_rejected():
    fields = bound_fields("$x = 1$", "$x = 1$", "")
    with pytest.raises(MathLiteralInputError):
        analyze_math_literals("unit-h", "item-h", {k: v for k, v in fields.items() if k != "answer.final"})
    extra = dict(fields)
    extra["answer.extra"] = fields["answer.final"]
    with pytest.raises(MathLiteralInputError):
        analyze_math_literals("unit-h", "item-h", extra)


def test_utf8_spans_and_zero_entries_are_exact():
    text = r"中文😀前缀 $\begin{vmatrix}0&1\\2&0\end{vmatrix}=-2$"
    result = analyze_math_literals("u", "i", bound_fields(text, "", ""))
    row = result["results"][0]
    assert row["verification_result"] == "verified"
    actual = text.encode()[row["start_byte"]:row["end_byte"]]
    assert actual.decode().startswith("$") and hashlib.sha256(actual).hexdigest() == row["span_hash"]


def test_prefixed_matrix_equality_and_unbounded_constant_power_are_not_trusted():
    text = r"$A+\begin{bmatrix}1\end{bmatrix}=\begin{bmatrix}1\end{bmatrix}$"
    result = analyze_math_literals("u", "i", bound_fields(text, "$2^{1000000000}=1$", ""))
    assert all(r["verification_result"] == "unsupported" for r in result["results"])

