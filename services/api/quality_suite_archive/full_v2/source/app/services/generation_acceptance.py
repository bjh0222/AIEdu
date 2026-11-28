"""Task-specific delivery checks. Static judgments never imply empirical learning gains."""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from . import exam_check
from .generation_contract import validate_generation_exchange, validate_generation_message
from .quality_findings import FindingLedger, evidence_refs_for, finding_from_l0, finding_from_l1_defect, finding_from_review

TASK_TYPES = {
    "default_tutorial": "knowledge_tutorial", "knowledge_tutorial": "knowledge_tutorial",
    "practical_tutorial": "practical_tutorial", "reading_guide": "reading_guide",
    "exercise": "exercise", "exam_paper": "exam",
}
METHODS = {"deterministic", "model_static", "tool", "human", "empirical"}
STATUSES = {"satisfied", "violated", "undecidable", "not_run", "not_applicable", "error"}
VALUE_RUBRICS = {
    "knowledge_tutorial": [
        ("explanation", "解释概念边界及理由", "概念、条件与解释的具体位置"),
        ("connections", "说明必要知识联系", "联系的理由及使用条件"),
        ("transfer", "支持向新情境迁移", "变式、反例或迁移任务及边界"),
    ],
    "practical_tutorial": [
        ("prerequisites", "明确操作前置条件", "环境、输入、权限或依赖"),
        ("verification", "步骤含预期结果与验证", "完整步骤与结果的可核查位置"),
        ("execution", "实际操作能够成功", "隔离环境中的运行结果"),
    ],
    "reading_guide": [
        ("source_grounding", "引文与来源一致", "来源版本、位置及完整原文"),
        ("argument", "解释论证与限定条件", "论点、论据、推论及作者限定"),
    ],
    "exercise": [
        ("correctness", "题目和答案正确完整", "全部题面、小问、答案和可核查推导"),
        ("assessment_value", "考查指定知识与策略", "知识点、策略与作答形态"),
        ("difficulty", "难度符合指定学习者", "条件结构与策略要求的先验机制解释"),
    ],
    "exam": [
        ("correctness", "题目和答案正确完整", "全部题面、小问、答案和可核查推导"),
        ("scope_style", "符合参考范围、题型和风格", "参考画像及整卷分布；无需逐题仿写"),
        ("difficulty", "难度符合指定学习者", "条件结构与策略要求的先验机制解释"),
    ],
}

# 版本化任务量规（01 任务书 S2 政策）：试卷基本交付把**正确性**列为必需——关闭模型
# 时显示「草稿/正确性待复核」，不暗开模型；普通教程/练习/阅读的选配规则保持。
# 用户量规仍可显式覆盖（explicit optional），硬约束不可关闭的既有保护不变；
# 策略只能升级必需，不得按执行结果/开关降低 required（acceptance_report 固化）。
# 政策变化与旧快照兼容见 docs/decisions.md 追加 ADR。
DELIVERY_RUBRIC_VERSION = "delivery-rubric.v1"
RUBRIC_V1_REQUIRED: dict[str, set[str]] = {"exam": {"correctness"}}

# quality_report 里的检查按**证据方法**分流：模型/工具产出的检查与 issue 归
# correctness 层，不再混入 structure（01 任务书：只有模型错误时 structure 不得被
# 标为 deterministic violated）。
MODEL_QUALITY_CHECK_NAMES = {"命题内容评价", "试卷答案盲解核验"}
MODEL_ISSUE_PREFIXES = ("[命题评价]", "[DEFECT]")


def source_ref(kind: str, identifier: str, *, version=None, text: str = "", location: str = "") -> dict:
    return {"kind": kind, "id": str(identifier), "version": str(version) if version is not None else None,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
            "location": location or None}


def adapt_task_context(goal, content: dict, *, tutorial_id: str = "", version: int | None = None,
                       task_context: dict | None = None) -> dict:
    """Adapt an old frozen snapshot without pretending missing reference evidence exists."""
    if task_context is not None:
        validate_generation_message(task_context, expected_kind="TaskContext")
        context = deepcopy(task_context)
        if tutorial_id:
            context["identity"]["tutorial_id"] = tutorial_id
        if version and version >= 1:
            context["identity"]["version"] = version
        return context
    spec = content.get("format_spec") or {}
    identity = {"task_id": str(getattr(goal, "id", None) or tutorial_id or "legacy"),
                "goal_id": getattr(goal, "id", None), "tutorial_id": tutorial_id or None,
                "version": version if version and version >= 1 else None, "unit_id": None}
    hard = str(getattr(goal, "hard_constraints", "") or "")
    constraints = []
    for i, line in enumerate(hard.splitlines()):
        if line.strip():
            constraints.append({"id": f"hard-{i + 1}", "text": line.strip(), "strength": "hard",
                                "origin": "current_user", "source_refs": [source_ref(
                                    "user_task", getattr(goal, "id", "legacy"), text=hard,
                                    location=f"hard_constraints:{i + 1}")]})
    context = {"contract_version": "generation.v1", "kind": "TaskContext", "identity": identity,
        "payload": {"task_type": TASK_TYPES.get(str(spec.get("scene", "")), "knowledge_tutorial"),
            "domain_key": str(getattr(goal, "domain_key", "") or ""),
            "branch": str(getattr(goal, "branch", "") or "") or None,
            "learner": str(getattr(goal, "current_level", "") or "") or None,
            "objective": "\n".join(str(getattr(goal, field, "") or "") for field in
                                   ("title", "motivation", "application_context")).strip(),
            "constraints": constraints,
            "scope": {"include": [], "exclude": [], "basis": "unknown", "source_refs": []},
            "reference_profile": None, "difficulty_target": {"mode": "unspecified", "instruction": None,
                                                              "evidence_basis": "unknown", "source_refs": []},
            "deliverables": [{"unit_id": str(s.get("id") or f"unit-{i + 1}"),
                              "title": str(s.get("title", ""))}
                             for i, s in enumerate(content.get("outline") or content.get("sections") or [])],
            "assumptions": ["旧快照未提供版本化来源画像；不确认参考范围、风格或实证难度。"],
            "conflicts": []}, "extensions": {"adapter": "legacy_goal_snapshot"}}
    validate_generation_message(context, expected_kind="TaskContext")
    return context


def plan_checks(task_context: dict, *, strategy: dict | None = None,
                criteria: list[dict] | None = None, rubric: list[dict] | None = None) -> list[dict]:
    """Fix applicability and requirement before any check executes or reads a model switch."""
    validate_generation_message(task_context, expected_kind="TaskContext")
    if strategy is not None:
        validate_generation_message(strategy, expected_kind="GenerationStrategy")
    task = task_context["payload"]
    plan = [{"id": "structure", "label": "结构与交付完整性", "layer": "structure",
             "applicable": True, "required": True, "strength": "hard",
             "method": "deterministic", "evidence_requirement": "最终章节及已声明结构契约",
             "source_refs": []}]
    for identifier, label, evidence in VALUE_RUBRICS[task["task_type"]]:
        method = "tool" if identifier == "execution" else "model_static"
        required = identifier in RUBRIC_V1_REQUIRED.get(task["task_type"], set())
        plan.append({"id": identifier, "label": label, "layer": "professional_value",
                     "applicable": True, "required": required,
                     "strength": "hard" if required else "soft", "method": method,
                     "rubric_version": DELIVERY_RUBRIC_VERSION,
                     "evidence_requirement": evidence, "source_refs": [source_ref(
                         "framework", f"delivery:{task['task_type']}:{identifier}", version="1",
                         location=f"{DELIVERY_RUBRIC_VERSION}:static_task_rubric")]})
    for constraint in task["constraints"]:
        plan.append({"id": f"constraint-{constraint['id']}", "label": constraint["text"],
                     "layer": "hard_constraint", "applicable": True,
                     "required": constraint["strength"] == "hard", "strength": constraint["strength"],
                     "method": "deterministic", "evidence_requirement": "用户原文与最终成果逐项核对",
                     "source_refs": constraint["source_refs"]})
    for criterion in criteria or []:
        # Mapping an acceptance ID is structure evidence, never a semantic pass.
        identifier = f"criterion-{criterion.get('id', len(plan))}"
        statement = str(criterion.get("statement", ""))
        delivery_requirement = bool(criterion.get("required", False)) or bool(re.search(
            r"(?:答案|题目|题面|选项).*(?:正确|无误|完整)|证明.*(?:完整|无缺口)|(?:代码|程序).*可运行", statement))
        plan.append({"id": identifier, "label": str(criterion.get("statement", "")),
                     "layer": "user_value" if delivery_requirement else "learner_outcome",
                     "applicable": True, "required": delivery_requirement,
                     "strength": "hard" if delivery_requirement else "soft",
                     "method": "model_static" if delivery_requirement else "human",
                     "evidence_requirement": str(criterion.get("pass_rule", "") or "用户标准所需实质证据"),
                     "conditions": str(criterion.get("conditions", "")),
                     "source_refs": [source_ref("user_task", criterion.get("id", identifier),
                                               text=str(criterion.get("statement", "")), location="statement")]})
    # Explicit user rubric overrides the professional defaults by stable check ID.
    for supplied in rubric or []:
        if not isinstance(supplied, dict) or not str(supplied.get("id", "")).strip():
            raise ValueError("验收量规缺少检查 id")
        candidate = next((p for p in plan if p["id"] == supplied["id"]), None)
        if candidate and candidate["layer"] == "hard_constraint" and candidate["required"] and (
            supplied.get("required") is False or supplied.get("applicable") is False or
            ("label" in supplied and supplied["label"] != candidate["label"])):
            raise ValueError(f"验收量规与用户硬约束冲突：{candidate['id']} 不能关闭或降为选配")
        baseline = candidate or {"id": supplied["id"], "layer": "user_value", "source_refs": []}
        merged = {**baseline, **deepcopy(supplied)}
        merged.setdefault("label", supplied["id"])
        merged.setdefault("applicable", True)
        merged.setdefault("required", True)
        merged.setdefault("strength", "hard" if merged["required"] else "soft")
        merged.setdefault("method", "model_static")
        merged.setdefault("evidence_requirement", "用户量规指定证据")
        if merged["method"] not in METHODS:
            raise ValueError(f"未知验收方法：{merged['method']}")
        if candidate:
            plan[plan.index(candidate)] = merged
        else:
            plan.append(merged)
    if strategy is not None:
        requested = {cid for unit in strategy["payload"]["units"] for cid in unit["check_ids"]}
        known = {p["id"] for p in plan}
        explicit_optional = {p["id"] for p in rubric or [] if p.get("required") is False}
        for target in plan:
            if target["id"] in requested and target["id"] not in explicit_optional:
                target["required"] = True
                target["strength"] = "hard"
        for identifier in sorted(requested - known):
            plan.append({"id": identifier, "label": identifier, "layer": "strategy",
                         "applicable": True, "required": True, "strength": "hard", "method": "model_static",
                         "evidence_requirement": "策略指定检查；尚无可用检查者", "source_refs": []})
    return plan


def _overall(checks: list[dict]) -> str:
    required = [c for c in checks if c["applicable"] and c["required"]]
    if any(c["status"] == "violated" for c in required):
        return "failed"
    if not required:
        return "not_evaluated"
    if all(c["status"] == "satisfied" for c in required):
        return "passed"
    if all(c["status"] == "not_run" for c in required):
        return "not_evaluated"
    return "needs_review"


def acceptance_report(task_context: dict, plan: list[dict], outcomes: dict[str, dict], *,
                      feedback_refs: list[dict] | None = None) -> dict:
    """Result data cannot weaken the preselected required/applicable flags."""
    checks = []
    uncertainties = list(task_context["payload"].get("assumptions") or [])
    for target in plan:
        result = outcomes.get(target["id"]) or {}
        status = result.get("status", "not_run") if target["applicable"] else "not_applicable"
        if status not in STATUSES:
            raise ValueError(f"检查 {target['id']} 返回未知状态 {status}")
        if target["applicable"] and status == "not_applicable":
            status = "undecidable"
        method = result.get("evidence_method") if status != "not_run" else None
        if method is not None and method not in METHODS:
            raise ValueError(f"检查 {target['id']} 返回未知证据方法 {method}")
        detail = str(result.get("detail") or "检查未运行；需要指定方法和完整证据。")
        # No independent observation may be represented by an empirical label.
        refs = result.get("evidence_refs") or []
        expected_method = target.get("method")
        if target["required"] and expected_method in {"empirical", "tool", "human"} and (
            status in {"satisfied", "violated"} and method != expected_method):
            status = "undecidable"
            detail = f"要求 {expected_method} 证据，现有 {method or '未知'} 证据不能替代；" + detail
        if status == "satisfied" and (method is None or (method in {"human", "empirical", "tool"} and not refs)):
            status = "undecidable"
            detail = "缺少可定位的判定证据；" + detail
        checks.append({"id": target["id"], "applicable": bool(target["applicable"]),
                       "required": bool(target["required"]), "status": status,
                       "evidence_method": method, "evidence_refs": refs, "detail": detail})
        if target["applicable"] and status in {"undecidable", "not_run", "error"}:
            uncertainties.append(f"{target['label']}：{detail}")
    status = _overall(checks)
    message = {"contract_version": "generation.v1", "kind": "AcceptanceReport",
               "identity": deepcopy(task_context["identity"]),
               "payload": {"status": status, "passed": status == "passed", "checks": checks,
                           "uncertainties": list(dict.fromkeys(uncertainties)),
                           "feedback_refs": feedback_refs or []},
               "extensions": {"check_plan": deepcopy(plan), "task_type": task_context["payload"]["task_type"],
                              "limitation": "通过仅表示适用必需检查满足；模型静态判断不保证绝对准确或实证收益。"}}
    validate_generation_message(message, expected_kind="AcceptanceReport")
    validate_generation_exchange(task_context, acceptance_report=message)
    return message


def _constraint_outcome(target: dict, task_context: dict, content: dict) -> dict:
    text = "\n".join(exam_check.section_body(s) for s in content.get("sections") or [])
    rule = target["label"]
    if re.search(r"只输出(?:一|1)套试卷", rule):
        papers = [s for s in content.get("sections", []) if exam_check.classify_spec(
            str(s.get("title", "")), content.get("format_spec"), exam_check.section_body(s)) == "exam"
            and exam_check.is_exam_markdown(exam_check.section_body(s))]
        if not papers and content.get("sections"):
            return {"status": "undecidable", "evidence_method": "deterministic",
                    "detail": "未识别到完整试卷单元；不能将分析或说明章节当成一套试卷。"}
        return {"status": "satisfied" if len(papers) == 1 else "violated",
                "evidence_method": "deterministic", "detail": f"最终试卷单元数为 {len(papers)}。"}
    from ..learning.teaching_profile import check_rule_compliance, parse_teaching_profile
    parsed = parse_teaching_profile(rule, context_text=task_context["payload"]["objective"])
    violations = check_rule_compliance(text, parsed.get("rules", []))
    if violations:
        return {"status": "violated", "evidence_method": "deterministic",
                "detail": "; ".join(violations), "evidence_refs": []}
    # Only the supported negative rules are actually decided by this checker.
    supported = bool(re.search(r"(?:不要|禁止|不允许|不得).*(?:标签|日历|按天|按周|时间表)", rule))
    if supported:
        return {"status": "satisfied", "evidence_method": "deterministic", "detail": "可判禁止项未出现。"}
    return {"status": "undecidable", "evidence_method": "deterministic",
            "detail": "现有确定性检查未覆盖此原文约束；需保留完整成果复核。"}


def _review_open_uncertainties(report: dict) -> list[str]:
    """报告里真正未决的 uncertainties。

    proposition_generated.classify_uncertainties 会把通用方法局限（未实测/排版/
    静态先验）分到 limitations、确认语句分到 confirmations，只剩未决项进
    open_uncertainties。新报告用分类结果；旧报告没有该字段时保守沿用原始
    uncertainties（旧版本报告保留原判定，不被新语义悄悄洗白）。
    """
    if "open_uncertainties" in report:
        return [str(u).strip() for u in (report.get("open_uncertainties") or []) if str(u).strip()]
    return [str(u).strip() for u in (report.get("uncertainties") or []) if str(u).strip()]


def _synthesize_findings(ledger: FindingLedger, quality: dict, content: dict) -> None:
    """无共享台账时，从 quality dict 把各检查者输出重新适配成发现（旧调用者回退路径）。

    复现 review_generated / run_exam_quality 的台账登记结果：审查缺陷 → review 发现，
    复核 resolved → 原发现 satisfied，复核新缺陷 → recheck 发现，L0 违规 → structure
    发现（fixed 项 satisfied），L1 缺陷 → correctness 发现；L1 已回写的答案按「新证据
    覆盖」关闭旧发现（resolved_by=l1_rewrite）。带共享台账时跳过（发现已在台账里）。
    """
    sections = [s for s in (content.get("sections") or []) if isinstance(s, dict)]
    unit_title = {exam_check.section_unit_id(s): str(s.get("title", "") or "") for s in sections}
    unit_paper: dict[str, object] = {}
    for section in sections:
        body = exam_check.section_body(section)
        if exam_check.is_exam_markdown(body):
            unit_paper[exam_check.section_unit_id(section)] = exam_check.parse_paper(
                body, str(section.get("title", "")))

    # L0：violation / fixed 已是 L0 修复后状态，hash 以当前正文为准。
    l0 = quality.get("exam_check") or {}
    for entry in (l0.get("sections") or []):
        unit_id = str(entry.get("unit_id") or "")
        if not unit_id:
            continue
        paper = unit_paper.get(unit_id)
        body = exam_check.section_body(next((s for s in sections
            if exam_check.section_unit_id(s) == unit_id), {}))
        for violation in entry.get("violations") or []:
            ledger.add_finding(finding_from_l0(
                violation, unit_id=unit_id, paper=paper, body=body,
                finding_id=ledger.new_finding_id(), spec=str(entry.get("spec") or "")))

    # L1：缺陷与已回写答案（新证据）。
    l1 = quality.get("exam_verify") or {}
    l1_applied: set[tuple[str, int]] = set()
    for paper_report in (l1.get("papers") or []):
        title = str(paper_report.get("title", "") or "")
        for applied in paper_report.get("applied") or []:
            if isinstance(applied, dict) and applied.get("number"):
                l1_applied.add((title, int(applied["number"])))
        for defect in paper_report.get("defects") or []:
            unit_id = next((u for u, t in unit_title.items() if t == title), "")
            paper = unit_paper.get(unit_id)
            ledger.add_finding(finding_from_l1_defect(
                defect, unit_id=unit_id, paper=paper, finding_id=ledger.new_finding_id()))

    # 审查报告：缺陷 → 发现；复核 resolved → satisfied；复核新缺陷 → 新发现。
    review = quality.get("proposition_review") or {}
    for report in review.get("reports") or []:
        if not isinstance(report, dict):
            continue
        unit_id = str(report.get("id") or "")
        paper = unit_paper.get(unit_id)
        body = exam_check.section_body(next((s for s in sections
            if exam_check.section_unit_id(s) == unit_id), {}))
        review_findings = []
        for defect in report.get("defects") or []:
            if not (isinstance(defect, dict) and defect.get("severity") == "blocking" and defect.get("evidence")):
                continue
            finding = finding_from_review(defect, unit_id=unit_id, paper=paper, body=body,
                                          finding_id=ledger.new_finding_id())
            ledger.add_finding(finding)
            review_findings.append(finding)
        for state in report.get("recheck_findings") or []:
            if state.get("status") == "resolved" and int(state.get("index", -1)) < len(review_findings):
                ledger.mark_satisfied(review_findings[int(state["index"])].finding_id,
                                      resolved_by=f"recheck:{unit_id}")
        for defect in ((report.get("recheck") or {}).get("defects") or []):
            if isinstance(defect, dict) and defect.get("severity") == "blocking" and defect.get("evidence"):
                ledger.add_finding(finding_from_review(
                    defect, unit_id=unit_id, paper=paper, body=body,
                    finding_id=ledger.new_finding_id(), source="recheck"))
        # L1 新证据覆盖：审查缺陷针对的答案被 L1 高置信回写 → 旧证据由新证据关闭。
        title = unit_title.get(unit_id, "")
        for finding in review_findings:
            if finding.status != "violated" or not finding.question_numbers:
                continue
            if (title, finding.question_numbers[0]) in l1_applied:
                ledger.mark_satisfied(finding.finding_id, resolved_by="l1_rewrite")


def _report_defects_covered(report: dict, findings: list[dict]) -> bool:
    """报告层「未解决」是否已被新证据完全覆盖（全部缺陷发现 satisfied/stale）。

    没有对应发现（无法定位的报告缺陷 → undecidable 发现）不算覆盖——未决项
    保守保留，不能因找不到位置就宣称旧缺陷已解决。
    """
    finding_ids = [str(x) for x in report.get("finding_ids") or []]
    related = ([f for f in findings if f.get("finding_id") in finding_ids] if finding_ids else
               [f for f in findings if f.get("unit_id") == str(report.get("id") or "")
                and f.get("source") == "review"])
    if not related:
        return False
    return all(f.get("status") in {"satisfied", "stale"} for f in related)


def quality_acceptance(goal, content: dict, quality: dict, *, tutorial_id: str = "", version=None,
                       task_context: dict | None = None, strategy: dict | None = None,
                       criteria: list[dict] | None = None, rubric: list[dict] | None = None,
                       planned_checks: list[dict] | None = None, outcomes: dict | None = None,
                       ledger: FindingLedger | None = None) -> dict:
    """Bridge legacy final quality and a typed report without turning ID coverage into value.

    S2：发现统一进 ``extensions.findings``（最终正文 hash 对齐后的 current/history），
    structure 只消费确定性检查，correctness 汇集模型/工具层的最终发现。
    """
    context = adapt_task_context(goal, content, tutorial_id=tutorial_id, version=version,
                                 task_context=task_context)
    if strategy is not None:
        # The frozen strategy describes the same task, reused for a new artifact version.
        # Preserve source snapshots while projecting its artifact identity for this report.
        strategy = deepcopy(strategy)
        strategy["identity"]["tutorial_id"] = context["identity"]["tutorial_id"]
        strategy["identity"]["version"] = context["identity"]["version"]
    plan = planned_checks or plan_checks(context, strategy=strategy, criteria=criteria, rubric=rubric)
    evidence_use_verification: dict = {}
    # R2-3：下界类设问明确性（确定性检查需求；advisory，required=False 不阻断交付）。
    from .proposition_generated import bound_tightness_findings

    bound_findings = bound_tightness_findings(content)
    if bound_findings and not any(p.get("id") == "bound_tightness" for p in plan):
        plan.append({"id": "bound_tightness", "label": "边界性设问明确性（最大下界/最小可能秩）",
                     "layer": "professional_value", "applicable": True, "required": False,
                     "strength": "soft", "method": "deterministic",
                     "rubric_version": DELIVERY_RUBRIC_VERSION,
                     "evidence_requirement": "边界类题目须要求最大下界/最小可能秩并给可核查依据；"
                                             "仅写 r(C)≥ 无最大/最小限定 → advisory",
                     "source_refs": []})
    # ---- 方法拆分：structure 只消费确定性检查；模型/工具检查与 issue 归 correctness ----
    all_checks = [c for c in quality.get("checks", []) if c.get("name") != "任务特异验收"]
    deterministic_checks = [c for c in all_checks if c.get("name") not in MODEL_QUALITY_CHECK_NAMES]
    issues = [str(i) for i in (quality.get("issues") or [])]
    structural_issues = [i for i in issues if not i.startswith(MODEL_ISSUE_PREFIXES)]
    outcomes = deepcopy(outcomes or {})
    structure_supplied = "structure" in outcomes
    check_errors = any(s.get("error") for s in (quality.get("spec_check") or {}).get("sections", []))
    outcomes.setdefault("structure", {"status": "error" if check_errors else
        "satisfied" if deterministic_checks and all(c.get("passed", False) for c in deterministic_checks)
        and not structural_issues else "violated",
        "evidence_method": "deterministic",
        "detail": "确定性结构/专项检查；模型静态与工具结论在 correctness 层判定。",
        "evidence_refs": [source_ref("generated", tutorial_id or "legacy", version=version,
                                     location="quality_report.checks")]})
    for target in plan:
        if target["layer"] == "hard_constraint" and target["id"] not in outcomes:
            outcomes[target["id"]] = _constraint_outcome(target, context, content)
    if bound_findings:
        outcomes.setdefault("bound_tightness", {
            "status": "violated", "evidence_method": "deterministic",
            "detail": "；".join(f"{f['reason']}（{f['location']}）" for f in bound_findings[:5]),
            "evidence_refs": [source_ref("generated", tutorial_id or "legacy", version=version,
                                         location=f"unit:<unit>/{f['location']}")
                              for f in bound_findings[:5]]})

    # ---- 发现台账：继承共享台账或从 quality dict 重新适配，并按最终正文 hash 对齐 ----
    if ledger is None:
        ledger = FindingLedger(tutorial_id=tutorial_id, version=version)
        _synthesize_findings(ledger, quality, content)
    extension = ledger.finalize(content)
    current = extension["items"]
    history = extension["history"]
    correctness_findings = [f for f in current if f.get("check_id") == "correctness"]
    violated_findings = [f for f in correctness_findings if f.get("status") == "violated"]
    undecidable_findings = [f for f in correctness_findings if f.get("status") == "undecidable"]
    stale_findings = [f for f in correctness_findings if f.get("status") == "stale"]

    review = quality.get("proposition_review") or {}
    reports = review.get("reports") or []
    review_active = bool(review.get("enabled")) and not review.get("demo") and bool(reports)
    l1 = quality.get("exam_verify") or {}
    l1_incomplete = bool(l1.get("errors")) or any(
        p.get("error") or any(s.get("verdict") == "undecidable" for s in p.get("skipped", []))
        for p in l1.get("papers", []))
    if review_active or correctness_findings or l1_incomplete:
        report_unresolved = [r for r in reports if (r.get("repair") != "applied_once" and any(
            isinstance(d, dict) and d.get("severity") == "blocking" and d.get("evidence")
            for d in r.get("defects", []))) or r.get("recheck_status") == "violated"] if review_active else []
        # 旧证据不掩盖新正文：报告层未解决若已被 satisfied/stale 发现完全覆盖，
        # 不再作为 violated 依据（stale → undecidable，由下方独立信号承接）。
        effective_unresolved = [r for r in report_unresolved if not _report_defects_covered(r, current + history)]
        recheck_violated = any(r.get("recheck_status") == "violated" for r in reports)
        # 未实测/排版类通用方法局限已在报告内分类为 limitations（open_uncertainties
        # 只剩真正未决项）；不得再把已确认修复拖成 undecidable。旧报告沿用原判定。
        incomplete = [r for r in reports if r.get("status") in {"failed", "deferred"} or
                      (r.get("repair") == "applied_once" and r.get("recheck_status") != "satisfied") or
                      (r.get("repair") != "applied_once" and _review_open_uncertainties(r))] if review_active else []
        other_failures = [c for c in deterministic_checks if not c.get("passed", False)]
        # Missing model review evidence belongs to correctness. It must not
        # overwrite completed deterministic structure checks with an error.
        if not structure_supplied and not deterministic_checks and incomplete and not report_unresolved and not other_failures:
            outcomes["structure"]["status"] = "error"
            outcomes["structure"]["detail"] = "已选检查未完成；不能把缺证据称为已判错误或通过。"
        # 方法局限如实呈现：模型静态证据不冒充已实测，empirical/tool 层保持 not_run。
        limitations = [lim for r in reports for lim in
                       [*(r.get("limitations") or []), *(r.get("recheck_limitations") or [])]] if review_active else []
        correctness_detail = "生成后内容审查与定向修复复核；仍为模型静态证据。"
        # R2-2：evidence_use 自述消费核验——只陈述可核验事实，不推断迁移成功。
        evidence_use_verification = quality.get("evidence_use_verification") or {}
        if evidence_use_verification.get("verified"):
            counts = "，".join(f"{key}={value}" for key, value in
                               sorted((evidence_use_verification.get("counts") or {}).items()))
            correctness_detail += (f"证据使用自述核验 {int(evidence_use_verification['verified'])} 条"
                                   f"（{counts}）；confirmed 仅表示自述与注入/正文一致，不证明迁移效果。")
        unique_limitations = list(dict.fromkeys(str(lim) for lim in limitations if str(lim).strip()))
        if unique_limitations:
            correctness_detail += ("方法局限（empirical/工具证据未运行，不冒充已实测）："
                                   + "；".join(unique_limitations))
        stale_notes = [f for f in stale_findings if f.get("detail")]
        if stale_notes:
            correctness_detail += ("存在基于旧正文的过期证据（已标 stale，需重检）："
                                   + "；".join(str(f.get("finding_id")) for f in stale_notes))
        if violated_findings or recheck_violated or effective_unresolved:
            correctness_status = "violated"
        elif incomplete or undecidable_findings or stale_findings or l1_incomplete:
            correctness_status = "undecidable"
        elif review_active:
            correctness_status = "satisfied"
        else:
            # 审查未运行而 L1 又没有发现：诚实保持未运行（关闭模型 → 正确性待复核）。
            correctness_status = "not_run"
        refs = evidence_refs_for(current, "correctness", tutorial_id=tutorial_id, version=version)
        outcomes.setdefault("correctness", {"status": correctness_status,
            "evidence_method": "model_static" if correctness_status != "not_run" else None,
            "detail": correctness_detail,
            "evidence_refs": refs or [source_ref("generated", tutorial_id or "legacy", version=version,
                                                location="quality_report.proposition_review")]})
        for target in plan:
            if outcomes.get(target["id"], {}).get("status") in (None, "not_run", "undecidable") and re.search(
                r"(?:答案|题目|题面|选项).*(?:正确|无误|完整)|证明.*(?:完整|无缺口)", target["label"]):
                outcomes[target["id"]] = deepcopy(outcomes["correctness"])
    result = acceptance_report(context, plan, outcomes)
    # 明细只进 extensions（generation.v1 不变）；checks.evidence_refs 指向 finding。
    structure_refs = evidence_refs_for(current, "structure", tutorial_id=tutorial_id, version=version)
    if structure_refs:
        result["extensions"]["structure_findings"] = structure_refs
    result["extensions"]["findings"] = extension
    result["extensions"]["rubric_version"] = DELIVERY_RUBRIC_VERSION
    if bound_findings:
        result["extensions"]["bound_tightness"] = bound_findings
    if evidence_use_verification:
        result["extensions"]["evidence_use_verification"] = evidence_use_verification
    validate_generation_exchange(context, strategy, acceptance_report=result)
    return result
