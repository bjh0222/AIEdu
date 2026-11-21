# -*- coding: utf-8 -*-
"""R2-1：embedding 健康探针、降级解释与真实线代作用域夹具。

来源：运行记录-两套线代试卷-20261108-修复后/监控与比较报告.md §4/§7 第 1 行——
本次语义检索因 HTTPStatusError 降级为 lexical，第一卷经验覆盖为 0。本测试钉死：
①健康探针可解释（endpoint/model/HTTP 状态/维度/耗时），只读、不启动 Ollama；
②降级原因带 endpoint/model 而非仅异常类名；
③真实线代作用域夹具：合法同义词命中、细粒度机制分开、关系边界不混淆。
全部零模型、零真实网络（httpx 替身）。
"""
from __future__ import annotations

import json

import httpx

from app.services.proposition_retrieval import (
    LINALG_ALIAS_VERSION,
    _ALIASES,
    _scope_match,
    _similar,
    embedding_health,
    normalize_question_form,
)


def _cfg(**overrides):
    base = {
        "embedding_enabled": True,
        "embedding_base_url": "http://127.0.0.1:11434/v1",
        "embedding_model": "bge-m3",
        "embedding_timeout": 1.5,
        "embedding_api_key": "",
    }
    base.update(overrides)
    return base


def _response(status: int, payload: dict, url: str) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


# ---------------------------------------------------------------- 健康探针


def test_health_disabled_never_probes(monkeypatch):
    def _forbidden(*args, **kwargs):  # pragma: no cover - 断言不被调用
        raise AssertionError("disabled 状态不得发起网络探测")

    monkeypatch.setattr("app.services.proposition_retrieval.httpx.post", _forbidden)
    result = embedding_health(_cfg(embedding_enabled=False))
    assert result["reason"] == "disabled"
    assert result["will_use_lexical"] is True
    assert "词法" in result["degraded_explanation"]


def test_health_not_configured_explains_no_auto_start():
    result = embedding_health(_cfg(embedding_model=""))
    assert result["configured"] is False
    assert result["reason"] == "not_configured"
    assert "不会自动启动 Ollama" in result["degraded_explanation"]


def test_health_ok_reports_dimension_and_latency(monkeypatch):
    def fake_post(url, **kwargs):
        return _response(200, {"data": [{"index": 0, "embedding": [0.1, -0.2, 0.3]}]}, url)

    monkeypatch.setattr("app.services.proposition_retrieval.httpx.post", fake_post)
    result = embedding_health(_cfg())
    assert result["reachable"] is True
    assert result["model_ok"] is True
    assert result["dimension"] == 3
    assert result["reason"] == "ok"
    assert result["will_use_lexical"] is False
    assert isinstance(result["latency_ms"], int)


def test_health_http_401_explains_endpoint_and_model(monkeypatch):
    def fake_post(url, **kwargs):
        return _response(401, {"error": "unauthorized"}, url)

    monkeypatch.setattr("app.services.proposition_retrieval.httpx.post", fake_post)
    result = embedding_health(_cfg())
    assert result["reason"] == "http_401"
    assert result["model_ok"] is False
    assert "HTTP 401" in result["degraded_explanation"]
    assert "127.0.0.1:11434" in result["degraded_explanation"]
    assert "bge-m3" in result["degraded_explanation"]


def test_health_connection_error_explains_unreachable(monkeypatch):
    def fake_post(url, **kwargs):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    monkeypatch.setattr("app.services.proposition_retrieval.httpx.post", fake_post)
    result = embedding_health(_cfg())
    assert result["reachable"] is False
    assert "不可达" in result["degraded_explanation"]
    assert "词法" in result["degraded_explanation"]


def test_health_never_returns_api_key(monkeypatch):
    def fake_post(url, **kwargs):
        return _response(200, {"data": [{"index": 0, "embedding": [1.0]}]}, url)

    monkeypatch.setattr("app.services.proposition_retrieval.httpx.post", fake_post)
    result = embedding_health(_cfg(embedding_api_key="sk-secret-123"))
    assert "sk-secret-123" not in json.dumps(result, ensure_ascii=False)


def test_health_api_route_returns_explainable_payload(client):
    response = client.get("/api/proposition/embedding-health")
    assert response.status_code == 200
    body = response.json()
    # 默认配置未启用 embedding：探针不发网络请求，返回可解释的降级说明。
    assert body["enabled"] is False
    assert body["reason"] == "disabled"
    assert body["will_use_lexical"] is True


# ---------------------------------------------------------------- 真实线代作用域夹具


def test_linalg_legal_synonyms_match():
    """合法同义词（已证明同义）应命中；《矩阵的秩》与「秩」不得因二元组交集为空而漏匹配。"""
    pairs = [
        ("矩阵的秩", "秩"),
        ("子矩阵的秩的下界", "子矩阵秩下界"),
        ("相似", "相似变换"),
        ("伴随矩阵", "A的伴随矩阵"),
    ]
    for left, right in pairs:
        assert _similar(left, right, _ALIASES) >= 0.85, (left, right)


def test_linalg_relation_boundaries_no_overmatch():
    """相抵/相似/合同/等价是不同关系：不得因「同属线代」被当作同义词。"""
    pairs = [("相抵", "相似"), ("合同", "相似"), ("等价", "相抵")]
    for left, right in pairs:
        assert _similar(left, right, _ALIASES) < 0.45, (left, right)


def test_linalg_scope_match_uses_synonym_without_lowering_threshold():
    scope = {"knowledge_points": ["矩阵的秩"]}
    context = {"knowledge_points": ["秩"]}
    allowed, score, reasons, evidence = _scope_match(scope, context, _ALIASES)
    assert allowed is True
    assert "knowledge_points:matched" in reasons
    assert evidence["knowledge_points"]["verdict"] == "matched"
    # 关系边界在作用域匹配中同样生效：相抵的范围不得放行到相似任务。
    allowed2, *_ = _scope_match({"knowledge_points": ["相抵"]},
                                {"knowledge_points": ["相似"]}, _ALIASES)
    assert allowed2 is False


def test_question_form_mechanism_is_fine_grained():
    """题型归一后「条件变化」与「结构迁移」是不同的考查机制，不得互相冒充。"""
    changed = normalize_question_form("证明题（条件变化）")
    transferred = normalize_question_form("证明题（结构迁移）")
    assert changed["canonical"] == transferred["canonical"] == "proof"
    assert changed["mechanisms"] == ["condition_change"]
    assert transferred["mechanisms"] == ["structure_transfer"]
    assert changed["map_version"] == "question-form.v1"
    assert LINALG_ALIAS_VERSION == "linear-algebra-aliases.v1"
