"""Content extraction for uploaded documents (PDF, TXT, Markdown, CSV) and fetched HTML pages.

Everything extracted is untrusted data: it is chunked and quoted, never executed or obeyed.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_UPLOADS = {".pdf", ".txt", ".md", ".markdown", ".csv"}
MAX_PAGES = 300
MAX_CHARS = 400_000
MAX_CSV_ROWS = 2_000


class ExtractionError(ValueError):
    pass


@dataclass
class ExtractedDocument:
    title: str
    sections: list[tuple[str, str]]  # (location label, text), e.g. ("page 3", "...")
    kind: str  # pdf | text | markdown | csv | html
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(t for _, t in self.sections)


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _guess_title(name: str, text: str) -> str:
    for line in text.splitlines()[:15]:
        line = line.strip().lstrip("#").strip()
        if 8 <= len(line) <= 160:
            return line
    return Path(name).stem.replace("_", " ")


def extract_pdf(data: bytes, name: str) -> ExtractedDocument:
    import pymupdf

    if not data.startswith(b"%PDF"):
        raise ExtractionError(f"'{name}' is not a valid PDF.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ExtractionError(f"'{name}' could not be opened: {exc}") from exc
    if doc.needs_pass:
        raise ExtractionError(f"'{name}' is password-protected.")
    sections, warnings = [], []
    with doc:
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        for number, page in enumerate(doc, start=1):
            if number > MAX_PAGES:
                warnings.append(f"Only the first {MAX_PAGES} pages were read.")
                break
            text = page.get_text("text").strip()
            if text:
                sections.append((f"page {number}", text))
    if not sections:
        raise ExtractionError(f"No text found in '{name}'. Scanned PDFs need OCR, which is not supported.")
    title = meta.get("title") or _guess_title(name, sections[0][1])
    return ExtractedDocument(title=title, sections=sections, kind="pdf", metadata=meta, warnings=warnings)


def extract_text(data: bytes, name: str) -> ExtractedDocument:
    text = _decode(data)
    is_md = Path(name).suffix.lower() in (".md", ".markdown")
    sections: list[tuple[str, str]] = []
    if is_md:
        parts = re.split(r"(?m)^(#{1,3} .+)$", text)
        current = "introduction"
        buffer = parts[0]
        for i in range(1, len(parts), 2):
            if buffer.strip():
                sections.append((f"section '{current}'", buffer.strip()))
            current, buffer = parts[i].lstrip("#").strip()[:60], parts[i + 1] if i + 1 < len(parts) else ""
        if buffer.strip():
            sections.append((f"section '{current}'", buffer.strip()))
    else:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        sections = [(f"paragraph {i}", p) for i, p in enumerate(paragraphs, start=1)]
    if not sections:
        raise ExtractionError(f"'{name}' is empty.")
    return ExtractedDocument(title=_guess_title(name, text), sections=sections, kind="markdown" if is_md else "text")


def extract_csv(data: bytes, name: str) -> ExtractedDocument:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ExtractionError(f"'{name}' needs a header row and at least one data row.")
    header, body = [h.strip() for h in rows[0]], rows[1:MAX_CSV_ROWS + 1]
    if not all(header) or len(set(header)) != len(header) or any(h.startswith("_") for h in header):
        raise ExtractionError("CSV headers must be unique, non-empty, and must not start with an underscore.")
    if any(len(row) != len(header) for row in body):
        raise ExtractionError("CSV rows must have the same number of cells as the header.")
    sections = [(f"row {i}", "; ".join(f"{h.strip()}: {v.strip()}" for h, v in zip(header, row) if v.strip()))
                for i, row in enumerate(body, start=2)]
    warnings = [f"Only the first {MAX_CSV_ROWS} rows were read."] if len(rows) - 1 > MAX_CSV_ROWS else []
    return ExtractedDocument(title=f"Dataset: {Path(name).stem}", sections=sections, kind="csv",
                             metadata={"columns": header, "rows": len(body), "records": [
                                 {**dict(zip(header, row)), "_location": loc, "_quote": quote}
                                 for row, (loc, quote) in zip(body, sections)]}, warnings=warnings)


def extract_upload(name: str, data: bytes, max_bytes: int) -> ExtractedDocument:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_UPLOADS:
        raise ExtractionError(f"'{name}': unsupported type. Allowed: {', '.join(sorted(ALLOWED_UPLOADS))}.")
    if not data:
        raise ExtractionError(f"'{name}' is empty.")
    if len(data) > max_bytes:
        raise ExtractionError(f"'{name}' exceeds the {max_bytes // 1_048_576} MB upload limit.")
    if suffix == ".pdf":
        doc = extract_pdf(data, name)
    elif suffix == ".csv":
        doc = extract_csv(data, name)
    else:
        doc = extract_text(data, name)
    total = 0
    kept = []
    for location, text in doc.sections:
        if total + len(text) > MAX_CHARS:
            doc.warnings.append(f"Content truncated to {MAX_CHARS:,} characters.")
            break
        kept.append((location, text))
        total += len(text)
    doc.sections = kept
    return doc


def extract_html(html: str, url: str) -> str | None:
    """Main-content extraction from an HTML page with trafilatura. Returns None if nothing usable is found."""
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_precision=True)
    return text[:MAX_CHARS] if text and len(text) > 200 else None


REFERENCE_PATTERN = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;)]+|https?://[^\s\"<>)]+|arXiv:\s?\d{4}\.\d{4,5})", re.I)


def find_references(text: str, limit: int = 20) -> list[str]:
    """DOIs, URLs and arXiv ids mentioned inside a document (research leads for follow-up)."""
    found = [m.rstrip(".") for m in REFERENCE_PATTERN.findall(text)]
    return list(dict.fromkeys(found))[:limit]
