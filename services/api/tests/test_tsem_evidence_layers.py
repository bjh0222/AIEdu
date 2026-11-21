"""03 切片 1：证据适用性预检（layer_status）+ 题型形态归一 + 字段级过滤解释。

钉死任务书 03 步骤 1-3：
- normalize_question_form：作答形态与考查机制分开；「解答题」保留宽类 solution_wide，
  不等同「证明题」；映射有版本代码表；未知保持 unknown=True 待核实；
- 七种夹具验证过滤原因：真实同义 / 宽类不确定 / 不同题型 / 无作用域 / 未知条件 /
  明确不适用 / proposed-rejected-withdrawn；
- 被排除原理输出 expected_scope/actual_scope/normalized_scope/字段级原因；
- layer_status：standards/principles/cases 各自 available/eligible/selected/injected
  + reasons + overall coverage（standards_only/mixed/none）；injection_status 枚举不变；
- 铁律重放：本次三条 trace 的形态（标准命中、原理全被题型过滤）重放仍是
  standards_only，不降 0.45 阈值、无证据不补出原理。
零真实模型、零网络（内存库 + httpx 禁用）。
"""
from __future__ import annotations

import json

import pytest

from app.services.proposition_evidence import build_layer_status
from app.services.proposition_retrieval import (
    QUESTION_FORM_MAP_VERSION,
    normalize_question_form,
)
from tests.test_proposition_retrieval_v2 import (  # noqa: F401  db fixture 复用
    context,
    db,
    framework,
    principle,
    question,
    retrieve,
)


# ---------------------------------------------------------------- normalize_question_form

def test_normalize_question_form_answer_forms():
    assert normalize_question_form("填空题")["canonical"] == "fill_in_blank"
    assert normalize_question_form("选择题")["canonical"] == "multiple_choice"
    assert normalize_question_form("证明题")["canonical"] == "proof"
    assert normalize_question_form("计算题")["canonical"] == "computation"
    assert normalize_question_form("程序设计题")["canonical"] == "programming"
    # 「解答题」保留宽类，不等同证明题
    wide = normalize_question_form("解答题")
    assert wide["canonical"] == "solution_wide"
    assert wide["canonical"] != normalize_question_form("证明题")["canonical"]


def test_normalize_question_form_separates_mechanisms_from_forms():
    result = normalize_question_form("解答题（通过条件变化与结构迁移考查）")
    assert result["canonical"] == "solution_wide"
    assert result["mechanisms"] == ["condition_change", "structure_transfer"]
    # 机制不进 canonical 作答形态
    assert "condition_change" not in (result["forms"] or [])
    # 机制也可独立出现（无作答形态词）
    mechanism_only = normalize_question_form("考查反例构造")
    assert mechanism_only["canonical"] is None
    assert mechanism_only["mechanisms"] == ["counterexample"]


def test_normalize_question_form_unknown_and_version():
    result = normalize_question_form("带括号长说明的自然语言描述")
    assert result["canonical"] is None
    assert result["unknown"] is True
    assert result["basis"] == "none"
    assert normalize_question_form("证明题")["map_version"] == QUESTION_FORM_MAP_VERSION
    assert QUESTION_FORM_MAP_VERSION.startswith("question-form.v")


def test_normalize_question_form_records_raw_and_domain():
    result = normalize_question_form("填空与证明各半", "math_logic")
    assert result["raw"] == "填空与证明各半"
    # 表序即优先级：证明 > 填空，canonical 取最高优先形态
    assert result["canonical"] == "proof"
    assert result["forms"] == ["proof", "fill_in_blank"]
    assert result["domain"] == "math_logic"


# ---------------------------------------------------------------- 过滤解释（七种夹具）

def _decision_by_id(result, principle_id):
    return next((d for d in result["decisions"]
                 if d.get("id") == principle_id and d.get("outcome") == "excluded"), None)


def test_fixture_synonym_form_matches_via_normalization(db):
    """真实同义形式：同一形态（proof）不同措辞 → 归一命中。"""
    principle(db, "syn", scope='{"question_types":["证明题：判断命题真假并给出证明"]}')
    out = retrieve(db, context(question_types=["证明题"]))
    assert out["stats"]["injected"] == 1
    assert any(r.startswith("question_types:form_matched:proof") for r in out["selected"][0]["reason"].split(";"))


def test_fixture_wide_class_not_upgraded_to_proof(db):
    """宽类不确定：scope 证明题 vs 任务解答题 → 排除且解释宽类差异（不自动升级）。"""
    principle(db, "wide", scope='{"question_types":["证明题"]}')
    out = retrieve(db, context(question_types=["解答题"]))
    assert out["stats"]["scope_filtered"] == 1
    decision = _decision_by_id(out, "wide")
    assert decision and any("solution_wide" in r and "proof" in r for r in decision["reasons"])
    assert any("宽类解答题不自动等同证明题" in r for r in decision["reasons"])
    assert decision["scope_evidence"]["normalized_scope"]["question_types"] == {
        "expected": ["proof"], "actual": ["solution_wide"]}
    assert decision["scope_evidence"]["field_reasons"]["question_types"] == "scope_mismatch"


def test_fixture_different_question_type_excluded(db):
    principle(db, "blank", scope='{"question_types":["填空题"]}')
    out = retrieve(db, context(question_types=["选择题"]))
    assert out["stats"]["scope_filtered"] == 1
    decision = _decision_by_id(out, "blank")
    assert decision["scope_evidence"]["normalized_scope"]["question_types"] == {
        "expected": ["fill_in_blank"], "actual": ["multiple_choice"]}


def test_fixture_empty_scope_is_universal(db):
    principle(db, "univ", scope="{}")
    out = retrieve(db, context(question_types=["解答题"]))
    assert out["stats"]["injected"] == 1


def test_fixture_unknown_condition_stays_conditional(db):
    """未知条件（learner 自由文本）→ 条件性候选 + 显式待核实，不静默失败。"""
    principle(db, "cond", scope='{"knowledge_points":["导数"],"learner":"本科生"}')
    out = retrieve(db, context(learner="了解极限，求导练习较少"))
    assert out["stats"]["injected"] == 1
    assert "learner:unverified_context" in out["selected"][0]["reason"]


def test_fixture_explicit_exclusion_is_strict(db):
    principle(db, "excl", scope='{"knowledge_points":["导数"],'
                               '"exclude":{"knowledge_points":["链式法则"]}}')
    out = retrieve(db, context(knowledge_points=["导数", "链式法则"]))
    assert out["stats"]["scope_filtered"] == 1
    decision = _decision_by_id(out, "excl")
    assert decision and any("excluded_context:knowledge_points" in r for r in decision["reasons"])


@pytest.mark.parametrize("status", ["proposed", "rejected", "withdrawn"])
def test_fixture_state_gate_is_strict(db, status):
    principle(db, f"p-{status}", status=status)
    out = retrieve(db)
    assert out["stats"]["state_filtered"] == 1
    decision = _decision_by_id(out, f"p-{status}")
    assert decision and decision["reasons"] == [f"state:{status}"]


def test_unknown_form_one_side_stays_conditional_not_excluded(db):
    """一侧可归一、另一侧 unknown → 待核实（条件性候选），不猜同义也不硬排除。"""
    principle(db, "half", scope='{"question_types":["证明题"]}')
    out = retrieve(db, context(question_types=["带括号长说明的自然语言"]))
    assert out["stats"]["injected"] == 1
    assert any("unknown_form" in r for r in out["selected"][0]["reason"].split(";"))


def test_both_unknown_falls_back_to_lexical_threshold(db):
    """双侧都无形态信号 → 回退词法阈值（0.45 不降，Concept 别名继续生效）。"""
    principle(db, "lex", scope='{"question_types":["multiple choice"]}')
    # 词法命中（别名组）：不排除
    out = retrieve(db, context(question_types=["mcq"]))
    assert out["stats"]["injected"] == 1
    # 词法零重叠：排除（保持原阈值语义）
    out2 = retrieve(db, context(question_types=["proof"]))
    assert out2["stats"]["scope_filtered"] == 1


def test_form_match_does_not_lower_lexical_threshold(db):
    """0.45 阈值未被本方案降低：无别名、无包含、无形态命中 → 仍排除。"""
    principle(db, "strict", scope='{"knowledge_points":["定积分"]}')
    out = retrieve(db, context(knowledge_points=["离散数学"]))
    assert out["stats"]["scope_filtered"] == 1


# ---------------------------------------------------------------- layer_status 预检

def test_build_layer_status_shapes():
    standards_only = build_layer_status({
        "stats": {"principles_available": 14, "state_filtered": 3, "scope_filtered": 11,
                  "confirmed_total": 11, "auto_accepted_total": 0,
                  "standards_available": 7, "standards_injected": 7,
                  "examples_available": 0},
        "standards": [{"id": f"s{i}"} for i in range(7)],
        "selected": [], "examples": [],
        "decisions": ([{"reasons": ["scope_mismatch:question_types:expected=proof;actual=solution_wide"]}
                       for _ in range(11)]
                      + [{"reasons": ["state:proposed"]} for _ in range(3)]),
    })
    layers = standards_only["layers"]
    assert layers["standards"]["injected"] == 7
    assert layers["principles"]["available"] == 14
    assert layers["principles"]["eligible"] == 0  # 11 作用域过滤 + 3 状态过滤
    assert layers["principles"]["injected"] == 0
    assert any("scope_mismatch:question_types" in r for r in layers["principles"]["reasons"])
    assert standards_only["overall_coverage"] == "standards_only"
    assert build_layer_status({"stats": {}, "standards": [], "selected": [], "examples": []}) \
        ["overall_coverage"] == "none"
    mixed = build_layer_status({"stats": {"principles_available": 2, "standards_available": 3,
                                          "examples_available": 1},
                                "standards": [{"id": "s1"}], "selected": [{"id": "p1"}],
                                "examples": [{"question_id": "q1"}]})
    assert mixed["overall_coverage"] == "mixed"


def test_replay_three_traces_shape_stays_standards_only(db):
    """铁律重放：7 条标准命中、11 条 confirmed 原理全被题型过滤、3 条 proposed 过滤
    → 注入状态 ok 仍只指传输成功，coverage=standards_only，不得无证据补出原理。"""
    framework(db, [{"id": f"std-{i}", "rule": f"标准{i}", "strength": "hard" if i == 0 else "core",
                    "scope": {}} for i in range(7)])
    for index in range(11):
        principle(db, f"confirmed-{index}",
                  scope='{"question_types":["证明题"]}',
                  statement=f"原理{index}：证明题专属经验")
    principle(db, "proposed-1", status="proposed")
    principle(db, "proposed-2", status="proposed")
    principle(db, "rejected-1", status="rejected")
    out = retrieve(db, context(question_types=["解答题"]))
    assert out["injection_status"] == "ok"  # 枚举语义不变：有标准注入即 ok
    assert len(out["standards"]) == 7
    assert out["selected"] == []
    layer_status = build_layer_status(out)
    assert layer_status["overall_coverage"] == "standards_only"
    assert layer_status["layers"]["principles"]["available"] == 14
    assert layer_status["layers"]["principles"]["eligible"] == 0
    assert layer_status["layers"]["principles"]["injected"] == 0
    assert layer_status["layers"]["standards"]["injected"] == 7
    # 排除可解释：11 条题型过滤（带 scope_evidence）+ 3 条状态过滤
    scope_excluded = [d for d in out["decisions"]
                      if d.get("outcome") == "excluded" and "scope_evidence" in d]
    assert len(scope_excluded) == 11
    assert all(d["scope_evidence"]["normalized_scope"]["question_types"]
               == {"expected": ["proof"], "actual": ["solution_wide"]}
               for d in scope_excluded)
    state_excluded = [d for d in out["decisions"]
                      if d.get("outcome") == "excluded" and any(
                          r.startswith("state:") for r in d.get("reasons", []))]
    assert len(state_excluded) == 3
    # 注入文本不虚构原理迁移成功：原理层标题不出现
    assert "【命题经验" not in out["text"]


def test_knowledge_request_refs_and_gaps(db):
    """03-5：知识需求→选用证据可解释关联；未满足列 knowledge_gaps（不虚构已对标）。"""
    from app.services.proposition_evidence import _knowledge_request_refs

    principle(db, "srv", statement="导数专属经验：先辨认可导结构",
              scope='{"knowledge_points":["导数"]}')
    out = retrieve(db, context(knowledge_points=["导数", "泰勒展开"]))
    refs = _knowledge_request_refs(out["context"], out, stage="unit")
    by_request = {r["request"]: r for r in refs}
    assert by_request["导数"]["satisfied"] is True
    assert any(s["kind"] == "principle" for s in by_request["导数"]["served_by"])
    assert all(s["version"] >= 1 for s in by_request["导数"]["served_by"])
    assert by_request["泰勒展开"]["satisfied"] is False
    # satisfied 的请求有稳定 request_id（kr- 前缀），可被 call_id 链引用
    assert by_request["导数"]["request_id"].startswith("kr-")


def test_request_refs_use_complete_injected_positive_evidence():
    from app.services.proposition_evidence import _knowledge_request_refs

    out = {
        "selected": [{"id": "dropped", "version": 1, "reason": "泰勒展开"}],
        "evidence_blocks": {"principles": [{
            "id": "long", "version": "2",
            "text": "经验说明" * 110 + "\n  范围：子空间维数公式"
                    + "\n  限制/反例（不得当正向做法）：泰勒展开不能这样处理",
        }], "cases": []},
    }
    refs = _knowledge_request_refs(
        {"knowledge_points": ["子空间维数公式", "泰勒展开"]}, out, stage="unit")
    assert refs[0]["served_by"] == [{"kind": "principle", "id": "long", "version": 2}]
    assert refs[1]["satisfied"] is False


def test_request_refs_cases_require_assembled_positive_content():
    from app.services.proposition_evidence import _knowledge_request_refs

    out = {
        "examples": [
            {"question_id": "positive", "polarity": "positive", "reason": "linked"},
            {"question_id": "negative", "polarity": "negative", "reason": "linked"},
            {"question_id": "missing", "polarity": "positive", "reason": "泰勒展开"},
        ],
        "evidence_blocks": {"principles": [], "cases": [
            {"id": "positive", "text": "用子空间维数公式证明"},
            {"id": "negative", "text": "泰勒展开错误用法"},
        ]},
    }
    refs = _knowledge_request_refs(
        {"knowledge_points": ["子空间维数公式", "泰勒展开"]}, out, stage="unit")
    assert refs[0]["served_by"] == [{"kind": "case", "id": "positive", "version": 1}]
    assert refs[1]["satisfied"] is False


def test_planning_retrieval_consumes_reference_semantics_topics():
    """planning 检索消费 02 已核验语义主题（quote_match 条目），作为细粒度知识点。"""
    from app.services.proposition_evidence import retrieval_context

    task_context = {
        "contract_version": "generation.v1", "kind": "TaskContext",
        "identity": {"task_id": "t", "goal_id": "g", "tutorial_id": None,
                     "version": None, "unit_id": None},
        "payload": {
            "task_type": "exam", "domain_key": "math_logic", "branch": "线性代数",
            "learner": None, "objective": "出卷", "constraints": [],
            "scope": {"include": [], "exclude": [], "basis": "unknown", "source_refs": []},
            "reference_profile": None,
            "difficulty_target": {"mode": "unspecified", "instruction": None,
                                  "evidence_basis": "unknown", "source_refs": []},
            "deliverables": [], "assumptions": [], "conflicts": [],
        },
        "extensions": {"reference_semantics": {"entries": [
            {"kind": "knowledge_point", "topic": "伴随矩阵的秩", "text": "伴随矩阵的秩与原矩阵秩的关系",
             "source_ref": "ref.md", "span": {"quote": "r(A*)"}, "basis": "quote_match"},
            {"kind": "style", "topic": "填空为主", "text": "材料以填空题为主",
             "source_ref": "ref.md", "span": {"quote": "填空"}, "basis": "quote_match"},
        ]}},
    }
    planning = retrieval_context(task_context, stage="planning")
    assert "伴随矩阵的秩" in planning["knowledge_points"]
    assert "伴随矩阵的秩与原矩阵秩的关系" in planning["knowledge_points"]
    # style 类条目只贡献主题文本，不冒充知识点强制注入——仍受 scope/exclude 过滤
    assert planning["task_kind"] == "planning"


def test_layer_status_travels_with_bundle_and_public_projection(db):
    framework(db, [{"id": "std-1", "rule": "标准一", "strength": "hard", "scope": {}}])
    out = retrieve(db, context(question_types=["解答题"]))
    from app.services.proposition_retrieval import public_guidance_result

    public = public_guidance_result({**out, "layer_status": build_layer_status(out)})
    assert "layer_status" in public
    assert public["layer_status"]["overall_coverage"] == "standards_only"
