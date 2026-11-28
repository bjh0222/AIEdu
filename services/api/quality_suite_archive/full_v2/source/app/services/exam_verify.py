# -*- coding: utf-8 -*-
"""L1 试卷盲解核验编排：把 exam_check（L0 账目）之上的「内容正确性」检查串成流程。

分工（这是实测数据逼出来的，不是理想设计）：
- L0 ``exam_check``：题号 / 分值 / 答案集合等**账目**，纯代码、零 token；
- L1 ``exam_verify``：答案**内容对不对**，靠「独立再解一遍」，每卷 1~2 次模型往返。

为什么 L0 不够：最新那份 5 套微积分卷，账目（L0）几乎全绿，客观验算 67 题却有 21 处
实质错误——y''(0) 算错、比值算错、一道选择题四个选项全错。账目对不代表答案对。

为什么必须有仲裁（arbitrate）而不是直接采信盲解：盲解模型自己也会算错。直接按盲解
改答案，等于用一次未经验证的计算覆盖另一次未经验证的计算。冲突必须再解一遍。

并发安全：每份卷的核验在**独立 Session + 独立 AIService** 里跑，绝不跨线程共享一个
AIService（它有可变的 usage_last 与会话绑定的 db）。这与 tutorials._companion_task、
_generate_section_task 的既有模式一致。

「自动改，但只改一次」：本模块对每份卷**只跑一轮**修正，不迭代。改完的结果与所有
判定（含未改写的）都写进 quality_report，让用户能看到改了什么、为什么没改某些。
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import exam_check as exam_check_service
from .quality_findings import FindingLedger, finding_from_l0, finding_from_l1_defect

logger = logging.getLogger("aiedu")

# 盲解核验的模型并发路数。默认比章节生成保守（章节是 9 路）：核验发生在全部章节
# 完成之后，此时若用满并发会和知识提取等后台任务抢额度。可用环境变量覆盖。
VERIFY_WORKERS = max(1, int(os.environ.get("AIEDU_VERIFY_CONCURRENCY", "3")))


def verify_paper_with_ai(ai, goal, discipline, title: str, markdown: str, spec: str) -> dict:
    """对**一份卷**跑完整的 L1 核验：盲解 → 检测冲突 → 仲裁 → 一轮回写。

    这是纯逻辑，不碰 DB / Session，接收一个 ai-like 对象（生产是 AIService，测试可注入
    假对象）。把编排逻辑与 I/O 管道分离，是为了让它可单测——否则测这一段就得建真库、
    配真模型。

    返回报告 dict；被回写修正后的正文放在内部字段 ``_markdown``（调用方回写后删除）。
    核验是质量增强环节，**任何一步失败都只在报告里留痕、不抛异常**，不能因为它不完整
    而让整份卷子交付失败。
    """
    report: dict = {
        "title": title, "spec": spec, "verified": 0, "conflicts": [],
        "defects": [], "applied": [], "skipped": [], "changed": False, "error": "",
    }
    if getattr(ai, "is_demo", False):
        report["error"] = "演示模式不做盲解核验"
        return report

    paper = exam_check_service.parse_paper(markdown, title)
    payload = exam_check_service.build_verification_payload(paper)
    if not payload:
        return report
    report["verified"] = len(payload)

    # ---- 第一层：工具验证（确定性，优先于盲解）----
    # 依据：T1 论文「记忆密集型验证必须卸载给工具，模型即使被蒸馏也做不好」；
    # Nine Judges「同模型多次调用独立信息增益≈0」。工具给出的 refuted 来自 sympy
    # 可复算，比盲解仲裁（模型自评 confidence=high）可信得多，所以工具能判定的题
    # 直接采信，**不再浪费一次盲解往返**；只有工具判不了（undecidable）的残差才走盲解。
    # 只对 capability=symbolic（数学）做工具转写：化学/生物按「简化处理」回落盲解。
    tool_verdicts, tool_settled = _tool_verify_paper(ai, goal, discipline, title, paper, report)

    # ---- 第二层：盲解核验，只针对工具未判定的残差 ----
    residual = [it for it in payload if it["number"] not in tool_settled]
    defects: list[dict] = []
    conflicts: list[dict] = []
    blind_verdicts: list[dict] = []
    results: list[dict] = []
    if residual:
        try:
            results = ai.verify_questions(goal, discipline, title, residual)
        except Exception as exc:  # noqa: BLE001
            report["error"] = f"盲解核验调用失败：{exc.__class__.__name__}: {str(exc)[:120]}"
            results = []
        if results:
            conflicts, defects = exam_check_service.detect_conflicts(paper, results)
            if conflicts:
                try:
                    blind_verdicts = ai.arbitrate_answers(goal, discipline, title, conflicts)
                except Exception as exc:  # noqa: BLE001
                    report["error"] = f"答案仲裁调用失败：{exc.__class__.__name__}: {str(exc)[:120]}"

    report["defects"] = defects
    report["conflicts"] = conflicts

    # 工具 verdict 与盲解 verdict 按题号不相交（残差才盲解），可直接合并；
    # 一轮修正：只回写 high 置信的客观题答案（工具 refuted=high；盲解仲裁 high 才采信）。
    # 工具“未决”只代表该方法没算出结论；后续盲解/仲裁已解决时不再遗留同题未决项。
    disputed = {int(it["number"]) for it in conflicts + defects if it.get("number") is not None}
    blind_settled = {int(it["number"]) for it in results if isinstance(it, dict)
                     and str(it.get("number", "")).isdigit() and it.get("solved")
                     and str(it.get("confidence", "")).lower() in ("high", "medium")
                     and int(it["number"]) not in disputed
                     and paper.answers.get(int(it["number"]))}
    blind_settled.update(int(it["number"]) for it in blind_verdicts if it.get("number") is not None
                         and it.get("confidence") == "high" and it.get("verdict") != "undecidable")
    report["tool_unresolved"] = [v for v in tool_verdicts if v.get("verdict") == "undecidable"]
    all_verdicts = [v for v in tool_verdicts if v.get("verdict") != "undecidable"
                    or int(v["number"]) not in blind_settled] + blind_verdicts
    if not all_verdicts:
        return report
    new_md, applied, skipped = exam_check_service.apply_answer_fixes(markdown, paper, all_verdicts)
    report["applied"] = applied
    report["skipped"] = skipped
    # A confirmed error remains blocking even when replacing a long solution is unsafe.
    for entry in skipped:
        if entry.get("confidence") == "high" and entry.get("verdict") in {"paper_wrong", "both_wrong", "question_broken"}:
            report["defects"].append({
                **entry, "defect": entry.get("question_defect") or entry.get("reasoning") or "仲裁确认卷面错误，需定向修复",
                "location": "question" if entry.get("verdict") == "question_broken" else "answer",
            })
    report["changed"] = new_md != markdown
    report["_markdown"] = new_md
    return report


def _tool_verify_paper(ai, goal, discipline, title: str, paper, report: dict) -> tuple[list[dict], set]:
    """对一份卷做 sympy 工具验证；返回 (可直接用于改写的 verdicts, 已被工具判定的题号集合)。

    只在学科 verify_capability == "symbolic"（数学）时启用：这是 sympy 已验证有效、
    也是本项目主战场（数学/计算机，其中数学可符号验算）的路径。化学（symbolic_partial）
    与生物（none）按用户「化生简化处理」的要求回落到盲解，不在这里强行转写。

    任何失败都只留痕、返回已判定的部分，绝不抛出——工具验证是增强环节。
    """
    from . import tool_verify as tool_verify_service

    try:
        capability = tool_verify_service.resolve_capability(getattr(goal, "domain_key", None))
    except Exception:  # noqa: BLE001
        capability = "none"
    if capability != "symbolic":
        return [], set()

    # 转写载荷：题目 + 卷面答案（转写是翻译任务，需要答案，见 transcribe_for_tools）
    items = []
    for q in paper.questions:
        answer = str(paper.answers.get(q.number, "") or "").strip()
        if not answer:
            continue
        stem = "\n".join(paper.blocks.get(q.number) or [q.stem])
        items.append({"number": q.number, "question": stem, "answer": answer, "type": q.kind})
    if not items:
        return [], set()

    try:
        transcriptions = ai.transcribe_for_tools(goal, discipline, title, items)
    except Exception as exc:  # noqa: BLE001
        report.setdefault("tool_errors", []).append(f"转写失败：{str(exc)[:100]}")
        return [], set()
    if not transcriptions:
        return [], set()

    tool_items = [
        {**it, "sympy_code": transcriptions[it["number"]]}
        for it in items if it["number"] in transcriptions
    ]
    try:
        results = tool_verify_service.run_batch(
            tool_items, domain_key=getattr(goal, "domain_key", None), max_workers=3,
        )
    except Exception as exc:  # noqa: BLE001
        report.setdefault("tool_errors", []).append(f"工具执行失败：{str(exc)[:100]}")
        return [], set()

    verdicts = tool_verify_service.to_fix_verdicts(results)
    report["tool_verified"] = len(results)
    report["tool_confirmed"] = sum(1 for r in results if r.get("verdict") == "confirmed")
    report["tool_refuted"] = sum(1 for r in results if r.get("verdict") == "refuted")
    # 工具给出确定结论（confirmed→blind_wrong / refuted→paper_wrong）的题号 = 已判定，
    # 这些题不再走盲解；undecidable 的题号留在残差里交给盲解。
    settled = {
        int(v["number"]) for v in verdicts
        if v.get("verdict") in ("paper_wrong", "blind_wrong")
    }
    return verdicts, settled


def _verify_one_paper(
    *, goal_id: str, tutorial_id: str, job_id: str,
    title: str, markdown: str, spec: str, discipline,
    run_id=None, budget=None,
) -> dict:
    """生产入口：在独立 Session / AIService 里跑 ``verify_paper_with_ai``。

    独立会话是并发安全的前提（AIService 有可变的 usage_last 与会话绑定的 db，
    不可跨线程共享），与 tutorials._companion_task 的既有模式一致。
    """
    from ..db import SessionLocal
    from ..llm.service import AIService
    from ..models import LearningGoal

    db = SessionLocal()
    try:
        goal = db.get(LearningGoal, goal_id)
        if goal is None:
            return {"title": title, "spec": spec, "verified": 0, "conflicts": [],
                    "defects": [], "applied": [], "skipped": [], "changed": False,
                    "error": "学习目标不存在"}
        ai = AIService(db, goal_id=goal_id, tutorial_id=tutorial_id, job_id=job_id,
                       run_id=run_id, budget=budget)
        report = verify_paper_with_ai(ai, goal, discipline, title, markdown, spec)
        db.commit()   # 落 ModelCallLog / TrainingRecord（核验失败时 commit 无害）
        return report
    except Exception as exc:  # noqa: BLE001  单卷核验失败不影响其它卷与整体交付
        db.rollback()
        logger.warning("盲解核验失败（%s）：%s", title, exc)
        return {"title": title, "spec": spec, "verified": 0, "conflicts": [],
                "defects": [], "applied": [], "skipped": [], "changed": False,
                "error": f"{exc.__class__.__name__}: {str(exc)[:120]}"}
    finally:
        db.close()


def recompute_cross(content: dict) -> list[dict]:
    """L1 改完答案后，基于**最终内容**重算跨卷一致性（E017/E018）。

    顺序不能反：L0 先修账目、L1 再改客观答案，两次都会改动卷内容。若沿用 L0 阶段
    算出的 cross 结果，E018（同母题不同答案）反映的是**改答案之前**的状态——
    可能 L1 已经把冲突答案改成一致，报告却仍说冲突；也可能反过来。所以跨卷检查
    必须在所有内容修改结束后、落库前重跑一次。
    """
    format_spec = content.get("format_spec") or {}
    papers: list[tuple[str, "object"]] = []
    for section in content.get("sections") or []:
        if not isinstance(section, dict):
            continue
        body = exam_check_service.section_body(section)
        spec = exam_check_service.classify_spec(str(section.get("title", "")), format_spec, body)
        if spec == "exam" and exam_check_service.is_exam_markdown(body):
            papers.append((str(section.get("title", "")), exam_check_service.parse_paper(body)))
    if len(papers) < 2:
        return []
    return [v.to_dict() for v in exam_check_service.check_cross_paper(papers)]


def run_exam_quality(
    content: dict, *, goal_id: str, tutorial_id: str, job_id: str, discipline,
    verify_enabled: bool = True,
    ledger: FindingLedger | None = None, on_stage=None, run_id=None, budget=None,
) -> dict:
    """试卷质量的统一入口：L0 账目 → L1 盲解 → 跨卷重算，全部就地改进 content。

    返回一份汇总，供调用方合并进 quality_report（checks / issues / exam_check）。
    这是 tutorials.py 与 exam_check/exam_verify 之间的**唯一**编排点，避免各调用点
    自己拼装三层、漏掉跨卷重算或搞错顺序。

    - L0（exam_check.apply_to_content / apply_to_extra_exercises）：纯代码，零 token；
    - L1（verify_content）：每份卷 1~2 次模型往返，只在 verify_enabled 且非演示模式跑；
    - 跨卷重算（recompute_cross）：必须在 L1 之后，基于最终内容。

    ``ledger``：共享修订台账（01 任务书 S2）。传入时 L0 修复/L1 回写登记修订并记账，
    发现统一进台账供验收层做最终 hash 对齐；不传时本地建账并随 summary 返回，
    旧调用者行为不变。``on_stage``：c-orchestration §2 的可选阶段回调。
    """
    ledger = ledger if ledger is not None else FindingLedger(tutorial_id=tutorial_id)
    summary: dict = {"l0": {}, "l1": {}, "cross": []}

    def _emit(stage: str, status: str, **meta) -> None:
        if on_stage is None:
            return
        try:
            on_stage(stage, status, meta)
        except Exception:  # noqa: BLE001  回调只做观测（双保险，与 C 契约一致）
            logger.warning("on_stage 回调失败（stage=%s status=%s）", stage, status, exc_info=True)

    # ---- L0：账目校验与修复（含额外练习）----
    _emit("verifying", "running", detail="L0 账目校验开始")
    l0 = exam_check_service.apply_to_content(content)
    extra = exam_check_service.apply_to_extra_exercises(content)
    if extra is not None:
        l0["extra_exercises"] = extra
    summary["l0"] = l0
    _register_l0(ledger, content, l0)

    # ---- L1：盲解核验（可能改客观答案）----
    if verify_enabled:
        summary["l1"] = verify_content(
            content, goal_id=goal_id, tutorial_id=tutorial_id,
            job_id=job_id, discipline=discipline, ledger=ledger, on_stage=on_stage,
            **({"run_id": run_id, "budget": budget} if run_id or budget else {}),
        )
    else:
        summary["l1"] = {"enabled": False, "skipped": "config_disabled"}

    # ---- 跨卷重算：基于 L0+L1 之后的最终内容 ----
    summary["cross"] = recompute_cross(content)
    # 让 l0 自带的 cross 与最终重算结果一致：quality_report.exam_check 直接存 l0，
    # 若不同步，前端看到的会是「改答案之前」的旧跨卷结论。
    if isinstance(l0, dict):
        l0["cross"] = summary["cross"]
    summary["ledger"] = ledger
    _emit("verifying", "done", detail="L0/L1/跨卷完成")
    return summary


def _register_l0(ledger: FindingLedger, content: dict, l0: dict) -> None:
    """L0 违规/修复 → 台账：发现（structure/deterministic）+ 可证明格式修复单独记账。"""
    sections = [s for s in (content.get("sections") or []) if isinstance(s, dict)]
    body_by_unit: dict[str, tuple[str, object]] = {}
    for section in sections:
        body = exam_check_service.section_body(section)
        unit_id = exam_check_service.section_unit_id(section)
        paper = exam_check_service.parse_paper(body, str(section.get("title", ""))) \
            if exam_check_service.is_exam_markdown(body) else None
        body_by_unit[unit_id] = (body, paper)
        ledger.register_unit(unit_id, title=str(section.get("title", "") or ""), text=body, kind="exam")
    entries = [(e.get("unit_id") or "", e) for e in (l0.get("sections") or [])]
    if (l0.get("extra_exercises") or {}).get("violations"):
        extra = content.get("extra_exercises") or {}
        ledger.register_unit("extra_exercises", title=str(extra.get("title", "") or "额外练习"),
                             text=str(extra.get("markdown", "") or ""), kind="exercise")
        entries.append(("extra_exercises", l0["extra_exercises"]))
    for unit_id, entry in entries:
        if not unit_id:
            continue
        body, paper = body_by_unit.get(unit_id, ("", None))
        ledger.record_l0_fixes(unit_id, int(entry.get("fixed") or 0))
        for violation in entry.get("violations") or []:
            ledger.add_finding(finding_from_l0(
                violation, unit_id=unit_id, paper=paper, body=body,
                finding_id=ledger.new_finding_id(), spec=str(entry.get("spec") or "")))


def quality_from_summary(summary: dict) -> tuple[list[dict], list[str]]:
    """把 run_exam_quality 的汇总转成 (checks, issues)，供并入 quality_report。

    issues 只收 blocking 级（账目错、题号断裂、答案冲突、答案区「需核验」）；
    L1 改了什么单独进 checks 的 detail，让用户能看到「代码/模型改了哪几题」。
    """
    l0 = summary.get("l0") or {}
    l1 = summary.get("l1") or {}
    cross = summary.get("cross") or []

    checks = list(exam_check_service.quality_checks(l0))

    # 跨卷一致性用**重算后**的结果覆盖 L0 里那条（L0 的 cross 是改答案前算的）
    checks = [c for c in checks if c.get("name") != "跨卷命题一致性"]
    conflicts = sum(1 for c in cross if c.get("code") == "E018")
    repeats = sum(1 for c in cross if c.get("code") == "E017")
    if cross:
        checks.append({
            "name": "跨卷命题一致性",
            "passed": conflicts == 0,
            "detail": f"母题重复 {repeats} 处；同母题答案冲突 {conflicts} 处",
        })

    # L1 盲解核验结果单独一条 check
    if l1.get("enabled") and not l1.get("skipped"):
        applied = l1.get("applied_total", 0)
        errs = l1.get("errors") or []
        defects = sum(len(p.get("defects") or []) for p in l1.get("papers") or [])
        pending = sum(sum(s.get("verdict") == "undecidable" for s in p.get("skipped", []))
                      for p in l1.get("papers") or [])
        checks.append({
            "name": "试卷答案盲解核验",
            "passed": not errs and not pending,
            "detail": (
                f"核验 {l1.get('verified_total', 0)} 题；"
                f"自动改正客观题答案 {applied} 处；题目缺陷 {defects} 处"
                + (f"；仍有 {pending} 题待复核" if pending else "")
                + (f"；核验异常 {len(errs)} 卷" if errs else "")
            ),
        })

    issues = list(exam_check_service.quality_issues(l0))
    for c in cross:
        if exam_check_service.is_blocking(c.get("code", "")):
            issues.append(f"[{c['code']}] 跨卷：{c['detail']}")
    # L1 发现的题目缺陷（如选项不完备）是 blocking：题目本身不成立，学生无解
    for p in l1.get("papers") or []:
        for d in p.get("defects") or []:
            issues.append(f"[DEFECT] {p.get('title', '')}·第{d.get('number')}题：{d.get('defect', '')[:80]}")
    return checks, issues[:20]


def verify_content(content: dict, *, goal_id: str, tutorial_id: str, job_id: str, discipline,
                   ledger: FindingLedger | None = None, on_stage=None, run_id=None, budget=None) -> dict:
    """对整份教程内容做 L1 盲解核验；就地修正可回写的答案，返回核验汇总。

    只核验试卷 / 练习类 section（与 L0 同一套 classify_spec 判据），讲解型教程跳过。
    跨卷答案冲突（E018）由 L0 的确定性检查负责；本层负责**单卷内**盲解与卷面答案的
    冲突——两者互补：E018 抓「同题多卷自相矛盾」，L1 抓「单卷答案算错」。

    就地修改 content 的 section 正文（回写被修正的答案），因此必须在 content_json
    落库**之前**调用。``ledger``：共享修订台账——L1 回写登记修订（actor=l1_rewrite、
    changed_locations=被改写的答案位置、finding_ids=覆盖这些位置的已有发现），
    L1 缺陷注册为 correctness 发现；旧调用者不传时本地建账，行为不变。
    """
    ledger = ledger if ledger is not None else FindingLedger(tutorial_id=tutorial_id)
    summary: dict = {
        "enabled": True, "demo": False, "papers": [],
        "verified_total": 0, "conflicts_total": 0, "applied_total": 0, "errors": [],
    }

    format_spec = content.get("format_spec") or {}
    targets: list[tuple[int, str, str, str, dict, str]] = []
    for idx, section in enumerate(content.get("sections") or []):
        if not isinstance(section, dict):
            continue
        body = exam_check_service.section_body(section)
        spec = exam_check_service.classify_spec(str(section.get("title", "")), format_spec, body)
        # 只对试卷做盲解核验：练习文件（exercise）不逐题判对错，且往往无标准答案形态
        if spec == "exam" and body.strip():
            # 单元键与 review_generated / 修订台账对齐（首个非空 body 块）。
            targets.append((idx, str(section.get("title", "")), body, spec, section,
                            exam_check_service.section_unit_id(section)))

    if not targets:
        summary["enabled"] = False
        return summary

    titles_by_idx = {idx: title for idx, title, _b, _s, _sec, _u in targets}
    reports: dict[int, dict] = {}
    workers = min(VERIFY_WORKERS, len(targets))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="verify") as pool:
        futures = {
            pool.submit(
                _verify_one_paper,
                goal_id=goal_id, tutorial_id=tutorial_id, job_id=job_id,
                title=title, markdown=body, spec=spec, discipline=discipline,
                **({"run_id": run_id, "budget": budget} if run_id or budget else {}),
            ): idx
            for idx, title, body, spec, _section, _unit in targets
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                reports[idx] = future.result()
            except Exception as exc:  # noqa: BLE001  线程级兜底
                reports[idx] = {"title": titles_by_idx.get(idx, ""), "error": str(exc)[:120]}

    for idx, report in reports.items():
        target = next(t for t in targets if t[0] == idx)
        _section, unit_id = target[4], target[5]
        new_md = report.pop("_markdown", None)
        if new_md is not None and report.get("changed"):
            before_body = exam_check_service.section_body(content["sections"][idx])
            section = content["sections"][idx]
            blocks = [b for b in (section.get("content") or []) if isinstance(b, dict)]
            if blocks:
                blocks[0]["body"] = new_md
                for extra in blocks[1:]:
                    extra["body"] = ""
            # 修订登记：L1 回写只改被修正的答案位置；finding_ids 取台账中覆盖这些
            # 位置的已有发现（如审查阶段对同题的缺陷），最终对齐时由新证据关闭。
            changed_locations = [f"answers/{a.get('number')}" for a in report.get("applied") or []
                                 if isinstance(a, dict) and a.get("number")]
            covered = [f.finding_id for f in ledger.findings
                       if f.unit_id == unit_id and f.location in set(changed_locations)]
            ledger.register_revision(
                actor="l1_rewrite", before_text=before_body, after_text=new_md,
                changed_locations=changed_locations, finding_ids=covered)
        ledger.record_l1_round(unit_id, applied=len(report.get("applied") or []))
        # L1 缺陷 → correctness 发现（工具转写来源标 tool 方法）。
        paper = None
        if report.get("verified"):
            final_body = exam_check_service.section_body(content["sections"][idx])
            paper = exam_check_service.parse_paper(final_body, str(target[1]))
        for defect in report.get("defects") or []:
            ledger.add_finding(finding_from_l1_defect(
                defect, unit_id=unit_id, paper=paper, finding_id=ledger.new_finding_id()))
        summary["verified_total"] += report.get("verified", 0)
        summary["conflicts_total"] += len(report.get("conflicts") or [])
        summary["applied_total"] += len(report.get("applied") or [])
        if report.get("error"):
            summary["errors"].append(f"{report.get('title', '')}: {report['error']}")
        summary["papers"].append(report)

    summary["papers"].sort(key=lambda r: r.get("title", ""))
    return summary
