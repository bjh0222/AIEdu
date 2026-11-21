"""真实 Markdown 回归：题目边界、外评保真，以及删除后的证据一致性。"""
from pathlib import Path
import threading

import pytest

from app.db import SessionLocal
from app.models import (
    DesignPrinciple, DesignPrincipleRevision, PropositionAnalysisJob,
    PropositionBatch, PropositionEmbedding, PropositionEvaluation, PropositionQuestion,
)
from app.schemas import PropositionBatchImport
from app.services import proposition_knowledge as knowledge
from app.services.utils import jdump, jload


SOURCE = (Path(__file__).parent / "fixtures" / "proposition_math_evaluated.md").read_text(encoding="utf-8")


def upload(client, text=SOURCE):
    response = client.post("/api/proposition/batches/import", json=PropositionBatchImport(
        filename="数学.md", markdown=text, domain_key="math_logic", branch="calculus", auto_analyse=False,
    ).model_dump())
    assert response.status_code == 200
    return response.json()


def test_real_math_upload_keeps_six_complete_stems_and_reports(client):
    result = upload(client)
    assert result["counts"]["total"] == result["counts"]["parsed"] == 6
    assert result["source_text"] == SOURCE
    assert [q["question_number"] for q in result["questions"]] == ["S1-1", "S2-2", "S4-3", "S3-3", "S2-3", "S4-2"]
    for question, raw in zip(result["questions"], SOURCE.split("\n## ")[1:]):
        expected_stem, expected_report = raw.split("**题目**\n\n", 1)[1].split("**评价**", 1)
        assert question["raw_stem"].strip() == expected_stem.strip()
        assert question["external_evaluation"].strip() == ("**评价**" + expected_report).strip()
        assert not question["raw_notes"]
        assert not question["raw_explanation"]
        lines = SOURCE.splitlines(keepends=True)
        assert question["raw_text"] == "".join(lines[question["source_line_start"] - 1:question["source_line_end"]])
    first = result["questions"][0]
    assert all(line in first["raw_stem"] for line in ("设 $f(x),g(x)$", "1. $f(x+y)", "2. $f(x),g(x)$", "3. $f(0)=0", "证明：$f(x)$"))
    assert "D. 若" in result["questions"][3]["raw_stem"]
    assert upload(client)["id"] == result["id"]


@pytest.mark.parametrize("title", ["## 第1题", "## 导数定义迁移", "## S1-1 抽象函数方程"])
def test_numbered_conditions_and_proof_target_are_one_question(client, title):
    text = f"{title}\n\n**题目**\n\n设函数满足以下条件。\n1. 条件一\n2. 条件二\n\n证明：结论成立。\n\n**评价**\n- **解答**：外部证明\n- **结论**：推荐\n"
    result = upload(client, text)
    assert result["counts"]["total"] == 1
    question = result["questions"][0]
    assert "条件一" in question["raw_stem"] and "条件二" in question["raw_stem"]
    assert "证明：结论成立。" in question["raw_stem"]
    assert "外部证明" in question["external_evaluation"]
    assert "外部证明" not in question["raw_explanation"]


def test_title_without_stem_does_not_pass_parse(client):
    result = upload(client, "## S1-1 空题\n\n**题目**\n\n**评价**\n- 推荐\n")
    assert result["counts"]["parse_failed"] == 1


def test_reupload_after_parser_upgrade_rebuilds_from_original(client):
    with SessionLocal() as db:
        old = PropositionBatch(filename="数学.md", domain_key="math_logic", branch="calculus", source_text=SOURCE,
            source_hash=knowledge.digest(jdump(["math_logic", "calculus", "", SOURCE])))
        db.add(old)
        db.commit()
        old_id = old.id
    repaired = upload(client)
    assert repaired["id"] != old_id and not repaired["duplicate"]
    assert repaired["counts"]["total"] == 6


def test_delete_question_removes_evaluations_and_reassesses_shared_principle(client):
    batch = upload(client)
    qid, other = [q["id"] for q in batch["questions"][:2]]
    with SessionLocal() as db:
        framework = knowledge.active_framework(db, "math_logic")
        db.add(PropositionEvaluation(question_id=qid, framework_id=framework.id))
        principle = DesignPrinciple(status="auto_accepted", approval_source="automatic", evidence_count=2, evidence_family_count=2,
            positive_refs=jdump([{"question_id": qid, "family_key": "one"}, {"question_id": other, "family_key": "two"}]))
        db.add(principle)
        db.flush()
        pid = principle.id
        db.add(PropositionEmbedding(source_id=pid, source_type="principle", model_key="test"))
        db.add(PropositionAnalysisJob(batch_id=batch["id"], status="done", question_ids=jdump([qid, other])))
        db.commit()
    response = client.delete(f"/api/proposition/questions/{qid}")
    assert response.status_code == 200 and response.json()["evaluations_deleted"] == 1
    assert client.get(f"/api/proposition/questions/{qid}").status_code == 404
    remaining = client.get(f"/api/proposition/batches/{batch['id']}").json()
    assert remaining["counts"]["total"] == 5 and remaining["source_text"] == SOURCE
    with SessionLocal() as db:
        principle = db.get(DesignPrinciple, pid)
        assert principle.status == "proposed" and not principle.approval_source
        assert principle.evidence_count == principle.evidence_family_count == 1
        assert jload(principle.positive_refs, [])[0]["question_id"] == other
        assert db.query(DesignPrincipleRevision).filter_by(principle_id=pid).count() == 2
        assert db.query(PropositionEmbedding).filter_by(source_id=pid).count() == 0
        assert jload(db.query(PropositionAnalysisJob).first().question_ids, []) == [other]
    assert client.delete(f"/api/proposition/questions/{qid}").status_code == 404


def test_delete_batch_allows_same_file_to_be_uploaded_again(client):
    batch = upload(client)
    unrelated = upload(client, "## 第1题\n计算 $1+2$。\n")
    with SessionLocal() as db:
        db.add(PropositionAnalysisJob(batch_id=batch["id"], status="done"))
        db.commit()
    response = client.delete(f"/api/proposition/batches/{batch['id']}")
    assert response.status_code == 200 and response.json()["questions_deleted"] == 6
    assert client.get(f"/api/proposition/batches/{batch['id']}").status_code == 404
    with SessionLocal() as db:
        assert db.query(PropositionAnalysisJob).filter_by(batch_id=batch["id"]).count() == 0
        assert db.query(PropositionQuestion).filter_by(batch_id=batch["id"]).count() == 0
        assert db.get(PropositionBatch, unrelated["id"])
    assert upload(client)["id"] != batch["id"]


@pytest.mark.parametrize("state", ["queued", "running", "cancelled_in_flight"])
def test_delete_is_blocked_until_analysis_finishes(client, state):
    batch = upload(client)
    with SessionLocal() as db:
        job = PropositionAnalysisJob(batch_id=batch["id"], status="cancelled" if state == "cancelled_in_flight" else state)
        db.add(job)
        db.commit()
        jid = job.id
    if state == "cancelled_in_flight":
        knowledge._job_threads[jid] = threading.Thread()
    try:
        assert client.delete(f"/api/proposition/batches/{batch['id']}").status_code == 409
        assert client.delete(f"/api/proposition/questions/{batch['questions'][0]['id']}").status_code == 409
        assert client.get(f"/api/proposition/batches/{batch['id']}").json()["counts"]["total"] == 6
    finally:
        knowledge._job_threads.pop(jid, None)


@pytest.mark.parametrize("whole_batch", [False, True])
def test_cross_batch_distillation_cannot_restore_deleted_evidence(client, whole_batch):
    batch = upload(client)
    ids = [q["id"] for q in batch["questions"][:2]]
    with SessionLocal() as db:
        fid = knowledge.active_framework(db, "math_logic").id
        for qid in ids:
            db.add(PropositionEvaluation(question_id=qid, framework_id=fid, status="done", demo=False,
                report=jdump({"correctness": {"status": "supported"}, "confidence": .95, "blind_confidence": .95})))
        db.commit()

        class DeleteDuringModelCall:
            is_demo = False

            def _real_json(self, *args):
                path = f"batches/{batch['id']}" if whole_batch else f"questions/{ids[0]}"
                assert client.delete(f"/api/proposition/{path}").status_code == 200
                return {"principles": [{"statement": "删除期间归纳的经验", "positive_refs": ids,
                    "confidence": .95, "mechanism": "结构", "applicability": "条件"}]}

        created = knowledge.distill_evaluations(db, ids, fid, DeleteDuringModelCall())
        if whole_batch:
            assert created == []
        else:
            assert len(created) == 1
            principle = db.get(DesignPrinciple, created[0])
            assert principle.status == "proposed" and principle.evidence_count == 1
            assert [ref["question_id"] for ref in jload(principle.positive_refs, [])] == [ids[1]]
