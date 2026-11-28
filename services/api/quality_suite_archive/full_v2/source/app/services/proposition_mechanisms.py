"""题目评价的专业适配与 generation.v1 出口，不执行模型或学生代码。"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from ..learning.disciplines import canonical_domain_key

MECHANISM_VERSION = "item-mechanisms.v1"
SCOPE_KEYS = {"domains", "domain_keys", "domain_key", "knowledge_points", "question_types", "question_type",
              "learners", "learner", "branches", "branch", "difficulties", "difficulty", "task_kinds", "task_kind",
              "purposes", "purpose", "complexity", "universal", "cross_domain", "requires_examples", "example_required",
              "conflicts_with", "exclude", "exclude_knowledge_points"}


def retrieval_scope(value: dict) -> dict:
    """机制全文留在专用字段和版本快照，不进入确定性作用域过滤。"""
    return {key: deepcopy(item) for key, item in value.items() if key in SCOPE_KEYS}


def principle_mechanism(value: dict) -> str:
    mechanism = str(value.get("mechanism") or "")
    source_scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    details = {key: item for key, item in source_scope.items() if key not in SCOPE_KEYS}
    details.update({key: value[key] for key in ("core_relations", "invariants", "variable_factors", "knowledge_links", "strategy_requirements", "generation_operations", "comparisons") if value.get(key)})
    if details:
        mechanism += "\n结构与先验比较（非实测）：" + json.dumps(details, ensure_ascii=False)
    return mechanism


def grounded_comparisons(entries, samples: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """比较仅引用此轮真实题目，不接受模型造出的来源或实证因果。"""
    accepted, warnings = [], []
    if not isinstance(entries, list):
        return [], ["跨题比较格式无效，尚未采纳为证据。"] if entries else []
    for entry in entries:
        ids = entry.get("question_ids") if isinstance(entry, dict) else None
        if not isinstance(ids, list) or len(set(str(qid) for qid in ids)) < 2 or not all(isinstance(qid, str) and qid in samples for qid in ids):
            warnings.append("跨题比较缺少两道可定位的真实题目，尚未采纳为证据。")
            continue
        value = deepcopy(entry)
        value["evidence_basis"] = "prior"
        value["source_refs"] = [source_ref("material", qid, 1, samples[qid].get("stem", ""), "raw_stem") for qid in ids]
        accepted.append(value)
    return accepted, warnings


def domain_adapter(domain: str, branch: str = "") -> dict:
    key, area = canonical_domain_key(domain), branch.casefold()
    if key == "math_logic" and (not area or any(term in area for term in ("calculus", "微积分", "高等数学", "导数", "积分"))):
        return {"id": "calculus.v1", "supported": True, "checks": [
            "保留定义域、量词、连续/可导与极限条件；给出全部小问的可核查证明。",
            "区分直接代公式、主动构造差商、条件变换与跨知识迁移；计算增长不等于策略难度增长。",
            "变式说明保持的核心关系、改变的条件/设问及反例边界。",
        ], "boundary": "微积分静态推导；未测学生难度、区分度和迁移收益。"}
    if key == "cs_it" and (not area or "python" in area or "编程" in area):
        return {"id": "python.v1", "supported": True, "checks": [
            "核对输入输出、可变性、异常约定、边界输入与算法复杂度；算法说明不要求符号表达式。",
            "区分增加输入规模/循环次数与改变数据结构、状态不变量或策略选择。",
            "没有工具运行记录时标记静态检查、未执行；不推断测试覆盖或程序通过。",
        ], "boundary": "Python 概念与算法静态评价；不执行上传代码，checker 可靠性未实测。"}
    return {"id": "general.v1", "supported": False, "checks": [
        "可评价完整性、独立答案和结构；专门学科事实缺证据时列待核实，不冒充专用专业适配。",
    ], "boundary": "此学科/分支尚无专用适配；通用静态判断不能证明领域事实。"}


def source_ref(kind: str, identifier: str, version=None, text: str = "", location: str | None = None) -> dict:
    return {"kind": kind, "id": str(identifier), "version": str(version) if version is not None else None,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None, "location": location}


def _strings(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def normalize_model_report(report: dict, question: dict, blind: dict) -> dict:
    """强制证据与推荐语义；不把报告的分数或赞语升级为正确性证据。"""
    value = deepcopy(report)
    correctness = value["correctness"]
    if blind.get("solvable") is not True and correctness.get("status") == "supported":
        correctness.update(status="undecidable", reason="盲解未完成，第二阶段正向结论不足以独立核实。")
    if correctness.get("status") == "refuted":
        value["recommendation"] = "reject"
    elif correctness.get("status") == "undecidable":
        value["recommendation"] = "defer"
    correctness["evidence_method"] = "model_static"
    difficulty = value["difficulty"]
    learner = str(question.get("learner") or difficulty.get("learner") or "").strip()
    difficulty["learner"] = learner or None
    difficulty["learner_origin"] = "user" if question.get("learner") else "model_assumption" if learner else "unknown"
    difficulty["learner_assumption"] = learner if learner and not question.get("learner") else None
    difficulty["prior"] = True
    difficulty["evidence_basis"] = "prior" if learner else "unknown"
    level = difficulty.get("level")
    if not learner or type(level) is not int or not 1 <= level <= 5:
        difficulty["level"] = None
    adapter = domain_adapter(question.get("domain_key", "general"), question.get("branch", ""))
    value["professional_adapter"] = adapter
    value["mechanism_version"] = MECHANISM_VERSION
    value["empirical_boundary"] = "先验机制假设；未获得真实作答数据，不报告实测难度、区分度或统计因果。"
    # 模型自述实证/运行不得升级证据类型。受控变式与跨题比较都为可复核的静态假设。
    for field in ("controlled_variations", "cross_item_comparisons"):
        for entry in value.get(field, []):
            if isinstance(entry, dict):
                entry["evidence_basis"] = "prior"
    if value.get("cross_item_comparisons"):
        value["cross_item_comparisons"] = []
        value.setdefault("uncertainties", []).append("单题评价未下发其它题目，跨题比较待多题提炼阶段核验。")
    if not adapter["supported"]:
        value.setdefault("uncertainties", []).append(adapter["boundary"])
    return value


def item_assessment(question: dict, evaluation: dict | None = None) -> dict:
    """旧报告只适配实际保存的内容；缺字段/null 保持未知。"""
    evaluation = evaluation or {}
    report = evaluation.get("report") if isinstance(evaluation.get("report"), dict) else {}
    active = bool(evaluation.get("status") == "done" and not evaluation.get("demo"))
    correctness = report.get("correctness", {}) if active and isinstance(report.get("correctness"), dict) else {}
    difficulty = report.get("difficulty", {}) if active and isinstance(report.get("difficulty"), dict) else {}
    structure = report.get("structure", {}) if active and isinstance(report.get("structure"), dict) else {}
    quality = report.get("quality", {}) if active and isinstance(report.get("quality"), dict) else {}
    ref = source_ref("material", question["id"], 1, question.get("raw_stem", ""),
                     f"lines:{question.get('source_line_start', 0)}-{question.get('source_line_end', 0)}")
    refs = [ref]
    if evaluation.get("id"):
        refs.append(source_ref("evaluation", evaluation["id"], evaluation.get("analysis_version"),
                               location="report"))
    state = correctness.get("status", "undecidable")
    if state not in ("supported", "refuted", "undecidable"):
        state = "undecidable"
    learner = question.get("learner") or difficulty.get("learner") or None
    if not isinstance(learner, (str, type(None))):
        learner = None
    level = difficulty.get("level")
    if not learner or type(level) is not int or not 1 <= level <= 5:
        level = None
    basis = difficulty.get("evidence_basis", "prior" if difficulty.get("prior") is True else "unknown")
    if basis not in ("prior", "empirical", "unknown") or not learner:
        basis = "unknown"
    # 本入口的报告仅来自模型静态评价，旧模型的 empirical 声明不能升级为实测。
    if basis == "empirical":
        basis = "unknown"
    recommendation = report.get("recommendation", "defer") if active else "defer"
    if recommendation not in ("priority", "consider", "defer", "reject"):
        recommendation = "defer"
    if state == "refuted":
        recommendation = "reject"
    elif state == "undecidable":
        recommendation = "defer"
    payload = {
        "correctness": {"status": state, "reason": str(correctness.get("reason") or "尚未形成可核查内部评价。"),
                        "evidence_method": "model_static", "source_refs": refs if active else []},
        "difficulty": {"level": level, "learner": learner, "sources": _strings(difficulty.get("sources")),
                       "evidence_basis": basis, "source_refs": refs if active else []},
        "quality": {"strengths": _strings(quality.get("strengths")), "limitations": _strings(quality.get("limitations"))},
        "teaching_value": str(report.get("teaching_value") or "未评价"),
        "collection_value": str(report.get("collection_value") or "待评价"),
        "structure": {"mechanism": str(structure.get("mechanism") or "未知"), "invariants": _strings(structure.get("invariants")),
                      "variable_factors": _strings(structure.get("variable_factors")), "boundaries": _strings(structure.get("boundaries")),
                      "source_refs": refs if active else []},
        "recommendation": recommendation, "uncertainties": _strings(report.get("uncertainties")),
    }
    if not active:
        payload["uncertainties"].append("未完成真实内部评价；旧记录或演示输出不构成新证据。")
    if basis == "unknown":
        payload["uncertainties"].append("学习者或难度证据性质未知，不能解释为实测难度。")
    if learner and not question.get("learner"):
        payload["uncertainties"].append(f"学习者为模型假设（{learner}），尚未由用户确认。")
    message = {"contract_version": "generation.v1", "kind": "ItemAssessment",
               "identity": {"task_id": str(question.get("batch_id") or question["id"]), "goal_id": None,
                            "tutorial_id": None, "version": evaluation.get("analysis_version") or None,
                            "unit_id": question["id"]},
               "payload": payload, "extensions": {"mechanism_version": report.get("mechanism_version"),
                   "framework_id": evaluation.get("framework_id"), "framework_version": evaluation.get("framework_version"),
                   "professional_adapter": report.get("professional_adapter") or domain_adapter(question.get("domain_key", "general"), question.get("branch", "")),
                   "controlled_variations": report.get("controlled_variations", []),
                   "cross_item_comparisons": report.get("cross_item_comparisons", []), "legacy_adapter": not bool(report.get("mechanism_version"))}}
    message["extensions"]["learner_origin"] = "user" if question.get("learner") else "model_assumption" if learner else "unknown"
    message["extensions"]["mechanism_details"] = {key: structure[key] for key in (
        "core_relations", "knowledge_links", "strategy_requirements", "cognitive_obstacles", "counterexamples", "generation_operations") if key in structure}
    from .generation_contract import validate_generation_message
    validate_generation_message(message, "ItemAssessment")
    return message
