"""Known terminal checklist requests stop before unrelated paid dispatches."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.v2 import checker, checker_batch
from app.v2.contracts import DomainError, digest


def fixture_state(concurrency):
    checks = [{"item_id": name, "decision_type": "semantic_rubric", "verifier_binding": "independent_oracle.v1",
               "pass_condition": name, "scope": {}} for name in ("C3.correctness", "C4.constraints", "Q5.exercise")]
    return {"context": {"domain_key": "math_logic"}, "check_plan": {"check_items": checks},
            "execution_policy": {"semantic_batch": True, "check_concurrency": concurrency}}


def fake_bundle(state, unit, item):
    return {"task": {"raw_input": "unchanged"}, "unit": {"unit_id": unit["unit_id"], "body": unit["body"]},
            "check": item, "sources": [{"text": "first"}, {"text": "second"}],
            "review_scope": {"scope": "single_planned_unit"},
            "presentation_contract": {"projection": "unchanged"}, "import_understanding": {"hash": "original"}}


@pytest.mark.parametrize("concurrency", [1, 2, 4])
@pytest.mark.parametrize("blocked_stage", ["checking", "checking_perturbation"])
def test_known_singleton_stops_whole_check_before_any_dispatch(monkeypatch, concurrency, blocked_stage):
    state = fixture_state(concurrency)
    units = [{"unit_id": uid, "artifact_role": "exercise", "body": uid, "items": [{"item_id": uid}]}
             for uid in ("first", "blocked")]
    profile = SimpleNamespace(llm_model_family="fixture", llm_model="fixture", llm_base_url="fixture")
    monkeypatch.setattr(checker, "check_snapshot", lambda *a: {"checks": [], "findings": []})
    monkeypatch.setattr(checker, "for_role", lambda *a: profile)
    monkeypatch.setattr(checker_batch, "for_role", lambda *a, **k: profile)
    monkeypatch.setattr(checker_batch, "scheduled_groups", lambda *a: [(a[-1][0], 32768)])
    monkeypatch.setattr(checker, "blind_bundle", fake_bundle)
    visited = []

    def signatures(run_id, snapshot, uid, stage, system, actual_profile):
        visited.append((uid, stage))
        if uid != "blocked" or stage != blocked_stage:
            return set()
        bundle = fake_bundle(snapshot, units[1], snapshot["check_plan"]["check_items"][0])
        bundle.pop("check")
        checks = snapshot["check_plan"]["check_items"]
        if stage == "checking_perturbation":
            checks = list(reversed(checks[:2]))
            bundle["sources"] = list(reversed(bundle["sources"]))
        bundle["checks"] = [{k: c[k] for k in ("item_id", "pass_condition", "decision_type")} for c in checks]
        # An exactly acknowledged ancestor tree already reached a singleton.
        return {digest(bundle), digest({**bundle, "checks": bundle["checks"][:1]})}

    def forbidden(*a, **k):
        pytest.fail("Known terminal request must stop before provider calls or parallel submission")

    monkeypatch.setattr(checker_batch, "quarantined_checklists", signatures)
    monkeypatch.setattr(checker, "parallel_evaluate", forbidden)
    monkeypatch.setattr(checker, "invoke", forbidden)
    monkeypatch.setattr(checker_batch, "invoke", forbidden)
    original = deepcopy(state)
    with pytest.raises(DomainError, match="CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED"):
        checker.evaluate("run", "worker", 0, state, units, "version")
    assert ("blocked", blocked_stage) in visited
    assert state == original  # No weakened criteria, changed profiles, or synthesized results.


def test_checklist_preflight_does_not_change_request_identity(monkeypatch):
    state = fixture_state(1)
    unit = {"unit_id": "u", "artifact_role": "exercise", "body": "unchanged"}
    checks = state["check_plan"]["check_items"]
    monkeypatch.setattr(checker, "blind_bundle", fake_bundle)
    bundle, system, changed = checker_batch.checklist_inputs(state, unit, checks)
    expected = fake_bundle(state, unit, checks[0])
    expected.pop("check")
    expected["checks"] = [{k: c[k] for k in ("item_id", "pass_condition", "decision_type")} for c in checks]
    assert bundle == expected
    assert system == checker_batch.PROMPT + checker.PRESENTATION_PROMPT
    assert changed == {**expected, "sources": list(reversed(expected["sources"])),
                       "checks": list(reversed(expected["checks"][:2]))}
    calls = []
    monkeypatch.setattr(checker_batch, "quarantined_checklists", lambda *a: calls.append(a) or set())
    checker_batch.preflight_checklists("run", state, [unit], None)
    assert [(a[2], a[3], a[4]) for a in calls] == [("u", "checking", system), ("u", "checking_perturbation", system)]


def test_runtime_and_inapplicable_checks_do_not_create_preflight_requests(monkeypatch):
    state = fixture_state(1)
    state["check_plan"]["check_items"] = [
        {"item_id": "runtime", "decision_type": "atomic_claim", "verifier_binding": "target_runtime.v1"},
        {"item_id": "other", "decision_type": "semantic_rubric", "verifier_binding": "independent_oracle.v1", "scope": {"roles": ["paper"]}},
        {"item_id": "research", "decision_type": "semantic_rubric", "verifier_binding": "research_rubric.v1"},
        {"item_id": "deterministic", "decision_type": "deterministic"}]
    monkeypatch.setattr(checker, "blind_bundle", lambda *a: pytest.fail("No applicable model checklist"))
    monkeypatch.setattr(checker_batch, "quarantined_checklists", lambda *a: pytest.fail("No applicable model checklist"))
    checker_batch.preflight_checklists("run", state, [{"unit_id": "u", "artifact_role": "exercise"}], None)
