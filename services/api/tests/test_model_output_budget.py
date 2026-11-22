"""Output limits come from profile override and sourced capacity, never model-name guesses."""
from types import SimpleNamespace

import pytest


def profile(limit=None):
    return SimpleNamespace(
        llm_base_url="provider://fixture",
        llm_model="fixture-model",
        llm_output_limit_tokens=limit,
    )


def test_profile_limit_cannot_exceed_sourced_capacity_and_null_uses_capacity():
    from app.v2.capacity import planned_output_tokens
    from app.v2.contracts import DomainError

    capacity = {
        "max_output_tokens": 32768,
        "output_limit_parameter": "max_completion_tokens",
        "source_uri_or_config": "fixture-capacity",
    }
    assert planned_output_tokens(profile(None), 65536, capacity) == {
        "requested_output_tokens": 65536,
        "user_output_limit_tokens": None,
        "reserved_output_tokens": 32768,
        "capacity_max_output_tokens": 32768,
        "output_limit_parameter": "max_completion_tokens",
        "capacity_source": "fixture-capacity",
    }
    assert planned_output_tokens(profile(12000), 65536, capacity)["reserved_output_tokens"] == 12000
    with pytest.raises(DomainError, match="MODEL_OUTPUT_LIMIT_INVALID"):
        planned_output_tokens(profile(40000), 65536, capacity)


def test_unknown_capacity_rejects_every_dispatch():
    from app.v2.capacity import planned_output_tokens
    from app.v2.contracts import DomainError

    with pytest.raises(DomainError, match="CAPACITY_UNKNOWN"):
        planned_output_tokens(profile(None), None, None)


def test_output_limit_is_persisted_in_full_profile_and_public_config(client):
    from app.model_settings import read_config

    response = client.post("/api/settings/models", json={
        "id": "budget",
        "name": "预算配置",
        "base_url": "https://budget.example/v1",
        "model": "budget-model",
        "api_key": "budget-secret",
        "output_limit_tokens": 12000,
    })
    assert response.status_code == 200, response.text
    profile_value = next(row for row in response.json()["profiles"] if row["id"] == "budget")
    assert profile_value["output_limit_tokens"] == 12000
    assert "budget-secret" not in response.text
    assert next(row for row in read_config()["profiles"]
                if row["id"] == "budget")["output_limit_tokens"] == 12000

    changed = {
        "id": "budget",
        "name": "预算配置",
        "base_url": "https://budget.example/v1",
        "model": "budget-model",
    }
    assert client.post("/api/settings/models", json=changed).status_code == 200
    assert next(row for row in read_config()["profiles"]
                if row["id"] == "budget")["output_limit_tokens"] == 12000

    changed["output_limit_tokens"] = None
    assert client.post("/api/settings/models", json=changed).status_code == 200
    assert next(row for row in read_config()["profiles"]
                if row["id"] == "budget")["output_limit_tokens"] is None
