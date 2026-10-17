"""Report rendering and export: Markdown, HTML (sanitised) and PDF (PyMuPDF Story).

Readers see the report without numeric citation markers. The stored report keeps them, with its reference
numbering and citation checks, so traceability stays available internally."""
from __future__ import annotations

import html
import re

import markdown

from src.evidence.citations import strip_citations
from src.models.evidence import Report, ReportSection

HTML_STYLE = """
body { font-family: -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 860px; margin: 2rem auto;
       padding: 0 1rem; line-height: 1.55; color: #1f2937; }
h1 { font-size: 1.7rem; } h2 { margin-top: 2rem; border-bottom: 1px solid #e5e7eb; padding-bottom: .3rem; }
h3 { font-size: 1.05rem; margin-top: 1.2rem; }
table { border-collapse: collapse; width: 100%; font-size: .88rem; margin: 1rem 0; }
th, td { border: 1px solid #e5e7eb; padding: .4rem .5rem; vertical-align: top; text-align: left; }
th { background: #f3f4f6; }
.meta { color: #6b7280; font-size: .9rem; }
.banner { background: #fef3c7; border: 1px solid #f59e0b; padding: .6rem .8rem; border-radius: 6px; }
"""


SOURCES_TITLE = "Sources consulted"
EVIDENCE_COLUMNS = (0, 3, 4)  # claim, agreement, quality; the supporting/contradicting columns hold only markers


def section_title(section: ReportSection) -> str:
    return SOURCES_TITLE if section.key == "references" else section.title


def section_text(section: ReportSection) -> str:
    """Section content as readers see it: no [n] markers, and no columns or numbering that only carried them."""
    if section.key == "references":
        return "\n".join("- " + re.sub(r"^\[\d+\]\s*", "", line) for line in section.content.splitlines() if line.strip())
    text = strip_citations(section.content)
    if section.key == "evidence":
        text = "\n".join("| " + " | ".join(line.strip().strip("|").split("|")[i].strip() for i in EVIDENCE_COLUMNS) + " |"
                         if line.startswith("|") else line for line in text.splitlines())
    return text


def to_markdown(report: Report, demo: bool = False) -> str:
    parts = [f"# {report.title}", "", f"**Research question:** {report.question}", "",
             f"_Generated {report.created_at:%d %b %Y %H:%M} by Deep Researcher AI ({report.generated_by})._", ""]
    if demo:
        parts += ["> **SYNTHETIC DEMO REPORT.** All sources are fictional and for demonstration only.", ""]
    for section in report.sections:
        parts += [f"## {section_title(section)}", "", section_text(section), ""]
    return "\n".join(parts)


def _md_to_safe_html(text: str) -> str:
    # Escape any raw HTML coming from sources before Markdown conversion, so retrieved content cannot inject markup.
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"&lt;(https?://[^\s&]+)&gt;", r"<\1>", escaped)  # keep autolinks in the reference list
    escaped = re.sub(r"(?m)^&gt; ", "> ", escaped)  # keep Markdown blockquotes (banners); they are not raw HTML
    return markdown.markdown(escaped, extensions=["tables", "sane_lists"])


def to_html(report: Report, demo: bool = False) -> str:
    body = _md_to_safe_html(to_markdown(report, demo))
    if demo:
        body = body.replace("<blockquote>", '<blockquote class="banner">', 1)
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{html.escape(report.title)}</title>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><style>{HTML_STYLE}</style></head>"
            f"<body>{body}</body></html>")


def to_pdf(report: Report, demo: bool = False) -> bytes | None:
    """Render the HTML report to PDF with PyMuPDF's Story API. Returns None if rendering fails."""
    try:
        import io

        import pymupdf

        body = _md_to_safe_html(to_markdown(report, demo))
        story = pymupdf.Story(html=body, user_css="body{font-family:sans-serif;font-size:10pt;} h1{font-size:16pt;} "
                                                   "h2{font-size:13pt;} table{font-size:8pt;} td,th{border:1px solid #ccc;}")
        buffer = io.BytesIO()
        writer = pymupdf.DocumentWriter(buffer)
        mediabox = pymupdf.paper_rect("a4")
        where = mediabox + (48, 48, -48, -48)
        more = True
        while more:
            device = writer.begin_page(mediabox)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
        writer.close()
        return buffer.getvalue()
    except Exception:
        return None
