"""Model-backed A binding for an explicitly optimized legacy fixed draft."""
from copy import deepcopy

import pytest

from test_task_run_v2 import task_run
from test_optimization_prompt_loop import _prepare
from test_integration_quality_entry import teaching


@pytest.mark.parametrize("aligns", [True, False])
def test_legacy_A_binding_is_model_backed_bounded_and_preserves_fixed_content(client, monkeypatch, aligns):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2 import inference
    from app.v2.contracts import DomainError
    from app.v2.planning import bind_legacy_optimization_plan, skill
    from app.v2.runtime import dump, job, load, write
    _, _, snap=task_run(client)
    _prepare(client,snap)
    with write() as db:
        obj=job(db,snap["run_id"])
        state=load(obj.state_json)
        state["plan"].pop("planning_skill")
        state["optimization_request"]={"base_version_id":snap["version_id"]}
        obj.state_json=dump(state)
        original_plan=deepcopy(state["plan"])
        refs = [{
            "profile_id": profile["profile_id"],
            "profile_version": profile["version"],
            "profile_hash": profile["profile_hash"],
            "rule_id": rule["rule_id"],
            "rule_version": rule["version"],
            "criterion_hash": rule["criterion_hash"],
        } for profile in state["context"].get("quality_profiles", [])
          for rule in profile.get("rules", [])]
        original_content=db.get(TutorialVersion,snap["version_id"]).content_json
        original_budget=deepcopy(state["budget"])
    stages=[]
    def invoke(*args,**kw):
        stages.append(kw["stage"])
        assert "Applicability selectors are not learner-content bans" in kw["system"]
        assert "excluded_keywords" in kw["data"]["planning_skill"]["instructions"]
        assert kw["data"]["planning_skill"]["version"]==skill()["version"]
        assert kw["data"]["frozen_layout"][0]["unit_id"]==snap["units"][0]["unit_id"]
        assert any(entry.get("source_id") == "teaching-arrangement" and entry.get("consumer") == "A"
                   and entry.get("sha256") == kw["data"]["planning_skill"]["hash"] for entry in kw["entries"])
        title=kw["data"]["frozen_layout"][0]["title"] if aligns and len(stages)==2 else "wrong title"
        return {"teaching_arrangement":teaching(title, refs)},{"call_id":"A-"+str(len(stages))}
    monkeypatch.setattr(inference,"invoke",invoke)
    if aligns:
        bind_legacy_optimization_plan(snap["run_id"],"fixture",1)
    else:
        with pytest.raises(DomainError,match="TEACHING_ARRANGEMENT_LAYOUT_UNBOUND"):
            bind_legacy_optimization_plan(snap["run_id"],"fixture",1)
    assert stages==["planning","arrangement_alignment"]
    with SessionLocal() as db:
        current=load(job(db,snap["run_id"]).state_json)
        assert current["version_id"]==snap["version_id"] and current["budget"]==original_budget
        assert db.get(TutorialVersion,snap["version_id"]).content_json==original_content
        if aligns:
            assert current["plan"]["planning_skill"]["hash"]==skill()["hash"]
            assert current["optimization_planning_call"]["alignment"]["call_id"]=="A-2"
            assert [u["unit_id"] for u in current["plan"]["units"]]==[u["unit_id"] for u in original_plan["units"]]
        else:
            assert current["plan"]==original_plan


def test_legacy_A_no_implicit_upgrade_without_explicit_optimization(client):
    from app.v2.contracts import DomainError
    from app.v2.planning import bind_legacy_optimization_plan
    from app.v2.runtime import dump,job,load,write
    _,_,snap=task_run(client)
    _prepare(client,snap)
    with write() as db:
        obj=job(db,snap["run_id"])
        state=load(obj.state_json)
        state["plan"].pop("planning_skill")
        obj.state_json=dump(state)
    with pytest.raises(DomainError,match="A_SKILL_PLAN_BINDING_REQUIRED"):
        bind_legacy_optimization_plan(snap["run_id"],"fixture",1)
