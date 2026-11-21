"""Isolated same-family review is executed without claiming family independence."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_v2_optimization import heading_task


@pytest.mark.parametrize("label", [None, "", "unknown", "demo", "1", "123", "N/A"])
def test_unverified_family_metadata_never_claims_cross_family_independence(label, monkeypatch):
    from app.v2 import checker, model_roles
    from app.v2.contracts import DomainError
    generation = SimpleNamespace(llm_model_family="deepseek")
    review = SimpleNamespace(llm_model_family=label, llm_configured=True)
    assert not checker.family_independent(generation, review)
    scoped = checker.scope_review({"independent_review": {"status": "completed"}}, generation, review)
    assert scoped["model_review"]["status"] == "completed"
    assert scoped["model_review"]["family_relationship"] == "unverified_family"
    assert scoped["independent_review"]["model_family_independent"] is False
    monkeypatch.setattr(model_roles, "role_config", lambda: ({"selected": {}}, {"review": "selected"}))
    monkeypatch.setattr(model_roles, "profile_settings", lambda p: review)
    assert model_roles.for_role("review") is review  # user's actual choice still runs
    with pytest.raises(DomainError, match="MODEL_FAMILY_NOT_INDEPENDENT"):
        model_roles.for_role("review", independent_of=["deepseek"])


@pytest.mark.parametrize("family", ["deepseek", "glm"])
@pytest.mark.parametrize("parallel", [False, True])
def test_review_runs_and_keeps_exact_observations_separate(client, monkeypatch, family, parallel):
    from app.db import SessionLocal
    from app.v2 import checker, checker_batch
    from app.v2.runtime import job, load
    _, snap = heading_task(client)
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    unit = deepcopy(snap["units"][0])
    unit["items"] = [{"item_id": "question", "stem_markdown": "Find x if x+1=2",
        "answer": {"final": "SECRET_FINAL", "reasoning": "SECRET_REASON"}}]
    units = [unit]
    if parallel:
        second = deepcopy(unit)
        second["unit_id"] = "second"
        second["items"][0]["item_id"] = "question2"
        units.append(second)
    state["context"]["domain_key"] = "math_logic"
    state["execution_policy"] = {"semantic_batch": True, "check_concurrency": 2 if parallel else 1}
    profile = lambda f: SimpleNamespace(llm_model_family=f, llm_model="fixture", llm_base_url="https://fixture.invalid")
    calls = []

    def role(name, **kwargs):
        result = profile("deepseek" if name == "generation" else family)
        assert result.llm_model_family not in kwargs.get("independent_of", ())
        return result

    def fake(*args, **kw):
        calls.append(kw)
        data = kw["data"]
        assert not {"generation_model", "prior_judgement", "trace"}.intersection(data)
        if kw["stage"] == "blind_solving":
            assert set(data) == {"items"}
            assert "SECRET_FINAL" not in str(data) and "SECRET_REASON" not in str(data)
            assert all(set(i) == {"item_id", "stem_markdown"} for i in data["items"])
            value = {"solutions": [{"item_id": i["item_id"], "final": "fixture"} for i in data["items"]]}
        elif kw["stage"] == "item_reviewing":
            value = {"item_reviews": [{"item_id": i["item_id"], "dimensions": {
                d: {"result": "supported", "reason": "offline observation", "quote": i["answer"]["final"]}
                for d in ("premises", "reasoning", "transfer", "difficulty")}} for i in data["items"]]}
        else:
            value = {"checks": [{"item_id": i["item_id"], "result": "undecidable", "findings": []} for i in data["checks"]]}
        return value, {"call_id": str(uuid4()), "model_family": family}

    monkeypatch.setattr(checker, "for_role", role)
    monkeypatch.setattr(checker_batch, "for_role", role)
    monkeypatch.setattr(checker_batch, "invoke", fake)
    monkeypatch.setattr("app.v2.capacity.get_capacity", lambda *a: {"max_output_tokens": 128000})
    result = checker.evaluate(snap["run_id"], "fixture", 0, state, units, "version")
    for stage in ("blind_solving", "item_reviewing", "checking", "checking_perturbation"):
        assert sum(c["stage"] == stage for c in calls) == len(units)
    same = family == "deepseek"
    review = result["model_review" if same else "independent_review"]
    assert review["status"] == "completed"
    assert review["model_family_independent"] is not same
    observations = result["item_quality"]["model_item_reviews" if same else "independent_item_reviews"]
    assert {o["item_id"] for o in observations} == {i["item_id"] for u in units for i in u["items"]}
    assert all(d["source"] and d["result"] == "undecidable" and d["score"] is None
        for o in observations for d in o["dimensions"].values())
    if same:
        assert result["independent_review"]["reason"] == "MODEL_FAMILY_NOT_INDEPENDENT"
        assert not result["item_quality"]["independent_item_reviews"]
        assert all(d["authority"].startswith("same_family") for o in observations for d in o["dimensions"].values())


def test_completed_recheck_is_explicit_and_preserves_content(client, monkeypatch):
    from app.v2.contracts import DomainError, Resume
    from app.v2.tasks import resume, snapshot
    from app.v2.worker import execute_run
    _, snap = heading_task(client)
    dto = Resume(expected_revision=snap["state_revision"], checkpoint_hash=snap["checkpoint_hash"])
    with pytest.raises(DomainError, match="CHECKPOINT_STALE"):
        resume(snap["run_id"], dto, "implicit-recheck")
    dto.recheck_completed = True
    new = resume(snap["run_id"], dto, "explicit-recheck")
    execute_run(new["run_id"])
    checked = snapshot(snap["task_id"])
    assert checked["state"] == "completed"
    assert checked["content_hash"] == snap["content_hash"]
    assert checked["units"] == snap["units"]
    assert len(checked["calls"]) == len(snap["calls"])


def test_failed_card_run_still_resumes_normally(client):
    from app.v2.contracts import Resume
    from app.v2.runtime import dump, job, load, write
    from app.v2.tasks import resume, snapshot
    _, snap = heading_task(client)
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["card_source"] = {"version_id": snap["version_id"]}
        obj.status, obj.state_json = "failed", dump(state)
    result = resume(snap["run_id"], Resume(expected_revision=snap["state_revision"],
        checkpoint_hash=snap["checkpoint_hash"]), "card-resume")
    assert snapshot(snap["task_id"], result["run_id"])["state"] == "queued"


def test_review_only_rebinds_cards_without_new_content_or_confirmation(client, monkeypatch):
    from app.v2.contracts import Resume
    from app.v2.insights import bind_cards, cards_key, record, save
    from app.v2.runtime import job, load, dump, write
    from app.v2.tasks import resume, snapshot
    from app.v2.worker import execute_run
    from app.models import TutorialVersion
    _, snap = heading_task(client)
    with write() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        unit = snap["units"][0]
        candidates = [{"unit_id": unit["unit_id"], "kind": "method", "title": "offline fixture",
            "quote": unit["body"]}]
        cards = bind_cards(version, candidates, {"call_id": "original-call"})
        cards["cards"][0]["status"] = "confirmed"
        save(db, cards_key(version.id), cards)
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["mode"] = "configured"
        obj.state_json = dump(state)
    result = resume(snap["run_id"], Resume(expected_revision=snap["state_revision"],
        checkpoint_hash=snap["checkpoint_hash"], recheck_completed=True), "recheck-with-cards")
    monkeypatch.setattr("app.v2.model_roles.role_config", lambda: ({}, {}))
    monkeypatch.setattr("app.v2.checker.evaluate", lambda *a, **k: {"findings": [], "checks": [], "quality_status": "needs_review"})
    execute_run(result["run_id"])
    new = snapshot(snap["task_id"])
    assert new["state"] == "completed", new.get("stop_reason")
    assert new["version_id"] != snap["version_id"]
    assert new["content_hash"] == snap["content_hash"]
    with write() as db:
        rebound = record(db, cards_key(new["version_id"]))
        original = record(db, cards_key(snap["version_id"]))
    assert rebound["new_model_calls"] == 0
    assert rebound["cards"][0]["source"]["version_id"] == new["version_id"]
    assert rebound["cards"][0]["status"] == "candidate"
    assert original["cards"][0]["status"] == "confirmed"
    assert rebound["cards"][0]["audit"]["call_id"] == "original-call"
    assert rebound["cards"][0]["quote"] == original["cards"][0]["quote"]
    from app.v2.contracts import DomainError
    previous = snapshot(snap["task_id"], snap["run_id"])
    with pytest.raises(DomainError, match="VERSION_MISMATCH"):
        resume(snap["run_id"], Resume(expected_revision=previous["state_revision"],
            checkpoint_hash=previous["checkpoint_hash"], recheck_completed=True), "recheck-superseded")
