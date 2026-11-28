# -*- coding: utf-8 -*-
"""质量发现（ReviewFinding）与修订台账：把各检查者输出统一成可定位、可失效的发现。

01 任务书步骤 3/4 的核心结构。设计目标（与 S1 误报修复互补）：

- **每条最终判定能定位到具体正文**：finding 携带 unit_id / question_numbers /
  location / content_hash（sha256，对发现所引用的正文片段计算）；
- **证据绑定最终版本**：正文被后续修改（L1 回写、审查修复、L0 格式修复）后，
  按 location 粒度重算 hash——旧证据描述的不是当前正文时标 stale，有新证据明确
  覆盖时标 satisfied/superseded，历史保留；第 9 题的发现不因第 5 题被修复而失效，
  第 5 题的旧证据也不能继续给改写后的正文下结论；
- **跨模块统一修复记账**：记账键 unit_id（与 BudgetController.unit_repair_limit
  同键同语义），每生成单元模型内容修复最多一次；并行检查经同一台账申请额度不重复
  消耗；L0 可证明格式修复单独记账；保护器拒绝后本轮修复结束，不换模块绕道。

依赖方向：本模块只依赖 exam_check（卷面解析）；proposition_generated /
exam_verify / generation_acceptance 反向消费本模块，避免循环导入。
对外协议不变：findings 明细只进 generation.v1 AcceptanceReport 的
``extensions.findings``，不造 generation.v2。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import exam_check

# finding 状态：violated=确定违反（当前正文仍成立）；satisfied=已修复（新证据确认）；
# undecidable=待核实（证据不足或无法唯一定位）；stale=证据基于旧正文，对当前正文失效；
# superseded=已被其他发现/修订取代。
FINDING_STATUSES = {"violated", "satisfied", "undecidable", "stale", "superseded"}

# 修订 actor：l0_fix=L0 可证明格式修复；review_repair=审查定向修复（模型）；
# l1_rewrite=L1 盲解/工具答案回写；final_review=最终复核（只查不改）。
REPAIR_ACTORS = {"l0_fix", "review_repair", "l1_rewrite", "final_review"}

# 题号文本定位：与 repair_guard 同源（本模块是唯一定义处）。
_QUESTION_TEXT_RE = re.compile(r"(?:第\s*(\d+)\s*题|题\s*(\d+))")


def content_hash(text: str) -> str:
    """正文片段 hash（sha256，``sha256:`` 前缀；与 B 契约 source_hash 口径一致）。"""
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def question_numbers_from_defect(defect: dict) -> list[int]:
    """finding 题号：显式字段优先，其次 reason/evidence 文本定位（repair_guard 同源）。"""
    numbers: list[int] = []
    if not isinstance(defect, dict):
        return numbers
    number = defect.get("number", defect.get("question_number"))
    if isinstance(number, int):
        numbers.append(number)
    for field_name in ("reason", "evidence"):
        for number_text in _QUESTION_TEXT_RE.findall(str(defect.get(field_name, ""))):
            value = int(number_text[0] or number_text[1])
            if value not in numbers:
                numbers.append(value)
    return numbers


def exam_unit_id(section: dict, index: int) -> str:
    """生成单元 id：``<section.id 或空>:<块索引>``（与 review_generated 既有键一致）。"""
    return f"{(section or {}).get('id') or ''}:{index}"


@dataclass
class ReviewFinding:
    """一条可定位、可失效的质量发现。字段语义见 contracts/a-quality-findings.md §3。"""

    finding_id: str
    check_id: str                       # 消费方 acceptance check（structure/correctness/…）
    unit_id: str
    question_numbers: list[int] = field(default_factory=list)
    location: str = "paper"             # answers/<n> | questions/<n> | paper | unit
    severity: str = "blocking"          # blocking | advisory
    status: str = "violated"
    evidence_method: str = "model_static"
    content_hash: str | None = None     # 对发现所引用正文片段的 sha256（发现时刻）
    reason: str = ""
    evidence: str = ""
    limitations: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    source: str = ""                    # 溯源：review|recheck|l0|l1|cross
    resolved_by: str = ""               # satisfied/superseded 时的处置依据（修订/新发现）
    replaced_by: str = ""               # superseded 时指向的新 finding_id
    check_spec: str = ""                # l0 发现的规范名（exam/exercise），最终复核重判用
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "finding_id": self.finding_id, "check_id": self.check_id, "unit_id": self.unit_id,
            "question_numbers": list(self.question_numbers), "location": self.location,
            "severity": self.severity, "status": self.status,
            "evidence_method": self.evidence_method, "content_hash": self.content_hash,
            "reason": self.reason, "evidence": self.evidence,
            "limitations": list(self.limitations), "missing_evidence": list(self.missing_evidence),
            "source": self.source, "resolved_by": self.resolved_by,
            "replaced_by": self.replaced_by, "check_spec": self.check_spec, "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReviewFinding":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------------------------------------------------------------- 适配器

def _answer_location(paper, numbers: list[int]) -> tuple[str, str | None]:
    """给出 findings 的 location 与发现时刻的 content_hash（按答案片段）。"""
    if numbers:
        n = numbers[0]
        text = (paper.answers.get(n, "") if paper is not None else "") or ""
        return f"answers/{n}", content_hash(text)
    return "paper", None


def finding_from_review(defect: dict, *, unit_id: str, paper=None, body: str = "",
                        finding_id: str, source: str = "review") -> ReviewFinding:
    """模型审查/复核缺陷 → ReviewFinding。旧模型结果兼容解析 reason+evidence。

    不能唯一定位（提不出题号）→ question_numbers=[]、location=paper、status=undecidable、
    missing_evidence 标注缺题号——保留发现但不扩大修改范围（repair_guard 会拒绝对
    非目标长答案的改写）。
    """
    numbers = question_numbers_from_defect(defect)
    if numbers:
        location, digest = _answer_location(paper, numbers)
        if re.search(r"题面|题干|设问|选项.*(?:缺失|不完备|不唯一)|条件.*(?:缺失|歧义)", str(defect.get("reason") or "")):
            location = f"questions/{numbers[0]}"
            digest = content_hash("\n".join(paper.blocks.get(numbers[0]) or [])) if paper else None
        status = "violated"
        missing: list[str] = []
    else:
        location, digest = "paper", content_hash(body)
        status = "undecidable"
        missing = ["question_number"]
    return ReviewFinding(
        finding_id=finding_id, check_id="correctness", unit_id=unit_id,
        question_numbers=numbers, location=location,
        severity=str(defect.get("severity") or "blocking"),
        status=status, evidence_method="model_static", content_hash=digest,
        reason=str(defect.get("reason") or ""), evidence=str(defect.get("evidence") or ""),
        limitations=[str(x) for x in defect.get("limitations") or []],
        missing_evidence=missing, source=source,
    )


def finding_from_l0(violation: dict, *, unit_id: str, paper=None, body: str = "",
                    finding_id: str, spec: str = "") -> ReviewFinding:
    """L0 确定性违规 → structure 发现（deterministic，账目/排版/结构事实）。"""
    code = str(violation.get("code") or "")
    numbers = [int(violation["question_number"])] if violation.get("question_number") else []
    if numbers:
        location, digest = f"questions/{numbers[0]}", None
        if paper is not None:
            digest = content_hash("\n".join(paper.blocks.get(numbers[0]) or []))
    else:
        location, digest = "paper", content_hash(body)
    return ReviewFinding(
        finding_id=finding_id, check_id="structure", unit_id=unit_id,
        question_numbers=numbers, location=location,
        severity=str(violation.get("severity") or "blocking"),
        status="violated" if not violation.get("fixed") else "satisfied",
        evidence_method="deterministic", content_hash=digest,
        reason=f"[{code}] {violation.get('rule', '')}",
        evidence=str(violation.get("detail") or ""),
        source="l0", resolved_by="l0_fix" if violation.get("fixed") else "",
        check_spec=str(spec or ""),
    )


def finding_from_l1_defect(defect: dict, *, unit_id: str, paper=None, finding_id: str) -> ReviewFinding:
    """L1 盲解/工具缺陷 → correctness 发现。工具来源（sympy 转写）标 tool 方法。"""
    reasoning = str(defect.get("reasoning") or "")
    method = "tool" if "工具验证" in reasoning or "sympy" in reasoning else "model_static"
    numbers = [int(defect["number"])] if defect.get("number") else []
    if numbers:
        location, digest = f"answers/{numbers[0]}", None
        if paper is not None:
            digest = content_hash((paper.answers.get(numbers[0]) or ""))
        if defect.get("location") == "question" or defect.get("question_defect"):
            location = f"questions/{numbers[0]}"
            digest = content_hash("\n".join(paper.blocks.get(numbers[0]) or [])) if paper else None
    else:
        location, digest = "paper", None
    return ReviewFinding(
        finding_id=finding_id, check_id="correctness", unit_id=unit_id,
        question_numbers=numbers, location=location,
        severity="blocking", status="violated", evidence_method=method,
        content_hash=digest,
        reason=str(defect.get("defect") or defect.get("reason") or ""),
        evidence=reasoning, source="l1",
    )


# ---------------------------------------------------------------- 修订台账

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FindingLedger:
    """run 级修订登记 + 发现台账 + 跨模块修复记账（内存对象，随 quality_report 落盘）。

    生命周期：质量阶段开始时创建（_apply_exam_quality 补丁），随 review_generated /
    run_exam_quality / verify_content / quality_acceptance 传递；不传时各入口内部
    临时建账，行为不变（旧调用者兼容）。
    """

    def __init__(self, *, tutorial_id: str = "", version: int | None = None,
                 model_repair_limit: int = 1):
        self.tutorial_id = str(tutorial_id or "")
        self.version = version
        self.model_repair_limit = max(0, int(model_repair_limit))
        self.units: dict[str, dict] = {}
        self.findings: list[ReviewFinding] = []
        self.revisions: list[dict] = []
        # 修复记账：unit_id -> {"model_content_attempts": int, "outcome": str,
        #                      "l0_fixes": int, "l1_rounds": int, "log": [..]}
        # 记账键 unit_id 与 c-orchestration BudgetController.unit_repair_limit 同键同语义。
        self.repairs: dict[str, dict] = {}
        self._seq = 0

    # ---- 单元登记 ----
    def register_unit(self, unit_id: str, *, title: str = "", text: str = "", kind: str = "exam") -> None:
        self.units.setdefault(unit_id, {"title": title, "text": text, "kind": kind})

    # ---- 修订登记（01 任务书：before/after hash、changed_locations、actor、finding_ids）----
    def register_revision(self, *, actor: str, before_text: str, after_text: str,
                          changed_locations: list[str], finding_ids: list[str] | None = None) -> dict | None:
        """登记一次内容修改；正文未变化时不产生修订（返回 None）。"""
        if actor not in REPAIR_ACTORS:
            raise ValueError(f"未知修订 actor：{actor}")
        before_hash, after_hash = content_hash(before_text), content_hash(after_text)
        if before_hash == after_hash:
            return None
        self._seq += 1
        revision = {"revision_id": f"r-{self._seq}", "actor": actor,
                    "before_hash": before_hash, "after_hash": after_hash,
                    "changed_locations": [str(x) for x in changed_locations],
                    "finding_ids": [str(x) for x in (finding_ids or [])], "at": _now()}
        self.revisions.append(revision)
        return revision

    # ---- 跨模块修复记账（契约 §6：查询/扣减接口）----
    def repair_state(self, unit_id: str) -> dict:
        return self.repairs.setdefault(unit_id, {"model_content_attempts": 0, "outcome": "",
                                                 "l0_fixes": 0, "l1_rounds": 0, "log": []})

    def request_model_repair(self, unit_id: str, *, purpose: str = "") -> bool:
        """申请一次模型内容修复额度。

        每生成单元最多一次；已申请过（无论成败）或已有结论的单元一律拒绝——
        并行检查不重复消耗额度，保护器拒绝后本轮修复结束，不换另一模块绕道。
        """
        state = self.repair_state(unit_id)
        if state["model_content_attempts"] >= self.model_repair_limit:
            state["log"].append({"at": _now(), "action": "denied", "purpose": purpose})
            return False
        state["model_content_attempts"] += 1
        state["log"].append({"at": _now(), "action": "reserved", "purpose": purpose})
        return True

    def settle_model_repair(self, unit_id: str, *, outcome: str, revision_id: str = "") -> None:
        """登记修复结果：applied / rejected / deferred / skipped。"""
        state = self.repair_state(unit_id)
        state["outcome"] = outcome
        if revision_id:
            state["revision_id"] = revision_id
        state["log"].append({"at": _now(), "action": "settled", "outcome": outcome})

    def record_l0_fixes(self, unit_id: str, count: int) -> None:
        """L0 可证明格式修复单独记账，不占模型内容修复额度。"""
        if count:
            state = self.repair_state(unit_id)
            state["l0_fixes"] += int(count)
            state["log"].append({"at": _now(), "action": "l0_fix", "count": int(count)})

    def record_l1_round(self, unit_id: str, *, applied: int) -> None:
        state = self.repair_state(unit_id)
        state["l1_rounds"] += 1
        if applied:
            state["log"].append({"at": _now(), "action": "l1_rewrite", "applied": int(applied)})

    def model_repair_used(self, unit_id: str) -> bool:
        state = self.repairs.get(unit_id) or {}
        return bool(state.get("model_content_attempts") or state.get("outcome") in {"applied", "rejected"})

    # ---- 发现管理 ----
    def add_finding(self, finding: ReviewFinding) -> ReviewFinding:
        self.findings.append(finding)
        return finding

    def new_finding_id(self) -> str:
        self._seq += 1
        return f"f-{self._seq}"

    def _by_id(self, finding_id: str) -> ReviewFinding | None:
        return next((f for f in self.findings if f.finding_id == finding_id), None)

    def mark_satisfied(self, finding_id: str, *, resolved_by: str) -> bool:
        finding = self._by_id(finding_id)
        if finding is None:
            return False
        finding.status = "satisfied"
        finding.resolved_by = resolved_by
        return True

    def supersede(self, finding_id: str, *, replaced_by: str, resolved_by: str = "") -> bool:
        finding = self._by_id(finding_id)
        if finding is None:
            return False
        finding.status = "superseded"
        finding.replaced_by = replaced_by
        finding.resolved_by = resolved_by or finding.resolved_by
        return True

    # ---- 最终对齐（01 任务书：最终 findings hash 必须与导出正文匹配）----
    def finalize(self, content: dict) -> dict:
        """按最终正文重算各 finding 的 location hash，处置失效证据并产出 extensions 形态。

        - hash 仍一致（或该发现无正文 hash）：保持原状态——证据仍描述当前正文；
        - hash 不一致且有修订/新证据覆盖该发现（finding_ids 或 changed_locations 命中）：
          satisfied（处理成功，如 L1 改正答案）；
        - hash 不一致且无覆盖：stale——旧证据不得再给当前正文下结论；
        - 历史（stale/superseded/satisfied 视图外的处理痕迹）保留，不删除不改写。
        """
        sections = [s for s in (content.get("sections") or []) if isinstance(s, dict)]
        finals: dict[str, dict] = {}
        for section in sections:
            unit_key = exam_check.section_unit_id(section)
            body = exam_check.section_body(section)
            finals[unit_key] = {"body": body, "paper": None}
            if exam_check.is_exam_markdown(body):
                finals[unit_key]["paper"] = exam_check.parse_paper(body, str(section.get("title", "")))
            for index, block in enumerate(section.get("content") or []):
                if isinstance(block, dict) and block.get("body") and exam_unit_id(section, index) != unit_key:
                    block_body = str(block["body"])
                    finals[exam_unit_id(section, index)] = {
                        "body": block_body, "paper": exam_check.parse_paper(block_body)}
        extra = content.get("extra_exercises")
        if isinstance(extra, dict) and str(extra.get("markdown", "") or "").strip():
            finals["extra_exercises"] = {"body": str(extra.get("markdown", "")), "paper": None}

        covered: dict[str, str] = {}
        revision_actors: dict[str, str] = {}
        for revision in self.revisions:
            revision_actors[revision["revision_id"]] = revision["actor"]
            for finding_id in revision["finding_ids"]:
                covered[finding_id] = revision["revision_id"]

        for finding in self.findings:
            if finding.status in {"satisfied", "superseded", "stale"}:
                continue
            final = finals.get(finding.unit_id)
            if final is None:
                continue
            digest = self._hash_at(final, finding)
            if digest is None or digest == finding.content_hash:
                continue  # 证据仍描述当前正文（或无片段 hash 可对齐）
            if finding.finding_id in covered:
                finding.status = "undecidable" if revision_actors[covered[finding.finding_id]] == "review_repair" else "satisfied"
                finding.resolved_by = covered[finding.finding_id] if finding.status == "satisfied" else ""
                # 已解决发现的 hash 刷新到最终正文：evidence_refs 必须定位最终版本，
                # 原始证据留在 revisions（before_hash）与历史里可追溯。
                finding.content_hash = digest
                finding.detail = ("修复已应用，但没有复核证据确认原缺陷解决。" if finding.status == "undecidable"
                                  else "正文已修改且存在覆盖本发现的修订；以修订后证据为准。")
                continue
            if finding.source == "l0" and finding.location == "paper" and finding.check_spec:
                # L0 是确定性检查：正文变了就直接**重判**最终卷面（比标 stale 更诚实，
                # 也比拿旧 detail 冒充当前结论诚实）。代码已不存在 → 由复核关闭。
                # 重判集合与 L0 管道一致：check_paper（账目/结构）+ check_answer_detail
                # （E012/E015 答案区检查在后者里）。
                paper = final.get("paper") or exam_check.parse_paper(final.get("body") or "", "")
                spec = finding.check_spec or "exam"
                codes = {v.code for v in exam_check.check_paper(paper, spec=spec)}
                codes |= {v.code for v in
                          exam_check.check_answer_detail(final.get("body") or "", paper, spec=spec)}
                code = (finding.reason or "").split("]")[0].strip("[").strip()
                if code and code not in codes:
                    finding.status = "satisfied"
                    finding.resolved_by = "l0_recheck"
                    finding.detail = "最终卷面确定性重判未再出现该违规；以重判为准。"
                else:
                    finding.content_hash = digest
                    finding.detail = "正文在判定后被修改；已在最终卷面上确定性重判，违规仍成立。"
                continue
            finding.status = "stale"
            finding.detail = "发现所引用的正文在判定后被修改（location hash 不一致）；旧证据不再描述当前正文，需重检或以新证据为准。"

        current = [f for f in self.findings if f.status not in {"stale", "superseded"}]
        history = [f for f in self.findings if f.status in {"stale", "superseded"}]
        return {
            "version": self.version,
            "final_text_hash": content_hash("\n".join(v["body"] for v in finals.values())),
            "items": [f.to_dict() for f in current],
            "history": [f.to_dict() for f in history],
            "revisions": [dict(r) for r in self.revisions],
            "repairs": {k: dict(v) for k, v in self.repairs.items()},
        }

    @staticmethod
    def _hash_at(final: dict, finding: ReviewFinding) -> str | None:
        """按 location 粒度重算最终正文 hash；定位不到返回 None（不对齐、不误判）。"""
        paper = final.get("paper")
        location = finding.location
        if location.startswith("answers/"):
            if paper is None:
                return None
            try:
                number = int(location.rsplit("/", 1)[1])
            except ValueError:
                return None
            return content_hash(paper.answers.get(number) or "")
        if location.startswith("questions/"):
            if paper is None:
                return None
            try:
                number = int(location.rsplit("/", 1)[1])
            except ValueError:
                return None
            return content_hash("\n".join(paper.blocks.get(number) or []))
        if location == "paper":
            return content_hash(final.get("body") or "")
        return None


# ---------------------------------------------------------------- 汇总输出

def evidence_refs_for(findings, check_id: str, *, tutorial_id: str,
                      version: int | None = None) -> list[dict]:
    """某 acceptance check 的 evidence_refs：generated 版本 + 单元/位置 + finding id。

    ``findings`` 接受 ReviewFinding 或其 to_dict() 形态（finalize 返回 dict）。
    只为 violated/satisfied 的发现产出引用——undecidable/stale 的缺证据或过期证据
    不冒充可定位判定。
    """
    refs: list[dict] = []
    seen: set[str] = set()
    for finding in findings:
        data = finding.to_dict() if isinstance(finding, ReviewFinding) else finding
        if data.get("check_id") != check_id or data.get("finding_id") in seen:
            continue
        if data.get("status") not in {"violated", "satisfied"}:
            continue
        seen.add(data["finding_id"])
        refs.append({"kind": "generated", "id": str(tutorial_id or "legacy"),
                     "version": str(version) if version is not None else None,
                     "hash": data.get("content_hash"),
                     "location": f"unit:{data.get('unit_id')}/{data.get('location')}"
                                 f"#finding:{data.get('finding_id')}"})
    return refs
