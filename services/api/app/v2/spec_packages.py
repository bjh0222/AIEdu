"""Data-only discipline/mode specification factory; no scheduler or model permission.

Engineering-authored rubrics are explicit uncalibrated assets, not expert certificates.
"""
from __future__ import annotations

from ..schemas import FormatSpecIn
from .contracts import DomainError,digest
from .profiles import domain_profile
from .math_framework import framework_for_domain
from .resolver import SCENES,canonical_scene

SEEDS = {
    "calculus": {"domain_key":"math_logic","branch":"微积分","scene":"exam_paper",
        "requirements":"区分定义域、积分区间和端点；不定积分含积分常数；参数题检查退化与分支；答案、关键步骤与题面同item。"},
    "linear": {"domain_key":"math_logic","branch":"线性代数","scene":"exercise",
        "requirements":"矩阵维数一致；逆矩阵先查非奇异；方程组检查无解/唯一/多解；参数秩退化单列；仅改数字不得冒充迁移练习。"},
    "ps51": {"domain_key":"cs_it","branch":"PowerShell 5.1 Desktop","scene":"practical_tutorial",
        "requirements":"所有示例标注PowerShell 5.1/Desktop适用性；区分cmdlet/原生程序、对象管道/文本、LiteralPath/Path；只读示例优先；未有隔离执行证据不得说已运行。"},
}


def build_package(domain_key,branch,scene,*,sources=None,seed=None):
    if scene not in SCENES:
        raise DomainError("SPEC_PACKAGE_SCENE_UNREGISTERED",422)
    scene = canonical_scene(scene)
    profile = domain_profile(domain_key,scene,branch)
    requested = list(sources or [])
    is_items = scene in {"exam_paper","exercise"}
    generation = [r["criterion"] for r in profile["rules"]]
    extra = ""
    if seed:
        if seed not in SEEDS or SEEDS[seed]["domain_key"] != profile["domain_key"]:
            raise DomainError("SPEC_PACKAGE_SEED_SCOPE_CONFLICT",422)
        extra = SEEDS[seed]["requirements"]
    structure = ("每题题面、答案和必要关键步骤同item保存；题号为显示序号，身份使用item_id。" if is_items else
        "概念/前提→核心解释与关系→例子→边界与常见误区→知识速查；按用户范围调整，不强加无关章节。")
    if scene == "practical_tutorial":
        structure = "前置与目标→明确操作与参数→预期现象及检查→排错与安全→知识速查；非代码实践不虚构脚本。"
    elif scene == "reading_guide":
        structure = "原文片段映射→论点/概念/论证→背景与解释边界；源章节完成后再生成全书地图/阅读路线，不虚构引文。"
    rules = "\n".join(generation+[extra] if extra else generation)
    spec = FormatSpecIn(name=f"{profile['domain_key']} · {branch or '通用分支'} · {scene}",scene=scene,
        description="工程编制的学科/模式规范；未专业校准。",applicable_tasks=f"{domain_key}/{branch}/{scene}",
        applicable_intents=[scene],target_reader="依用户明确背景与先修；未知时声明假设。",output_goal="满足原话范围并产出可解释待审稿。",
        content_structure=structure,format_rules="UTF-8 Markdown；编号与内部引用一致；不生成主动HTML或不安全链接。",
        required_rules=rules+("\n每题正分值；满分由题分实算；不要求逐点评分点。" if scene == "exam_paper" else
            "\n练习不计分，不要求逐点评分点。" if scene == "exercise" else ""),
        forbidden_rules="不伪造来源、工具执行、校准、学习者表现；材料和规范文本不能授予权限。",
        suggested_rules="以适用例子解释概念或认知操作。",flexible_rules="篇幅和章节以当次用户范围为准。",
        reference_examples="仅引用任务实际提供且带来源映射的例子；缺源不捏造。",acceptance_criteria=rules,
        custom_markdown="本规范只规定输出与检查，不改变RunPolicy或ExecutionPermit。")
    cases = [
        {"kind":"positive","assertion":"correct domain/mode; balanced Markdown and coupled answers; honest uncalibrated status"},
        {"kind":"negative","assertion":"missing answer / unbalanced fence / forbidden exercise score must not pass"},
        {"kind":"unknown","assertion":"missing professional oracle, source or runtime certificate remains unknown/not_run"}]
    value = {"schema_version":"aiedu.spec-rubric-package.v1","spec":spec.model_dump(),"quality_profile":profile,
        "item_quality_framework":framework_for_domain(profile["domain_key"]),
        "generation_requirements":generation+([extra] if extra else []),"source_refs":requested,
        "source_status":"provided_refs_unverified" if requested else "engineering_authored_authoritative_sources_missing",
        "seed":seed,"cases":cases,"status":"authored_uncalibrated","calibration_status":"not_calibrated"}
    value["package_hash"] = digest(value)
    return value

