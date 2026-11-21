"""Composable authored discipline assets; no certificate is implied by their existence.

Source for the taxonomy is learning.disciplines.SUBJECTS. Criteria below are
engineering-authored operational rubrics, not externally calibrated standards.
"""
from __future__ import annotations

from ..learning.disciplines import SUBJECTS, canonical_domain_key
from .check_criteria import rule_criterion
from .contracts import digest
from .math_framework import framework_for_domain

VERSION = "aiedu.domain-rubrics.v1"
# Each row carries an actual domain-specific distinction, not a renamed generic rubric.
CRITERIA = {
    "math_logic": ("先列定义域、量词和前提；推导逐步说明等价关系，给出边界反例", "题意可满足、答案与关键步骤相符；典型性绑定考查机制，难度说明先修/操作/算量"),
    "cs_it": ("标明语言/版本/环境；操作有前置、预期输出、失败排查和安全回退", "题目说明输入输出与边界；答案解释算法/状态/复杂度，未实际执行不得称可运行"),
    "physics_astronomy": ("物理模型与适用尺度先行；单位、符号、守恒条件和理想化假设明确", "已知量/边界条件充分；答案核量纲与极限，测量不确定性不混作计算错误"),
    "chemistry_materials": ("区分反应条件/相态/可逆性；原子与净电荷守恒，实验风险独立说明", "方程式与计量关系明确；单位/有效数字/条件齐备，配平不代替反应可行性"),
    "biology_life": ("机制标明分子/细胞/个体尺度；相关与因果分开，实验说明对照和变量", "题干证据足以支持结论；答案限定尺度、对照与外推边界，不把假说写成定论"),
    "earth_environment": ("区分空间/时间尺度、观测与模型；基线、情景和不确定性可追溯", "地图/气候数据有尺度及时间；结论区分趋势/变率与因果，单位口径一致"),
    "engineering_manufacturing": ("说明载荷/材料/公差/安全系数及约束；设计取舍与失效模式配对", "边界条件和参数单位齐备；答案核制造约束及失效风险，不以理论值当认证"),
    "health_sport": ("区分健康教育与个体诊断；注明适用人群、证据等级、禁忌及就医边界", "病例去身份化；答案说明鉴别依据与不确定性，不给未经支持的个体治疗保证"),
    "agriculture_food": ("标明物种/品种、生长或加工条件；食品/动物安全与地方要求分列", "参数随物种、环境及储存条件限定；答案核剂量单位和生物安全，不泛化经验"),
    "psychology_cognition": ("构念与测量工具分开；研究设计/效应/混杂清楚，群体证据不作个人诊断", "情境对应可测构念；答案区分相关/因果及替代解释，不以刻板印象作证据"),
    "society_politics": ("概念、制度与历史情境明确；一手事实、解释框架和价值判断分列", "争议题允许有依据的多种解释；答案列材料证据与反证，不把立场当事实"),
    "economics_business": ("模型假设、激励与约束先行；名义/实际/边际口径、风险与外部性明确", "数据时间与计量口径一致；答案说明均衡条件/敏感性，不把相关收益当因果保证"),
    "law_policy": ("注明法域、时点与效力层级；法条、案例、解释和政策建议分开", "事实要件与法律要件对应；答案列适用规范及争议，不构造不存在的法条/判例"),
    "education_learning": ("学习目标、活动与可观察评价对齐；区分教学设计合理性与实测学习收益", "题目对应认知操作与目标；答案说明可接受表现和误解，不伪造测量效度"),
    "language_communication": ("语言/方言/语域与语境明确；规则、惯用与可变表达区分，例句和释义相配", "题干语境足以判定；答案接受合理变体并解释语用/语法依据，不把偏好当唯一正确"),
    "philosophy_ethics": ("论题、前提、推论和反驳明确；文本诠释与自建论证分开，避免稻草人", "开放题按论证有效性与反驳质量判断；答案展示前提限制，不把流派结论当唯一事实"),
    "history_culture": ("史料出处、年代、作者位置与证据限度清楚；史实与后世解释分开", "题目材料可定位；答案以来源互证/时序/因果解释支撑，争议不作确定事实"),
    "arts_creativity": ("媒介、技法、形式与创作意图相连；作品分析有可观察依据，审美判断标主观", "练习目标与媒介限制可操作；示例答案是可行路径非唯一风格，反馈指向形式证据"),
    "design_architecture": ("用户/场地/材料/尺度与可达性约束明确；方案权衡、规范适用及验证方式分列", "设计任务给使用场景与约束；答案评功能/结构/可用性，不把效果图当工程合规"),
    "geography_urban": ("坐标、投影、空间尺度和数据时间明确；空间相关不直接作因果解释", "地图比例/坐标与统计边界齐备；答案核尺度效应、空间偏差和情景适用性"),
    "interdisciplinary": ("逐单元注明学科概念与证据方法；跨域术语映射和推论桥梁显式，不混用效度标准", "每个子题保留本域约束；综合答案解释跨域接口与不能相互替代的证据"),
}


def domain_profile(domain, scene="knowledge_tutorial", branch=""):
    key = canonical_domain_key(domain)
    tutorial, exercise = CRITERIA.get(key, ("定义、前提、来源和边界可追溯", "题意完整、答案有依据并保留未知"))
    criterion = exercise if scene in {"exercise", "exam", "exam_paper"} else tutorial
    profile = {"profile_id": f"{key}.{('exercise' if scene in {'exercise','exam','exam_paper'} else 'tutorial')}",
               "version": VERSION, "domain_key": key, "branch": branch, "scene": scene,
               "status": "authored_uncalibrated" if key in CRITERIA else "inherited_only",
               "parent_refs": ["general.v1"], "calibration_status": "not_calibrated",
               "calibration_scope": {"domain": key, "branch": branch, "scene": scene},
               "rules": [{"rule_id": f"{key}.{scene}.professional", "version": VERSION,
                   "source_ref": "authored:RF-v2-GENERAL/7.3;taxonomy:learning.disciplines.SUBJECTS",
                   "criterion": criterion, "consumer": ["planning", "generating", "checking"],
                   "applies_when": {"domain_key": key, "scene": scene}, "required": True,
                   "severity_class": "blocker", "decision_type": "semantic_rubric",
                   "evidence_method_refs": ["independent_oracle.v1"],
                   "pass_condition": "independent evidence supports every applicable clause; otherwise undecidable"}]}
    from .assessment_assets import rubric
    asset = rubric(key)
    if asset:
        profile["item_quality_rubric"] = asset
        if key == "math_logic":
            profile["item_quality_framework"] = framework_for_domain(key)
        profile["rules"][0]["source_ref"] += ";" + ";".join(asset["source_refs"])
        profile["rules"][0]["criterion"] += "；" + "；".join(d["criterion"] for d in asset["dimensions"])
    profile["rules"][0]["criterion_hash"] = rule_criterion(profile["rules"][0])["criterion_hash"]
    profile["profile_hash"] = digest(profile)
    return profile


def coverage_catalog():
    return [{"domain_key": key, "display_name": name,
             "tutorial": domain_profile(key), "exercise": domain_profile(key, "exercise")}
            for key, name, _ in SUBJECTS]


