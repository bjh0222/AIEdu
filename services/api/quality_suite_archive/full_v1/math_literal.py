from __future__ import annotations

"""Bounded literal LaTeX math evidence.

This module is intentionally independent of the checker. It parses only math
delimited in supplied bound fields, never prose, and never returns a whole-item
verdict. Proof-like or translated claims remain unsupported.
"""

import ast
import hashlib
import math
import re
from fractions import Fraction
from typing import Any

METHOD_VERSION = "math-literal.v1"
BOUND_FIELDS = ("stem_markdown", "answer.final", "answer.reasoning")
MAX_FIELD_CHARS = 100_000
MAX_CANDIDATES_PER_FIELD = 64
MAX_MATRIX_ROWS = 8
MAX_MATRIX_COLS = 8
MAX_SYMBOLIC_MATRIX_SIZE = 4
MAX_POLY_DEGREE = 6
MAX_VARIABLES = 6
MAX_EXPR_CHARS = 2_000

SCOPE = {
    "literal_math_only": True,
    "model_translation": "unsupported",
    "proof_of_general_statement": "unsupported",
    "whole_item_verdict": "not_signed",
    "source_binding_required": True,
    "checker_integration": "separate_literal_evidence_not_whole_item_verdict",
    "no_private_expression_text": True,
    "claim_kinds": ["matrix_equality", "determinant", "rank", "scalar_equation", "unsupported"],
    "verification_result_values": ["verified", "contradicted", "unsupported"],
    "claim_status_values": ["supported", "contradicted", "not_covered"],
    "supported": [
        "literal_matrix_equality",
        "literal_determinant",
        "literal_numeric_rank",
        "scalar_equation_roots_or_identity",
    ],
    "limits": {
        "max_matrix_rows": MAX_MATRIX_ROWS,
        "max_matrix_cols": MAX_MATRIX_COLS,
        "max_symbolic_matrix_size": MAX_SYMBOLIC_MATRIX_SIZE,
        "max_polynomial_degree": MAX_POLY_DEGREE,
        "max_variables": MAX_VARIABLES,
    },
}


class MathLiteralInputError(ValueError):
    pass


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span_hash(text: str, start: int, end: int) -> str:
    return hashlib.sha256(text.encode("utf-8")[start:end]).hexdigest()


class Poly:
    """Small exact multivariate polynomial with nonnegative integer powers."""

    def __init__(self, terms: dict[tuple[tuple[str, int], ...], Fraction] | None = None):
        if len(terms or {}) > 1024:
            raise MathLiteralInputError("POLYNOMIAL_TERM_LIMIT")
        self.terms: dict[tuple[tuple[str, int], ...], Fraction] = {}
        for key, coeff in (terms or {}).items():
            key = tuple(sorted((name, power) for name, power in key if power != 0))
            if coeff != 0:
                self.terms[key] = self.terms.get(key, Fraction(0)) + coeff
        self.terms = {k: v for k, v in self.terms.items() if v}
        if any(v.numerator.bit_length() > 4096 or v.denominator.bit_length() > 4096 for v in self.terms.values()):
            raise MathLiteralInputError("NUMERIC_SIZE_LIMIT")

    @classmethod
    def constant(cls, value: Fraction | int) -> "Poly":
        return cls({(): Fraction(value)})

    @classmethod
    def variable(cls, name: str) -> "Poly":
        return cls({((name, 1),): Fraction(1)})

    @property
    def variables(self) -> set[str]:
        return {name for key in self.terms for name, _ in key}

    @property
    def degree(self) -> int:
        return max((sum(power for _, power in key) for key in self.terms), default=0)

    def is_constant(self) -> bool:
        return not self.terms or len(self.terms) == 1 and () in self.terms

    def constant_value(self) -> Fraction:
        if not self.is_constant():
            raise MathLiteralInputError("NOT_CONSTANT")
        return self.terms.get((), Fraction(0))

    def _map(self, term: tuple[tuple[str, int], ...]) -> dict[str, int]:
        return {name: power for name, power in term}

    def _key(self, term: dict[str, int]) -> tuple[tuple[str, int], ...]:
        return tuple(sorted((name, power) for name, power in term.items() if power != 0))

    def _add(self, other: "Poly", sign: Fraction) -> "Poly":
        result = dict(self.terms)
        for key, coeff in other.terms.items():
            result[key] = result.get(key, Fraction(0)) + sign * coeff
        return Poly(result)

    def __add__(self, other: "Poly") -> "Poly":
        return self._add(other, Fraction(1))

    def __sub__(self, other: "Poly") -> "Poly":
        return self._add(other, Fraction(-1))

    def __mul__(self, other: "Poly") -> "Poly":
        if self.degree + other.degree > MAX_POLY_DEGREE or len(self.variables | other.variables) > MAX_VARIABLES:
            raise MathLiteralInputError("POLYNOMIAL_LIMIT")
        result: dict[tuple[tuple[str, int], ...], Fraction] = {}
        for left_key, left_coeff in self.terms.items():
            left_map = self._map(left_key)
            for right_key, right_coeff in other.terms.items():
                right_map = self._map(right_key)
                combined = dict(left_map)
                for name, power in right_map.items():
                    combined[name] = combined.get(name, 0) + power
                key = self._key(combined)
                result[key] = result.get(key, Fraction(0)) + left_coeff * right_coeff
        return Poly(result)

    def __truediv__(self, other: "Poly") -> "Poly":
        if not other.is_constant() or other.constant_value() == 0:
            raise MathLiteralInputError("NON_CONSTANT_OR_ZERO_DIVISOR")
        return Poly({key: coeff / other.constant_value() for key, coeff in self.terms.items()})

    def __pow__(self, power: int) -> "Poly":
        if power < 0 or power > MAX_POLY_DEGREE or self.degree * power > MAX_POLY_DEGREE:
            raise MathLiteralInputError("POLYNOMIAL_LIMIT")
        result = Poly.constant(Fraction(1))
        for _ in range(power):
            result = result * self
        return result

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Poly) and self.terms == other.terms

    def __repr__(self) -> str:
        return f"Poly({self.terms!r})"


_GREEK = {
    "alpha": "alpha_", "beta": "beta_", "gamma": "gamma_", "delta": "delta_",
    "epsilon": "epsilon_", "varepsilon": "epsilon_", "zeta": "zeta_",
    "eta": "eta_", "theta": "theta_", "iota": "iota_", "kappa": "kappa_",
    "lambda": "lambda_", "mu": "mu_", "nu": "nu_", "xi": "xi_",
    "pi": "pi_", "rho": "rho_", "sigma": "sigma_", "tau": "tau_",
    "phi": "phi_", "varphi": "phi_", "chi": "chi_", "psi": "psi_", "omega": "omega_",
}


def _replace_fractions(expr: str) -> str:
    pattern = re.compile(r"\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    while True:
        expr, count = pattern.subn(lambda m: f"(({m.group(1)})/({m.group(2)}))", expr)
        if count == 0:
            return expr


def _latex_to_python(expr: str) -> str:
    value = expr.strip()
    value = re.sub(r"\\(?:left|right|bigl?|bigr?|Bigl?|Bigr?)\b", "", value)
    value = re.sub(r"\\[,;:!]", " ", value)
    value = re.sub(r"\\(?:cdot|times)", "*", value)
    value = re.sub(r"\\div", "/", value)
    value = re.sub(r"\^\{([^{}]*)\}", r"**(\1)", value)
    value = re.sub(r"(?<![\\])\^", "**", value)
    value = _replace_fractions(value)
    for name, identifier in _GREEK.items():
        value = re.sub(r"\\" + name + r"\b", identifier, value)
    value = re.sub(r"\\(?:quad|qquad)\b", " ", value)
    value = re.sub(r"(?<=\d)(?=[A-Za-z_])", "*", value)
    value = re.sub(r"(?<=\d)(?=\()", "*", value)
    value = re.sub(r"(?<=\))(?=\()", "*", value)
    return value.strip()


def _poly_from_ast(node: ast.AST) -> Poly:
    if isinstance(node, ast.Expression):
        return _poly_from_ast(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            raise MathLiteralInputError("BOOLEAN_NOT_NUMERIC")
        if isinstance(node.value, int):
            return Poly.constant(Fraction(node.value))
        if isinstance(node.value, float):
            return Poly.constant(Fraction(str(node.value)))
        raise MathLiteralInputError("UNSUPPORTED_CONSTANT")
    if isinstance(node, ast.Name):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", node.id):
            raise MathLiteralInputError("INVALID_VARIABLE_NAME")
        return Poly.variable(node.id)
    if isinstance(node, ast.UnaryOp):
        value = _poly_from_ast(node.operand)
        if isinstance(node.op, ast.USub):
            return Poly({key: -coeff for key, coeff in value.terms.items()})
        if isinstance(node.op, ast.UAdd):
            return value
        raise MathLiteralInputError("UNSUPPORTED_UNARY_OPERATOR")
    if isinstance(node, ast.BinOp):
        left, right = _poly_from_ast(node.left), _poly_from_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.Pow):
            if not right.is_constant():
                raise MathLiteralInputError("NON_CONSTANT_EXPONENT")
            exponent = right.constant_value()
            if exponent.denominator != 1 or exponent < 0:
                raise MathLiteralInputError("UNSUPPORTED_EXPONENT")
            return left ** int(exponent)
        raise MathLiteralInputError("UNSUPPORTED_BINARY_OPERATOR")
    raise MathLiteralInputError("UNSUPPORTED_AST_NODE")


def parse_scalar(expr: str) -> Poly:
    if not isinstance(expr, str) or not expr.strip():
        raise MathLiteralInputError("EMPTY_EXPRESSION")
    if len(expr) > MAX_EXPR_CHARS:
        raise MathLiteralInputError("EXPRESSION_TOO_LONG")
    source = _latex_to_python(expr)
    if not source:
        raise MathLiteralInputError("EMPTY_NORMALIZED_EXPRESSION")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise MathLiteralInputError("SYNTAX_NOT_SUPPORTED") from exc
    if len(list(ast.walk(tree))) > 256:
        raise MathLiteralInputError("EXPRESSION_NODE_LIMIT")
    value = _poly_from_ast(tree)
    if value.degree > MAX_POLY_DEGREE or len(value.variables) > MAX_VARIABLES:
        raise MathLiteralInputError("POLYNOMIAL_LIMIT")
    return value


def _split_outside_braces(text: str, separator: str) -> list[str]:
    parts, depth, start, index = [], 0, 0, 0
    while index < len(text):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index])
            start = index + len(separator)
            index = start
            continue
        index += 1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


_MATRIX_ENV = r"(?:bmatrix|pmatrix|vmatrix|matrix)"
_MATRIX_RE = re.compile(r"\\begin\s*\{\s*(" + _MATRIX_ENV + r")\s*\}(?P<body>.*?)\\end\s*\{\s*\1\s*\}", re.S)


def _parse_matrix(expr: str) -> tuple[list[list[Poly]], tuple[int, int]]:
    match = _MATRIX_RE.search(expr)
    if not match:
        raise MathLiteralInputError("MATRIX_LITERAL_REQUIRED")
    body = match.group("body").strip()
    if _MATRIX_RE.search(body):
        raise MathLiteralInputError("NESTED_MATRIX_UNSUPPORTED")
    rows = _split_outside_braces(body, r"\\")
    if not 1 <= len(rows) <= MAX_MATRIX_ROWS:
        raise MathLiteralInputError("MATRIX_ROW_LIMIT")
    matrix: list[list[Poly]] = []
    width = None
    for row in rows:
        cells = _split_outside_braces(row, "&")
        if width is None:
            width = len(cells)
        elif len(cells) != width:
            raise MathLiteralInputError("RAGGED_MATRIX")
        if not 1 <= len(cells) <= MAX_MATRIX_COLS:
            raise MathLiteralInputError("MATRIX_COLUMN_LIMIT")
        matrix.append([parse_scalar(cell) for cell in cells])
    if width is None:
        raise MathLiteralInputError("EMPTY_MATRIX")
    if len(matrix) * width > 64:
        raise MathLiteralInputError("MATRIX_CELL_LIMIT")
    return matrix, (len(matrix), width)


def _fraction_matrix(matrix: list[list[Poly]]) -> list[list[Fraction]]:
    return [[cell.constant_value() for cell in row] for row in matrix]


def _fraction_determinant(matrix: list[list[Fraction]]) -> Fraction:
    values = [list(row) for row in matrix]
    size = len(values)
    sign = Fraction(1)
    for pivot in range(size):
        exchange = next((r for r in range(pivot, size) if values[r][pivot] != 0), None)
        if exchange is None:
            return Fraction(0)
        if exchange != pivot:
            values[pivot], values[exchange] = values[exchange], values[pivot]
            sign = -sign
        divisor = values[pivot][pivot]
        for row in range(pivot + 1, size):
            factor = values[row][pivot] / divisor
            for col in range(pivot, size):
                values[row][col] -= factor * values[pivot][col]
    result = sign
    for diag in range(size):
        result *= values[diag][diag]
    return result


def _fraction_rank(matrix: list[list[Fraction]]) -> int:
    values = [list(row) for row in matrix]
    rows, cols = len(values), len(values[0])
    rank = 0
    for col in range(cols):
        pivot = next((r for r in range(rank, rows) if values[r][col] != 0), None)
        if pivot is None:
            continue
        values[rank], values[pivot] = values[pivot], values[rank]
        divisor = values[rank][col]
        values[rank] = [value / divisor for value in values[rank]]
        for row in range(rows):
            if row != rank and values[row][col] != 0:
                factor = values[row][col]
                values[row] = [a - factor * b for a, b in zip(values[row], values[rank])]
        rank += 1
    return rank


def _symbolic_determinant(matrix: list[list[Poly]]) -> Poly:
    size = len(matrix)
    if size > MAX_SYMBOLIC_MATRIX_SIZE:
        raise MathLiteralInputError("SYMBOLIC_DETERMINANT_LIMIT")
    if size == 1:
        return matrix[0][0]
    total = Poly.constant(Fraction(0))
    for col, cell in enumerate(matrix[0]):
        if cell.is_constant() and cell.constant_value() == 0:
            continue
        minor = [row[:col] + row[col+1:] for row in matrix[1:]]
        term = cell * _symbolic_determinant(minor)
        total = total + term if col % 2 == 0 else total - term
    return total


def _strip_prefix(expr: str, kind: str) -> str:
    if kind == "determinant" and re.match(r"^\\begin\{vmatrix\}", expr.strip()):
        return expr.strip()
    patterns = {
        "determinant": [r"^\\det\b"],
        "rank": [r"^\\operatorname\{rank\}", r"^\\mathrm\{rank\}", r"^\\rank\b"],
    }
    for pattern in patterns[kind]:
        if re.match(pattern, expr.strip()):
            return expr.strip()[re.match(pattern, expr.strip()).end():]
    raise MathLiteralInputError("OPERATOR_PREFIX_NOT_FOUND")


def _parse_operator_statement(expr: str, kind: str) -> tuple[str, dict[str, Any]]:
    body = _strip_prefix(expr, kind)
    matrix_match = _MATRIX_RE.search(body)
    if not matrix_match:
        raise MathLiteralInputError("MATRIX_LITERAL_REQUIRED")
    matrix, shape = _parse_matrix(body)
    if body[:matrix_match.start()].strip():
        raise MathLiteralInputError("OPERATOR_ARGUMENT_AMBIGUOUS")
    after = body[matrix_match.end():].strip()
    if "=" not in after:
        raise MathLiteralInputError("CLAIM_TARGET_REQUIRED")
    lhs, rhs = after.split("=", 1)
    if lhs.strip():
        raise MathLiteralInputError("TEXT_BEFORE_CLAIM_TARGET")
    expected = parse_scalar(rhs)
    if kind == "determinant":
        if all(cell.is_constant() for row in matrix for cell in row):
            actual = Poly.constant(_fraction_determinant(_fraction_matrix(matrix)))
        else:
            actual = _symbolic_determinant(matrix)
        evidence: dict[str, Any] = {"shape": shape, "kind": "exact_determinant"}
    else:
        if not all(cell.is_constant() for row in matrix for cell in row):
            raise MathLiteralInputError("SYMBOLIC_RANK_UNSUPPORTED")
        actual = Poly.constant(Fraction(_fraction_rank(_fraction_matrix(matrix))))
        evidence = {"shape": shape, "kind": "exact_numeric_rank"}
    if actual == expected:
        return "verified", evidence
    return "contradicted", evidence


def _parse_matrix_equality(expr: str) -> tuple[str, dict[str, Any]]:
    matches = list(_MATRIX_RE.finditer(expr))
    if len(matches) != 2:
        raise MathLiteralInputError("TWO_MATRIX_LITERALS_REQUIRED")
    if expr[:matches[0].start()].strip() or expr[matches[1].end():].strip():
        raise MathLiteralInputError("MATRIX_EQUALITY_CONTEXT_UNSUPPORTED")
    separator = expr[matches[0].end():matches[1].start()]
    if separator.count("=") != 1 or separator.strip() != "=":
        raise MathLiteralInputError("SINGLE_EQUALITY_REQUIRED")
    left, left_shape = _parse_matrix(expr[matches[0].start():matches[0].end()])
    right, right_shape = _parse_matrix(expr[matches[1].start():matches[1].end()])
    if left_shape != right_shape:
        return "contradicted", {"left_shape": left_shape, "right_shape": right_shape}
    for row_left, row_right in zip(left, right):
        for cell_left, cell_right in zip(row_left, row_right):
            if cell_left != cell_right:
                return "contradicted", {"left_shape": left_shape, "right_shape": right_shape}
    return "verified", {"left_shape": left_shape, "right_shape": right_shape}


def _scalar_equation(expr: str) -> tuple[str, dict[str, Any]]:
    parts = _split_outside_braces(expr, "=")
    if len(parts) != 2:
        raise MathLiteralInputError("SINGLE_SCALAR_EQUALITY_REQUIRED")
    left, right = parse_scalar(parts[0]), parse_scalar(parts[1])
    difference = left - right
    variables = sorted(left.variables | right.variables)
    evidence: dict[str, Any] = {"kind": "scalar_equation", "variable_count": len(variables)}
    if not difference.terms:
        return "verified", evidence
    if not variables:
        return "contradicted", evidence
    if len(variables) != 1 or difference.degree > 2:
        raise MathLiteralInputError("SOLUTION_SET_NOT_BOUNDED")
    variable = variables[0]
    if difference.degree == 1:
        roots = [(-difference.terms.get((), Fraction(0)) / difference.terms[((variable, 1),)])]
    else:
        a = difference.terms.get(((variable, 2),), Fraction(0))
        b = difference.terms.get(((variable, 1),), Fraction(0))
        c = difference.terms.get((), Fraction(0))
        discriminant = b * b - 4 * a * c
        if discriminant < 0:
            raise MathLiteralInputError("COMPLEX_ROOTS_UNSUPPORTED")
        numerator = discriminant.numerator
        denominator = discriminant.denominator
        if math.isqrt(numerator) ** 2 != numerator or math.isqrt(denominator) ** 2 != denominator:
            raise MathLiteralInputError("IRRATIONAL_ROOT_UNSUPPORTED")
        sqrt = Fraction(math.isqrt(numerator), math.isqrt(denominator))
        roots = sorted([(-b + sqrt) / (2 * a), (-b - sqrt) / (2 * a)])
    evidence.update(roots=[str(root) for root in roots], degree=difference.degree)
    # Solving a conditional equation is not verification that the equation is
    # an identity, nor confirmation of any later prose solution claim.
    evidence["conditional_solution_set_only"] = True
    return "unsupported", evidence


def _parse_statement(expr: str) -> tuple[str, str, dict[str, Any]]:
    compact = expr.strip()
    if not compact:
        raise MathLiteralInputError("EMPTY_STATEMENT")
    if re.match(r"^\\det\b|^\\begin\{vmatrix\}", compact):
        kind = "determinant"
        result, evidence = _parse_operator_statement(compact, kind)
    elif re.search(r"^\\(?:operatorname|mathrm)\s*\{\s*rank\s*\}|^\\rank\b", compact):
        kind = "rank"
        result, evidence = _parse_operator_statement(compact, kind)
    elif _MATRIX_RE.search(compact):
        kind = "matrix_equality"
        result, evidence = _parse_matrix_equality(compact)
    else:
        kind = "scalar_equation"
        result, evidence = _scalar_equation(compact)
    return kind, result, evidence


_MATH_SPAN_RE = re.compile(
    r"(?s)\$\$(?P<display>.+?)\$\$|\$(?P<inline>.+?)\$|"
    r"\\\((?P<paren>.+?)\\\)|\\\[(?P<bracket>.+?)\\\]"
)


def _math_spans(text: str) -> list[dict[str, Any]]:
    spans = []
    for match in _MATH_SPAN_RE.finditer(text):
        kind = match.lastgroup
        kind_name = {
            "display": "display_math",
            "inline": "inline_math",
            "paren": "paren_math",
            "bracket": "bracket_math",
        }[kind]
        spans.append({
            "start_byte": len(text[:match.start()].encode("utf-8")),
            "end_byte": len(text[:match.end()].encode("utf-8")),
            "span_hash": _sha256(match.group(0)),
            "span_kind": kind_name,
            "expression": match.group(kind).strip(),
        })
        if len(spans) >= MAX_CANDIDATES_PER_FIELD:
            break
    return spans


def _validate_bound_field(field: str, value: Any) -> tuple[str, str]:
    if field not in BOUND_FIELDS:
        raise MathLiteralInputError(f"UNSUPPORTED_BOUND_FIELD:{field}")
    if not isinstance(value, dict):
        raise MathLiteralInputError("BOUND_FIELD_NOT_HASHED")
    text = value.get("text")
    expected_hash = value.get("sha256")
    if not isinstance(text, str) or not isinstance(expected_hash, str):
        raise MathLiteralInputError("BOUND_FIELD_MISSING_TEXT_OR_HASH")
    if len(text) > MAX_FIELD_CHARS:
        raise MathLiteralInputError("BOUND_FIELD_TOO_LONG")
    actual_hash = _sha256(text)
    if actual_hash != expected_hash:
        raise MathLiteralInputError("BOUND_FIELD_HASH_MISMATCH")
    return text, actual_hash


def analyze_math_literals(unit_id: str, item_id: str, bound_fields: dict[str, Any]) -> dict[str, Any]:
    """Return literal evidence only. This function never signs the whole item."""
    if not isinstance(unit_id, str) or not unit_id:
        raise MathLiteralInputError("UNIT_ID_REQUIRED")
    if not isinstance(item_id, str) or not item_id:
        raise MathLiteralInputError("ITEM_ID_REQUIRED")
    if not isinstance(bound_fields, dict) or not bound_fields:
        raise MathLiteralInputError("BOUND_FIELDS_REQUIRED")
    unknown = set(bound_fields) - set(BOUND_FIELDS)
    if unknown:
        raise MathLiteralInputError("UNSUPPORTED_BOUND_FIELD:" + ",".join(sorted(unknown)))
    if set(bound_fields) != set(BOUND_FIELDS):
        raise MathLiteralInputError("ALL_THREE_BOUND_FIELDS_REQUIRED")

    field_bindings: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    unsupported_reasons: list[str] = []
    for field in BOUND_FIELDS:
        text, actual_hash = _validate_bound_field(field, bound_fields[field])
        spans = _math_spans(text)
        field_bindings[field] = {
            "sha256": actual_hash,
            "byte_length": len(text.encode("utf-8")),
            "candidate_count": len(spans),
        }
        for span in spans:
            expression_hash = hashlib.sha256(span["expression"].encode("utf-8")).hexdigest()
            try:
                kind, verification_result, evidence = _parse_statement(span["expression"])
                if verification_result == "verified":
                    claim_status = "supported"
                elif verification_result == "contradicted":
                    claim_status = "contradicted"
                else:
                    claim_status = "not_covered"
                reasons = ["CONDITIONAL_EQUATION_SOLUTION_SET_ONLY"] if evidence.get("conditional_solution_set_only") else []
            except MathLiteralInputError as exc:
                kind = "unsupported"
                verification_result = "unsupported"
                claim_status = "not_covered"
                evidence = {}
                reasons = [str(exc)]
                unsupported_reasons.append(str(exc))
            results.append({
                "field": field,
                "start_byte": span["start_byte"],
                "end_byte": span["end_byte"],
                "span_hash": span["span_hash"],
                "span_kind": span["span_kind"],
                "expression_hash": expression_hash,
                "claim_kind": kind,
                "verification_result": verification_result,
                "claim_status": claim_status,
                "evidence": evidence,
                "tool_raw_output_hash": _sha256(repr((kind, verification_result, evidence))),
                "unsupported_reasons": reasons,
            })
    supported_count = sum(row["verification_result"] == "verified" for row in results)
    contradicted_count = sum(row["verification_result"] == "contradicted" for row in results)
    return {
        "method_version": METHOD_VERSION,
        "scope": SCOPE,
        "unit_id": unit_id,
        "item_id": item_id,
        "fields": field_bindings,
        "results": results,
        "coverage": {
            "candidate_count": len(results),
            "supported_count": supported_count,
            "contradicted_count": contradicted_count,
            "unsupported_count": sum(row["claim_status"] == "not_covered" for row in results),
            "whole_item_status": "not_signed",
            "item_verdict": None,
        },
    }








