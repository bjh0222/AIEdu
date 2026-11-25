"""Four workflow partitions map every core task role to the saved model."""
from __future__ import annotations

import json

from test_task_run_v2 import owner


def _profile(profile_id, model, family):
    return {
        "id": profile_id,
        "name": profile_id,
        "base_url": "https://model.example/v1",
        "model": model,
        "model_family": family,
        "api_key": "fixture-secret",
        "timeout": 120,
    }


def configure_workflow_models(tmp_path, monkeypatch):
    public = tmp_path / "workflow-model-settings.json"
    private = tmp_path / "workflow-private-roles.json"
    public.write_text(json.dumps({
        "active_id": "generation",
        "profiles": [
            _profile("planner", "glm-plan", "glm"),
            _profile("generator", "deepseek-v4.1-flash-hs", "deepseek"),
            _profile("quality", "qwen3.8-flash", "qwen"),
            _profile("chat", "glm-5.3-flash", "glm"),
        ],
        "model_roles": {"generation": "generator"},
    }), encoding="utf-8")
    private.write_text(json.dumps({"profiles": [], "roles": {}}), encoding="utf-8")
    monkeypatch.setenv("MODEL_SETTINGS_FILE", str(public))
    monkeypatch.setenv("AIEDU_ROLE_SETTINGS_FILE", str(private))
    return public


def save_workflow_roles(client, headers, current):
    assignment = {
        "understanding_planning": "planner",
        "generation_repair": "generator",
        "quality": "quality",
        "chat": "chat",
    }
    response = client.put("/api/v2/model-settings", headers={
        **headers, "Idempotency-Key": "workflow-role-save",
    }, json={
        "expected_config_hash": current["config_hash"],
        "action": "workflow_roles",
        "value": assignment,
    })
    assert response.status_code == 200, response.text
    return assignment


def test_workflow_roles_are_saved_read_back_and_resolved_exactly(client, tmp_path, monkeypatch):
    from app.v2.model_roles import for_role

    public = configure_workflow_models(tmp_path, monkeypatch)
    headers = owner(client)
    current = client.get("/api/v2/model-settings", headers=headers).json()
    assignment = save_workflow_roles(client, headers, current)

    readback = client.get("/api/v2/model-settings", headers=headers)
    body = readback.json()
    assert readback.status_code == 200
    assert body["workflow_role_assignments"] == assignment
    assert all(body["workflow_roles"][key]["configured"] for key in assignment)
    assert body["workflow_roles"]["quality"]["model"] == "qwen3.8-flash"
    assert "fixture-secret" not in readback.text
    assert json.loads(public.read_text(encoding="utf-8"))["workflow_roles"] == assignment

    expected = {
        "understanding": ("planner", "glm-plan"),
        "planning": ("planner", "glm-plan"),
        "generation": ("generator", "deepseek-v4.1-flash-hs"),
        "repair": ("generator", "deepseek-v4.1-flash-hs"),
        "review": ("quality", "qwen3.8-flash"),
        "blind_solver": ("quality", "qwen3.8-flash"),
        "secondary_review": ("quality", "qwen3.8-flash"),
        "chat": ("chat", "glm-5.3-flash"),
    }
    for role, (profile_id, model) in expected.items():
        profile = for_role(role)
        assert profile.llm_profile_id == profile_id
        assert profile.llm_model == model
    assert for_role("review", independent_of=["deepseek"]).llm_profile_id == "quality"


def test_workflow_save_is_cas_and_rejects_unknown_profiles(client, tmp_path, monkeypatch):
    configure_workflow_models(tmp_path, monkeypatch)
    headers = owner(client)
    current = client.get("/api/v2/model-settings", headers=headers).json()
    response = client.put("/api/v2/model-settings", headers={
        **headers, "Idempotency-Key": "workflow-role-invalid",
    }, json={
        "expected_config_hash": current["config_hash"],
        "action": "workflow_roles",
        "value": {"quality": "missing-profile"},
    })
    assert response.status_code == 422
    assert response.json()["code"] == "MODEL_ROLE_INVALID"

    assignment = save_workflow_roles(client, headers, current)
    stale = client.put("/api/v2/model-settings", headers={
        **headers, "Idempotency-Key": "workflow-role-stale",
    }, json={
        "expected_config_hash": current["config_hash"],
        "action": "workflow_roles",
        "value": assignment,
    })
    assert stale.status_code == 409


def test_legacy_roles_remain_until_an_explicit_workflow_choice(client, tmp_path, monkeypatch):
    from app.v2.model_roles import for_role

    configure_workflow_models(tmp_path, monkeypatch)
    private = tmp_path / "workflow-private-roles.json"
    private.write_text(json.dumps({
        "profiles": [_profile("legacy-review", "legacy-qwen", "qwen")],
        "roles": {"review": "legacy-review", "blind_solver": "legacy-review"},
    }), encoding="utf-8")
    before = client.get("/api/v2/model-settings", headers=owner(client)).json()
    assert before["workflow_role_assignments"] == {}
    assert before["workflow_roles"]["quality"]["profile_id"] == "legacy-review"
    assert for_role("review").llm_model == "legacy-qwen"


def test_workflow_metrics_keep_wall_time_and_provider_time_separate():
    from app.v2.review_contract import summarize_calls

    calls = [
        {"stage": "checking", "workflow_role": "quality", "status": "settled",
         "current_run": True, "started_at": 10.0, "finished_at": 20.0,
         "elapsed_seconds": 10.0, "usage": {"total_tokens": 10}, "price": None},
        {"stage": "item_reviewing", "workflow_role": "quality", "status": "settled",
         "current_run": True, "started_at": 12.0, "finished_at": 18.0,
         "elapsed_seconds": 6.0, "usage": {"total_tokens": 8}, "price": None},
    ]
    metrics = summarize_calls(calls)
    quality = metrics["workflow_distribution"]["quality"]
    assert quality["calls"] == 2
    assert quality["wall_seconds"] == 10.0
    assert quality["elapsed_seconds"] == 16.0
    assert quality["tokens_known"] == 18
