"""Trusted verifier adapters. No Python eval, sympify, shell, or generated scripts."""
from __future__ import annotations

import ast

from .contracts import DomainError, digest


def polynomial_identity(left: str, right: str, variables: list[str], input_hash: str) -> dict:
    if (not isinstance(left,str) or not isinstance(right,str) or not isinstance(variables,list)
            or len(left)+len(right) > 1024 or len(variables) > 5 or len(set(variables)) != len(variables)
            or not all(isinstance(v,str) and v.isidentifier() for v in variables)):
        raise DomainError("VERIFIER_RESOURCE_LIMIT", 422)
    # Sparse integer-polynomial arithmetic with a hard work/term/degree envelope.
    # Never expand a model-controlled expression through an unbounded CAS in the API process.
    symbols = {name:index for index,name in enumerate(variables)}
    zero = (0,)*len(variables)
    operations = 0
    def checked(poly):
        result = {m:c for m,c in poly.items() if c}
        if len(result) > 256 or any(sum(m)>32 or abs(c).bit_length()>512 for m,c in result.items()):
            raise DomainError("VERIFIER_RESOURCE_LIMIT",422)
        return result
    def add(a,b):
        result = dict(a)
        for monomial,coefficient in b.items():
            result[monomial] = result.get(monomial,0)+coefficient
        return checked(result)
    def multiply(a,b):
        nonlocal operations
        result = {}
        for x,c in a.items():
            for y,d in b.items():
                operations += 1
                if operations > 4096:
                    raise DomainError("VERIFIER_RESOURCE_LIMIT",422)
                power = tuple(i+j for i,j in zip(x,y))
                result[power] = result.get(power,0)+c*d
            checked(result)
        return checked(result)
    def parse(value):
        tree = ast.parse(value, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 80:
            raise DomainError("VERIFIER_RESOURCE_LIMIT", 422)
        def visit(node,depth=0):
            if depth > 12:
                raise DomainError("VERIFIER_RESOURCE_LIMIT",422)
            if isinstance(node, ast.Expression):
                return visit(node.body,depth+1)
            if isinstance(node, ast.Name) and node.id in symbols:
                power = list(zero)
                power[symbols[node.id]] = 1
                return {tuple(power):1}
            if isinstance(node, ast.Constant) and type(node.value) is int and abs(node.value) <= 10**9:
                return {zero:node.value} if node.value else {}
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return {m:c*(-1 if isinstance(node.op,ast.USub) else 1) for m,c in visit(node.operand,depth+1).items()}
            if isinstance(node, ast.BinOp):
                if isinstance(node.op, ast.Pow):
                    if not isinstance(node.right, ast.Constant) or type(node.right.value) is not int or not 0 <= node.right.value <= 8:
                        raise DomainError("UNSUPPORTED_POLYNOMIAL", 422)
                    a,result = visit(node.left,depth+1),{zero:1}
                    for _ in range(node.right.value):
                        result = multiply(result,a)
                    return result
                a, b = visit(node.left,depth+1), visit(node.right,depth+1)
                if isinstance(node.op, ast.Add):
                    return add(a,b)
                if isinstance(node.op, ast.Sub):
                    return add(a,{m:-c for m,c in b.items()})
                if isinstance(node.op, ast.Mult):
                    return multiply(a,b)
            raise DomainError("UNSUPPORTED_POLYNOMIAL", 422)
        return visit(tree)
    try:
        difference = add(parse(left),{m:-c for m,c in parse(right).items()})
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise DomainError("UNSUPPORTED_POLYNOMIAL", 422) from exc
    raw = {"difference_terms": [{"powers":list(m),"coefficient":c} for m,c in sorted(difference.items())], "variables": variables, "scope": "integer_polynomial_identity_only"}
    return {"result": "satisfied" if not difference else "violated", "input_hash": input_hash,
            "method_version": "polynomial-identity.v2", "tool_raw_output_hash": digest(raw), "raw_output": raw,
            "professional_correctness": "not_run"}


def ps51_verify(code: str, template: dict | None, sandbox_certificate: dict | None):
    # Missing/unverified OS isolation has NO host-execution fallback.
    if not sandbox_certificate or sandbox_certificate.get("status") != "verified":
        return {"result": "not_run", "reason": "PS51_SANDBOX_UNVERIFIED", "method_version": "ps51.trusted-template.v1"}
    # No OS runner has been shipped/certified; a caller-supplied dict is not authority.
    return {"result": "not_run", "reason": "TRUSTED_RUNNER_UNAVAILABLE", "method_version": "ps51.trusted-template.v1"}
