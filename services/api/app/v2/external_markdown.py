"""Deterministic external-Markdown structure parsing.

Rules parse first. The parser never rewrites source bytes: each unit body is an
exact slice of the decoded file, and adjacent bodies reconstruct the source.
AI assistance is opt-in only for low-confidence documents.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .contracts import sha

METHOD_VERSION = "external-markdown-structure.v1"
ASSET_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}
ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SETATTR_RE = re.compile(r"^\s*(={2,}|-{2,})\s*$")
BOLD_ONLY_RE = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*[:：]?\s*$")
NUMBERED_RE = re.compile(
    r"^\s*(?:(第\s*[一二三四五六七八九十百\d]+(?:[章节篇部分讲回]|部分))|"
    r"([一二三四五六七八九十]+|[0-9]+(?:\.[0-9]+)*)[、.．]\s*(.+))\s*$"
)
IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_IMAGE_RE = re.compile(r"<img\s+[^>]*?src=[\"']([^\"']+)[\"']", re.I)


@dataclass(frozen=True)
class Candidate:
    index: int
    offset: int
    kind: str
    level: int
    title: str


def _plain(title: str) -> str:
    value = BOLD_ONLY_RE.match(title)
    if value:
        title = value.group(1).strip().rstrip("：:")
    return re.sub(r"[*_`~]", "", title).strip() or "未命名章节"


def _fence_marker(line: str) -> str | None:
    match = re.match(r"^\s*(`{3,}|~{3,})", line)
    return match.group(1) if match else None


def _scan_fence(lines: list[str]) -> list[bool]:
    """True means the line is inside a fenced block; fence delimiters are excluded."""
    flags, fence = [], None
    for line in lines:
        marker = _fence_marker(line)
        if fence is None:
            if marker:
                fence = marker
                flags.append(True)
                continue
            flags.append(False)
        else:
            flags.append(True)
            if marker and marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
    return flags


def _pseudo_candidate(index: int, lines: list[str], offsets: list[int], inside: bool) -> Candidate | None:
    if inside:
        return None
    line = lines[index]
    text = line.strip()
    if not text or len(text) > 120:
        return None
    bold = BOLD_ONLY_RE.match(text)
    numbered = NUMBERED_RE.match(text)
    if not bold and not numbered:
        return None
    # A pseudo heading needs visual isolation. Adjacent list items are content.
    prev_blank = index == 0 or not lines[index - 1].strip()
    next_blank = index == len(lines) - 1 or not lines[index + 1].strip()
    if not (prev_blank or next_blank):
        return None
    if bold:
        title = bold.group(1)
        level = 2
    else:
        title = (numbered.group(1) or (numbered.group(2) + " " + (numbered.group(3) or ""))) if numbered else text
        level = 2
    return Candidate(index=index, offset=offsets[index], kind="pseudo", level=level, title=_plain(title))


def _atx_candidates(lines: list[str], flags: list[bool], offsets: list[int]) -> list[Candidate]:
    result = []
    for index, line in enumerate(lines):
        if flags[index]:
            continue
        match = ATX_RE.match(line)
        if match:
            result.append(Candidate(index=index, offset=offsets[index], kind="atx",
                                    level=len(match.group(1)), title=_plain(match.group(2))))
    return result


def _setext_candidates(lines: list[str], flags: list[bool], offsets: list[int]) -> list[Candidate]:
    result = []
    for index in range(1, len(lines)):
        if flags[index] or flags[index - 1] or not lines[index - 1].strip():
            continue
        if not SETATTR_RE.match(lines[index]):
            continue
        underline = lines[index].strip()
        result.append(Candidate(index=index - 1, offset=offsets[index - 1], kind="setext",
                                level=1 if underline.startswith("=") else 2,
                                title=_plain(lines[index - 1].strip())))
    return result


def _choose_atx_level(candidates: list[Candidate]) -> int:
    counts = {level: sum(c.level == level for c in candidates) for level in range(1, 7)}
    levels = [level for level in range(1, 7) if counts[level]]
    repeated = [level for level in levels if counts[level] >= 2]
    chosen = min(repeated or levels)
    if len(levels) > 1 and counts[chosen] == 1:
        following = [level for level in levels if level > chosen]
        if following:
            chosen = min(following)
    return chosen


def _assets(text: str) -> list[dict]:
    refs: list[str] = []
    for match in IMAGE_RE.finditer(text):
        refs.append(match.group(1))
    for match in HTML_IMAGE_RE.finditer(text):
        refs.append(match.group(1))
    out = []
    seen = set()
    for ref in refs:
        path = unquote(ref.split("#", 1)[0].split("?", 1)[0])
        parsed = urlsplit(path)
        if parsed.scheme in {"http", "https", "data", "mailto"} or parsed.scheme == "":
            # Empty scheme plus a host is protocol-relative; treat it as remote.
            if not (parsed.scheme == "" and not parsed.netloc):
                continue
        key = ref
        if key in seen:
            continue
        seen.add(key)
        out.append({"original_ref": ref, "resolved_path": path, "blob_hash": None,
                    "media_type": ASSET_EXTENSIONS.get(path.lower().rsplit(".", 1)[-1] if "." in path else "", ""),
                    "status": "missing"})
    return out


def _finish_parse(text: str, lines: list[str], offsets: list[int], candidates: list[Candidate],
                           chosen: int, mode: str, confidence: float, warnings: list[str], assets: list[dict]) -> dict:
    units = []
    if candidates:
        prefix_end = candidates[0].offset
        prefix = text[:prefix_end]
        if prefix.strip():
            units.append({"title": "前言与元信息", "body": prefix, "section_level": 0,
                          "heading_kind": "prefix", "source_start": 0, "source_end": prefix_end})
        for index, candidate in enumerate(candidates):
            end = candidates[index + 1].offset if index + 1 < len(candidates) else len(text)
            units.append({"title": candidate.title, "body": text[candidate.offset:end], "section_level": chosen,
                          "heading_kind": candidate.kind, "source_start": candidate.offset, "source_end": end})
    else:
        units = [{"title": "全文", "body": text, "section_level": 0, "heading_kind": "plain",
                  "source_start": 0, "source_end": len(text)}]
    if "".join(u["body"] for u in units) != text:
        raise AssertionError("external markdown slices do not reconstruct source")
    metadata = {
        "method_version": METHOD_VERSION,
        "source_hash": sha(text),
        "parser_mode": mode,
        "split_level": chosen,
        "confidence": confidence,
        "ai_assist_candidate": confidence < 0.70,
        "warnings": warnings,
        "assets": assets,
        "unit_map": [{"title": u["title"], "source_start": u["source_start"], "source_end": u["source_end"],
                      "source_hash": sha(u["body"])} for u in units],
    }
    return {"manifest": metadata, "units": units}


def parse_markdown(text: str, ai_line_indices: list[int] | None = None) -> dict:
    if not text.strip():
        from .contracts import DomainError
        raise DomainError("EMPTY_EXTERNAL_MARKDOWN", 422)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines[:-1]:
        offsets.append(offsets[-1] + len(line))
    flags = _scan_fence(lines)
    if ai_line_indices is not None:
        accepted = sorted({int(value) for value in ai_line_indices if isinstance(value, int) and 0 <= value < len(lines)})
        ai_candidates = [
            Candidate(index=index, offset=offsets[index], kind="ai_confirmed", level=2, title=_plain(lines[index].strip()))
            for index in accepted if lines[index].strip() and not flags[index]
        ]
        if len(ai_candidates) >= 2:
            return _finish_parse(text, lines, offsets, ai_candidates, 2, "ai_assisted_headings", 0.70,
                                 ["ai_confirmed_structure"], _assets(text))
        return _finish_parse(text, lines, offsets, [], 0, "plain_document", 0.20,
                             ["ai_confirmed_structure", "insufficient_ai_headings"], _assets(text))

    atx = _atx_candidates(lines, flags, offsets)
    setext = [] if atx else _setext_candidates(lines, flags, offsets)
    candidates = atx or setext
    warnings: list[str] = []
    if atx:
        chosen = _choose_atx_level(atx)
        candidates = [c for c in atx if c.level == chosen]
        mode = "atx_headings"
        confidence = 0.90
    elif setext:
        chosen = min(c.level for c in setext)
        candidates = [c for c in setext if c.level == chosen]
        mode = "setext_headings"
        confidence = 0.82
    else:
        pseudo = [_pseudo_candidate(i, lines, offsets, flags[i]) for i in range(len(lines))]
        candidates = [c for c in pseudo if c]
        if len(candidates) >= 2:
            chosen = 2
            mode = "pseudo_headings"
            confidence = 0.65
            warnings.append("non_standard_headings_rule_detected")
        else:
            chosen = 0
            candidates = []
            mode = "plain_document"
            confidence = 0.20
    if any(_fence_marker(line) and flags[i] and i == len(lines) - 1 for i, line in enumerate(lines)):
        warnings.append("unclosed_code_fence")
    assets = _assets(text)

    units = []
    if candidates:
        prefix_end = candidates[0].offset
        prefix = text[:prefix_end]
        if prefix.strip():
            units.append({"title": "前言与元信息", "body": prefix, "section_level": 0,
                          "heading_kind": "prefix", "source_start": 0, "source_end": prefix_end})
        for index, candidate in enumerate(candidates):
            end = candidates[index + 1].offset if index + 1 < len(candidates) else len(text)
            units.append({"title": candidate.title, "body": text[candidate.offset:end], "section_level": chosen,
                          "heading_kind": candidate.kind, "source_start": candidate.offset, "source_end": end})
    else:
        units = [{"title": "全文", "body": text, "section_level": 0, "heading_kind": "plain",
                  "source_start": 0, "source_end": len(text)}]

    # A structural parser is allowed to drop only zero-length separators. Every
    # non-empty byte must round-trip through the unit bodies.
    if "".join(u["body"] for u in units) != text:
        raise AssertionError("external markdown slices do not reconstruct source")
    metadata = {
        "method_version": METHOD_VERSION,
        "source_hash": sha(text),
        "parser_mode": mode,
        "split_level": chosen,
        "confidence": confidence,
        "ai_assist_candidate": confidence < 0.70,
        "warnings": warnings,
        "assets": assets,
        "unit_map": [{"title": u["title"], "source_start": u["source_start"], "source_end": u["source_end"],
                      "source_hash": sha(u["body"])} for u in units],
    }
    return {"manifest": metadata, "units": units}


