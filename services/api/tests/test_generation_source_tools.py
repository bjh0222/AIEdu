"""B tool turns, privacy, durable reuse, limits and unknown non-replay."""
from copy import deepcopy
from uuid import uuid4

import pytest

from test_task_run_v2 import task_run


def running(client):
    from app.v2.runtime import claim_root, job, load
    from app.v2.tasks import start_run
    from app.v2.contracts import StartRun
    from app.db import SessionLocal
    # Freeze units for the scheduling test; adaptive planning has separate coverage.
    _, created, _ = task_run(client, False, execution_policy={
        "task_adaptive_format": False, "quality_loop": False, "criterion_assessment": False})
    started = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), str(uuid4()))
    lease = claim_root(started["run_id"], "source-fixture")
    with SessionLocal() as db:
        state = load(job(db, started["run_id"]).state_json)
    # These cases exercise the explicit source-tool wrapper. Product tasks
    # default research/off unless the user enables research_request.
    state["execution_policy"]["network_enabled"] = True
    state["execution_policy"]["network_tool_rounds"] = 3
    return started["run_id"], lease["epoch"], state


def test_B_requests_search_then_reads_and_consumes_real_tool_shape(client, monkeypatch):
    from app.v2 import generation_tools as g
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    rid, epoch, state = running(client)
    turns, network = [], []
    def tool(request, data, transport):
        network.append(request)
        if request["name"] == "search_sources":
            return {"status": "ok", "candidates": [{"source_id": "s", "url": "https://example.org/source"}]}
        return {"status": "ok", "source_id": "s", "text": "public literal evidence", "access_level": "full_html", "source_hash": "a"*64}
    def invoke(*args, **kw):
        turns.append(deepcopy(kw))
        if len(turns) == 1:
            return {"tool_requests": [{"name": "search_sources", "query": "cognitive operations", "index": "crossref"}]}, {"call_id": "B1"}
        if len(turns) == 2:
            assert kw["data"]["network_evidence"][0]["result"]["candidates"][0]["source_id"] == "s"
            return {"tool_requests": [{"name": "read_source", "url": "https://example.org/source"}]}, {"call_id": "B2"}
        assert kw["data"]["network_evidence"][1]["result"]["text"] == "public literal evidence"
        return {"body": "generated candidate"}, {"call_id": "B3"}
    monkeypatch.setattr(g, "_source_tool", tool)
    monkeypatch.setattr(g, "invoke", invoke)
    output, audit, trace = g.generation_with_sources(rid, "source-fixture", epoch, state,
        stage="generating", unit_id="unit", data={"raw_input": "private source remains unchanged"}, system="candidate")
    assert output["body"] == "generated candidate" and audit["call_id"] == "B3"
    assert len(trace["calls"]) == 3 and len(trace["receipts"]) == 2
    assert turns[0]["stage"] == "generating" and turns[2]["stage"] == "generating_source_continuation"
    assert "untrusted evidence" in turns[2]["system"]
    cached = g._tool_result(rid, "source-fixture", epoch, network[-1], {}, None)
    assert cached["cache"] == "exact_successful_tool_request" and len(network) == 2
    with SessionLocal() as db:
        stored = load(job(db, rid).state_json)
        assert len(stored["network_tool_receipts"]) == 2


def test_private_query_unknown_and_budget_do_not_get_retried(client, monkeypatch):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError, match="PRIVATE_DATA"):
        g._public_query("a"*40, {"raw_input": "a"*80})
    with pytest.raises(DomainError, match="PRIVATE_DATA"):
        g._public_query("person@example.org", {})
    rid, epoch, state = running(client)
    calls = []
    def unknown(*args, **kw):
        calls.append(kw)
        raise DomainError("UNKNOWN_CALL")
    monkeypatch.setattr(g, "invoke", unknown)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        g.generation_with_sources(rid, "source-fixture", epoch, state, stage="generating", unit_id="unit", data={}, system="candidate")
    assert len(calls) == 1


def test_repeated_successful_query_gets_one_final_generation_turn(client, monkeypatch):
    from app.v2 import generation_tools as g
    rid, epoch, state = running(client)
    turns = []
    request = {"name": "search_sources", "query": "bounded evidence", "index": "crossref"}

    def tool(req, data, transport):
        return {"status": "ok", "candidates": [{"source_id": "cached", "title": "one source"}]}

    def invoke(*args, **kw):
        turns.append(kw)
        if len(turns) <= 2:
            return {"tool_requests": [request]}, {"call_id": f"R{len(turns)}"}
        return {"body": "candidate after bounded evidence"}, {"call_id": "R3"}

    monkeypatch.setattr(g, "_source_tool", tool)
    monkeypatch.setattr(g, "invoke", invoke)
    output, _, trace = g.generation_with_sources(rid, "source-fixture", epoch, state,
        stage="generating", unit_id="unit", data={}, system="candidate")
    assert output["body"] == "candidate after bounded evidence"
    assert len(turns) == 3
    assert len(trace["receipts"]) == 2
    assert "untrusted evidence" in turns[2]["system"]
    assert "No further tool requests are allowed" in turns[2]["system"]
    assert "EITHER" not in turns[2]["system"]


def test_independent_B_units_are_actually_parallel(client, monkeypatch):
    import json
    import threading
    from types import SimpleNamespace
    from app.config import settings
    from app.v2 import worker, checker, model_roles, repairs
    from app.v2.contracts import StartRun, digest
    from app.v2.runtime import write, job, load, dump
    from app.v2.tasks import start_run, snapshot
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    profile = lambda *a, **kw: SimpleNamespace(llm_base_url="https://api.deepseek.com/v1", llm_model="deepseek-flash",
        llm_model_family="deepseek", llm_thinking="enabled", llm_reasoning_effort="high", llm_configured=True)
    monkeypatch.setattr(model_roles, "role_config", lambda: ({}, {"generation": "fixture"}))
    monkeypatch.setattr(model_roles, "for_role", profile)
    monkeypatch.setattr(checker, "evaluate", lambda rid, w, ep, st, un, ver, *a, **kw: worker.check_snapshot(st, un, ver))
    monkeypatch.setattr(repairs, "auto_repair", lambda *a, **kw: None)
    _, created, _ = task_run(client, False, execution_policy={
        "task_adaptive_format": False, "quality_loop": False, "criterion_assessment": False})
    started = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"], mode="configured"), str(uuid4()))
    with write() as db:
        obj = job(db, started["run_id"])
        state = load(obj.state_json)
        unit = state["plan"]["units"][0]
        state["plan"]["units"] = [{**unit, "unit_id": "parallel-"+str(i), "title": "chapter "+str(i)} for i in range(2)]
        state["plan"]["plan_hash"] = digest(state["plan"]["units"])
        state["execution_policy"].update(generation_concurrency=2)
        obj.state_json = dump(state)
    barrier = threading.Barrier(2, timeout=4)
    threads = set()
    class Provider:
        def complete_json(self, system, user):
            data = json.loads(user)
            threads.add(threading.current_thread().name)
            barrier.wait()
            return {"body": data["unit"]["title"]}, 1, {"total_tokens": 2}
    worker.execute_run(started["run_id"], lambda _: Provider())
    result = snapshot(created["task_id"])
    assert result["state"] == "completed", result["stop_reason"]
    assert len(threads) == 2
    assert [u["unit_id"] for u in result["units"]] == ["parallel-0", "parallel-1"]
    assert len(result["calls"]) == 2 and all(c["status"] == "settled" for c in result["calls"])
