"""Synthetic migration fixtures; never read the real legacy database."""
import copy

from test_task_run_v2 import owner,post


def record(scene="exercise",suffix="one"):
    from app.schemas import FormatSpecOut
    return FormatSpecOut(id="synthetic-"+suffix,name="合成规范",scene=scene,version=26,
        output_goal="保留来源规范",content_structure="  原文\r\n### 三段\n",
        reference_examples="独立正反例",created_at="2020-01-01T00:00:00",updated_at="2020-01-02T00:00:00").model_dump()


def envelope(records):
    from app.v2.contracts import digest
    return {"records":records,"source_hash":digest(records)}


def test_spec_import_preserves_values_identity_history_and_binds_custom_scene(client):
    from app.v2.contracts import digest
    headers=owner(client)
    records=[record(),record("reading_guide","two")]
    payload=envelope(records)
    first=post(client,"/format-spec-imports",payload,headers,"import-once")
    assert first.status_code == 201,first.text
    assert post(client,"/format-spec-imports",payload,headers,"import-once").json() == first.json()
    repeated=post(client,"/format-spec-imports",payload,headers).json()
    assert repeated["inserted_ids"] == [] and len(repeated["unchanged_ids"]) == 2
    catalog={v["id"]:v for v in client.get("/api/v2/format-specs").json()["items"]}
    for value in records:
        assert catalog[value["id"]] == value
        role="exercise" if value["scene"] == "exercise" else "tutorial"
        created=post(client,"/tasks",{"title":"合成任务","raw_input":"只验证选中快照",
            "spec_bindings":[{"scene":value["scene"],"artifact_role":role}],"requested_artifacts":[role]},headers).json()
        snap=client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
        binding=snap["task"]["spec_bindings"][0]
        assert binding["snapshot"] == value and binding["snapshot_hash"] == digest(value)
        assert binding["spec_id"] == value["id"] and binding["spec_version"] == 26


def test_spec_import_conflicts_rollback_entire_batch_and_bad_hash_is_rejected(client):
    headers=owner(client)
    original=record()
    assert post(client,"/format-spec-imports",envelope([original]),headers).status_code == 201
    changed=copy.deepcopy(original)
    changed["format_rules"]="不可覆盖"
    result=post(client,"/format-spec-imports",envelope([record("reading_guide","rollback"),changed]),headers)
    assert result.status_code == 409 and result.json()["code"] == "SPEC_IMPORT_ID_CONFLICT"
    catalog={v["id"]:v for v in client.get("/api/v2/format-specs").json()["items"]}
    assert "synthetic-rollback" not in catalog and catalog[original["id"]] == original
    assert post(client,"/format-spec-imports",{"records":[original],"source_hash":"0"*64},headers).status_code == 422


def test_multiple_custom_scene_specs_require_explicit_selection(client):
    headers=owner(client)
    values=[record(suffix="a"),record(suffix="b")]
    assert post(client,"/format-spec-imports",envelope(values),headers).status_code == 201
    task={"title":"合成任务","raw_input":"生成练习","requested_artifacts":["exercise"],
          "spec_bindings":[{"scene":"exercise","artifact_role":"exercise"}]}
    refused=post(client,"/tasks",task,headers)
    assert refused.status_code == 422 and refused.json()["code"] == "SPEC_SELECTION_REQUIRED"
    task["spec_bindings"][0]["spec_id"]=values[0]["id"]
    assert post(client,"/tasks",task,headers).status_code == 201
