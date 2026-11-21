"""Synthetic legacy-shape fixtures, never private specs or source documents."""
import copy
import json

import pytest

from test_task_run_v2 import owner, post


def context(scene="exercise"):
    from app.v2.resolver import resolve_task
    role = "exercise" if scene == "exercise" else "tutorial"
    structure = ("### 一、易错辨析\n### 二、训练\n### 参考答案" if scene == "exercise" else
                 "本章概要 深度导读 全书认知地图 阅读路线")
    return resolve_task({"title": "合成全书", "raw_input": "仅根据全部所选材料", "domain_key": "math_logic",
        "branch": "", "requested_artifacts": [role, "answer"] if role == "exercise" else [role],
        "spec_bindings": [{"scene": scene, "artifact_role": role, "snapshot": {"scene": scene, "content_structure": structure}}]})


BOOK = "# 合成材料\n\n**第1章 目录**\n**第2章 目录**\n\n## 第1章 集合\n### 习题1.1\n集合问题。\n\n## 第2章 矩阵\n### 习题2.1\n矩阵问题。\n"


def test_chapter_chunking_is_lossless_and_never_splits_across_chapters():
    from app.v2.materials import chunk_text, verify_coverage
    from app.v2.resolver import build_units
    chunks = chunk_text(BOOK, "s", max_bytes=20)
    assert verify_coverage(BOOK, chunks)["complete"]
    units = build_units(context(), chunks)
    assert [u["title"] for u in units] == ["第1章 集合", "第2章 矩阵"]
    assert len(units) == 2  # even with many capacity fragments
    assert set(c["chunk_id"] for c in chunks) == {c for u in units for c in u["material_chunk_ids"]}
    for unit in units:
        injected = [c for c in chunks if c["chunk_id"] in unit["material_chunk_ids"]]
        assert all(not c.get("chapter_title") or c["chapter_title"] == unit["title"] for c in injected)
        assert len(unit["source_sections"]) == 1


def test_reading_two_book_units_generate_last_but_display_first_and_use_digests():
    from app.v2.format_runtime import reading_digests, section_body
    from app.v2.materials import chunk_text
    from app.v2.resolver import build_units
    units = build_units(context("reading_guide"), chunk_text(BOOK, "s", max_bytes=20))
    assert [u["title"] for u in units] == ["第1章 集合", "第2章 矩阵", "全书认知地图", "阅读路线"]
    assert [u["title"] for u in sorted(units, key=lambda u:u["display_order"])][:2] == ["全书认知地图", "阅读路线"]
    for unit in units[2:]:
        assert unit["depends_on"] == [u["unit_id"] for u in units[:2]]
        assert unit["material_chunk_ids"] == []
    chapters = [u | {"body": "### 本章概要\n概要"*5+"\n### 深度导读\n不应复制全文\n#### 论证结构分析\n论证\n#### 难点辨析\n细节"} for u in units[:2]]
    digests = reading_digests(chapters)
    assert len(digests) == 2 and all("不应复制全文" not in d["digest"] and "论证" in d["digest"] for d in digests)
    bounded = reading_digests(chapters, total_chars=10)
    assert len(bounded) == 2 and all(d["truncated"] for d in bounded)
    assert section_body("### 本章概要\n正文\n### 深度导读\n细节", "第1章 集合", 3).startswith("## 三、第1章 集合\n")


def test_no_chapter_is_one_unit_not_capacity_pseudo_chapters():
    from app.v2.materials import chunk_text
    from app.v2.resolver import build_units
    for scene in ("exercise", "reading_guide"):
        units = build_units(context(scene), chunk_text("无章论文正文。"*1000, "s", max_bytes=40))
        assert len(units) == 1 and not units[0]["depends_on"]


def test_chapter_parser_ignores_fenced_samples_and_heading_only_toc():
    from app.v2.format_runtime import chapter_ranges
    text = "```md\n# 第9章 示例\n伪正文\n```\n## 第1章 目录\n## 第2章 目录\n## 第1章 正文\n真正正文\n"
    assert [r["chapter_title"] for r in chapter_ranges(text)] == ["第1章 正文"]


def candidates():
    return {"items": [
        {"question_type": "综合题", "stem_markdown": "合成证明题。", "source_sections": ["习题2.1"],
         "answer": {"final": "结论", "reasoning": "完整内部证明", "display_reasoning": "关键论证"}},
        {"question_type": "判断题", "stem_markdown": "合成判断题。", "source_sections": [],
         "answer": {"final": "正确", "reasoning": "内部依据"}},
        {"question_type": "基础概念题", "stem_markdown": "合成概念题。", "source_sections": [],
         "answer": {"final": "结论", "reasoning": "内部完整推导"}}]}


def test_exercise_projection_preserves_coupling_numbering_and_answer_verbosity():
    from app.v2.items import normalize_items, render_unit_items, project_units
    from app.v2.format_runtime import EXERCISE_LAYOUT
    items = normalize_items(candidates(), "exercise", "u", EXERCISE_LAYOUT, ["习题2.1"])
    assert [i["ordinal"] for i in items] == [1, 2, 3]
    assert [i["question_type"] for i in items] == ["判断题", "基础概念题", "综合题"]
    unit = {"unit_id":"u", "title":"第2章 矩阵", "section_number":2, "artifact_role":"exercise",
            "artifact_roles":["exercise","answer"], "format_contract":EXERCISE_LAYOUT, "items":items}
    body = render_unit_items(items, unit)
    assert body.startswith("## 二、第2章 矩阵")
    assert body.count("### 参考答案") == 1 and body.count("解析：") == 1
    assert "内部" not in body and "<input" not in body and "- [ ]" in body
    assert body.index("### 二、训练") < body.index("### 参考答案")
    assert [u["body"] for u in project_units([unit])][0] == body


@pytest.mark.parametrize("fault", ["missing_section", "missing_type", "bad_reasoning", "score"])
def test_invalid_exercise_contract_fails_without_publishing_fake_conformance(fault):
    from app.v2.items import normalize_items
    from app.v2.format_runtime import EXERCISE_LAYOUT
    from app.v2.contracts import DomainError
    value = candidates()
    if fault == "missing_section": value["items"][0]["source_sections"] = []
    if fault == "missing_type": value["items"][0].pop("question_type")
    if fault == "bad_reasoning": value["items"][0]["answer"]["display_reasoning"] = ""
    if fault == "score": value["items"][0]["score"] = 10
    with pytest.raises(DomainError):
        normalize_items(value, "exercise", "u", EXERCISE_LAYOUT, ["习题2.1"])


def test_demo_inherited_exercise_snapshot_and_export_keep_chapter_answers(client):
    headers = owner(client)
    value = context()
    created = post(client, "/tasks", {k:v for k,v in value.items() if k in {"title", "raw_input", "domain_key", "branch", "spec_bindings", "requested_artifacts"}}, headers)
    assert created.status_code == 201, created.text
    created = created.json()
    post(client, f"/tasks/{created['task_id']}/confirm", {"expected_revision":1,"context_hash":created["context_hash"]}, headers)
    result = post(client, f"/tasks/{created['task_id']}/runs", {"expected_revision":1,"confirmed_context_hash":created["context_hash"]}, headers)
    assert result.status_code == 202, result.text
    snap = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    assert snap["state"] == "completed", snap
    assert snap["units"][0]["body"].count("### 参考答案") == 1
    export = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                  {"expected_revision":snap["state_revision"],"version_id":snap["version_id"], "kind":"available_draft"}, headers).json()
    body = client.get(f"/api/v2/export-sets/{export['bundle_id']}/files/main").text
    assert "### 参考答案" in body and "demo：true" in body


def test_empty_task_export_is_editable_gfm_and_preserves_code_examples():
    from app.v2.exports import safe_markdown
    raw = "- [ ]\n- [x]\n\n```md\n- [ ]\n```\n\n`- [ ]`"
    result, changes = safe_markdown(raw, {})
    assert result.startswith("- [ ] 待勾选\n- [x] 待勾选")
    assert "```md\n- [ ]\n```" in result and "`- [ ]`" in result
    assert changes == [{"kind":"empty_task_label_added"}, {"kind":"empty_task_label_added"}]
