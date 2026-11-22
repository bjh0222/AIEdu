"""RF-v2 generality and evidence honesty. All data/providers here are isolated fixtures."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post


@pytest.mark.parametrize("domain,scene,runtime,forbidden", [
    ("history_culture", "exam_paper", "", "independent_math.v1"),
    ("biology_life", "exercise", "", "independent_math.v1"),
    ("arts_creativity", "practical_tutorial", "", "ps51_sandbox.v1"),
    ("cs_it", "practical_tutorial", "python-3.12", "ps51_sandbox.v1"),
])
def test_nonmatching_verifiers_are_never_bound(domain, scene, runtime, forbidden):
    from app.v2.quality import compile_plan
    plan = compile_plan({"domain_key": domain, "runtime_target": runtime,
                         "spec_bindings": [{"scene": scene, "artifact_role": "tutorial"}]})
    assert not plan["conflicts"]
    assert forbidden not in {i["verifier_binding"] for i in plan["check_items"]}
    assert all(i["applicability_reason"] for i in plan["check_items"])


def test_matching_math_and_ps_keep_required_unknowns():
    from app.v2.quality import compile_plan
    for domain, scene, runtime, method in [("math_data", "exam", "", "independent_math.v1"),
        ("computer_cli", "practical_tutorial", "powershell-5.1-desktop", "ps51_sandbox.v1")]:
        plan = compile_plan({"domain_key": domain, "runtime_target": runtime,
                            "spec_bindings": [{"scene": scene, "artifact_role": "tutorial"}]})
        assert any(i["verifier_binding"] == method and i["required"] for i in plan["check_items"])


@pytest.mark.parametrize("scene", ["default_tutorial", "knowledge_tutorial", "practical_tutorial", "reading_guide", "exercise", "exam_paper", "exam"])
def test_all_modes_are_reachable_with_preserved_snapshots(client, scene):
    from app.v2.contracts import TaskInput
    from app.v2.tasks import create_task, snapshot
    snap = {"scene": scene, "version": 7, "content_structure": "原始\r\n结构", "custom_markdown": "不改写旧规则", "deliverables": []}
    value = TaskInput(title="人文", raw_input="解释历史", domain_key="history_culture", branch="任意旧分支/原字符串",
                     spec_bindings=[{"scene": scene, "snapshot": snap}])
    created = create_task(value, str(uuid4()))
    assert not created["conflicts"]
    context = snapshot(created["task_id"])["task"]
    assert context["branch"] == value.branch
    assert context["spec_bindings"][0]["snapshot"] == snap


def test_intent_does_not_override_explicit_values():
    from app.v2.resolver import resolve_task
    from app.v2.contracts import TaskInput
    payload = TaskInput(title="x", raw_input="摄影实践", domain_key="history_culture", branch="旧分支", runtime_target="",
                        spec_bindings=[{"scene": "exam_paper", "artifact_role": "paper"}])
    result = resolve_task(payload.model_dump(), payload.model_fields_set,
                          {"domain_key": "cs_it", "branch": "PS", "scene": "practical_tutorial"})
    assert result["domain_key"] == "history_culture" and result["branch"] == "旧分支"
    assert result["spec_bindings"][0]["scene"] == "exam_paper"
    assert result["runtime_target"] == ""


def test_all_21_profiles_have_distinct_tutorial_exercise_criteria():
    from app.v2.profiles import coverage_catalog
    profiles = coverage_catalog()
    assert len(profiles) == 21
    assert len({p["tutorial"]["rules"][0]["criterion"] for p in profiles}) == 21
    assert len({p["exercise"]["rules"][0]["criterion"] for p in profiles}) == 21
    assert all(p["tutorial"]["status"] == "authored_uncalibrated" for p in profiles)
    assert sum(p["tutorial"]["item_quality_rubric"]["provenance"] == "research_pointer_only"
               for p in profiles) == 7
    assert sum(p["tutorial"]["item_quality_rubric"]["provenance"] == "operational_domain_pack"
               for p in profiles) == 14
    assert all(p["tutorial"]["item_quality_rubric"]["calibration_status"] == "not_calibrated"
               and p["tutorial"]["item_quality_rubric"]["warnings"] == ["authored_uncalibrated"]
               for p in profiles)


def test_one_click_demo_requires_no_manual_confirm_and_is_idempotent(client):
    headers = owner(client)
    payload = {"raw_input": "做一份历史知识教程", "mode": "demo"}
    one = post(client, "/task-starts", payload, headers, "one-click-demo")
    assert one.status_code == 202, one.text
    two = post(client, "/task-starts", payload, headers, "one-click-demo")
    assert two.json() == one.json()
    snap = client.get(one.json()["snapshot_url"]).json()
    assert snap["state"] == "completed" and snap["demo"] is True
    assert snap["units"]
    assert snap["quality"]["quality_status"] != "passed"


def test_no_silent_demo_fallback(client):
    headers = owner(client)
    response = post(client, "/task-starts", {"raw_input": "教我摄影"}, headers)
    assert response.status_code == 403
    assert response.json()["code"] == "PAID_RUN_NOT_AUTHORIZED"


def test_spec_consumer_manifest_covers_all_fields(client):
    from app.v2.resolver import SPEC_FIELDS, spec_entries
    context = {"spec_bindings": [{"artifact_role": "tutorial", "scene": "knowledge_tutorial",
                                  "snapshot": {field: "原文" for field in SPEC_FIELDS}}]}
    entries = spec_entries(context, "generating")
    assert {e["field"] for e in entries} == set(SPEC_FIELDS)
    assert all(e["sha256"] and e["reason"] for e in entries)


def test_claim_identity_is_stable_across_quality_versions():
    from app.v2.quality import finding
    from app.v2.contracts import sha
    item = {"item_id": "i", "verifier_binding": "markdown.v1", "system": "general", "severity_class": "blocker"}
    unit = {"unit_id": "u", "content_hash": sha("abc"), "body": "abc"}
    one = finding(item, {"check_plan_hash": "p"}, unit, "v1", 0, 3, "错", {"hash": "a"})
    two = finding(item, {"check_plan_hash": "p"}, unit, "v2", 0, 3, "错", {"hash": "b"})
    assert one["normalized_claim_hash"] == two["normalized_claim_hash"]


def test_same_family_cannot_be_independent(client, monkeypatch):
    from app.v2 import model_roles
    from app.model_settings import save_profile, ModelProfile
    save_profile(ModelProfile(id="same", name="same", base_url="https://example.test/v1", model="another", api_key="test", model_family="deepseek"))
    monkeypatch.setattr(model_roles, "role_config", lambda: ({"same": {"id": "same", "name": "same", "base_url": "https://example.test/v1",
        "model": "another", "api_key": "test", "timeout": 120, "model_family": "deepseek"}}, {"review": "same"}))
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError, match="MODEL_FAMILY_NOT_INDEPENDENT"):
        model_roles.for_role("review", independent_of=["deepseek"])


def test_saved_roles_override_private_defaults_and_keep_private_only_roles(tmp_path, monkeypatch):
    import json
    from app.v2 import model_roles

    public = tmp_path / "public.json"
    private = tmp_path / "private.json"
    public.write_text(json.dumps({
        "profiles": [{"id": "generation", "name": "generation", "base_url": "https://example.test/v1",
                      "model": "deepseek", "api_key": "test", "timeout": 120, "model_family": "deepseek"}],
        "model_roles": {"generation": "generation", "review": "generation"},
    }), encoding="utf-8")
    private.write_text(json.dumps({
        "profiles": [{"id": "review", "name": "review", "base_url": "https://example.test/v1",
                      "model": "glm", "api_key": "test", "timeout": 120, "model_family": "glm"},
                     {"id": "generation", "base_url": "https://old-endpoint.test/v1",
                      "model": "old", "api_key": "old-key", "timeout": 120, "model_family": "other"}],
        "roles": {"review": "review", "blind_solver": "review"},
    }), encoding="utf-8")
    monkeypatch.setenv("MODEL_SETTINGS_FILE", str(public))
    monkeypatch.setenv("AIEDU_ROLE_SETTINGS_FILE", str(private))
    profiles, roles = model_roles.role_config()
    assert roles["review"] == "generation"
    assert profiles[roles["review"]]["model_family"] == "deepseek"
    assert profiles["generation"]["base_url"] == "https://example.test/v1"
    assert profiles["generation"]["api_key"] == "test"
    assert roles["blind_solver"] == "review"
    assert model_roles.for_role("blind_solver").llm_model_family == "glm"
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError, match="MODEL_FAMILY_NOT_INDEPENDENT"):
        model_roles.for_role("review", independent_of=["deepseek"])


def test_api_saved_role_choices_match_readback_and_runtime(client, tmp_path, monkeypatch):
    from app.v2 import model_roles

    public = tmp_path / "user-settings.json"
    private = tmp_path / "private-defaults.json"
    public.write_text(json.dumps({"active_id": "generation", "profiles": [
        {"id": "generation", "name": "Generation", "base_url": "https://example.test/v1",
         "model": "deepseek-flash", "model_family": "deepseek", "api_key": "fixture-only-secret",
         "timeout": 600, "thinking": "enabled", "reasoning_effort": "high"}],
        "model_roles": {"generation": "generation"}}), encoding="utf-8")
    private.write_text(json.dumps({"profiles": [
        {"id": "review", "name": "Old review", "base_url": "https://example.test/v1",
         "model": "glm", "model_family": "glm", "api_key": "fixture-other-secret", "timeout": 120}],
        "roles": {"review": "review", "blind_solver": "review"}}), encoding="utf-8")
    monkeypatch.setenv("MODEL_SETTINGS_FILE", str(public))
    monkeypatch.setenv("AIEDU_ROLE_SETTINGS_FILE", str(private))
    headers = owner(client)
    current = client.get("/api/v2/model-settings").json()
    assert current["roles"]["review"]["model_family"] == "glm"
    assignments = {role: "generation" for role in
        ("generation", "review", "blind_solver", "secondary_review", "benchmark_panel", "benchmark_refiner")}
    body = {"expected_config_hash": current["config_hash"], "action": "roles", "value": assignments}
    write_headers = headers | {"Idempotency-Key": "unify-roles"}
    saved = client.put("/api/v2/model-settings", json=body, headers=write_headers)
    assert saved.status_code == 200, saved.text
    assert client.put("/api/v2/model-settings", json=body, headers=write_headers).json() == saved.json()
    readback = client.get("/api/v2/model-settings")
    assert readback.json()["role_assignments"] == assignments
    configured_roles = {k: v for k, v in readback.json()["roles"].items()
                        if k in assignments}
    assert set(configured_roles) == set(assignments)
    assert all(r["model"] == "deepseek-flash" and r["model_family"] == "deepseek"
               for r in configured_roles.values())
    assert "fixture-only-secret" not in readback.text and "fixture-other-secret" not in readback.text
    for role in (*assignments, "understanding", "planning", "repair", "knowledge"):
        profile = model_roles.for_role(role)
        assert profile.llm_model == "deepseek-flash"
        assert profile.llm_api_key == "fixture-only-secret"
        assert profile.llm_timeout == 600
        assert profile.llm_reasoning_effort == "high"
    assert client.put("/api/v2/model-settings", json=body,
                      headers=headers | {"Idempotency-Key": "stale-unify"}).status_code == 409
    assert json.loads(private.read_text(encoding="utf-8"))["roles"]["review"] == "review"


def test_untrusted_model_hash_and_confirmed_are_ignored(client):
    from app.v2.checker import wrap_candidate
    from app.v2.contracts import sha
    item = {"item_id": "i", "verifier_binding": "independent_oracle.v1", "system": "general", "severity_class": "blocker"}
    unit = {"unit_id": "u", "content_hash": sha("x=2"), "body": "x=2"}
    out = wrap_candidate({"quote": "x=2", "claim": "错", "status": "confirmed", "input_hash": "fake", "method_version": "fake"},
                         item, {"check_plan_hash": "p"}, unit, "v", {"call_id": "c", "model_family": "glm"})
    assert out["status"] == "unsupported" and out["input_hash"] == unit["content_hash"]
    assert out["method_version"] != "fake"
