"""Executable, versioned criteria; observations never confer certification.

Q1--Q6 transcribe the frozen project benchmark, not model-authored thresholds.
The criterion hash travels with the check plan and each assessment request.
"""
from copy import deepcopy

from .contracts import digest

VERSION = "aiedu.criterion-assessment.v1"
BENCHMARK = "project:benchmark:2026-11-12:6.3"

# These are the product's cross-disciplinary operational constructs.  They
# are deliberately not presented as research-authored cut scores: the cited
# sources motivate the construct, while the evidence/unknown rules below are
# this product's conservative operationalisation.  Each one is consumed by C
# through criterion_review.v1 and is kept separate from a domain pack.
GENERAL = {
    "G1.semantic_alignment": {
        "name": "语义理解与任务对齐",
        "target": "unit",
        "clauses": {
            "intent": "将用户目标、受众、范围、组织安排和明确限制落实到实际正文/题面；不能只复述计划或标签。",
            "format_applicability": "格式规范只在本任务适用的范围内生效；不适用模板、示例题型或固定章节不得覆盖用户意图。",
        },
        "pass_condition": "每个适用条款都有实际内容证据；计划本身不能证明正文已对齐；缺少证据保持unknown。",
        "source_ref": "operational:general-quality.v1;research:2026-11-18-cross-domain",
    },
    "G2.knowledge_integration": {
        "name": "知识结构与整合",
        "target": "unit",
        "clauses": {
            "relations": "定义、前提、概念关系、方法/证据和结论之间的桥梁在正文中可定位；跨单元依赖不靠标题猜测。",
            "synthesis": "例证、反例、限制或总结服务于同一知识目标；跨学科材料明确术语映射和不能互相替代的证据。",
        },
        "pass_condition": "每个适用条款均有正文或绑定来源的定位证据；仅有目录、知识卡或模型自述不得计为整合完成。",
        "source_ref": "operational:general-quality.v1;research:2026-11-18-cross-domain",
    },
    "G3.assessment_design": {
        "name": "教学与命题设计",
        "target": "unit",
        "clauses": {
            "alignment": "练习/问题的作答要求、答案和必要推理与本单元目标及认知操作一致；教程没有题目时确认不适用并说明。",
            "transfer": "存在可执行的例题、反例、错误诊断或变式迁移；只换数字、堆步骤或使用难度标签不构成命题证据。",
        },
        "pass_condition": "适用条款有题面与答案/正文证据；没有学习者作答数据时不得声称实测难度、区分度或学习收益。",
        "source_ref": "operational:general-quality.v1;research:2026-11-18-cross-domain",
    },
    "G4.evidence_boundaries": {
        "name": "证据、来源与未知边界",
        "target": "unit",
        "clauses": {
            "support": "事实、推论、假设、来源主张和模型建议分开；引用原文确实支持对应主张，否则报告缺口。",
            "unknown": "未检索、未执行、未测量或不具备专业校准的部分明确unknown；不能用自评、共识或文件完整代替证据。",
        },
        "pass_condition": "每个适用条款都有可定位证据或明确unknown理由；unknown不得被当作满意。",
        "source_ref": "operational:general-quality.v1;research:2026-11-18-cross-domain",
    },
}

MATH = {
    "Q1": {
        "name": "题面正确与可解",
        "target": "each_item",
        "clauses": {
            "premises": "核对定义域、量词、符号与条件相容性，包括参数退化值；不得默补条件。",
            "solvability": "独立求解或反例检查题目可解性、解的数量与设问一致性。",
        },
        "pass_condition": "必需集合内确认严重题面错误=0；unknown不计通过；每题须有独立人工或确定性数学验证。",
    },
    "Q2": {
        "name": "答案与关键步骤",
        "target": "each_item",
        "clauses": {
            "answer": "逐项比较题面、答案与必要关键步骤；检验代入、符号、边界和参数分支。",
            "reasoning": "检查每次等价变形与定理前提、证明链和结论；不要求逐题评分点；合法分值表不误报，非法小计须报。",
        },
        "pass_condition": "答案/必要关键步骤一致错误=0；每题独立工具或人工证据，模型共识不能代替。",
    },
    "Q3": {
        "name": "范围与硬约束覆盖",
        "target": "unit",
        "clauses": {
            "coverage": "把每项原始用户要求、参考考点、题型、题数、分值、范围及禁用知识逐项对应到实际题号/位置/版本；不能仅核标签。",
            "completeness": "区分单元局部范围与全任务覆盖；缺少要求不得由其它项高分抵消，不凭计划断言正文已覆盖。",
        },
        "pass_condition": "全部必需集合100%可追踪覆盖；缺失不得通过。",
    },
    "Q4": {
        "name": "典型性与分支适用",
        "target": "each_item",
        "clauses": {
            "core_method": "独立说明该题考查的核心关系、方法及目标分支/层次适用理由；不能只复述题型名。",
            "transfer": "对照其它题说明表征、条件或求解方向的变化；只换数字不能冒充迁移，必要巩固与新迁移分别说明。",
        },
        "rating_scale": {"0": "错题或偏题", "1": "关联弱或仅表面模仿", "2": "核心方法明确且适合目标", "3": "适合目标且有可解释的辨析或迁移价值"},
        "pass_condition": "独立逐题0--3评价，每题>=1、全任务中位数>=2，并保留逐题理由；模型评分仅工程初筛，不代替校准。",
    },
    "Q5": {
        "name": "先验难度与参照",
        "target": "each_item",
        "clauses": {
            "declaration": "声明相对目标/参考的先验难度；逐题列出先修知识、认知操作、关键步骤和算量依据，不能拿题型名称作难度证据。",
            "alignment": "核对实际题面是否落实用户难度与先验声明；不以缺少实测数据为由降低用户要求。",
        },
        "pass_condition": "每题有难度声明、依据和核对结论；无实际作答数据，实测难度/区分度/学习收益明确not_measured，不设新数值阈值。",
    },
    "Q6": {
        "name": "材料与专业证据",
        "target": "unit",
        "clauses": {
            "bindings": "核对引用的原题、原理、外评来源、实际注入、适用理由、自述位置和生成使用；只把实际hash/version/location匹配计为绑定。",
            "support": "检查引用原文是否支持对应专业主张；无支持须报。同源报告不充多源互证；无用户选材不等于必须虚构用户来源。",
        },
        "pass_condition": "实际引用材料绑定100%confirmed；绑定只证明出处，不代签专业支持或指导收益。",
    },
}


def criterion(item):
    prefix = item["item_id"].split(".")[0]
    if prefix in MATH:
        value = deepcopy(MATH[prefix])
        value["source_ref"] = BENCHMARK
    elif item["item_id"] in GENERAL:
        value = deepcopy(GENERAL[item["item_id"]])
    elif item.get("quality_dimension"):
        # Domain packs are data-bound at plan compilation time.  They use the
        # same conservative scale as the authored item-quality asset, but do
        # not inherit a numeric pass threshold from an external publication.
        dimension = item["quality_dimension"]
        value = {
            "name": dimension.get("name") or dimension.get("id") or item["item_id"],
            "target": "each_item",
            "clauses": {"dimension": dimension.get("criterion", "")},
            "rating_scale": {"0": "有可定位反证或明确缺陷", "1": "部分证据、限制或边界未闭合", "2": "适用要求均有可定位支持"},
            "pass_condition": "0为已确认缺陷；1为部分证据并保持待复核；2才表示本量规条款在当前内容上的工程性满足；缺证据为unknown。",
            "source_ref": dimension.get("source_ref", item.get("source_rule", {}).get("source_ref", "authored:domain-pack")),
        }
    else:
        value = {"name": item["item_id"], "target": "unit",
            "clauses": {"requirement": item["pass_condition"]},
            "pass_condition": item["pass_condition"],
            "source_ref": item.get("source_rule", {}).get("source_ref", "frozen:task-check-plan")}
    value.update(version=VERSION, check_id=item["item_id"])
    value["criterion_hash"] = digest(value)
    return value


def rule_criterion(rule):
    """Build the exact contract later compiled from one bound domain rule."""
    value = {
        "name": rule["rule_id"],
        "target": "unit",
        "clauses": {"requirement": rule["criterion"]},
        # quality.compile_plan intentionally uses the observable criterion as
        # the generic domain rule's pass condition. Keep this derivation
        # byte-for-byte aligned with that compiled contract.
        "pass_condition": rule["criterion"],
        "source_ref": rule["source_ref"],
        "version": VERSION,
        "check_id": rule["rule_id"],
    }
    value["criterion_hash"] = digest(value)
    return value


def bind_criteria(items):
    for item in items:
        if item["decision_type"] != "deterministic":
            item["criterion_contract"] = criterion(item)
            if item["item_id"].split(".")[0] in MATH:
                spec = item["criterion_contract"]
                item["pass_condition"] = spec["name"] + "；" + "；".join(spec["clauses"].values()) + "；" + spec["pass_condition"]
    return items
