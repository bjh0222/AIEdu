"""Research taxonomies cannot become every task's curriculum by model fiat."""
from copy import deepcopy
import threading
import time

import pytest

from app.v2 import rubric_research as research
from app.v2.contracts import DomainError
from test_rubric_research_contract import source, claim_output, dimension


def dimensions():
    claims = research.bind_claims(claim_output(), [source()])
    dim = dimension(1, claims[0]["claim_id"])
    dim["scope"] = "knowledge"
    return [dim], claims


def decision(dim, result="usable"):
    return {"dimension_id": dim["dimension_id"], "result": result,
            "reason": "Assessment constructs must not replace the actual task's learning targets.",
            "evidence": {"field": "construct_definition", "quote": dim["construct_definition"]},
            "revision_instruction": "Derive coverage denominator from the frozen task's target concepts, not a paper taxonomy."}


def review(dims, claims):
    return research.review_rubric_scope("run", "worker", 0,
        {"context": {"raw_input": "methods, three difficult questions each", "domain_key": "math_logic"}},
        "knowledge", dims, claims)


def test_C_reviews_task_scope_and_complete_rubric_before_product_consumption(monkeypatch):
    dims, claims = dimensions()
    calls = []
    def call(*args, **kw):
        calls.append((args, kw))
        return {"decisions": [decision(dims[0])]}, {"call_id": "c"}
    monkeypatch.setattr(research, "_call", call)
    approved, records, audits = review(dims, claims)
    assert approved == dims and len(records) == 1
    assert calls[0][1]["role"] == "review"
    assert calls[0][0][5]["task_contract"]["raw_input"] == "methods, three difficult questions each"
    assert records[0]["professional_certification"] == "not_calibrated"
    assert audits[0]["call_id"] == "c"


def test_scope_reviews_use_configured_parallelism(monkeypatch):
    dims, claims = dimensions()
    dims = [dict(deepcopy(dims[0]), dimension_id=f"d{i}", source_refs=[f"claim:{i}"]) for i in range(4)]
    claims = [{"claim_id": f"claim:{i}", "claim": f"evidence {i}"} for i in range(4)]
    monkeypatch.setattr(research, "_persisted_scope_input", lambda *a: False)
    monkeypatch.setattr(research, "_quarantined_scope_input", lambda *a: None)
    lock = threading.Lock()
    active = {"now": 0, "max": 0}
    def call(*args, **kwargs):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.1)
        with lock:
            active["now"] -= 1
        data = args[5]
        return {"decisions": [decision(d) for d in data["dimensions"]]}, {"call_id": "c"}
    monkeypatch.setattr(research, "_call", call)
    state = {"context": {"raw_input": "methods", "domain_key": "math_logic"},
             "execution_policy": {"check_concurrency": 3}}
    approved, records, audits = research.review_rubric_scope(
        "run", "worker", 0, state, "knowledge", dims, claims)
    assert len(approved) == 4 and len(records) == 1
    assert active["max"] >= 2


def test_C_revision_is_consumed_and_rechecked_not_merely_relabelled(monkeypatch):
    dims, claims = dimensions()
    revised = deepcopy(dims)
    revised[0]["construct_definition"] = "Coverage of applicable concepts from the frozen task arrangement, not source taxonomies."
    outputs = [({"decisions": [decision(dims[0], "revise")]}, {"call_id": "c1"}),
               ({"dimensions": revised}, {"call_id": "b1"}),
               ({"decisions": [decision(revised[0])]}, {"call_id": "c2"})]
    seen = []
    def call(*args, **kw):
        seen.append(args)
        return outputs.pop(0)
    monkeypatch.setattr(research, "_call", call)
    approved, records, audits = review(dims, claims)
    assert approved == revised and dims != revised
    assert seen[1][5]["scope_review"][0]["revision_instruction"]
    assert seen[2][5]["dimensions"] == revised
    assert [r["decisions"][0]["result"] for r in records] == ["revise", "usable"]
    assert [a["call_id"] for a in audits] == ["c1", "b1", "c2"]


def test_revision_freezes_usable_dimensions_and_only_sends_rejected_dimensions_to_B(monkeypatch):
    dims, claims = dimensions()
    dims.append(dict(deepcopy(dims[0]), dimension_id="needs-repair"))
    before = deepcopy(dims)
    repaired = deepcopy(dims[1])
    repaired["construct_definition"] = "A corrected observable target; all user requirements remain preserved."
    seen = []
    def call(*args, **kwargs):
        stage, data = args[4], args[5]
        seen.append((stage, deepcopy(data)))
        if "revision_" in stage:
            assert data["original_dimensions"] == [before[1]]
            assert data["preserved_dimension_hashes"] == {before[0]["dimension_id"]: research.digest(before[0])}
            assert [d["dimension_id"] for d in data["scope_review"]] == ["needs-repair"]
            assert data["revision_method"] == "per-dimension-scope-revision.v1"
            return {"dimensions": [repaired]}, {"call_id": "repair"}
        return {"decisions": [decision(d, "revise" if d == before[1] else "usable")
            for d in data["dimensions"]]}, {"call_id": "review-"+stage}
    monkeypatch.setattr(research, "_call", call)
    approved, records, audits = review(dims, claims)
    assert dims == before
    assert approved == [before[0], repaired]
    assert len(seen) == 4  # two initial C calls, one B repair, one C recheck
    assert len(records[1]["unchanged_accepted_decisions_reused"]) == 1
    assert records[1]["unchanged_accepted_decisions_reused"][0] == decision(before[0])
    assert seen[-1][1]["dimensions"] == [repaired]


def test_revision_splits_one_bounded_call_per_rejected_dimension(monkeypatch):
    """A combined multi-dimension rewrite exceeded the provider read window."""
    dims, _ = dimensions()
    base = dims[0]
    claims = [{"claim_id": f"claim:{i}", "claim": f"evidence {i}"} for i in range(3)]
    dims = [dict(deepcopy(base), dimension_id=f"D{i}", source_refs=[f"claim:{i}"]) for i in range(3)]
    before = deepcopy(dims)
    seen = []
    def call(*args, **kwargs):
        stage, data = args[4], args[5]
        seen.append((stage, deepcopy(data)))
        if "research_scope_revision_" in stage:
            # 评审只出意见；维度改写由生成角色执行，不由评审自己改。
            assert kwargs["role"] == "generation"
            dim = data["original_dimensions"][0]
            assert [d["dimension_id"] for d in data["original_dimensions"]] == [dim["dimension_id"]]
            assert [r["dimension_id"] for r in data["scope_review"]] == [dim["dimension_id"]]
            assert data["source_claims"] == [c for c in claims if c["claim_id"] in dim["source_refs"]]
            return {"dimensions": [deepcopy(dim)]}, {"call_id": "repair-" + stage}
        assert kwargs["role"] == "review"
        attempt_one = "_1_batch" in stage
        return {"decisions": [decision(d, "usable" if attempt_one else "revise") for d in data["dimensions"]]
            }, {"call_id": "review-" + stage}
    monkeypatch.setattr(research, "_call", call)
    approved, records, audits = review(dims, claims)
    assert dims == before
    assert [d["dimension_id"] for d in approved] == ["D0", "D1", "D2"]
    revisions = [(s, d) for s, d in seen if "research_scope_revision_" in s]
    assert [s for s, _ in revisions] == [f"research_scope_revision_knowledge_1_D{i}" for i in range(3)]
    assert [d["original_dimensions"][0]["dimension_id"] for _, d in revisions] == ["D0", "D1", "D2"]
    assert [a["call_id"] for a in audits if a["call_id"].startswith("repair-")] == \
        ["repair-" + s for s, _ in revisions]


@pytest.mark.parametrize("fault", ["rename", "drop", "extra"])
def test_revision_cannot_rename_drop_or_overwrite_preserved_dimensions(monkeypatch, fault):
    dims, claims = dimensions()
    dims.append(dict(deepcopy(dims[0]), dimension_id="needs-repair"))
    before = deepcopy(dims)
    repaired = deepcopy(dims[1])
    if fault == "rename": repaired["dimension_id"] = "renamed"
    def call(*args, **kwargs):
        stage, data = args[4], args[5]
        if "revision_" in stage:
            proposed = [repaired] if fault != "drop" else [before[0]]
            if fault == "extra": proposed.append(before[0])
            return {"dimensions": proposed}, {}
        return {"decisions": [decision(d, "revise" if d["dimension_id"] == "needs-repair" else "usable")
            for d in data["dimensions"]]}, {}
    monkeypatch.setattr(research, "_call", call)
    with pytest.raises(DomainError, match="RESEARCH_SCOPE_REVISION_IDENTITY_MISMATCH"):
        review(dims, claims)
    assert dims == before


@pytest.mark.parametrize("fault", ["missing", "duplicate", "quote", "field", "unknown_without_instruction"])
def test_unbound_or_incomplete_review_cannot_approve_scope(fault):
    dims, _ = dimensions()
    row = decision(dims[0])
    output = {"decisions": [row]}
    if fault == "missing": output["decisions"] = []
    if fault == "duplicate": output["decisions"] *= 2
    if fault == "quote": row["evidence"]["quote"] = "invented evidence"
    if fault == "field": row["evidence"] = None
    if fault == "unknown_without_instruction": row.update(result="undecidable", revision_instruction="")
    with pytest.raises(DomainError):
        research._scope_decisions(output, dims)


def test_scope_decision_contract_is_corrected_before_literal_evidence_binding(monkeypatch):
    dims, _ = dimensions()
    malformed = {"decisions": [decision(dims[0]), decision(dims[0])]}
    corrected = {"decisions": [decision(dims[0])]}
    calls = []

    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return corrected, {"call_id": "contract-correction"}

    monkeypatch.setattr(research, "_call", call)
    rows, audits = research.bind_scope_decisions(
        "run", "worker", 0, {}, "scope-batch", {"dimensions": dims, "task_contract": {}}, malformed
    )
    assert rows == corrected["decisions"]
    assert calls[0][0][4] == "scope-batch_contract_retry_1"
    assert calls[0][0][5]["failure_code"] == "RESEARCH_SCOPE_REVIEW_INCOMPLETE"
    assert calls[0][1]["role"] == "review"
    assert audits == [{"call_id": "contract-correction"}]


def test_scope_contract_correction_cannot_fill_missing_dimension_as_usable(monkeypatch):
    dims, _ = dimensions()
    malformed = {"decisions": []}
    undecidable = decision(dims[0], "undecidable")
    corrected = {"decisions": [undecidable]}

    monkeypatch.setattr(research, "_call", lambda *args, **kwargs: (corrected, {"call_id": "c"}))
    rows, _ = research.bind_scope_decisions(
        "run", "worker", 0, {}, "scope-batch", {"dimensions": dims, "task_contract": {}}, malformed
    )
    assert rows[0]["result"] == "undecidable"
    assert rows[0]["revision_instruction"]


def test_unusable_or_unknown_scope_stops_after_bounded_revisions(monkeypatch):
    dims, claims = dimensions()
    seen = []
    def call(*args, **kw):
        seen.append(args[4])
        return ({"decisions": [decision(dims[0], "undecidable")]} if "review_" in args[4]
                else {"dimensions": dims}), {"call_id": str(len(seen))}
    monkeypatch.setattr(research, "_call", call)
    with pytest.raises(DomainError, match="RESEARCH_RUBRIC_SCOPE_NOT_USABLE"):
        review(dims, claims)
    assert len(seen) == 1


def test_scope_batches_cover_every_dimension_with_only_its_bound_claims(monkeypatch):
    dims, claims = dimensions()
    dims = [dict(deepcopy(dims[0]), dimension_id=f"d{i}", source_refs=[f"claim:{i}"]) for i in range(5)]
    claims = [{"claim_id": f"claim:{i}", "claim": f"source evidence {i}"} for i in range(5)]
    seen = []
    def call(*args, **kwargs):
        data = args[5]
        seen.append((args[4], data))
        assert kwargs["role"] == "review" and args[8] is None
        assert len(data["dimensions"]) == 1
        assert {c["claim_id"] for c in data["source_claims"]} == {r for d in data["dimensions"] for r in d["source_refs"]}
        return {"decisions": [decision(d) for d in data["dimensions"]]}, {"call_id": f"c{len(seen)}"}
    monkeypatch.setattr(research, "_call", call)
    approved, records, audits = review(dims, claims)
    assert approved == dims and len(seen) == 5
    assert len(records[0]["decisions"]) == 5 and len(records[0]["batches"]) == 5
    assert len(audits) == 5 and len({stage for stage, _ in seen}) == 5


@pytest.mark.parametrize("domain,user_text,scene", [
    ("math_logic", "按求解方法分类，每类三道较难大题并详解", "exercise"),
    ("cs_it", "按调试策略组织案例，每组两个完整故障分析", "practical_tutorial"),
    ("humanities", "比较三种历史解释方法，不出选择题", "knowledge_tutorial"),
    ("general", "先逐章梳理，再写全书综合，不新增习题", "reading_guide"),
])
def test_pre_A_research_preserves_request_without_consuming_undecided_template(domain, user_text, scene):
    original = {"context": {"raw_input": user_text, "domain_key": domain,
        "spec_bindings": [{"spec_id": "source", "scene": scene, "artifact_role": "tutorial",
            "snapshot_hash": "s"*64,
            "snapshot": {"content_structure": "TEMPLATE_FIXED_TYPES_AND_ANSWER_ONLY_LIMIT"}}]}}
    before = deepcopy(original)
    task = research.research_scope_task(original)
    assert task["raw_input"] == user_text and task["domain_key"] == domain
    assert task["format_applicability"]["status"] == "pending_A"
    assert task["format_applicability"]["source_specs"][0]["scene"] == scene
    assert "TEMPLATE_FIXED_TYPES_AND_ANSWER_ONLY_LIMIT" not in str(task)
    assert original == before  # source bytes/fields are never rewritten.


def test_frozen_A_design_and_shared_authority_reach_both_scope_review_and_revision(monkeypatch):
    from app.v2.adaptive_format import POLICY_PROMPT
    dims, claims = dimensions()
    outputs = [({"decisions": [decision(dims[0], "revise")]}, {}),
               ({"dimensions": dims}, {}), ({"decisions": [decision(dims[0])]}, {})]
    seen = []
    def call(*args, **kwargs):
        seen.append(args)
        return outputs.pop(0)
    monkeypatch.setattr(research, "_call", call)
    design = {"mode": "custom", "question_form": "constructed_response_only", "explanation_policy": "full"}
    state = {"context": {"raw_input": "explicit complete task"}, "plan": {"output_design": design}}
    research.review_rubric_scope("run", "worker", 0, state, "knowledge", dims, claims)
    for args in seen:
        assert POLICY_PROMPT in args[6]
        assert args[5]["task_contract"]["format_applicability"] == {
            "status": "frozen_by_A", "frozen_output_design": design, "source_specs": [],
            "authority": "complete_user_requirements_then_A_applicability_not_raw_template_defaults"}
        assert args[5]["method_version"] == research.SCOPE_REVIEW_METHOD
        assert args[5]["semantic_policy_hash"] == research.sha(POLICY_PROMPT)
        assert args[5]["operational_policy_hash"] == research.sha(research.SCOPE_OPERATIONAL_POLICY)


def test_scope_policy_cannot_trade_prior_demand_for_difficulty_labels(monkeypatch):
    from app.v2.adaptive_format import POLICY_PROMPT
    dims, claims = dimensions()
    weak = deepcopy(dims)
    weak[0]["construct_definition"] = "Author presentation or harder labels suffice for a difficult-exercise request."
    corrected = deepcopy(dims)
    corrected[0]["construct_definition"] = "Count distinct exercises structurally; require a separate evidence-based prior cognitive-demand review."
    outputs = [({"decisions": [decision(weak[0], "revise")]}, {}),
               ({"dimensions": corrected}, {}), ({"decisions": [decision(corrected[0])]}, {})]
    seen = []
    def call(*args, **kwargs):
        seen.append(args)
        return outputs.pop(0)
    monkeypatch.setattr(research, "_call", call)
    approved, records, _ = review(weak, claims)
    assert approved == corrected and weak[0]["construct_definition"].startswith("Author presentation")
    assert [r["decisions"][0]["result"] for r in records] == ["revise", "usable"]
    assert "MUST be revised" in seen[0][6]
    for args in seen:
        assert POLICY_PROMPT in args[6]
        assert "alone never satisfies a requested difficulty level" in args[6]
        assert "does not waive or downgrade the executable prior-demand check" in args[6]


def test_scope_policy_change_invalidates_cached_scope_inputs(monkeypatch):
    from app.v2 import adaptive_format
    dims, claims = dimensions()
    task = {"raw_input": "unchanged task"}
    before = research._scope_requests({}, "knowledge", 0, dims, claims, task)
    monkeypatch.setattr(adaptive_format, "POLICY_PROMPT", adaptive_format.POLICY_PROMPT+" new evidence policy")
    after = research._scope_requests({}, "knowledge", 0, dims, claims, task)
    assert before[0][0] == after[0][0]
    assert before[0][1]["dimensions"] == after[0][1]["dimensions"]
    assert research.digest(before[0][1]) != research.digest(after[0][1])


def test_research_artifact_cache_identity_includes_prompt_and_profile(monkeypatch):
    from types import SimpleNamespace

    profile = SimpleNamespace(
        llm_base_url="https://review.invalid/v1", llm_model="glm-5.3-flash",
        llm_model_family="fixture", llm_timeout=120, llm_temperature=0.3,
        llm_thinking="enabled", llm_reasoning_effort="high", llm_json_mode=True,
        llm_max_tokens=8192, llm_output_limit_parameter="max_tokens")
    monkeypatch.setattr("app.v2.model_roles.for_role", lambda role: profile)
    first = research._research_cache_identity("scope", {"x": 1}, "policy-a", 8192, "review")
    changed_prompt = research._research_cache_identity("scope", {"x": 1}, "policy-b", 8192, "review")
    changed_model = SimpleNamespace(**{**profile.__dict__, "llm_model": "other-reviewer"})
    monkeypatch.setattr("app.v2.model_roles.for_role", lambda role: changed_model)
    changed_profile = research._research_cache_identity("scope", {"x": 1}, "policy-a", 8192, "review")
    assert first != changed_prompt
    assert first != changed_profile


def test_operational_anchor_policy_reaches_C_and_full_revision_without_lowering_user_requirements(monkeypatch):
    dims, claims = dimensions()
    weak = deepcopy(dims)
    weak[0]["anchors"] = {
        "0": "Some required boundaries are missing or an unsupported claim exists.",
        "1": "Some required boundaries are missing but no unsupported claim exists.",
        "2": "All required boundaries are covered and no unsupported claim exists."}
    repaired = deepcopy(dims)
    repaired[0]["anchors"] = {
        "0": "No required boundary is covered, or an unsupported claim exists.",
        "1": "Some but not all required boundaries are covered and no unsupported claim exists.",
        "2": "All required boundaries are covered and no unsupported claim exists."}
    outputs = [({"decisions": [decision(weak[0], "revise")]}, {}),
               ({"dimensions": repaired}, {}), ({"decisions": [decision(repaired[0])]}, {})]
    seen = []
    def call(*args, **kwargs):
        seen.append(args)
        return outputs.pop(0)
    monkeypatch.setattr(research, "_call", call)
    approved, records, _ = review(weak, claims)
    assert approved == repaired and weak != repaired
    assert [r["decisions"][0]["result"] for r in records] == ["revise", "usable"]
    for args in seen:
        assert research.SCOPE_OPERATIONAL_POLICY in args[6]
        assert "mutually exclusive and collectively exhaustive" in args[6]
        assert "A arrangement cannot relax an explicit" in args[6]
        assert args[5]["operational_policy_hash"] == research.sha(research.SCOPE_OPERATIONAL_POLICY)


def test_operational_policy_change_invalidates_old_acceptance_cache(monkeypatch):
    dims, claims = dimensions()
    before = research._scope_requests({}, "knowledge", 0, dims, claims, {})
    monkeypatch.setattr(research, "SCOPE_OPERATIONAL_POLICY", research.SCOPE_OPERATIONAL_POLICY+" clarified edge")
    after = research._scope_requests({}, "knowledge", 0, dims, claims, {})
    assert before[0][1]["dimensions"] == after[0][1]["dimensions"]
    assert research.digest(before[0][1]) != research.digest(after[0][1])


def test_only_explicitly_isolated_exact_batch_splits_preserving_other_inputs(monkeypatch):
    dims, claims = dimensions()
    dims = [dict(deepcopy(dims[0]), dimension_id=f"d{i}", source_refs=[f"claim:{i}"]) for i in range(6)]
    claims = [{"claim_id": f"claim:{i}", "claim": f"complete evidence {i}"} for i in range(6)]
    task = {"raw_input": "same actual request"}
    monkeypatch.setattr(research, "_persisted_scope_input", lambda *a: True)
    original = research._scope_requests({}, "knowledge", 0, dims, claims, task)
    binding = {"call_id": "exact-isolated-call", "request_hash": "h", "stage": original[2][0]}
    def isolated(state, stage, data):
        return binding if stage == original[2][0] and data == original[2][1] else None
    monkeypatch.setattr(research, "_quarantined_scope_input", isolated)
    split = research._scope_requests({}, "knowledge", 0, dims, claims, task)
    assert split[:2] == original[:2]  # exact successful-input caches remain reusable.
    assert [s for s, _, _ in split[2:]] == [original[2][0]+"_part_0", original[2][0]+"_part_1"]
    assert [d["dimension_id"] for _, data, _ in split for d in data["dimensions"]] == [d["dimension_id"] for d in dims]
    for _, data, reserve in split[2:]:
        assert len(data["dimensions"]) == 1 and data["split_origin"] == binding and reserve is None
        assert data["source_claims"] == [claims[4+data["batch_part_index"]]]


def test_new_scope_partitions_preserve_complete_dimensions_and_do_not_replay_whole_batch(monkeypatch):
    dims, claims = dimensions()
    dims = [dict(deepcopy(dims[0]), dimension_id=f"d{i}") for i in range(4)]
    before = deepcopy(dims)
    new = research._scope_requests({}, "knowledge", 0, dims, claims, {})
    assert [d for _, data, _ in new for d in data["dimensions"]] == before
    assert all(len(data["dimensions"]) == 1 and reserve is None for _, data, reserve in new)
    assert all(data["split_origin"]["method_version"] == "proactive-single-dimension-scope.v1"
               for _, data, _ in new)
    monkeypatch.setattr(research, "_persisted_scope_input", lambda state, stage, data: stage.endswith("batch_0"))
    mixed = research._scope_requests({}, "knowledge", 0, dims, claims, {})
    assert [len(data["dimensions"]) for _, data, _ in mixed] == [2, 1, 1]
    assert mixed[0][0].endswith("batch_0") and mixed[0][2] is None
    assert dims == before


def test_whole_scope_cache_requires_exact_hash_and_input(monkeypatch):
    from app.v2.contracts import canonical, digest
    from app.v2.storage import BlobStore
    data = {"dimensions": ["complete-a", "complete-b"]}
    value = {"input_hash": digest(data), "output": {"decisions": []}, "audit": {"call_id": "success"}}
    monkeypatch.setattr(BlobStore, "get", lambda self, key: canonical(value))
    state = {"research_artifacts": {"stage": {"blob_hash": "b", "hash": digest(value)}}}
    assert research._persisted_scope_input(state, "stage", data)
    assert not research._persisted_scope_input(state, "stage", {"dimensions": ["different"]})
    state["research_artifacts"]["stage"]["hash"] = "wrong"
    assert not research._persisted_scope_input(state, "stage", data)


def test_split_recovery_requires_durable_risk_ack_and_exact_call_request(client):
    from test_unknown_quarantine_v2 import unknown_run
    from app.v2.contracts import QuarantineUnknown
    from app.v2.runtime import dump, job, load, write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import quarantine_unknown
    _, run_id, payload = unknown_run(client)
    stage, data = "research_scope_review_knowledge_0_batch_2", {"scope": "knowledge", "dimensions": ["exact", "input"]}
    with write() as db:
        state = load(job(db, run_id).state_json)
        call = db.get(CallDispatch, payload["calls"][0]["call_id"])
        call.unit_id = "research:"+stage
        call.input_hash = state["context"]["context_hash"]
        call.request_json = dump({"messages": [{"role": "user", "content": dump(data)}]})
    quarantine_unknown(run_id, QuarantineUnknown(**payload), "isolate-split")
    with write() as db:
        state = load(job(db, run_id).state_json)
    assert research._quarantined_scope_input(state, stage, data)["call_id"] == payload["calls"][0]["call_id"]
    assert research._quarantined_scope_input(state, stage, {**data, "dimensions": ["different"]}) is None
    assert research._quarantined_scope_input(state, stage+"different", data) is None
    no_ack = deepcopy(state)
    no_ack["unknown_quarantines"][payload["calls"][0]["call_id"]]["acknowledge_possible_duplicate_cost"] = False
    assert research._quarantined_scope_input(no_ack, stage, data) is None
    with write() as db:
        db.get(CallDispatch, payload["calls"][0]["call_id"]).status = "unknown"
    assert research._quarantined_scope_input(state, stage, data) is None


def test_scope_evidence_is_corrected_by_C_without_changing_any_semantic_decision(monkeypatch):
    dims, _ = dimensions()
    original = {"decisions": [decision(dims[0], "revise")]}
    original["decisions"][0]["evidence"]["field"] = "indicators.0.numerator"
    before = deepcopy(original)
    calls = []
    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return {"dimension_id": dims[0]["dimension_id"],
                "evidence": {"field": "construct_definition", "quote": dims[0]["construct_definition"]},
                "result": "usable"}, {"call_id": "literal-correction"}
    monkeypatch.setattr(research, "_call", call)
    rows, audits = research.bind_scope_decisions("r", "w", 0, {}, "scope-batch",
        {"dimensions": dims, "task_contract": {"raw_input": "actual request"}}, original)
    assert original == before and rows[0]["result"] == "revise"
    assert {k: v for k, v in rows[0].items() if k != "evidence"} == {
        k: v for k, v in original["decisions"][0].items() if k != "evidence"}
    assert calls[0][0][4] == "scope-batch_evidence_binding_retry_1"
    assert calls[0][0][5]["validation_error"]["dimension_id"] == dims[0]["dimension_id"]
    assert calls[0][1]["role"] == "review" and calls[0][0][8] == 8192
    assert audits == [{"call_id": "literal-correction"}]


@pytest.mark.parametrize("fault", ["wrong_id", "null", "paraphrase"])
def test_unbindable_scope_evidence_remains_failed_after_bounded_C_corrections(monkeypatch, fault):
    dims, _ = dimensions()
    original = {"decisions": [decision(dims[0], "undecidable")]}
    original["decisions"][0]["evidence"]["quote"] = "unbound paraphrase"
    calls = []
    def call(*args, **kwargs):
        calls.append(args[4])
        return {"dimension_id": "wrong" if fault == "wrong_id" else dims[0]["dimension_id"],
                "evidence": None if fault == "null" else {"field": "construct_definition", "quote": "not present"}}, {}
    monkeypatch.setattr(research, "_call", call)
    with pytest.raises(DomainError, match="RESEARCH_SCOPE_REVIEW_EVIDENCE_UNBOUND"):
        research.bind_scope_decisions("r", "w", 0, {}, "scope-batch",
            {"dimensions": dims, "task_contract": {}}, original)
    assert calls == ["scope-batch_evidence_binding_retry_1", "scope-batch_evidence_binding_retry_2"]
    assert original["decisions"][0]["result"] == "undecidable"

def test_scope_evidence_accepts_only_the_legacy_decision_procedure_alias():
    dim = {
        "dimension_id": "D2",
        "c_decision_procedure": [
            "Enumerate actual types.",
            "the hard requirement is deferred to a separate prior-demand check",
        ],
    }
    row = {
        "dimension_id": "D2",
        "result": "usable",
        "reason": "actual task types and prior demand remain separate",
        "evidence": {
            "field": "decision_procedure.1",
            "quote": "deferred to a separate prior-demand check",
        },
        "revision_instruction": None,
    }
    bound = research._scope_decisions({"decisions": [row]}, [dim])
    assert bound == [row]
    assert row["evidence"]["field"] == "c_decision_procedure.1"

def test_scope_requests_never_impose_an_internal_output_cap():
    dims, claims = dimensions()
    before = deepcopy(dims)
    requests = research._scope_requests({}, "knowledge", 0, dims, claims, {})
    assert requests and all(reserve is None for _, _, reserve in requests)
    assert dims == before

def test_mixed_scope_keeps_usable_dimensions_and_records_explicit_gap(monkeypatch):
    dims, claims = dimensions()
    dims = [dims[0], dict(deepcopy(dims[0]), dimension_id="still-unusable")]
    seen = []
    def call(*args, **kw):
        seen.append(args[4])
        if "review_" in args[4]:
            return {"decisions": [decision(d, "usable" if d["dimension_id"] == dims[0]["dimension_id"] else "undecidable")
                                  for d in args[5]["dimensions"]]}, {"call_id": str(len(seen))}
        return {"dimensions": [deepcopy(d) for d in args[5]["original_dimensions"]]}, {"call_id": str(len(seen))}
    monkeypatch.setattr(research, "_call", call)
    usable, records, audits = review(dims, claims)
    assert [d["dimension_id"] for d in usable] == [dims[0]["dimension_id"]]
    assert records[-1]["scope_completion_status"] == "partial_unresolved"
    assert [g["dimension_id"] for g in records[-1]["unresolved_dimensions"]] == ["still-unusable"]
    assert records[-1]["unresolved_dimensions"][0]["decision"]["result"] == "undecidable"


def test_partial_research_scope_adds_a_required_blocker_not_a_fake_pass():
    from app.v2.quality import compile_plan, deterministic_checks
    rubric = {"rubric_id": "research-gap", "version": 1, "hash": "h", "dimensions": [],
              "scope_completion_status": "partial_unresolved",
              "scope_gaps": [{"dimension_id": "D6", "decision": {"result": "undecidable"}}]}
    task = {"domain_key": "chemistry_materials", "spec_bindings": [{"scene": "knowledge_tutorial", "artifact_role": "tutorial", "snapshot": {}}],
            "requested_artifacts": ["tutorial"], "subject_bindings": [{"domain_key": "chemistry_materials", "branch": "", "unit_keys": []}],
            "research_rubrics": [{"id": "research-gap", "version": 1, "hash": "h", "content": rubric}]}
    plan = compile_plan(task)
    item = next(i for i in plan["check_items"] if i["verifier_binding"] == "research_scope_completion.v1")
    assert item["required"] is True and item["scope_gaps"][0]["dimension_id"] == "D6"
    unit = {"unit_id": "u", "body": "content", "content_hash": __import__("app.v2.contracts", fromlist=["sha"]).sha("content")}
    report = deterministic_checks({"check_items": [item], "check_plan_hash": "p"}, [unit], "v")
    assert report["checks"][0]["result"] == "violated"
    assert report["checks"][0]["reason"] == "RESEARCH_RUBRIC_SCOPE_PARTIAL"
    assert report["tqi_verdict"] == "needs_repair"
    assert report["blocking_reasons"][0]["item_id"] == item["item_id"]

def test_undecidable_dimension_is_preserved_not_forced_into_rewrite(monkeypatch):
    dims, claims = dimensions()
    dims = [dims[0], dict(deepcopy(dims[0]), dimension_id="D4_ANSWER_STEM_CONSISTENCY")]
    calls = []
    review_count = 0
    def call(*args, **kw):
        nonlocal review_count
        stage, data = args[4], args[5]
        calls.append((stage, data))
        if "review_" in stage:
            review_count += 1
            rows = []
            for dim in data["dimensions"]:
                result = "revise" if review_count == 1 and dim["dimension_id"] == dims[0]["dimension_id"] else "undecidable"
                if review_count > 1 and dim["dimension_id"] == dims[0]["dimension_id"]:
                    result = "usable"
                rows.append(decision(dim, result))
            return {"decisions": rows}, {"call_id": stage}
        revised = deepcopy(data["original_dimensions"][0])
        revised["construct_definition"] += " Revised for applicability."
        return {"dimensions": [revised]}, {"call_id": stage}
    monkeypatch.setattr(research, "_call", call)
    usable, records, _ = review(dims, claims)
    revision_calls = [data for stage, data in calls if "revision_" in stage]
    assert revision_calls
    assert [d["dimension_id"] for d in revision_calls[0]["original_dimensions"]] == [dims[0]["dimension_id"]]
    assert dims[1]["dimension_id"] in revision_calls[0]["preserved_dimension_hashes"]
    assert [d["dimension_id"] for d in usable] == [dims[0]["dimension_id"]]
    assert records[-1]["scope_completion_status"] == "partial_unresolved"
    assert [g["dimension_id"] for g in records[-1]["unresolved_dimensions"]] == [dims[1]["dimension_id"]]

