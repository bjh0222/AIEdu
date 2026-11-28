import hashlib
import json
from pathlib import Path


ARCHIVE = Path(__file__).resolve().parents[1] / "quality_suite_archive" / "full_v2"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_full_v2_archive_manifest_recomputes_and_contains_quality_system():
    manifest = json.loads((ARCHIVE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "aiedu.quality-suite-archive.v2"
    assert manifest["archive_id"] == "full_v2"
    assert manifest["files"]

    rows = manifest["files"]
    paths = [row["path"] for row in rows]
    assert len(paths) == len(set(paths))
    for row in rows:
        archived = ARCHIVE / "source" / row["path"]
        assert archived.is_file(), row["path"]
        assert archived.stat().st_size == row["bytes"], row["path"]
        assert _sha256(archived) == row["sha256"], row["path"]

    expected_tree_hash = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert manifest["source_tree_hash"] == expected_tree_hash
    assert "app/v2/quality_suite/correctness_only_v1.py" in paths
    assert "app/v2/checker.py" in paths
    assert "app/v2/optimization.py" in paths
    assert "app/v2/optimization_recheck.py" in paths
    assert "app/v2/criterion_review.py" in paths
    assert "tests/test_correctness_repair_loop.py" in paths
