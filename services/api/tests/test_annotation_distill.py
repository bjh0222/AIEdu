# -*- coding: utf-8 -*-
"""人工批注 → AI 提炼命题规律：解析约定、逐字保留、提炼建档、降级路径。

覆盖四类风险：
1. 批注解析（两种约定 / 全半角冒号 / 题号归属）解析错归属=污染别的题的经验；
2. 抽取入库「逐字保留」与「只增不覆盖」（覆盖=销毁用户证据）；
3. AI 提炼建档（proposed、refs 绑定、statement 去重）；
4. 降级路径（demo / 无样本 / refs 绑不上）不抛异常。
"""
from __future__ import annotations

import pytest

from app.services import proposition as P
from app.services.utils import jdump
from app.models import DesignPrinciple, ItemModel


ANN_MD = r"""
## 第一套试卷

### 填空题

**1.**（20 分）设 $f_n(x)=x^n+2nx-2$ 的正根为 $a_n$，则 $\lim_{n\to\infty}(1+a_n)^n=$ ______。

<!-- 批注: 好题——不动点法典型载体，学生会忘了验证取正根 -->

**2.**（20 分）设参数方程 $x=\ln(t+\sqrt{1+t^2}), y=\arctan t$，则 $\dfrac{d^2x}{dy^2}=$ ______。【批注：难在隐含 t>0，二阶求导链条长】

### 参考答案

**1.** $e$

**2.** $\dfrac{\sqrt2}{2}
"""

NO_NOTE_MD = ANN_MD.replace("\n<!-- 批注: 好题——不动点法典型载体，学生会忘了验证取正根 -->", "").replace("【批注：难在隐含 t>0，二阶求导链条长】", "")


class FakeAI:
    """ai-like 测试替身：distill_proposition_principles 返回预置 payload。"""

    is_demo = False

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def distill_proposition_principles(self, system: str, user: str) -> list[dict]:
        self.calls.append((system, user))
        return self.payload


def _db(client):
    from app.db import SessionLocal

    return SessionLocal()


def _seed_model(db, domain: str, stem: str, note: str = "", points=None) -> ItemModel:
    row = ItemModel(
        domain_key=domain,
        title=stem[:30],
        knowledge_points=jdump(points or ["数列极限"]),
        radicals=jdump({"stem_sample": stem}),
        human_note=note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------- 解析


def test_parse_annotations_two_conventions_and_ownership():
    ann = P._parse_annotations(ANN_MD)
    assert len(ann) == 2
    by_num = {num: text for (_name, num), text in ann.items()}
    # 题号归属：题 1 之后的注释归题 1；题 2 行内的括注归题 2
    assert "不动点法" in by_num[1]
    assert "忘了验证取正根" in by_num[1]
    assert "难在隐含" in by_num[2]


def test_parse_annotations_marks_verbatim():
    """「保留批注」：原文逐字入库，不得转写。"""
    ann = P._parse_annotations(ANN_MD)
    text = next(iter(ann.values()))
    assert "好题——" in text  # 全角破折号等原样保留


def test_parse_annotations_empty():
    assert P._parse_annotations("") == {}
    assert P._parse_annotations(NO_NOTE_MD) == {}


# ---------------------------------------------------------------- 抽取集成


def test_extract_preserves_annotations(client):
    db = _db(client)
    try:
        models = P.extract_item_models(db, "math_logic", ANN_MD, chapter="期中")
        assert len(models) == 2
        fn = next(m for m in models if "f_n" in m["radicals"]["stem_sample"])
        ln = next(m for m in models if "f_n" not in m["radicals"]["stem_sample"])
        assert "不动点法" in fn["human_note"]
        assert "难在隐含" in ln["human_note"]
        # 无批注的卷 → human_note 为空，不编造
        fresh = P.extract_item_models(db, "cs_it", NO_NOTE_MD, chapter="期中")
        assert len(fresh) == 2
        assert all(m["human_note"] == "" for m in fresh)
    finally:
        db.close()


def test_re_extraction_never_overwrites_notes(client):
    """重复抽取同一卷带不同批注 → 只增不覆盖（已有批注是用户成果）。"""
    db = _db(client)
    try:
        models = P.extract_item_models(db, "math_logic", ANN_MD)
        target = next(m for m in models if "f_n" in m["radicals"]["stem_sample"])
        P.extract_item_models(db, "math_logic", NO_NOTE_MD)  # 同一卷去掉批注重抽
        after = P.get_item_model(db, target["id"])
        assert "不动点法" in after["human_note"]
    finally:
        db.close()


def test_api_extract_returns_human_note(client):
    """契约防回归：ItemModelOut 不声明 human_note 就会被 response_model 过滤。"""
    resp = client.post(
        "/api/proposition/item-models/extract",
        json={"domain_key": "math_logic", "markdown": ANN_MD, "chapter": ""},
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert any("不动点法" in m["human_note"] for m in items)


# ---------------------------------------------------------------- AI 提炼


def test_distill_with_notes_creates_proposed(client):
    db = _db(client)
    try:
        m1 = _seed_model(db, "math_logic", "f_n(x)=x^n+2nx-2 求极限", note="好题：不动点法，注意取正根")
        m2 = _seed_model(db, "math_logic", "参数方程二阶求导", note="难在隐含 t>0")
        ai = FakeAI([
            {
                "statement": "不动点型数列极限题应设置需要验证取正根的条件，考查严谨性",
                "principle_type": "discipline",
                "scope_knowledge_points": ["数列极限"],
                "positive_refs": [m1.id, m2.id],
                "negative_refs": [],
                "rationale": "来自两条批注的共同主题",
            }
        ])
        out = P.distill_principles_from_notes(db, ai, "math_logic")
        assert out["error"] == "" and out["notes_used"] == 2 and len(out["created"]) == 1
        p = out["created"][0]
        assert p["status"] == "proposed"
        assert p["evidence_count"] == 2
        refs = p["positive_refs"]
        assert {r["item_model_id"] for r in refs} == {m1.id, m2.id}
        assert refs[0]["note"] in ("好题：不动点法，注意取正根", "难在隐含 t>0")
        # 批注原文逐字进了 prompt（保留证据）
        system, user = ai.calls[0]
        assert "好题：不动点法，注意取正根" in user
        # 落库
        row = db.get(DesignPrinciple, p["id"])
        assert row is not None and row.status == "proposed"
    finally:
        db.close()


def test_distill_statement_dedup(client):
    """同学科同 statement 已存在（任何状态）→ 不重复建档。"""
    db = _db(client)
    try:
        m = _seed_model(db, "math_logic", "f_n(x) 求极限", note="好题")
        payload = [{
            "statement": "同一条规律", "positive_refs": [m.id],
            "scope_knowledge_points": [], "rationale": "r",
        }]
        first = P.distill_principles_from_notes(db, FakeAI(payload), "math_logic")
        assert len(first["created"]) == 1
        second = P.distill_principles_from_notes(db, FakeAI(payload), "math_logic")
        assert second["created"] == []
        assert db.query(DesignPrinciple).count() == 1
    finally:
        db.close()


def test_distill_unbound_refs_rejected_when_notes_used(client):
    """带批注提炼时，绑不上任何母题的规律不可回溯 → 拒绝入库。"""
    db = _db(client)
    try:
        _seed_model(db, "math_logic", "f_n(x) 求极限", note="好题")
        payload = [{"statement": "空话规律", "positive_refs": ["ghost-id"], "rationale": ""}]
        out = P.distill_principles_from_notes(db, FakeAI(payload), "math_logic")
        assert out["created"] == []
        assert db.query(DesignPrinciple).count() == 0
    finally:
        db.close()


def test_distill_no_notes_falls_back_to_stems(client):
    """无批注：AI 自行总结（notes_used=0），样本仍带进 prompt。"""
    db = _db(client)
    try:
        m = _seed_model(db, "math_logic", "f_n(x)=x^n+2nx-2 求极限", note="")
        ai = FakeAI([{"statement": "不动点构造适合做母题", "positive_refs": [m.id], "rationale": ""}])
        out = P.distill_principles_from_notes(db, ai, "math_logic")
        assert out["error"] == "" and out["notes_used"] == 0 and len(out["created"]) == 1
        assert "f_n(x)=x^n+2nx-2" in ai.calls[0][1]
    finally:
        db.close()


def test_distill_demo_and_no_samples_degrade(client):
    db = _db(client)
    try:
        # demo：不调模型，如实报告
        out = P.distill_principles_from_notes(db, FakeAI([{"statement": "x"}]), "math_logic")
        assert out["error"] == "no_samples"
        _seed_model(db, "math_logic", "f_n(x) 求极限", note="好题")
        demo = FakeAI([])
        demo.is_demo = True
        out = P.distill_principles_from_notes(db, demo, "math_logic")
        assert out["error"] == "demo" and out["created"] == []
        # 模型抛异常：不外溢，错误留痕
        class Boom:
            is_demo = False
            def distill_proposition_principles(self, system, user):
                raise RuntimeError("模型超时")

        out = P.distill_principles_from_notes(db, Boom(), "math_logic")
        assert "模型超时" in out["error"] and out["created"] == []
    finally:
        db.close()
