"""Interface-level availability for criterion_review.v1; no real provider call."""
from types import SimpleNamespace

import httpx

from test_task_run_v2 import owner


def test_registry_and_saved_review_role_resolve_with_authorized_wait(client):
    from app.v2.inference import AuditedProvider
    from app.v2.model_roles import for_role
    headers = owner(client)
    current = client.get("/api/v2/model-settings", headers=headers).json()
    profile = client.put("/api/v2/model-settings", headers=headers | {"Idempotency-Key": "criterion-profile"}, json={
        "expected_config_hash": current["config_hash"], "action": "profile",
        "value": {"id": "glm-review", "name": "GLM Review", "base_url": "https://fixture.invalid/v1",
                  "model": "glm-5.3-flash", "api_key": "fixture-secret", "timeout": 120,
                  "read_timeout": 600, "thinking": "enabled", "reasoning_effort": "high",
                  "model_family": "glm"}})
    assert profile.status_code == 200, profile.text
    current = client.get("/api/v2/model-settings", headers=headers).json()
    roles = client.put("/api/v2/model-settings", headers=headers | {"Idempotency-Key": "criterion-role"}, json={
        "expected_config_hash": current["config_hash"], "action": "roles",
        "value": {"review": "glm-review"}})
    assert roles.status_code == 200, roles.text

    settings = client.get("/api/v2/model-settings", headers=headers).json()
    assert settings["role_assignments"]["review"] == "glm-review"
    assert settings["roles"]["review"]["configured"] is True
    assert settings["roles"]["review"]["model"] == "glm-5.3-flash"
    capabilities = client.get("/api/v2/capabilities", headers=headers).json()
    assert capabilities["role_configuration"]["review"]["configured"] is True
    assert capabilities["verifiers"]["criterion_review.v1"] == "available_with_configured_review_role"

    resolved = for_role("review")
    assert resolved.llm_model == "glm-5.3-flash"
    assert resolved.llm_model_family == "glm"
    assert resolved.llm_read_timeout == 600
    assert AuditedProvider(resolved).read_timeout == 600


def test_criterion_report_replaces_pending_verifier_check(monkeypatch):
    from copy import deepcopy
    from app.v2 import checker
    from app.v2.assessment_binding import input_binding
    from app.v2.quality import reduce_quality
    from test_assessment_consumption import fixed_assessments

    state, units, assessments = fixed_assessments()
    state["check_plan"]["check_plan_hash"] = "fixture-plan"
    item = state["check_plan"]["check_items"][0]
    item.update(verifier_binding="criterion_review.v1", required=True, severity_class="blocker")
    assessments["automated_assessment"]["input_binding"] = input_binding(state, units, "v")

    def baseline(*args, **kwargs):
        return reduce_quality(state["check_plan"] | {"check_plan_hash": "fixture-plan"}, [{"item_id": item["item_id"], "result": "not_run",
            "reason": "independent verifier unavailable"}], []) | {"check_plan_hash": "fixture-plan", "material_coverage": {}, "item_quality": None}

    monkeypatch.setattr(checker, "check_snapshot", baseline)
    monkeypatch.setattr(checker, "for_role", lambda role, **kwargs: SimpleNamespace(
        llm_model_family="deepseek", llm_model=role, llm_base_url="fixture"))
    monkeypatch.setattr("app.v2.criterion_review.evaluate_criteria",
                        lambda *a, **kw: deepcopy(assessments["automated_assessment"]))
    monkeypatch.setattr("app.v2.difficulty_review.evaluate_difficulty",
                        lambda *a, **kw: deepcopy(assessments["difficulty_review"]))
    report = checker.evaluate("r", "w", 0, state, units, "v")
    check = next(c for c in report["checks"] if c["item_id"] == item["item_id"])
    assert check["result"] == "satisfied"
    assert check["method_version"] == "assessment-input-binding.v1"
    assert report["check_coverage"] == {"completed": 1, "total": 1}
    assert report["tqi_verdict"] == "not_calibrated"


def test_batch_path_does_not_double_call_registered_criterion_verifier():
    from app.v2 import checker_batch
    state = {"execution_policy": {"criterion_assessment": True}, "check_plan": {"check_items": [
        {"item_id": "G1", "decision_type": "semantic", "verifier_binding": "criterion_review.v1"},
        {"item_id": "C1", "decision_type": "semantic", "verifier_binding": "independent_oracle.v1"}]}}
    assert [i["item_id"] for i in checker_batch.semantic_check_items(state)] == ["C1"]
