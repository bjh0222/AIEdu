"""Verify that literal assertion evidence survives the actual item boundary."""
import pytest
from app.v2.items import normalize_items
from app.v2.math_framework import evaluate_item_quality, framework_for_domain
from app.v2.contracts import DomainError


def test_normalized_items_reach_verifier_without_trusting_unbound_metadata():
    identity = {"assertion": "polynomial_identity", "left": "x^2", "right": "x*x", "variables": ["x"]}
    incorrect = {**identity, "right": "x^3"}
    payload = {"items": [{"stem_markdown": "验证恒等式 x^2=x*x", "answer": {"final": "成立", "reasoning": "逐项展开。"},
        "formal_assertions": [identity, incorrect]}]}
    items = normalize_items(payload, "exercise", "unit")
    assert items[0]["formal_assertions"] == [identity, incorrect]
    report = evaluate_item_quality({}, [{"unit_id": "unit", "items": items}], framework=framework_for_domain("math_logic"))
    records = report["item_results"][0]["evidence"]
    assert records[0]["result"] == "satisfied"
    assert records[0]["source_binding"]["status"] == "bound"
    assert records[1]["literal_assertion_result"] == "violated"
    assert records[1]["result"] == "undecidable"
    assert records[1]["source_binding"]["status"] == "unbound"
    assert report["aggregate_score"] is None


@pytest.mark.parametrize("invalid", ["not-list", [None], [{}]*25])
def test_bad_metadata_is_rejected_instead_of_discarded(invalid):
    value = {"items": [{"stem_markdown": "题干", "answer": {"final": "答案", "reasoning": "推理"},
        "formal_assertions": invalid}]}
    with pytest.raises(DomainError, match="ITEM_ASSERTIONS_INVALID"):
        normalize_items(value, "exercise", "unit")


def test_full_exercise_reserves_reasoning_output_in_the_audited_request(client, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.config import settings
    from app.v2 import worker, checker
    from app.v2.contracts import TaskInput, Confirmation, StartRun
    from app.v2.tasks import create_task, mutate_input, start_run, snapshot
    from app.v2.quality import check_snapshot
    from app.v2.runtime import dump
    from app.db import SessionLocal
    from app.v2.storage import CallDispatch
    import httpx, json
    created = create_task(TaskInput(title="隔离容量用例", raw_input="生成一份练习", requested_artifacts=["exercise"],
        execution_policy={"task_adaptive_format": False, "quality_loop": False, "criterion_assessment": False},
        spec_bindings=[{"scene":"exercise","artifact_role":"exercise"}]), str(uuid4()))
    mutate_input(created["task_id"],Confirmation(expected_revision=1,context_hash=created["context_hash"]),str(uuid4()),confirmation=True)
    monkeypatch.setattr(settings,"v2_allow_paid",True)
    profile=SimpleNamespace(llm_base_url="https://api.deepseek.com/v1",llm_model="deepseek-flash",llm_api_key="fixture-only",
        llm_model_family="deepseek",llm_timeout=10,llm_temperature=None,llm_json_mode=True,llm_max_tokens=0,
        llm_thinking="enabled",llm_reasoning_effort="high")
    monkeypatch.setattr(worker,"runtime_settings",lambda:profile)
    monkeypatch.setattr(checker,"evaluate",lambda rid,w,e,state,units,vid,*a,**k:check_snapshot(state,units,vid))
    captured=[]
    def respond(url,**kwargs):
        captured.append(kwargs["json"])
        payload={"items":[{"stem_markdown":"合成题", "answer":{"final":"合成答案","reasoning":"合成推理"}}]}
        return httpx.Response(200,json={"id":"fixture", "choices":[{"message":{"content":dump(payload)},"finish_reason":"stop"}],
            "usage":{"total_tokens":10}})
    monkeypatch.setattr("app.v2.inference.httpx.post",respond)
    run=start_run(created["task_id"],StartRun(expected_revision=1,confirmed_context_hash=created["context_hash"],mode="configured"),str(uuid4()))
    worker.execute_run(run["run_id"])
    final=snapshot(created["task_id"])
    assert final["state"]=="completed" and len(captured)==1
    assert captured[0]["max_tokens"]==65536
    with SessionLocal() as db:
        call=db.get(CallDispatch,final["calls"][0]["call_id"])
        assert json.loads(call.request_json)["parameters"]["max_tokens"]==65536
