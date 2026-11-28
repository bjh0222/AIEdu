"""Versioned planning/unit evidence over the existing proposition knowledge base.

The envelope is generation.v1; local types describe only this block's output.
Full strategy and source material stay in memory. Existing trace JSON stores
selection evidence, not a second copy of the generation pipeline.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

from .. import models
from ..learning.disciplines import canonical_domain_key, domain_keys_for
from .generation_contract import validate_generation_exchange, validate_generation_message
from .proposition_retrieval import _ALIASES, _FORM_TYPE_ALIASES, _json, _list, _overlap, retrieve_guidance


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceBundle:
    stage: Literal["planning", "unit"]
    injection_status: Literal["ok", "disabled", "no_match", "degraded", "insufficient_budget"]
    standards: list[dict]
    principles: list[dict]
    cases: list[dict]
    budget_chars: int
    chars_used: int
    trace_id: str | None
    warnings: list[str]

    def to_message(self, identity: dict, extensions: dict | None = None) -> dict:
        message = {"contract_version": "generation.v1", "kind": "EvidenceBundle",
                   "identity": dict(identity), "payload": dict(self.__dict__), "extensions": extensions or {}}
        validate_generation_message(message, "EvidenceBundle")
        return message


def _fine_topics(texts: list[str]) -> list[str]:
    """Extract concepts and response forms, rather than using an entire paper name."""
    result: list[str] = []
    for group in _ALIASES:
        for text in texts:
            if any((re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text.casefold())
                    if term.isascii() else term in text) for term in group):
                result.append(group[0])
                break
    for text in texts:
        for part in re.findall(r"[「《“]([^」》”]{1,40})[」》”]", text):
            if not re.search(r"试卷|教程|模拟卷|exam|tutorial", part, re.I):
                result.append(part)
        for match in re.findall(r"(?:知识点|考查|涉及)[:：\s]+([^。\n]{1,100})", text):
            result.extend(part.strip() for part in re.split(r"[,，;；、]", match) if 1 < len(part.strip()) <= 40)
    # Cohorts and response forms have dedicated fields, so exclude them here.
    excluded = {group[0] for group in _ALIASES[-8:]}
    return [value for value in dict.fromkeys(result) if value not in excluded]


def adapt_legacy_task_context(goal, item: dict | None = None, criteria_list=None, *,
                              outline_items=None, task_kind="section", tutorial_id=None,
                              section_id=None, version=None) -> dict:
    """Read-only adapter. Unknown learner/reference/difficulty remain unknown."""
    item = item if isinstance(item, dict) else {}
    blueprint = item.get("_blueprint") if isinstance(item.get("_blueprint"), dict) else {}
    snapshot = _json(getattr(goal, "format_spec_snapshot", ""), {})
    scene = str(snapshot.get("scene") or "")
    texts = [str(getattr(goal, key, "") or "") for key in ("title", "motivation", "application_context")]
    texts += [str(item.get(key) or "") for key in ("title", "purpose", "summary")]
    texts += [str(c.get("description") or c.get("title") or "") for c in criteria_list or [] if isinstance(c, dict)]
    texts += [str(x.get("title") or "") for x in outline_items or [] if isinstance(x, dict)]
    texts += _list(blueprint.get("practice_focus"))
    points = _list(item.get("knowledge_points")) + _list(blueprint.get("knowledge_points"))
    points += [str(k) for x in outline_items or [] if isinstance(x, dict) for k in _list(x.get("knowledge_points"))]
    points = list(dict.fromkeys(points + _fine_topics(texts)))
    forms = _list(blueprint.get("question_types")) + _list(item.get("question_types"))
    if not forms:
        joined = " ".join(texts)
        forms = [group[0] for group in _FORM_TYPE_ALIASES
                 if any(term.casefold() in joined.casefold() for term in group)]
    task_text = scene + str(item.get("_deliverable") or "") + str(item.get("title") or "")
    task_type = ("exam" if task_kind == "exam" or re.search(r"试卷|exam", task_text, re.I) else
                 "reading_guide" if scene == "reading_guide" else
                 "exercise" if task_kind in {"exercise", "extra_exercises"} or scene in {"exercise", "exercises"} else
                 "practical_tutorial" if re.search(r"practical|实践", scene, re.I) else "knowledge_tutorial")
    goal_id = getattr(goal, "id", None)
    unit_id = str(section_id or item.get("unit_id") or item.get("id") or "unit-1")
    identity = {"task_id": str(goal_id or tutorial_id or "legacy-task"), "goal_id": goal_id,
                "tutorial_id": tutorial_id, "version": version, "unit_id": None}
    source = {"kind": "user_task", "id": str(goal_id or "legacy-task"), "version": None,
              "hash": _hash([getattr(goal, "title", ""), getattr(goal, "hard_constraints", "")]),
              "location": "goal"}
    hard = str(getattr(goal, "hard_constraints", "") or "")
    constraints = ([{"id": "legacy-hard-constraints", "text": hard, "strength": "hard",
                     "origin": "current_user", "source_refs": [source]}] if hard else [])
    deliverables = [{"unit_id": unit_id, "title": str(item.get("title") or "当前单元")}]
    if task_kind in {"planning", "synthesis", "blueprint"} and outline_items:
        deliverables = [{"unit_id": str(outline.get("unit_id") or outline.get("id") or f"unit-{index + 1}"),
                         "title": str(outline.get("title") or f"单元{index + 1}")}
                        for index, outline in enumerate(outline_items) if isinstance(outline, dict)]
    payload = {"task_type": task_type, "domain_key": canonical_domain_key(getattr(goal, "domain_key", "general")),
               "branch": getattr(goal, "branch", "") or item.get("branch") or blueprint.get("branch") or None,
               "learner": getattr(goal, "current_level", "") or None,
               "objective": str(getattr(goal, "application_context", "") or getattr(goal, "title", "")),
               "constraints": constraints,
               "scope": {"include": points, "exclude": [], "basis": "inferred" if points else "unknown",
                         "source_refs": [source] if points else []},
               "reference_profile": None,
               "difficulty_target": {"mode": "explicit" if blueprint.get("difficulty") else "unspecified",
                                     "instruction": str(blueprint["difficulty"]) if blueprint.get("difficulty") else None,
                                     "evidence_basis": "prior" if blueprint.get("difficulty") else "unknown", "source_refs": []},
               "deliverables": deliverables,
               "assumptions": ["旧资料适配：未提供来源画像；知识点为结构字段或词法推断。"], "conflicts": []}
    result = {"contract_version": "generation.v1", "kind": "TaskContext", "identity": identity,
              "payload": payload, "extensions": {"adapter": "legacy-evidence-v1", "question_types": forms,
                  "unit_id": unit_id, "knowledge_points": points,
                  "complexity": "complex" if re.search(r"综合|创新|难题|压轴", " ".join(texts)) else "normal"}}
    validate_generation_message(result, "TaskContext")
    return result


def retrieval_context(task_context: dict, strategy: dict | None = None, *, stage="planning", unit_id=None) -> dict:
    validate_generation_exchange(task_context, strategy)
    payload, identity = task_context["payload"], task_context["identity"]
    points = _list(payload["scope"]["include"])
    profile = payload["reference_profile"] or {}
    forms = [str(form["name"]) for form in profile.get("question_types", [])]
    forms += _list(task_context["extensions"].get("question_types"))
    mechanisms: list[str] = []
    if stage == "unit":
        ids = [d["unit_id"] for d in payload["deliverables"]]
        if unit_id is None:
            unit_id = task_context["extensions"].get("unit_id") or (ids[0] if len(ids) == 1 else None)
        if unit_id not in ids:
            raise ValueError("generation.v1 EvidenceBundle unit_id does not identify a task deliverable")
        if strategy:
            unit = next((unit for unit in strategy["payload"]["units"] if unit["unit_id"] == unit_id), None)
            if unit is None:
                raise ValueError("generation.v1 unit strategy is missing")
            points = _list(unit["knowledge_points"])
            points += _fine_topics(_list(unit["knowledge_requests"]))
            forms = _list(unit["question_types"])
            mechanisms = [item["mechanism"] for item in unit["difficulty_mechanisms"]]
    if not points and stage == "planning":
        points = _list(profile.get("knowledge_points")) or _fine_topics([payload["objective"]])
    # 03-5：planning 检索消费 02 已核验的大纲语义（quote_match 引文核验条目）——
    # 细粒度知识点/设问形态来自 TaskContext.extensions.reference_semantics。
    if stage == "planning":
        semantics = ((task_context.get("extensions") or {}).get("reference_semantics") or {})
        semantic_topics = [str(entry.get("topic") or "").strip()
                           for entry in semantics.get("entries", []) if isinstance(entry, dict)]
        semantic_topics += [str(entry.get("text") or "").strip()
                            for entry in semantics.get("entries", []) if isinstance(entry, dict)
                            and str(entry.get("kind")) == "knowledge_point"]
        points = list(dict.fromkeys([*points, *(t for t in semantic_topics if t)]))
    excluded = set(_list(payload["scope"]["exclude"]))
    points = [point for point in dict.fromkeys(points) if point not in excluded]
    return {"domain_key": payload["domain_key"], "branch": payload["branch"] or "",
            "learner": payload["learner"] or "", "knowledge_points": points, "question_types": list(dict.fromkeys(forms)),
            "purpose": payload["objective"], "difficulty": payload["difficulty_target"]["instruction"] or "",
            "task_kind": "planning" if stage == "planning" else payload["task_type"],
            "complexity": "complex" if mechanisms else task_context["extensions"].get("complexity", "normal"),
            "goal_id": identity["goal_id"], "tutorial_id": identity["tutorial_id"], "section_id": unit_id or ""}


def evidence_source_fingerprint(db: Session, context: dict, config: dict | None = None) -> str:
    """State/content fingerprint catches revision, rejection, removal and linked evidence."""
    domains = [*domain_keys_for(context["domain_key"]), "general"]
    with db.no_autoflush:
        principles = db.query(models.DesignPrinciple).filter(models.DesignPrinciple.domain_key.in_(domains)).populate_existing().all()
        # General-classified principles can belong to another subject.
        principles += db.query(models.DesignPrinciple).filter(models.DesignPrinciple.principle_type == "general",
            ~models.DesignPrinciple.domain_key.in_(domains)).populate_existing().all()
        frameworks = db.query(models.EvaluationFramework).filter(models.EvaluationFramework.domain_key.in_(domains)).populate_existing().all()
        referenced_ids = {str(ref["question_id"]) for principle in principles
                          for refs in (principle.positive_refs, principle.negative_refs)
                          for ref in _json(refs, []) if isinstance(ref, dict) and ref.get("question_id")}
        questions = (db.query(models.PropositionQuestion).filter(models.PropositionQuestion.id.in_(referenced_ids))
                     .populate_existing().all() if referenced_ids else [])
        principle_ids = [principle.id for principle in principles]
        links = db.query(models.DesignPrincipleLink).filter(
            models.DesignPrincipleLink.source_id.in_(principle_ids) |
            models.DesignPrincipleLink.target_id.in_(principle_ids)).populate_existing().all()
        concepts = db.query(models.Concept).filter(models.Concept.status == "active").populate_existing().all()
        saved = db.get(models.PropositionConfig, "default", populate_existing=True)
        rows = [[getattr(row, column.name) for column in row.__table__.columns]
                for row in [*principles, *frameworks, *questions, *links, *concepts]]
        return _hash([rows, saved.settings_json if saved else None, config or {}])


# ---------------------------------------------------------------- evidence_use 消费校验（R2-2）
#
# 报告 2026-11-08 §7 第 2 行：注入数量 ≠ 正确应用。模型自述 evidence_use 必须能
# 绑定 call_id / principle_id / version / 题号 / 最终正文 hash，并由服务端核验：
# 声明位置存在、来源确实被注入、修订后证据是否过期（stale）。self-report 永远
# 不等于「迁移已验证」——confirmed 只表示「自述与注入/正文一致」，不证明效果。

EVIDENCE_USE_VERSION = "evidence-use.v1"
_EVIDENCE_USE_FIELDS = ("principle_id", "version", "question_numbers", "location",
                        "intended_application")


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _section_texts(content: dict | None) -> list[tuple[str, str]]:
    """把最终 content 展开为 [(section_id, 全文)]（各块 body 按顺序拼接）。"""
    texts: list[tuple[str, str]] = []
    if not isinstance(content, dict):
        return texts
    for section in content.get("sections") or []:
        if not isinstance(section, dict):
            continue
        blocks = [str(block.get("body") or "") for block in section.get("content") or []
                  if isinstance(block, dict) and block.get("body")]
        texts.append((str(section.get("id") or ""), "\n".join(blocks)))
    return texts


def normalize_evidence_use(raw: Any) -> list[dict]:
    """纯函数：结构化 evidence_use 条目校验；非法条目丢弃，不猜测、不补造。"""
    entries: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        principle_id = str(item.get("principle_id") or "").strip()
        location = str(item.get("location") or "").strip()
        application = str(item.get("intended_application") or "").strip()
        if not (principle_id or location or application):
            continue
        try:
            version = int(item.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        numbers: list[int] = []
        for value in item.get("question_numbers") or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                numbers.append(number)
        entries.append({
            "version": version,
            "principle_id": principle_id or None,
            "question_numbers": sorted(set(numbers)),
            "location": location or None,
            "intended_application": application,
            "call_id": str(item.get("call_id") or "").strip() or None,
            "claimed_text_hash": str(item.get("claimed_text_hash") or "").strip() or None,
            "evidence_use_version": EVIDENCE_USE_VERSION,
        })
    return entries


def _question_marker(number: int) -> re.Pattern:
    return re.compile(
        rf"(?:\*\*\s*{number}\s*\.\s*\*\*|第\s*{number}\s*题|(?m:^\s*{number}[.、]\s))")


def _location_in_texts(location: str, numbers: list[int],
                       texts: list[tuple[str, str]]) -> bool:
    """位置存在性：questions/N | answers/N | sections/<id>[:<idx>] | 字面片段。"""
    location = str(location or "").strip()
    if not texts:
        return False
    if location.startswith("sections/"):
        section_id, _, index = location[len("sections/"):].partition(":")
        match = next((text for sid, text in texts if sid == section_id), None)
        if match is None:
            return False
        if not index:
            return bool(match.strip())
        try:
            blocks = match.split("\n")
            return 0 <= int(index) < len(blocks)
        except ValueError:
            return False
    for number in numbers:
        marker = _question_marker(number)
        if any(marker.search(text) for _, text in texts):
            return True
    if location.startswith(("questions/", "answers/")):
        try:
            number = int(location.split("/", 1)[1])
        except ValueError:
            return False
        return any(_question_marker(number).search(text) for _, text in texts)
    return any(location in text for _, text in texts)


def verify_evidence_use(entry: dict, *, final_sections: dict[str, str],
                        injected_principle_ids: set[str] | None = None) -> dict:
    """纯函数：核验一条 evidence_use 自述（R2-2）。

    返回 status ∈ confirmed | unverified | not_injected | location_missing | expired。
    - unverified：绑定字段缺失（无法核验）；
    - location_missing：声明位置在最终正文找不到；
    - not_injected：声明的 principle_id 不在本次实际注入集合；
    - expired：声明位置正文 hash 与最终正文不一致（修订后旧证据过期）；
    - confirmed：以上全部通过（仅表示自述与注入/正文一致，不证明迁移效果）。
    """
    entry = dict(entry or {})
    texts = list((final_sections or {}).items())
    injected = {str(v) for v in (injected_principle_ids or set())}
    missing = [field for field in _EVIDENCE_USE_FIELDS if not entry.get(field)]
    numbers = [int(n) for n in (entry.get("question_numbers") or []) if str(n).isdigit()]
    location = str(entry.get("location") or "")
    location_exists = (_location_in_texts(location, numbers, texts) if location else None)
    principle_id = str(entry.get("principle_id") or "")
    source_injected = (principle_id in injected) if principle_id else None
    claimed = str(entry.get("claimed_text_hash") or "")
    hash_matches = None
    if claimed:
        candidates = [text for _, text in texts] if not location else [
            text for sid, text in texts if location.startswith("sections/") and sid == location[9:].split(":", 1)[0]
        ] or [text for _, text in texts]
        hash_matches = any(_hash_text(text) == claimed for text in candidates)
    if missing:
        status = "unverified"
    elif location_exists is False:
        status = "location_missing"
    elif source_injected is False:
        status = "not_injected"
    elif hash_matches is False:
        status = "expired"
    else:
        status = "confirmed"
    return {
        "status": status,
        "checks": {"binding_complete": not missing, "location_exists": location_exists,
                   "source_injected": source_injected, "hash_matches": hash_matches},
        "missing_fields": missing,
        "evidence_use_version": EVIDENCE_USE_VERSION,
        **{key: entry.get(key) for key in
           ("call_id", "principle_id", "version", "question_numbers", "location",
            "intended_application", "claimed_text_hash")},
    }


def verify_evidence_use_reports(db: Session, tutorial_id: str, content: dict | None) -> dict:
    """汇总核验：读取该 tutorial 的 guidance trace 中的 evidence_use 自述，对照最终正文。

    - injected 集合来自 trace.result.evidence_blocks.principles[].id（实际装配进 prompt 的块）；
    - final_sections 来自最终 content（修订后）；
    - 返回 {verified, counts:{status: n}, items:[...], note}，零模型。
    """
    texts = _section_texts(content)
    final_sections = {sid: text for sid, text in texts}
    traces = (db.query(models.PropositionGuidanceTrace)
              .filter(models.PropositionGuidanceTrace.tutorial_id == tutorial_id)
              .order_by(models.PropositionGuidanceTrace.created_at.desc()).limit(50).all())
    items: list[dict] = []
    for trace in traces:
        result = _json(trace.result, {})
        injected = {str(block.get("id")) for block in
                    (result.get("evidence_blocks") or {}).get("principles") or []
                    if isinstance(block, dict) and block.get("id")}
        for report in result.get("evidence_use_reports") or []:
            for entry in report.get("evidence_use") or []:
                if isinstance(entry, dict):
                    items.append(verify_evidence_use(entry, final_sections=final_sections,
                                                     injected_principle_ids=injected))
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"verified": len(items), "counts": counts, "items": items,
            "note": "confirmed 仅表示自述与注入/正文一致；不证明迁移效果或专业收益。"}


def _knowledge_request_refs(context: dict, result: dict, *, stage: str) -> list[dict]:
    """03-5：把任务的知识需求对应到实际选用的证据（可解释关联，零模型）。

    - 请求来源：unit 阶段用 Strategy 的 knowledge_requests/知识点；planning 用
      检索上下文的知识点（含 02 已核验语义主题）；
    - served_by：选用原理/案例与请求文本的词法关联（沿用 0.45 阈值，不降低门槛）；
    - 未满足的请求列 knowledge_gaps——可照常生成草稿，但不得虚构「已对标/已用原理」。
    """
    requests: list[str] = list(context.get("knowledge_points") or [])
    served_pool: list[tuple[str, str, int, str]] = []
    # Match the evidence actually assembled into the prompt, not selection
    # reasons. Long blocks commonly put their scope/mechanism after character
    # 400; truncating here falsely reports an injected topic as a knowledge gap.
    for block in (result.get("evidence_blocks") or {}).get("principles") or []:
        text = str(block.get("text") or "")
        # Counterexamples describe failure boundaries, not positive coverage.
        text = text.split("\n  限制/反例（不得当正向做法）：", 1)[0]
        served_pool.append(("principle", str(block.get("id")), int(block.get("version") or 1),
                            text))
    for example in result.get("examples") or []:
        if example.get("polarity") == "negative":
            continue
        # Only selected, assembled cases can serve a request; a candidate's
        # reason alone does not prove its content covers the requested topic.
        block = next((b for b in (result.get("evidence_blocks") or {}).get("cases") or []
                      if str(b.get("id")) == str(example.get("question_id"))), None)
        if block is None:
            continue
        served_pool.append(("case", str(example.get("question_id")), 1,
                            str(block.get("text") or "")))
    refs: list[dict] = []
    for index, request in enumerate(dict.fromkeys(requests)):
        request_id = f"kr-{hashlib.sha256(f'{stage}:{request}'.encode()).hexdigest()[:10]}"
        seen: set[str] = set()
        served_by: list[dict] = []
        for kind, source_id, version, text in served_pool:
            if _overlap([request], [text], list(_ALIASES)) >= 0.45:
                key = f"{kind}:{source_id}:{version}"
                if key not in seen:
                    seen.add(key)
                    served_by.append({"kind": kind, "id": source_id, "version": version})
        refs.append({"request_id": request_id, "request": request,
                     "served_by": served_by, "satisfied": bool(served_by)})
    return refs


def build_layer_status(result: dict) -> dict:
    """03-1：逐层覆盖预检（零模型）——回答「有哪些可用证据、为什么适用/不适用」。

    - available：该层进入检索的总量（principles 含被状态过滤的 proposed/rejected）；
    - eligible：通过状态/作用域/冲突/去重后可参与选择的候选；
    - selected：装配进 prompt 的块；injected 与 selected 同义
      （generation.v1 injection_status=ok 仍指传输/注入成功，枚举不变）；
    - reasons：字段级排除原因聚合（scope_mismatch:question_types×N 这类可解释摘要）；
    - overall_coverage：mixed（原理或案例被消费）/ standards_only / none。
    不因为「想提高命中率」而改判定；排除原因如实聚合。
    """
    stats = result.get("stats") or {}
    decisions = result.get("decisions") or []

    def _reason_summary() -> list[str]:
        counts: dict[str, int] = {}
        for decision in decisions:
            for reason in decision.get("reasons") or []:
                key = str(reason).split(";")[0]
                counts[key] = counts.get(key, 0) + 1
        return [f"{key}×{count}" for key, count in
                sorted(counts.items(), key=lambda item: -item[1])[:8]]

    principles_available = int(stats.get("principles_available") or 0)
    principles_eligible = max(0, principles_available
                              - int(stats.get("state_filtered") or 0)
                              - int(stats.get("scope_filtered") or 0)
                              - int(stats.get("conflict_excluded") or 0)
                              - int(stats.get("deduplicated") or 0))
    standards_available = int(stats.get("standards_available") or 0)
    standards_eligible = max(0, standards_available
                             - int(stats.get("standards_scope_filtered") or 0))
    cases_available = int(stats.get("examples_available") or 0)
    cases_eligible = max(0, cases_available
                         - int(stats.get("example_deduplicated") or 0)
                         - int(stats.get("example_conflict_excluded") or 0))
    standards_injected = len(result.get("standards") or [])
    principles_injected = len(result.get("selected") or [])
    cases_injected = len(result.get("examples") or [])
    layers = {
        "standards": {
            "available": standards_available, "eligible": standards_eligible,
            "selected": standards_injected, "injected": standards_injected,
            "reasons": ([f"standards_scope_filtered×{stats.get('standards_scope_filtered', 0)}"]
                        if stats.get("standards_scope_filtered") else [])
                       + ([f"standards_dropped_budget×{stats.get('standards_dropped', 0)}"]
                          if stats.get("standards_dropped") else []),
        },
        "principles": {
            "available": principles_available, "eligible": principles_eligible,
            "selected": principles_injected, "injected": principles_injected,
            "reasons": _reason_summary(),
        },
        "cases": {
            "available": cases_available, "eligible": cases_eligible,
            "selected": cases_injected, "injected": cases_injected,
            "reasons": ([f"examples_dropped_budget×{stats.get('examples_dropped', 0)}"]
                        if stats.get("examples_dropped") else []),
        },
    }
    if principles_injected or cases_injected:
        coverage = "mixed"
    elif standards_injected:
        coverage = "standards_only"
    else:
        coverage = "none"
    return {"layers": layers, "overall_coverage": coverage,
            "note": "available/eligible/selected/injected 为预检口径；"
                    "injection_status=ok 仍指传输/注入成功（generation.v1 枚举不变）"}


def retrieve_evidence_guidance(db: Session, task_context: dict, strategy: dict | None = None, *,
                               stage="planning", unit_id=None, config=None, record=False) -> dict:
    if stage not in {"planning", "unit"}:
        raise ValueError("generation.v1 evidence stage must be planning or unit")
    context = retrieval_context(task_context, strategy, stage=stage, unit_id=unit_id)
    result = retrieve_guidance(db, context, config, record=record)
    layers = result["evidence_blocks"]
    identity = {**task_context["identity"], "unit_id": context["section_id"] or None}
    bundle = EvidenceBundle(stage, result["injection_status"], layers["standards"], layers["principles"],
                            layers["cases"], result["budget_chars"], result["chars"], result["trace_id"], result["warnings"])
    layer_status = build_layer_status(result)
    result["layer_status"] = layer_status
    # 03-5：layer hash（证据块指纹）与知识需求链（request→served_by；未满足列 gaps）
    result["layer_hash"] = _hash(result.get("evidence_blocks") or {})
    request_refs = _knowledge_request_refs(context, result, stage=stage)
    result["knowledge_request_refs"] = request_refs
    result["knowledge_gaps"] = [
        {"request_id": ref["request_id"], "request": ref["request"],
         "reason": "no_selected_evidence;可照常生成草稿，不虚构已对标/已用原理"}
        for ref in request_refs if not ref["satisfied"]]
    message = bundle.to_message(identity, {"prompt_text": result["text"], "backend": result["backend"],
                                           "decisions": result["decisions"], "retrieval_context": context,
                                           "layer_status": layer_status,
                                           "knowledge_gaps": result["knowledge_gaps"]})
    validate_generation_exchange(task_context, strategy, evidence_bundles=[message])
    result["evidence_bundle"] = message
    result["source_fingerprint"] = evidence_source_fingerprint(db, context, config)
    if record and result["trace_id"]:
        trace = db.get(models.PropositionGuidanceTrace, result["trace_id"])
        trace.result = _dump(result)
        db.flush([trace])
    return result


def planning_evidence_bundle(db: Session, task_context: dict, strategy: dict | None = None, *, config=None,
                             record=False) -> dict:
    return retrieve_evidence_guidance(db, task_context, strategy, stage="planning", config=config, record=record)["evidence_bundle"]


def unit_evidence_bundle(db: Session, task_context: dict, strategy: dict | None = None, *, unit_id=None,
                         config=None, record=False) -> dict:
    return retrieve_evidence_guidance(db, task_context, strategy, stage="unit", unit_id=unit_id,
                                      config=config, record=record)["evidence_bundle"]
