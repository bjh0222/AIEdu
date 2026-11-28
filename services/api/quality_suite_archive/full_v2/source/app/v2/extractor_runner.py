"""Untrusted DOCUMENT parser, not a command execution sandbox. No application/DB import.

The parent supplies only its owned staging path and an operator-owned resource policy.
DOCX/EPUB XML is defused; archive contents are never extracted to filesystem.
"""
from __future__ import annotations

import json
import os
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath


class ExtractionError(Exception):
    pass


def checked_archive(path, policy):
    archive = zipfile.ZipFile(path)
    entries = archive.infolist()
    if len(entries) > policy["max_archive_entries"]:
        raise ExtractionError("ARCHIVE_ENTRY_LIMIT")
    total = 0
    for entry in entries:
        name = entry.orig_filename  # ZipInfo normalizes OS separators; validate original bytes-as-name.
        parts = PurePosixPath(name).parts
        if (name.startswith(("/", "\\")) or "\\" in name or ".." in parts or ":" in name
                or (entry.external_attr >> 16) & 0o170000 == 0o120000 or entry.flag_bits & 1):
            raise ExtractionError("UNSAFE_ARCHIVE_ENTRY")
        total += entry.file_size
        if total > policy["max_expanded_bytes"]:
            raise ExtractionError("ARCHIVE_EXPANSION_LIMIT")
    if len({e.filename for e in entries}) != len(entries):
        raise ExtractionError("DUPLICATE_ARCHIVE_ENTRY")
    return archive


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "section", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"}:
            self.hidden = max(0, self.hidden-1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract(path, kind, policy):
    if kind in {".txt", ".md", ".csv", ".markdown"}:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise ExtractionError("ENCODING_UNKNOWN") from None
        return text, [{"locator": "text:full", "text": text}], "utf8-text.v2"
    if kind == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ExtractionError("PDF_ENCRYPTED")
        if len(reader.pages) > policy["max_archive_entries"]:
            raise ExtractionError("PDF_PAGE_LIMIT")
        segments = []
        size = 0
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            # Do not silently skip scanned/blank pages: no full-coverage claim without OCR.
            if not text.strip():
                raise ExtractionError("PDF_OCR_REQUIRED")
            size += len(text.encode())
            if size > policy["max_expanded_bytes"]:
                raise ExtractionError("EXTRACTION_EXPANSION_LIMIT")
            segments.append({"locator": f"pdf:page:{number}", "text": text})
        return "\n\n".join(s["text"] for s in segments), segments, "pypdf-text.v1"
    if kind not in {".docx", ".epub"}:
        raise ExtractionError("UNSUPPORTED_MATERIAL_TYPE")
    from defusedxml import ElementTree as ET
    with checked_archive(path, policy) as archive:
        if kind == ".docx":
            root = ET.fromstring(archive.read("word/document.xml"))
            if any(e.tag.endswith(("}drawing","}object","}altChunk","}pict")) for e in root.iter()):
                raise ExtractionError("DOCX_NON_TEXT_CONTENT_REQUIRES_ADAPTER")
            paragraphs = []
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            for paragraph in root.iter(namespace+"p"):
                text = "".join(e.text or "" for e in paragraph.iter(namespace+"t"))
                if text:
                    paragraphs.append({"locator": f"docx:paragraph:{len(paragraphs)+1}", "text": text})
            # Headers/footnotes are source material too; include all textual ancillary parts.
            for name in sorted(archive.namelist()):
                if name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes")) and name.endswith(".xml"):
                    section = ET.fromstring(archive.read(name))
                    text = " ".join(e.text or "" for e in section.iter(namespace+"t"))
                    if text.strip():
                        paragraphs.append({"locator": "docx:"+name, "text": text})
            return "\n\n".join(s["text"] for s in paragraphs), paragraphs, "docx-defused.v1"
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfiles = [e for e in container.iter() if e.tag.endswith("}rootfile")]
        if len(rootfiles) != 1:
            raise ExtractionError("EPUB_ROOT_AMBIGUOUS")
        package_name = rootfiles[0].get("full-path", "")
        package = ET.fromstring(archive.read(package_name))
        items = {e.get("id"):e for e in package.iter() if e.tag.endswith("}item")}
        segments = []
        for itemref in (e for e in package.iter() if e.tag.endswith("}itemref")):
            item = items.get(itemref.get("idref"))
            if item is None or item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                raise ExtractionError("EPUB_SPINE_UNSUPPORTED")
            relative = PurePosixPath(item.get("href", ""))
            if ".." in relative.parts or relative.is_absolute() or ":" in str(relative):
                raise ExtractionError("UNSAFE_ARCHIVE_ENTRY")
            name = str(PurePosixPath(package_name).parent / relative)
            parser = TextHTML()
            parser.feed(archive.read(name).decode("utf-8-sig", errors="strict"))
            text = "".join(parser.parts).strip()
            if not text:
                raise ExtractionError("EPUB_NON_TEXT_SPINE")
            segments.append({"locator": "epub:"+name, "text": text})
        return "\n\n".join(s["text"] for s in segments), segments, "epub-spine.v1"


def main():
    # Isolated interpreter with an explicit, sanitized env. No user code is evaluated.
    api = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(api))
    try:
        from app.services.proposition_symbolic_runner import _apply_process_limits
        policy = json.loads(sys.argv[3])
        limiter = _apply_process_limits(int(policy["extractor_memory_bytes"]))
        path = Path(sys.argv[1])
        text, segments, version = extract(path, sys.argv[2], policy)
        if len(text.encode()) > policy["max_expanded_bytes"]:
            raise ExtractionError("EXTRACTION_EXPANSION_LIMIT")
        if not text.strip():
            raise ExtractionError("EMPTY_MATERIAL")
        response = {"ok": True, "text": text, "segments": segments, "extractor_version": version, "memory_limiter": limiter}
    except ExtractionError as exc:
        response = {"ok": False, "code": str(exc)}
    except MemoryError:
        response = {"ok": False, "code": "EXTRACTOR_MEMORY_LIMIT"}
    except Exception:
        response = {"ok": False, "code": "EXTRACTION_FAILED"}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=True).encode("ascii"))


if __name__ == "__main__":
    main()
