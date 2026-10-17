"""Conservative numeric extraction from structured CSV rows and validated source quotes."""
import hashlib
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from src.models.visualization import DataPoint, EvidenceChart
from src.analysis.text import INJECTION_PATTERNS

NUMBER = re.compile(r"^([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(%|percent|million|billion|USD|INR|EUR)?$", re.I)
PERCENT = re.compile(r"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b)", re.I)
HEADER_UNIT = re.compile(r"\((%|percent|million|billion|USD|INR|EUR)\)\s*$", re.I)


def numeric_cell(text):
    m = NUMBER.fullmatch(text.strip())
    if not m:
        return None
    try:
        value = float(Decimal(m[1].replace(",", "")))
    except (InvalidOperation, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    unit = (m[2] or "").lower().replace("percent", "%")
    if unit == "%" and not 0 <= value <= 100:
        return None
    return value, unit


def _id(*parts):
    return "chart_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def extract_charts(sources, passages, claims, limit=12):
    charts, notes = [], []
    by_source = defaultdict(list)
    for p in passages:
        by_source[p.source_id].append(p)
    for source in sources:
        if len(charts) >= limit:
            break
        if source.metadata.get("kind") != "csv":
            continue
        # New uploads preserve complete records; old CSV uploads cannot safely recover cells from prose.
        records = source.metadata.get("table_records", [])
        columns = source.metadata.get("table_columns", [])
        if not records or len(columns) < 2:
            notes.append(f"{source.title}: no structured table retained; re-upload the CSV to enable charts.")
            continue
        labels = [c for c in columns if c.lower() in {"year", "date", "month", "quarter", "category", "region", "country"}]
        label_col = labels[0] if labels else columns[0]
        for metric in columns:
            if metric == label_col or len(charts) >= limit:
                continue
            header_unit = HEADER_UNIT.search(metric)
            declared = header_unit[1].lower().replace("percent", "%") if header_unit else ""
            points, units, valid = [], set(), True
            for rec in records[:2000]:
                cell = str(rec.get(metric, "")).strip()
                if not cell or cell.lower() in {"na", "n/a", "null", "none"}:
                    continue
                parsed = numeric_cell(cell)
                if parsed is None:
                    valid = False
                    break
                value, unit = parsed
                if declared and unit and declared != unit:
                    valid = False
                    break
                unit = unit or declared
                if unit == "%" and not 0 <= value <= 100:
                    valid = False
                    break
                label = str(rec.get(label_col, "")).strip()
                quote = rec.get("_quote", "")
                location = rec.get("_location", "")
                passage = next((p for p in by_source[source.id] if quote and quote in p.text), None)
                if not label or not passage or INJECTION_PATTERNS.search(quote):
                    valid = False
                    break
                units.add(unit)
                points.append(DataPoint(label=label, value=value, source_id=source.id, passage_id=passage.id,
                                        location=location, quote=quote))
            if not valid or len(units) != 1 or len(points) < 2 or len({p.label for p in points}) != len(points):
                notes.append(f"{source.title} / {metric}: skipped ambiguous, incompatible, duplicate or insufficient data.")
                continue
            time_axis = label_col.lower() == "year" and all(re.fullmatch(r"(?:19|20)\d{2}", p.label) for p in points)
            if time_axis:
                points.sort(key=lambda p: int(p.label))
            charts.append(EvidenceChart(id=_id(source.id, metric), title=f"{metric} by {label_col}", metric=metric,
                unit=next(iter(units)) or "as reported (unit unspecified)", kind="line" if time_axis else "bar",
                points=points, synthetic=source.is_synthetic,
                limitation="One source and one metric; missing cells omitted. Units are not inferred or converted."))
    # A narrative statistic is an individual reported value, never a fabricated cross-source comparison or trend.
    source_map = {s.id: s for s in sources}
    passage_map = {p.id: p for p in passages}
    for claim in claims:
        if len(charts) >= limit:
            break
        source, passage = source_map.get(claim.source_id), passage_map.get(claim.passage_id)
        matches = list(PERCENT.finditer(claim.quote))
        if not source or source.metadata.get("kind") == "csv" or not passage or claim.quote not in passage.text:
            continue
        if len(matches) != 1 or claim.claim_type.value == "forecast" or INJECTION_PATTERNS.search(claim.quote):
            continue
        value = float(matches[0][1])
        if not 0 <= value <= 100:
            continue
        charts.append(EvidenceChart(id=_id(claim.id), title="Reported percentage", metric=claim.quote, unit="%",
            kind="indicator", synthetic=source.is_synthetic,
            points=[DataPoint(label=claim.quote, value=value, source_id=source.id, passage_id=passage.id,
                              location=passage.location, quote=claim.quote)],
            limitation="Single reported value, not a trend or a comparison. Population and context remain in the quotation."))
    if not charts:
        notes.append("No unambiguous, traceable numeric evidence was available for a chart. No values were invented.")
    return charts, notes
