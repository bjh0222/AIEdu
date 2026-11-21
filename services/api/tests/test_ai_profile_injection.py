"""Fresh fixtures check actual A/C input identity, evidence binding and expiry."""
from copy import deepcopy
from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post, task_run


def assets():
    return {"profile": {"id": "import-test", "version": 1, "difficulty_features": ["two dependent operations"],
                "representative_samples": [{"source_id": "public-test", "quote_hash": "a"*64}]},
        "rubric": {"id": "rubric-test", "version": 1, "dimensions": [{"dimension_id": "alignment", "name": "目标对齐",
            "anchors": {"0": "contradictory target", "1": "partial target", "2": "all declared target operations located"}}]}}


def test_freeze_identity_rejects_tampering_and_old_head(client):
    from app.v2.import_profiles import publish_assets, freeze_import_profiles, binding_status
    from app.v2.runtime import write
    from app.v2.contracts import DomainError
    with write() as db:
        refs = publish_assets(db, assets())
        frozen = freeze_import_profiles(db, {"import_profile_refs": [refs]})
        assert frozen["import_understanding"][0]["profile"]["hash"] == refs["profile"]["hash"]
        corrupted = deepcopy(refs)
        corrupted["profile"]["hash"] = "0"*64
        with pytest.raises(DomainError, match="HASH_MISMATCH"):
            freeze_import_profiles(db, {"import_profile_refs": [corrupted]})
        changed = assets()
        changed["profile"].update(version=2, difficulty_features=["new interpretation"])
        publish_assets(db, changed)
        assert binding_status(db, frozen)["status"] == "stale"
        with pytest.raises(DomainError, match="STALE"):
            freeze_import_profiles(db, {"import_profile_refs": [refs]})
        assert frozen["import_understanding"][0]["profile"]["content"]["version"] == 1


def test_publish_assets_rejects_malformed_rubric_before_any_write(client):
    from app.v2.import_profiles import publish_assets
    from app.v2.runtime import write
    from app.v2.contracts import DomainError

    malformed = assets()
    malformed["rubric"]["dimensions"].append(
        {**malformed["rubric"]["dimensions"][0]}
    )
    with pytest.raises(DomainError, match="IMPORT_RUBRIC_CONTRACT_INVALID"):
        with write() as db:
            publish_assets(db, malformed)

    missing_indicator = assets()
    missing_indicator["rubric"]["dimensions"][0]["indicators"] = [
        {"indicator_id": "coverage"},
        {"indicator_id": "coverage"},
    ]
    with pytest.raises(DomainError, match="IMPORT_RUBRIC_CONTRACT_INVALID"):
        with write() as db:
            publish_assets(db, missing_indicator)


def test_research_rubric_publication_rejects_empty_indicator_or_unbound_source(client):
    from app.v2.import_profiles import publish_research_rubric
    from app.v2.runtime import write
    from app.v2.contracts import DomainError

    malformed = {
        "rubric_id": "research-fixture",
        "version": 1,
        "source_claims": [{"claim_id": "claim:a"}],
        "dimensions": [{
            "dimension_id": "D1",
            "source_refs": ["claim:missing"],
            "indicators": [],
        }],
    }
    with pytest.raises(DomainError, match="RESEARCH_RUBRIC"):
        with write() as db:
            publish_research_rubric(db, malformed)


def test_A_and_C_consume_same_profile_rubric_and_only_literal_score(client, monkeypatch):
    from app.v2.import_profiles import publish_assets, review_bound_profiles
    from app.v2.runtime import write, job, load, dump, claim_root
    from app.v2.tasks import start_run
    from app.v2.contracts import StartRun, sha
    from app.v2 import worker, inference
    headers = owner(client)
    with write() as db:
        refs = publish_assets(db, assets())
    created = post(client, "/tasks", {"title": "profile fixture", "raw_input": "teach", "import_profile_refs": [refs]}, headers).json()
    post(client, f"/tasks/{created['task_id']}/confirm", {"expected_revision": 1, "context_hash": created["context_hash"]}, headers)
    started = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), str(uuid4()))
    rid = started["run_id"]
    lease = claim_root(rid, "fixture")
    with write() as db:
        obj = job(db, rid)
        state = load(obj.state_json)
        state["intent_pending"] = True
        obj.state_json = dump(state)
    seen = []
    def fake(*args, **kw):
        from app.v2.skills import entry as skill_entry, review_skill
        seen.append(kw)
        bindings = kw["data"]["import_understanding"]
        bindings = bindings if isinstance(bindings, list) else [bindings]
        assert bindings[0]["profile"]["hash"] == refs["profile"]["hash"]
        assert bindings[0]["rubric"]["hash"] == refs["rubric"]["hash"]
        if kw["stage"] == "understanding":
            assert kw["data"]["planning_skill"]["hash"]
            return {"outline": [{"title": "lesson"}], "conflicts": []}, {"call_id": "A"}
        skill = review_skill()
        assert kw["entries"].count(skill_entry(skill, "C")) == 1
        assert f"# {skill['skill_id']}.v{skill['version']}" in kw["system"]
        assert "generation_process" not in kw["data"] and "previous_judgement" not in kw["data"]
        return {"dimensions": [{"dimension_id": "alignment", "result": "satisfied", "score": 2,
            "reason": "literal observation", "evidence": [{"field": "body", "quote": "two operations"}]}]}, {"call_id": "C"}
    monkeypatch.setattr(inference, "invoke", fake)
    worker.resolve_run_intent(rid, "fixture", lease["epoch"])
    with write() as db:
        state = load(job(db, rid).state_json)
    unit = {"unit_id": "unit", "body": "two operations", "content_hash": sha("two operations")}
    report = review_bound_profiles(rid, "fixture", lease["epoch"], state, [unit], "fixed")
    assert report["dimensions"][0]["score"] == 2
    assert report["aggregate_score"] is report["threshold"] is None
    assert report["model_family_independent"] is False
    unit["body"] = "quote no longer present"
    report = review_bound_profiles(rid, "fixture", lease["epoch"], state, [unit], "fixed2")
    assert report["dimensions"][0]["score"] is None
    assert report["dimensions"][0]["result"] == "undecidable"


def test_planning_dependency_cycle_and_nonlocal_arrangement():
    from app.v2.planning import arrangement, apply_arrangement
    from app.v2.contracts import DomainError
    base = {"goals": ["goal"], "learner_prerequisites": [], "scope": "system", "quality_targets": [],
        "unresolved_conflicts": [], "generation_prompt": "generate only planned units", "units": [
            {"title": title, "concepts": [], "question_types": [], "difficulty_basis": {}, "quantity": None,
                "material_mapping": [], "acceptance_criteria": [], "depends_on_titles": dependency}
            for title, dependency in (("chapter", []), ("conclusion", ["chapter"]))]}
    plan = {"units": [{"unit_id": "b", "title": "conclusion", "depends_on": []}, {"unit_id": "a", "title": "chapter", "depends_on": []}]}
    result = apply_arrangement(plan, arrangement({"teaching_arrangement": base}))
    assert [u["unit_id"] for u in result["units"]] == ["a", "b"]
    assert result["units"][1]["depends_on"] == ["a"]
    base["units"][0]["depends_on_titles"] = ["conclusion"]
    with pytest.raises(DomainError, match="CYCLE"):
        apply_arrangement(plan, arrangement({"teaching_arrangement": base}))
