"""Deterministic text utilities shared by agents: tokenising, sentences, claim cues, polarity, similarity."""
from __future__ import annotations

import re
from collections import Counter

STOPWORDS = set("""
a an the and or but of to in on for with at by from as is are was were be been being it its this that these those
there their they them we our you your he she his her i me my not no nor so than then too very can could would should
will shall may might must do does did done have has had having about into over under between through during before
after above below up down out off again further once here when where why how all any both each few more most other
some such only own same just also what which who whom whose while if because until against among per via within without
next five years year new latest major key make made using use used based many much one two three four
""".split())

GENERIC_QUESTION_WORDS = {"how", "what", "which", "why", "will", "are", "is", "does", "do", "evidence", "support",
                          "supports", "contradict", "contradicts", "claim", "claims", "latest", "developments",
                          "transform", "major", "emerging", "could", "create", "new", "research", "development",
                          "developments", "recent", "state"}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(])")
_WORD = re.compile(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]")

FORECAST_CUES = re.compile(r"\b(will|would|expected to|projected|forecast|predict|by 20\d\d|over the next|in the coming|"
                           r"likely to|poised to|set to|anticipate)\b", re.I)
OPINION_CUES = re.compile(r"\b(i believe|we believe|in my view|in our view|we think|arguably|should|must|"
                          r"opinion|we argue|it is clear that|undoubtedly)\b", re.I)
STAT_CUES = re.compile(r"\b\d+(?:\.\d+)?\s?(%|percent|per cent|x\b|times|billion|million|bn|mn)|\b\d{2,}\b", re.I)
CLAIM_CUES = re.compile(r"\b(found|finds|show|shows|showed|report|reports|reported|survey|study|according to|increase|"
                        r"increased|decrease|reduce|reduced|improve|improved|grow|grew|growth|adopt|adopted|adoption|"
                        r"deploy|deployed|replace|replacing|outperform|accuracy|cost|revenue|risk|evidence|"
                        r"percent|%|will|expected|estimate|estimated|account for|lead to|leads to)\b", re.I)
NEGATIVE_CUES = re.compile(r"\b(not|no|never|little evidence|no evidence|limited evidence|fail|fails|failed|failure|"
                           r"unlikely|overstated|overhyped|hype|decline|declined|decrease|fewer|lack|lacks|lacking|"
                           r"struggle|struggles|stalled|abandon|abandoned|contrary|doubt|sceptic|skeptic|rather than|"
                           r"augment rather|complement rather|only a minority|cannot|can't|won't|insufficient|"
                           r"remain(s)? rare|did not|does not|do not|only \d+(?:\.\d+)?\s?%|only a (minority|third|fraction))\b", re.I)
WEAKNESS_CUES = {
    "self-reported survey data": re.compile(r"\b(self-reported|respondents said|survey of)\b", re.I),
    "small or pilot sample": re.compile(r"\b(pilot|small sample|n\s?=\s?\d{1,2}\b|handful of|anecdotal)\b", re.I),
    "vendor-sponsored": re.compile(r"\b(sponsored by|commissioned by|our customers|our platform)\b", re.I),
    "no methodology stated": re.compile(r"\b(sources say|people familiar|it is said)\b", re.I),
}
INJECTION_PATTERNS = re.compile(
    r"(ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts)|disregard (the|all|your) .{0,30}"
    r"(instructions|rules)|system prompt|you are now|act as (an?|the) |developer mode|rate this source|"
    r"<\s*/?\s*(system|assistant|instructions)\s*>|run the following (command|code)|execute (this|the following))",
    re.I)


def tokenize(text: str) -> list[str]:
    """Lower-cased, stop-word-filtered, lightly stemmed tokens (shared by BM25 and term overlap)."""
    return [stem(t) for t in _WORD.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def stem(word: str) -> str:
    """Very light suffix stripping so 'agents'/'agent' and 'deploying'/'deploy' match."""
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)] + replacement
            break
    if word.endswith("e") and len(word) > 5:  # complete/completes -> complet, replace/replacing -> replac
        word = word[:-1]
    return word


def content_terms(text: str) -> set[str]:
    return {t for t in tokenize(text) if not t.isdigit() and len(t) > 2}


def overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient: shared terms relative to the smaller set (robust to sentence-length differences)."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def shingles(text: str, size: int = 5) -> set[tuple[str, ...]]:
    words = tokenize(text)
    return {tuple(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}


def near_duplicate(a: str, b: str, threshold: float = 0.6) -> bool:
    sa, sb = shingles(a), shingles(b)
    return bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) >= threshold


def polarity(text: str) -> int:
    """+1 for affirming statements, -1 for negating/sceptical ones (a deliberately simple heuristic)."""
    return -1 if NEGATIVE_CUES.search(text) else 1


CHANGE_WORDS = re.compile(r"\b(grow|grew|growth|increase\w*|decrease\w*|reduc\w*|cut|rise|rose|fall|fell|decline\w*|"
                          r"improv\w*|by)\s*$", re.I)


def percent_mentions(text: str, window: int = 4) -> list[tuple[float, str, set[str]]]:
    """(value, kind, nearby terms) for each percentage. kind is 'change' (grew by 40%) or 'share' (38% of firms)."""
    found = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s?(%|percent|per cent)", text, re.I):
        before = text[:match.start()]
        after = text[match.end():]
        kind = "change" if CHANGE_WORDS.search(before[-25:]) else "share"
        near = " ".join(before.split()[-window:] + after.split()[:window])
        found.append((float(match.group(1)), kind, content_terms(near)))
    return found


def key_phrases(text: str, limit: int = 8) -> list[str]:
    """Most informative terms and two-word phrases of a question (non-generic, order preserved)."""
    words = [w for w in _WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1
             and w not in GENERIC_QUESTION_WORDS]
    bigrams = [f"{a} {b}" for a, b in zip(words, words[1:])]
    counts = Counter(words)
    ranked = list(dict.fromkeys(bigrams[:3] + sorted(dict.fromkeys(words), key=lambda w: -counts[w] * len(w))))
    return ranked[:limit]


def entities(text: str) -> list[str]:
    """Capitalised multi-letter tokens that are not sentence starters (rough named-entity heuristic)."""
    tokens = re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][A-Za-z0-9&\-]+(?:\s+[A-Z][A-Za-z0-9&\-]+)*)", text)
    return list(dict.fromkeys(t for t in tokens if t.lower() not in STOPWORDS and len(t) > 2))[:8]


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"
