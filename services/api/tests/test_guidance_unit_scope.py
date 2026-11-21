"""A shared book query must not inject a chapter excerpt into every chapter."""
from app.v2.knowledge import evidence_bundle


def test_curated_excerpt_respects_source_unit_before_lexical_ranking():
    card = {"id":"source-card", "version":2, "status":"confirmed", "domain":"math_logic", "branch":"基础代数",
        "mechanism":"待定系数 线性方程组", "guidance_eligibility":True, "evidence_hash":"hash", "source":{},
        "unit_scope":"第 1 章 线性方程组"}
    args = dict(domain="math_logic", branch="基础代数", query="基础代数 线性方程组 集合", call_id="call",
        stage="generating", unit_id="unit", guidance_enabled=True)
    same = evidence_bundle([card], **args, unit_title="第1章 线性方程组")
    assert same["selected"][0]["id"] == card["id"]
    other = evidence_bundle([card], **args, unit_title="第 2 章 集合与映射")
    assert not other["selected"]
    assert other["excluded"][0]["reason"] == "outside_curated_source_unit"
    missing = evidence_bundle([card], **args)
    assert not missing["selected"]


def test_legacy_frozen_excerpt_gets_scope_without_mutating_context(monkeypatch):
    from copy import deepcopy
    from app.v2 import knowledge
    source = {"id":"card", "version":2, "status":"confirmed", "domain":"math_logic", "branch":"基础代数",
        "mechanism":"线性方程组", "guidance":"source", "evidence_hash":"hash", "source":{},
        "source_kind":"tutorial_card", "guidance_eligibility":True}
    context = {"knowledge":{"guidance_enabled":True}, "knowledge_sources":[source], "canonical_domain_key":"math_logic",
        "branch":"基础代数", "raw_input":"讲解线性方程组及集合"}
    before = deepcopy(context)
    monkeypatch.setattr(knowledge,"source_unit_title",lambda _:"线性方程组")
    bundle,_ = knowledge.unit_guidance(context,{"unit_id":"other", "title":"集合"})
    assert not bundle["selected"] and bundle["excluded"][0]["reason"]=="outside_curated_source_unit"
    assert context==before


def test_conflicted_sealed_or_unvalidated_guidance_is_not_eligible_for_a_or_b(monkeypatch):
    from app.v2 import knowledge
    eligible = {"id":"ok", "version":1, "conflict":False, "usage":"guidance",
                "guidance_eligibility":True, "evidence_hash":"hash", "guidance":"fixture"}
    conflicted = {**eligible, "id":"conflict", "conflict":True}
    sealed = {**eligible, "id":"sealed", "usage":"sealed"}
    unvalidated = {**eligible, "id":"unvalidated", "guidance_eligibility":False}
    seen = []
    def fake_bundle(sources, **kwargs):
        seen.extend(sources)
        return {"selected": [{"id": source["id"]} for source in sources], "excluded": []}
    monkeypatch.setattr(knowledge, "evidence_bundle", fake_bundle)
    context = {"knowledge":{"guidance_enabled":True}, "knowledge_sources":[eligible, conflicted, sealed, unvalidated],
        "canonical_domain_key":"math_logic", "branch":"", "raw_input":"fixture"}
    bundle, _ = knowledge.unit_guidance(context, {"unit_id":"u", "title":"fixture"})
    assert [source["id"] for source in seen] == ["ok"]
    assert bundle["selected"] == [{"id": "ok"}]
    assert knowledge.guidance_exclusion_reason(conflicted) == "confirmed_conflict"
    assert knowledge.guidance_exclusion_reason(sealed) == "sealed"
    assert knowledge.guidance_exclusion_reason(unvalidated) == "guidance_ablation_not_validated"
