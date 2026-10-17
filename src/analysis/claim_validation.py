"""Conservative checks; canonical source wording remains authoritative, not an LLM paraphrase."""
import re
from decimal import Decimal
from src.analysis.text import INJECTION_PATTERNS, FORECAST_CUES

NUMBERS = re.compile(r"(?<![\w.])[+-]?\d+(?:,\d{3})*(?:\.\d+)?")
NEGATION = re.compile(r"\b(?:not|no|never|cannot|can't|won't|didn't|doesn't|without)\b", re.I)
HEDGE = re.compile(r"\b(?:may|might|could|possible|possibly|potentially|estimated|estimate|approximately)\b", re.I)
UNITS = re.compile(r"%|\b(?:percent|million|billion|trillion|USD|INR|EUR|dollars|rupees)\b", re.I)


def numbers(text):
    return {Decimal(m[0].replace(",", "")) for m in NUMBERS.finditer(text)}


def paraphrase_issues(quote, claim):
    issues = []
    if INJECTION_PATTERNS.search(claim):
        issues.append("instruction-like paraphrase")
    if numbers(claim) - numbers(quote):
        issues.append("unsupported number or date")
    units = lambda t: {m[0].lower().replace("percent", "%") for m in UNITS.finditer(t)}
    if units(claim) - units(quote):
        issues.append("changed unit or magnitude")
    if bool(NEGATION.search(quote)) != bool(NEGATION.search(claim)):
        issues.append("changed negation")
    if FORECAST_CUES.search(quote) and not FORECAST_CUES.search(claim):
        issues.append("forecast presented as an observation")
    if HEDGE.search(quote) and not HEDGE.search(claim):
        issues.append("uncertainty removed")
    return issues
