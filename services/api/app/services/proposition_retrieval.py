"""Task-scoped proposition guidance, shared by previews and generation.

Retrieval never changes a principle or merges similar evidence. Network work is
finished before cache/trace writes; the caller owns the transaction (no commit).
``record=False`` performs no writes, including embedding-cache writes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from urllib.parse import urlsplit
from typing import Any

import httpx
from sqlalchemy import or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import models
from ..learning.disciplines import canonical_domain_key, domain_keys_for


_DEFAULTS = {
    "budget_chars": 3000, "complex_budget_chars": 6000,
    "max_experiences": 6, "include_examples": True,
    "embedding_enabled": False, "embedding_timeout": 15,
    "embedding_candidate_limit": 24, "embedding_threshold": 0.72,
    "auto_accept": True, "auto_accept_confidence": 0.8, "auto_accept_min_families": 2,
    "embedding_cache_limit": 512,
}

# ---------------------------------------------------------------- 题型形态归一（03-2）
#
# 现状：scope.question_types 是带括号长说明的自然语言，作用域过滤前无法解释
# 「填空/证明/解答」这类作答形态与「条件变化/结构迁移」这类考查机制是否同一维度。
# 规则（有版本代码表；用户 Concept 别名继续经由 raw 相似度生效）：
# - 作答形态 canonical：填空/选择/判断/计算/证明/简答/论述/程序/解答；
#   「解答题」保留宽类 solution_wide，**不等同**「证明题 proof」——宽类任务命中
#   证明范围的经验必须有显式解释，不得自动升级匹配；
# - 考查机制单独抽取（mechanisms），进入 mechanism/knowledge_requests，不把整段
#   practice_focus 当标签；
# - 未命中任何形态词 → canonical=None + unknown=True（保持待核实，不猜）。
QUESTION_FORM_MAP_VERSION = "question-form.v1"
# 表序即优先级：一道题同时提到「判断+证明」时按证明处理（表内靠前者优先），
# 单独出现时各归各的形态。宽类 solution_wide 永不向窄类升级。
_FORM_TERMS: tuple[tuple[str, str], ...] = (
    ("证明", "proof"), ("解答", "solution_wide"), ("计算", "computation"),
    ("填空", "fill_in_blank"), ("选择题", "multiple_choice"), ("选择", "multiple_choice"),
    ("简答", "short_answer"), ("论述", "essay"),
    ("程序设计", "programming"), ("编程", "programming"), ("程序", "programming"),
    ("上机", "programming"), ("判断", "true_false"), ("综合应用", "solution_wide"),
)
_MECHANISM_TERMS: tuple[tuple[str, str], ...] = (
    ("条件变化", "condition_change"), ("条件隐藏", "condition_hidden"),
    ("结构迁移", "structure_transfer"), ("变式", "variation"),
    ("反例", "counterexample"), ("推广", "generalization"),
    ("逆向", "inverse_problem"), ("开放", "open_ended"),
)


def normalize_question_form(text: str, domain: str = "") -> dict:
    """纯函数：把自然语言题型描述归一为 canonical 作答形态 + 独立机制清单。

    返回 {"canonical": str|None, "raw": str, "basis": "form_map_v1"|"none",
          "unknown": bool, "mechanisms": [canonical…], "map_version": str, "domain": str}。
    只归一，不判定适用性；宽类（solution_wide）不向窄类（proof/computation）升级。
    """
    raw = str(text or "")
    domain_key = canonical_domain_key(str(domain or ""))
    normalized = re.sub(r"\s+", "", raw)
    forms: list[str] = []
    for term, canonical in _FORM_TERMS:
        if term in normalized and canonical not in forms:
            forms.append(canonical)
    mechanisms = [canonical for term, canonical in _MECHANISM_TERMS if term in normalized]
    if forms:
        # 多形态并存（如「填空与证明」）时按出现顺序保留首个为 canonical，其余记进 unknown=False 附带列表
        return {"canonical": forms[0], "raw": raw, "basis": "form_map_v1", "unknown": False,
                "forms": forms, "mechanisms": mechanisms,
                "map_version": QUESTION_FORM_MAP_VERSION, "domain": domain_key}
    return {"canonical": None, "raw": raw, "basis": "none", "unknown": True,
            "forms": [], "mechanisms": mechanisms,
            "map_version": QUESTION_FORM_MAP_VERSION, "domain": domain_key}


def _normalized_form_pair(expected: list[str], actual: list[str]) -> dict:
    """归一两侧 question_types，输出字段级解释（03-3：expected/actual/normalized）。"""
    expected_norm = [normalize_question_form(value) for value in expected]
    actual_norm = [normalize_question_form(value) for value in actual]
    expected_canonical = [item["canonical"] for item in expected_norm if item["canonical"]]
    actual_canonical = [item["canonical"] for item in actual_norm if item["canonical"]]
    unknown_expected = any(item["unknown"] for item in expected_norm)
    unknown_actual = any(item["unknown"] for item in actual_norm)
    match = bool(expected_canonical and actual_canonical
                 and set(expected_canonical) & set(actual_canonical))
    return {
        "expected": expected_norm, "actual": actual_norm,
        "expected_canonical": expected_canonical, "actual_canonical": actual_canonical,
        "unknown_expected": unknown_expected, "unknown_actual": unknown_actual,
        "match": match,
    }
_ALIASES = (
    ("导数", "求导", "derivative", "differentiation", "differentiate"),
    ("微积分", "calculus"), ("定积分", "definite integral"),
    ("不定积分", "indefinite integral", "antiderivative"),
    ("极限", "limit", "limits"), ("连续性", "continuity", "continuous"),
    ("泰勒展开", "泰勒级数", "taylor expansion", "taylor series"),
    ("链式法则", "链式求导", "chain rule"),
    ("列表推导式", "列表推导", "list comprehension"),
    ("字典", "dictionary", "dict"), ("迭代器", "iterator"),
    ("生成器", "generator"), ("递归", "recursion", "recursive"),
    ("异常处理", "exception handling"), ("作用域", "scope"),
    ("选择题", "multiple choice", "mcq"),
    ("证明题", "proof", "proof question"),
    ("计算题", "calculation", "computational"),
    ("编程题", "coding", "programming"),
    ("本科生", "大学生", "undergraduate", "college student", "university student"),
    ("初学者", "入门", "零基础", "beginner", "novice"),
    ("进阶", "advanced", "有经验", "experienced"),
    ("高中生", "高中", "high school student"),
)
# R2-1 线代同义组：与 _ALIASES 分开维护并在 _aliases() 中合并（保持 learner 判定
# 与旧表的索引假设完全解耦；下表只登记已证明同义，相抵/相似/合同/等价互不同组）。
_LINALG_ALIASES = (
    ("秩", "矩阵的秩", "矩阵秩"),
    ("子矩阵秩下界", "子矩阵的秩的下界", "子矩阵的秩下界"),
    ("满秩分解", "满秩分解与秩1外积和"),
    ("伴随矩阵", "伴随阵", "A的伴随矩阵"),
)
LINALG_ALIAS_VERSION = "linear-algebra-aliases.v1"
# 学习者/阶段别名组独立常量：作用域 learner 判定必须引用本表，不能用 _ALIASES[-4:]
# 这类位置假设（R2-1 追加线代组时该假设曾把 learner 判定悄悄改坏）。
_LEARNER_ALIASES = (
    ("本科生", "大学生", "undergraduate", "college student", "university student"),
    ("初学者", "入门", "零基础", "beginner", "novice"),
    ("进阶", "advanced", "有经验", "experienced"),
    ("高中生", "高中", "high school student"),
)
# 作答形态别名组独立常量：legacy 适配从任务文本识别题型时引用本表，
# 同理不能用 _ALIASES[-8:-4] 位置切片（追加组会错位，把题型识别静默关掉）。
_FORM_TYPE_ALIASES = (
    ("选择题", "multiple choice", "mcq"),
    ("证明题", "proof", "proof question"),
    ("计算题", "calculation", "computational"),
    ("编程题", "coding", "programming"),
)
# 线代别名表版本（R2-1 真实线代夹具按版本断言；表变更时递增）。
LINALG_ALIAS_VERSION = "linear-algebra-aliases.v1"
# 合并进作用域/检索别名表（learner 判定已改用 _LEARNER_ALIASES 常量，不再依赖位置假设）。
_ALIASES = _ALIASES + _LINALG_ALIASES
_SCOPE_KEYS = {
    "domains": "domain_key", "domain_keys": "domain_key", "domain_key": "domain_key",
    "knowledge_points": "knowledge_points", "question_types": "question_types",
    "question_type": "question_types", "learners": "learner", "learner": "learner",
    "difficulties": "difficulty", "difficulty": "difficulty",
    "purposes": "purpose", "purpose": "purpose",
    "task_kinds": "task_kind", "task_kind": "task_kind",
    "branches": "branch", "branch": "branch", "complexity": "complexity",
}
_SCOPE_META = {"universal", "cross_domain", "requires_examples", "example_required",
               "conflicts_with", "exclude", "exclude_knowledge_points"}
_HEADINGS = (
    "【命题评价标准：hard 是硬边界；core 是核心标准；advisory 为建议。均按所列范围适用】",
    "【命题经验：软性生成指导，不能覆盖硬边界或核心标准；仅当所列条件成立才适用】",
    "【案例（仅用于理解经验）】",
)


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_ref(kind: str, source_id: str, version: Any, text: str, location: str) -> dict:
    return {"kind": kind, "id": str(source_id), "version": str(version) if version is not None else None,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(), "location": location}


def _evidence_block(source_id: str, version: Any, state: str, text: str, *,
                    mechanism: str | None = None, conditions: list[str] | None = None,
                    boundaries: list[str] | None = None, reason: str = "", refs: list[dict] | None = None,
                    method: str = "human") -> dict:
    """Only the public generation.v1 fields; diagnostics belong to extensions."""
    return {"id": str(source_id), "version": str(version or 1), "state": state, "text": text,
            "mechanism": mechanism, "conditions": conditions or [], "boundaries": boundaries or [],
            "selection_reason": reason, "source_refs": refs or [], "evidence_method": method}


def public_guidance_result(result: dict) -> dict:
    """UI projection. Keep selected evidence, omit internal prompt/error details."""
    allowed = ("text", "chars", "budget_chars", "selected", "standards", "examples", "stats",
               "warnings", "context", "trace_id", "backend", "injection_status", "decisions",
               "evidence_bundle", "layer_status")
    public = {key: result[key] for key in allowed if key in result}
    if result.get("calls"):
        public["calls"] = [{key: call.get(key) for key in ("feature", "status", "block_present")}
                           for call in result["calls"] if isinstance(call, dict)]
    return public


def _list(value: Any) -> list[str]:
    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return list(dict.fromkeys(str(v).strip() for v in value if str(v).strip()))


def _number(value: Any, default: float) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int, maximum: int = 100000) -> int:
    return max(0, min(maximum, int(_number(value, default))))


def _norm(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value)


def _grams(value: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", value.casefold())
    return {g for part in parts for g in (
        [part] if part.isascii() else [part[i:i + 2] for i in range(len(part) - 1)]
    ) if len(g) >= 2}


def _aliases(db: Session, context: dict, config: dict) -> list[tuple[str, ...]]:
    groups = [tuple(_norm(v) for v in group) for group in _ALIASES]
    for title, aliases in (config.get("knowledge_aliases") or {}).items():
        groups.append(tuple(_norm(v) for v in [title, *_list(aliases)] if _norm(v)))
    # Existing user-maintained concept aliases are valid only in this domain.
    for concept in db.query(models.Concept).filter(models.Concept.status == "active").all():
        domains = _list(_json(concept.domain_keys, [])) or [concept.domain_key]
        if context["domain_key"] in [canonical_domain_key(value) for value in domains]:
            groups.append(tuple(_norm(v) for v in [concept.title, *_json(concept.aliases, [])] if _norm(v)))
    return groups


def _similar(left: str, right: str, aliases: list[tuple[str, ...]]) -> float:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    if a == b or any(a in group and b in group for group in aliases):
        return 1.0
    # Whole keyword containment supports '计算导数' and 'Python list comprehension'.
    # Do not equate two longer concepts merely because both contain a short alias.
    if min(len(a), len(b)) >= 2 and (a in b or b in a):
        return 0.9
    for group in aliases:
        if a in group and any(len(term) >= 2 and term in b for term in group):
            return 0.85
        if b in group and any(len(term) >= 2 and term in a for term in group):
            return 0.85
    x, y = _grams(left), _grams(right)
    return len(x & y) / max(1, len(x | y)) if x and y else 0.0


def _overlap(expected: Any, actual: Any, aliases: list[tuple[str, ...]]) -> float:
    return max((_similar(x, y, aliases) for x in _list(expected) for y in _list(actual)), default=0.0)


def _scope(value: Any, legacy_points: Any = None) -> tuple[dict, bool]:
    # Malformed conditional metadata must not silently become an unrestricted rule.
    parsed = _json(value, {})
    invalid = value not in (None, "", "{}", {}) and not parsed
    result = dict(parsed)
    if legacy := _list(_json(legacy_points, [])):
        result.setdefault("knowledge_points", legacy)
    return result, invalid


def _scope_match(scope: dict, context: dict, aliases: list[tuple[str, ...]], *,
                 allow_semantic_points: bool = False) -> tuple[bool, float, list[str], dict]:
    """作用域匹配：返回 (allowed, score, reasons, evidence)。

    evidence（03-3）记录每个被评估字段的 expected/actual/normalized 与字段级原因；
    question_types 经 normalize_question_form 归一后按形态判定：
    - 同形态命中（含宽类自身）→ matched；
    - 任一侧 unknown → 待核实（条件性候选，不排除也不计分）；
    - 已知形态不相交 → 排除，并解释「宽类解答题不自动等同证明题」类差异。
    0.45 词法阈值与状态/领域边界保持原样，不为命中率降低门槛。
    """
    reasons: list[str] = []
    scores: list[float] = []
    field_evidence: dict[str, dict] = {}
    weights = {"knowledge_points": 1.0, "question_types": 0.5, "purpose": 0.4,
               "branch": 0.3, "task_kind": 0.2, "learner": 0.15, "difficulty": 0.15,
               "complexity": 0.1, "domain_key": 0.0}
    for key, expected in scope.items():
        if key in _SCOPE_META:
            continue
        field = _SCOPE_KEYS.get(key)
        if field is None:
            return False, 0.0, [f"unsupported_scope:{key}"], {
                "unsupported_scope": {"field": key}}
        if not _list(expected):
            continue
        actual = context.get(field)
        entry = {"expected": _list(expected), "actual": _list(actual)}
        if not _list(actual):
            if field in {"learner", "branch"}:
                reasons.append(f"{field}:unverified_context;仅当所列条件成立才适用")
                field_evidence[field] = {**entry, "verdict": "unverified_context"}
                continue
            return False, 0.0, [f"missing_context:{field}"], {
                field: {**entry, "verdict": "missing_context"}}
        score = _overlap(expected, actual, aliases)
        # A domain is a routing boundary, not a fuzzy keyword.
        if field == "domain_key":
            score = float(any(canonical_domain_key(v) == canonical_domain_key(actual) for v in _list(expected)))
        if field == "question_types":
            pair = _normalized_form_pair(_list(expected), _list(actual))
            entry["normalized"] = {"expected": pair["expected_canonical"],
                                   "actual": pair["actual_canonical"]}
            if pair["match"]:
                score = max(score, 0.9)
                reasons.append("question_types:form_matched:" + ",".join(
                    sorted(set(pair["expected_canonical"]) & set(pair["actual_canonical"]))))
                field_evidence[field] = {**entry, "verdict": "matched"}
            elif (pair["expected_canonical"] or pair["actual_canonical"]) and (
                    pair["unknown_expected"] or pair["unknown_actual"]):
                # 一侧可归一、另一侧 unknown：保持待核实（条件性候选），不猜同义、不硬排除
                reasons.append("question_types:unknown_form;待核实（归一表未命中作答形态）")
                field_evidence[field] = {**entry, "verdict": "unknown_form"}
                continue
            elif pair["expected_canonical"] and pair["actual_canonical"]:
                return False, 0.0, [
                    "scope_mismatch:question_types:expected=" + ",".join(pair["expected_canonical"])
                    + ";actual=" + ",".join(pair["actual_canonical"])
                    + ";作答形态归一后不相交（宽类解答题不自动等同证明题/计算题）",
                ], {field: {**entry, "verdict": "scope_mismatch"}}
            # 双侧都归一不出形态：回退词法别名匹配（用户 Concept 别名继续生效）
        if score < 0.45:
            if field == "learner":
                # Production current_level is free text, often not a cohort label.
                # Retain the learner condition explicitly instead of treating an
                # unrecognised description as proof that the cohort mismatches.
                cohort_groups = _LEARNER_ALIASES
                wanted_groups = {i for i, group in enumerate(cohort_groups)
                                 if any(_norm(v) in {_norm(t) for t in group} for v in _list(expected))}
                actual_groups = {i for i, group in enumerate(cohort_groups)
                                 if any(_norm(t) in _norm(v) for t in group for v in _list(actual))}
                if not wanted_groups or not actual_groups or wanted_groups & actual_groups:
                    reasons.append("learner:unverified_context;仅当所列条件成立才适用")
                    field_evidence[field] = {**entry, "verdict": "unverified_context"}
                    continue
            if field == "knowledge_points" and allow_semantic_points:
                reasons.append("knowledge_points:pending_embedding")
                field_evidence[field] = {**entry, "verdict": "pending_embedding"}
                continue
            return False, 0.0, [f"scope_mismatch:{field}"], {
                field: {**entry, "verdict": "scope_mismatch"}}
        scores.append(score * weights[field])
        reasons.append(f"{field}:matched")
        field_evidence[field] = {**entry, "verdict": "matched"}
    exclusions = _json(scope.get("exclude"), {})
    if scope.get("exclude") and not exclusions:
        return False, 0.0, ["invalid_exclude_scope"], {"exclude": {"verdict": "invalid_exclude_scope"}}
    if "exclude_knowledge_points" in scope:
        exclusions["knowledge_points"] = scope["exclude_knowledge_points"]
    for key, values in exclusions.items():
        field = _SCOPE_KEYS.get(key)
        if field is None:
            return False, 0.0, [f"unsupported_exclusion:{key}"], {
                "unsupported_exclusion": {"field": key}}
        if _overlap(values, context.get(field), aliases) >= 0.45:
            return False, 0.0, [f"excluded_context:{field}"], {
                field: {"expected": _list(values), "actual": _list(context.get(field)),
                        "verdict": "excluded_context"}}
    return True, min(1.0, sum(scores)), reasons, {
        key: value for key, value in field_evidence.items()}


def _domain_allowed(domain: str, context: dict, scope: dict, *, general: bool) -> bool:
    if canonical_domain_key(domain) == canonical_domain_key(context["domain_key"]):
        return True
    if not general:
        return False
    # A missing cohort/branch can be kept as a conditional suggestion within a
    # discipline, but cannot justify crossing a domain boundary.
    if any(_list(value) and not _list(context.get(_SCOPE_KEYS[key]))
           for key, value in scope.items() if key in _SCOPE_KEYS):
        return False
    # Cross-domain use needs an explicit general classification and matching scope.
    return domain == "general" or scope.get("universal") is True or scope.get("cross_domain") is True


def _auto_allowed(row: Any, config: dict) -> bool:
    return (
        config.get("auto_accept", True)
        and _number(row.confidence, 0) >= max(0.8, _number(config.get("auto_accept_confidence"), 0.8))
        and _integer(row.evidence_family_count, 0) >= max(2, _integer(config.get("auto_accept_min_families"), 2))
        and bool(str(row.mechanism or "").strip())
        and bool(str(row.applicability or "").strip())
        and not _json(row.negative_refs, [])
        and row.approval_source in {"auto", "automatic", "auto_accept", "model", "auto_accepted"}
    )


def _render_scope(scope: dict) -> str:
    return "；".join(f"{key}={','.join(_list(value))}" for key, value in scope.items()
                    if key in _SCOPE_KEYS and _list(value))


def _principle_block(row: Any, scope: dict) -> str:
    status = "人工确认 confirmed" if row.status == "confirmed" else "自动采纳 auto_accepted"
    parts = [f"- {row.title or '命题经验'} [经验:{row.id};版本:{row.version or 1};来源状态:{status}]"]
    for label, text in (("经验", row.statement), ("机制", row.mechanism),
                        ("适用条件", row.applicability), ("生成指导", row.guidance)):
        if str(text or "").strip():
            parts.append(f"  {label}：{str(text).strip()}")
    if conditions := _render_scope(scope):
        parts.append(f"  范围：{conditions}")
    counterexamples = _json(row.counterexamples, [])
    if counterexamples:
        parts.append("  限制/反例（不得当正向做法）：" + _dump(counterexamples))
    elif str(row.counterexamples or "").strip() not in ("", "[]"):
        parts.append(f"  限制/反例（不得当正向做法）：{row.counterexamples}")
    return "\n".join(parts)


def _standard_block(standard: dict) -> str:
    parts = [f"- {standard.get('name') or standard['id']} [标准:{standard['id']};"
             f"框架:{standard['framework_id']};版本:{standard['framework_version']}]"]
    parts.append(f"  标准：{standard['rule']}")
    if scope := _render_scope(standard["scope"]):
        parts.append(f"  范围：{scope}")
    for key, label in (("strength", "强度"), ("evidence_required", "证据要求"), ("source_ref", "来源")):
        if value := standard.get(key):
            parts.append(f"  {label}：{value if isinstance(value, str) else _dump(value)}")
    return "\n".join(parts)


def _vector(value: Any) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    vector = [float(v) for v in value]
    return vector if any(vector) else None


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding_dimension_mismatch")
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    if not left_norm or not right_norm or not math.isfinite(left_norm) or not math.isfinite(right_norm):
        raise ValueError("invalid_embedding_norm")
    return max(-1.0, min(1.0, sum((a / left_norm) * (b / right_norm) for a, b in zip(left, right))))


def _embedding_request(url: str, texts: list[str], config: dict) -> list[list[float]]:
    headers = {"Content-Type": "application/json"}
    if config.get("embedding_api_key"):
        headers["Authorization"] = f"Bearer {config['embedding_api_key']}"
    response = httpx.post(
        url, json={"model": config["embedding_model"], "input": texts}, headers=headers,
        timeout=max(0.1, min(60, _number(config.get("embedding_timeout"), 15))),
    )
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise ValueError("invalid_embedding_response")
    indexed = {item.get("index"): _vector(item.get("embedding")) for item in data if isinstance(item, dict)}
    if set(indexed) != set(range(len(texts))) or any(v is None for v in indexed.values()):
        raise ValueError("invalid_embedding_vector")
    return [indexed[i] for i in range(len(texts))]


def _probe_host(url: str) -> str:
    """只提取 host:port；绝不回显路径、查询参数或凭据。"""
    try:
        parts = urlsplit(url)
        return f"{parts.hostname}:{parts.port}" if parts.port else (parts.hostname or "")
    except ValueError:
        return ""


EMBEDDING_PROBE_VERSION = "embedding-probe.v1"
_EMBEDDING_PROBE_TEXT = "embedding health probe"  # 固定探针文本，不含任何用户内容


def embedding_health(config: dict | None) -> dict:
    """只读 embedding 健康探针：检查 endpoint/model 响应与可用性（R2-1）。

    约束：**绝不自动启动 Ollama**（只探测现有 endpoint，失败如实报告）；
    不写库、不调模型、不记录输入文本；返回结果不含 API key。
    输出可解释：reachable / model_ok / dimension / latency_ms / reason /
    degraded_explanation（为何本轮语义检索会降级为词法）。
    """
    cfg = dict(config or {})
    base = str(cfg.get("embedding_base_url") or "").strip().rstrip("/")
    model = str(cfg.get("embedding_model") or "").strip()
    enabled = bool(cfg.get("embedding_enabled"))
    result: dict[str, Any] = {
        "probe_version": EMBEDDING_PROBE_VERSION,
        "configured": bool(base and model),
        "enabled": enabled,
        "reachable": None,
        "model_ok": None,
        "dimension": None,
        "latency_ms": None,
        "reason": "",
        "degraded_explanation": "",
        "endpoint_host": _probe_host(base) if base else "",
        "model": model or None,
        "will_use_lexical": True,
    }
    if not enabled:
        result["reason"] = "disabled"
        result["degraded_explanation"] = "embedding_enabled=false：本轮检索使用词法检索（无语义匹配）"
        return result
    if not (base and model):
        result["reason"] = "not_configured"
        result["degraded_explanation"] = "embedding 未配置终结点或模型：本轮检索使用词法检索；不会自动启动 Ollama"
        return result
    url = base if base.endswith("/embeddings") else base + "/embeddings"
    headers = {"Content-Type": "application/json"}
    if cfg.get("embedding_api_key"):
        headers["Authorization"] = f"Bearer {cfg['embedding_api_key']}"
    timeout = max(0.1, min(60.0, _number(cfg.get("embedding_timeout"), 15)))
    started = time.perf_counter()
    try:
        response = httpx.post(
            url, json={"model": model, "input": [_EMBEDDING_PROBE_TEXT]},
            headers=headers, timeout=timeout,
        )
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            result.update({
                "reachable": True, "model_ok": False,
                "reason": f"http_{response.status_code}",
                "degraded_explanation": (
                    f"embedding endpoint {_probe_host(url)} 返回 HTTP {response.status_code}"
                    f"（模型 {model}）：语义检索不可用，将降级为词法检索；不会自动启动 Ollama"),
            })
            return result
        data = response.json().get("data")
        vector = None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            vector = _vector(data[0].get("embedding"))
        if vector is None:
            result.update({
                "reachable": True, "model_ok": False, "reason": "invalid_response",
                "degraded_explanation": (
                    f"embedding endpoint {_probe_host(url)} 响应不含有效向量"
                    f"（模型 {model}）：语义检索将降级为词法检索"),
            })
            return result
        result.update({"reachable": True, "model_ok": True, "dimension": len(vector),
                       "reason": "ok", "will_use_lexical": False})
        return result
    except httpx.TimeoutException:
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        result.update({
            "reachable": False, "reason": "timeout",
            "degraded_explanation": (
                f"embedding endpoint {_probe_host(url)} 探测超时"
                f"（{timeout:g}s，模型 {model}）：语义检索将降级为词法检索；不会自动启动 Ollama"),
        })
        return result
    except httpx.HTTPError as exc:
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        result.update({
            "reachable": False, "reason": f"connection_error:{type(exc).__name__}",
            "degraded_explanation": (
                f"embedding endpoint {_probe_host(url)} 不可达"
                f"（{type(exc).__name__}，模型 {model}）：语义检索将降级为词法检索；不会自动启动 Ollama"),
        })
        return result
    except (ValueError, TypeError) as exc:
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        result.update({
            "reachable": True, "model_ok": False,
            "reason": f"invalid_response:{type(exc).__name__}",
            "degraded_explanation": (
                f"embedding endpoint {_probe_host(url)} 响应异常"
                f"（{type(exc).__name__}，模型 {model}）：语义检索将降级为词法检索"),
        })
        return result


def _semantic(db: Session, candidates: list[dict], context: dict, config: dict,
              warnings: list[str], stats: dict) -> tuple[str, list[dict]]:
    """At most two requests: query first, then one bounded missing-document batch."""
    if not config.get("embedding_enabled"):
        stats["embedding_reason"] = "disabled"
        return "lexical", []
    base = str(config.get("embedding_base_url") or "").strip().rstrip("/")
    model = str(config.get("embedding_model") or "").strip()
    if not base or not model:
        stats["embedding_reason"] = "not_configured"
        warnings.append("embedding_not_configured: 使用词法检索，未进行语义匹配")
        return "lexical", []
    if not candidates:
        stats["embedding_reason"] = "no_candidates"
        return "lexical", []
    # A caller may already have flushed changes. Never perform a network request
    # while that SQLite transaction holds a write lock, even with no_autoflush.
    raw_connection = db.connection().connection.driver_connection
    if getattr(raw_connection, "in_transaction", False):
        stats["embedding_reason"] = "failed:write_transaction"
        warnings.append("embedding_write_transaction: 当前写事务内使用词法检索")
        return "lexical", []
    url = base if base.endswith("/embeddings") else base + "/embeddings"
    signature = hashlib.sha256(_dump([url, model]).encode()).hexdigest()
    query_text = _dump({k: v for k, v in context.items() if k not in {"goal_id", "tutorial_id", "section_id"}})
    query_id = hashlib.sha256(query_text.encode()).hexdigest()
    limit = _integer(config.get("embedding_candidate_limit"), 24, 64)
    if limit == 0:
        stats["embedding_reason"] = "candidate_limit_zero"
        return "lexical", []
    bounded = sorted(candidates, key=lambda c: (-c["score"], -c["confidence"], -c["families"], c["row"].id))[:limit]
    pending: list[dict] = []

    def cached(source_id: str, kind: str, revision: int) -> list[float] | None:
        row = db.query(models.PropositionEmbedding).filter_by(
            source_id=source_id, source_type=kind, revision=revision, model_key=signature,
        ).populate_existing().first()
        vector = _vector(_json(row.vector, [])) if row else None
        stats["embedding_cache_hits"] += int(vector is not None)
        return vector

    def fetch(texts: list[str]) -> list[list[float]]:
        stats["embedding_requests"] += 1
        stats["embedding_inputs"] += len(texts)
        return _embedding_request(url, texts, config)

    def entry(source_id: str, kind: str, revision: int, vector: list[float]) -> dict:
        return {"source_id": source_id, "source_type": kind, "revision": revision,
                "model_key": signature, "vector": _dump(vector)}

    try:
        query = cached(query_id, "query", 1)
        if query is None:
            query = fetch([query_text])[0]
            pending.append(entry(query_id, "query", 1, query))
        missing: list[dict] = []
        vectors: dict[str, list[float]] = {}
        for candidate in bounded:
            row = candidate["row"]
            vector = cached(row.id, "principle", row.version or 1)
            if vector is None:
                missing.append(candidate)
            else:
                vectors[row.id] = vector
        if missing:
            received = fetch([_principle_block(c["row"], c["scope"]) for c in missing])
            for candidate, vector in zip(missing, received):
                row = candidate["row"]
                vectors[row.id] = vector
                pending.append(entry(row.id, "principle", row.version or 1, vector))
        # Compute every similarity before modifying scores, so failure is an honest full fallback.
        similarities = [(candidate, _cosine(query, vectors[candidate["row"].id])) for candidate in bounded]
        for candidate, similarity in similarities:
            candidate["semantic"] = similarity
            candidate["score"] = max(candidate["score"], max(0, similarity) * 0.95)
            candidate["reasons"].append(f"embedding_similarity:{similarity:.3f}")
        stats["embedding_reason"] = "matched"
        return "embedding", pending
    except httpx.HTTPStatusError as exc:
        # R2-1：降级原因必须可解释（endpoint/model/HTTP 状态），而不是只写异常类名。
        status = exc.response.status_code
        stats["embedding_reason"] = f"failed:http_{status}"
        stats["embedding_endpoint"] = _probe_host(url)
        stats["embedding_model"] = model
        warnings.append(
            f"embedding_failed:http_{status}: endpoint={_probe_host(url)} model={model}，"
            f"已降级词法检索（语义结果不可用；不会自动启动 Ollama）")
        return "lexical", []
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        # Exception messages/response bodies can expose credentials or private prompt text.
        reason = "timeout" if isinstance(exc, httpx.TimeoutException) else type(exc).__name__
        stats["embedding_reason"] = f"failed:{reason}"
        stats["embedding_endpoint"] = _probe_host(url)
        stats["embedding_model"] = model
        warnings.append(
            f"embedding_failed:{reason}: endpoint={_probe_host(url)} model={model}，"
            f"已降级词法检索（语义结果不可用）")
        return "lexical", []


def _conflicting_ids(db: Session, candidates: list[dict]) -> set[str]:
    ids = {c["row"].id for c in candidates}
    conflicts: set[str] = set()
    for candidate in candidates:
        linked = ids & set(_list(candidate["scope"].get("conflicts_with")))
        if linked:
            conflicts.update(linked | {candidate["row"].id})
    if link_model := getattr(models, "DesignPrincipleLink", None):
        links = db.query(link_model).filter(
            link_model.status == "confirmed",
            link_model.relation.in_(["conflict", "conflicts", "contradicts", "contradiction"]),
        ).all()
        for link in links:
            if link.source_id in ids and link.target_id in ids:
                conflicts.update((link.source_id, link.target_id))
    return conflicts


def _example_candidates(db: Session, selected: list[dict], context: dict,
                        aliases: list[tuple[str, ...]], stats: dict) -> list[dict]:
    examples: list[dict] = []
    seen: set[tuple[str, str]] = set()
    negative_ids = {str(ref["question_id"]) for candidate in selected
                    for ref in _json(candidate["row"].negative_refs, [])
                    if isinstance(ref, dict) and ref.get("question_id")}
    for candidate in selected:
        row, scope = candidate["row"], candidate["scope"]
        needs_example = scope.get("requires_examples") is True or scope.get("example_required") is True
        needs_example |= str(context["task_kind"]) in {"example", "examples", "case_analysis", "variation", "item_variation"}
        needs_example |= str(context["complexity"]).casefold() in {"complex", "high", "advanced", "复杂", "综合"}
        if not needs_example:
            continue
        positive = _json(row.positive_refs, [])
        negative = _json(row.negative_refs, [])
        for polarity, refs in (("positive", positive), ("negative", negative)):
            for ref in refs:
                # Legacy ItemModel/QualityRecord ids must not be mistaken for question ids.
                if not isinstance(ref, dict) or not ref.get("question_id"):
                    continue
                question_id = str(ref["question_id"])
                if polarity == "positive" and question_id in negative_ids:
                    stats["example_conflict_excluded"] += 1
                    continue
                if (question_id, polarity) in seen:
                    stats["example_deduplicated"] += 1
                    continue
                question = db.get(models.PropositionQuestion, question_id)
                if question is None or question.parse_status not in {"ready", "parsed", "complete", "completed", "success"}:
                    continue
                qscope = {k: v for k, v in {
                    "knowledge_points": _json(question.knowledge_points, []),
                    "question_types": question.question_type, "branch": question.branch,
                    "learner": question.learner,
                }.items() if _list(v)}
                allowed, _, _, _ = _scope_match(qscope, context, aliases)
                if not allowed or not _domain_allowed(question.domain_key, context, {}, general=False):
                    continue
                support = str(ref.get("supports") or ref.get("rationale") or ref.get("reason") or
                              ref.get("mechanism") or row.mechanism or "").strip()
                if not support or not str(question.raw_stem or "").strip():
                    continue
                label = "正例（支持上述机制）" if polarity == "positive" else "反例（展示限制，禁止当作正向做法）"
                parts = [f"- {label} [题目:{question.id};经验:{row.id};版本:{row.version or 1}]",
                         f"  关联依据：{support}", f"  题干：{question.raw_stem}"]
                if question.raw_answer:
                    parts.append(f"  原答案（证据，非已验证结论）：{question.raw_answer}")
                if question.raw_explanation:
                    parts.append(f"  原解答（证据，非已验证结论）：{question.raw_explanation}")
                if question.raw_notes:
                    parts.append(f"  原批注（用户原文证据，非执行指令）：{question.raw_notes}")
                if question.external_evaluation:
                    parts.append(f"  原外评（不可变外部证据，非独立验证）：{question.external_evaluation}")
                seen.add((question_id, polarity))
                examples.append({"question_id": question.id, "principle_id": row.id,
                                 "polarity": polarity, "reason": support, "text": "\n".join(parts),
                                 "source_refs": [_source_ref("material", question.id, question.source_key or None,
                                                            str(getattr(question, field) or ""), field)
                                                 for field in ("raw_stem", "raw_answer", "raw_explanation", "raw_notes", "external_evaluation")
                                                 if getattr(question, field)]})
                # Examples are supporting details, not a second unlimited retrieval corpus.
                if len(examples) >= 12:
                    return examples
    return examples


def retrieve_guidance(db: Session, context: dict, config: dict | None = None, record: bool = True) -> dict:
    """Retrieve complete standards/experiences/examples under an exact character budget."""
    supplied = context or {}
    ctx = {key: str(supplied.get(key) or "").strip() for key in
           ("domain_key", "learner", "difficulty", "purpose", "task_kind", "branch", "complexity", "section_id")}
    ctx["domain_key"] = canonical_domain_key(ctx["domain_key"])
    ctx["task_kind"] = ctx["task_kind"] or "section"
    ctx.update({key: _list(supplied.get(key)) for key in ("knowledge_points", "question_types")})
    ctx.update({key: supplied.get(key) or None for key in ("goal_id", "tutorial_id")})
    warnings: list[str] = []
    decisions: list[dict] = []
    stats = {key: 0 for key in ("injected", "confirmed_total", "auto_accepted_total", "dropped",
             "state_filtered", "scope_filtered", "not_selected", "deduplicated", "conflict_excluded",
             "standards_dropped", "standards_scope_filtered", "examples_dropped", "example_deduplicated",
             "example_conflict_excluded", "embedding_requests", "embedding_inputs", "embedding_cache_hits",
             "principles_available", "standards_available", "examples_available",
             "question_types_form_matched", "question_types_unknown_form")}
    with db.no_autoflush:
        effective = dict(_DEFAULTS)
        if config_model := getattr(models, "PropositionConfig", None):
            saved = db.get(config_model, "default")
            if saved:
                effective.update(_json(saved.settings_json, {}))
        effective.update(config or {})
        complex_task = ctx["complexity"].casefold() in {"complex", "high", "advanced", "复杂", "综合"}
        budget = _integer(effective.get("complex_budget_chars" if complex_task else "budget_chars"),
                          6000 if complex_task else 3000)
        # An explicit global zero disables even complex tasks.
        if _integer(effective.get("budget_chars"), 3000) == 0:
            budget = 0
        aliases = _aliases(db, ctx, effective)
        candidates: list[dict] = []
        rows = db.query(models.DesignPrinciple).filter(or_(
            models.DesignPrinciple.domain_key.in_(domain_keys_for(ctx["domain_key"])),
            models.DesignPrinciple.domain_key == "general",
            models.DesignPrinciple.principle_type == "general",
        )).populate_existing().all()
        embedding_ready = bool(budget and effective.get("embedding_enabled") and effective.get("embedding_base_url")
                               and effective.get("embedding_model"))
        for row in rows:
            stats["principles_available"] += 1
            stats["confirmed_total"] += int(row.status == "confirmed")
            stats["auto_accepted_total"] += int(row.status == "auto_accepted")
            if row.status != "confirmed" and not (row.status == "auto_accepted" and _auto_allowed(row, effective)):
                stats["state_filtered"] += 1
                decisions.append({"kind": "principle", "id": row.id, "version": row.version or 1,
                                  "outcome": "excluded", "reasons": ["state:" + str(row.status)]})
                continue
            scope, invalid = _scope(row.scope, row.scope_knowledge_points)
            general = row.principle_type == "general"
            if invalid or not _domain_allowed(row.domain_key, ctx, scope, general=general):
                stats["scope_filtered"] += 1
                decisions.append({"kind": "principle", "id": row.id, "version": row.version or 1,
                                  "outcome": "excluded", "reasons": ["invalid_scope" if invalid else "domain_mismatch"]})
                continue
            allowed, score, reasons, evidence = _scope_match(scope, ctx, aliases, allow_semantic_points=embedding_ready)
            stats["question_types_form_matched"] += sum(
                1 for value in evidence.values()
                if isinstance(value, dict) and value.get("verdict") == "matched"
                and value.get("normalized"))
            stats["question_types_unknown_form"] += sum(
                1 for value in evidence.values()
                if isinstance(value, dict) and value.get("verdict") == "unknown_form")
            if not allowed:
                stats["scope_filtered"] += 1
                decisions.append({
                    "kind": "principle", "id": row.id, "version": row.version or 1,
                    "outcome": "excluded", "reasons": reasons,
                    # 03-3：先解释过滤——每条被排除原理输出 expected/actual/normalized + 字段级原因
                    "scope_evidence": {
                        "expected_scope": {key: value.get("expected") for key, value in evidence.items()
                                           if isinstance(value, dict) and "expected" in value},
                        "actual_scope": {key: value.get("actual") for key, value in evidence.items()
                                         if isinstance(value, dict) and "actual" in value},
                        "normalized_scope": {key: value.get("normalized") for key, value in evidence.items()
                                             if isinstance(value, dict) and value.get("normalized")},
                        "field_reasons": {key: value.get("verdict") for key, value in evidence.items()
                                          if isinstance(value, dict) and value.get("verdict")},
                    },
                })
                continue
            if not str(row.statement or row.guidance or "").strip():
                stats["not_selected"] += 1
                continue
            if ctx["knowledge_points"]:
                content = " ".join(str(v or "") for v in (row.title, row.statement, row.mechanism, row.guidance))
                content_score = _overlap(ctx["knowledge_points"], [content], aliases)
                score = max(score, content_score)
                if content_score:
                    reasons.append("lexical_keyword_or_alias")
            candidates.append({"row": row, "scope": scope, "score": score,
                               "confidence": max(0, min(1, _number(row.confidence, 0))),
                               "families": _integer(row.evidence_family_count, 0), "reasons": reasons,
                               "pending_scope": "knowledge_points:pending_embedding" in reasons})
        if budget:
            backend, cache_entries = _semantic(db, candidates, ctx, effective, warnings, stats)
        else:
            backend, cache_entries = "lexical", []
            stats["embedding_reason"] = "budget_zero"
        applicable: list[dict] = []
        for candidate in candidates:
            if candidate["pending_scope"] and (
                backend != "embedding" or candidate.get("semantic", -1) < _number(effective.get("embedding_threshold"), 0.72)
            ):
                stats["scope_filtered"] += 1
                continue
            applicable.append(candidate)
        conflicts = _conflicting_ids(db, applicable)
        decisions.extend({"kind": "principle", "id": candidate["row"].id,
                          "version": candidate["row"].version or 1, "outcome": "excluded",
                          "reasons": ["unresolved_conflict"]}
                         for candidate in applicable if candidate["row"].id in conflicts)
        candidates = [c for c in applicable if c["row"].id not in conflicts]
        stats["conflict_excluded"] = len(applicable) - len(candidates)
        candidates.sort(key=lambda c: (-c["score"], -c["confidence"], -c["families"], c["row"].id))
        unique: list[dict] = []
        seen: set[str] = set()
        for candidate in candidates:
            row = candidate["row"]
            # Exact full-content + same-scope duplicate only; no fuzzy merging or DB mutation.
            def exact_text(value: Any) -> str:
                return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()

            fingerprint = _dump([exact_text(row.statement), exact_text(row.mechanism), exact_text(row.guidance),
                                 exact_text(row.applicability), exact_text(row.counterexamples), candidate["scope"]])
            if fingerprint in seen:
                stats["deduplicated"] += 1
            else:
                seen.add(fingerprint)
                unique.append(candidate)

        standard_candidates: list[dict] = []
        frameworks = db.query(models.EvaluationFramework).filter(models.EvaluationFramework.status == "active", models.EvaluationFramework.domain_key.in_([*domain_keys_for(ctx["domain_key"]), "general"])).order_by(models.EvaluationFramework.created_at.desc()).populate_existing().all()
        specific = [f for f in frameworks if canonical_domain_key(f.domain_key) == ctx["domain_key"]]
        frameworks = specific[:1] or [f for f in frameworks if f.domain_key == "general"][:1]
        for framework in frameworks:
            for index, raw in enumerate(_json(framework.standards, [])):
                stats["standards_available"] += 1
                if not isinstance(raw, dict) or not str(raw.get("rule") or "").strip():
                    continue
                scope, invalid = _scope(raw.get("scope"))
                allowed, relevance, _, _evidence = _scope_match(scope, ctx, aliases)
                if invalid or not allowed or not _domain_allowed(framework.domain_key, ctx, scope, general=framework.domain_key == "general"):
                    stats["standards_scope_filtered"] += 1
                    continue
                standard_candidates.append({**raw, "id": str(raw.get("id") or f"standard-{index + 1}"),
                                            "rule": str(raw["rule"]).strip(), "scope": scope,
                                            "framework_id": framework.id, "framework_version": framework.version or 1,
                                            "framework_hash": framework.source_hash or hashlib.sha256(str(framework.source_text or "").encode()).hexdigest(),
                                            "relevance": relevance})
        standard_candidates.sort(key=lambda s: (0 if s.get("strength") == "hard" else 1, _number(s.get("priority"), 0), -s["relevance"],
                                               s["framework_id"], s["id"]))
        blocks: list[list[str]] = [[], [], []]
        standards: list[dict] = []
        selected: list[dict] = []
        examples: list[dict] = []
        evidence_layers: list[list[dict]] = [[], [], []]

        def render() -> str:
            return "\n\n".join(f"{heading}\n" + "\n".join(layer)
                                for heading, layer in zip(_HEADINGS, blocks) if layer)

        def fit(layer: int, block: str, cap: int) -> bool:
            blocks[layer].append(block)
            layer_text = _HEADINGS[layer] + "\n" + "\n".join(blocks[layer])
            if len(render()) > budget or len(layer_text) > cap:
                blocks[layer].pop()
                return False
            return True

        # Layer caps reserve room, unused space remains available to later layers.
        standard_cap = _integer(effective.get("standards_budget_chars"), max(0, int(budget * 0.55)))
        experience_cap = _integer(effective.get("experiences_budget_chars"), budget)
        example_cap = _integer(effective.get("examples_budget_chars"), max(0, int(budget * 0.25)))
        if not unique:
            standard_cap = _integer(effective.get("standards_budget_chars"), budget)
        for standard in standard_candidates:
            standard_text = _standard_block(standard)
            if fit(0, standard_text, standard_cap):
                standards.append(standard)
                ref = _source_ref("framework", standard["framework_id"], standard["framework_version"],
                                  standard["rule"], "standards/" + standard["id"])
                evidence_layers[0].append(_evidence_block(standard["id"], standard["framework_version"],
                    "active", standard_text, conditions=[_render_scope(standard["scope"])] if standard["scope"] else [],
                    reason="active_framework;task_scope_match;strength:" + str(standard.get("strength") or "core"),
                    refs=[ref]))
            else:
                stats["standards_dropped"] += 1
                if standard.get("strength") == "hard":
                    warnings.append(f"hard_standard_budget_omitted:{standard['framework_id']}:{standard['id']}")
        maximum = _integer(effective.get("max_experiences"), 6, 6)
        selected_candidates: list[dict] = []
        for candidate in unique:
            if len(selected) >= maximum:
                stats["not_selected"] += 1
                decisions.append({"kind": "principle", "id": candidate["row"].id,
                                  "version": candidate["row"].version or 1, "outcome": "excluded",
                                  "reasons": ["max_experiences"]})
                continue
            row = candidate["row"]
            principle_text = _principle_block(row, candidate["scope"])
            reason = ";".join(candidate["reasons"]) or "same_domain_general_guidance"
            if fit(1, principle_text, experience_cap):
                selected_candidates.append(candidate)
                selected.append({"id": row.id, "version": row.version or 1, "status": row.status,
                                 "reason": reason,
                                 "score": round(candidate["score"], 6)})
                evidence_layers[1].append(_evidence_block(row.id, row.version, row.status, principle_text,
                    mechanism=str(row.mechanism or "") or None,
                    conditions=[value for value in (str(row.applicability or ""), _render_scope(candidate["scope"])) if value],
                    boundaries=_list(_json(row.counterexamples, [])) or ([str(row.counterexamples)] if row.counterexamples not in (None, "", "[]") else []),
                    reason=reason, refs=[_source_ref("principle", row.id, row.version or 1, principle_text, "guidance")],
                    method="human" if row.status == "confirmed" else "model_static"))
                decisions.append({"kind": "principle", "id": row.id, "version": row.version or 1,
                                  "outcome": "selected", "reasons": candidate["reasons"]})
            else:
                stats["dropped"] += 1
                decisions.append({"kind": "principle", "id": row.id, "version": row.version or 1,
                                  "outcome": "excluded", "reasons": ["insufficient_budget"]})
        if effective.get("include_examples"):
            example_pool = _example_candidates(db, selected_candidates, ctx, aliases, stats)
            stats["examples_available"] = len(example_pool)
            for example in example_pool:
                if fit(2, example["text"], example_cap):
                    examples.append(example)
                    evidence_layers[2].append(_evidence_block(example["question_id"], 1, "raw", example["text"],
                        boundaries=["反例仅展示失败边界，禁止作为正向做法"] if example["polarity"] == "negative" else [],
                        reason=example["reason"], refs=example["source_refs"]))
                else:
                    stats["examples_dropped"] += 1
        stats["injected"] = len(selected)
        stats["standards_injected"] = len(standards)
        stats["examples_injected"] = len(examples)
        text = render()
        omitted = sum(stats[key] for key in ("dropped", "standards_dropped", "examples_dropped"))
        degraded = str(stats.get("embedding_reason", "")).startswith("failed:") or stats.get("embedding_reason") == "not_configured"
        status = ("disabled" if budget == 0 else "degraded" if degraded else
                  "insufficient_budget" if omitted and not text else "ok" if text else "no_match")
        if any("unverified_context" in reason for choice in decisions for reason in choice.get("reasons", [])):
            warnings.append("conditional_evidence: 学习者或分支条件尚未确认，所选指导仅按显式条件适用")
        result = {"text": text, "chars": len(text), "budget_chars": budget, "selected": selected,
                  "standards": standards, "examples": examples, "stats": stats, "warnings": warnings,
                  "context": ctx, "trace_id": None, "backend": backend,
                  "injection_status": status, "decisions": decisions,
                  "evidence_blocks": dict(zip(("standards", "principles", "cases"), evidence_layers))}

    # All network operations ended above. No commits: callers keep ownership of changes.
    if record:
        for entry in cache_entries:
            db.execute(sqlite_insert(models.PropositionEmbedding).values(**entry).on_conflict_do_update(
                index_elements=["source_id", "source_type", "revision", "model_key"],
                set_={"vector": entry["vector"]}))
        # Cache is disposable. Retain bounded newest entries; ordering uses the
        # existing created_at index, and every write happens after network work.
        if cache_entries:
            limit = _integer(effective.get("embedding_cache_limit"), 512, 10000)
            stale = db.query(models.PropositionEmbedding.id).order_by(
                models.PropositionEmbedding.created_at.desc()).offset(limit).all()
            for start in range(0, len(stale), 200):
                db.query(models.PropositionEmbedding).filter(models.PropositionEmbedding.id.in_(
                    [entry[0] for entry in stale[start:start + 200]])).delete(synchronize_session=False)
        trace = models.PropositionGuidanceTrace(
            goal_id=ctx["goal_id"], tutorial_id=ctx["tutorial_id"], section_id=ctx["section_id"],
            task_kind=ctx["task_kind"], context=_dump(ctx), result=_dump(result),
        )
        db.add(trace)
        db.flush([trace])
        result["trace_id"] = trace.id
        trace.result = _dump(result)
        db.flush([trace])
    return result
