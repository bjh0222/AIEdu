"""Synthetic task-derived organization; no real model or user database."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.v2.adaptive_format import LAYOUT, apply_design, document_end_answers, validated_design
from app.v2.contracts import DomainError, digest, sha
from app.v2.items import normalize_items, render_unit_items
from app.v2.planning import apply_arrangement, arrangement


def design(**changes):
    return {"mode": "method_practice", "grouping_basis": "实际求解方法分类", "rationale": "按照用户的数学求解方法需求选择结构而非教学难度分桶",
        "request_quotes": ["每一类题型附带3个较难的题目"], "question_form": "constructed_response_only",
        "explanation_policy": "full", "answer_placement": "unit_end", "quantity_per_group": 3,
        "template_adaptations": [{"field": "content_structure", "reason": "用户明确要求方法分类，不采用默认判断栏目"}],
        "difficulty_target": {"level": "较难", "minimum_nonroutine_operations": 1, "minimum_concept_interactions": 1,
            "minimum_dependency_depth": 3, "rationale": "需要非套路选择与至少三个依赖推理环节，而非增加算量"}, **changes}


def context():
    return {"raw_input": "为我总结一种数学方法，每一类题型附带3个较难的题目", "hard_constraints": [],
            "requested_artifacts": ["exercise", "answer"], "material_ids": [], "spec_bindings": []}


def teaching(output_design=None, titles=("合成方法甲", "合成方法乙")):
    return arrangement({"teaching_arrangement": {"goals": ["符合原任务"], "learner_prerequisites": [], "scope": "focused",
        "quality_targets": ["较难且方法明确"], "unresolved_conflicts": [], "generation_prompt": "依方法分组生成",
        "output_design": output_design or design(),
        "units": [{"title": t, "concepts": ["合成概念"], "question_types": ["计算证明大题"], "difficulty_basis": {},
            "quantity": 3, "material_mapping": [], "acceptance_criteria": ["三个实质大题"], "artifact_role": "exercise"} for t in titles]}})


def planned(output_design=None):
    a = teaching(output_design)
    return apply_arrangement(apply_design(context(), {"units": []}, a, []), a, require_complete=True)["units"][0]


def candidates():
    return {"method_overview": "合成方法说明，与题目分离。", "items": [{"question_form": "constructed_response",
        "stem_markdown": f"合成大题{n}，根据给定关系完成证明。", "difficulty_basis": {"prerequisites": ["合成概念"],
            "cognitive_operations": ["分析"], "nonroutine_operations": ["选择变换"], "concept_interactions": ["概念联动"],
            "dependency_depth": 3, "rationale": "合成依据而非实际校准"},
        "answer": {"final": "合成答案", "reasoning": "合成的完整关键推导", "display_reasoning": "精炼说明"}} for n in range(3)]}


def test_A_owns_actual_groups_and_B_quantities_forms_and_projection():
    unit = planned()
    output = candidates()
    items = normalize_items(output, "exercise", unit["unit_id"], LAYOUT, [], unit)
    unit.update(items=items, method_overview=output["method_overview"])
    body = render_unit_items(items, unit)
    assert unit["title"] == "合成方法甲" and len(items) == 3
    assert "方法说明" in body and body.index("方法说明") < body.index("**1.**")
    assert "判断题" not in body and "基础概念题" not in body and "- [ ]" not in body
    assert "完整关键推导" in body and [i["ordinal"] for i in items] == [1, 2, 3]


@pytest.mark.parametrize("fault,code", [("quantity", "TASK_ITEM_QUANTITY_MISMATCH"), ("form", "TASK_QUESTION_FORM_MISMATCH"),
    ("basis", "ITEM_DIFFICULTY_BASIS_REQUIRED"), ("low", "ITEM_DIFFICULTY_DECLARATION_BELOW_TARGET")])
def test_B_cannot_drop_quantity_use_judgment_or_declare_routine_as_hard(fault, code):
    unit, output = planned(), candidates()
    if fault == "quantity": output["items"].pop()
    if fault == "form": output["items"][0]["question_form"] = "judgement"
    if fault == "basis": output["items"][0].pop("difficulty_basis")
    if fault == "low": output["items"][0]["difficulty_basis"]["dependency_depth"] = 1
    with pytest.raises(DomainError, match=code):
        normalize_items(output, "exercise", unit["unit_id"], LAYOUT, [], unit)


@pytest.mark.parametrize("fault,code", [("quote", "OUTPUT_DESIGN_REQUEST_UNBOUND"), ("low", "HARD_TASK_DIFFICULTY_TARGET_TOO_LOW"),
    ("quantity", "EXPLICIT_PER_GROUP_QUANTITY_NOT_BOUND")])
def test_A_cannot_weaken_explicit_task_or_invent_request_quote(fault, code):
    value = design()
    if fault == "quote": value["request_quotes"] = ["用户没有说过"]
    if fault == "low": value["difficulty_target"]["minimum_nonroutine_operations"] = 0
    if fault == "quantity": value["quantity_per_group"] = 2
    with pytest.raises(DomainError, match=code): validated_design(value, context())


def test_document_end_answers_are_projection_of_exact_coupled_items():
    unit, output = planned(design(answer_placement="document_end")), candidates()
    unit["items"] = normalize_items(output, "exercise", unit["unit_id"], LAYOUT, [], unit)
    unit["method_overview"] = output["method_overview"]
    assert "参考答案" not in render_unit_items(unit["items"], unit)
    answers = document_end_answers([unit])
    assert "参考答案" in answers and answers.count("合成的完整关键推导") == 3
    assert "合成大题" not in answers


def test_selected_sources_cannot_disappear_in_adaptive_plan():
    ctx, value = context(), teaching()
    ctx["material_ids"] = ["s"]
    chunks = [{"source_id": "s", "chunk_id": "s:0"}]
    with pytest.raises(DomainError, match="OUTPUT_DESIGN_MATERIAL_MAPPING_REQUIRED"):
        apply_design(ctx, {}, value, chunks)
    for unit in value["content"]["units"]: unit["material_mapping"] = ["s"]
    assert apply_design(ctx, {}, value, chunks)["units"][0]["material_chunk_ids"] == ["s:0"]


def test_method_organization_is_not_a_universal_question_form_template():
    ctx = context() | {"raw_input": "按科学探究方法整理，每类给选择题和简答题", "requested_artifacts": ["exercise", "answer"]}
    value = design(question_form="mixed", difficulty_target=None, quantity_per_group=None,
                   request_quotes=["按科学探究方法整理"])
    assert validated_design(value, ctx)["question_form"] == "mixed"


def test_non_item_history_tutorial_does_not_gain_a_hard_math_target():
    ctx = context() | {"raw_input": "讲解历史研究中的难题与史料互证", "requested_artifacts": ["tutorial"]}
    value = design(mode="tutorial", question_form="not_applicable", difficulty_target=None,
                   quantity_per_group=None, request_quotes=["讲解历史研究"])
    assert validated_design(value, ctx)["difficulty_target"] is None


def test_no_universal_step_count_can_replace_actual_challenge_evidence():
    value = design()
    value["difficulty_target"]["minimum_dependency_depth"] = 2
    # This only validates A's declaration. It never signs this as a difficult item.
    assert validated_design(value, context())["difficulty_target"]["minimum_dependency_depth"] == 2


def test_reading_adapts_headings_but_keeps_all_chapter_digest_dependencies():
    ctx = context() | {"raw_input": "按章导读这份资料，最后整合全书论证", "requested_artifacts": ["tutorial"], "material_ids": ["s"]}
    output = design(mode="reading", question_form="not_applicable", difficulty_target=None, quantity_per_group=None,
                    request_quotes=["按章导读这份资料"])
    value = teaching(output, titles=("第一章", "全书论证"))
    chapter, whole = value["content"]["units"]
    chapter.update(artifact_role="chapter", material_mapping=["s"], summary_heading="论证摘要", required_sections=["论证摘要"])
    whole.update(artifact_role="book_level", material_mapping=[], depends_on_titles=["第一章"])
    plan = apply_arrangement(apply_design(ctx, {}, value, [{"source_id": "s", "chunk_id": "s:0"}]), value, require_complete=True)
    first, last = plan["units"]
    assert last["depends_on"] == [first["unit_id"]] and last["material_chunk_ids"] == []
    assert first["summary_heading"] == "论证摘要"
    whole["depends_on_titles"] = []
    with pytest.raises(DomainError, match="READING_DIGEST_DEPENDENCIES_REQUIRED"):
        apply_design(ctx, {}, value, [{"source_id": "s", "chunk_id": "s:0"}])


def test_explicit_replan_is_same_task_new_plan_no_content_replay_and_parent_immutable(client):
    from test_task_run_v2 import task_run
    from app.db import SessionLocal
    from app.v2.contracts import Resume
    from app.v2.runtime import job, load
    from app.v2.tasks import resume
    _, _, snap = task_run(client)
    with SessionLocal() as db:
        old = job(db, snap["run_id"])
        frozen = deepcopy(load(old.state_json))
        parent_id = old.id
    payload = Resume(expected_revision=snap["state_revision"], checkpoint_hash=snap["checkpoint_hash"],
                     replan=True, acknowledge_new_generation=True)
    result = resume(snap["run_id"], payload, "same-task-replan")
    assert resume(snap["run_id"], payload, "same-task-replan") == result
    with SessionLocal() as db:
        old, new = job(db, snap["run_id"]), job(db, result["run_id"])
        state, parent = load(new.state_json), load(old.state_json)
        assert new.goal_id == old.goal_id and new.parent_job_id == parent_id
        assert state["version_id"] == snap["version_id"] and state["replan_from_version_id"] == snap["version_id"]
        assert state["plan"]["units"] == [] and state["recovered_candidates"] == {} and state["intent_pending"]
        assert state["execution_policy"]["task_adaptive_format"]
        assert parent["plan"] == frozen["plan"] and parent["execution_policy"] == frozen["execution_policy"]
        assert parent["budget"] == frozen["budget"]


def test_replan_requires_ack_and_excludes_fixed_recheck(client):
    from test_task_run_v2 import task_run
    from app.v2.contracts import Resume
    from app.v2.tasks import resume
    _, _, snap = task_run(client)
    with pytest.raises(DomainError, match="REPLAN_REQUIRES_EXPLICIT_GENERATION_ACK"):
        resume(snap["run_id"], Resume(expected_revision=snap["state_revision"], checkpoint_hash=snap["checkpoint_hash"], replan=True), "no-ack")


def test_research_enable_is_explicit_same_ancestry_and_preserves_parent(client):
    from test_task_run_v2 import task_run
    from app.db import SessionLocal
    from app.v2.contracts import Resume
    from app.v2.runtime import job, load
    from app.v2.tasks import resume, goal
    _, _, snap = task_run(client)
    with SessionLocal() as db:
        parent_context = deepcopy(load(job(db, snap["run_id"]).state_json)["context"])
    payload = Resume(expected_revision=snap["state_revision"], checkpoint_hash=snap["checkpoint_hash"],
                     replan=True, acknowledge_new_generation=True, enable_research=True)
    result = resume(snap["run_id"], payload, "research-replan")
    assert resume(snap["run_id"], payload, "research-replan") == result
    with SessionLocal() as db:
        parent, child = job(db, snap["run_id"]), job(db, result["run_id"])
        old, state = load(parent.state_json), load(child.state_json)
        assert old["context"] == parent_context
        assert child.goal_id == parent.goal_id and child.parent_job_id == parent.id
        assert state["context"]["research_request"]["enabled"] is True
        assert state["context"]["context_hash"] != parent_context["context_hash"]
        assert state["context"]["raw_input_hash"] == parent_context["raw_input_hash"]
        assert state["context"]["spec_bindings"] == parent_context["spec_bindings"]
        assert state["budget"] == old["budget"]
        assert state["replan_amendment"]["parent_context_hash"] == parent_context["context_hash"]
        assert load(goal(db, child.goal_id).task_state_json)["context"] == state["context"]


def test_plain_resume_cannot_silently_enable_research(client):
    from test_task_run_v2 import task_run
    from app.db import SessionLocal
    from app.v2.contracts import Resume
    from app.v2.runtime import job
    from app.v2.tasks import resume
    _, _, snap = task_run(client)
    with SessionLocal() as db:
        before = job(db, snap["run_id"]).state_json
    with pytest.raises(DomainError, match="RESEARCH_ENABLE_REQUIRES_EXPLICIT_REPLAN"):
        resume(snap["run_id"], Resume(expected_revision=snap["state_revision"],
            checkpoint_hash=snap["checkpoint_hash"], enable_research=True), "no-research-replan")
    with SessionLocal() as db:
        assert job(db, snap["run_id"]).state_json == before


def test_replan_rejects_stale_current_version_before_any_generation(client):
    from test_task_run_v2 import task_run
    from app.models import Tutorial
    from app.v2.contracts import Resume
    from app.v2.runtime import write, job, load
    from app.v2.tasks import resume
    _, _, snap = task_run(client)
    with write() as db:
        state = load(job(db, snap["run_id"]).state_json)
        db.get(Tutorial, state["artifact_id"]).current_version_id = str(uuid4())
    with pytest.raises(DomainError, match="VERSION_MISMATCH"):
        resume(snap["run_id"], Resume(expected_revision=snap["state_revision"], checkpoint_hash=snap["checkpoint_hash"],
            replan=True, acknowledge_new_generation=True), "stale-replan")
