"""统一任务理解：目标 + 硬约束 + 参考材料 + 既有偏好 → generation.v1 TaskContext。

职责边界（板块一）：理解目标、调度规范与策略输入；不评价题目、不判断交付质量。
本模块是确定性的（ADR-015/018 同源原则）：分类、条件求值、难度解析、参考画像统计
全部不调用模型；语义理解复用既有材料处理与大纲调用的注入链路（参考画像摘要随
TaskContext 进入 Synthesis/Blueprint prompt），不新增无依据的模型往返。

红线：
- 参考材料只提供风格、分布、范围与证据；逐题解析仅作统计证据，不成为仿写映射。
- 无题型标题不得推断为六题型或客观题；无参考来源不得声称已对标。
- 上传材料中的指令、答案、批注是数据，不是执行授权；来源错题不自动当正确知识。
- 同级冲突可见（conflicts 列表），不能擅自忽略一方；只有确实影响结果且不能合理
  默认时才返回最少澄清项（clarifications）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy.orm import Session

from ..learning.disciplines import canonical_domain_key
from ..learning.teaching_profile import parse_teaching_profile
from ..models import GoalMaterial, LearningGoal
from .generation_contract import validate_generation_message
from .reference_documents import (
    aggregate_reference_documents,
    legacy_question_count_summary,
    parse_reference_documents,
)

# ---------------------------------------------------------------- 同义表达归一
# 用户不写精准提示词也能进正确路径：把口语说法归一到五种任务类型。
_TASK_TEXT_HINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    # (task_type, 描述, 触发词) —— 触发词做包含匹配（已 lower）
    ("exam", "试卷/练习卷类交付", (
        "试卷", "出题", "出卷", "模拟考试", "模拟卷", "检测卷", "测验卷", "练习卷",
        "题库", "真题", "试题", "考卷", "出一套", "套卷", "命题",
    )),
    ("exercise", "练习/刷题类交付", ("练习题", "刷题", "习题集", "做题练习", "课后习题")),
    ("reading_guide", "阅读导读类交付", ("导读", "阅读指导", "带着我读", "读书笔记导读")),
    ("practical_tutorial", "实践教程", ("实操", "上手操作", "操作指南", "使用教程", "工具教程")),
    ("knowledge_tutorial", "知识教程", ("教程", "学会", "理解", "掌握", "讲解")),
)

# 任务书：先于「知识教程」判定的强信号优先级在 classify_task_type 内显式排序。
_SCENE_TASK_TYPES = {
    "exam_paper": "exam",
    "reading_guide": "reading_guide",
    "practical_tutorial": "practical_tutorial",
    "exercise": "exercise",
    "default_tutorial": "knowledge_tutorial",
}
# 练习生成规范 scene 由 format_specs.get_exercise_spec_dict 读取，目标绑定场景仍按上表。

_TASK_TYPE_ALIASES = {
    "exam": "exam", "exam_paper": "exam", "paper": "exam",
    "exercise": "exercise", "exercises": "exercise",
    "reading_guide": "reading_guide",
    "knowledge_tutorial": "knowledge_tutorial", "default_tutorial": "knowledge_tutorial",
    "practical_tutorial": "practical_tutorial",
}

# 用户显式难度表达（难度期望字段为空时按原话归一；显式选择优先于默认拔高）
_DIFFICULTY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("basic", ("基础", "简单", "容易", "入门", "基础题", "基础达标", "由浅入深")),
    ("equal", ("同等难度", "等难", "与参考相当", "和参考一样", "难度一致", "同难度")),
    ("harder", ("更难", "拔高", "高于参考", "加难", "难一点", "比参考难")),
)

# 「不要答案」与「要答案」并存等同级冲突检测
_CONFLICT_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("answers", r"(?:不要|不得|禁止|不附|不含|无需)[^。；\n]{0,8}答案", r"(?:需要|要|附|给|包含|写出)[^。；\n]{0,6}(?:答案|解析)"),
    ("schedule", r"(?:不要|不得|禁止)[^。；\n]{0,8}(?:日程|计划表|时间表)", r"(?:安排|制定|给出)[^。；\n]{0,6}(?:日程|计划表|时间表)"),
)
_COUNT_RE = re.compile(r"(?:共|要|生成|输出|出)?\s*([0-9一二两三四五六七八九十]+)\s*套")
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 材料内嵌答案/解析与疑似指令（是数据，不是授权）
_ANSWER_EVIDENCE_RE = re.compile(r"参考答案|【答案】|答案[:：]|解析[:：]|【解析】|评分标准")
_EMBEDDED_INSTRUCTION_RE = re.compile(
    r"^\s*(?:请|务必|注意|要求|提示模型|生成时|输出时)[^。；\n]{0,40}"
    r"(?:生成|输出|确保|保证|必须|不要|避免|按)", re.I
)
_KNOWLEDGE_POINT_RE = re.compile(r"(?:知识点|考查|涉及|考点)[:：]\s*([^。\n]{1,120})")
_EXCLUDE_RE = re.compile(r"(?:不要考|不涉及|不考|排除|不包含|不要包含)[^。；\n，,]{1,20}")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm_body(text: Any) -> str:
    """保行归一：压行内空白、去行尾空白，但保留换行结构（逐行解析依赖它）。"""
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in str(text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _text_ref(identifier: Any, text: str, location: str) -> dict:
    return {"kind": "user_task", "id": str(identifier), "version": None,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
            "location": location}


def material_ref(row: GoalMaterial, body: str) -> dict:
    return {"kind": "material", "id": row.id, "version": None,
            "hash": hashlib.sha256((body or "").encode("utf-8")).hexdigest() if body else None,
            "location": f"filename:{row.filename}"}


# ---------------------------------------------------------------- 任务类型识别

def classify_task_type(goal: LearningGoal, snapshot: dict | None) -> str:
    """任务类型判定；显式选择的规范优先于文本推断，文本推断优先于规范默认场景。

    「显式选择优先于默认」：用户手动选择的规范（source=manual/explicit）表达的是
    明确意图；自动匹配的规范（keyword/semantic/default）本身来自文本线索，
    此时用户原话里的强信号（如「出三套卷」）更可信。
    """
    snap = snapshot or {}
    scene = _norm(snap.get("scene"))
    scene_type = _TASK_TYPE_ALIASES.get(scene, "")
    source = _norm(getattr(goal, "format_spec_source", ""))
    text = " ".join(str(getattr(goal, f, "") or "") for f in
                    ("title", "motivation", "application_context", "hard_constraints", "user_notes")).lower()
    text_type = ""
    for task_type, _label, words in _TASK_TEXT_HINTS:
        if any(word in text for word in words):
            text_type = task_type
            break
    if source in {"manual", "explicit"} and scene_type:
        return scene_type
    if text_type:
        return text_type
    return scene_type or "knowledge_tutorial"


# ---------------------------------------------------------------- 难度目标解析

def resolve_difficulty(goal: LearningGoal, profile: dict | None) -> dict:
    """难度目标：用户显式选择 > 用户原话归一 > 默认整体高于参考 > 无来源明确默认。

    - 无参考来源时不声称已对标（instruction 显式写明）。
    - evidence_basis 恒为 prior（难度是 declared/先验判断，不是实测）。
    - 整体拔高 ≠ 每题机械加难：由策略层用不同难度机制表达。
    """
    refs: list[dict] = [r for r in (profile or {}).get("source_refs", []) if isinstance(r, dict)]
    mode_pref = _norm(getattr(goal, "difficulty_mode", ""))
    note = _norm(getattr(goal, "difficulty_note", ""))
    if not mode_pref:
        # 难度比较必须先读当前硬约束；“矩阵基础”等知识名称不应抢占难度意图。
        hard = str(getattr(goal, "hard_constraints", "") or "")
        if re.search(r"难度\s*(?:明显|整体|略)?\s*(?:高于|超过)|(?:高于|超过).{0,10}(?:来源|参考|例题|原题).*难度", hard):
            mode_pref = "harder"
        elif re.search(r"难度\s*(?:明显|整体|略)?\s*低于", hard):
            mode_pref = "basic"
        elif re.search(r"难度.{0,16}(?:相当|一致|保持不变)", hard):
            mode_pref = "equal"
    if not mode_pref:
        # 用户原话里的难度词归一（同义表达；显式字段缺失时仍尊重原意）
        text = " ".join(str(getattr(goal, f, "") or "") for f in
                        ("title", "motivation", "application_context", "hard_constraints", "user_notes"))
        for pref, words in _DIFFICULTY_HINTS:
            if any(word in text for word in words):
                mode_pref = pref
                break
    explicit_instruction = note or _explicit_difficulty_line(str(getattr(goal, "hard_constraints", "") or ""))
    if mode_pref == "basic":
        if refs:
            return {"mode": "below_reference", "instruction": explicit_instruction or "基础达标：难度低于参考来源，覆盖同一范围。",
                    "evidence_basis": "prior", "source_refs": refs}
        return {"mode": "explicit", "instruction": explicit_instruction or "基础难度（未提供参考来源，不声称对标任何参考卷）。",
                "evidence_basis": "prior", "source_refs": []}
    if mode_pref == "equal":
        if refs:
            return {"mode": "equal_reference", "instruction": explicit_instruction or "与参考来源整体难度相当。",
                    "evidence_basis": "prior", "source_refs": refs}
        return {"mode": "explicit", "instruction": explicit_instruction or "与常规难度相当（未提供参考来源，不声称对标）。",
                "evidence_basis": "prior", "source_refs": []}
    if mode_pref == "harder":
        if refs:
            return {"mode": "above_reference", "instruction": explicit_instruction or "整体难度明显高于参考来源。",
                    "evidence_basis": "prior", "source_refs": refs}
        return {"mode": "explicit", "instruction": explicit_instruction or "整体难度高于常规水平（未提供参考来源，不声称对标）。",
                "evidence_basis": "prior", "source_refs": []}
    if explicit_instruction:
        return {"mode": "explicit", "instruction": explicit_instruction,
                "evidence_basis": "prior", "source_refs": refs}
    if refs:
        # 任务书默认：整体难度高于参考来源；通过策略分配实现，不逐题机械加难。
        return {"mode": "above_reference", "instruction": None,
                "evidence_basis": "prior", "source_refs": refs}
    # 无来源：明确默认，不虚假对标。
    return {"mode": "unspecified",
            "instruction": "未提供参考来源，无法对标；按学习者层级与学科默认组织难度，不声称已对标任何参考卷。",
            "evidence_basis": "unknown", "source_refs": []}


def _explicit_difficulty_line(hard_constraints: str) -> str:
    for line in str(hard_constraints or "").splitlines():
        clean = _norm(line)
        if clean and re.search(r"难度|压轴|基础题|简单题|高考|竞赛|考研", clean):
            return clean
    return ""


# ---------------------------------------------------------------- 参考画像

def _profile_materials(db: Session, goal_id: str) -> list[GoalMaterial]:
    return (
        db.query(GoalMaterial)
        .filter(GoalMaterial.goal_id == goal_id)
        .order_by(GoalMaterial.created_at.asc())
        .all()
    )


def _parse_materials(db: Session, goal_id: str) -> list[dict]:
    """逐份读取材料并解析逻辑文档；返回 [{"row","body","parsed"}]（空正文不含 parsed）。

    解析是纯函数（reference_documents.parse_reference_documents）：跨文档状态显式重置，
    修复三册合辑「第二册无题型标题沿用第一册解答题状态」的串染错误。
    """
    entries: list[dict] = []
    for row in _profile_materials(db, goal_id):
        body = _norm_body(row.text_excerpt)
        entry: dict = {"row": row, "body": body}
        if body:
            entry["parsed"] = parse_reference_documents(body, material_ref(row, body))
        entries.append(entry)
    return entries


def _reference_documents_extension(entries: list[dict]) -> dict | None:
    """TaskContext.extensions.reference_documents：合辑级逻辑文档视图（纯装配，零推断）。

    携带 per-document count、question spans、count_basis、parse_coverage、anomalies；
    legacy_question_count 记录单卷口径适配语义（合辑总量不冒充单卷题量）。
    """
    payloads = [
        {"material_id": str(entry["row"].id),
         "filename": str(entry["row"].filename or entry["row"].id),
         "parsed": entry.get("parsed")}
        for entry in entries if entry.get("body")
    ]
    if not payloads:
        return None
    aggregate = aggregate_reference_documents(payloads)
    if not aggregate["documents"]:
        return None
    return {
        **aggregate,
        "legacy_question_count": legacy_question_count_summary(aggregate),
    }


def _profile_from_parsed(entries: list[dict]) -> dict | None:
    """从已解析材料装配 generation.v1 ReferenceProfile（不加未声明字段）。

    - 题型分布逐逻辑文档聚合；跨文档状态不串染。
    - question_count 是 legacy 字段，语义 = 典型单卷题量：各逻辑卷一致 → 该值；
      不一致/未识别 → None + 范围摘要（不取合计/均值冒充共同题量）；
      合辑总量只在 extensions.reference_documents.totals（契约 §1.1）。
    - 材料内答案/解析/指令是数据：只记疑点，不进约束、不成为执行授权。
    """
    if not entries:
        return None
    question_types: dict[str, dict] = {}
    knowledge_points: list[str] = []
    style_features: list[str] = []
    anomalies: list[str] = []
    source_refs: list[dict] = []
    aggregate_entries: list[dict] = []
    for entry in entries:
        row, body = entry["row"], entry["body"]
        label = row.filename or row.id
        source_refs.append(material_ref(row, body))
        parsed = entry.get("parsed")
        if not body or not parsed:
            # 抽取失败 / 空材料：如实记为来源疑点（它属于参考集合），不参与统计。
            if row.extract_status in {"failed", "unsupported"}:
                anomalies.append(
                    f"材料「{label}」正文抽取{row.extract_status}；仅作来源标注，未参与统计。")
            continue
        aggregate_entries.append(
            {"material_id": str(row.id), "filename": label, "parsed": parsed})
        multi_doc = parsed["logical_document_count"] > 1
        if not parsed["documents"]:
            anomalies.append(f"材料「{label}」未能解析出逻辑文档与题量；不编造题数。")
        for doc in parsed["documents"]:
            doc_label = f"{label}·第{doc['index']}册" if multi_doc else label
            count = doc["question_count"]
            if count is None:
                anomalies.append(f"材料「{doc_label}」未能可靠识别题量；不编造题数。")
            else:
                dist_text = "、".join(
                    f"{t['name']} {t['count']}" for t in doc["type_distribution"]) or "未提供题型划分"
                style_features.append(
                    f"{doc_label}：题量 {count}（{doc['count_basis']}），{dist_text}。")
            for form in doc["type_distribution"]:
                # ReferenceForm.basis 枚举只有 direct/inferred/unknown：
                # direct=逐题识别；declared（卷头自述）按证据强度归入 inferred。
                basis = "direct" if form["basis"] == "direct" else "inferred"
                type_entry = question_types.setdefault(
                    form["name"], {"name": form["name"], "count": 0, "basis": "inferred",
                                   "source_refs": []})
                type_entry["count"] += int(form["count"] or 0)
                if basis == "direct":
                    type_entry["basis"] = "direct"
                type_entry["source_refs"].append(material_ref(row, body))
            # 有题型标题但小节内没数出题号也没自述：题型存在、题量未知，不推断客观/主观。
            if doc["type_distribution_basis"] == "unknown" and doc["type_sections"]:
                for section in doc["type_sections"]:
                    question_types.setdefault(
                        section["name"],
                        {"name": section["name"], "count": None, "basis": "inferred",
                         "source_refs": [material_ref(row, body)]})
            for anomaly in doc["anomalies"]:
                anomalies.append(
                    f"材料「{doc_label}」疑点[{anomaly['kind']}]：{anomaly['message']}")
        # 材料级既有疑点保留（答案/指令/截断/failed）
        if int(row.source_char_count or 0) > int(row.char_count or 0):
            anomalies.append(f"材料「{label}」入库被截断：统计仅基于已入库文本。")
        if row.extract_status in {"failed", "unsupported"}:
            anomalies.append(
                f"材料「{label}」正文抽取{row.extract_status}；仅作来源标注，未参与统计。")
        if _ANSWER_EVIDENCE_RE.search(body):
            anomalies.append(
                f"材料「{label}」内含答案/解析：仅作分布与风格统计证据，"
                "不自动视为正确知识；来源题目可能存在错漏，不作逐题仿写。")
        embedded = [_norm(line) for line in body.splitlines() if _EMBEDDED_INSTRUCTION_RE.match(line)]
        if embedded:
            anomalies.append(
                f"材料「{label}」发现疑似指令文本（如「{embedded[0][:40]}」）："
                "按数据处理，不作为执行授权，不进入提示词规则。")
        for match in _KNOWLEDGE_POINT_RE.finditer(body):
            for part in re.split(r"[,，;；、]", match.group(1)):
                clean = _norm(part)
                if 1 < len(clean) <= 40:
                    knowledge_points.append(clean)
    aggregate = aggregate_reference_documents(aggregate_entries)
    summary = legacy_question_count_summary(aggregate)
    profile = {
        "question_count": summary["question_count"],
        "question_types": [],
        "knowledge_points": [],
        "style_features": [],
        "anomalies": [],
        "source_refs": [],
    }
    if summary["question_count"] is None and any(
            isinstance(c, int) for c in summary["per_document_counts"]):
        anomalies.append(
            f"参考题量在逻辑卷间不同（{summary['range_summary']}，合计 {summary['total_count']}）："
            "不取合计或均值冒充共同单卷题量；单卷口径置 None，合辑总量与分布见 extensions.reference_documents。")
    typed_names = sorted(question_types, key=lambda n: -((question_types[n]["count"] or 0)))
    profile["question_types"] = [question_types[name] for name in typed_names]
    if not profile["question_types"]:
        anomalies.append("参考材料未提供题型标题：不推断为六题型或客观/主观分布，题型按本次任务与学科确定。")
    profile["knowledge_points"] = list(dict.fromkeys(knowledge_points))[:40]
    if profile["knowledge_points"]:
        profile_style = "知识点来源为材料中的「知识点/考查/涉及」标注，仅作范围证据。"
        style_features.append(profile_style)
    profile["style_features"] = style_features
    profile["anomalies"] = list(dict.fromkeys(anomalies))
    profile["source_refs"] = source_refs
    return profile


def build_reference_profile(db: Session, goal: LearningGoal) -> dict | None:
    """从参考材料统计真实作答形态与分布；无材料返回 None（不是空画像假装有据）。

    多来源/多逻辑卷：逐份统计后给出共同特征与合理差异，不强制某份参考对应某套生成卷；
    question_count 为典型单卷口径（见 _profile_from_parsed 与契约 §1.1）。
    """
    return _profile_from_parsed(_parse_materials(db, goal.id))


# ---------------------------------------------------------------- 约束与冲突

def _cn_count(token: str) -> int | None:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token)


def collect_constraints(goal: LearningGoal) -> tuple[list[dict], list[str], list[str], dict]:
    """本次任务生效的原文约束（用户硬约束，hard/current_user）+ 画像规则调度记录。

    画像的长期规则不进 acceptance 约束清单：它们由 ADR-015 自己的选择/核验路径
    承载（teaching charter 注入 + `_rule_checked` 产出 rule_check），放进
    验收必需检查只会对现有确定性核验不覆盖的正向规则产出不可判定的未决项。
    这里把「本次生效了哪些画像规则、几硬几软」记进调度记录，冲突可见、来源可查。

    返回 (constraints, conflicts, assumptions, profile_scheduling)。
    """
    hard_text = str(getattr(goal, "hard_constraints", "") or "")
    constraints: list[dict] = []
    conflicts: list[str] = []
    assumptions: list[str] = []
    lines = [ _norm(line) for line in hard_text.splitlines() if _norm(line)]
    for i, line in enumerate(lines, 1):
        constraints.append({"id": f"hard-{i}", "text": line, "strength": "hard",
                            "origin": "current_user",
                            "source_refs": [_text_ref(goal.id, hard_text, f"hard_constraints:{i}")]})
    profile_data = parse_teaching_profile(context_text=_goal_context(goal))
    active_hard = [str(r["text"]) for r in profile_data.get("rules", []) if r.get("hard")]
    inactive = [str(x) for x in profile_data.get("inactive", [])]
    profile_scheduling = {
        "source": profile_data.get("source", "long_term_user_profile"),
        "active_hard_rules": active_hard[:8],
        "inactive_rules": inactive[:8],
        "enforcement": "teaching_charter 注入 + _rule_checked 生成期核验（ADR-015/018），"
                       "核验结果落在章节 rule_check 字段；不重复计入验收必需检查。",
    }
    if inactive:
        assumptions.append(
            "画像条件规则本次未生效（条件不成立）："
            + "；".join(inactive[:3]) + ("…" if len(inactive) > 3 else ""))
    # 同级冲突可见：同一来源（用户硬约束内部）互相否定时列出，不擅自取舍。
    joined = "\n".join(lines)
    for name, negative_re, positive_re in _CONFLICT_PATTERNS:
        negative_lines = [line for line in lines if re.search(negative_re, line)]
        positive_lines = [line for line in lines if re.search(positive_re, line)]
        # 否定行自身常含肯定动词（「不要安排日程」里有「安排」）：肯定侧只看非否定行。
        has_negative = bool(negative_lines)
        has_positive = any(line not in negative_lines for line in positive_lines)
        if has_negative and has_positive:
            label = "答案" if name == "answers" else "日程/时间表"
            conflicts.append(f"硬性约束同时包含「不要{label}」与「要{label}」的表述，两者同级冲突，需用户确认取舍。")
    counts = {_cn_count(m.group(1)) for m in _COUNT_RE.finditer(joined)}
    counts.discard(None)
    if len(counts) > 1:
        conflicts.append(
            f"硬性约束出现不同的套数要求（{'、'.join(str(c) for c in sorted(counts))}），需确认本次份数。")
    return constraints, conflicts, assumptions, profile_scheduling


def _goal_context(goal: LearningGoal) -> str:
    return "\n".join(str(getattr(goal, f, "") or "") for f in
                     ("title", "motivation", "application_context", "current_level", "time_budget", "user_notes"))


# ---------------------------------------------------------------- 范围与交付

def _scope(goal: LearningGoal, profile: dict | None) -> dict:
    include: list[str] = []
    basis = "unknown"
    refs: list[dict] = []
    if profile and profile.get("knowledge_points"):
        include = list(profile["knowledge_points"])
        basis = "direct"
        refs = [r for r in profile["source_refs"] if isinstance(r, dict)][:8]
    branch = _norm(getattr(goal, "branch", ""))
    if not include and branch:
        include = [branch]
        basis = "inferred"
        refs = [_text_ref(goal.id, branch, "goal.branch")]
    exclude: list[str] = []
    for match in _EXCLUDE_RE.finditer(str(getattr(goal, "hard_constraints", "") or "")):
        clean = _norm(match.group(0))
        clean = re.sub(r"^(?:不要考|不涉及|不考|排除|不包含|不要包含)[:：]?", "", clean).strip()
        if clean:
            exclude.append(clean)
    return {"include": include, "exclude": list(dict.fromkeys(exclude)), "basis": basis, "source_refs": refs}


def _deliverables(outline: list[dict] | None, snapshot: dict | None) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(outline or []):
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("id") or item.get("unit_id") or f"unit-{index + 1}")
        # 防御异常数据：重复 unit_id 会让整条生成链路的契约校验失败，这里保序去重。
        base_id, suffix = unit_id, 1
        while unit_id in seen:
            suffix += 1
            unit_id = f"{base_id}-{suffix}"
        seen.add(unit_id)
        title = _norm(item.get("title")) or f"单元{index + 1}"
        items.append({"unit_id": unit_id, "title": title})
    if items:
        return items
    for index, item in enumerate((snapshot or {}).get("deliverables") or []):
        name = _norm(item.get("name"))
        status = str(item.get("status", ""))
        if not name or status == "forbidden":
            continue
        items.append({"unit_id": f"deliverable-{index + 1}", "title": name})
    return items


# ---------------------------------------------------------------- 澄清项

def clarifications(conflicts: list[str]) -> list[str]:
    """最少澄清项：只有同源冲突无法用默认规则消解时才问。"""
    return [f"请确认：{conflict}" for conflict in conflicts] if conflicts else []


# ---------------------------------------------------------------- 组装

def scheduling_decisions(
    goal: LearningGoal, *, task_type: str, task_source: str, snapshot: dict | None,
    profile: dict | None, difficulty: dict, criteria_count: int,
    profile_rules: dict | None = None,
) -> dict:
    """规则来源、适用理由与优先级；普通界面只展示这里，向量/协议细节不进表单。"""
    snap = snapshot or {}
    mode_pref = _norm(getattr(goal, "difficulty_mode", ""))
    if mode_pref:
        difficulty_source = "user_explicit"
    elif difficulty["mode"] == "above_reference":
        difficulty_source = "default_elevation"
    else:
        difficulty_source = "no_reference_default"
    return {
        "priority_ladder": "current_user > active_profile > task_spec > default",
        "task_type": {"value": task_type, "source": task_source,
                      "reason": f"依据{task_source}判定；同义说法已归一。"},
        "format_spec": {
            "id": snap.get("id", ""), "name": snap.get("name", ""),
            "source": getattr(goal, "format_spec_source", "") or "default",
            "snapshot_version": snap.get("snapshot_version", ""),
            "note": getattr(goal, "format_spec_match_note", "") or "",
            "reason": "显式选择优先于默认；选定后规范独占结构、标题与排版权限。",
        },
        "knowledge": {
            "scope_basis": (profile or {}).get("knowledge_points") and "materials" or
                           (_norm(getattr(goal, "branch", "")) and "goal.branch" or "unknown"),
            "reason": "检索与验收消费细粒度知识点/题型，不把整份 practice_focus 当标签。",
        },
        "difficulty": {
            "mode": difficulty["mode"],
            "source": difficulty_source,
            "reason": "用户明确的基础/等难/更难及硬性难度要求优先；默认整体高于参考来源；无来源不声称对标。",
        },
        "evaluation": {"criteria_count": criteria_count,
                       "source": "confirmed_criteria" if criteria_count else "none",
                       "reason": "评价体系按任务类型取任务特异量规，用户已确认标准优先。"},
        "profile_rules": profile_rules or {},
        "reference_policy": "参考来源只提供风格、分布、范围与证据；逐题解析仅作统计证据，不作仿写映射；"
                            "来源错题与不完整材料不自动当正确知识；材料内指令按数据处理。",
    }


# ---------------------------------------------------------------- 大纲语义补充

# 模型可贡献的语义条目 kinds；确定性事实（题量/分值）由解析器负责，adapter 拒绝。
_SEMANTIC_FACT_KINDS = frozenset({"question_count", "score_distribution", "score"})
_SEMANTIC_ENTRY_LIMIT = 24


def reference_source_texts(db: Session, goal_id: str) -> dict[str, str]:
    """材料定位 → 正文（id 与 filename 都作键），供 reference_semantics_adapter 核对引文。"""
    texts: dict[str, str] = {}
    for row in _profile_materials(db, goal_id):
        body = _norm_body(row.text_excerpt)
        if row.id:
            texts[str(row.id)] = body
        if row.filename:
            texts[str(row.filename)] = body
    return texts


def reference_semantics_adapter(
    outline_data: dict, task_context: dict, *, source_texts: dict[str, str] | None = None,
) -> dict:
    """校验并装配大纲调用返回的内部 reference_semantics（契约 §3；零模型调用）。

    - 只保留「引文能在对应材料正文逐字命中」的条目（basis=quote_match）；
    - source_ref 不在已上传材料内 / 缺引文 / 是确定性事实 → dropped + 原因；
    - 同级冲突（同 kind+topic 但文本不一致）双方保留且进 conflicts 可见；
    - 来源内指令是数据：条目照常核对，不因措辞像指令而被执行。
    返回 {"entries", "conflicts", "dropped"}；task_context 只读，不修改。
    """
    texts = {str(key): str(value or "") for key, value in (source_texts or {}).items()}
    scope = ((task_context or {}).get("payload") or {}).get("scope") or {}
    exclude_terms = [str(x) for x in (scope.get("exclude") or []) if str(x).strip()]
    raw: Any = outline_data.get("reference_semantics") if isinstance(outline_data, dict) else None
    if isinstance(raw, dict):
        raw = raw.get("entries")
    raw_items = [item for item in (raw or []) if isinstance(item, dict)]
    entries: list[dict] = []
    dropped: list[dict] = []
    conflicts: list[str] = []
    by_topic: dict[tuple[str, str], list[str]] = {}
    for item in raw_items:
        kind = _norm(item.get("kind")) or "knowledge_point"
        text = _norm(item.get("text") or item.get("statement") or item.get("topic"))
        if not text:
            dropped.append({"text": _norm(str(item))[:80], "reason": "缺 text/topic，无法核对"})
            continue
        if kind in _SEMANTIC_FACT_KINDS:
            dropped.append({"text": text[:80],
                            "reason": "确定性事实（题量/分值）由解析器负责，不接受模型断言"})
            continue
        source_ref = _norm(item.get("source_ref"))
        span = item.get("span") if isinstance(item.get("span"), dict) else {}
        quote = re.sub(r"\s+", "", str(span.get("quote") or item.get("quote") or ""))
        body = texts.get(source_ref, "")
        if source_ref not in texts:
            dropped.append({"text": text[:80],
                            "reason": f"source_ref 不在已上传材料内：{source_ref or '空'}"})
            continue
        if not quote:
            dropped.append({"text": text[:80], "reason": "缺 span.quote 逐字引文，无法定位核实"})
            continue
        if quote not in re.sub(r"\s+", "", body):
            dropped.append({"text": text[:80],
                            "reason": f"引文未命中材料「{source_ref}」正文，定位不可信"})
            continue
        if any(term and term in text for term in exclude_terms):
            dropped.append({"text": text[:80],
                            "reason": "与任务明确排除范围冲突（scope.exclude）"})
            continue
        topic = _norm(item.get("topic")) or text[:20]
        line_hint = span.get("line")
        entry = {
            "kind": kind, "topic": topic, "text": text,
            "source_ref": source_ref,
            "span": {"quote": str(span.get("quote") or item.get("quote") or "")[:160],
                     **({"line": line_hint} if line_hint else {})},
            "basis": "quote_match",
        }
        entries.append(entry)
        by_topic.setdefault((kind, topic), []).append(text)
    for (kind, topic), texts_list in by_topic.items():
        if len(set(texts_list)) > 1:
            conflicts.append(
                f"{kind}/{topic}：材料语义存在 {len(texts_list)} 条互不一致的表述，双方保留供规划比对。")
    if len(entries) > _SEMANTIC_ENTRY_LIMIT:
        overflow = len(entries) - _SEMANTIC_ENTRY_LIMIT
        entries = entries[:_SEMANTIC_ENTRY_LIMIT]
        dropped.append({"text": f"…共 {overflow} 条",
                        "reason": f"超出语义条目上限 {_SEMANTIC_ENTRY_LIMIT}，仅保留前 {_SEMANTIC_ENTRY_LIMIT} 条"})
    return {"entries": entries, "conflicts": conflicts, "dropped": dropped}


def merge_reference_semantics(task_context: dict, semantics: dict) -> dict:
    """把 adapter 输出合并回 TaskContext.extensions.reference_semantics（返回新消息）。

    payload 不变；合并后重过 generation.v1 校验。旧快照没有语义字段时 semantics 可为空。
    """
    extensions = dict(task_context.get("extensions") or {})
    extensions["reference_semantics"] = semantics
    merged = {**task_context, "extensions": extensions}
    validate_generation_message(merged, expected_kind="TaskContext")
    return merged


def decide_execution_policy(
    task_type: str, goal: LearningGoal | None = None, snapshot: dict | None = None,
) -> dict:
    """TaskContext.extensions.execution_policy（契约 §2）。

    v1 只记录交付模式与依据，**不改变调用生命周期**：
    - delivery_mode：exam/exercise → artifact（交付物本身就是本次目标，无学习过程消费者）；
      其余任务类型 → learning（诊断/计划/掌握度有真实消费者）。
    - knowledge_extract_timing 恒为 outline_and_final（现状不变；c-orchestration 契约 §5
      的消费点按该枚举执行）。按模式收窄时机属 02-5 激活范围：先离线端到端消费者测试
      与成本对照，激活时单独追加 ADR。
    - skip_learning_stages 恒 false：可回退开关，未激活。
    """
    delivery_mode = "artifact" if task_type in {"exam", "exercise"} else "learning"
    if delivery_mode == "artifact":
        mode_basis = (f"task_type={task_type} → artifact：交付物即本次目标，"
                      "无学习诊断/计划消费者。")
    else:
        mode_basis = f"task_type={task_type} → learning：学习过程有诊断/计划/掌握度消费者。"
    return {
        "policy_version": "execution-policy.v1",
        "delivery_mode": delivery_mode,
        "mode_basis": mode_basis,
        "knowledge_extract_timing": "outline_and_final",
        "skip_learning_stages": False,
        "reason": "v1 只记录模式与依据，不改变调用生命周期；跳过无消费者的学习阶段"
                  "需先过离线端到端消费者测试与成本对照，激活时单独追加 ADR。",
    }


# ---------------------------------------------------------------- criteria 角色分类

# learner_outcome / delivery_requirement 的确定性分类（02-5）。
# 显式 criterion_type（模型输出字段）优先；既有数据没有该字段时按文本推断，
# 两者都缺时按任务类型默认：artifact → 交付规格，learning → 学习结果。
_CRITERION_LEARNER_RE = re.compile(
    r"能够|独立完成|变式|迁移|解释|说明|诊断|运用|评估自己的")
_CRITERION_DELIVERY_RE = re.compile(
    r"试卷|卷面|题号|题目数量|答案|作答区|分值|满分|每套|套卷|排版|栏目|交付|一一对应|不附|不出现|集中在")


def classify_criterion_role(criterion: dict, task_type: str = "") -> str:
    """单条 criterion 的角色：learner_outcome | delivery_requirement（纯函数）。

    出卷检查计划来自交付规格与用户要求（delivery_requirement），学习结果类项
    保留为参考（learner_outcome）；分类落在消费点做，不要求改既有表结构。
    """
    explicit = str((criterion or {}).get("criterion_type") or "").strip()
    if explicit in {"learner_outcome", "delivery_requirement"}:
        return explicit
    text = " ".join(str((criterion or {}).get(field, "") or "")
                    for field in ("statement", "conditions", "pass_rule"))
    if _CRITERION_DELIVERY_RE.search(text):
        return "delivery_requirement"
    if _CRITERION_LEARNER_RE.search(text):
        return "learner_outcome"
    return "delivery_requirement" if task_type in {"exam", "exercise"} else "learner_outcome"


def classify_criteria_roles(criteria: list[dict], task_type: str = "") -> dict[str, str]:
    """批量分类：{criterion_id: role}；供验收计划/消费点兼容存储语义使用。"""
    return {
        str(criterion.get("id")): classify_criterion_role(criterion, task_type)
        for criterion in (criteria or []) if isinstance(criterion, dict) and criterion.get("id")
    }


def build_task_context(
    db: Session, goal: LearningGoal, *, tutorial_id: str | None = None,
    version: int | None = None, outline: list[dict] | None = None,
    criteria_count: int = 0, snapshot: dict | None = None,
) -> dict:
    """构造 generation.v1 TaskContext 线格式消息；确定性、零模型调用。

    outline 传入时（大纲已定）交付物即大纲单元；未传时从规范交付清单推导
    （供目标详情页展示系统理解，先于大纲存在）。
    """
    snap = snapshot if snapshot is not None else _safe_snapshot(goal)
    materials = _parse_materials(db, goal.id)
    profile = _profile_from_parsed(materials)
    reference_documents = _reference_documents_extension(materials)
    # 显式规范（manual/explicit）锁定任务类型；否则按用户原话归一，再退回场景。
    source = _norm(getattr(goal, "format_spec_source", ""))
    if source in {"manual", "explicit"} and _norm(snap.get("scene")):
        task_type = _TASK_TYPE_ALIASES.get(_norm(snap.get("scene")), "knowledge_tutorial")
        task_source = "explicit_spec"
    else:
        task_type = classify_task_type(goal, snap)
        task_source = "user_text" if task_type != _TASK_TYPE_ALIASES.get(_norm(snap.get("scene")), "") else "spec_scene"
    difficulty = resolve_difficulty(goal, profile)
    constraints, conflicts, assumptions, profile_rules = collect_constraints(goal)
    scope = _scope(goal, profile)
    deliverables = _deliverables(outline, snap)
    if not deliverables:
        assumptions.append("尚无交付物清单：交付物在大纲确定后回填。")
    if difficulty["mode"] == "above_reference" and difficulty.get("source_refs"):
        assumptions.append(
            "默认整体难度高于参考来源（用户未显式指定难度）；拔高通过条件结构、知识联系与综合度实现，不逐题机械加难。")
    if profile and profile.get("anomalies"):
        assumptions.extend(f"参考来源疑点：{item}" for item in profile["anomalies"][:6])
    assumptions.append("任务理解为系统判定结果，无需用户填写协议字段；冲突与默认值在界面可见。")
    scheduling = scheduling_decisions(
        goal, task_type=task_type, task_source=task_source, snapshot=snap,
        profile=profile, difficulty=difficulty, criteria_count=criteria_count,
        profile_rules=profile_rules)
    extensions: dict = {
        "adapter": "task-understanding.v1",
        "scheduling": scheduling,
        "clarifications": clarifications(conflicts),
        "question_types": [str(form["name"]) for form in (profile or {}).get("question_types", [])],
        "knowledge_points": list((profile or {}).get("knowledge_points", [])),
        "execution_policy": decide_execution_policy(task_type, goal, snap),
    }
    if reference_documents is not None:
        extensions["reference_documents"] = reference_documents
    message = {
        "contract_version": "generation.v1",
        "kind": "TaskContext",
        "identity": {"task_id": str(goal.id), "goal_id": goal.id,
                     "tutorial_id": tutorial_id or None, "version": version if version and version >= 1 else None,
                     "unit_id": None},
        "payload": {
            "task_type": task_type,
            "domain_key": canonical_domain_key(getattr(goal, "domain_key", "") or "general"),
            "branch": _norm(getattr(goal, "branch", "")) or None,
            "learner": _norm(getattr(goal, "current_level", "")) or None,
            "objective": _norm(getattr(goal, "application_context", "")) or _norm(getattr(goal, "motivation", "")) or _norm(goal.title),
            "constraints": constraints,
            "scope": scope,
            "reference_profile": profile,
            "difficulty_target": difficulty,
            "deliverables": deliverables,
            "assumptions": list(dict.fromkeys(assumptions)),
            "conflicts": conflicts,
        },
        "extensions": extensions,
    }
    validate_generation_message(message, expected_kind="TaskContext")
    return message


def _safe_snapshot(goal: LearningGoal) -> dict:
    from .utils import jload
    value = jload(getattr(goal, "format_spec_snapshot", "") or "", {})
    return value if isinstance(value, dict) else {}


def task_understanding_summary(message: dict) -> dict:
    """界面展示用摘要：系统理解、默认与冲突；不暴露向量/协议细节。"""
    payload = message.get("payload", {})
    scheduling = (message.get("extensions", {}) or {}).get("scheduling", {})
    profile = payload.get("reference_profile") or {}
    difficulty = payload.get("difficulty_target") or {}
    return {
        "task_type": payload.get("task_type", ""),
        "objective": payload.get("objective", ""),
        "scope": payload.get("scope", {}),
        "constraints": [
            {"id": c.get("id"), "text": c.get("text"), "strength": c.get("strength"), "origin": c.get("origin")}
            for c in payload.get("constraints", [])
        ],
        "difficulty": {
            "mode": difficulty.get("mode"),
            "instruction": difficulty.get("instruction"),
            "basis": difficulty.get("evidence_basis"),
        },
        "reference": {
            "source_count": len(profile.get("source_refs", []) or []),
            "question_count": profile.get("question_count"),
            "question_types": [t.get("name") for t in profile.get("question_types", [])],
            "anomalies": profile.get("anomalies", []),
        },
        "assumptions": payload.get("assumptions", []),
        "conflicts": payload.get("conflicts", []),
        "clarifications": (message.get("extensions", {}) or {}).get("clarifications", []),
        "scheduling": scheduling,
        "deliverables": payload.get("deliverables", []),
        # 执行策略是可展示的调度决策（artifact/learning 与依据），不含协议内部字段
        "execution_policy": (message.get("extensions", {}) or {}).get("execution_policy", {}),
    }


def load_persisted_task_context(content: dict | None) -> dict | None:
    """读取版本快照里的 TaskContext；旧快照没有该字段时返回 None（走旧适配器）。"""
    if not isinstance(content, dict):
        return None
    candidate = content.get("task_context")
    if not isinstance(candidate, dict):
        return None
    try:
        validate_generation_message(candidate, expected_kind="TaskContext")
    except Exception:  # noqa: BLE001  旧数据损坏时不阻断生成，退回旧适配器
        return None
    return candidate


__all__ = [
    "build_reference_profile", "build_task_context", "classify_criteria_roles",
    "classify_criterion_role", "classify_task_type",
    "clarifications", "collect_constraints", "decide_execution_policy",
    "load_persisted_task_context", "merge_reference_semantics",
    "reference_semantics_adapter", "reference_source_texts",
    "resolve_difficulty", "scheduling_decisions", "task_understanding_summary",
]
