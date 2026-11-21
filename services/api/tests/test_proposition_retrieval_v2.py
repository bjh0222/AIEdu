"""Retrieval safety contracts: scope, provenance, whole-item budgets and fallback.

All databases are isolated in-memory test databases. Embeddings are mocked;
these tests cannot start a real service or call a real model.
"""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app import models
from app.db import Base
from app.services import proposition_retrieval as R


@pytest.fixture
def db(monkeypatch):
    def prohibited_network(*args, **kwargs):
        raise AssertionError("Real embedding requests are prohibited in tests")

    monkeypatch.setattr(R.httpx, "post", prohibited_network)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def principle(db, id="p1", **values):
    defaults = dict(id=id, domain_key="math_data", statement="为关键步骤设计可区分的干扰项",
                    title=f"经验{id}", status="confirmed", version=1, confidence=0.9,
                    evidence_family_count=2, mechanism="干扰项对应真实误解",
                    guidance="通过干扰项区分不同理解", applicability="选择题",
                    scope="{}", scope_knowledge_points="[]", counterexamples="[]")
    defaults.update(values)
    row = models.DesignPrinciple(**defaults)
    db.add(row)
    db.commit()
    return row


def context(**values):
    return {"domain_key": "math_data", "knowledge_points": ["导数"], "question_types": ["选择题"],
            "task_kind": "section", **values}


def retrieve(db, ctx=None, **config):
    return R.retrieve_guidance(db, context() if ctx is None else ctx,
                              {"include_examples": False, **config}, record=False)


def framework(db, standards, **values):
    row = models.EvaluationFramework(title="测试评价体系", domain_key="general", status="active",
                                     standards=json.dumps(standards, ensure_ascii=False), **values)
    db.add(row)
    db.commit()
    return row


def question(db, id="q1", **values):
    row = models.PropositionQuestion(id=id, domain_key="math_data", raw_stem=f"题目{id}：计算导数。",
                                     knowledge_points='["导数"]', question_type="选择题",
                                     parse_status="ready", **values)
    db.add(row)
    db.commit()
    return row


def embedding_config(**values):
    return {"embedding_enabled": True, "embedding_base_url": "http://127.0.0.1:9911/v1",
            "embedding_model": "local-test", **values}


def mock_embeddings(monkeypatch, vectors=None, callback=None):
    calls = []

    def post(url, *, json, headers, timeout):
        if callback:
            callback()
        calls.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        outputs = vectors(json["input"]) if vectors else [[1.0, 0.0] for _ in json["input"]]
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": vector} for i, vector in enumerate(outputs)
        ]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(R.httpx, "post", post)
    return calls


def test_states_and_auto_accept_gate(db):
    principle(db, "manual")
    principle(db, "automatic", status="auto_accepted", approval_source="automatic", confidence=0.8)
    principle(db, "one-family", status="auto_accepted", approval_source="automatic", evidence_family_count=1)
    principle(db, "low-confidence", status="auto_accepted", approval_source="automatic", confidence=0.79)
    principle(db, "negative-auto", status="auto_accepted", approval_source="automatic", negative_refs='[{"question_id":"bad"}]')
    principle(db, "proposed", status="proposed")
    principle(db, "rejected", status="rejected")
    # Distinct complete content avoids exact duplicate suppression.
    automatic = db.get(models.DesignPrinciple, "automatic")
    automatic.statement = "自动经验：让条件变化检验理解"
    db.commit()
    out = retrieve(db)
    assert {p["id"] for p in out["selected"]} == {"manual", "automatic"}
    assert out["stats"]["state_filtered"] == 5
    assert out["stats"]["confirmed_total"] == 1
    assert "人工确认 confirmed" in out["text"] and "自动采纳 auto_accepted" in out["text"]
    assert "软性生成指导" in out["text"] and "由人核实" not in out["text"]


def test_auto_accept_can_be_disabled_without_removing_confirmed(db):
    principle(db, "a", status="auto_accepted", approval_source="automatic")
    principle(db, "b", statement="人工确认仍可使用")
    out = retrieve(db, auto_accept=False)
    assert [p["id"] for p in out["selected"]] == ["b"]
    assert out["stats"]["state_filtered"] == 1


def test_general_requires_classification_and_scope_compatibility(db):
    principle(db, "universal", domain_key="general", principle_type="general", statement="题面不能泄漏答案")
    principle(db, "not-general", domain_key="general", principle_type="discipline", statement="定积分计算规则")
    principle(db, "other-domain", domain_key="computer_cli", principle_type="general", statement="Python运行规则")
    principle(db, "scoped-general", domain_key="general", principle_type="general", statement="Python专有规则",
              scope='{"domains":["computer_cli"]}')
    out = retrieve(db)
    assert [p["id"] for p in out["selected"]] == ["universal"]
    assert out["stats"]["scope_filtered"] == 3


def test_missing_knowledge_does_not_inject_specific_experiences(db):
    principle(db, "legacy")
    principle(db, "specialised", statement="导数专属", scope_knowledge_points='["导数"]')
    principle(db, "new-specific", statement="新导数专属", scope='{"knowledge_points":["导数"]}')
    out = retrieve(db, context(knowledge_points=[]))
    assert [p["id"] for p in out["selected"]] == ["legacy"]
    assert out["stats"]["scope_filtered"] == 2
    assert out["stats"]["dropped"] == 0


@pytest.mark.parametrize("term", ["derivative", "计算导数", "differentiation"])
def test_chinese_english_alias_and_contained_keyword(db, term):
    principle(db, scope='{"knowledge_points":["导数"]}')
    out = retrieve(db, context(knowledge_points=[term]))
    assert out["stats"]["injected"] == 1
    assert out["backend"] == "lexical"
    assert "embedding" not in out["selected"][0]["reason"]


def test_user_maintained_concept_alias(db):
    db.add(models.Concept(title="自定义知识点", domain_key="math_data", aliases='["custom terminology"]'))
    db.commit()
    principle(db, scope='{"knowledge_points":["自定义知识点"]}')
    assert retrieve(db, context(knowledge_points=["custom terminology"]))["stats"]["injected"] == 1


def test_scope_question_type_branch_and_explicit_exclusion(db):
    scope = json.dumps({"knowledge_points": ["导数"], "question_types": ["multiple choice"],
                        "branch": "微积分", "difficulty": "advanced", "purpose": "期末考核",
                        "task_kinds": ["section"], "exclude": {"knowledge_points": ["链式法则"]}}, ensure_ascii=False)
    principle(db, scope=scope)
    matching = context(branch="calculus", difficulty="advanced", purpose="期末考核")
    assert retrieve(db, matching)["stats"]["injected"] == 1
    assert retrieve(db, {**matching, "knowledge_points": ["导数", "chain rule"]})["stats"]["scope_filtered"] == 1
    assert retrieve(db, {**matching, "question_types": ["proof"]})["stats"]["scope_filtered"] == 1


def test_learner_and_branch_unknown_are_explicit_conditions_not_silent_failures(db):
    principle(db, scope='{"knowledge_points":["导数"],"learner":"本科生","branch":"微积分"}')
    out = retrieve(db, context(learner="了解极限，求导练习较少"))
    assert out["stats"]["injected"] == 1
    assert "learner:unverified_context" in out["selected"][0]["reason"]
    assert "branch:unverified_context" in out["selected"][0]["reason"]
    assert "learner=本科生" in out["text"] and "仅当所列条件成立" in out["text"]
    assert retrieve(db, context(learner="高中生", branch="微积分"))["stats"]["scope_filtered"] == 1


def test_unknown_cross_domain_condition_does_not_justify_general_use(db):
    principle(db, domain_key="general", principle_type="general", scope='{"learner":"本科生","branch":"微积分"}')
    assert retrieve(db)["stats"]["scope_filtered"] == 1


def test_malformed_or_unsupported_scope_fails_closed(db):
    principle(db, "broken", scope="{broken json")
    principle(db, "unknown", scope='{"unknown_condition":"specific"}')
    assert retrieve(db)["stats"]["scope_filtered"] == 2


def test_relevance_then_confidence_then_independent_families(db):
    principle(db, "generic", statement="通用规律", confidence=1.0, evidence_family_count=100)
    principle(db, "relevant", statement="专属规律", scope='{"knowledge_points":["导数"]}', confidence=0.1)
    principle(db, "many-repeats", statement="重复证据规律", confidence=0.8, evidence_count=1000, evidence_family_count=1)
    principle(db, "independent", statement="独立证据规律", confidence=0.8, evidence_count=2, evidence_family_count=2)
    ids = [p["id"] for p in retrieve(db)["selected"]]
    assert ids == ["relevant", "generic", "independent", "many-repeats"]


def test_exact_duplicates_only_do_not_merge_opposite_or_similar_rules(db):
    principle(db, "first", statement="令 x>0")
    principle(db, "duplicate", statement="令 x>0", confidence=0.7)
    principle(db, "opposite", statement="令 x<0")
    principle(db, "similar", statement="令 x>=0")
    out = retrieve(db)
    assert out["stats"]["deduplicated"] == 1
    assert out["stats"]["injected"] == 3
    assert db.query(models.DesignPrinciple).count() == 4


def test_confirmed_conflicts_are_excluded_and_not_counted_as_budget_drops(db):
    principle(db, "a", statement="采用A")
    principle(db, "b", statement="禁止A")
    db.add(models.DesignPrincipleLink(source_id="a", target_id="b", relation="conflicts", status="confirmed"))
    db.commit()
    out = retrieve(db)
    assert out["stats"]["conflict_excluded"] == 2
    assert out["stats"]["dropped"] == 0 and out["selected"] == []


def test_active_framework_scope_and_strength_are_preserved(db):
    active = framework(db, [
        {"id": "hard", "name": "唯一性", "rule": "选择题答案唯一", "scope": {}, "strength": "hard", "priority": 3},
        {"id": "python", "rule": "代码可执行", "scope": {"domains": ["computer_cli"]}, "strength": "core"},
        {"id": "math", "rule": "数学条件充分", "scope": {"domains": ["math_data"]}, "strength": "core"},
    ])
    old = framework(db, [{"id": "old", "rule": "不应出现旧标准"}])
    old.status = "archived"
    db.commit()
    out = retrieve(db, max_experiences=0)
    assert {s["id"] for s in out["standards"]} == {"hard", "math"}
    assert out["standards"][0]["framework_id"] == active.id
    assert "domains=math_data" in out["text"] and "hard 是硬边界" in out["text"]
    assert "core 是核心标准" in out["text"] and "代码可执行" not in out["text"]


def test_exact_budget_includes_headings_and_does_not_truncate(db):
    principle(db, statement="完整语义，不能截断最后的关键否定条件。")
    full = retrieve(db)
    assert full["chars"] == len(full["text"])
    exact = retrieve(db, budget_chars=full["chars"])
    small = retrieve(db, budget_chars=full["chars"] - 1)
    assert exact["text"] == full["text"]
    assert small["text"] == "" and small["stats"]["dropped"] == 1


def test_budget_and_selection_limit_have_separate_counts(db):
    principle(db, "large", statement="超长经验" * 1000, confidence=1)
    principle(db, "fits", statement="适合短经验", confidence=0.9)
    principle(db, "limit", statement="超过条数限制", confidence=0.8)
    out = retrieve(db, budget_chars=1000, max_experiences=1)
    assert [p["id"] for p in out["selected"]] == ["fits"]
    assert out["stats"]["dropped"] == 1 and out["stats"]["not_selected"] == 1


def test_zero_budget_disables_complex_tasks_and_network(db):
    principle(db)
    out = retrieve(db, context(complexity="complex"), budget_chars=0, **embedding_config())
    assert out["budget_chars"] == 0 and out["text"] == ""
    assert out["stats"]["embedding_requests"] == 0 and out["stats"]["dropped"] == 1


def test_lazy_config_and_explicit_override(db):
    principle(db)
    db.add(models.PropositionConfig(id="default", settings_json='{"budget_chars":0}'))
    db.commit()
    assert R.retrieve_guidance(db, context(), record=False)["text"] == ""
    assert R.retrieve_guidance(db, context(), {"budget_chars": 3000}, record=False)["text"]


def test_examples_need_explicit_use_and_keep_negative_polarity(db):
    good, bad = question(db, "good"), question(db, "bad")
    row = principle(db, positive_refs=json.dumps([{"question_id": good.id}]),
                    negative_refs=json.dumps([{"question_id": bad.id}]))
    out = retrieve(db, include_examples=True)
    assert out["examples"] == []
    row.scope = '{"requires_examples":true}'
    db.commit()
    out = retrieve(db, include_examples=True, examples_budget_chars=2000)
    assert {(e["question_id"], e["polarity"]) for e in out["examples"]} == {("good", "positive"), ("bad", "negative")}
    assert "反例（展示限制，禁止当作正向做法）" in out["text"]
    assert out["chars"] == len(out["text"]) <= out["budget_chars"]


def test_negative_reference_never_reappears_as_a_positive_example(db):
    question(db)
    principle(db, "a", statement="经验A", scope='{"requires_examples":true}', positive_refs='[{"question_id":"q1"}]')
    principle(db, "b", statement="经验B", scope='{"requires_examples":true}', negative_refs='[{"question_id":"q1"}]')
    out = retrieve(db, include_examples=True, examples_budget_chars=2000)
    assert [e["polarity"] for e in out["examples"]] == ["negative"]
    assert out["stats"]["example_conflict_excluded"] == 1


def test_example_budget_has_separate_drop_count(db):
    question(db, raw_explanation="冗长解答" * 1000)
    principle(db, scope='{"requires_examples":true}', positive_refs='[{"question_id":"q1"}]')
    out = retrieve(db, include_examples=True)
    assert out["stats"]["injected"] == 1 and out["stats"]["dropped"] == 0
    assert out["examples"] == [] and out["stats"]["examples_dropped"] == 1


def test_trace_is_flush_only_and_preview_is_read_only(db):
    principle(db, version=7)
    preview = R.retrieve_guidance(db, context(), record=False)
    assert db.query(models.PropositionGuidanceTrace).count() == 0
    actual = R.retrieve_guidance(db, context(tutorial_id="tutorial-1", section_id="s1"))
    assert actual["text"] == preview["text"] and actual["selected"] == preview["selected"]
    trace = db.get(models.PropositionGuidanceTrace, actual["trace_id"])
    assert json.loads(trace.result)["selected"][0]["version"] == 7
    assert json.loads(trace.result)["trace_id"] == trace.id
    db.rollback()
    assert db.query(models.PropositionGuidanceTrace).count() == 0


def test_embeddings_cache_query_and_documents_with_endpoint_model_and_revision(db, monkeypatch):
    row = principle(db)
    calls = mock_embeddings(monkeypatch)
    config = embedding_config()
    first = R.retrieve_guidance(db, context(), config)
    db.commit()
    second = R.retrieve_guidance(db, context(), config, record=False)
    assert first["text"] == second["text"] and first["backend"] == "embedding"
    assert first["stats"]["embedding_requests"] == 2 and second["stats"]["embedding_requests"] == 0
    assert len(calls) == 2
    changed = R.retrieve_guidance(db, context(), embedding_config(embedding_base_url="http://127.0.0.1:9912/v1"))
    db.commit()
    assert changed["stats"]["embedding_requests"] == 2
    row.version = 2
    db.commit()
    revised = R.retrieve_guidance(db, context(), config)
    db.commit()
    assert revised["stats"]["embedding_requests"] == 1
    assert len(calls) == 5


def test_embedding_network_finishes_before_any_cache_or_trace_write(db, monkeypatch):
    principle(db)
    writes = []

    def observe(conn, cursor, statement, parameters, execution_context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    event.listen(db.bind, "before_cursor_execute", observe)
    try:
        calls = mock_embeddings(monkeypatch, callback=lambda: pytest.fail("write lock before network") if writes else None)
        out = R.retrieve_guidance(db, context(), embedding_config())
        assert len(calls) == 2 and writes and out["trace_id"]
    finally:
        event.remove(db.bind, "before_cursor_execute", observe)


def test_embedding_candidate_and_request_counts_are_bounded(db, monkeypatch):
    for i in range(10):
        principle(db, str(i), statement=f"不同经验{i}")
    calls = mock_embeddings(monkeypatch)
    out = retrieve(db, **embedding_config(embedding_candidate_limit=3))
    assert len(calls) == 2
    assert out["stats"]["embedding_inputs"] == 4
    assert len(calls[1]["body"]["input"]) == 3
    assert db.query(models.PropositionEmbedding).count() == 0


def test_semantic_scope_match_can_rescue_nonlexical_knowledge(db, monkeypatch):
    principle(db, scope='{"knowledge_points":["判断局部变化率"]}')
    mock_embeddings(monkeypatch)
    out = retrieve(db, context(knowledge_points=["derivative"]), **embedding_config())
    assert out["stats"]["injected"] == 1 and out["backend"] == "embedding"
    assert "embedding_similarity" in out["selected"][0]["reason"]


def test_unconfigured_semantic_backend_never_claims_matching(db):
    principle(db)
    out = retrieve(db, embedding_enabled=True)
    assert out["backend"] == "lexical" and out["stats"]["embedding_reason"] == "not_configured"
    assert out["warnings"] and "embedding" not in out["selected"][0]["reason"]


def test_timeout_downgrades_without_exposing_key_or_error_body(db, monkeypatch):
    principle(db)

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("private-key secret-query-text")

    monkeypatch.setattr(R.httpx, "post", timeout)
    out = retrieve(db, **embedding_config(embedding_api_key="private-key"))
    assert out["backend"] == "lexical" and out["stats"]["embedding_reason"] == "failed:timeout"
    assert "private-key" not in json.dumps(out) and out["stats"]["injected"] == 1


def test_embedding_invalid_dimensions_downgrade_all_results(db, monkeypatch):
    principle(db)
    calls = []

    def vectors(texts):
        calls.append(texts)
        return [[1, 0]] if len(calls) == 1 else [[1, 0, 1] for _ in texts]

    mock_embeddings(monkeypatch, vectors=vectors)
    out = retrieve(db, **embedding_config())
    assert out["backend"] == "lexical" and out["stats"]["embedding_reason"] == "failed:ValueError"
    assert db.query(models.PropositionEmbedding).count() == 0
    assert all("embedding_similarity" not in p["reason"] for p in out["selected"])


def test_invalid_cached_vector_is_repaired_once(db, monkeypatch):
    principle(db)
    calls = mock_embeddings(monkeypatch)
    config = embedding_config()
    R.retrieve_guidance(db, context(), config)
    db.commit()
    cached = db.query(models.PropositionEmbedding).filter_by(source_type="principle").one()
    cached.vector = "[0,0]"
    db.commit()
    repaired = R.retrieve_guidance(db, context(), config)
    db.commit()
    next_result = R.retrieve_guidance(db, context(), config, record=False)
    assert repaired["stats"]["embedding_requests"] == 1
    assert next_result["stats"]["embedding_requests"] == 0 and len(calls) == 3


def test_large_finite_embedding_components_do_not_produce_nan(db, monkeypatch):
    principle(db)
    mock_embeddings(monkeypatch, vectors=lambda texts: [[1e308, 0.0] for _ in texts])
    out = retrieve(db, **embedding_config())
    assert out["backend"] == "embedding" and out["selected"][0]["score"] > 0
    assert "NaN" not in json.dumps(out)


def test_omitted_hard_standard_has_an_explicit_budget_warning(db):
    framework(db, [{"id": "boundary", "rule": "不可省略的硬边界" * 100, "strength": "hard"}])
    out = retrieve(db, budget_chars=100)
    assert out["standards"] == [] and out["stats"]["standards_dropped"] == 1
    assert any(w.startswith("hard_standard_budget_omitted:") for w in out["warnings"])


@pytest.mark.parametrize("stored,requested", [
    ("computer_cli", "cs_it"), ("cs_it", "computer_cli"),
    ("math_data", "math_logic"), ("math_logic", "math_data"),
])
def test_legacy_subject_keys_match_new_goals_without_cross_subject_injection(db, stored, requested):
    principle(db, "matching", domain_key=stored, scope=json.dumps({"domains": [stored], "branch": "自定义分支"}))
    principle(db, "wrong-branch", domain_key=stored, scope='{"branch":"其他分支"}')
    principle(db, "wrong-subject", domain_key="physics_astronomy")
    out = retrieve(db, context(domain_key=requested, branch="自定义分支"))
    assert [item["id"] for item in out["selected"]] == ["matching"]
    assert out["stats"]["scope_filtered"] == 1


def test_legacy_computer_framework_and_examples_follow_new_goal_subject(db):
    principle(db, "p-python", domain_key="computer_cli", scope='{"requires_examples":true}', positive_refs='[{"question_id":"q-python"}]')
    q = question(db, "q-python", branch="python", analysis_status="analysed")
    q.domain_key = "computer_cli"
    db.add(models.EvaluationFramework(title="Python 标准", domain_key="computer_cli", status="active",
                                    standards='[{"id":"python-standard","rule":"应可执行","scope":{"domains":["computer_cli"]}}]'))
    db.commit()
    out = retrieve(db, context(domain_key="cs_it", branch="python"), include_examples=True)
    assert [standard["id"] for standard in out["standards"]] == ["python-standard"]
    assert [example["question_id"] for example in out["examples"]] == ["q-python"]
