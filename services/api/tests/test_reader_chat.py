from __future__ import annotations

import httpx
from app.db import SessionLocal
from app.models import ChatMessage, ModelCallLog


def save_profile(client, profile_id: str, model: str):
    response = client.post("/api/settings/models", json={
        "id": profile_id,
        "name": profile_id.title(),
        "base_url": "https://model.example/v1",
        "model": model,
        "api_key": "secret-test-key",
        "model_family": "deepseek" if profile_id.startswith("generation") else "chat-family",
    })
    assert response.status_code == 200, response.text


def activate(client, profile_id: str):
    response = client.post("/api/settings/models/activate", json={"active_id": profile_id})
    assert response.status_code == 200, response.text


def mock_complete(monkeypatch, captured: dict):
    def complete_json(self, system: str, user: str):
        captured["model"] = self.model
        captured["system"] = system
        captured["user"] = user
        return {"answer": "这是 mocked 回答"}, 12, {
            "prompt_tokens": 18,
            "completion_tokens": 7,
            "total_tokens": 25,
            "usage_source": "provider",
        }
    monkeypatch.setattr("app.llm.provider.OpenAICompatibleProvider.complete_json", complete_json)


def chat_payload() -> dict:
    return {
        "task_id": "task-fixture",
        "artifact_id": "version-fixture",
        "question": "解释这一章的核心概念",
        "context_scope": "current_unit",
        "context_units": [
            {"unit_id": "unit-1", "title": "当前章节", "body": "这是当前章节完整内容。"}
        ],
        "selected_quote": "核心概念",
    }


def test_reader_chat_defaults_to_generation_role_and_records_provider_receipt(client, monkeypatch):
    save_profile(client, "generation", "generation-model")
    activate(client, "generation")
    captured: dict = {}
    mock_complete(monkeypatch, captured)

    response = client.post("/api/chat", json=chat_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["demo"] is False
    assert body["model_call"]["model"] == "generation-model"
    assert body["model_call"]["usage"]["usage_source"] == "provider"
    assert captured["model"] == "generation-model"
    assert "这是当前章节完整内容。" in captured["user"]
    assert "current_unit" in captured["user"]
    assert "核心概念" in captured["user"]

    with SessionLocal() as db:
        log = db.query(ModelCallLog).filter(ModelCallLog.feature == "chat").one()
        assert log.model == "generation-model"
        assert log.provider == "openai-compatible"
        assert log.outcome == "ok"
        assert log.total_tokens == 25
        assert log.usage_source == "provider"
        messages = db.query(ChatMessage).order_by(ChatMessage.created_at).all()
        assert [m.role for m in messages] == ["user", "assistant"]
        assert all(m.task_id == "task-fixture" for m in messages)
        assert all(m.section_id == "unit-1" for m in messages)


def test_saved_chat_role_uses_exact_profile_without_silent_replacement(client, monkeypatch):
    save_profile(client, "generation", "generation-model")
    save_profile(client, "chat-profile", "chat-model")
    activate(client, "generation")
    response = client.put("/api/settings/models/roles", json={"profile_id": "chat-profile"})
    assert response.status_code == 200, response.text

    from app.model_settings import read_config
    assert read_config()["model_roles"]["chat"] == "chat-profile"

    captured: dict = {}
    mock_complete(monkeypatch, captured)
    response = client.post("/api/chat", json=chat_payload())
    assert response.status_code == 200, response.text
    assert response.json()["model_call"]["model"] == "chat-model"
    assert captured["model"] == "chat-model"


def test_chat_role_endpoint_rejects_missing_profile(client):
    response = client.put("/api/settings/models/roles", json={"profile_id": "missing"})
    assert response.status_code == 422
    from app.model_settings import read_config
    assert "chat" not in read_config().get("model_roles", {})
