# -*- coding: utf-8 -*-
"""命题知识库与变式引擎测试（P2 / Agent B）。

覆盖的核心命题不是「函数能跑」，而是三条**架构承诺**——它们各自都对应一个实测故障，
一旦回退，单元测试仍可能全绿而线上重新出错：

1. **同母题不再漂移**：同一 ItemModel 的 isomorph 变式，答案必须由 solution_template
   机械求出。实测那份卷里母题 ``x=ln(t+sqrt(1+t^2)),y=arctan t`` 出现 5 次给出 4 个
   不同答案，``f_n=x^n+2nx-2`` 出现 4 次给出 e/e^2/e^{-1}，根因是每次独立生成。
2. **求解失败绝不编造**：``computed=False`` 时答案必须为空串；isomorph 变式求不出
   答案必须 409 且不落库。一个看起来很确定的错答案比「算不出来」危害大得多。
3. **未确认的经验不得影响交付**：``proposed`` / ``rejected`` 的命题规律绝不进注入文本。

另覆盖：sympy 沙箱（实测 ``9**9**9`` 会让 sympy 永久不返回，必须有静态守卫）、
质量记录回流去重（重生成不得重复计数，否则聚类阈值会被同一次错误刷满）、
聚类的确定性与「不复活」语义、注入预算裁剪。
"""
from __future__ import annotations

import pytest

from app.services import proposition as P


# ------------------------------------------------------------------ 夹具

def _db(client):
    """拿一个 Session；表已由 conftest 的 client fixture 建好。

    必须先取 client 再建 Session：conftest 在 client setup 时 drop_all + create_all，
    顺序反了会拿到「no such table」。
    """
    from app.db import SessionLocal

    return SessionLocal()


def _goal(db, domain_key="math_logic", title="微积分期中冲刺"):
    from app.models import LearningGoal

    g = LearningGoal(title=title, domain_key=domain_key, motivation="测试")
    db.add(g)
    db.commit()
    return g


def _model(db, *, answer_expr="", solution_template="", incidentals=None,
           answer_select="", unknown="ANS", domain_key="math_logic",
           stem_sample="设 $f(x)=x^2-p$，求正根。", **kw):
    """直接建一条母题（模拟人工补录过解法模板的状态）。"""
    from app.models import ItemModel
    from app.services.utils import jdump

    radicals = {
        "definitions": ["f(x)=x^2-p"],
        "queries": ["\\sqrt{p}="],
        "occurrences": [{"paper": "第一套试卷", "number": 1}],
        "unknown": unknown,
        "answer_select": answer_select,
        "stem_sample": stem_sample,
    }
    radicals.update(kw.pop("radicals_extra", {}))
    row = ItemModel(
        domain_key=domain_key,
        title=kw.pop("title", "求 x^2=p 的正根"),
        chapter=kw.pop("chapter", "第一套"),
        knowledge_points=jdump(["平方根"]),
        radicals=jdump(radicals),
        incidentals=jdump(incidentals if incidentals is not None else {"p": 2}),
        solution_template=solution_template,
        answer_expr=answer_expr,
        provenance=kw.pop("provenance", "manual"),
        status=kw.pop("status", "confirmed"),
        **kw,
    )
    db.add(row)
    db.commit()
    return row


# ------------------------------------------------------------------ solve_isomorph


class TestSolveIsomorph:
    """机械求解：不同 incidentals 得到不同数值、但**结构一致**的答案。"""

    def test_answer_expr_yields_structurally_consistent_answers(self):
        model = {"answer_expr": "sqrt(p)/2", "incidentals": {"p": 2}}
        results = {p: P.solve_isomorph(model, {"p": p}) for p in (2, 3, 8)}
        for p, r in results.items():
            assert r["computed"] is True, r["error"]
            assert r["error"] == ""
            assert r["answer"]
        # 数值随参数变化
        assert results[2]["answer"] != results[3]["answer"] != results[8]["answer"]
        # 结构一致：都是 sqrt(k)/2 形态（sympy 会化简 sqrt(8)/2 -> sqrt(2)）
        for p in (2, 3):
            assert results[p]["answer"].startswith("sqrt(") and results[p]["answer"].endswith(")/2")
        assert results[2]["answer"] == "sqrt(2)/2"
        assert results[8]["answer"] == "sqrt(2)"

    def test_same_incidentals_always_same_answer(self):
        """同母题 + 同参数，反复求解必须给出**同一个**答案（漂移的根治点）。"""
        model = {"solution_template": "Eq(ANS**2 - p, 0)", "answer_select": "positive"}
        answers = {P.solve_isomorph(model, {"p": 2})["answer"] for _ in range(5)}
        assert answers == {"sqrt(2)"}, answers

    def test_equation_form_picks_declared_root(self):
        model = {"solution_template": "Eq(ANS**2 - p, 0)", "answer_select": "positive"}
        assert P.solve_isomorph(model, {"p": 9})["answer"] == "3"
        neg = {"solution_template": "Eq(ANS**2 - p, 0)", "answer_select": "negative"}
        assert P.solve_isomorph(neg, {"p": 9})["answer"] == "-3"

    def test_multi_solution_without_select_is_refused(self):
        """多解但未声明选取规则 => 拒绝任选（那正是「同题多解」的病根）。"""
        model = {"solution_template": "Eq(ANS**2 - p, 0)"}
        r = P.solve_isomorph(model, {"p": 2})
        assert r["computed"] is False
        assert r["answer"] == ""
        assert "拒绝任选" in r["error"]

    def test_no_template_never_fabricates(self):
        """抽出的母题默认没有模板：必须 computed=False，绝不编造答案。"""
        r = P.solve_isomorph({"incidentals": {"p": 2}}, {"p": 2})
        assert r["computed"] is False
        assert r["answer"] == ""
        assert "拒绝编造" in r["error"]

    def test_division_by_zero_is_not_an_answer(self):
        """实测 ``1/(p-2)`` 在 p=2 时 sympy 给 zoo；除零产物不得当成答案交付。"""
        r = P.solve_isomorph({"answer_expr": "1/(p-2)"}, {"p": 2})
        assert r["computed"] is False
        assert r["answer"] == ""
        assert "未定义" in r["error"]

    def test_infinite_limit_is_a_legal_answer(self):
        """oo / -oo 是极限题的合法答案（实测卷里就有 ``D. $+\\infty$`` 选项），不得误拦。"""
        assert P.solve_isomorph({"answer_expr": "oo"}, {})["computed"] is True
        assert P.solve_isomorph({"answer_expr": "-oo"}, {})["computed"] is True

    def test_missing_declared_param_reports_clearly(self):
        r = P.solve_isomorph({"answer_expr": "p*q", "incidentals": {"p": 2, "q": 3}}, {"p": 2})
        assert r["computed"] is False
        assert "缺少母题声明的参数" in r["error"] and "q" in r["error"]

    def test_bool_param_is_not_silently_treated_as_number(self):
        """True == 1：静默当数字会让「参数填错」变成「答案算错」，两者都难排查。"""
        r = P.solve_isomorph({"answer_expr": "p+1"}, {"p": True})
        assert r["computed"] is False
        assert "无法解析为数学量" in r["error"]

    def test_fraction_string_is_exact(self):
        r = P.solve_isomorph({"answer_expr": "sqrt(p)/2"}, {"p": "1/4"})
        assert r["computed"] is True and r["answer"] == "1/4"

    def test_latex_and_numeric_are_provided(self):
        r = P.solve_isomorph({"answer_expr": "exp(-1)"}, {})
        assert r["computed"] is True
        assert r["answer_latex"] == "e^{-1}"
        assert r["numeric"].startswith("0.3678")

    def test_unknown_answer_select_is_rejected(self):
        r = P.solve_isomorph({"answer_expr": "p", "answer_select": "weird"}, {"p": 1})
        assert r["computed"] is False
        assert "未知的 answer_select" in r["error"]

    def test_unknown_symbol_is_not_substituted(self):
        """未知量不能被当参数代入，否则方程会被消掉。"""
        r = P.solve_isomorph(
            {"solution_template": "Eq(ANS**2 - p, 0)", "answer_select": "positive"},
            {"p": 4, "ANS": 99},
        )
        assert r["computed"] is True and r["answer"] == "2"


class TestSympySandbox:
    """sympy 受控执行：模板是**数据**，不得成为任意代码执行的入口。"""

    @pytest.mark.parametrize("evil", [
        "x.__class__.__bases__",      # 属性访问（清空 builtins 挡不住的经典逃逸路径）
        "__import__('os')",
        "'abc'",
        "lambda: 1",
        "p if p else 1",
    ])
    def test_forbidden_syntax_is_rejected(self, evil):
        r = P.solve_isomorph({"answer_expr": evil}, {"p": 2})
        assert r["computed"] is False, f"{evil!r} 不该被求值"
        assert r["answer"] == ""
        assert r["error"]

    def test_disallowed_operator_rejected(self):
        r = P.solve_isomorph({"answer_expr": "p @ 2"}, {"p": 2})
        assert r["computed"] is False and "不允许的运算符" in r["error"]

    def test_oversized_exponent_is_refused_fast(self):
        """实测 ``parse_expr("9**9**9")`` 会让 sympy 构造 3.8 亿位整数而**永久挂死**。

        必须在求值前用 AST 静态拒绝，且拒绝要**快**（挂死与慢的区别只在于是否返回）。
        """
        import time

        t0 = time.time()
        r = P.solve_isomorph({"answer_expr": "9**9**9"}, {})
        assert time.time() - t0 < 2.0, "守卫失效：大整数幂把求解挂住了"
        assert r["computed"] is False and "整数幂规模" in r["error"]

    def test_symbolic_exponent_is_allowed(self):
        """``(1+p)**n`` 是合法的参数化模板，符号幂不会被误拒。"""
        r = P.solve_isomorph({"answer_expr": "(1+p)**n"}, {"p": 1, "n": 3})
        assert r["computed"] is True and r["answer"] == "8"
        r2 = P.solve_isomorph({"answer_expr": "p**9"}, {"p": 2})
        assert r2["computed"] is True and r2["answer"] == "512"

    def test_overlong_template_rejected(self):
        r = P.solve_isomorph({"answer_expr": "p+" * 400 + "1"}, {"p": 1})
        assert r["computed"] is False and "上限" in r["error"]

# ------------------------------------------------------------------ 变式一致性


class TestVariationConsistency:
    """核心目标：同一母题的两个 isomorph 变式，答案由模板机械求出，不再漂移。"""

    def test_two_isomorphs_are_solved_from_the_same_template(self, client):
        db = _db(client)
        try:
            row = _model(db, solution_template="Eq(ANS**2 - p, 0)", answer_select="positive")
            v1 = P.make_variation(db, row.id, {"p": 2})
            v2 = P.make_variation(db, row.id, {"p": 9})
            assert v1["verified_by"] == v2["verified_by"] == "tool_symbolic"
            assert v1["variation_type"] == v2["variation_type"] == "isomorph"
            # 同一模板、不同参数 => 不同数值、同结构（都是精确平方根，没有随机漂移）
            assert v1["answer"] == "sqrt(2)" and v2["answer"] == "3"
            # 两者都指向同一母题（溯源只存在这一列里）
            assert v1["item_model_id"] == v2["item_model_id"] == row.id
            assert v1["id"] != v2["id"]
            # 落库可查
            stored = P.list_instances(db, item_model_id=row.id)
            assert {s["answer"] for s in stored} == {"sqrt(2)", "3"}
            assert all(s["verified_by"] == "tool_symbolic" for s in stored)
        finally:
            db.close()

    def test_repeat_is_deterministic_not_random(self, client):
        """实测病根是「每次独立生成必然漂移」：这里同参数重复 5 次必须是同一个答案。"""
        db = _db(client)
        try:
            row = _model(db, answer_expr="sqrt(p)/2")
            answers = {P.make_variation(db, row.id, {"p": 2})["answer"] for _ in range(5)}
            assert answers == {"sqrt(2)/2"}, answers
        finally:
            db.close()

    def test_stem_never_leaks_provenance(self, client):
        """变式规范的 forbidden_rules 明令禁止溯源括注：stem 里不得出现原题编号 / 母题指纹。"""
        db = _db(client)
        try:
            row = _model(db, answer_expr="p")
            inst = P.make_variation(db, row.id, {"p": 7})
            for banned in ("原题", "变式一", "母题", "来源", row.id, "fingerprint"):
                assert banned not in inst["stem"], f"stem 泄漏溯源信息：{banned}"
        finally:
            db.close()

    def test_undeclared_template_refuses_isomorph_and_persists_nothing(self, client):
        """抽出的母题没有模板 => isomorph 必须 409，且**不落库**一条空答案的实例。"""
        from fastapi import HTTPException

        db = _db(client)
        try:
            row = _model(db)   # answer_expr / solution_template 都为空
            before = db.query(P.ItemInstance).count()
            with pytest.raises(HTTPException) as ei:
                P.make_variation(db, row.id, {"p": 2})
            assert ei.value.status_code == 409
            assert ei.value.detail["code"] == "answer_not_computable"
            assert db.query(P.ItemInstance).count() == before
        finally:
            db.close()

    def test_procedural_and_conceptual_are_disabled_without_persisting(self, client):
        """没有结构变更和独立验证实现的变式明确拒绝，不写空答案实例。"""
        db = _db(client)
        try:
            row = _model(db, solution_template="Eq(ANS**2 - p, 0)", answer_select="positive")
            for vtype in ("procedural", "conceptual"):
                from fastapi import HTTPException

                with pytest.raises(HTTPException) as exc:
                    P.make_variation(db, row.id, {"p": 2}, variation_type=vtype)
                assert exc.value.status_code == 409
                assert exc.value.detail["code"] == "variation_type_disabled"
                assert db.query(P.ItemInstance).count() == 0
        finally:
            db.close()

    def test_invalid_variation_type_and_missing_model(self, client):
        from fastapi import HTTPException

        db = _db(client)
        try:
            row = _model(db, answer_expr="p")
            with pytest.raises(HTTPException) as e1:
                P.make_variation(db, row.id, {}, variation_type="weird")
            assert e1.value.status_code == 422 and e1.value.detail["code"] == "invalid_variation_type"
            with pytest.raises(HTTPException) as e2:
                P.make_variation(db, "no-such-id", {})
            assert e2.value.status_code == 404 and e2.value.detail["code"] == "item_model_not_found"
        finally:
            db.close()

    def test_partial_incidentals_fall_back_to_recorded_originals(self, client):
        """只改一个槽位时，其余槽位用母题记录的原值补（数据库事实，不是猜测）。

        槽位名用抽取时的位置式命名 ``p1..pn``（见 extract_item_models）：题干渲染是
        **按位置**替换的，命名式槽位（``{"参数": "n"}``）无法映射回题面里的某个数字，
        因此只有位置式槽位才既能进求解、又能改题面。这里两者都验：
        答案由 ``p1``/``p2`` 求出，题面里对应的数字被替换。
        """
        db = _db(client)
        try:
            row = _model(
                db,
                answer_expr="p1 + p2",
                incidentals={"p1": 3, "p2": 5},
                stem_sample="设 $f(x)=3x+5$，求 $f(1)$。",
            )
            inst = P.make_variation(db, row.id, {"p1": 10})
            assert inst["answer"] == "15"
            assert inst["incidental_values"] == {"p1": 10, "p2": 5}
            # 题面里第一个槽位（3）被替换，第二个（5）保持原值
            assert "$f(x)=10x+5$" in inst["stem"], inst["stem"]
        finally:
            db.close()


# ------------------------------------------------------------------ 母题抽取


EXAM_MD = r"""
## 第一套试卷

### 选择题

**1.**（20 分）设 $f_n(x)=x^n+2nx-2$ 的正根为 $a_n$，则 $\lim_{n\to\infty}(1+a_n)^n=$（　　）

A. $1$　　B. $e$　　C. $e^2$　　D. $+\infty$

**2.**（20 分）设参数方程 $x=\ln(t+\sqrt{1+t^2}), y=\arctan t$，则 $\dfrac{d^2x}{dy^2}=$（　　）

A. $\sqrt2$　　B. $-\sqrt2$　　C. $\dfrac{\sqrt2}{2}$　　D. $-2$

### 参考答案

**1.** C　**2.** A

## 第二套试卷

### 填空题

**1.**（20 分）设 $f_n(x)=x^n+2nx-2$ 的正根为 $a_n$，则 $\lim_{n\to\infty}(1+a_n)^n=$ ______。

**2.**（20 分）设参数方程 $x=\ln(t+\sqrt{1+t^2}), y=\arctan t$，则 $\dfrac{d^2x}{dy^2}=$ ______。

### 参考答案

**1.** $e^{-1}$

**2.** $\dfrac{\sqrt2}{2}$
"""


class TestExtractItemModels:
    def test_extracts_real_item_models_with_answer_conflict(self, client):
        """实测那两个漂移母题必须被抽出来，并留下「答案冲突」证据。"""
        db = _db(client)
        try:
            models = P.extract_item_models(db, "math_logic", EXAM_MD, chapter="微积分期中冲刺")
            assert len(models) == 2
            by_title = {m["title"]: m for m in models}
            fn = by_title["f_n(x)=x^n+2nx-2"]
            assert fn["radicals"]["definitions"] == ["f_n(x)=x^n+2nx-2"]
            assert fn["radicals"]["occurrence_count"] == 2
            assert fn["radicals"]["answer_conflict"] is True
            assert set(fn["radicals"]["observed_answers"]) == {"e^2", "e^{-1}"}
            ln = by_title["x=\\ln(t+\\sqrt{1+t^2}),y=\\arctant"]
            assert ln["radicals"]["answer_conflict"] is True
            assert set(ln["radicals"]["observed_answers"]) == {"\\sqrt2", "\\frac{\\sqrt2}{2}"}
            for m in models:
                # 抽出来的是草稿，且**没有**凭空造出的解法模板
                assert m["status"] == "draft"
                assert m["solution_template"] == "" and m["answer_expr"] == ""
                assert m["provenance"] == "ai_constructed"
                assert m["domain_key"] == "math_logic"
                assert m["incidentals"], "槽位原值应被记录，供人工补模板时引用"
        finally:
            db.close()

    def test_re_extraction_is_idempotent(self, client):
        db = _db(client)
        try:
            first = P.extract_item_models(db, "math_logic", EXAM_MD)
            before = db.query(P.ItemModel).count()
            second = P.extract_item_models(db, "math_logic", EXAM_MD)
            assert db.query(P.ItemModel).count() == before
            assert sorted(m["id"] for m in first) == sorted(m["id"] for m in second)
        finally:
            db.close()

    def test_re_extraction_preserves_manual_template(self, client):
        """重复抽取不得抹掉人工补录的模板与状态（否则补录成果会丢）。"""
        db = _db(client)
        try:
            models = P.extract_item_models(db, "math_logic", EXAM_MD)
            target = models[0]
            P.patch_item_model(db, target["id"], _patch(solution_template="Eq(ANS-p, 0)", status="confirmed"))
            P.extract_item_models(db, "math_logic", EXAM_MD)
            after = P.get_item_model(db, target["id"])
            assert after["solution_template"] == "Eq(ANS-p, 0)"
            assert after["status"] == "confirmed"
        finally:
            db.close()

    def test_empty_markdown_yields_nothing(self, client):
        db = _db(client)
        try:
            assert P.extract_item_models(db, "math_logic", "") == []
            assert P.extract_item_models(db, "math_logic", "这不是试卷，没有任何题目。") == []
        finally:
            db.close()


def _patch(**kw):
    """构造一个 ItemModelPatch（pydantic 对象），避免测试直接依赖字段顺序。"""
    from app.schemas import ItemModelPatch

    return ItemModelPatch(**kw)


def _code_of(statement: str) -> str:
    """从规律 statement 里取出缺陷码。

    方括号形如 ``[E018·同一母题在不同卷给出不同答案]``（带 exam_check.CODES 的规则名）
    或 ``[DEFECT]``（CODES 表里没有的码）。所以要先切「·」再切「]」，
    只按「]」切会把规则名一起留下。
    """
    head = str(statement or "").lstrip("[").split("]")[0]
    return head.split("·")[0].strip()


class _Stub:
    """绕过 pydantic 的最小载荷：用于测 service 层自身的校验。

    schema 的 Literal 会在 HTTP 入口先拦掉非法枚举值，但 service 也可能被内部
    Python 代码直接调用（tutorials 回流、脚本导入），那时没有 pydantic 兜底，
    service 层的校验必须独立成立。
    """

    def __init__(self, **kw):
        self._kw = kw

    def __getattr__(self, name):
        return self._kw.get(name)

# ------------------------------------------------------------------ 质量回流

EXAM_SUMMARY = {
    "cross": [
        {
            "code": "E018", "severity": "blocking",
            "detail": "母题「f_n(x)=x^n+2nx-2」，同问「\\lim(1+a_n)^n=」给出不同答案："
                      "第一套·第1题(选择题)→e^2；第二套·第1题(填空题)→e^{-1}",
        },
        {
            "code": "E017", "severity": "advisory",
            "detail": "母题「x=\\ln(t+\\sqrt{1+t^2})」出现 3 次：第一套·第2题；第二套·第2题",
        },
        # 排版账目类：由 exam_check 自动修复负责，不得混进命题质量记录
        {"code": "E003", "severity": "advisory", "detail": "题号未用加粗形式", "fixed": False},
        {"code": "E008", "severity": "blocking", "detail": "分值分布表账目不自洽", "fixed": False},
    ],
    "sections": [
        {"title": "第一套试卷", "violations": [
            {"code": "E018", "severity": "blocking", "detail": "母题「f_n(x)=x^n+2nx-2」第1题答案冲突", "fixed": False},
            # 已自动修好的不算待改进项
            {"code": "E005", "severity": "blocking", "detail": "题号断裂", "fixed": True},
        ]},
    ],
}

L1_SUMMARY = {"papers": [
    {"title": "第一套试卷", "defects": [
        {"number": 3, "defect": "四个选项均与正确答案不符，学生无法作答", "blind_answer": "e"},
        {"number": 7, "defect": "题干未给出 n 的取值范围，条件不足", "blind_answer": ""},
    ]},
    {"title": "第二套试卷", "defects": [
        {"number": 5, "defect": "「作答区域」占位文字残留", "blind_answer": ""},
    ]},
]}


class TestReflowQualityRecords:
    def test_writes_proposition_records_only(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            n = P.reflow_quality_records(db, goal, "tut1", 1, EXAM_SUMMARY, L1_SUMMARY)
            rows = db.query(P.QualityRecord).all()
            assert n == len(rows) == 6, [(r.source, r.code, r.question_number) for r in rows]
            # 只收命题类：E003 / E008 / 已修好的 E005 都不该进来
            assert {r.code for r in rows} == {"E017", "E018", "DEFECT"}
            sources = {(r.source, r.code, r.question_number) for r in rows}
            assert ("l0_cross", "E018", 1) in sources
            assert ("l1_defect", "DEFECT", 3) in sources
            # 不同来源是不同观察，均保留；重复运行同一来源才是幂等去重。
            assert {(r.source, r.paper_title) for r in rows if r.code == "E018"} == {
                ("l0_cross", ""), ("l0_account", "第一套试卷"),
            }
            assert len({r.source_key for r in rows}) == 6
            for r in rows:
                assert r.domain_key == "math_logic" and r.goal_id == goal.id
                assert r.tutorial_id == "tut1" and r.version == 1
        finally:
            db.close()

    def test_motif_and_severity_are_preserved(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            P.reflow_quality_records(db, goal, "tut1", 1, EXAM_SUMMARY, L1_SUMMARY)
            e018 = [r for r in db.query(P.QualityRecord).all() if r.code == "E018"]
            assert e018 and all(r.motif == "f_n(x)=x^n+2nx-2" for r in e018)
            cross = [r for r in e018 if r.source == "l0_cross"][0]
            assert cross.severity == "blocking"
            e017 = [r for r in db.query(P.QualityRecord).all() if r.code == "E017"][0]
            assert e017.severity == "advisory"
        finally:
            db.close()

    def test_dedup_on_repeated_call(self, client):
        """重生成不得重复计数：否则聚类阈值会被同一次错误刷满，产出无根据的规律。"""
        db = _db(client)
        try:
            goal = _goal(db)
            first = P.reflow_quality_records(db, goal, "tut1", 1, EXAM_SUMMARY, L1_SUMMARY)
            total = db.query(P.QualityRecord).count()
            assert first == 6 and total == 6
            again = P.reflow_quality_records(db, goal, "tut1", 1, EXAM_SUMMARY, L1_SUMMARY)
            assert again == 0
            assert db.query(P.QualityRecord).count() == total
        finally:
            db.close()

    def test_new_version_is_counted_separately(self, client):
        """不同版本的新缺陷要计入（跨版本累积才能看出「反复出现」）。"""
        db = _db(client)
        try:
            goal = _goal(db)
            P.reflow_quality_records(db, goal, "tut1", 1, EXAM_SUMMARY, L1_SUMMARY)
            n2 = P.reflow_quality_records(db, goal, "tut1", 2, EXAM_SUMMARY, L1_SUMMARY)
            assert n2 == 6
            assert db.query(P.QualityRecord).count() == 12
        finally:
            db.close()

    def test_clean_summary_writes_nothing(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            assert P.reflow_quality_records(db, goal, "t", 1, {"cross": [], "sections": []}, {"papers": []}) == 0
            assert P.reflow_quality_records(db, goal, "t", 1, {}, {}) == 0
            assert db.query(P.QualityRecord).count() == 0
        finally:
            db.close()


# ------------------------------------------------------------------ 命题规律


def _seed_records(db, goal, *, code="DEFECT", count=3, version=1):
    """造 count 条同 (domain, code) 的记录，题号各不相同（避免被去重键合并）。"""
    from app.models import QualityRecord

    for i in range(count):
        db.add(QualityRecord(
            domain_key=goal.domain_key, goal_id=goal.id, tutorial_id="tut1",
            version=version, source="l1_defect", code=code,
            paper_title="第一套试卷", question_number=i + 1,
            motif="f_n(x)=x^n+2nx-2", detail=f"第{i+1}题缺陷", severity="blocking",
        ))
    db.commit()


class TestDistillPrinciples:
    def test_threshold_reached_creates_one_proposed_principle(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            made = P.distill_principles(db, "math_logic", threshold=3)
            assert len(made) == 1
            p = made[0]
            assert p["status"] == "proposed"
            assert p["evidence_count"] == 3
            assert p["domain_key"] == "math_logic"
            assert p["positive_refs"] == []
            assert len(p["negative_refs"]) == 3
            assert p["evidence_family_count"] == 1
            # statement 必须绑定具体 code + 出现次数 + 示例，不得是空话
            assert "DEFECT" in p["statement"]
            assert "3 次" in p["statement"]
            assert "f_n(x)=x^n+2nx-2" in p["statement"]
            for empty in ("要注意答案正确性", "注意质量", "保证正确"):
                assert empty not in p["statement"]
        finally:
            db.close()

    def test_below_threshold_creates_nothing(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            assert P.distill_principles(db, "math_logic", threshold=4) == []
            assert db.query(P.DesignPrinciple).count() == 0
        finally:
            db.close()

    def test_clustering_is_per_code_and_per_domain(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, code="DEFECT", count=3)
            _seed_records(db, goal, code="E018", count=2)
            made = P.distill_principles(db, "math_logic", threshold=3)
            assert len(made) == 1 and "DEFECT" in made[0]["statement"]
            # 别的学科的证据不能拿来凑数
            assert P.distill_principles(db, "cs_it", threshold=3) == []
        finally:
            db.close()

    def test_idempotent_and_evidence_refreshed(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            first = P.distill_principles(db, "math_logic", threshold=3)
            assert len(first) == 1
            assert P.distill_principles(db, "math_logic", threshold=3) == []   # 不重复建档
            assert db.query(P.DesignPrinciple).count() == 1
            # 新证据来了：只更新证据数，proposed 态措辞如实刷新
            _seed_records(db, goal, count=2, version=2)
            P.distill_principles(db, "math_logic", threshold=3)
            p = P.list_principles(db, "math_logic")["items"][0]
            assert p["evidence_count"] == 5 and "5 次" in p["statement"]
            assert db.query(P.DesignPrinciple).count() == 1
        finally:
            db.close()

    def test_e018_statement_carries_mechanism_not_platitude(self, client):
        """E018 的规律必须说清「答案要由模板机械求出」这个机理。"""
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, code="E018", count=3)
            made = P.distill_principles(db, "math_logic", threshold=3)
            assert len(made) == 1
            s = made[0]["statement"]
            assert _code_of(s) == "E018"
            # 方括号里除了 code 还带 exam_check.CODES 的规则名，便于人读懂
            assert "同一母题在不同卷给出不同答案" in s
            assert "3 次" in s
            assert "机械求出" in s or "solution_template" in s
        finally:
            db.close()


class TestReviewAndInjection:
    def test_confirmed_principle_becomes_injectable(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            p = P.distill_principles(db, "math_logic", threshold=3)[0]
            # proposed 经验绝不注入；独立的标准层仍可使用。
            before = P.preview_injection(db, "math_logic")
            assert before["injected"] == 0 and before["state_filtered"] == 1 and before["selected"] == []
            assert P.principles_for_injection(db, "math_logic") == before["text"]
            assert before["standards"] and before["text"].startswith("【命题评价标准")
            assert p["id"] not in before["text"] and "【命题经验" not in before["text"]
            reviewed = P.review_principle(db, p["id"], "confirm", rationale="实测确认")
            assert reviewed["status"] == "confirmed"
            text = P.principles_for_injection(db, "math_logic")
            assert text and "DEFECT" in text
            assert "【命题经验：软性生成指导" in text
            assert "人工确认 confirmed" in text
            assert reviewed["rationale"] == p["rationale"]
            from app.models import DesignPrincipleRevision
            from app.services.utils import jload

            revision = db.query(DesignPrincipleRevision).filter_by(principle_id=p["id"], action="confirm").one()
            assert revision.rationale == "实测确认"
            assert jload(revision.snapshot, {})["rationale"] == p["rationale"]
            # 聚类标记必须保留（distill 的幂等与「不复活」都靠它）
            assert reviewed["rationale"].startswith("[cluster:math_logic|DEFECT]")
        finally:
            db.close()

    def test_rejected_principle_never_injected_and_never_revived(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            p = P.distill_principles(db, "math_logic", threshold=3)[0]
            d = P.review_principle(db, p["id"], "reject", rationale="与本学科无关")
            assert d["status"] == "rejected"
            rejected = P.preview_injection(db, "math_logic")
            assert rejected["injected"] == 0 and rejected["selected"] == [] and rejected["state_filtered"] == 1
            assert p["id"] not in rejected["text"] and "【命题经验" not in rejected["text"]
            assert rejected["standards"] and P.principles_for_injection(db, "math_logic") == rejected["text"]
            # 再攒够证据也不复活
            _seed_records(db, goal, count=3, version=2)
            assert P.distill_principles(db, "math_logic", threshold=3) == []
            assert db.query(P.DesignPrinciple).count() == 1
            assert P.list_principles(db, "math_logic")["rejected"] == 1
        finally:
            db.close()

    def test_review_is_one_shot(self, client):
        from fastapi import HTTPException

        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            p = P.distill_principles(db, "math_logic", threshold=3)[0]
            P.review_principle(db, p["id"], "confirm")
            with pytest.raises(HTTPException) as e:
                P.review_principle(db, p["id"], "reject")
            assert e.value.status_code == 409 and e.value.detail["code"] == "already_reviewed"
            with pytest.raises(HTTPException) as e2:
                P.review_principle(db, p["id"], "maybe")
            assert e2.value.status_code == 422
            with pytest.raises(HTTPException) as e3:
                P.review_principle(db, "nope", "confirm")
            assert e3.value.status_code == 404
        finally:
            db.close()

    def test_injection_filters_by_domain_and_budget(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, code="DEFECT", count=3)
            _seed_records(db, goal, code="E018", count=4)
            made = P.distill_principles(db, "math_logic", threshold=3)
            for m in made:
                P.review_principle(db, m["id"], "confirm")
            full = P.principles_for_injection(db, "math_logic", budget_chars=4000)
            assert "DEFECT" in full and "E018" in full
            # 预算不足以容纳任何一条时返回空串，**不编造**一般性建议来填空
            assert P.principles_for_injection(db, "math_logic", budget_chars=10) == ""
            # 别的学科不能取到本学科经验；通用框架标准并非本学科经验。
            other = P.preview_injection(db, "cs_it")
            assert other["injected"] == other["confirmed_total"] == 0 and other["selected"] == []
            assert "DEFECT" not in other["text"] and "E018" not in other["text"]
            assert other["standards"] and P.principles_for_injection(db, "cs_it") == other["text"]
        finally:
            db.close()

    def test_budget_keeps_more_independent_families_first(self, client):
        """同相关性和可信度下，更多题目家族优先；预算按完整经验块计算。"""
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, code="DEFECT", count=3)
            _seed_records(db, goal, code="E018", count=9)
            for idx, record in enumerate(db.query(P.QualityRecord).filter_by(code="E018").all()):
                record.motif = f"f_{idx}(x)=x^2+{idx}"
                record.motif_full = record.motif
                record.motif_key = P._motif_id(record.motif)
            db.commit()
            made = P.distill_principles(db, "math_logic", threshold=3)
            by_code = {_code_of(m["statement"]): m for m in made}
            for m in made:
                P.review_principle(db, m["id"], "confirm")
            assert by_code["DEFECT"]["evidence_count"] == 3
            assert by_code["E018"]["evidence_count"] == 9
            assert by_code["DEFECT"]["evidence_family_count"] == 1
            assert by_code["E018"]["evidence_family_count"] == 9
            from app.models import PropositionConfig
            from app.services.utils import jdump

            # 固定标准层上限，避免总预算变化时默认55%层配额改变标准入选集合。
            # 总长度限制仍生效；标准和经验都按完整块计入预算。
            db.add(PropositionConfig(id="default", settings_json=jdump({"standards_budget_chars": 4000})))
            db.commit()
            both = P.preview_injection(db, "math_logic", budget_chars=4000)
            assert [x["id"] for x in both["selected"]] == [by_code["E018"]["id"], by_code["DEFECT"]["id"]]
            first_block = both["text"].rsplit("\n- ", 1)[0]
            one = P.preview_injection(db, "math_logic", budget_chars=len(first_block))
            assert [x["id"] for x in one["selected"]] == [by_code["E018"]["id"]]
            assert one["text"] == first_block and len(one["text"]) <= one["budget_chars"]
            assert one["dropped"] == 1 and one["injected"] == 1
            assert one["standards"] == both["standards"]
        finally:
            db.close()

    def test_preview_reports_dropped(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
            m = P.distill_principles(db, "math_logic", threshold=3)[0]
            P.review_principle(db, m["id"], "confirm")
            ok = P.preview_injection(db, "math_logic", None, 4000)
            assert ok["injected"] == 1 and ok["confirmed_total"] == 1 and ok["dropped"] == 0
            tight = P.preview_injection(db, "math_logic", None, 10)
            assert tight["injected"] == 0 and tight["dropped"] == 1 and tight["text"] == ""
        finally:
            db.close()

# ------------------------------------------------------------------ 模板补录


class TestPatchItemModel:
    """补录解法模板是让母题可用的唯一途径；不合法的模板必须在入库前就被拒。"""

    def test_valid_template_enables_isomorph(self, client):
        db = _db(client)
        try:
            row = _model(db, status="draft")
            # 补录前：isomorph 不可产出
            from fastapi import HTTPException
            with pytest.raises(HTTPException):
                P.make_variation(db, row.id, {"p": 4})
            out = P.patch_item_model(db, row.id, _patch(
                solution_template="Eq(ANS**2 - p, 0)", answer_select="positive", status="confirmed",
            ))
            assert out["solution_template"] == "Eq(ANS**2 - p, 0)"
            assert out["answer_select"] == "positive"
            assert out["status"] == "confirmed"
            # 补录后：机械求解可用
            inst = P.make_variation(db, row.id, {"p": 4})
            assert inst["answer"] == "2" and inst["verified_by"] == "tool_symbolic"
        finally:
            db.close()

    def test_invalid_template_is_rejected_and_not_persisted(self, client):
        """存进不可求解的模板，会让错误现场离原因很远：必须在 PATCH 时就拒。"""
        from fastapi import HTTPException

        db = _db(client)
        try:
            row = _model(db)
            for bad, code in (
                ("9**9**9", "invalid_solution_template"),          # 幂规模超限（实测会挂死 sympy）
                ("x.__class__", "invalid_solution_template"),      # 属性访问
                ("__import__('os')", "invalid_solution_template"),
                ("Eq(ANS**2 -", "invalid_solution_template"),      # 语法错误
            ):
                with pytest.raises(HTTPException) as ei:
                    P.patch_item_model(db, row.id, _patch(solution_template=bad))
                assert ei.value.status_code == 422, bad
                assert ei.value.detail["code"] == code, bad
            # 一条都没落库
            db.refresh(row)
            assert row.solution_template == ""
        finally:
            db.close()

    def test_partial_patch_leaves_other_fields_intact(self, client):
        db = _db(client)
        try:
            row = _model(db, answer_expr="sqrt(p)/2", status="draft", difficulty="basic")
            out = P.patch_item_model(db, row.id, _patch(status="confirmed"))
            assert out["status"] == "confirmed"
            assert out["answer_expr"] == "sqrt(p)/2"      # 未提交的字段保持原值
            assert out["difficulty"] == "basic"
        finally:
            db.close()

    def test_unknown_and_select_are_validated(self, client):
        """service 层校验（内部 Python 调用也会经过，不只 HTTP）。"""
        from fastapi import HTTPException

        db = _db(client)
        try:
            row = _model(db, answer_expr="p")
            with pytest.raises(HTTPException) as e1:
                P.patch_item_model(db, row.id, _patch(unknown="a.b"))
            assert e1.value.status_code == 422 and e1.value.detail["code"] == "invalid_unknown"
            # answer_select 的非法值走 service 层：用绕过 pydantic 的桩对象，
            # 因为 schema 的 Literal 会先拦（那一层由下面的 HTTP 测试覆盖）。
            with pytest.raises(HTTPException) as e2:
                P.patch_item_model(db, row.id, _Stub(answer_select="biggest"))
            assert e2.value.status_code == 422 and e2.value.detail["code"] == "invalid_answer_select"
            ok = P.patch_item_model(db, row.id, _patch(unknown="X", answer_select="max"))
            assert ok["unknown"] == "X" and ok["answer_select"] == "max"
        finally:
            db.close()

    def test_schema_rejects_unknown_answer_select(self, client):
        """schema 层 Literal 先行拦截（HTTP 调用根本到不了 service）。"""
        mid = client.post("/api/proposition/item-models/extract", json={
            "domain_key": "math_logic", "markdown": EXAM_MD}).json()[0]["id"]
        resp = client.patch(f"/api/proposition/item-models/{mid}", json={"answer_select": "biggest"})
        assert resp.status_code == 422, resp.text

    def test_missing_model_404(self, client):
        from fastapi import HTTPException

        db = _db(client)
        try:
            with pytest.raises(HTTPException) as e:
                P.patch_item_model(db, "nope", _patch(status="confirmed"))
            assert e.value.status_code == 404
        finally:
            db.close()

    def test_re_extraction_preserves_solve_conventions(self, client):
        """回归：重新抽取不得抹掉人工补录的 ``unknown`` / ``answer_select``。

        它们与 solution_template 是**一套**的（模板里写的就是那个未知量符号）。只保留
        模板、把约定重置成默认值，会让模板去解一个不存在的符号——实测确实如此，
        而且比模板一起丢掉更隐蔽：看起来还能求解，解出来的却是错的。
        """
        db = _db(client)
        try:
            models = P.extract_item_models(db, "math_logic", EXAM_MD)
            mid = models[0]["id"]
            P.patch_item_model(db, mid, _patch(
                solution_template="Eq(X**2 - p1, 0)", unknown="X",
                answer_select="max", status="confirmed"))
            before = P.get_item_model(db, mid)
            assert (before["unknown"], before["answer_select"]) == ("X", "max")

            P.extract_item_models(db, "math_logic", EXAM_MD)   # 重新抽取同一份卷子

            after = P.get_item_model(db, mid)
            assert after["id"] == mid
            assert after["solution_template"] == "Eq(X**2 - p1, 0)"
            assert after["unknown"] == "X", "求解约定被重新抽取抹掉了"
            assert after["answer_select"] == "max", "选取规则被重新抽取抹掉了"
            assert after["status"] == "confirmed"
            # 出现位置等**新证据**仍应被刷新（不是整条冻结）
            assert after["radicals"]["occurrence_count"] == before["radicals"]["occurrence_count"] == 2
        finally:
            db.close()


class TestPrincipleScope:
    """规律的知识点作用域必须是**真实知识点**，否则按知识点过滤会静默失效。"""

    def test_scope_holds_knowledge_points_not_motifs(self, client):
        """回归：把定义式指纹塞进 scope_knowledge_points 会让过滤永远不相交。

        motif 形如 ``f_n(x)=x^n+2nx-2``，而调用方传的是「数列极限」这类知识点名，
        两者永不相交 => 调用方一传 knowledge_points，已确认规律就被整条丢掉，
        注入形同虚设，且日志里完全看不出来。
        """
        db = _db(client)
        try:
            # 走真实抽取链路，让 QualityRecord.motif 与 ItemModel 的 definitions 对得上
            models = P.extract_item_models(db, "math_logic", EXAM_MD)
            assert models, "前提：应能抽出母题"

            class _G:
                id = "g-scope"
                domain_key = "math_logic"

            written = P.reflow_quality_records(db, _G(), "tut-scope", 1, {
                "cross": [{
                    "code": "E018", "severity": "blocking",
                    "detail": "母题「f_n(x)=x^n+2nx-2」，同问「\\lim(1+a_n)^n=」"
                              "给出不同答案：第一套·第1题(选择题)→e^2；第二套·第1题(填空题)→e^{-1}",
                }],
                "sections": [],
            }, {"papers": []})
            assert written == 1

            made = P.distill_principles(db, "math_logic", threshold=1)
            assert len(made) == 1
            scope = made[0]["scope_knowledge_points"]
            motif = "f_n(x)=x^n+2nx-2"
            assert scope, "母题能连上时，作用域不该为空"
            assert motif not in scope, "作用域里不得是定义式指纹"
            # 母题的 knowledge_points 存的是「所问的量」，那才是知识点的表达
            fn = [m for m in models if m["title"] == motif][0]
            assert set(scope) <= set(fn["knowledge_points"])

            P.review_principle(db, made[0]["id"], "confirm")
            # 匹配的知识点 => 选中该经验，而不是只看非空的标准文本。
            matching = P.preview_injection(db, "math_logic", knowledge_points=scope)
            assert [x["id"] for x in matching["selected"]] == [made[0]["id"]]
            assert matching["injected"] == 1 and made[0]["id"] in matching["text"]
            assert P.principles_for_injection(db, "math_logic", knowledge_points=scope) == matching["text"]
            # 不匹配的知识点 => 按作用域过滤掉（这是期望行为，不是失效）
            unrelated = P.preview_injection(db, "math_logic", knowledge_points=["有机合成"])
            assert unrelated["injected"] == 0 and unrelated["scope_filtered"] == 1
            assert unrelated["dropped"] == 0 and unrelated["selected"] == []
            assert made[0]["id"] not in unrelated["text"] and unrelated["standards"]
            assert P.principles_for_injection(db, "math_logic", knowledge_points=["有机合成"]) == unrelated["text"]
            # 缺少知识点上下文不能证明专用经验适用；分类计数而非预算丢弃。
            missing_context = P.preview_injection(db, "math_logic")
            assert P.principles_for_injection(db, "math_logic") == missing_context["text"]
            assert made[0]["id"] not in missing_context["text"] and missing_context["selected"] == []
            assert missing_context["standards"]
            assert missing_context["confirmed_total"] == missing_context["scope_filtered"] == 1
            assert missing_context["injected"] == missing_context["dropped"] == 0
        finally:
            db.close()

    def test_unlinkable_scope_falls_back_to_domain_wide(self, client):
        """旧数据无法绑定知识点时保留空范围；人工确认后可在本学科预算内检索。"""
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)     # motif 连不上任何母题
            made = P.distill_principles(db, "math_logic", threshold=3)
            assert made[0]["scope_knowledge_points"] == []
            P.review_principle(db, made[0]["id"], "confirm")
            preview = P.preview_injection(db, "math_logic", knowledge_points=["任意知识点"])
            assert [x["id"] for x in preview["selected"]] == [made[0]["id"]]
            assert preview["injected"] == 1 and preview["scope_filtered"] == 0
            assert made[0]["id"] in preview["text"]
            assert P.principles_for_injection(db, "math_logic", knowledge_points=["任意知识点"]) == preview["text"]
        finally:
            db.close()


# ------------------------------------------------------------------ HTTP 层


class TestHttpRoutes:
    """路由层：状态码、过滤参数、以及 409/422 能否正确透传给前端。"""

    def test_list_and_extract(self, client):
        resp = client.post("/api/proposition/item-models/extract", json={
            "domain_key": "math_logic", "markdown": EXAM_MD, "chapter": "微积分期中冲刺",
        })
        assert resp.status_code == 200, resp.text
        models = resp.json()
        assert len(models) == 2
        assert all(m["status"] == "draft" for m in models)

        lst = client.get("/api/proposition/item-models", params={"domain": "math_logic"}).json()
        assert lst["total"] == 2
        assert lst["conflicts"] == 2      # 两个母题都有「同题不同答案」证据
        assert lst["solvable"] == 0       # 都还没有可机械求解的模板
        # 过滤：别的学科一条也取不到
        assert client.get("/api/proposition/item-models", params={"domain": "cs_it"}).json()["total"] == 0
        assert client.get("/api/proposition/item-models", params={"status": "confirmed"}).json()["total"] == 0

    def test_detail_includes_instances(self, client):
        mid = client.post("/api/proposition/item-models/extract", json={
            "domain_key": "math_logic", "markdown": EXAM_MD,
        }).json()[0]["id"]
        client.patch(f"/api/proposition/item-models/{mid}", json={"answer_expr": "p", "status": "confirmed"})
        inst = client.post(f"/api/proposition/item-models/{mid}/variations", json={
            "incidental_values": {"p": 5}, "variation_type": "isomorph",
        })
        assert inst.status_code == 200, inst.text
        assert inst.json()["answer"] == "5"
        assert inst.json()["verified_by"] == "tool_symbolic"
        detail = client.get(f"/api/proposition/item-models/{mid}").json()
        assert len(detail["instances"]) == 1
        assert detail["instances"][0]["item_model_id"] == mid

    def test_variation_409_is_surfaced(self, client):
        mid = client.post("/api/proposition/item-models/extract", json={
            "domain_key": "math_logic", "markdown": EXAM_MD,
        }).json()[0]["id"]
        resp = client.post(f"/api/proposition/item-models/{mid}/variations", json={
            "incidental_values": {}, "variation_type": "isomorph",
        })
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "item_model_not_confirmed"
        assert client.patch(f"/api/proposition/item-models/{mid}", json={"status": "confirmed"}).status_code == 200
        confirmed = client.post(f"/api/proposition/item-models/{mid}/variations", json={"incidental_values": {}})
        assert confirmed.status_code == 409
        assert confirmed.json()["detail"]["code"] == "answer_not_computable"
        assert confirmed.json()["detail"]["reason"]
        assert client.get("/api/proposition/item-instances").json() == []

    def test_variation_validation_errors(self, client):
        mid = client.post("/api/proposition/item-models/extract", json={
            "domain_key": "math_logic", "markdown": EXAM_MD,
        }).json()[0]["id"]
        assert client.post(f"/api/proposition/item-models/{mid}/variations", json={
            "incidental_values": {}, "variation_type": "weird"}).status_code == 422
        assert client.post("/api/proposition/item-models/nope/variations", json={
            "incidental_values": {}}).status_code == 404

    def test_instances_listing_filters(self, client):
        mid = client.post("/api/proposition/item-models/extract", json={
            "domain_key": "math_logic", "markdown": EXAM_MD,
        }).json()[0]["id"]
        assert client.patch(f"/api/proposition/item-models/{mid}", json={"answer_expr": "p", "status": "confirmed"}).status_code == 200
        made = client.post(f"/api/proposition/item-models/{mid}/variations", json={
            "incidental_values": {"p": 1}, "variation_type": "isomorph"})
        assert made.status_code == 200 and made.json()["answer"] == "1"
        disabled = client.post(f"/api/proposition/item-models/{mid}/variations", json={
            "incidental_values": {"p": 2}, "variation_type": "conceptual"})
        assert disabled.status_code == 409 and disabled.json()["detail"]["code"] == "variation_type_disabled"
        db = _db(client)
        try:
            historical = P.ItemInstance(item_model_id=mid, stem="历史概念变式", variation_type="conceptual", answer="", verified_by="none")
            db.add(historical)
            db.commit()
            historical_id = historical.id
        finally:
            db.close()
        rows = client.get("/api/proposition/item-instances").json()
        assert len(rows) == 2
        iso = client.get("/api/proposition/item-instances", params={"variation_type": "isomorph"}).json()
        assert len(iso) == 1 and iso[0]["verified_by"] == "tool_symbolic"
        con = client.get("/api/proposition/item-instances", params={"variation_type": "conceptual"}).json()
        assert len(con) == 1 and con[0]["answer"] == "" and con[0]["verified_by"] == "none"
        assert con[0]["id"] == historical_id and con[0]["stem"] == "历史概念变式"

    def test_principle_review_flow(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
        finally:
            db.close()

        made = client.post("/api/proposition/distill", json={"domain_key": "math_logic", "threshold": 3}).json()
        assert len(made) == 1 and made[0]["status"] == "proposed"
        # proposed 不出现在注入预览里
        preview = client.get("/api/proposition/principles/injection-preview", params={"domain": "math_logic"}).json()
        assert preview["injected"] == 0 and preview["confirmed_total"] == 0 and preview["selected"] == []
        assert preview["state_filtered"] == 1 and preview["stats"]["standards_injected"] > 0
        assert made[0]["id"] not in preview["text"] and "【命题经验" not in preview["text"]
        assert preview["text"].startswith("【命题评价标准")

        pid = made[0]["id"]
        listed = client.get("/api/proposition/principles", params={"domain": "math_logic", "status": "proposed"}).json()
        assert listed["total"] == 1 and listed["proposed"] == 1 and listed["confirmed"] == 0

        reviewed = client.post(f"/api/proposition/principles/{pid}/review", json={
            "action": "confirm", "rationale": "实测确认"})
        assert reviewed.status_code == 200 and reviewed.json()["status"] == "confirmed"

        preview2 = client.get("/api/proposition/principles/injection-preview", params={"domain": "math_logic"}).json()
        assert preview2["injected"] == 1 and preview2["confirmed_total"] == 1 and "DEFECT" in preview2["text"]
        # 再次评审 => 409
        again = client.post(f"/api/proposition/principles/{pid}/review", json={"action": "reject"})
        assert again.status_code == 409 and again.json()["detail"]["code"] == "already_reviewed"
        # 非法 action => 422
        bad = client.post(f"/api/proposition/principles/{pid}/review", json={"action": "maybe"})
        assert bad.status_code == 422
        assert client.post("/api/proposition/principles/nope/review", json={"action": "confirm"}).status_code == 404

    def test_distill_is_idempotent_over_http(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, count=3)
        finally:
            db.close()
        first = client.post("/api/proposition/distill", json={"domain_key": "math_logic", "threshold": 3}).json()
        second = client.post("/api/proposition/distill", json={"domain_key": "math_logic", "threshold": 3}).json()
        assert len(first) == 1 and second == []
        assert client.get("/api/proposition/principles").json()["total"] == 1

    def test_quality_records_listing(self, client):
        db = _db(client)
        try:
            goal = _goal(db)
            _seed_records(db, goal, code="DEFECT", count=2)
            _seed_records(db, goal, code="E018", count=1)
        finally:
            db.close()
        out = client.get("/api/proposition/quality-records", params={"domain": "math_logic"}).json()
        assert out["total"] == 3
        assert out["by_code"] == {"DEFECT": 2, "E018": 1}
        only = client.get("/api/proposition/quality-records", params={"code": "E018"}).json()
        assert only["total"] == 1 and only["items"][0]["motif"] == "f_n(x)=x^n+2nx-2"
        assert client.get("/api/proposition/quality-records", params={"domain": "cs_it"}).json()["total"] == 0

    def test_empty_library_keeps_framework_standards_without_experiences(self, client):
        assert client.get("/api/proposition/item-models").json() == {
            "total": 0, "items": [], "conflicts": 0, "solvable": 0}
        assert client.get("/api/proposition/principles").json()["total"] == 0
        assert client.get("/api/proposition/quality-records").json()["total"] == 0
        assert client.get("/api/proposition/item-instances").json() == []
        preview = client.get("/api/proposition/principles/injection-preview", params={"domain": "math_logic"}).json()
        assert preview["injected"] == preview["confirmed_total"] == preview["dropped"] == 0
        assert preview["selected"] == [] and preview["stats"]["standards_injected"] > 0
        assert preview["text"].startswith("【命题评价标准") and "【命题经验" not in preview["text"]
        assert preview["chars"] == len(preview["text"]) <= preview["budget_chars"]
        assert client.get("/api/proposition/item-models/nope").status_code == 404

# ------------------------------------------------------------------ 回归：抽取的确定性


_ONE_Q_PAPER = r"""
### 选择题

**1.**（20 分）设 $f_n(x)=x^n+2nx-2$ 的正根为 $a_n$，则 $\lim_{n\to\infty}(1+a_n)^n=$（　　）

A. $1$　　B. $e$　　C. $e^2$　　D. $+\infty$

### 参考答案

**1.** B
"""


class TestExtractionDeterminism:
    def test_duplicate_paper_names_do_not_collapse_occurrences(self, client):
        """回归：``_definition_index`` 用 ``(卷名, 题号)`` 当键，重名卷会互相覆盖。

        三节同名时，若不消歧，母题的出现次数会被算成 1 而不是 3——「同一母题重复
        考查」这个命题质量问题就被静默丢掉了。
        """
        db = _db(client)
        try:
            md = "".join(f"## 第一套试卷\n{_ONE_Q_PAPER}" for _ in range(3))
            models = P.extract_item_models(db, "math_logic", md)
            assert len(models) == 1
            occ = models[0]["radicals"]["occurrences"]
            assert len(occ) == 3, occ
            assert models[0]["radicals"]["occurrence_count"] == 3
            names = [o["paper"] for o in occ]
            assert len(set(names)) == 3, f"卷名仍不唯一：{names}"
        finally:
            db.close()

    def test_long_stem_truncation_keeps_slots_aligned(self, client):
        """回归：槽位是按字符偏移定位的，必须先截断再算偏移。

        走**真实抽取路径**（而不是手工塞 stem_sample）：若在完整题干上算偏移、
        却把截断后的文本存进 stem_sample，渲染时槽位就会错位，生成的变式会替换到
        **错误的位置**——那是比不改题面更糟的结果。
        """
        db = _db(client)
        try:
            long_tail = "补充说明" * 700      # 把题干块撑到远超 _MAX_STEM_CHARS
            md = (
                "## 第一套试卷\n\n### 选择题\n\n"
                f"**1.**（20 分）设 $f_n(x)=x^n+2nx-2$ 的正根为 $a_n$。{long_tail}\n\n"
                "A. $1$　　B. $e$\n\n### 参考答案\n\n**1.** B\n"
            )
            papers = P._papers_from_markdown(md, "")
            block = "\n".join(papers[0][1].blocks[1])
            assert len(block) > P._MAX_STEM_CHARS, "题干块不够长，测不到截断分支"
            models = P.extract_item_models(db, "math_logic", md)
            assert len(models) == 1
            radicals = models[0]["radicals"]
            sample = radicals["stem_sample"]
            assert len(sample) <= P._MAX_STEM_CHARS
            # 登记的槽位必须全部落在留存样本范围内（这是「先截断再算偏移」的判据）
            spans = radicals["slot_spans"]
            assert spans, "应至少登记一个槽位"
            slots = P._stem_slots(sample)
            assert len(slots) == len(spans)
            assert all(end <= len(sample) for _s, end, _o in slots)
            # 槽位原值与留存样本对应位置的文本一致（不错位）
            for (start, end, original), span in zip(slots, spans):
                assert sample[start:end] == original == span["original"]
            # 补录模板后能正常渲染：替换落在正确位置，不是静默失效
            P.patch_item_model(db, models[0]["id"], _patch(answer_expr="p1", status="confirmed"))
            inst = P.make_variation(db, models[0]["id"], {"p1": 7})
            assert inst["answer"] == "7"
            assert len(inst["stem"]) <= len(sample)
            assert sample[slots[0][0]:slots[0][1]] != inst["stem"][slots[0][0]:slots[0][1]]
        finally:
            db.close()

    def test_slot_names_are_positional(self, client):
        """抽取出的槽位用位置式命名 p1..pn（与 _render_stem 的位置替换一致）。"""
        db = _db(client)
        try:
            models = P.extract_item_models(db, "math_logic", EXAM_MD)
            fn = [m for m in models if m["title"] == "f_n(x)=x^n+2nx-2"][0]
            names = list(fn["incidentals"].keys())
            assert names == [f"p{i+1}" for i in range(len(names))]
            assert fn["radicals"]["slot_spans"][0]["name"] == "p1"
            assert all(s["original"] for s in fn["radicals"]["slot_spans"])
        finally:
            db.close()
