"""POST /api/v2/model-settings/discover：只读供应商模型列表探测（无生成调用）。"""
from __future__ import annotations

import pytest

from test_task_run_v2 import owner


@pytest.fixture()
def fake_provider(monkeypatch):
    captured: dict = {}

    class FakeProvider:
        def __init__(self, settings_obj) -> None:
            captured["settings"] = settings_obj

        def list_models(self) -> list[str]:
            return ["glm-5.3-flash", "deepseek-v4-pro"]

    from app.api import model_settings
    monkeypatch.setattr(model_settings, "OpenAICompatibleProvider", FakeProvider)
    return captured


def test_discover_with_explicit_key(client, fake_provider):
    headers = owner(client)
    r = client.post("/api/v2/model-settings/discover",
                    json={"base_url": "https://api.example.com/v1", "api_key": "sk-test"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"models": ["glm-5.3-flash", "deepseek-v4-pro"]}
    assert fake_provider["settings"].llm_api_key == "sk-test"


def test_discover_reuses_saved_profile_key(client, fake_provider):
    headers = owner(client)
    current = client.get("/api/v2/model-settings").json()
    body = {"expected_config_hash": current["config_hash"], "action": "profile",
            "value": {"id": "saved-a", "name": "已保存A", "base_url": "https://api.example.com",
                      "model": "model-a", "api_key": "sk-saved"}}
    saved = client.put("/api/v2/model-settings", json=body,
                       headers=headers | {"Idempotency-Key": "discover-save"})
    assert saved.status_code == 200, saved.text
    r = client.post("/api/v2/model-settings/discover",
                    json={"base_url": "https://api.example.com", "profile_id": "saved-a"}, headers=headers)
    assert r.status_code == 200, r.text
    assert fake_provider["settings"].llm_api_key == "sk-saved"


def test_discover_rejects_mismatched_profile_reuse(client, fake_provider):
    headers = owner(client)
    current = client.get("/api/v2/model-settings").json()
    body = {"expected_config_hash": current["config_hash"], "action": "profile",
            "value": {"id": "saved-b", "name": "已保存B", "base_url": "https://other.example.com",
                      "model": "model-b", "api_key": "sk-other"}}
    saved = client.put("/api/v2/model-settings", json=body,
                       headers=headers | {"Idempotency-Key": "discover-save-b"})
    assert saved.status_code == 200, saved.text
    r = client.post("/api/v2/model-settings/discover",
                    json={"base_url": "https://api.example.com", "profile_id": "saved-b"}, headers=headers)
    assert r.status_code == 404, r.text
    assert r.json()["code"] == "MODEL_PROFILE_NOT_FOUND"


def test_discover_rejects_bad_url(client):
    headers = owner(client)
    r = client.post("/api/v2/model-settings/discover", json={"base_url": "ftp://nope"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "MODEL_PROFILE_INVALID"