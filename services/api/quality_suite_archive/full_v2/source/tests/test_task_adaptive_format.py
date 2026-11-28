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
    assert "\n\n\n\n**2.**" in body  # Three blank lines between constructed-response items.
    assert "判断题" not in body and "基础概念题" not in body and "- [ ]" not in body
    assert "完整关键推导" in body and [i["ordinal"] for i in items] == [1, 2, 3]


@pytest.mark.parametrize("scene", ["exercise", "exam_paper"])
def test_item_spec_packages_preserve_three_blank_lines_between_essay_questions(scene):
    from app.v2.spec_packages import build_package
    package = build_package("math_logic", "微积分", scene)
    assert "相邻解答题、证明题、综合题及其它主观题之间固定保留3个空行" in package["spec"]["required_rules"]


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


def test_B_item_forms_must_match_question_blueprint_group_counts():
    unit, output = planned(), candidates()
    unit["teaching_arrangement"]["question_blueprint"] = [{
        "question_form": "judgement",
        "quantity": 3,
    }]
    with pytest.raises(DomainError, match="TASK_BLUEPRINT_FORM_MISMATCH"):
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


def test_inline_answers_follow_each_item_without_separate_answer_section():
    unit, output = planned(), candidates()
    blueprint = {
        "content": {
            "design": {
                "difficulty_strategy": ["Use nonroutine reasoning."],
                "teaching_or_assessment_strategy": ["Keep each answer with its item."],
            },
            "structure": {"units": [{
                "title": unit["title"],
                "question_blueprint": [{"question_form": "constructed_response"}],
            }]},
        },
    }
    ctx = {
        "raw_input": "每题必须在同一个 item 内给出题干、答案和必要解析。",
        "hard_constraints": [],
        "requested_artifacts": ["exercise", "answer"],
        "material_ids": [],
        "spec_bindings": [],
        "teaching_arrangement_units": [{
            "question_blueprint": [{"question_form": "constructed_response"}],
        }],
    }
    from app.v2.adaptive_format import derive_output_design

    unit["output_design"] = derive_output_design(ctx, blueprint)
    unit["items"] = normalize_items(output, "exercise", unit["unit_id"], LAYOUT, [], unit)
    body = render_unit_items(unit["items"], unit)
    assert unit["output_design"]["answer_placement"] == "item_inline"
    assert "参考答案" not in body
    assert body.count("答案：") == len(unit["items"])
    assert body.count("解析：") == len(unit["items"])
    assert body.index("答案：") < body.index("解析：")


def test_negated_inline_answer_request_does_not_override_unit_end_placement():
    unit, output = planned(design(answer_placement="unit_end")), candidates()
    ctx = context() | {
        "raw_input": "不要在同一个 item 内给答案，答案统一放末尾。",
    }
    unit["output_design"] = validated_design(
        design(answer_placement="unit_end", request_quotes=[ctx["raw_input"]]), ctx)
    unit["items"] = normalize_items(output, "exercise", unit["unit_id"], LAYOUT, [], unit)
    assert unit["output_design"]["answer_placement"] == "unit_end"
    assert "参考答案" in render_unit_items(unit["items"], unit)


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


def test_explicit_tutorial_artifact_cannot_use_practice_output_design():
    ctx = context() | {
        "raw_input": "请写一份入门教程，不要额外习题集",
        "requested_artifacts": ["tutorial"],
    }
    value = design(
        mode="chapter_practice",
        difficulty_target=None,
        quantity_per_group=None,
        request_quotes=["不要额外习题集"],
    )
    with pytest.raises(DomainError, match="OUTPUT_DESIGN_ARTIFACT_MODE_CONFLICT"):
        validated_design(value, ctx)


def test_no_universal_step_count_can_replace_actual_challenge_evidence():
    value = design()
    value["difficulty_target"]["minimum_dependency_depth"] = 2
    # This only validates A's declaration. It never signs this as a difficult item.
    assert validated_design(value, context())["difficulty_target"]["minimum_dependency_depth"] == 2


def test_frozen_subject_understanding_is_an_allowed_knowledge_evidence_ref():
    from app.v2.planning import _allowed_evidence_refs
    ctx = {
        "material_ids": [],
        "subject_understanding": {
            "general": {"id": "subject-understanding:general", "version": 1, "hash": "g" * 64},
            "subjects": [{
                "id": "subject-understanding:math_logic",
                "version": 1,
                "hash": "m" * 64,
            }],
        },
        "quality_profiles": [{
            "profile_id": "math_logic.exercise",
            "version": "aiedu.domain-rubrics.v2",
            "profile_hash": "p" * 64,
            "item_quality_framework": {
                "framework_id": "independent-math.v2",
                "framework_hash": "q" * 64,
            },
        }],
        "evaluation_frameworks": {"frameworks": [{
            "id": "research-math-quality-v1",
            "version": 1,
            "source_hash": "f" * 64,
        }]},
        "knowledge_guidance_eligible": [{
            "id": "imported:research-knowledge-structure-v1",
            "evidence_hash": "k" * 64,
        }],
        "spec_bindings": [{
            "spec_id": "spec-1",
            "snapshot_hash": "s" * 64,
        }],
    }
    allowed = _allowed_evidence_refs(ctx, {"material_mapping": []})
    assert "profile:subject-understanding:math_logic:1:" + "m" * 64 in allowed
    assert "profile:subject-understanding:general:1:" + "g" * 64 in allowed
    assert "profile:math_logic.exercise:aiedu.domain-rubrics.v2:" + "p" * 64 in allowed
    assert "evaluation_framework:research-math-quality-v1" in allowed
    assert "frozen:evaluation-framework:research-math-quality-v1" in allowed
    assert "frozen:guidance:imported:research-knowledge-structure-v1" in allowed
    assert "frozen:import:research-knowledge-structure-v1" in allowed
    assert "knowledge_guidance_eligible:imported:research-knowledge-structure-v1" in allowed
    assert "knowledge_guidance:imported:research-knowledge-structure-v1" in allowed
    assert "profile:imported:research-knowledge-structure-v1:1:" + "k" * 64 in allowed
    assert "subject_understanding:math_logic" in allowed
    assert "quality_profiles:math_logic.exercise" in allowed
    assert "math_logic.exercise" in allowed
    assert "quality_profiles:math_logic.exercise:item_quality_framework:independent-math.v2" in allowed
    assert "evaluation_frameworks:research-math-quality-v1" in allowed
    assert "spec_bindings:spec-1" in allowed


def test_unique_blueprint_group_prefix_is_normalized_without_rewriting_content():
    from app.v2.planning import normalize_blueprint_group_refs
    content = {"units": [{
        "title": "u",
        "question_blueprint": [{"group": "G1-判断-定义域"}, {"group": "G2-选择-值域"}],
        "misconceptions": [
            {"blueprint_group": "G1", "plausible_idea": "a"},
            {"blueprint_group": "G2", "plausible_idea": "b"},
        ],
    }]}
    rows = normalize_blueprint_group_refs(content)
    assert [row["from"] for row in rows] == ["G1", "G2"]
    assert [row["to"] for row in rows] == ["G1-判断-定义域", "G2-选择-值域"]
    assert [row["plausible_idea"] for row in content["units"][0]["misconceptions"]] == ["a", "b"]


def test_frozen_understanding_and_guided_knowledge_are_valid_blueprint_sources():
    from app.v2.planning import research_source_catalog
    ctx = {
        "material_ids": [],
        "subject_understanding": {
            "general": {"id": "subject-understanding:general", "hash": "g" * 64},
            "subjects": [{"id": "subject-understanding:math_logic", "hash": "m" * 64}],
        },
        "knowledge_guidance_eligible": [{
            "id": "imported:research-knowledge-structure-v1",
            "evidence_hash": "k" * 64,
        }],
        "quality_profiles": [{
            "profile_id": "math_logic.exercise",
            "profile_hash": "p" * 64,
            "item_quality_framework": {
                "framework_id": "independent-math.v2",
                "framework_hash": "q" * 64,
            },
        }],
        "evaluation_frameworks": {"frameworks": [{
            "id": "research-math-quality-v1",
            "source_hash": "f" * 64,
            "evidence_refs": [{
                "id": "E15",
                "title": "official",
                "source_card": "研究/来源卡/数学与计算机/AP.md",
            }],
        }, {
            "id": "research-general-quality-v1",
            "source_hash": "r" * 64,
            "evidence_refs": [
                {"id": "E2", "title": "shared"},
                {"id": "E2", "title": "shared"},
            ],
        }]},
        "spec_bindings": [{
            "spec_id": "spec-1",
            "snapshot_hash": "s" * 64,
        }],
    }
    catalog = research_source_catalog({"research_result": {}}, ctx)
    assert catalog["subject-understanding:general"] == "g" * 64
    assert catalog["subject-understanding:math_logic"] == "m" * 64
    assert catalog["imported:research-knowledge-structure-v1"] == "k" * 64
    assert catalog["quality-profile:math_logic.exercise"] == "p" * 64
    assert catalog["evaluation-framework:research-math-quality-v1"] == "f" * 64
    assert catalog["evaluation_framework:research-math-quality-v1"] == "f" * 64
    assert "research-math-quality-v1#E15" in catalog
    assert "研究/来源卡/数学与计算机/AP.md" in catalog
    assert "frozen:evaluation-framework:research-math-quality-v1" in catalog
    assert "frozen:guidance:imported:research-knowledge-structure-v1" in catalog
    assert "frozen:import:research-knowledge-structure-v1" in catalog
    assert "knowledge_guidance_eligible:imported:research-knowledge-structure-v1" in catalog
    assert "knowledge_guidance:imported:research-knowledge-structure-v1" in catalog
    assert catalog["profile:imported:research-knowledge-structure-v1:1:" + "k" * 64] == "k" * 64
    assert catalog["subject_understanding:math_logic"] == "m" * 64
    assert catalog["quality_profiles:math_logic.exercise"] == "p" * 64
    assert catalog["math_logic.exercise"] == "p" * 64
    assert catalog["quality_profiles:math_logic.exercise:item_quality_framework:independent-math.v2"] == "q" * 64
    assert catalog["evaluation_frameworks:research-math-quality-v1"] == "f" * 64
    assert catalog["spec_bindings:spec-1"] == "s" * 64
    assert "E15" in catalog
    assert "E2" in catalog


def test_unknown_schema_field_is_removed_and_recorded_without_semantic_change():
    value = {"teaching_arrangement": {
        "goals": ["g"],
        "learner_prerequisites": [],
        "scope": "focused",
        "quality_targets": ["q"],
        "unresolved_conflicts": [],
        "generation_prompt": "p",
        "output_design": design() | {"source_spec_hashes": ["unexpected"]},
        "units": [{
            "title": "u",
            "concepts": ["c"],
            "question_types": [],
            "difficulty_basis": {},
            "quantity": None,
            "material_mapping": [],
            "acceptance_criteria": ["a"],
            "artifact_role": "exercise",
        }],
    }}
    result = arrangement(value)
    assert "source_spec_hashes" not in result["content"]["output_design"]
    assert result["normalizations"] == [{
        "path": ["output_design", "source_spec_hashes"],
        "removed": ["unexpected"],
        "reason": "extra_field_outside_frozen_schema",
    }]


def test_quality_ref_hash_is_normalized_only_when_frozen_rule_identity_is_unique():
    from app.v2.planning import rebind_quality_target_refs
    from app.v2.profiles import domain_profile
    profile = domain_profile("math_logic", "exercise")
    rule = profile["rules"][0]
    ctx = {"quality_profiles": [profile]}
    content = {"quality_target_refs": [{
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "profile_hash": "wrong",
        "rule_id": rule["rule_id"],
        "rule_version": rule["version"],
        "criterion_hash": rule["criterion_hash"],
    }]}
    normalized, rows = rebind_quality_target_refs(content, ctx, ctx)
    assert normalized["quality_target_refs"][0]["profile_hash"] == profile["profile_hash"]
    assert rows[0]["reason"] == "frozen_quality_ref_identity_normalized"


def test_blueprint_avoid_rule_detects_forbidden_method_overview():
    from app.v2.planning import blueprint_forbids_method_overview
    blueprint = {"content": {
        "design": {"avoid": ["教程正文、知识归纳、总结不得写入"]},
        "decisions": [],
    }}
    assert blueprint_forbids_method_overview(blueprint) is True
    blueprint["content"]["design"]["avoid"] = ["避免把算量当作难度"]
    assert blueprint_forbids_method_overview(blueprint) is False


def test_missing_output_design_is_derived_from_existing_blueprint_and_question_plan():
    from app.v2.adaptive_format import derive_output_design, validated_design
    ctx = context() | {"teaching_arrangement_units": [{
        "question_blueprint": [
            {"question_form": "judgement", "difficulty_basis": {
                "nonroutine_operations": 1,
                "concept_interactions": 2,
                "dependency_depth": 2,
            }},
            {"question_form": "choice", "difficulty_basis": {
                "nonroutine_operations": 1,
                "concept_interactions": 3,
                "dependency_depth": 2,
            }},
        ],
    }]}
    blueprint = {"content": {
        "design": {
            "difficulty_strategy": ["中等难度需要概念联动和非套路判断"],
            "teaching_or_assessment_strategy": ["每题在同一个 item 内给出答案和必要解析"],
            "avoid": ["教程正文、知识归纳、总结不得写入"],
        },
        "structure": {"units": [{"title": "合成方法甲"}]},
    }}
    derived = derive_output_design(ctx, blueprint)
    assert derived["mode"] == "custom"
    assert derived["question_form"] == "mixed"
    assert derived["difficulty_target"]["minimum_concept_interactions"] == 3
    assert derived["difficulty_target"]["minimum_dependency_depth"] == 2
    validated = validated_design(derived, ctx)
    assert validated["design_hash"] == digest({
        key: item for key, item in validated.items() if key != "design_hash"
    })


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


def tutorial_design():
    return design(mode="tutorial", question_form="not_applicable", difficulty_target=None,
                  quantity_per_group=None, request_quotes=["最后比较三种方法并总结全书"])


def tutorial_evaluation(content=None, titles=("方法甲", "方法乙", "方法丙")):
    chapters = [{"title": title, "concepts": [title], "question_types": [], "difficulty_basis": {},
                 "quantity": None, "material_mapping": [], "acceptance_criteria": [f"{title}讲清楚"],
                 "artifact_role": "chapter"} for title in titles]
    sections = [
        {"title": "方法比较", "kind": "cross_chapter_synthesis", "covers_titles": list(titles),
         "required_content": ["比较三种方法的适用条件、选择依据和边界"],
         "rationale": "用户要求在同一教程中比较三种方法的适用差异"},
        {"title": "全书总结", "kind": "whole_tutorial_summary",
         "covers_titles": [*titles, "方法比较"],
         "required_content": ["归纳三种方法的选择规则并给出全书结论"],
         "rationale": "用户明确要求最后给出覆盖全书的总结"},
    ]
    units = chapters + [
        {"title": "方法比较", "concepts": list(titles), "question_types": [], "difficulty_basis": {},
         "quantity": None, "material_mapping": [], "acceptance_criteria": ["比较结论可指导选择"],
         "artifact_role": "book_level", "depends_on_titles": list(titles)},
        {"title": "全书总结", "concepts": list(titles), "question_types": [], "difficulty_basis": {},
         "quantity": None, "material_mapping": [], "acceptance_criteria": ["总结覆盖全部方法"],
         "artifact_role": "book_level", "depends_on_titles": [*titles, "方法比较"]},
    ]
    if content:
        content(units, sections)
    return arrangement({"teaching_arrangement": {
        "goals": ["比较三种方法"], "learner_prerequisites": [], "scope": "system",
        "quality_targets": ["三种方法可区分"], "unresolved_conflicts": [],
        "generation_prompt": "按 A 的集成计划生成", "output_design": tutorial_design(),
        "units": units,
        "integration_plan": {"required": True, "decision_basis": "用户原话要求比较三种方法与总结全书",
                             "whole_tutorial_summary": True, "sections": sections},
    }})


def test_A_plans_cross_chapter_synthesis_and_whole_summary_as_B_units():
    ctx = context() | {"raw_input": "写三种方法的教程，最后比较三种方法并总结全书",
                       "requested_artifacts": ["tutorial"]}
    value = tutorial_evaluation()
    built = apply_design(ctx, {"units": []}, value, [])
    assert [u["title"] for u in built["units"]] == ["方法甲", "方法乙", "方法丙", "方法比较", "全书总结"]
    assert [u["artifact_role"] for u in built["units"][-2:]] == ["book_level", "book_level"]
    assert built["units"][3]["depends_on"] == [
        built["units"][0]["unit_id"], built["units"][1]["unit_id"], built["units"][2]["unit_id"]]
    assert built["units"][4]["depends_on"] == [
        built["units"][0]["unit_id"], built["units"][1]["unit_id"],
        built["units"][2]["unit_id"], built["units"][3]["unit_id"]]
    assert built["units"][3]["integration_section"]["kind"] == "cross_chapter_synthesis"
    assert built["units"][4]["integration_section"]["kind"] == "whole_tutorial_summary"
    assert built["integration_plan"]["whole_tutorial_summary"] is True
    strict = apply_arrangement(built, value, require_complete=True)
    assert strict["units"][3]["teaching_arrangement"]["artifact_role"] == "book_level"


def test_A_material_mapping_selects_task_scope_not_the_whole_file():
    ctx = context() | {
        "raw_input": "写三种方法的教程，最后比较三种方法并总结全书",
        "requested_artifacts": ["tutorial"],
        "material_ids": ["book"],
    }
    value = teaching(tutorial_design(), titles=("目标章",))
    unit = value["content"]["units"][0]
    unit.update(artifact_role="chapter", material_mapping=["book:6", "book:7"],
                required_sections=["章末摘要"], summary_heading="章末摘要")
    chunks = [{"source_id": "book", "chunk_id": f"book:{index}"} for index in range(12)]
    built = apply_design(ctx, {"units": []}, value, chunks)
    assert built["units"][0]["material_chunk_ids"] == ["book:6", "book:7"]
    assert built["material_scope"]["mapped_chunk_ids"] == ["book:6", "book:7"]
    assert set(built["material_scope"]["out_of_scope_chunk_ids"]) == {
        f"book:{index}" for index in range(12) if index not in {6, 7}
    }


def test_source_level_material_mapping_keeps_all_source_chunks():
    ctx = context() | {
        "raw_input": "写三种方法的教程，最后比较三种方法并总结全书",
        "requested_artifacts": ["tutorial"],
        "material_ids": ["book"],
    }
    value = teaching(tutorial_design(), titles=("全书",))
    unit = value["content"]["units"][0]
    unit.update(artifact_role="chapter", material_mapping=["book"],
                required_sections=["章末摘要"], summary_heading="章末摘要")
    chunks = [{"source_id": "book", "chunk_id": f"book:{index}"} for index in range(12)]
    built = apply_design(ctx, {"units": []}, value, chunks)
    assert set(built["units"][0]["material_chunk_ids"]) == {f"book:{index}" for index in range(12)}
    assert built["material_scope"]["out_of_scope_chunk_ids"] == []


def test_integration_plan_is_required_for_multi_unit_tutorial():
    ctx = context() | {"raw_input": "写三种方法的教程，最后比较三种方法并总结全书",
                       "requested_artifacts": ["tutorial"]}
    value = tutorial_evaluation()
    value["content"].pop("integration_plan")
    with pytest.raises(DomainError, match="INTEGRATION_PLAN_REQUIRED"):
        apply_design(ctx, {"units": []}, value, [])


@pytest.mark.parametrize("fault,code", [
    ("missing_unit", "INTEGRATION_UNIT_MISSING"),
    ("wrong_role", "INTEGRATION_UNIT_ROLE_INVALID"),
    ("wrong_dependency", "INTEGRATION_DEPENDENCY_MISMATCH"),
    ("wrong_placement", "INTEGRATION_PLACEMENT_INVALID"),
    ("whole_coverage", "INTEGRATION_WHOLE_SUMMARY_COVERAGE"),
    ("undeclared_unit", "INTEGRATION_UNIT_UNDECLARED"),
])
def test_integration_plan_cannot_hide_or_misplace_A_synthesis(fault, code):
    ctx = context() | {"raw_input": "写三种方法的教程，最后比较三种方法并总结全书",
                       "requested_artifacts": ["tutorial"]}

    def corrupt(units, sections):
        if fault == "missing_unit":
            sections[0]["title"] = "不存在的综合"
        if fault == "wrong_role":
            units[3]["artifact_role"] = "chapter"
        if fault == "wrong_dependency":
            units[3]["depends_on_titles"] = ["方法甲"]
        if fault == "wrong_placement":
            units.insert(0, units.pop(3))
        if fault == "whole_coverage":
            sections[1]["covers_titles"] = ["方法甲", "方法乙"]
            units[4]["depends_on_titles"] = ["方法甲", "方法乙"]
        if fault == "undeclared_unit":
            units.append({"title": "额外全书层", "concepts": [], "question_types": [], "difficulty_basis": {},
                          "quantity": None, "material_mapping": [], "acceptance_criteria": ["x"],
                          "artifact_role": "book_level", "depends_on_titles": ["方法甲"]})

    value = tutorial_evaluation(corrupt)
    with pytest.raises(DomainError, match=code):
        apply_design(ctx, {"units": []}, value, [])


def test_B_receives_A_integration_scope_and_actual_upstream_contract():
    from app.v2.worker import integration_generation_instruction

    assert integration_generation_instruction({}) == ""
    text = integration_generation_instruction({"integration_section": {
        "title": "方法比较",
        "kind": "cross_chapter_synthesis",
        "covers_titles": ["方法甲", "方法乙"],
        "required_content": ["比较适用条件与选择依据"],
        "rationale": "用户要求比较两种方法",
    }})
    assert "A-planned integration section" in text
    assert "covers_titles=['方法甲', '方法乙']" in text
    assert "actual generated upstream content" in text
    assert "do not repeat their lessons" in text


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
