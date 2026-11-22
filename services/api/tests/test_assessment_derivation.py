"""New math plans derive C3/C4/G3 once from bound criterion source rows."""
from copy import deepcopy

from app.v2.contracts import sha
from app.v2.derivation import derive_assessment_checks, is_derived_target
from app.v2.quality import compile_plan


def math_fixture():
    task = {
        "domain_key": "math_logic",
        "subject_bindings": [{"domain_key": "math_logic"}],
        "requested_artifacts": ["exercise"],
        "spec_bindings": [
            {"scene": "exercise", "artifact_role": "exercise", "snapshot": {}}
        ],
        "task_intent": {"conflicts": []},
    }
    plan = compile_plan(task)
    body = "题干：求值。\n\n答案：2。"
    unit = {
        "unit_id": "unit",
        "title": "方法题",
        "artifact_role": "exercise",
        "body": body,
        "content_hash": sha(body),
        "subject_bindings": [{"domain_key": "math_logic"}],
        "items": [{
            "item_id": "item-1",
            "ordinal": 1,
            "stem_markdown": "题干：求值。",
            "answer": {"final": "2", "reasoning": "先化简，再代入。"},
        }],
    }
    state = {
        "context": task,
        "plan": {"units": [unit]},
        "check_plan": plan,
        "execution_policy": {"criterion_assessment": True},
    }
    return state, plan, unit


def source_requests(state, plan, units):
    from app.v2.check_criteria import criterion
    requests = []
    for target in plan["derivation_contract"]["targets"]:
        for source in target["sources"]:
            item = next(row for row in plan["check_items"]
                        if row["item_id"] == source["check_id"])
            contract = item["criterion_contract"]
            targets = [entry for unit in units for entry in unit["items"]] \
                if contract["target"] == "each_item" else [None]
            for target_item in targets:
                identity = target_item["item_id"] if target_item else None
                requests.append({
                    "request_id": source["check_id"] + ":" + str(identity),
                    "check_id": source["check_id"],
                    "item_id": identity,
                    "ordinal": target_item.get("ordinal") if target_item else None,
                    "criterion": contract,
                })
    return requests


def assessment_report(requests, *, violated=None, missing=None):
    from app.v2.assessment_binding import input_binding
    rows = []
    for request in requests:
        if request["check_id"] == missing:
            continue
        clauses = []
        for clause_id in request["criterion"]["clauses"]:
            result = "violated" if (request["check_id"], clause_id) == violated else "satisfied"
            clauses.append({
                "clause_id": clause_id,
                "result": result,
                "reason": "synthetic bound source observation",
                "evidence": [{"field": "unit:body", "quote": "题干：求值。"}],
                "invalid_or_missing_observation": False,
            })
        row_result = ("violated" if any(row["result"] == "violated" for row in clauses)
                      else "satisfied")
        rows.append({
            "unit_id": "unit",
            "check_id": request["check_id"],
            "item_id": request["item_id"],
            "ordinal": request["ordinal"],
            "criterion_hash": request["criterion"]["criterion_hash"],
            "result": row_result,
            "clauses": clauses,
            "rating": 3 if request["check_id"].startswith("Q4.") else None,
            "processing_status": "complete",
            "audit": {"call_id": "source"},
        })
    return {
        "criteria": rows,
        "status": "completed",
        "result": "satisfied",
        "version_id": "version",
        "input_binding": input_binding(
            {"context": {"domain_key": "math_logic"}}, [{
                "unit_id": "unit",
                "title": "方法题",
                "artifact_role": "exercise",
                "body": "题干：求值。\n\n答案：2。",
                "content_hash": sha("题干：求值。\n\n答案：2。"),
                "subject_bindings": [{"domain_key": "math_logic"}],
                "items": [{
                    "item_id": "item-1",
                    "ordinal": 1,
                    "stem_markdown": "题干：求值。",
                    "answer": {"final": "2", "reasoning": "先化简，再代入。"},
                }],
            }], "version",
        ),
        "quality_preflight": {"matrix_hash": "matrix"},
    }


def test_compiler_freezes_one_derivation_contract_and_excludes_three_dispatch_entries(monkeypatch):
    from app.v2.checker_batch import semantic_check_items
    from app.v2.criterion_review import requests_for_unit

    state, plan, unit = math_fixture()
    contract = plan["derivation_contract"]
    assert contract["enabled"] is True
    assert contract["target_ids"] == ["C3.correctness", "C4.constraints", "G3.assessment_design"]
    assert all(is_derived_target(state, target_id) for target_id in contract["target_ids"])

    requests, _ = requests_for_unit(state, unit)
    assert {request["check_id"] for request in requests}.isdisjoint(contract["target_ids"])
    assert {"Q1.exercise", "Q2.exercise", "Q3.exercise", "Q4.exercise"} <= {
        request["check_id"] for request in requests
    }
    assert {
        item["item_id"] for item in semantic_check_items(state)
    }.isdisjoint(contract["target_ids"])

    old_state = deepcopy(state)
    old_state["check_plan"].pop("derivation_contract")
    assert not any(is_derived_target(old_state, target_id) for target_id in contract["target_ids"])


def test_source_reduction_preserves_violated_undecidable_and_not_applicable(monkeypatch):
    import app.v2.derivation as derivation
    import app.v2.criterion_review as criterion_review

    state, plan, unit = math_fixture()
    requests = source_requests(state, plan, [unit])
    monkeypatch.setattr(criterion_review, "requests_for_unit", lambda _state, _unit: (requests, []))
    monkeypatch.setattr(derivation, "consume", lambda *args, **kwargs: ("satisfied", []))

    satisfied = derive_assessment_checks(
        state, [unit], assessment_report(requests), "version", matrix_hash="matrix"
    )
    assert {row["result"] for row in satisfied.values()} == {"satisfied"}
    assert all(row["source_evidence"] for row in satisfied.values())

    violated = derive_assessment_checks(
        state, [unit],
        assessment_report(requests, violated=("Q2.exercise", "reasoning")),
        "version", matrix_hash="matrix",
    )
    assert violated["C3.correctness"]["result"] == "violated"
    assert violated["C4.constraints"]["result"] == "satisfied"
    assert violated["G3.assessment_design"]["result"] == "satisfied"

    missing = derive_assessment_checks(
        state, [unit], assessment_report(requests, missing="Q3.exercise"),
        "version", matrix_hash="matrix",
    )
    assert missing["C4.constraints"]["result"] == "undecidable"

    no_items = {**unit, "items": []}
    not_applicable = derive_assessment_checks(
        state, [no_items], assessment_report(requests), "version", matrix_hash="matrix"
    )
    assert {row["result"] for row in not_applicable.values()} == {"not_applicable"}


def test_non_math_plan_keeps_targets_on_independent_paths():
    task = {
        "domain_key": "history_culture",
        "subject_bindings": [{"domain_key": "history_culture"}],
        "requested_artifacts": ["exercise"],
        "spec_bindings": [
            {"scene": "exercise", "artifact_role": "exercise", "snapshot": {}}
        ],
        "task_intent": {"conflicts": []},
    }
    plan = compile_plan(task)
    assert plan["derivation_contract"]["enabled"] is False
    assert not any(
        row["item_id"] in {"C3.correctness", "C4.constraints", "G3.assessment_design"}
        and row.get("derivation_target")
        for row in plan["check_items"]
    )


def test_real_criterion_requests_and_consumer_feed_derivation_without_mocks():
    from app.v2.assessment_binding import input_binding
    from app.v2.criterion_review import bound_quote, requests_for_unit, summarize

    state, plan, unit = math_fixture()
    second = deepcopy(unit["items"][0])
    second.update(item_id="item-2", ordinal=2, stem_markdown="题干：化简。",
                  answer={"final": "x", "reasoning": "合并同类项。"})
    unit["items"].append(second)
    fields = {"unit:body": unit["body"]}
    for entry in unit["items"]:
        fields[f"item:{entry['item_id']}:stem_markdown"] = entry["stem_markdown"]
        fields[f"item:{entry['item_id']}:answer.final"] = entry["answer"]["final"]
        fields[f"item:{entry['item_id']}:answer.reasoning"] = entry["answer"]["reasoning"]
    requests, _ = requests_for_unit(state, unit)
    rows = []
    for request in requests:
        clauses = []
        own_id = request["item_id"]
        if own_id:
            own_field = f"item:{own_id}:stem_markdown"
            own_text = next(item["stem_markdown"] for item in unit["items"]
                            if item["item_id"] == own_id)
            evidence = [bound_quote({"field": own_field, "quote": own_text}, fields)]
        else:
            evidence = [bound_quote({"field": "unit:body", "quote": "题干：求值。"}, fields)]
        for clause_id in request["criterion"]["clauses"]:
            clause_evidence = deepcopy(evidence)
            if (own_id and request["check_id"].startswith("Q4.")
                    and clause_id == "transfer"):
                peer = next(item for item in unit["items"] if item["item_id"] != own_id)
                clause_evidence.append(bound_quote({
                    "field": f"item:{peer['item_id']}:stem_markdown",
                    "quote": peer["stem_markdown"],
                }, fields))
            clauses.append({
                "clause_id": clause_id,
                "result": "satisfied",
                "reason": "actual source-bound criterion observation",
                "evidence": clause_evidence,
                "invalid_or_missing_observation": False,
            })
        rows.append({
            "unit_id": unit["unit_id"],
            "check_id": request["check_id"],
            "item_id": request["item_id"],
            "ordinal": request["ordinal"],
            "criterion_hash": request["criterion"]["criterion_hash"],
            "result": "satisfied",
            "clauses": clauses,
            "rating": 3 if request["check_id"].startswith("Q4.") else None,
            "processing_status": "complete",
            "audit": {"call_id": "actual-source"},
        })
    report = summarize(rows, len(requests), []) | {
        "version_id": "version",
        "input_binding": input_binding(state, [unit], "version"),
        "quality_preflight": {"matrix_hash": "matrix"},
    }
    from app.v2.assessment_binding import _criteria, consume
    assert _criteria(report, state, [unit])[0] == "satisfied"
    assert consume(report, "automated_assessment", state, [unit], "version") == (
        "satisfied",
        [],
    )
    result = derive_assessment_checks(
        state, [unit], report, "version", matrix_hash="matrix"
    )
    assert {row["result"] for row in result.values()} == {"satisfied"}
    assert all(row["source_evidence"] for row in result.values())

    violating_rows = deepcopy(rows)
    q2 = next(
        row for row in violating_rows
        if row["check_id"] == "Q2.exercise" and row["item_id"] == "item-1"
    )
    next(
        clause for clause in q2["clauses"] if clause["clause_id"] == "reasoning"
    )["result"] = "violated"
    q2["result"] = "violated"
    violating_report = summarize(violating_rows, len(requests), []) | {
        "version_id": "version",
        "input_binding": input_binding(state, [unit], "version"),
        "quality_preflight": {"matrix_hash": "matrix"},
    }
    violating = derive_assessment_checks(
        state, [unit], violating_report, "version", matrix_hash="matrix"
    )
    assert violating["C3.correctness"]["result"] == "violated"
    assert violating["C4.constraints"]["result"] == "satisfied"
    assert violating["G3.assessment_design"]["result"] == "satisfied"
