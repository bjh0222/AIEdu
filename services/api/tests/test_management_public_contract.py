"""The integration contract must include management responses, not just writes."""
from test_task_run_v2 import owner
from test_v2_management_trash import material, change


def test_management_responses_are_in_openapi_and_keep_receipt_shape(client):
    from app.api.v2_management import ManagedImpact, ManagedPage, ManagedReceipt
    from app.main import app

    paths = app.openapi()["paths"]
    for path, method, name in (
        ("/api/v2/managed-resources/{kind}", "get", "ManagedPage"),
        ("/api/v2/managed-resources/{kind}/{object_id}/impact", "get", "ManagedImpact"),
        ("/api/v2/managed-resource-actions", "post", "ManagedReceipt"),
        ("/api/v2/managed-resource-cleanup", "post", "ManagedReceipt"),
    ):
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/" + name)

    headers = owner(client)
    identity, _ = material(client, headers)
    catalog = client.get("/api/v2/managed-resources/materials").json()
    assert ManagedPage.model_validate(catalog).items[0].id == identity
    impact = client.get(f"/api/v2/managed-resources/materials/{identity}/impact").json()
    assert ManagedImpact.model_validate(impact).resource.id == identity
    # Response validation must not add default fields to hash-bound objects.
    assert "history" not in impact["resource"]["management"]
    result, _ = change(client, headers, "materials", identity, "trash")
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert ManagedReceipt.model_validate(receipt).operation == "trash"
    assert receipt["items"][0]["management"]["trashed"] is True
    assert "purged" not in receipt["items"][0]
