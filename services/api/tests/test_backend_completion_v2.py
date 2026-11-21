"""Second-round contracts; fixtures only, no network, no production data."""
from __future__ import annotations

import io
import json
import sqlite3
import zipfile
from uuid import uuid4

import pytest

from test_task_run_v2 import owner,post


def policy():
    return {"disk_reserve_bytes":0,"temp_quota_bytes":64*1024**2,"extractor_memory_bytes":256*1024**2,
        "extractor_timeout_seconds":15,"max_extract_workers":1,"max_expanded_bytes":16*1024**2,
        "max_archive_entries":1000,"ingress_body_policy":"stream","cancel_grace_seconds":2,
        "policy_version":"test-only-measured-separately","chunk_bytes":4096}


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream,"w",zipfile.ZIP_DEFLATED) as archive:
        for name,body in files.items():
            info = zipfile.ZipInfo(name)
            info.filename = name  # preserve malicious backslash fixtures on Windows.
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info,body)
    return stream.getvalue()


def test_docx_extraction_has_original_locator_and_complete_hash(tmp_path):
    from app.v2.materials import ingest
    from app.v2.storage import BlobStore
    raw = zipped({"word/document.xml":'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文原始材料</w:t></w:r></w:p></w:body></w:document>'})
    store = BlobStore(tmp_path/"blobs")
    result = ingest(io.BytesIO(raw),policy(),blob_store=store,filename="lesson.docx")
    assert result["coverage"]["complete"]
    assert result["memory_limiter"] in {"windows_job_commit","rlimit_as"}
    assert store.get(result["extracted_hash"]).decode() == "中文原始材料"
    assert result["chunks"][0]["original_locators"] == ["docx:paragraph:1"]


@pytest.mark.parametrize("entry",["../escape.xml","C:/escape.xml","x\\escape.xml"])
def test_document_archive_traversal_is_rejected(tmp_path,entry):
    from app.v2.materials import ingest
    from app.v2.storage import BlobStore
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError,match="UNSAFE_ARCHIVE_ENTRY"):
        ingest(io.BytesIO(zipped({entry:"x"})),policy(),blob_store=BlobStore(tmp_path/"blobs"),filename="bad.docx")


def test_epub_uses_spine_order_not_archive_filename_order(tmp_path):
    from app.v2.materials import ingest
    from app.v2.storage import BlobStore
    raw = zipped({"META-INF/container.xml":'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
        "OEBPS/book.opf":'<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>',
        "OEBPS/a.xhtml":"<p>第二章</p>","OEBPS/b.xhtml":"<p>第一章</p><script>不应执行</script>"})
    store = BlobStore(tmp_path/"blobs")
    result = ingest(io.BytesIO(raw),policy(),blob_store=store,filename="book.epub")
    assert store.get(result["extracted_hash"]).decode() == "第一章\n\n第二章"


def test_scanned_pdf_fails_closed_without_ocr(tmp_path):
    from pypdf import PdfWriter
    from app.v2.materials import ingest
    from app.v2.storage import BlobStore
    from app.v2.contracts import DomainError
    writer = PdfWriter()
    writer.add_blank_page(width=200,height=200)
    stream = io.BytesIO()
    writer.write(stream)
    with pytest.raises(DomainError,match="PDF_OCR_REQUIRED"):
        ingest(io.BytesIO(stream.getvalue()),policy(),blob_store=BlobStore(tmp_path/"blobs"),filename="scan.pdf")


def test_xml_entities_and_zip_expansion_fail_closed(tmp_path):
    from app.v2.materials import ingest
    from app.v2.storage import BlobStore
    from app.v2.contracts import DomainError
    raw = zipped({"word/document.xml":'<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///private">]><x>&xxe;</x>'})
    with pytest.raises(DomainError,match="EXTRACTION_FAILED"):
        ingest(io.BytesIO(raw),policy(),blob_store=BlobStore(tmp_path/"blobs"),filename="bad.docx")
    small = policy() | {"max_expanded_bytes":1024}
    with pytest.raises(DomainError,match="ARCHIVE_EXPANSION_LIMIT"):
        ingest(io.BytesIO(zipped({"word/document.xml":"x"*8192})),small,blob_store=BlobStore(tmp_path/"blobs"),filename="bomb.docx")


def test_portable_export_links_and_active_content_are_transformed():
    from app.v2.exports import safe_markdown
    source = '[[一章|阅读]] [[缺失|降级]] <script>alert(1)</script> [坏](javascript:alert(1)) ![远图](https://a.test/x.png)\n```html\n<script>literal</script>\n```'
    body,changes = safe_markdown(source,{"一章":"u.md"})
    assert "[阅读](u.md)" in body and "[[" not in body
    assert "javascript:" not in body and "![远图]" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>literal</script>" in body
    assert changes


def test_all_asset_recovery_is_hash_verified_and_non_overwriting(tmp_path):
    from app.v2.migration import backup_asset_set,restore_asset_set
    from app.v2.contracts import DomainError
    source = tmp_path/"test_source.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE things(id TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO things VALUES('one','original')")
    files = tmp_path/"test_assets"
    files.mkdir()
    (files/"fake-private-config.json").write_text('{"fixture":true}')
    backup = tmp_path/"test_package"
    recorded = backup_asset_set(source,{"private_config":files},backup,quiescent=True)
    target = tmp_path/"test_restored"
    restored = restore_asset_set(backup,target)
    assert restored["manifest_hash"] == recorded["manifest_hash"] and restored["restored_files"] == 1
    assert restored["integrity_check"] == "ok" and restored["production_restore"] == "not_run"
    with pytest.raises(DomainError,match="RESTORE_TARGET_EXISTS"):
        restore_asset_set(backup,target)
    (backup/"assets"/"private_config"/"fake-private-config.json").write_text("corrupt")
    with pytest.raises(DomainError,match="RECOVERY_ASSET_CORRUPTED"):
        restore_asset_set(backup,tmp_path/"test_corrupt_restore")


def test_raw_sql_bulk_and_rollback_are_in_delta(client):
    from app.db import SessionLocal
    from app.v2.storage import MigrationDelta
    from sqlalchemy import text
    with SessionLocal() as db:
        db.execute(text("INSERT INTO proposition_config(id,settings_json) VALUES('test_raw','{}')"))
        db.commit()
        row = db.query(MigrationDelta).filter_by(entity_type="proposition_config",entity_id="test_raw").one()
        assert json.loads(row.after_json)["id"] == "test_raw"
        db.execute(text("UPDATE proposition_config SET settings_json='changed' WHERE id='test_raw'"))
        db.rollback()
        assert db.query(MigrationDelta).filter_by(entity_id="test_raw").count() == 1
        db.execute(text("DELETE FROM proposition_config WHERE id='test_raw'"))
        db.commit()
        assert db.query(MigrationDelta).filter_by(entity_id="test_raw",operation="delete").count() == 1


def test_synthetic_benchmark_never_claims_human_labels_or_calibration():
    from app.v2.benchmark import run_controlled_benchmark,aggregate_panel
    report = run_controlled_benchmark()
    assert report["professional_calibration"] == "not_calibrated"
    assert report["dataset"]["human_labels"] == 0
    assert len(report["metrics_by_family"]) == 3
    assert aggregate_panel([{"model_family":"glm","label":"clean","counterfactual_consistent":True}],
        generator_family="deepseek",checker_family="glm",refiner_family="deepseek",reliability={"glm":1})["label"] == "unsupported"


def test_spec_assets_have_cas_idempotency_and_survive_legacy_quarantine(client,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,"enable_legacy_api",False)
    headers = owner(client)
    payload = {"spec":{"name":"原规范","scene":"knowledge_tutorial","output_goal":"保留所有字段","custom_markdown":"原文\r\n不改"}}
    first = post(client,"/format-specs",payload,headers,"new-spec")
    assert first.status_code == 201,first.text
    assert post(client,"/format-specs",payload,headers,"new-spec").json() == first.json()
    update = {"expected_version":1,"spec":payload["spec"]|{"name":"新版"}}
    url = "/api/v2/format-specs/"+first.json()["id"]
    saved = client.put(url,json=update,headers=headers|{"Idempotency-Key":"update"})
    assert saved.status_code == 200 and saved.json()["version"] == 2
    stale = client.put(url,json=update,headers=headers|{"Idempotency-Key":"stale"})
    assert stale.status_code == 409
    assert client.get("/api/v2/format-specs").status_code == 200
    assert client.get("/api/format-specs").status_code == 410


def test_single_lesson_does_not_multiply_model_outline_into_full_tutorials():
    from app.v2.resolver import build_units,resolve_task
    from app.v2.contracts import TaskInput
    p = TaskInput(title="宋代科举",raw_input="写一节600字的宋代科举知识教程")
    context = resolve_task(p.model_dump(),p.model_fields_set)
    units = build_units(context,outline=[{"title":"背景"},{"title":"制度"},{"title":"影响"}])
    assert len(units) == 1


def test_subject_rules_are_scoped_to_cross_domain_units():
    from app.v2.resolver import build_units,resolve_task
    from app.v2.contracts import TaskInput
    p = TaskInput(title="交叉学习",raw_input="多章：数学和历史分别讲解")
    context = resolve_task(p.model_dump(),p.model_fields_set)
    units = build_units(context,outline=[{"title":"数学","domain_key":"math_logic"},{"title":"历史","domain_key":"history_culture"}])
    assert [{s["domain_key"] for s in u["subject_bindings"]} for u in units] == [{"math_logic"},{"history_culture"}]


@pytest.mark.parametrize("domain",["history_culture","arts_creativity","math_logic"])
def test_spec_package_is_real_data_with_positive_negative_unknown_tests(client,domain):
    from app.v2.spec_packages import build_package
    from app.schemas import FormatSpecIn
    from app.v2.quality import compile_plan,deterministic_checks
    from app.v2.contracts import sha
    scene = "exercise" if domain == "math_logic" else "practical_tutorial" if domain == "arts_creativity" else "knowledge_tutorial"
    package = build_package(domain,"任意分支",scene)
    spec = FormatSpecIn.model_validate(package["spec"])
    assert spec.required_rules and spec.acceptance_criteria and spec.reference_examples
    assert {c["kind"] for c in package["cases"]} == {"positive","negative","unknown"}
    plan = compile_plan({"domain_key":domain,"spec_bindings":[{"scene":scene,"artifact_role":"exercise" if scene == "exercise" else "tutorial"}],
                         "quality_profiles":[package["quality_profile"]]})
    good = {"unit_id":"u","body":"# 受控样例\n\n```text\nexample\n```","artifact_role":"tutorial"}
    good["content_hash"] = sha(good["body"])
    report = deterministic_checks(plan,[good],"v")
    assert next(c for c in report["checks"] if c["item_id"] == "C2.structure")["result"] == "satisfied"
    assert report["quality_status"] != "passed"
    bad = good|{"body":"```text\nexample\n"}
    bad["content_hash"] = sha(bad["body"])
    report = deterministic_checks(plan,[bad],"v")
    assert any(f["status"] == "confirmed" for f in report["findings"])
    assert report["quality_status"] == "failed"


def test_polynomial_verifier_has_bounded_expansion():
    from app.v2.verifiers import polynomial_identity
    from app.v2.contracts import DomainError
    assert polynomial_identity("(x+1)**2","x*x+2*x+1",["x"],"hash")["result"] == "satisfied"
    assert polynomial_identity("2","3",[],"hash")["result"] == "violated"
    with pytest.raises(DomainError,match="VERIFIER_RESOURCE_LIMIT"):
        polynomial_identity("((((x+1)**8)**8)**8)**8","0",["x"],"hash")


def test_quality_reducer_matches_five_state_priority_without_hiding_known_failure():
    from app.v2.quality import reduce_quality
    item = {"item_id":"one","required":True,"severity_class":"blocker"}
    plan = {"check_items":[item]}
    confirmed = [{"status":"confirmed","severity_class":"blocker","finding_id":"f"}]
    one = reduce_quality(plan,[{"item_id":"one","result":"violated"}],confirmed)
    assert one["tqi_verdict"] == "needs_repair" and one["quality_status"] == "failed"
    assert one["calibration_status"] == "not_calibrated"
    assert reduce_quality(plan,[{"item_id":"one","result":"not_run"}],[])["tqi_verdict"] == "blocked"
    assert reduce_quality(plan,[{"item_id":"one","result":"violated"}],confirmed,"calibrated")["tqi_verdict"] == "needs_repair"


def test_knowledge_analysis_blinds_answer_and_uses_one_root(client,monkeypatch):
    from app.db import SessionLocal
    from app.models import PropositionBatch,PropositionQuestion,PropositionEvaluation
    from app.config import settings
    from app.v2 import knowledge,worker,model_roles
    from app.v2.contracts import TaskStart
    from app.v2.tasks import task_start,snapshot
    from types import SimpleNamespace
    qid,bid = str(uuid4()),str(uuid4())
    with SessionLocal() as db:
        db.add(PropositionBatch(id=bid,filename="fixture",domain_key="math_logic",source_text="原题",source_hash="source"))
        db.add(PropositionQuestion(id=qid,batch_id=bid,domain_key="math_logic",raw_stem="1+1=?",raw_answer="PRIVATE ANSWER",
            external_evaluation="ORIGINAL EXTERNAL",source_map="{}",parse_status="ready"))
        db.commit()
    monkeypatch.setattr(settings,"v2_allow_paid",True)
    def profile(role,independent_of=()):
        return SimpleNamespace(llm_base_url="https://api.deepseek.com/v1",llm_model="deepseek-flash",llm_configured=True,
            llm_model_family="glm" if role == "blind_solver" else "deepseek",llm_thinking="enabled",llm_reasoning_effort="high",llm_max_tokens=0)
    monkeypatch.setattr(knowledge,"for_role",profile,raising=False)
    monkeypatch.setattr(model_roles,"for_role",profile)
    monkeypatch.setattr("app.model_settings.runtime_settings",lambda:profile("generation"))
    inputs = []
    class Provider:
        def complete_json(self,system,user):
            inputs.append((system,user))
            if "independently" in system:
                return {"answer":"2","reasoning":"算术"},1,{"total_tokens":2}
            return {"analysis":"reference only","candidate_principles":[]},1,{"total_tokens":2}
    started = task_start(TaskStart(raw_input="分析选定题目",domain_key="math_logic",operation="knowledge_analysis",
        knowledge={"question_ids":[qid],"analyse":True}),str(uuid4()))
    worker.execute_run(started["run_id"],lambda _:Provider())
    snap = snapshot(started["task_id"])
    assert snap["state"] == "completed",snap
    assert len(snap["calls"]) == 2 and snap["knowledge_results"]
    assert "PRIVATE ANSWER" not in inputs[0][1] and "ORIGINAL EXTERNAL" not in inputs[0][1]
    with SessionLocal() as db:
        assert db.get(PropositionQuestion,qid).external_evaluation == "ORIGINAL EXTERNAL"
        evaluation = db.query(PropositionEvaluation).filter_by(question_id=qid).one()
        assert json.loads(evaluation.report)["root_run_id"] == started["run_id"]
