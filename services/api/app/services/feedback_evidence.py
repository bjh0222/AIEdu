"""反馈证据闭环（板块五 / ADR-025）：训练反馈 → FeedbackEvidence → 候选专业证据。

注：本文件头旧版标注「ADR-023」，与 docs/decisions.md 的板块五 ADR-025 不一致，
属文档漂移，已修正为 ADR-025（ADR-023 讲题目机制与不可变反馈，仍有效）。

边界（对齐任务书 05 执行切片 A 与 ADR-025）：
- 只产出**候选证据 / 重评请求 / 可观察回归待办**；全部确定性代码，零模型调用。
- 评分与赞语永不直接改动经验（DesignPrinciple）状态；提炼与状态治理仍走
  proposition 既有入口（proposed 门槛、rejected 不复活）。
- 用户原文逐字保存，不 strip、不截断；超长由 API 显式校验拒绝（training.py）。
- 台账是「只追加 JSONL 分片日志 + 可重建投影」（引擎见 feedback_ledger.py）：
  跨进程文件锁、唯一临时文件名、超 2000 条轮换归档分片不删证据、旧单文件台账
  迁移为 legacy_import（先备份、校验数量与 hash，不删旧文件）。锁只覆盖本地
  文件操作，不包裹模型调用（本模块无模型调用）。
- DB ↔ 文件同步：完整待投递事件先随 TrainingRecord.context_json 进同一 DB 事务，
  commit 之后由 ``_deliver_event`` 幂等投递并记 delivered/pending/failed；失败经
  显式恢复入口（POST /training/feedback-evidence/recover）零模型补投。DB 未提交
  就不会产生文件事件，rollback 不留悬空引用。
- 版本绑定：优先用户显式 tutorial_version_id（binding_basis=user_viewed_version）；
  旧客户端无版本时绑定提交当时 current（binding_basis=current_at_submission），
  不冒称用户所见旧版本；general 反馈无 artifact，basis=unbound、
  guidance_bindings=[]，不抓任何其他任务的 trace。指导绑定只认能精确关联到
  本次版本生成调用的 trace（run/call 引用，B 补丁与 04 call_id 落地后激活）；
  无法精确关联时如实返回 []（unbound），绝不退回「最近 3 条」冒充精确关联。
- 指纹 = actor + artifact(version_id/content_hash/unit_id) + rubric_version +
  event_kind + original_text + tags/rating：同版本逐字重复幂等（occurrences 累计），
  跨版本不去重，撤回后新提交产生新事件。
- 撤回/修订以**追加事件**表达，历史不删除、不覆盖；purge_goal 的历史保留语义
  不变（台账是文件，不参与 purge）。
- 板块三未接入时专业评价保持 ``pending_review`` 待评状态，不伪造闭环。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..models import (
    LearningGoal,
    PropositionGuidanceTrace,
    TrainingRecord,
    Tutorial,
    TutorialVersion,
)
from .utils import jdump, jload
from .generation_contract import validate_generation_message
from . import feedback_ledger as ledger_engine

logger = logging.getLogger("aiedu")

LEDGER_VERSION = ledger_engine.LEDGER_VERSION
MAX_LEDGER_EVENTS = ledger_engine.MAX_ACTIVE_EVENTS
EVENT_OUTCOME_POSITIVE = "recorded"
EVENT_OUTCOME_CANDIDATE = "proposed"
EVENT_OUTCOME_REVIEW = "review_requested"
EVENT_OUTCOME_RETRACTED = "retracted"
MAX_LOCATED = 20

# 命题回流只认题目级、可定位的缺陷证据；排版/结构类反馈不进命题库。
_PROP_CODE_RE = None  # 延迟编译：见 _prop_code_match
_PROP_CODE_PATTERN = r"^\[(E0\d{2}|DEFECT)\]"
_QUESTION_RE_R = r"第\s*(\d+)\s*题"

# 定位到的失败模式 → 固定基准包（fixtures/benchmark/fixed-tasks.package.json）中
# 覆盖同类失效的工程回归任务。仅是「可观察回归待办」的建议项，不自动执行。
REGRESSION_TASK_HINTS: dict[str, list[str]] = {
    "correctness": ["exam-wrong-answer", "exercise-wrong-answer"],
    "E017": ["exam-no-type-titles", "exam-no-answers-two-papers"],
    "E018": ["exam-no-answers-two-papers"],
    "DEFECT": ["exercise-wrong-answer"],
    "scope_style": ["exam-scope-boundary", "exam-no-type-titles"],
    "structure": ["exam-no-answers-two-papers", "tutorial-spec-conflict"],
    "difficulty": ["exam-hard-difficulty-default"],
    "explanation": ["tutorial-minimal-brief"],
    "connections": ["tutorial-minimal-brief"],
    "transfer": ["tutorial-minimal-brief"],
    "source_grounding": ["reading-long-material"],
    "verification": ["practical-verification-steps"],
    "prerequisites": ["practical-verification-steps"],
    "assessment_value": ["exercise-wrong-answer"],
}

# 台账公开 API 再导出（消费方统一从本模块取用）
ledger_lock = ledger_engine.ledger_lock
iter_all_events = ledger_engine.iter_all_events
migrate_legacy_ledger = ledger_engine.migrate_legacy_ledger
build_projection = ledger_engine.build_projection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prop_code_match(text: str):
    global _PROP_CODE_RE
    if _PROP_CODE_RE is None:
        import re

        _PROP_CODE_RE = re.compile(_PROP_CODE_PATTERN)
    return _PROP_CODE_RE.match(text)


def _question_number(text: str) -> int:
    import re

    m = re.search(_QUESTION_RE_R, text)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 台账文件（引擎：feedback_ledger.py；此处保留旧路径/读写兼容入口）
# ---------------------------------------------------------------------------


def ledger_path() -> Path:
    """旧单文件台账路径（v1 形态）。新事件写进 feedback_evidence/ 分片日志。"""
    return ledger_engine.legacy_ledger_path()


def load_ledger() -> dict:
    """兼容读取：返回折叠后的投影（events/revaluations/regressions）。"""
    projection = ledger_engine.build_projection()
    return {"version": LEDGER_VERSION, "updated_at": projection["updated_at"],
            "events": projection["events"], "revaluations": projection["revaluations"],
            "regressions": projection["regressions"]}


def save_ledger(ledger: dict) -> None:
    """旧形态兼容写（迁移种子 / 测试播种用）：写旧单文件台账。

    新代码不应调用——事件一律经 append 路径进分片日志；这里写出的旧格式文件会在
    引擎下一次使用时被 legacy_import 流程接管（备份 + hash 校验）。
    """
    payload = dict(ledger or {})
    payload["updated_at"] = _now()
    path = ledger_path()
    tmp = path.with_suffix(f".json.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# generation.v1 FeedbackEvidence 构造
# ---------------------------------------------------------------------------


def source_ref(kind: str, identifier: str, *, version=None, text: str = "", location: str = "") -> dict:
    return {"kind": kind, "id": str(identifier), "version": str(version) if version is not None else None,
            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
            "location": location or None}


def _event_kind(message: dict) -> str:
    payload = message.get("payload") or {}
    ext = message.get("extensions") or {}
    if payload.get("outcome") == EVENT_OUTCOME_RETRACTED or ext.get("retracts"):
        return "retraction"
    if ext.get("revises"):
        return "revision"
    return "feedback"


def evidence_fingerprint(message: dict) -> str:
    """指纹 = actor + artifact(version_id/content_hash/unit_id) + rubric_version +
    event_kind + original_text + tags/rating。

    原文逐字参与（不 strip、不截断）；同一版本（同 version_id/content_hash/unit_id）
    逐字重复才判定重复；跨版本、跨量规版本（spec hash）、不同 event_kind 都是新观测。
    """
    identity = message.get("identity") or {}
    payload = message.get("payload") or {}
    ext = message.get("extensions") or {}
    binding = ext.get("binding") or {}
    spec = ext.get("format_spec") or {}
    artifact = [
        str(binding.get("tutorial_version_id") or ""),
        str(binding.get("content_hash") or ""),
        str(identity.get("tutorial_id") or ""),
        str(identity.get("version") if identity.get("version") is not None else ""),
        str(identity.get("unit_id") or ""),
    ]
    factors = [
        "actor:user",  # 单用户工具；引入多账号时从 client_context 取真实 actor
        artifact,
        str(spec.get("hash") or ""),  # rubric_version：版本内嵌规范的 hash
        _event_kind(message),
        str(payload.get("text") or ""),
        sorted(str(t) for t in (ext.get("tags") or [])),
        int(ext.get("rating") or 0),
    ]
    return hashlib.sha256(jdump(factors).encode("utf-8")).hexdigest()


def locate_problems(quality_report: dict | None) -> list[dict]:
    """从最终质量报告定位问题：只取可定位的实质项，不猜未知。

    - AcceptanceReport（generation.v1）：适用且 ``violated``/``error`` 的检查，
      以及必需但 ``undecidable`` 的检查（未判定 ≠ 通过，需要专业复核）。
    - ``issues``：命题相关码（E0xx / DEFECT）前缀的实质缺陷。
    """
    problems: list[dict] = []
    seen: set[tuple] = set()
    report = quality_report if isinstance(quality_report, dict) else {}
    acceptance = report.get("acceptance") if isinstance(report.get("acceptance"), dict) else {}
    for check in (acceptance.get("payload") or {}).get("checks") or []:
        if not isinstance(check, dict) or not check.get("applicable"):
            continue
        status = check.get("status")
        if status in ("violated", "error") or (status == "undecidable" and check.get("required")):
            key = ("check", check.get("id"), status, str(check.get("detail") or "")[:200])
            if key in seen:
                continue
            seen.add(key)
            problems.append({"origin": "acceptance_check", "check_id": str(check.get("id") or ""),
                             "status": status, "required": bool(check.get("required")),
                             "detail": str(check.get("detail") or "")[:500]})
        if len(problems) >= MAX_LOCATED:
            return problems
    for issue in report.get("issues") or []:
        text = str(issue or "")
        m = _prop_code_match(text)
        if not m:
            continue
        key = ("issue", m.group(1), text[:200])
        if key in seen:
            continue
        seen.add(key)
        problems.append({"origin": "quality_issue", "code": m.group(1), "detail": text[:500],
                         "question_number": _question_number(text)})
        if len(problems) >= MAX_LOCATED:
            break
    return problems


def build_feedback_evidence(*, record_id: str, goal_id=None, tutorial_id=None, section_id=None,
                            version: int | None = None, rating, tags, comment: str,
                            quality_report: dict | None = None, guidance=None,
                            spec_version=None, origin: str = "user",
                            evidence_method: str = "human", outcome: str | None = None,
                            event_id: str | None = None, extra_extensions: dict | None = None,
                            binding: dict | None = None) -> dict:
    """构造一条 generation.v1 FeedbackEvidence 线消息（不落台账）。"""
    located = locate_problems(quality_report)
    if outcome is None:
        if int(rating or 0) < 0:
            outcome = EVENT_OUTCOME_REVIEW if located else EVENT_OUTCOME_CANDIDATE
        else:
            outcome = EVENT_OUTCOME_POSITIVE
    identity = {"task_id": f"feedback-{record_id}", "goal_id": goal_id or None,
                "tutorial_id": tutorial_id or None, "version": version if version and version >= 1 else None,
                "unit_id": section_id or None}
    target_refs: list[dict] = []
    if tutorial_id:
        target_refs.append(source_ref("generated", tutorial_id, version=version, location="tutorial_version"))
    if section_id:
        target_refs.append(source_ref("generated", f"{tutorial_id or ''}:{section_id}", version=version,
                                      location="section"))
    source_refs = [source_ref("feedback", record_id, text=comment or "", location="training_records.comment")]
    if goal_id:
        source_refs.append(source_ref("user_task", goal_id, location="goal"))
    extensions = {
        "rating": int(rating or 0),
        "tags": [str(t) for t in (tags or [])],
        "located_problems": located,
        "guidance_bindings": guidance or [],
        "format_spec": spec_version or {},
        "governance_note": "用户反馈是人工证据；评分/赞语不改变经验状态，专业评价走命题知识库既有治理。",
    }
    if binding:
        extensions["binding"] = binding
    if extra_extensions:
        extensions.update(extra_extensions)
    message = {"contract_version": "generation.v1", "kind": "FeedbackEvidence",
               "identity": identity,
               "payload": {"event_id": event_id or f"fe-{uuid.uuid4().hex[:20]}", "origin": origin,
                           "text": str(comment or ""), "outcome": outcome,
                           "source_refs": source_refs, "target_refs": target_refs,
                           "evidence_method": evidence_method},
               "extensions": extensions}
    validate_generation_message(message, "FeedbackEvidence")
    return message


def build_retraction(original: dict, *, reason: str = "", replacement_comment: str = "",
                     record_id: str = "") -> tuple[dict, dict | None]:
    """撤回以追加事件表达：原事件 payload 不变，新事件 outcome=retracted。"""
    payload = original["payload"]
    original_binding = (original.get("extensions") or {}).get("binding")
    retraction = build_feedback_evidence(
        record_id=record_id or f"retract-{payload['event_id']}",
        goal_id=original["identity"].get("goal_id"),
        tutorial_id=original["identity"].get("tutorial_id"),
        section_id=original["identity"].get("unit_id"),
        version=original["identity"].get("version"),
        rating=(original.get("extensions") or {}).get("rating", 0),
        tags=(original.get("extensions") or {}).get("tags", []),
        comment=reason or "用户撤回该反馈证据。",
        origin="user", outcome=EVENT_OUTCOME_RETRACTED,
        binding=original_binding,
        extra_extensions={"retracts": payload["event_id"]},
    )
    if replacement_comment:
        revision = build_feedback_evidence(
            record_id=f"revise-{payload['event_id']}",
            goal_id=original["identity"].get("goal_id"),
            tutorial_id=original["identity"].get("tutorial_id"),
            section_id=original["identity"].get("unit_id"),
            version=original["identity"].get("version"),
            rating=(original.get("extensions") or {}).get("rating", 0),
            tags=(original.get("extensions") or {}).get("tags", []),
            comment=replacement_comment,
            quality_report={"acceptance": {}, "issues": []},
            origin="user", outcome=EVENT_OUTCOME_CANDIDATE,
            binding=original_binding,
            extra_extensions={"revises": payload["event_id"]},
        )
        return retraction, revision
    return retraction, None


# ---------------------------------------------------------------------------
# 版本绑定（切片 A 核心）
# ---------------------------------------------------------------------------


def _resolve_binding(db, record, request: dict) -> tuple[dict, int | None, TutorialVersion | None]:
    """决定绑定对象与 basis。从属校验在 training.submit_feedback（400），这里兜底防御。

    - 用户显式 tutorial_version_id → basis=user_viewed_version；
    - 旧客户端无版本 → 绑定提交当时 current → basis=current_at_submission；
    - 无 tutorial（general）→ basis=unbound。
    """
    tutorial_id = record.tutorial_id
    tv: TutorialVersion | None = None
    basis = "unbound"
    version: int | None = None
    content_hash: str | None = None
    if tutorial_id:
        tutorial = db.get(Tutorial, tutorial_id)
        requested = request.get("tutorial_version_id")
        if requested:
            candidate = db.get(TutorialVersion, requested)
            if candidate is not None and candidate.tutorial_id == tutorial_id:
                tv = candidate
        if tv is None and tutorial is not None and tutorial.current_version_id:
            tv = db.get(TutorialVersion, tutorial.current_version_id)
        if tv is not None:
            basis = "user_viewed_version" if requested else "current_at_submission"
            version = tv.version
            content_hash = hashlib.sha256(str(tv.content_json or "").encode("utf-8")).hexdigest()
    binding = {
        "basis": basis,
        "tutorial_version_id": str(tv.id) if tv is not None else None,
        "version": version,
        "content_hash": content_hash,
        "generation_record_id": request.get("generation_record_id"),
        "run_id": request.get("run_id"),
    }
    return binding, version, tv


def _spec_evidence(goal, tutorial_version: TutorialVersion | None) -> dict:
    """版本证据优先：TutorialVersion.content_json.format_spec；旧数据回退 goal 快照。"""
    if tutorial_version is not None:
        content = jload(tutorial_version.content_json, {}) or {}
        spec = content.get("format_spec")
        if isinstance(spec, dict) and spec:
            raw = json.dumps(spec, ensure_ascii=False, sort_keys=True)
            return {"hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32],
                    "scene": str(spec.get("scene") or ""),
                    "source": "version_content"}
    raw = str(getattr(goal, "format_spec_snapshot", "") or "")
    if not raw:
        return {}
    try:
        scene = str((json.loads(raw) or {}).get("scene") or "")
    except ValueError:
        scene = ""
    return {"hash": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32], "scene": scene,
            "source": "goal_snapshot"}


def _guidance_bindings(db, record, binding: dict | None) -> list[dict]:
    """精确指导绑定：只认能精确关联到本次版本生成调用的 trace。

    general / 无 artifact 反馈直接 []（不抓任何任务的 trace——修复原「无过滤抓
    最近 3 条」缺陷）。有 artifact 但缺 run/call 关联时同样返回 []（unbound）：
    「最近 3 条」不构成精确关联，宁可 unbound 也不冒充。trace 携带 run_id /
    call_id 引用后（B 补丁 + 04 call_id）自动激活精确绑定。
    """
    if not record.tutorial_id:
        return []
    run_id = (binding or {}).get("run_id")
    generation_record_id = (binding or {}).get("generation_record_id")
    if not run_id and not generation_record_id:
        return []
    query = db.query(PropositionGuidanceTrace).filter(
        PropositionGuidanceTrace.tutorial_id == record.tutorial_id)
    if record.section_id:
        query = query.filter(PropositionGuidanceTrace.section_id == record.section_id)
    rows = query.order_by(PropositionGuidanceTrace.created_at.desc()).limit(50).all()
    bindings: list[dict] = []
    for row in rows:
        result = jload(row.result, {})
        context = jload(row.context, {}) or {}
        if not isinstance(result, dict):
            continue
        row_run = str((context or {}).get("run_id") or "")
        row_records = {str(r) for r in ((context or {}).get("generation_record_ids") or [])}
        row_calls = {str(c.get("call_id")) for c in (result.get("calls") or [])
                     if isinstance(c, dict) and c.get("call_id")}
        precise = (
            (run_id and row_run == run_id)
            or (generation_record_id and generation_record_id in row_records)
            or bool(row_calls)  # trace 已带 call_id（04 落地后）→ 与版本调用可精确对账
        )
        if not precise:
            continue
        bindings.append({
            "trace_id": row.id,
            "task_kind": row.task_kind or "",
            "injection_status": result.get("injection_status") or "",
            "principles": [{"id": s.get("id"), "version": s.get("version"), "status": s.get("status")}
                           for s in result.get("selected") or [] if isinstance(s, dict)],
            "frameworks": [{"id": s.get("framework_id"), "version": s.get("framework_version"),
                            "standard_id": s.get("id")}
                           for s in result.get("standards") or [] if isinstance(s, dict)],
            "examples": len(result.get("examples") or []),
        })
        if len(bindings) >= 3:
            break
    return bindings


def record_user_feedback(db, record) -> dict | None:
    """反馈消费：绑定 → 去重判定 → 写 context（pending），**不落台账文件**。

    台账交付在 DB commit 之后由 ``_deliver_event`` 幂等执行（05 切片 A：事件先随
    TrainingRecord.context_json 进同一 DB 事务，rollback 不产生文件侧悬空事件）。
    返回写进 TrainingRecord.context 的绑定摘要；失败由调用方兜底，不阻断反馈提交。
    """
    context = jload(record.context_json, {}) or {}
    request = context.get("binding_request") or {}
    binding, version, tv = _resolve_binding(db, record, request)
    quality_report: dict = {}
    if tv is not None:
        quality_report = jload(tv.quality_report, {}) or {}
    spec_version: dict = {}
    goal = db.get(LearningGoal, record.goal_id) if record.goal_id else None
    spec_version = _spec_evidence(goal, tv)  # 版本内嵌 spec 优先，与 goal 无关
    guidance = _guidance_bindings(db, record, binding)
    message = build_feedback_evidence(
        record_id=record.id, goal_id=record.goal_id, tutorial_id=record.tutorial_id,
        section_id=record.section_id, version=version, rating=record.rating,
        tags=jload(record.tags, []), comment=record.comment or "",
        quality_report=quality_report, guidance=guidance, spec_version=spec_version,
        binding=binding,
    )
    located = message["extensions"]["located_problems"]
    fingerprint = evidence_fingerprint(message)
    target = ledger_engine.find_active_by_fingerprint(fingerprint)
    if target is not None:
        # 同版本逐字重复：只累计次数；交付时追加 occurrence 操作行
        ev = {"event_id": target["event_id"],
              "outcome": ((target.get("message") or {}).get("payload") or {}).get("outcome"),
              "deduplicated": True, "occurrences": int(target.get("occurrences", 1)) + 1,
              "located": 0, "reflux_written": 0, "revaluation_requested": False,
              "regression_todos": 0, "binding": binding, "delivery_status": "pending",
              "pending_ops": [{"kind": "occurrence", "target_event_id": target["event_id"],
                               "fingerprint": fingerprint}]}
    else:
        governance = int(record.rating or 0) < 0 and bool(located)
        ev = {"event_id": message["payload"]["event_id"],
              "outcome": message["payload"]["outcome"], "deduplicated": False,
              "occurrences": 1, "located": len(located), "reflux_written": 0,
              "revaluation_requested": False, "regression_todos": 0,
              "binding": binding, "delivery_status": "pending",
              "pending_ops": [{"kind": "event", "message": message, "governance": governance}]}
    context["feedback_evidence"] = ev
    record.context_json = jdump(context)
    return ev


# ---------------------------------------------------------------------------
# 台账写入（追加式；governance 附属事件与主事件同批落盘）
# ---------------------------------------------------------------------------


def _event_entry(message: dict, *, kind: str) -> dict:
    now = _now()
    return {"event_id": message["payload"]["event_id"], "created_at": now, "updated_at": now,
            "fingerprint": evidence_fingerprint(message), "occurrences": 1,
            "status": "active", "message": message, "kind": kind}


def _governance_lines(message: dict, *, event_id: str, ext: dict, located: list[dict],
                      now: str) -> tuple[list[dict], int]:
    """负面且可定位：重评请求 + 回归待办（追加行，随主事件同批落盘）。"""
    lines: list[dict] = []
    request_id = f"rev-{uuid.uuid4().hex[:20]}"
    lines.append({"kind": "revaluation", "created_at": now, "payload": {
        "request_id": request_id, "created_at": now, "event_id": event_id,
        "target": {"goal_id": message["identity"]["goal_id"],
                   "tutorial_id": message["identity"]["tutorial_id"],
                   "section_id": message["identity"]["unit_id"],
                   "version": message["identity"]["version"]},
        "reason": {"rating": ext["rating"], "located": located[:5]},
        "scope": "proposition_re_evaluation", "status": "pending_review",
        "note": "板块三未接入时保持待评状态；分析须显式触发，不自动调用模型。",
    }})
    hints: list[str] = []
    for problem in located:
        hints.extend(REGRESSION_TASK_HINTS.get(str(problem.get("check_id") or problem.get("code") or ""), []))
    lines.append({"kind": "regression", "created_at": now, "payload": {
        "todo_id": f"reg-{uuid.uuid4().hex[:20]}", "created_at": now, "event_id": event_id,
        "goal_id": message["identity"]["goal_id"], "tutorial_id": message["identity"]["tutorial_id"],
        "version": message["identity"]["version"],
        "failure_modes": [{"key": str(p.get("check_id") or p.get("code") or ""), "status": p.get("status")}
                          for p in located[:10]],
        "suggested_benchmark_cases": list(dict.fromkeys(hints)),
        "status": "open",
        "note": "可观察回归待办：建议在固定基准包中覆盖同类失效；不自动执行。",
    }})
    return lines, 1


def _append_event(db, message: dict) -> dict:
    """立即落台账（直接调用 / 测试 / 重放入口）。API 提交路径经 _deliver_event 幂等投递。

    去重判定与追加在同一文件锁内原子完成；同版本逐字重复只累计 occurrences，
    原文与历史不动。
    """
    ext = message.get("extensions") or {}
    located = ext.get("located_problems") or []
    fingerprint = evidence_fingerprint(message)
    entry = _event_entry(message, kind=_event_kind(message))
    extra_lines: list[dict] = []
    if int(ext.get("rating") or 0) < 0 and located:
        lines, _count = _governance_lines(message, event_id=entry["event_id"], ext=ext,
                                          located=located, now=entry["created_at"])
        extra_lines.extend(lines)
    target, occurrences = ledger_engine.append_batch_with_dedup(entry, fingerprint, extra_lines)
    if target is not None:
        return {"event_id": target["event_id"],
                "outcome": ((target.get("message") or {}).get("payload") or {}).get("outcome"),
                "deduplicated": True, "occurrences": occurrences, "located": 0,
                "reflux_written": 0, "revaluation_requested": False, "regression_todos": 0}
    reflux_written = 0
    revaluation_requested = bool(extra_lines)
    if revaluation_requested:
        # 现有质量回流（旧入口）：候选 QualityRecord + 既有确定性提炼治理（DB 操作，
        # 不在台账文件锁内执行；失败不阻断反馈提交）。
        from . import proposition as proposition_service

        try:
            reflux_written = proposition_service.reflux_feedback_evidence(db, message)
        except Exception as exc:  # noqa: BLE001  回流失败不阻断反馈提交
            logger.warning("反馈回流候选证据失败（event=%s）：%s", entry["event_id"], exc)
    return {"event_id": entry["event_id"], "outcome": message["payload"]["outcome"],
            "deduplicated": False, "occurrences": 1, "located": len(located),
            "reflux_written": reflux_written, "revaluation_requested": revaluation_requested,
            "regression_todos": 1 if revaluation_requested else 0}


def _apply_ops(db, ops: list[dict]) -> dict | None:
    """幂等投递一批 pending 操作；返回最后一个成功操作的摘要（无操作则 None）。"""
    present = ledger_engine.event_ids_present()
    last: dict | None = None
    for op in ops or []:
        kind = op.get("kind")
        if kind == "event":
            message = op.get("message") or {}
            event_id = (message.get("payload") or {}).get("event_id")
            if not event_id or event_id in present:
                continue
            last = _append_event(db, message)
            present.add(last["event_id"])
        elif kind == "occurrence":
            try:
                occurrences = ledger_engine.append_occurrence(op["target_event_id"],
                                                              op["fingerprint"])
            except KeyError:
                continue  # 目标已撤回/不存在：occurrence 无意义，跳过不留悬空引用
            last = {"event_id": op["target_event_id"], "outcome": None, "deduplicated": True,
                    "occurrences": occurrences}
    return last


def _deliver_event(db, record) -> dict | None:
    """commit 后幂等投递：把 context 里的 pending_ops 落台账。

    状态机：pending →（成功）delivered /（异常）failed。只做本地文件与确定性 DB
    操作，零模型调用；投递异常转 failed 状态不向上抛（恢复入口可补投）。
    """
    context = jload(record.context_json, {}) or {}
    ev = context.get("feedback_evidence") or {}
    ops = ev.get("pending_ops") or []
    if not ops or ev.get("delivery_status") == "delivered":
        return None
    try:
        summary = _apply_ops(db, ops)
        ev["delivery_status"] = "delivered"
        ev["delivered_at"] = _now()
        if summary:
            ev["event_id"] = summary.get("event_id") or ev.get("event_id")
            if summary.get("outcome"):
                ev["outcome"] = summary["outcome"]
            ev["deduplicated"] = bool(summary.get("deduplicated"))
            ev["occurrences"] = int(summary.get("occurrences") or ev.get("occurrences") or 1)
    except Exception as exc:  # noqa: BLE001  投递失败不阻断调用方
        ev["delivery_status"] = "failed"
        ev["delivery_error"] = str(exc)[:200]
        summary = None
    record.context_json = jdump(context)
    db.commit()  # 独立短事务：只落投递状态
    return summary


def recover_pending(db) -> dict:
    """显式恢复入口（零模型）：把 pending/failed 的待投递证据补投到台账（幂等）。"""
    rows = (db.query(TrainingRecord)
            .filter(TrainingRecord.source_type == "feedback")
            .order_by(TrainingRecord.created_at.asc())
            .all())
    recovered = already_delivered = previously_failed = 0
    for record in rows:
        context = jload(record.context_json, {}) or {}
        ev = context.get("feedback_evidence") or {}
        if not ev.get("pending_ops"):
            continue
        if ev.get("delivery_status") == "delivered":
            already_delivered += 1
            continue
        if ev.get("delivery_status") == "failed":
            previously_failed += 1
        _deliver_event(db, record)
        after = (jload(record.context_json, {}) or {}).get("feedback_evidence") or {}
        if after.get("delivery_status") == "delivered":
            recovered += 1
    return {"recovered": recovered, "already_delivered": already_delivered,
            "previously_failed": previously_failed}


# ---------------------------------------------------------------------------
# 重评消费桥与状态机（任务书 05 切片 C；零模型——模型工作由既有命题分析任务执行）
# ---------------------------------------------------------------------------


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _question_stem_from_version(db, tutorial_id: str, version: int | None, number: int) -> str:
    """从绑定版本内容中反查「第 N 题」题干（确定性文本抽取，不猜不造）。"""
    import re

    if not tutorial_id or not number:
        return ""
    tutorial = db.get(Tutorial, tutorial_id)
    if tutorial is None:
        return ""
    tv: TutorialVersion | None = None
    if version:
        tv = (db.query(TutorialVersion)
              .filter(TutorialVersion.tutorial_id == tutorial_id,
                      TutorialVersion.version == version)
              .first())
    if tv is None and tutorial.current_version_id:
        tv = db.get(TutorialVersion, tutorial.current_version_id)
    if tv is None:
        return ""
    content = jload(tv.content_json, {}) or {}
    parts: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            for value in node.values():
                if isinstance(value, str):
                    parts.append(value)
                elif isinstance(value, (dict, list)):
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(content)
    text = "\n".join(p for p in parts if p)
    match = re.search(rf"第\s*{int(number)}\s*题", text)
    if not match:
        return ""
    rest = text[match.start():]
    next_q = re.search(r"第\s*\d+\s*题", rest[5:])
    stem = rest[:(5 + next_q.start())] if next_q else rest
    return _norm_text(stem)[:1200]


def _match_bank_question(db, stem: str, *, question_id: str | None = None):
    """把可定位题目对到命题库题目：显式 question_id（人工 triage）或题干逐字匹配。

    匹配不到/多义 → 返回 (None, candidates)；绝不编造题目。
    """
    from ..models import PropositionQuestion

    if question_id:
        row = db.get(PropositionQuestion, question_id)
        if row is None or row.parse_status != "ready":
            return None, []
        return row, []
    if not stem or len(stem) < 24:
        return None, []
    needle = _norm_text(stem)[:400]
    candidates = []
    for row in db.query(PropositionQuestion).filter(
            PropositionQuestion.parse_status == "ready").all():
        haystack = _norm_text(row.raw_text)
        if needle and needle[:200] in haystack:
            candidates.append(row)
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


def _ensure_revaluation_row(event_id: str, message: dict) -> str:
    """确保该事件存在一条可迁移的 revaluation 行；返回 request_id（追加式）。"""
    projection = ledger_engine.build_projection()
    existing = next((p for p in projection["revaluations"]
                     if p.get("event_id") == event_id
                     and str(p.get("status") or "") in ("pending_review", "needs_triage")), None)
    if existing is not None:
        return str(existing.get("request_id"))
    identity = message.get("identity") or {}
    ext = message.get("extensions") or {}
    now = _now()
    request_id = f"rev-{uuid.uuid4().hex[:20]}"
    ledger_engine.append_lines([{"kind": "revaluation", "created_at": now, "payload": {
        "request_id": request_id, "created_at": now, "event_id": event_id,
        "target": {"goal_id": identity.get("goal_id"),
                   "tutorial_id": identity.get("tutorial_id"),
                   "section_id": identity.get("unit_id"),
                   "version": identity.get("version")},
        "reason": {"rating": ext.get("rating"),
                   "located": (ext.get("located_problems") or [])[:5]},
        "scope": "proposition_re_evaluation", "status": "pending_review",
        "note": "显式重评消费入口建档；模型工作仍由既有命题任务执行，不暗加调用。"}}])
    return request_id


def request_review(db, event_id: str, *, question_id: str | None = None) -> dict:
    """显式动作端点（POST /training/feedback-evidence/{event_id}/review-request）。

    - 校验事件存在且未撤回；无 question_number 的可定位项 → needs_triage；
    - 可定位题目优先题干逐字匹配命题库（或多义时列候选），显式 question_id 表示
      人工 triage 指认；匹配不到保持 needs_triage，**不自动编造题目**；
    - 命中：适配既有指定题目重评任务（proposition_knowledge.start_analysis，
      force=True 单题），revaluation 追加 dispatched 更新（task_id + 预算摘要）；
      普通提交反馈仍零模型（模型工作由该既有任务自己的预算与治理执行）。
    """
    projection = ledger_engine.build_projection()
    entry = next((e for e in projection["events"] if e.get("event_id") == event_id), None)
    if entry is None:
        raise KeyError(event_id)
    if entry.get("status") == "retracted":
        raise ValueError("event_retracted")
    message = entry.get("message") or {}
    identity = message.get("identity") or {}
    located = [p for p in ((message.get("extensions") or {}).get("located_problems") or [])
               if isinstance(p, dict)]
    numbered = [p for p in located if int(p.get("question_number") or 0) > 0]
    now = _now()
    request_id = _ensure_revaluation_row(event_id, message)
    if not numbered and not question_id:
        update = {"status": "needs_triage",
                  "note": "无可定位题目（缺陷未带题号且未指认命题库题目）；保持待人工甄别，不造题。"}
        ledger_engine.append_lines([{"kind": "revaluation_update", "at": now,
                                     "request_id": request_id, "update": update}])
        return {"event_id": event_id, "request_id": request_id,
                "status": "needs_triage", "task_id": None}
    stem = _question_stem_from_version(db, identity.get("tutorial_id") or "",
                                       identity.get("version"),
                                       int(numbered[0].get("question_number")) if numbered else 0)
    question, candidates = _match_bank_question(db, stem, question_id=question_id)
    if question is None:
        update = {"status": "needs_triage",
                  "note": (f"可定位第 {numbered[0].get('question_number')} 题，但命题库"
                           f"{'多义匹配 ' + str(len(candidates)) + ' 条' if candidates else '无逐字匹配题干'}；"
                           "需人工指认 question_id，不自动编造题目。"),
                  "candidates": [c.id for c in candidates][:10]}
        ledger_engine.append_lines([{"kind": "revaluation_update", "at": now,
                                     "request_id": request_id, "update": update}])
        return {"event_id": event_id, "request_id": request_id, "status": "needs_triage",
                "task_id": None, "candidates": [c.id for c in candidates][:10]}
    from .proposition_knowledge import start_analysis

    job = start_analysis(db, question.batch_id, force=True, question_ids=[question.id],
                         background=True)
    budget_summary = {
        "kind": "proposition_analysis",
        "task_id": job.get("id"),
        "max_model_calls": 200, "retries": 1,
        "source": "ADR-020 既有任务预算（每任务最多 200 次调用、失败最多一次重试）",
    }
    update = {"status": "dispatched", "task_id": job.get("id"),
              "question_id": question.id, "batch_id": question.batch_id,
              "budget": budget_summary,
              "note": "已适配既有指定题目重评任务；完成状态由同步入口按任务终态落账，不暗加模型调用。"}
    ledger_engine.append_lines([{"kind": "revaluation_update", "at": now,
                                 "request_id": request_id, "update": update}])
    return {"event_id": event_id, "request_id": request_id, "status": "dispatched",
            "task_id": job.get("id"), "budget": budget_summary}


def _update_revaluation(request_id: str, update: dict) -> None:
    ledger_engine.append_lines([{"kind": "revaluation_update", "at": _now(),
                                 "request_id": request_id, "update": update}])


def _update_regression(todo_id: str, update: dict) -> None:
    ledger_engine.append_lines([{"kind": "regression_update", "at": _now(),
                                 "todo_id": todo_id, "update": update}])


def cancel_revaluation(db, request_id: str, *, reason: str = "") -> dict:
    """取消重评请求：pending_review/needs_triage/dispatched → cancelled（追加，历史保留）。"""
    projection = ledger_engine.build_projection()
    row = next((p for p in projection["revaluations"]
                if p.get("request_id") == request_id), None)
    if row is None:
        raise KeyError(request_id)
    status = str(row.get("status") or "")
    if status in ("reviewed", "failed", "cancelled"):
        raise ValueError(f"invalid_transition:{status}")
    _update_revaluation(request_id, {"status": "cancelled",
                                     "note": reason or "用户取消；尚未派发或不再消费其结果。"})
    return {"request_id": request_id, "status": "cancelled", "previous": status}


def sync_revaluation_states(db) -> int:
    """把 dispatched 的重评请求按命题分析任务终态落账（零模型，列表读取侧调用）。

    done → reviewed（保存关联内部评价版本=分析任务 id 与框架版本）；failed/partial → failed。
    """
    projection = ledger_engine.build_projection()
    from ..models import PropositionAnalysisJob

    changed = 0
    for row in projection["revaluations"]:
        if str(row.get("status") or "") != "dispatched":
            continue
        task_id = row.get("task_id")
        if not task_id:
            continue
        job = db.get(PropositionAnalysisJob, task_id)
        if job is None:
            continue
        if job.status == "done":
            _update_revaluation(row["request_id"], {
                "status": "reviewed", "task_status": job.status,
                "internal_evaluation": {"task_id": task_id, "framework_id": job.framework_id},
                "note": "既有重评任务完成；其结果经命题库既有治理（采纳门槛不变），不因反馈自动 confirmed。"})
            changed += 1
        elif job.status in ("failed", "partial"):
            _update_revaluation(row["request_id"], {
                "status": "failed", "task_status": job.status,
                "note": "既有重评任务未完成；失败如实落账，可显式重派。"})
            changed += 1
    return changed


def bind_regression_todo(todo_id: str, *, fixed_case_id: str, case_version: int | str,
                         report_id: str) -> dict:
    """回归待办绑定生产 runner 的固定失效案例与报告（追加）。"""
    if not fixed_case_id or not report_id:
        raise ValueError("bind 需要 fixed_case_id 与 report_id")
    update = {"fixed_case_id": fixed_case_id, "case_version": str(case_version),
              "report_id": report_id, "status": "tracked",
              "note": "已绑定固定失效案例；只有该案例在后续真实回归中判定 met 才可 resolved。"}
    _update_regression(todo_id, update)
    return {"todo_id": todo_id, **update}


def resolve_regression_todo(todo_id: str, *, report_passed: bool, report_id: str = "") -> dict:
    """回归待办 resolved：必须已绑定案例且报告判定通过，否则保持 open（预设判据）。"""
    projection = ledger_engine.build_projection()
    row = next((p for p in projection["regressions"]
                if p.get("todo_id") == todo_id), None)
    if row is None:
        raise KeyError(todo_id)
    if not row.get("fixed_case_id"):
        raise ValueError("unbound: 回归待办未绑定 fixed_case_id，不能 resolved")
    if not report_passed:
        _update_regression(todo_id, {"status": "open",
                                     "report_id": report_id or row.get("report_id"),
                                     "note": "回归未通过；保持 open，不伪装已改进。"})
        return {"todo_id": todo_id, "status": "open"}
    _update_regression(todo_id, {"status": "resolved",
                                 "report_id": report_id or row.get("report_id"),
                                 "note": "绑定案例在真实回归中判定 met；待办关闭。"})
    return {"todo_id": todo_id, "status": "resolved"}


# ---------------------------------------------------------------------------
# 撤回 / 修订与列表
# ---------------------------------------------------------------------------


def retract_event(event_id: str, *, reason: str = "", replacement_comment: str = "",
                  db=None) -> dict:
    """撤回（可选修订）：先存独立事件记录（有 DB 时），再以追加事件重建投影。

    原事件 payload 不变（retracted 是投影折叠出的状态，不是覆写）。
    """
    projection = ledger_engine.build_projection()
    original = next((e for e in projection["events"] if e.get("event_id") == event_id), None)
    if original is None:
        raise KeyError(event_id)
    if original.get("status", "active") == "retracted":
        return {"event_id": event_id, "status": "retracted", "already": True,
                "revision_event_id": None}
    retraction, revision = build_retraction(
        original["message"], reason=reason, replacement_comment=replacement_comment,
    )
    messages = [retraction] + ([revision] if revision is not None else [])
    revision_event_id = revision["payload"]["event_id"] if revision is not None else None
    if db is not None:
        # 先落独立事件记录（DB 可审计、可恢复），commit 后幂等投递到台账
        journal = TrainingRecord(
            source_type="feedback", feature="feedback_retraction",
            goal_id=original["message"]["identity"].get("goal_id"),
            tutorial_id=original["message"]["identity"].get("tutorial_id"),
            rating=None, tags=jdump([]), comment=reason or "",
            model_provider="user", model_name="feedback_retraction",
            context_json=jdump({
                "feedback_retraction": {"target_event_id": event_id, "reason": reason,
                                        "replacement_comment": replacement_comment},
                "feedback_evidence": {"delivery_status": "pending", "pending_ops": [
                    {"kind": "event", "message": m, "governance": False} for m in messages]},
            }),
        )
        db.add(journal)
        db.commit()  # 先提交撤回意图，崩溃后可经恢复入口补投
        _deliver_event(db, journal)
        _tombstone_consumption(event_id)
    else:
        now = _now()
        ledger_engine.append_lines(
            [_event_entry(m, kind=_event_kind(m)) for m in messages])
        _tombstone_consumption(event_id)
    return {"event_id": event_id, "status": "retracted", "already": False,
            "retraction_event_id": retraction["payload"]["event_id"],
            "revision_event_id": revision_event_id}


def _tombstone_consumption(event_id: str) -> None:
    """任务书 05 切片 C：撤回产生 tombstone——未派发消费随撤回取消（追加，历史保留）。

    pending_review / needs_triage 的重评与 open/tracked 的回归待办 → cancelled；
    已 reviewed/failed 的历史结论保留不动；不自动复活任何 rejected 原理。
    """
    projection = ledger_engine.build_projection()
    updates: list[dict] = []
    for row in projection["revaluations"]:
        if row.get("event_id") != event_id:
            continue
        if str(row.get("status") or "") in ("pending_review", "needs_triage", "dispatched"):
            updates.append({"kind": "revaluation_update", "at": _now(),
                            "request_id": row.get("request_id"),
                            "update": {"status": "cancelled",
                                       "note": "源事件已撤回（tombstone）；未派发消费取消。"}})
    for row in projection["regressions"]:
        if row.get("event_id") != event_id:
            continue
        if str(row.get("status") or "open") in ("open", "tracked"):
            updates.append({"kind": "regression_update", "at": _now(),
                            "todo_id": row.get("todo_id"),
                            "update": {"status": "cancelled",
                                       "note": "源事件已撤回（tombstone）；回归待办关闭。"}})
    if updates:
        ledger_engine.append_lines(updates)


def _event_summary(entry: dict) -> dict:
    message = entry.get("message") or {}
    payload = message.get("payload") or {}
    identity = message.get("identity") or {}
    ext = message.get("extensions") or {}
    return {"event_id": entry.get("event_id"), "created_at": entry.get("created_at"),
            "status": entry.get("status", "active"), "occurrences": int(entry.get("occurrences", 1)),
            "outcome": payload.get("outcome"), "origin": payload.get("origin"),
            "rating": ext.get("rating"), "tags": ext.get("tags", []),
            "text": payload.get("text", ""),
            "goal_id": identity.get("goal_id"), "tutorial_id": identity.get("tutorial_id"),
            "section_id": identity.get("unit_id"), "version": identity.get("version"),
            "located_count": len(ext.get("located_problems") or []),
            "guidance_bindings": ext.get("guidance_bindings") or [],
            "event_kind": entry.get("kind") or "feedback",
            "binding": ext.get("binding") or None}


def list_evidence(limit: int = 50) -> dict:
    projection = ledger_engine.build_projection()
    window = max(1, min(int(limit), 200))
    return {
        "total": len(projection["events"]),
        "events": [_event_summary(e) for e in reversed(projection["events"])][:window],
        "revaluations": list(reversed(projection["revaluations"]))[:window],
        "regressions": list(reversed(projection["regressions"]))[:window],
        "total_events_logged": int(sum(projection["shards"].values())),
        "archived_shards": sorted(projection["shards"]),
        "governance_note": "候选证据与重评请求默认只产生、不自动分析；撤回/修订历史保留，评分/赞语不直接确认经验。",
    }


def rebuild_projection() -> dict:
    """从事件日志重建投影并返回摘要（观测/自检入口）。"""
    projection = ledger_engine.build_projection()
    return {"total": len(projection["events"]),
            "events": [_event_summary(e) for e in projection["events"]],
            "revaluations": projection["revaluations"],
            "regressions": projection["regressions"],
            "shards": projection["shards"]}
