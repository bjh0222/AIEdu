"""Task-derived presentation, shared by A/B/C and deterministic projections.

Original format-spec snapshots remain immutable. A chooses the applicable
organization; the wrapper enforces its frozen counts/forms, not a global list
of teaching-level labels. Safety and evidence authority never come from A.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import DomainError, digest

LAYOUT = "task-adaptive.v1"
POLICY_PROMPT = (
    " Format authority is consistent at every stage: the COMPLETE explicit natural-language user request "
    "(not only hard_constraints) has priority over template defaults. The frozen output_design, when present, "
    "records A's task-specific applicability and organization decision. Before A freezes that decision, "
    "template applicability is pending: do not assume default question types, counts, headings or answer "
    "lengths are task mandates. Define rubric targets conditionally on the eventual applicable arrangement. "
    "Original spec snapshots remain source data, not a command to "
    "restore inapplicable example headings, question forms, chapter counts or answer brevity. Apply useful "
    "format rules through that decision, preserving safe Markdown, coupled questions/answers and source bytes. "
    "Do not confuse pedagogical difficulty or response-form labels with subject-matter methods. A request "
    "for methods/categories needs actual task-specific categories and explanations, with the requested "
    "number, form and level of substantive questions per category. "
    "Neither templates nor output_design can change model roles, tool authority, evidence/calibration policy "
    "or user content requirements. Generated difficulty labels are proposals, not measured learner difficulty. "
    "Three distinct claims must remain separate: structural coverage/counts, evidence-based a-priori cognitive "
    "demand, and empirically measured learner difficulty. A label, author's presentation, or increased calculation "
    "volume alone never satisfies a requested difficulty level. Evaluate actual required cognitive operations, "
    "prerequisites, strategy choices and dependencies against the task-specific A targets; before A, define "
    "that evaluation conditionally on the eventual targets. Lack of learner response data leaves empirical "
    "difficulty not_measured, but does not waive or downgrade the executable prior-demand check. A coverage "
    "dimension may count items without assessing difficulty only if it explicitly leaves the difficulty "
    "requirement to that separate check; it cannot declare labels sufficient for the user's difficulty requirement."
)


class DifficultyTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = Field(min_length=1)
    minimum_nonroutine_operations: int = Field(ge=0, le=20)
    minimum_concept_interactions: int = Field(ge=1, le=20)
    minimum_dependency_depth: int = Field(ge=1, le=30)
    rationale: str = Field(min_length=12)
    empirical_status: Literal["not_measured"] = "not_measured"


class OutputDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["method_practice", "chapter_practice", "exam", "reading", "tutorial", "custom"]
    grouping_basis: str = Field(min_length=4)
    rationale: str = Field(min_length=12)
    request_quotes: list[str] = Field(min_length=1)
    question_form: Literal["constructed_response_only", "mixed", "not_applicable"]
    explanation_policy: Literal["full", "concise", "answer_only"]
    answer_placement: Literal["item_inline", "unit_end", "document_end"] = "unit_end"
    template_adaptations: list[dict[str, str]] = Field(default_factory=list)
    difficulty_target: DifficultyTarget | None = None
    quantity_per_group: int | None = Field(default=None, ge=1, le=100)


def _requires_inline_answers(raw_input: str) -> bool:
    patterns = (
        r"同一(?:个)?\s*item.{0,24}(?:题干|题目).{0,16}答案",
        r"(?:每题|每小题).{0,16}(?:必须|需要|要|应|须).{0,16}答案.{0,16}(?:解析|推理)",
    )
    negative = re.compile(r"(?:不要|不需要|禁止|不得|无需|无须)\s*(?:在|将|把)?\s*$")
    for pattern in patterns:
        for match in re.finditer(pattern, raw_input):
            if not negative.search(raw_input[max(0, match.start() - 10):match.start()]):
                return True
    return False


def derive_output_design(context, blueprint):
    if not isinstance(blueprint, dict):
        raise DomainError("OUTPUT_DESIGN_REQUIRED", 422)
    content = blueprint.get("content") or blueprint
    design = content.get("design") or {}
    units = content.get("structure", {}).get("units") or []
    if not units:
        raise DomainError("OUTPUT_DESIGN_REQUIRED", 422)
    blueprint_by_title = {row.get("title"): row for row in units}
    question_rows = []
    teaching_units = content.get("teaching_units") or []
    if teaching_units:
        for unit in teaching_units:
            question_rows.extend(unit.get("question_blueprint", []))
    if not question_rows:
        question_rows = [
            row
            for unit in context.get("teaching_arrangement_units", [])
            for row in unit.get("question_blueprint", [])
        ]
    forms = {
        row.get("question_form")
        for row in question_rows
        if isinstance(row, dict) and row.get("question_form")
    }
    question_form = (
        "not_applicable" if not forms
        else "constructed_response_only" if forms == {"constructed_response"}
        else "mixed"
    )
    artifact_roles = set(context.get("requested_artifacts", []))
    mode = (
        "tutorial" if artifact_roles == {"tutorial"}
        else "custom" if artifact_roles & {"exercise", "paper"}
        else "tutorial"
    )
    strategy = [
        *design.get("difficulty_strategy", []),
        *design.get("teaching_or_assessment_strategy", []),
    ]
    rationale = " ".join(str(value).strip() for value in strategy if str(value).strip())
    if len(rationale) < 12:
        raise DomainError("OUTPUT_DESIGN_REQUIRED", 422)
    def maximum(field, default):
        values = []
        for row in question_rows:
            basis = row.get("difficulty_basis") if isinstance(row, dict) else None
            value = basis.get(field) if isinstance(basis, dict) else None
            if type(value) is int:
                values.append(value)
        return max(values) if values else default
    level = next((value for value in design.get("difficulty_strategy", []) if value), "task_adaptive")
    counts = {
        int(match)
        for match in re.findall(
            r"每(?:一)?(?:类|组)[^\n。；]{0,18}?(\d+)\s*(?:个|道)[^\n。；]{0,8}?题",
            context.get("raw_input", ""),
        )
    }
    raw_input = context.get("raw_input", "")
    inline_answers = _requires_inline_answers(raw_input)
    result = {
        "mode": mode,
        "grouping_basis": "blueprint question groups",
        "rationale": rationale,
        "request_quotes": [context.get("raw_input", "")],
        "question_form": question_form,
        "explanation_policy": "concise" if artifact_roles & {"exercise", "paper"} else "full",
        "answer_placement": "item_inline" if inline_answers else "unit_end",
        "template_adaptations": [],
        "difficulty_target": {
            "level": level,
            "minimum_nonroutine_operations": maximum("nonroutine_operations", 0),
            "minimum_concept_interactions": maximum("concept_interactions", 1),
            "minimum_dependency_depth": maximum("dependency_depth", 1),
            "rationale": rationale,
            "empirical_status": "not_measured",
        } if artifact_roles & {"exercise", "paper"} else None,
        "quantity_per_group": next(iter(counts)) if len(counts) == 1 else None,
    }
    return validated_design(result, context)


def validated_design(value, context):
    value = {
        key: item for key, item in value.items()
        if key not in {"method_version", "source_spec_hashes", "design_hash"}
    }
    design = OutputDesign.model_validate(value).model_dump()
    raw = context.get("raw_input", "") + "\n" + "\n".join(context.get("hard_constraints", []))
    if any(not quote or quote not in raw for quote in design["request_quotes"]):
        raise DomainError("OUTPUT_DESIGN_REQUEST_UNBOUND", 422)
    artifacts = set(context.get("requested_artifacts", []))
    item_task = bool(artifacts & {"exercise", "paper"})
    if item_task and _requires_inline_answers(raw):
        design["answer_placement"] = "item_inline"
    if item_task and design["question_form"] == "not_applicable":
        raise DomainError("TASK_QUESTION_FORM_REQUIRED", 422)
    tutorial_only = "tutorial" in artifacts and not item_task
    item_only = item_task and "tutorial" not in artifacts
    if tutorial_only and design["mode"] in {"method_practice", "chapter_practice", "exam"}:
        raise DomainError("OUTPUT_DESIGN_ARTIFACT_MODE_CONFLICT", 422)
    if item_only and design["mode"] in {"tutorial", "reading"}:
        raise DomainError("OUTPUT_DESIGN_ARTIFACT_MODE_CONFLICT", 422)
    # A may not silently turn an explicit harder task into a routine worksheet.
    # Nonroutine demand must be present for an explicitly challenging item task.
    # There is deliberately no universal "three steps = difficult" cutoff:
    # A's task-specific numerical targets still need reference/learner grounding.
    target = design.get("difficulty_target")
    hard = bool(re.search(r"较难|高难|难题|challenging|advanced problems", raw, re.I))
    if hard and item_task and (not target or target["minimum_nonroutine_operations"] < 1):
        raise DomainError("HARD_TASK_DIFFICULTY_TARGET_TOO_LOW", 422)
    counts = {int(m) for m in re.findall(r"每(?:一)?(?:类|组)[^\n。；]{0,18}?(\d+)\s*(?:个|道)[^\n。；]{0,8}?题", raw)}
    if len(counts) == 1 and design.get("quantity_per_group") != next(iter(counts)):
        raise DomainError("EXPLICIT_PER_GROUP_QUANTITY_NOT_BOUND", 422)
    design.update(method_version=LAYOUT,
        source_spec_hashes=[b.get("snapshot_hash", digest(b.get("snapshot", {}))) for b in context.get("spec_bindings", [])])
    design["design_hash"] = digest(design)
    return design


def apply_design(context, plan, arrangement, chunks):
    """Before generation, A owns organization; source coverage stays explicit."""
    from uuid import uuid4
    content = arrangement.get("content") or {}
    proposed = content.get("output_design")
    if not proposed:
        raise DomainError("OUTPUT_DESIGN_REQUIRED", 422)
    design = validated_design(proposed, context)
    descriptions = content["units"]
    if len(descriptions) > 64:
        raise DomainError("PLAN_RESOURCE_LIMIT", 422)
    roles = [r for r in context["requested_artifacts"] if r != "answer"]
    source_ids = {m for m in context.get("material_ids", [])}
    chunk_ids = {c["chunk_id"] for c in chunks}
    units = []
    role_aliases = {}

    def canonical_role(value):
        """Convert only an explicit composite role to its requested base role.

        A's contract has one primary artifact_role per unit while answer
        projection is represented by artifact_roles.  Models sometimes
        express that projection as ``exercise_and_answer`` (or the paper
        equivalent).  Accept that unambiguous representation only when its
        base role and the answer artifact are both requested; all other
        unknown roles remain hard errors rather than being guessed.
        """
        if not value:
            return None
        if value in roles:
            return value
        suffix = "_and_answer"
        if value.endswith(suffix):
            base = value[:-len(suffix)]
            if base in roles and "answer" in context["requested_artifacts"]:
                role_aliases[value] = base
                return base
        return value

    for index, description in enumerate(descriptions):
        source_role = description.get("artifact_role")
        role = canonical_role(source_role) or (roles[0] if len(roles) == 1 else None)
        if role not in roles and not ("tutorial" in roles and role in {"chapter", "book_level"}):
            raise DomainError("ADAPTIVE_ARTIFACT_ROLE_UNBOUND", 422)
        mappings = set(description["material_mapping"])
        if mappings - source_ids - chunk_ids:
            raise DomainError("OUTPUT_DESIGN_MATERIAL_MAPPING_INVALID", 422)
        selected = [c["chunk_id"] for c in chunks if c["chunk_id"] in mappings or c.get("source_id") in mappings]
        # Empty mapping is only valid with no selected material; never silently
        # lose sources in a model-designed layout.
        if role == "book_level" and design["mode"] == "reading":
            selected = []  # generated chapter digests, never whole-book raw injection
        if chunks and not selected and role != "book_level":
            raise DomainError("OUTPUT_DESIGN_MATERIAL_MAPPING_REQUIRED", 422)
        unit = {"unit_id": str(uuid4()), "unit_key": f"adaptive:{index}", "title": description["title"],
            "artifact_role": role, "artifact_roles": ([role, "answer"] if role in {"paper", "exercise"} and "answer" in context["requested_artifacts"] else ["tutorial"] if role in {"chapter", "book_level"} else [role]),
            "required": True, "depends_on": [], "material_chunk_ids": selected,
            "source_sections": [], "format_contract": LAYOUT, "output_design": deepcopy(design),
            "section_number": index+1, "display_order": index,
            "subject_bindings": context.get("subject_bindings", []), "origin": "generated"}
        if source_role and source_role != role:
            unit["source_artifact_role"] = source_role
            unit["artifact_role_normalization"] = "explicit_composite_to_primary_with_answer_projection"
        unit["required_sections"] = description.get("required_sections", [])
        unit["summary_heading"] = description.get("summary_heading")
        if design.get("quantity_per_group") is not None and role in {"exercise", "paper"}:
            if description.get("quantity") != design["quantity_per_group"]:
                raise DomainError("OUTPUT_DESIGN_QUANTITY_UNBOUND", 422)
        units.append(unit)
    by_title = {u["title"]: u for u in units}
    for description, unit in zip(descriptions, units):
        dependency_titles = description.get("depends_on_titles", [])
        if unit["title"] in dependency_titles or not set(dependency_titles) <= set(by_title):
            raise DomainError("OUTPUT_DESIGN_DEPENDENCY_INVALID", 422)
        unit["depends_on"] = [by_title[title]["unit_id"] for title in dependency_titles]
    pending, complete = list(units), set()
    while pending:
        ready = [u for u in pending if set(u["depends_on"]) <= complete]
        if not ready:
            raise DomainError("OUTPUT_DESIGN_DEPENDENCY_CYCLE", 422)
        for unit in ready:
            complete.add(unit["unit_id"])
            pending.remove(unit)
    integration = _validated_integration_plan(content, design, descriptions, roles, by_title)
    if integration:
        for section in integration["sections"]:
            by_title[section["title"]]["integration_section"] = deepcopy(section)
    if design["mode"] == "reading":
        chapters = {u["title"] for u in units if u["artifact_role"] == "chapter"}
        for unit, description in zip(units, descriptions):
            legacy_whole_book = (
                unit["artifact_role"] == "book_level"
                and not unit.get("integration_section")
                and (not chapters or not chapters <= set(description.get("depends_on_titles", [])))
            )
            if legacy_whole_book:
                raise DomainError("READING_DIGEST_DEPENDENCIES_REQUIRED", 422)
            if unit["artifact_role"] == "chapter" and not unit["summary_heading"]:
                raise DomainError("READING_SUMMARY_HEADING_REQUIRED", 422)
    if set(roles) - {r for u in units for r in u["artifact_roles"]}:
        raise DomainError("OUTPUT_DESIGN_ARTIFACT_COVERAGE_INCOMPLETE", 422)
    mapped_chunk_ids = {c for u in units for c in u["material_chunk_ids"]}
    result = deepcopy(plan)
    result["units"] = units
    result["output_design"] = design
    result["material_scope"] = {
        "mapped_chunk_ids": sorted(mapped_chunk_ids),
        "out_of_scope_chunk_ids": sorted(chunk_ids - mapped_chunk_ids),
    }
    if integration:
        result["integration_plan"] = deepcopy(integration)
    return result


def _validated_integration_plan(content, design, descriptions, roles, by_title):
    """A decides whether/how B integrates; every synthesis section is a planned unit."""
    from copy import deepcopy

    plan = content.get("integration_plan")
    substantive = [d for d in descriptions if d.get("artifact_role") != "book_level"]
    tutorial_like = "tutorial" in roles and design.get("mode") in {"tutorial", "reading", "custom"}
    if plan is None:
        if tutorial_like and len(substantive) >= 2:
            raise DomainError("INTEGRATION_PLAN_REQUIRED", 422,
                              substantive_unit_count=len(substantive))
        return None
    titles = [d["title"] for d in descriptions]
    description_by_title = {d["title"]: d for d in descriptions}
    if plan["required"] != bool(plan["sections"]):
        raise DomainError("INTEGRATION_PLAN_REQUIRED_FLAG_MISMATCH", 422)
    declared, covered = set(), set()
    for section in plan["sections"]:
        title = section["title"]
        unit = by_title.get(title)
        description = description_by_title.get(title)
        if unit is None:
            raise DomainError("INTEGRATION_UNIT_MISSING", 422, title=title)
        if title in declared:
            raise DomainError("INTEGRATION_SECTION_DUPLICATE", 422, title=title)
        if unit.get("artifact_role") != "book_level" or description.get("artifact_role") != "book_level":
            raise DomainError("INTEGRATION_UNIT_ROLE_INVALID", 422, title=title)
        covers = section["covers_titles"]
        if len(covers) != len(set(covers)):
            raise DomainError("INTEGRATION_COVERAGE_DUPLICATE", 422, title=title)
        if title in covers or any(value not in by_title for value in covers):
            raise DomainError("INTEGRATION_COVERAGE_UNBOUND", 422, title=title,
                              covers_titles=covers)
        if set(description.get("depends_on_titles", [])) != set(covers):
            raise DomainError("INTEGRATION_DEPENDENCY_MISMATCH", 422, title=title,
                              depends_on_titles=description.get("depends_on_titles", []),
                              covers_titles=covers)
        if titles.index(title) <= max(titles.index(value) for value in covers):
            raise DomainError("INTEGRATION_PLACEMENT_INVALID", 422, title=title,
                              covers_titles=covers)
        if section["kind"] == "cross_chapter_synthesis" and len(covers) < 2:
            raise DomainError("INTEGRATION_CROSS_CHAPTER_TOO_SMALL", 422, title=title)
        declared.add(title)
        covered.update(covers)
    undeclared = [d["title"] for d in descriptions
                  if d.get("artifact_role") == "book_level" and d["title"] not in declared]
    if undeclared:
        raise DomainError("INTEGRATION_UNIT_UNDECLARED", 422, titles=undeclared)
    summaries = [s for s in plan["sections"] if s["kind"] == "whole_tutorial_summary"]
    summary_titles = {s["title"] for s in summaries}
    # A whole-tutorial summary may (and normally should) consume earlier
    # cross-chapter/part-summary units too, not only raw chapters. It must not
    # consume itself.
    summary_targets = [d["title"] for d in descriptions if d["title"] not in summary_titles]
    if plan["whole_tutorial_summary"]:
        if len(summaries) != 1:
            raise DomainError("INTEGRATION_WHOLE_SUMMARY_REQUIRED", 422)
        if set(summaries[0]["covers_titles"]) != set(summary_targets):
            raise DomainError("INTEGRATION_WHOLE_SUMMARY_COVERAGE", 422,
                              covers_titles=summaries[0]["covers_titles"],
                              substantive_titles=summary_targets)
    elif summaries:
        raise DomainError("INTEGRATION_WHOLE_SUMMARY_UNDECLARED", 422,
                          titles=[s["title"] for s in summaries])
    return deepcopy(plan)


def item_schema(unit):
    value = {"method_overview": "Explain the category's method, applicable conditions, choices and pitfalls; no question answers here.",
        "items": [{"question_form": "constructed_response",
            "stem_markdown": "complete unnumbered Chinese question, no outer headings",
            "difficulty_basis": {"prerequisites": ["specific concept"], "cognitive_operations": ["actual cognitive operation"],
                "nonroutine_operations": ["non-routine decision or transfer"], "concept_interactions": ["interaction of distinct concepts"],
                "dependency_depth": "positive integer; number of dependent reasoning stages", "rationale": "why the actual question meets the frozen target"},
            "answer": {"final": "complete final answer", "reasoning": "complete checking derivation",
                "display_reasoning": "learner explanation according to output_design.explanation_policy"}}]}
    if unit.get("artifact_role") == "paper":
        value["items"][0]["score"] = "positive numeric points"
    return value


def validate_item(candidate, unit_contract):
    form = candidate.get("question_form")
    # The response contract uses a small enum, but older prompt examples
    # accidentally encouraged models to append an explanatory parenthesis.
    # Normalize only that unambiguous constructed-response spelling; other
    # values remain hard failures and cannot bypass a task's form requirement.
    if isinstance(form, str) and form.startswith("constructed_response "):
        form = "constructed_response"
    design = unit_contract.get("output_design", {})
    if form not in {"constructed_response", "judgement", "choice"}:
        raise DomainError("TASK_QUESTION_FORM_REQUIRED", 422)
    if design.get("question_form") == "constructed_response_only" and form != "constructed_response":
        raise DomainError("TASK_QUESTION_FORM_MISMATCH", 422)
    basis = candidate.get("difficulty_basis")
    if design.get("difficulty_target"):
        if not isinstance(basis, dict) or any(not isinstance(basis.get(k), list) or
                any(not isinstance(v, str) or not v.strip() for v in basis[k]) for k in
                ("prerequisites", "cognitive_operations", "nonroutine_operations", "concept_interactions")):
            raise DomainError("ITEM_DIFFICULTY_BASIS_REQUIRED", 422)
        if type(basis.get("dependency_depth")) is not int or basis["dependency_depth"] < 1 or not isinstance(basis.get("rationale"), str):
            raise DomainError("ITEM_DIFFICULTY_BASIS_INVALID", 422)
        target = design["difficulty_target"]
        if (len(basis["nonroutine_operations"]) < target["minimum_nonroutine_operations"] or
                len(basis["concept_interactions"]) < target["minimum_concept_interactions"] or
                basis["dependency_depth"] < target["minimum_dependency_depth"]):
            raise DomainError("ITEM_DIFFICULTY_DECLARATION_BELOW_TARGET", 422)
        # These are declared counts only; C must recompute them from the actual
        # question. This structural gate is not a difficulty certificate.
    return {"question_form": form, "difficulty_basis": deepcopy(basis),
        "difficulty_authority": "generation_proposal_requires_blind_review"}


def validate_quantity(items, unit_contract):
    quantity = unit_contract.get("teaching_arrangement", {}).get("quantity")
    if type(quantity) is int and len(items) != quantity:
        raise DomainError("TASK_ITEM_QUANTITY_MISMATCH", 422, required=quantity, received=len(items))
    groups = (unit_contract.get("teaching_arrangement") or {}).get("question_blueprint", [])
    expected = {}
    for row in groups:
        form = row.get("question_form")
        if form:
            expected[form] = expected.get(form, 0) + int(row.get("quantity", 0))
    actual = {}
    for item in items:
        form = item.get("question_form")
        if form:
            actual[form] = actual.get(form, 0) + 1
    if expected and actual != expected:
        raise DomainError(
            "TASK_BLUEPRINT_FORM_MISMATCH", 422,
            required_forms=expected,
            received_forms=actual,
        )


def render(items, unit, answers_only=False):
    from ..services.format_specs import cn_numeral
    lines = [f"## {cn_numeral(unit.get('section_number', 1))}、{unit['title']}", ""]
    if not answers_only:
        if unit.get("method_overview"):
            lines.extend([unit["method_overview"].strip(), ""])
        for item in items:
            gap = ["", "", ""] if item.get("question_form") == "constructed_response" else [""]
            lines.extend([f"**{item['ordinal']}.** {item['stem_markdown'].strip()}", *gap])
        if unit["output_design"].get("answer_placement") == "item_inline":
            policy = unit["output_design"]["explanation_policy"]
            for item in items:
                final = item["answer"]["final"].strip()
                answer_label = "" if re.match(r"^(?:答案|答)\s*[:：]", final) else "答案："
                lines.extend([f"**{item['ordinal']}.** {answer_label}{final}"])
                if policy != "answer_only":
                    explanation = (item["answer"].get("display_reasoning")
                                   if policy == "concise" else item["answer"]["reasoning"])
                    explanation = (explanation or item["answer"]["reasoning"]).strip()
                    explanation_label = "" if re.match(r"^解析\s*[:：]", explanation) else "解析："
                    lines.extend([f"{explanation_label}{explanation}"])
                lines.append("")
    if not answers_only and unit["output_design"].get("answer_placement") in {"item_inline", "document_end"}:
        return "\n".join(lines)
    lines.extend(["### 参考答案", ""])
    policy = unit["output_design"]["explanation_policy"]
    for item in items:
        lines.extend([f"**{item['ordinal']}.** {item['answer']['final'].strip()}", ""])
        if policy != "answer_only":
            explanation = item["answer"].get("display_reasoning") if policy == "concise" else item["answer"]["reasoning"]
            lines.extend([explanation or item["answer"]["reasoning"], ""])
    return "\n".join(lines)


def document_end_answers(units):
    """Coupled items mechanically projected once at the document's end."""
    selected = [u for u in sorted(units, key=lambda u: u.get("display_order", 0)) if u.get("items")
                and u.get("format_contract") == LAYOUT and u.get("output_design", {}).get("answer_placement") == "document_end"]
    return "\n\n".join(render(u["items"], u, True) for u in selected)
