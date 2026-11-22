"""Versioned, repository-local skills consumed by the A and C roles.

The skill text is an execution contract, not generated learner content. It is
hashed and recorded in prompt manifests so a later review can identify exactly
which operating guidance was in force.
"""
from __future__ import annotations

from pathlib import Path

from .contracts import sha


def _load(filename: str, skill_id: str, version: int) -> dict:
    path = Path(__file__).with_name(filename)
    text = path.read_text(encoding="utf-8")
    return {"skill_id": skill_id, "version": version, "hash": sha(text), "instructions": text}


def review_skill() -> dict:
    return _load("review_skill.md", "assessment-review", 3)


def entry(skill: dict, consumer: str = "C") -> dict:
    return {"source_id": skill["skill_id"], "version": skill["version"],
            "sha256": skill["hash"], "consumer": consumer,
            "message_position": "system.skill"}
