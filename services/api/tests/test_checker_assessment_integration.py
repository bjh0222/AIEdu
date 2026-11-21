"""Math observations reach C on the exact fixed content; no whole-item waiver."""
from app.v2 import checker


def test_checker_runs_literal_tools_before_clause_review_and_passes_scoped_evidence(monkeypatch):
    monkeypatch.setattr(checker, "_evaluate_core", lambda *a, **k: {"checks": [], "findings": [], "tqi_verdict": "blocked"})
    observed = []
    def criteria(*args, **kw):
        observed.append(kw["literal_evidence"])
        return {"result": "violated"}
    monkeypatch.setattr("app.v2.criterion_review.evaluate_criteria", criteria)
    monkeypatch.setattr("app.v2.difficulty_review.evaluate_difficulty", lambda *a, **k: {"result": "violated"})
    item = {"item_id": "i", "ordinal": 1, "stem_markdown": "Synthetic question", "answer": {"final": "$2+2=5$", "reasoning": "Synthetic proof"}}
    report = checker.evaluate("r", "w", 0, {"context": {"domain_key": "math_logic"}, "execution_policy": {"criterion_assessment": True}},
                              [{"unit_id": "u", "items": [item]}], "v")
    assert len(observed) == 1 and observed[0]["items"][0]["coverage"]["contradicted_count"] == 1
    assert report["tqi_verdict"] == "blocked" and report["automated_assessment"]["result"] == "violated"


def test_bounded_tool_rejection_does_not_crash_whole_quality_run(monkeypatch):
    from app.v2.math_literal import MathLiteralInputError
    monkeypatch.setattr(checker, "_evaluate_core", lambda *a, **k: {})
    def reject(*args, **kwargs): raise MathLiteralInputError("BOUND_FIELD_TOO_LONG")
    monkeypatch.setattr("app.v2.math_literal.analyze_math_literals", reject)
    monkeypatch.setattr("app.v2.criterion_review.evaluate_criteria", lambda *a, **k: {"result": "undecidable"})
    monkeypatch.setattr("app.v2.difficulty_review.evaluate_difficulty", lambda *a, **k: {"result": "undecidable"})
    item = {"item_id": "i", "stem_markdown": "Synthetic", "answer": {"final": "answer", "reasoning": "reason"}}
    report = checker.evaluate("r", "w", 0, {"context": {"domain_key": "math_logic"}, "execution_policy": {"criterion_assessment": True}},
                              [{"unit_id": "u", "items": [item]}], "v")
    assert report["mathematical_evidence"]["items"][0]["status"] == "unsupported"
    assert report["mathematical_evidence"]["items"][0]["reason"] == "BOUND_FIELD_TOO_LONG"
