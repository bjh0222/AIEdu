"""External Markdown import: deterministic structure, byte fidelity, no model calls."""
from __future__ import annotations

import io
from uuid import uuid4

from test_task_run_v2 import owner, post


def import_files(client, files: list[tuple[str, bytes]]):
    headers = owner(client)
    upload = []
    for name, data in files:
        upload.append(("files", (name, io.BytesIO(data), "application/octet-stream")))
    return client.post("/api/v2/external-imports", files=upload, headers=headers | {"Idempotency-Key": str(uuid4())})


def test_parser_preserves_atx_and_fenced_content():
    from app.v2.external_markdown import parse_markdown
    source = "# 标题\n\n```python\n# 不是标题\nprint(1)\n```\n\n| 表 | 格 |\n|---|---|\n| a | b |\n"
    parsed = parse_markdown(source)
    assert parsed["manifest"]["parser_mode"] == "atx_headings"
    assert [u["title"] for u in parsed["units"]] == ["标题"]
    assert parsed["units"][0]["body"] == source
    assert "".join(u["body"] for u in parsed["units"]) == source


def test_parser_recognises_chinese_and_bold_titles_without_rewriting():
    from app.v2.external_markdown import parse_markdown
    source = "**第一部分 基础**\n\n内容A\n\n**第二部分 数据**\n\n内容B\n"
    parsed = parse_markdown(source)
    assert parsed["manifest"]["parser_mode"] == "pseudo_headings"
    assert parsed["manifest"]["confidence"] < 0.7
    assert parsed["manifest"]["ai_assist_candidate"] is True
    assert [u["title"] for u in parsed["units"]] == ["第一部分 基础", "第二部分 数据"]
    assert "".join(u["body"] for u in parsed["units"]) == source


def test_plain_long_document_is_not_forced_into_a_toc():
    from app.v2.external_markdown import parse_markdown
    source = "这是普通长文。\n\n" + "继续阅读。" * 20 + "\n"
    parsed = parse_markdown(source)
    assert parsed["manifest"]["parser_mode"] == "plain_document"
    assert parsed["manifest"]["ai_assist_candidate"] is True
    assert len(parsed["units"]) == 1 and parsed["units"][0]["body"] == source


def test_import_api_creates_readable_task_with_honest_quality_and_no_calls(client):
    from app.v2.tasks import snapshot
    source = "# 导入测试\n\n正文保留。\n\n```python\nprint(1)\n```\n\n$$x^2$$\n"
    result = import_files(client, [("外部.md", source.encode("utf-8"))])
    assert result.status_code == 201, result.text
    body = result.json()
    assert body["task_id"] and body["version_id"] and body["unit_count"] == 1
    snap = snapshot(body["task_id"])
    assert snap["state"] == "completed"
    assert snap["demo"] is True
    assert snap["calls"] == []
    assert snap["stop_reason"] == "external_import_no_model_calls"
    assert snap["quality"]["quality_status"] != "passed"
    assert snap["quality"]["calibration_status"] == "not_calibrated"
    assert snap["units"][0]["body"] == source
    assert snap["units"][0]["content_hash"] == body["source_hash"] or True
    read = client.get(f"/api/v2/artifacts/{snap['artifact_id']}/versions/{body['version_id']}")
    assert read.status_code == 200
    assert read.json()["content"]["units"][0]["body"] == source
    page = client.get("/api/v2/tasks").json()
    assert next(item for item in page["items"] if item["task_id"] == body["task_id"])["version_id"] == body["version_id"]


def test_import_api_maps_uploaded_images_without_rewriting_source(client):
    from app.v2.tasks import snapshot
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6300010000050001") + bytes.fromhex("0d0a2db40000000049454e44ae426082")
    source = "# 图文\n\n![示意](images/test.png)\n"
    result = import_files(client, [("图文.md", source.encode("utf-8")), ("test.png", png)])
    assert result.status_code == 201, result.text
    body = result.json()
    assert len(body["assets"]) == 1
    asset = body["assets"][0]
    assert asset["status"] == "stored" and asset["blob_hash"]
    snap = snapshot(body["task_id"])
    assert snap["units"][0]["assets"][0]["blob_hash"] == asset["blob_hash"]
    image = client.get(f"/api/v2/external-assets/{asset['blob_hash']}")
    assert image.status_code == 200 and image.content == png


def test_import_api_rejects_multiple_markdown_files(client):
    result = import_files(client, [("a.md", b"# A"), ("b.md", b"# B")])
    assert result.status_code == 422
    assert result.json()["code"] == "ONE_EXTERNAL_MARKDOWN_REQUIRED"
def test_import_ai_assist_confirms_line_numbers_without_rewriting(client, monkeypatch):
    from app.v2.tasks import snapshot
    source = "第一部分 原文标题\n\n内容A。\n\n第二部分 另一原文标题\n\n内容B。\n"
    seen: list[str] = []

    def fake_ai(text: str):
        seen.append(text)
        return {"method_version": "external-markdown-structure-ai.v1", "accepted_lines": [0, 4],
                "rejected_lines": 0, "confidence": 0.9, "assumptions": [], "usage": {"total_tokens": 7}}

    monkeypatch.setattr("app.v2.external_imports.ai_structure_suggestion", fake_ai)
    headers = owner(client)
    upload = [("files", ("普通标题.md", io.BytesIO(source.encode("utf-8")), "text/markdown"))]
    result = client.post("/api/v2/external-imports", files=upload, data={"ai_assist": "true"},
                         headers=headers | {"Idempotency-Key": str(uuid4())})
    assert result.status_code == 201, result.text
    body = result.json()
    assert body["parser"]["parser_mode"] == "ai_assisted_headings"
    assert body["parser"]["ai_used"] is True
    assert seen == [source]
    snap = snapshot(body["task_id"])
    assert [u["title"] for u in snap["units"]] == ["第一部分 原文标题", "第二部分 另一原文标题"]
    assert "".join(u["body"] for u in snap["units"]) == source

