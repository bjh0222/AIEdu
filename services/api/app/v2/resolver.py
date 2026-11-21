"""Task intent, lossless spec inheritance and mode plans. No execution permissions here."""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

from ..learning.disciplines import SUBJECTS, canonical_domain_key
from .contracts import DomainError, digest
from .profiles import domain_profile

SCENES = {"default_tutorial", "knowledge_tutorial", "practical_tutorial", "reading_guide", "exam_paper", "exercise", "exam"}
SPEC_FIELDS = ("name", "scene", "description", "applicable_tasks", "target_reader", "output_goal", "deliverables",
               "content_structure", "format_rules", "required_rules", "forbidden_rules", "suggested_rules",
               "flexible_rules", "reference_examples", "acceptance_criteria", "custom_markdown",
               "trigger_keywords", "excluded_keywords", "allow_keyword_trigger", "allow_semantic_recommendation",
               "allow_explicit_selection", "is_default", "is_builtin", "applicable_intents", "excluded_scenarios", "excluded_intents")
CONSUMERS = {field: ["planning", "generating", "checking", "exporting"] for field in SPEC_FIELDS}
CONSUMERS.update(target_reader=["planning", "generating"], suggested_rules=["generating"], flexible_rules=["generating"])
CONSUMERS.update({field:["planning"] for field in ("trigger_keywords","excluded_keywords","allow_keyword_trigger",
    "allow_semantic_recommendation","allow_explicit_selection","is_default","is_builtin","applicable_intents","excluded_scenarios","excluded_intents")})
HINTS = {
    "math_logic": ["数学", "微积分", "线代", "线性代数", "积分", "导数", "矩阵", "概率", "逻辑证明"],
    "cs_it": ["python", "powershell", "编程", "算法", "javascript", "数据库", "计算机"],
    "history_culture": ["历史", "史料", "唐朝", "考古", "宋代"], "biology_life": ["生物", "细胞", "光合作用", "遗传"],
    "arts_creativity": ["摄影", "绘画", "艺术", "音乐", "文学"], "physics_astronomy": ["物理", "力学", "天文"],
    "chemistry_materials": ["化学", "化学反应", "配平"], "law_policy": ["法律", "法条", "法学"],
    "economics_business": ["经济", "商业", "会计"], "language_communication": ["英语", "语言学", "语法"],
    "education_learning": ["教育学", "教学设计"], "philosophy_ethics": ["哲学", "伦理"],
}


def canonical_scene(scene):
    return "exam_paper" if scene == "exam" else scene


def resolve_task(value, explicit_fields=None, proposal=None):
    """An AI proposal may fill gaps, but never replace explicit input or hard scope."""
    result = deepcopy(value)
    explicit = set(explicit_fields if explicit_fields is not None else value.get("explicit_fields", value.keys()))
    proposal = proposal if isinstance(proposal, dict) else {}
    raw = result["raw_input"].lower()
    reasons, conflicts = [], []
    explicit_scope = "generation_scope" in explicit and value.get("generation_scope") in {"focused", "system"}
    if explicit_scope:
        scope, scope_source = value["generation_scope"], "explicit"
    elif proposal.get("generation_scope") in {"focused", "system"}:
        scope, scope_source = proposal["generation_scope"], "model_candidate"
    else:
        broad = bool(re.search(r"系统(?:学习|掌握|教程)|完整(?:体系|课程)|从零|全书|多章|分章|逐章|[二三四五六七八九十\d]+章", raw))
        scope, scope_source = ("system" if broad else "focused"), "local_candidate"
    result["generation_scope"] = scope
    result["scope_resolution"] = {"value": scope, "source": scope_source,
        "reason": "user_override" if explicit_scope else "requested_breadth; editable before start", "levels": ["focused", "system"]}
    for conflict in proposal.get("conflicts", []) if isinstance(proposal.get("conflicts"), list) else []:
        if isinstance(conflict, str) and conflict.strip():
            conflicts.append({"code": "INTENT_CONFLICT", "description": conflict})
        elif isinstance(conflict, dict) and conflict.get("description"):
            conflicts.append({"code": "INTENT_CONFLICT", "description": str(conflict["description"])})
    known = {key for key, _, _ in SUBJECTS} | {"general"}
    detected = [key for key, words in HINTS.items() if any(word in raw for word in words)]
    if "domain_key" not in explicit:
        candidate = canonical_domain_key(proposal.get("domain_key") or (detected[0] if len(detected) == 1 else "general"))
        result["domain_key"] = candidate if candidate in known else "general"
        reasons.append({"field": "domain_key", "reason": "model_or_local_intent", "value": result["domain_key"]})
    result["canonical_domain_key"] = canonical_domain_key(result.get("domain_key"))
    if "branch" not in explicit and isinstance(proposal.get("branch"), str):
        result["branch"] = proposal["branch"]  # Preserve arbitrary historical branch strings.
    if "title" not in explicit:
        result["title"] = result["raw_input"].strip()[:80] or "新学习任务"
    if "runtime_target" not in explicit:
        if "powershell" in raw and ("5.1" in raw or "5．1" in raw):
            result["runtime_target"] = "powershell-5.1-desktop"
        elif "python" in raw:
            result["runtime_target"] = "python"
    scene = "knowledge_tutorial"
    for token, mode in (("试卷", "exam_paper"), ("练习", "exercise"), ("习题", "exercise"),
                        ("阅读", "reading_guide"), ("导读", "reading_guide"), ("实践", "practical_tutorial"),
                        ("操作", "practical_tutorial")):
        if token in raw:
            scene = mode
            break
    if proposal.get("scene") in SCENES:
        scene = canonical_scene(proposal["scene"])
    if "spec_bindings" not in explicit:
        role = "paper" if scene == "exam_paper" else ("exercise" if scene == "exercise" else "tutorial")
        result["spec_bindings"] = [{"scene": scene, "artifact_role": role, "spec_id": None, "snapshot": {}}]
    for binding in result["spec_bindings"]:
        if binding.get("artifact_role", "tutorial") == "tutorial" and canonical_scene(binding["scene"]) in {"exam_paper", "exercise"}:
            binding["requested_artifact_role"] = "tutorial"
            binding["artifact_role"] = "paper" if canonical_scene(binding["scene"]) == "exam_paper" else "exercise"
    if "requested_artifacts" not in explicit:
        result["requested_artifacts"] = list(dict.fromkeys(b["artifact_role"] for b in result["spec_bindings"]))
        if any(b["scene"] in {"exam", "exam_paper", "exercise"} for b in result["spec_bindings"]):
            result["requested_artifacts"].append("answer")
        if "速查" in raw:
            result["requested_artifacts"].append("cheatsheet")
    if "subject_bindings" not in explicit or not result.get("subject_bindings"):
        domains = detected if len(detected) > 1 and "domain_key" not in explicit else [result["domain_key"]]
        result["subject_bindings"] = [{"domain_key": d, "branch": result.get("branch", ""), "unit_keys": []} for d in domains]
    result["quality_profiles"] = [domain_profile(s["domain_key"], canonical_scene(b["scene"]), s.get("branch", ""))
        for s in result["subject_bindings"] for b in result["spec_bindings"]]
    result["explicit_fields"] = sorted(explicit)
    result["task_intent"] = {"schema_version": "aiedu.task-intent.v1", "resolution_reasons": reasons,
                             "assumptions": [], "conflicts": conflicts, "mode_source": "explicit" if "spec_bindings" in explicit else "resolved"}
    return result


def bind_specs(db, value):
    """Resolve IDs using the actual spec service; snapshots remain byte-preserving values."""
    from ..models import FormatSpec
    from ..services.format_specs import spec_dict
    from .management_lifecycle import overlay_ids, visible
    hidden = set(db.scalars(overlay_ids("specs")))
    result = deepcopy(value)
    for binding in result.get("spec_bindings", []):
        spec_id = binding.get("spec_id")
        snapshot = binding.get("snapshot") or {}
        if spec_id:
            if spec_id in hidden:
                raise DomainError("RESOURCE_TRASHED", 409)
            obj = db.get(FormatSpec, spec_id)
            if obj is None:
                raise DomainError("SPEC_NOT_FOUND", 422, spec_id=spec_id)
            actual = spec_dict(obj)
            expected = binding.get("spec_version")
            if expected is not None and expected != actual["version"]:
                raise DomainError("SPEC_VERSION_STALE", 409, spec_id=spec_id)
            if snapshot and digest(snapshot) != digest(actual):
                raise DomainError("SPEC_SNAPSHOT_CONFLICT", 409, spec_id=spec_id)
            snapshot = actual
        elif not snapshot:
            mode = canonical_scene(binding["scene"])
            # This only selects existing defaults; no user record is re-created.
            lookup = "default_tutorial" if mode == "knowledge_tutorial" else mode
            available = visible(db.query(FormatSpec), "specs", FormatSpec.id).filter_by(scene=lookup)
            candidates = available.filter_by(is_default=True).all()
            if not candidates:
                candidates = available.filter_by(is_builtin=True).all()
            if not candidates:
                candidates = available.filter_by(allow_explicit_selection=True).all()
                if len(candidates) > 1:
                    raise DomainError("SPEC_SELECTION_REQUIRED",422,scene=lookup,
                                      spec_ids=sorted(s.id for s in candidates))
            if len(candidates) == 1:
                snapshot = spec_dict(candidates[0])
                binding["spec_id"] = candidates[0].id
        actual_scene = canonical_scene(snapshot.get("scene", binding["scene"]))
        compatible = actual_scene == canonical_scene(binding["scene"]) or {actual_scene, binding["scene"]} <= {"default_tutorial", "knowledge_tutorial"}
        if snapshot and not compatible:
            raise DomainError("SPEC_SCENE_CONFLICT", 422)
        binding["snapshot"] = snapshot
        binding["snapshot_hash"] = digest(snapshot)
        binding["spec_version"] = snapshot.get("version", binding.get("spec_version"))
    from .math_framework import freeze_framework
    return freeze_framework(db, result)


def spec_entries(context, stage, role=None):
    entries = []
    for binding in context.get("spec_bindings", []):
        if role and binding["artifact_role"] not in {role, "tutorial"}:
            continue
        for field, content in binding.get("snapshot", {}).items():
            if field not in SPEC_FIELDS:
                continue
            selected = stage in CONSUMERS[field]
            entries.append({"source_id": binding.get("spec_id") or "inline", "version": binding.get("spec_version"),
                "field": field, "sha256": digest(content), "selected": selected,
                "reason": "field_consumer" if selected else "out_of_scope", "consumer": stage,
                "message_position": f"context.spec_bindings.{binding['artifact_role']}.snapshot.{field}"})
    return entries


def build_units(context, chunks=None, outline=None, demo=False):
    from .format_runtime import format_layout, chapter_groups, EXERCISE_LAYOUT, READING_LAYOUT
    roles = context["requested_artifacts"]
    unknown = set(roles) - {"tutorial", "chapter", "book_level", "paper", "exercise", "answer", "cheatsheet"}
    if unknown:
        raise DomainError("UNSUPPORTED_ARTIFACT_ROLE", 422, roles=sorted(unknown))
    coupled = "answer" in roles and bool(set(roles) & {"paper", "exercise"})
    reading = any(canonical_scene(b["scene"]) == "reading_guide" for b in context["spec_bindings"])
    groups, current, size = [], [], 0
    for chunk in chunks or []:
        amount = chunk["end_byte"] - chunk["start_byte"]
        if current and size + amount > 32 * 1024:
            groups.append(current)
            current, size = [], 0
        current.append(chunk["chunk_id"])
        size += amount
    if current:
        groups.append(current)
    units = []
    chapters = chapter_groups(chunks or [])
    for role in roles:
        if coupled and role == "answer":
            continue
        bindings = [b for b in context["spec_bindings"] if b["artifact_role"] == role]
        snapshot = bindings[0].get("snapshot", {}) if bindings else {}
        layout = format_layout(snapshot)
        if layout in {EXERCISE_LAYOUT, READING_LAYOUT} and not snapshot.get("deliverables"):
            sections = chapters or [{"title": context["title"], "chunk_ids": [c["chunk_id"] for c in chunks or []], "source_sections": []}]
            if len(sections) > 64:
                raise DomainError("PLAN_RESOURCE_LIMIT", 422)
            own_units = []
            for number, section in enumerate(sections):
                own_units.append({"unit_id": str(uuid4()), "unit_key": f"{role}:{number}:0", "title": section["title"],
                    "artifact_role": role, "artifact_roles": [role, "answer"] if coupled and role == "exercise" else [role],
                    "required": True, "depends_on": [], "material_chunk_ids": section["chunk_ids"],
                    "source_sections": section["source_sections"], "format_contract": layout,
                    "section_number": number+1+(2 if layout == READING_LAYOUT and chapters else 0),
                    "display_order": len(units)+number+(2 if layout == READING_LAYOUT and chapters else 0),
                    "subject_bindings": context.get("subject_bindings", []), "origin": "demo" if demo else "generated"})
            if layout == READING_LAYOUT and chapters:
                for number, title in enumerate(("全书认知地图", "阅读路线")):
                    units.append({"unit_id": str(uuid4()), "unit_key": f"book_level:{number}", "title": title,
                        "artifact_role": "book_level", "artifact_roles": [role], "required": True,
                        "depends_on": [u["unit_id"] for u in own_units], "material_chunk_ids": [],
                        "format_contract": layout, "section_number": number+1, "display_order": len(units),
                        "subject_bindings": context.get("subject_bindings", []), "origin": "demo" if demo else "generated"})
            units.extend(own_units)
            continue
        deliverables = snapshot.get("deliverables") or []
        titles = [d.get("title") or d.get("name") if isinstance(d, dict) else d for d in deliverables]
        titles = [t for t in titles if isinstance(t, str) and t.strip()]
        if role in {"paper", "exercise"}:
            titles = [context["title"]]  # Questions and answers stay in one candidate.
        elif (not titles and isinstance(outline, list) and role == "tutorial" and not groups
              and (context.get("generation_scope") == "system" or
                   context.get("generation_scope") is None and re.search(r"多章|分章|多节|逐章|全书|[二三四五六七八九十\d]+章", context["raw_input"]))):
            titles = [o["title"] for o in outline if isinstance(o, dict) and isinstance(o.get("title"), str) and o["title"].strip()]
        titles = titles or [context["title"]]
        if len(titles) > 64:
            raise DomainError("PLAN_RESOURCE_LIMIT", 422)
        for index, title in enumerate(titles):
            partitions = groups if role not in {"cheatsheet", "book_level"} and groups else [[]]
            for number, group in enumerate(partitions):
                unit_key = f"{role}:{index}:{number}"
                scopes = [s for s in context.get("subject_bindings", []) if not s.get("unit_keys") or unit_key in s["unit_keys"]]
                described = next((o for o in outline or [] if isinstance(o, dict) and o.get("title") == title), {})
                scoped_domains = described.get("domain_keys") or ([described["domain_key"]] if described.get("domain_key") else [])
                if scoped_domains and "subject_bindings" not in context.get("explicit_fields", []):
                    proposed = {canonical_domain_key(d) for d in scoped_domains if isinstance(d, str)}
                    scopes = [s for s in scopes if canonical_domain_key(s["domain_key"]) in proposed] or scopes
                if not scoped_domains and len(context.get("subject_bindings", [])) > 1 and not all(s.get("unit_keys") for s in context["subject_bindings"]):
                    # Explicitly mixed units retain all relevant scopes; never silently choose the first.
                    scopes = context["subject_bindings"]
                units.append({"unit_id": str(uuid4()), "unit_key": unit_key, "title": title + (f" · {number+1}" if len(partitions)>1 else ""),
                    "artifact_role": role, "artifact_roles": [role, "answer"] if coupled and role in {"paper", "exercise"} else [role],
                    "required": True, "depends_on": [], "material_chunk_ids": group,
                    "subject_bindings": scopes, "origin": "demo" if demo else "generated"})
    if reading and groups and "book_level" not in roles and not any(u.get("format_contract") == READING_LAYOUT for u in units):
        units.append({"unit_id": str(uuid4()), "title": "全书认知地图与阅读路线", "artifact_role": "book_level",
                      "artifact_roles": ["tutorial"], "depends_on": [], "required": True,
                      "material_chunk_ids": [], "origin": "demo" if demo else "generated"})
    upstream = [u["unit_id"] for u in units if u["artifact_role"] not in {"cheatsheet", "book_level"}]
    for unit in units:
        if unit["artifact_role"] in {"cheatsheet", "book_level"} and not unit["depends_on"]:
            unit["depends_on"] = upstream
    return sorted(units, key=lambda u: bool(u["depends_on"]))
