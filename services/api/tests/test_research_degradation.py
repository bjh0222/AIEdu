"""External evidence insufficiency degrades; contract/privacy failures do not."""
import time

import pytest

from test_task_run_v2_guards import queued


def degraded_result():
    return {
        "schema_version": "aiedu.research-result.v2",
        "status": "degraded",
        "research_mode": "local_materials_only",
        "rubric": None,
        "research_rubric_ref": None,
        "degradation": {
            "method_version": "research-evidence-degrade.v1",
            "reason": "RESEARCH_TWO_INDEXES_NOT_RETRIEVED",
            "attempted_indexes": ["crossref", "openalex"],
            "successful_indexes": [],
            "citation_boundary": "No unavailable source may be cited as retrieved.",
        },
    }


def test_only_evidence_insufficiency_degrades_to_local_materials(monkeypatch):
    from app.v2 import rubric_research
    from app.v2.contracts import DomainError

    monkeypatch.setattr(
        rubric_research, "_previous",
        lambda run_id, stage: {
            "results": [
                {"index": "crossref", "query": "a", "status": "failed", "reason": "HTTP_503"},
                {"index": "openalex", "query": "a", "status": "failed", "reason": "HTTP_429"},
            ],
            "attempts": [{"status": "failed"}],
            "sources": [],
        } if stage in {"discovery", "source_reads"} else None,
    )
    monkeypatch.setattr(
        rubric_research, "_execute_research_strict",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DomainError("RESEARCH_TWO_INDEXES_NOT_RETRIEVED", 422)
        ),
    )
    monkeypatch.setattr(
        rubric_research, "_save",
        lambda run_id, worker, epoch, stage, value: value,
    )
    result = rubric_research.execute_research("r", "w", 0, {}, lambda _: None)
    assert result["research_mode"] == "local_materials_only"
    assert result["degradation"]["attempted_indexes"] == ["crossref", "openalex"]
    assert result["degradation"]["successful_indexes"] == []
    assert "may be cited" in result["degradation"]["citation_boundary"]

    monkeypatch.setattr(
        rubric_research, "_execute_research_strict",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            DomainError("RESEARCH_QUERIES_INVALID", 422)
        ),
    )
    with pytest.raises(DomainError, match="RESEARCH_QUERIES_INVALID"):
        rubric_research.execute_research("r", "w", 0, {}, lambda _: None)


def test_worker_freezes_degraded_research_and_continues_planning(client, monkeypatch):
    from app.models import LearningGoal
    from app.v2 import rubric_research, worker
    from app.v2.runtime import claim_root, dump, job, load, reconcile_stale_jobs, write

    _, run = queued(client)
    with write() as db:
        reconcile_stale_jobs(db, now=time.time() + 10**6)
    lease = claim_root(run["run_id"], "worker")
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        state["mode"] = "configured"
        state["context"]["research_request"] = {"enabled": True}
        obj.state_json = dump(state)
        obj.state_revision += 1
    monkeypatch.setattr(
        rubric_research, "execute_research",
        lambda *args, **kwargs: degraded_result(),
    )
    monkeypatch.setattr(
        worker, "ensure_research_strategy",
        lambda *args, **kwargs: {"hash": "s" * 64, "content": {"necessity": "required"}},
    )
    worker.run_research_before_planning(run["run_id"], "worker", lease["epoch"])
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        assert state["research_binding_frozen"] is True
        assert state["research_degraded"] is True
        assert state["stages"]["researching"] == "degraded"
        assert state["context"]["research_degradation"]["reason"] == "RESEARCH_TWO_INDEXES_NOT_RETRIEVED"
        assert "research_rubrics" not in state["context"]
        owner = db.get(LearningGoal, obj.goal_id)
        assert load(owner.task_state_json)["context"]["research_degradation"]["reason"] == "RESEARCH_TWO_INDEXES_NOT_RETRIEVED"
