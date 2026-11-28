"""Authored operational rubrics and narrowly bounded structured-claim adapters.

The adapters validate supplied formal data, never a model's translation of prose,
program execution, causal truth, reaction feasibility or psychometric difficulty.
"""
from __future__ import annotations
from collections import defaultdict
from .contracts import digest
from .math_framework import evaluate_item_quality

COMMON_DIMENSIONS = {
    "semantic_alignment": "用户目标、受众、范围、组织安排和限制在实际内容中可定位；不适用模板不得覆盖用户意图",
    "knowledge_integration": "定义、前提、关系、方法/证据、例证和结论之间的桥梁可追溯；跨单元接口不靠标题猜测",
    "assessment_design": "题目/练习/活动与目标及认知操作对齐，答案或反馈可判定；没有学习者数据不声称实测难度或学习收益",
    "evidence_boundaries": "来源、事实、推论、假设和unknown分开；引用、模型自评和同族共识不代替专业真值",
}

# Domain dimensions are intentionally small, composable packs.  The common
# dimensions above are checked separately as G1-G4; these rows express only
# what changes with the subject and leave unsupported claims unknown.
DIMENSIONS = {
    "math_logic": {"premises": "定义域、量词、可满足条件和退化情形", "reasoning": "推导与答案一致，等价变形有前提", "transfer": "解法机制、反例和迁移而非只换数字", "difficulty": "先修、认知操作和算量分别说明；实测难度未知"},
    "cs_it": {"contract": "输入、输出、状态变化和运行环境明确", "boundaries": "空输入、末端、越界和自匹配等边界", "algorithm": "算法、循环不变量和复杂度依据", "execution": "静态检查与隔离执行证据分列；未执行保持未知"},
    "physics_astronomy": {"model": "物理模型、尺度、符号和理想化假设明确", "dimensions": "单位、量纲、极限和守恒关系一致", "prediction": "已知量、边界条件与可检验预测相互对应", "uncertainty": "测量误差、近似和模型适用范围不被混作确定结论"},
    "chemistry_materials": {"representations": "宏观现象、微观粒子与符号表征建立对应", "conditions": "相态、温度、浓度、体积与假设明确", "conservation": "原子、净电荷和计量关系一致", "quantities": "单位、数量级、有效数字与平衡条件一致"},
    "biology_life": {"scale": "分子、细胞、个体和群体的尺度明确", "design": "处理、对照、重复、变量与混杂显式", "causality": "观测、假说与因果证据分开", "inference": "结论不超出数据、条件和样本范围"},
    "earth_environment": {"scale": "空间、时间、系统边界和观测尺度明确", "representation": "地图、图表、模型和单位的口径一致", "causality": "趋势、变率、相关与因果区分", "uncertainty": "情景、误差和外推范围显式"},
    "engineering_manufacturing": {"requirements": "用户、功能、载荷、材料、公差和安全约束明确", "tradeoffs": "方案选择与性能、成本、可制造性和风险配对", "verification": "计算、测试、原型和合规证据分列", "failure": "失效模式、边界条件和回退措施可追溯"},
    "health_sport": {"scope": "健康教育、运动建议和个体诊断边界明确", "evidence": "人群、证据等级、禁忌和适用条件清楚", "reasoning": "观察、鉴别依据和不确定性分开", "safety": "风险、停止条件和就医/专业转介边界显式"},
    "agriculture_food": {"context": "物种/品种、环境、生长或加工阶段明确", "measurement": "剂量、单位、储存和时间口径一致", "safety": "食品、动物、生态和生物安全约束可追溯", "inference": "经验、试验结果和可推广范围分开"},
    "psychology_cognition": {"construct": "理论构念与测量工具区分", "design": "样本、处理、对照、混杂和效应解释清楚", "inference": "相关、因果和替代解释分开", "ethics": "不把群体研究或模型输出当作个人诊断"},
    "society_politics": {"concepts": "概念、制度、主体和历史情境明确", "sources": "一手事实、二手解释和价值判断分列", "comparison": "比较对象、时间、尺度和指标同口径", "plurality": "争议允许有依据的多种解释并保留反证"},
    "economics_business": {"assumptions": "模型假设、激励、约束和时间口径明确", "measurement": "名义/实际、边际/总量、风险和外部性不混淆", "reasoning": "均衡、敏感性和替代解释可定位", "inference": "相关、因果和商业预测的保证边界清楚"},
    "law_policy": {"jurisdiction": "法域、时点、效力层级和事实范围明确", "authority": "法条、案例、解释、政策和意见分列", "application": "事实要件与法律要件逐项对应", "uncertainty": "争议、例外和非法律建议边界显式"},
    "education_learning": {"alignment": "学习目标、活动、证据和评价任务对齐", "cognition": "目标认知操作与可观察表现对应", "evidence": "教学合理性与实测学习收益分开", "equity": "学习者差异、可及性和适用条件不被忽略"},
    "language_communication": {"context": "语言/方言、语域、受众和语境明确", "form_meaning": "形式、意义、语用和惯用表达相互对应", "variation": "合理变体与规范要求分开", "evidence": "例句、释义和规则有可定位语境证据"},
    "philosophy_ethics": {"argument": "论题、前提、推论和反驳可分辨", "interpretation": "文本诠释与自建论证分开", "counterargument": "反例、限制和替代立场得到公平处理", "scope": "流派结论、价值判断和事实主张不混同"},
    "history_culture": {"provenance": "史料出处、年代、作者位置和版本可定位", "chronology": "时序、因果、相关和后见之明分开", "corroboration": "多来源互证与来源冲突显式", "interpretation": "史实、解释和不确定争议不混为定论"},
    "arts_creativity": {"medium": "媒介、技法、形式和创作意图对应", "observation": "作品分析以可观察形式证据为依据", "criteria": "评价标准与主观审美判断分开", "alternatives": "示例是可行路径而非唯一风格"},
    "design_architecture": {"brief": "用户、场地、材料、尺度、可达性和法规约束明确", "tradeoffs": "功能、结构、体验、成本和环境权衡可追溯", "validation": "原型、模拟、测试和合规证据分列", "safety": "效果图、概念方案与工程认证边界清楚"},
    "geography_urban": {"space_time": "坐标、投影、空间尺度和数据时间明确", "data": "统计边界、比例尺、分辨率和缺失口径一致", "causality": "空间相关、聚集与因果解释分开", "uncertainty": "尺度效应、空间偏差和情景限制显式"},
    "interdisciplinary": {"mapping": "每个单元注明学科概念、方法和证据类型", "bridge": "跨域术语映射和推论桥梁可定位", "non_equivalence": "不能相互替代的证据效度边界显式", "integration": "综合结论保留各域约束、冲突和未整合部分"},
}

SOURCE_REFS = {
    "math_logic": ["https://apcentral.collegeboard.org/media/pdf/ap25-frq-calculus-ab.pdf", "https://apcentral.collegeboard.org/media/pdf/ap25-sg-calculus-ab.pdf"],
    "cs_it": ["https://apcentral.collegeboard.org/media/pdf/ap25-frq-computer-science-a.pdf", "https://apcentral.collegeboard.org/media/pdf/ap25-sg-computer-science-a.pdf"],
    "physics_astronomy": ["https://www.nextgenscience.org/sites/default/files/Appendix%20F%20%20Science%20and%20Engineering%20Practices%20in%20the%20NGSS%20-%20FINAL%20060513.pdf"],
    "chemistry_materials": ["https://www.nextgenscience.org/sites/default/files/dci-arrangement/HS-PS106.21.17.pdf", "https://apcentral.collegeboard.org/media/pdf/ap25-frq-chemistry.pdf"],
    "biology_life": ["https://www.nextgenscience.org/pe/hs-ls1-2-molecules-organisms-structures-and-processes", "https://apcentral.collegeboard.org/media/pdf/ap24-frq-biology.pdf", "https://apcentral.collegeboard.org/media/pdf/ap24-sg-biology.pdf"],
    "engineering_manufacturing": ["https://www.abet.org/accreditation/accreditation-criteria/criteria-for-accrediting-engineering-programs-2025-2026/"],
    "interdisciplinary": ["https://www.testingstandards.net/open-access-files.html"],
}


def rubric(domain):
    if domain not in DIMENSIONS:
        return None
    provenance = "research_pointer_only" if SOURCE_REFS.get(domain) else "operational_domain_pack"
    return {"version": "item-quality.operational.v2", "status": "authored_uncalibrated", "domain_key": domain,
        "provenance": provenance, "warnings": ["authored_uncalibrated"],
        "dimensions": [{"id": k, "criterion": v, "scale": {"0": "有可定位反证", "1": "有部分证据或限制", "2": "适用条款均有独立证据"},
            "missing_evidence_value": None, "method": "criterion_review.v1", "result": "undecidable", "score": None,
            "source_ref": (SOURCE_REFS.get(domain) or ["operational:domain-pack.v1"])[0],
            "source_provenance": provenance} for k, v in DIMENSIONS[domain].items()],
        "common_dimensions": [{"id": k, "criterion": v, "scale": {"0": "有可定位反证", "1": "有部分证据或限制", "2": "适用条款均有独立证据"},
            "missing_evidence_value": None, "method": "criterion_review.v1", "result": "undecidable", "score": None,
            "source_ref": "operational:general-quality.v1", "source_provenance": "operational_general_pack"}
            for k, v in COMMON_DIMENSIONS.items()],
        "source_refs": SOURCE_REFS.get(domain, []), "aggregate_score": None, "threshold": None,
        "difficulty_status": "not_measured", "learning_gain": "not_measured", "calibration_status": "not_calibrated",
        "controlled_binding": {"method": "structured-claim-contract.v1", "scope": "literal formal data only", "prose_translation": "unsupported"}}


def structured_claim(domain, value):
    base = {"method_version": "structured-claim-contract.v1", "domain_key": domain, "input_hash": digest(value),
            "professional_correctness": "not_run", "scope": "supplied formal data consistency only"}
    result = "undecidable"
    try:
        if not isinstance(value, dict) or len(str(value)) > 16000:
            return {**base, "result": result, "reason": "unsupported_or_resource_limit"}
        if domain == "math_logic" and value.get("assertion") == "polynomial_identity" and set(value) == {"assertion", "left", "right", "variables"}:
            from .verifiers import polynomial_identity
            evidence = polynomial_identity(value["left"], value["right"], value["variables"], digest(value))
            result = evidence["result"]
        elif domain == "cs_it" and value.get("assertion") == "all_cases_match" and isinstance(value.get("cases"), list) and 0 < len(value["cases"]) <= 64:
            cases = value["cases"]
            if all(isinstance(c, dict) and {"input", "expected", "observed"} <= set(c) for c in cases):
                result = "satisfied" if all(c["expected"] == c["observed"] for c in cases) else "violated"
                base["scope"] = "supplied case output agreement; execution and general correctness not verified"
        elif domain == "biology_life" and value.get("assertion") == "single_treatment_contrast" and isinstance(value.get("groups"), list) and len(value["groups"]) == 2:
            a, b = value["groups"]
            if all(isinstance(g, dict) and {"treatment", "controlled_variables"} <= set(g) for g in (a, b)):
                result = "satisfied" if a["treatment"] != b["treatment"] and a["controlled_variables"] == b["controlled_variables"] else "violated"
                base["scope"] = "declared single treatment contrast; randomization, actual controls and causality not verified"
        elif domain == "chemistry_materials" and value.get("assertion") == "atoms_and_charge_conserved" and set(value) == {"assertion", "left", "right"}:
            sides = []
            for side in (value["left"], value["right"]):
                if not isinstance(side, list) or not 1 <= len(side) <= 12: raise ValueError()
                counts = defaultdict(int)
                for term in side:
                    coefficient = term["coefficient"]
                    if type(coefficient) is not int or not 1 <= coefficient <= 100 or len(term["atoms"]) > 20: raise ValueError()
                    for element, amount in term["atoms"].items():
                        if not isinstance(element, str) or type(amount) is not int or not 1 <= amount <= 100: raise ValueError()
                        counts[element] += coefficient * amount
                    if type(term["charge"]) is not int or abs(term["charge"]) > 20: raise ValueError()
                    counts["net_charge"] += coefficient * term["charge"]
                sides.append(dict(counts))
            result = "satisfied" if sides[0] == sides[1] else "violated"
            base["scope"] = "supplied atom and charge vectors; formula identity and reaction feasibility not verified"
    except Exception:
        result = "undecidable"
    evidence = {**base, "result": result}
    return {**evidence, "tool_raw_output_hash": digest(evidence)}


def quantity_summary(state, units):
    items = [item for unit in units for item in unit.get("items", [])]
    # Incomplete checkpoint candidates still need a coverage report, not a crash.
    roles = {u.get("artifact_role") for u in units}
    item_evaluation = evaluate_item_quality(state, units)
    return {
        "method_version": "item-structure-and-formal-evidence.v2", "item_count": len(items),
        "coupled_answer_count": sum(bool(i.get("answer", {}).get("final") and i.get("answer", {}).get("reasoning")) for i in items),
        "applicable": bool(roles & {"paper", "exercise"}), "semantic_score": None,
        "calibration_status": "not_calibrated", "difficulty_status": "not_measured",
        "rubrics": [p["item_quality_rubric"] for p in state.get("context", {}).get("quality_profiles", []) if p.get("item_quality_rubric")],
        "item_evaluation": item_evaluation,
    }

