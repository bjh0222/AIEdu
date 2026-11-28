from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
API = REPO / "services" / "api"
ARCHIVE_ROOT = API / "quality_suite_archive"
TEST_PATTERNS = (
    "test_*quality*",
    "test_correctness*",
    "test_s11*",
    "test_optimization*",
    "test_checker*",
    "test_criterion*",
    "test_difficulty*",
    "test_task_adaptive*",
    "test_integration_quality*",
    "test_structured_a_contract.py",
    "conftest.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_files() -> list[Path]:
    files = [
        path for path in (API / "app").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    selected_tests = {
        path for pattern in TEST_PATTERNS for path in (API / "tests").glob(pattern)
        if path.is_file() and path.suffix == ".py"
    }
    return sorted([*files, *selected_tests, API / "requirements-offline.lock.txt"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="full_v2")
    args = parser.parse_args()
    if not args.name.replace("_", "").replace("-", "").isalnum():
        raise SystemExit("archive name must be a simple identifier")
    output = (ARCHIVE_ROOT / args.name).resolve()
    if output.parent != ARCHIVE_ROOT.resolve():
        raise SystemExit("archive path escaped quality_suite_archive")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    rows = []
    for source in source_files():
        relative = source.relative_to(API)
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        rows.append({
            "path": relative.as_posix(),
            "bytes": target.stat().st_size,
            "sha256": sha256(target),
        })

    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()
    source_worktree_dirty = bool(subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=REPO, text=True
    ).strip())
    manifest = {
        "schema_version": "aiedu.quality-suite-archive.v2",
        "archive_id": args.name,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "source_worktree_dirty": source_worktree_dirty,
        "source_tree_hash": hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "files": rows,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# Full quality-suite archive\n\n"
        "This folder is a source snapshot for future focused maintenance. "
        "`manifest.json` records every archived path, byte count and SHA-256. "
        "The current web workflow does not execute this full suite; it uses the "
        "separate correctness-only adapter.\n",
        encoding="utf-8",
    )
    print(f"archived {len(rows)} files to {output}")


if __name__ == "__main__":
    main()
