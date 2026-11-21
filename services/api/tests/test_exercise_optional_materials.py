"""No-source exercises must not inherit the source-only chapter restriction."""
from copy import deepcopy

import pytest

from test_task_run_v2 import task_run


def test_source_requirement_is_conditional_and_missing_injection_fails_closed():
    from app.v2.contracts import DomainError
    from app.v2.worker import exercise_source_instruction
    own = exercise_source_instruction({"material_chunk_ids": []}, [])
    assert "允许的自主命题任务" in own and "只取材" not in own
    assert "每类题量" in own and "空items" in own
    sourced = exercise_source_instruction({"material_chunk_ids": ["c"]}, [{"chunk_id": "c"}])
    assert "只取材material_chunks本章正文" in sourced
    with pytest.raises(DomainError, match="MATERIAL_INCOMPLETE"):
        exercise_source_instruction({"material_chunk_ids": ["missing"]}, [])


def test_actual_no_material_generation_wire_and_item_projection(client, monkeypatch):
    from app.db import SessionLocal
    from app.v2 import generation_tools, worker
    from app.v2.runtime import job, load
    _, _, snap = task_run(client)
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    state = deepcopy(state)
    state["mode"] = "configured"
    unit = state["plan"]["units"][0] | {"artifact_role": "exercise", "format_contract": "exercise.chapter.v1",
        "material_chunk_ids": [], "source_sections": []}
    seen = []
    def generate(*args, **kw):
        seen.append(kw)
        assert kw["data"]["material_chunks"] == []
        assert "允许的自主命题任务" in kw["system"] and "只取材" not in kw["system"]
        answer = {"final": "合成答案", "reasoning": "合成测试推理"}
        return {"items": [{"question_type": kind, "stem_markdown": "合成题干", "source_sections": [], "answer": answer}
                          for kind in ("判断题", "基础概念题")]}, {"call_id": "synthetic", "usage": None}, None
    monkeypatch.setattr(generation_tools, "generation_with_sources", generate)
    result = worker.generate_planned_unit(snap["run_id"], "synthetic", 0, state, unit, [], [], None)
    assert len(seen) == 1 and len(result["items"]) == 2 and "### 参考答案" in result["body"]
