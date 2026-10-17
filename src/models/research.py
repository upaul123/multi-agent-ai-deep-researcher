"""Research request, budget and plan models."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def utc_now() -> datetime:
    """Naive UTC time for values compared across machines (run heartbeats).

    `now()` is the server's local time, which differs between a laptop and a cloud host sharing one database; a
    heartbeat written in UTC+5:30 would look hours stale (or in the future) to a host running in UTC.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class Depth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class SourcePreference(StrEnum):
    WEB = "web"
    ACADEMIC = "academic"
    NEWS = "news"
    OFFICIAL = "official"
    UPLOADED = "uploaded"
    DATASET = "dataset"


class ResearchMode(StrEnum):
    DEMO = "demo"  # synthetic offline corpus, deterministic agents
    LIVE = "live"  # configured live providers (+ LLM if configured)


class ResearchBudget(BaseModel):
    """Hard limits that bound every investigation."""

    max_iterations: int = Field(2, ge=1, le=6)  # follow-up research rounds after the initial one
    max_searches: int = Field(24, ge=1, le=200)
    max_sources: int = Field(30, ge=1, le=300)
    max_seconds: int = Field(240, ge=10, le=3600)
    max_llm_calls: int = Field(40, ge=0, le=500)
    max_llm_tokens: int = Field(200_000, ge=0, le=5_000_000)
    min_sources_per_subquestion: int = Field(2, ge=1, le=10)


DEPTH_PRESETS: dict[Depth, dict] = {
    Depth.QUICK: dict(subquestions=4, queries_per_subquestion=1, results_per_search=4,
                      budget=ResearchBudget(max_iterations=1, max_searches=10, max_sources=15, max_seconds=120,
                                            max_llm_calls=20)),
    Depth.STANDARD: dict(subquestions=5, queries_per_subquestion=2, results_per_search=5,
                         budget=ResearchBudget(max_iterations=2, max_searches=24, max_sources=30, max_seconds=240,
                                               max_llm_calls=40)),
    Depth.DEEP: dict(subquestions=7, queries_per_subquestion=2, results_per_search=6,
                     budget=ResearchBudget(max_iterations=3, max_searches=48, max_sources=60, max_seconds=480,
                                           max_llm_calls=80, max_llm_tokens=400_000)),
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# The synthetic demo corpus only covers AI and enterprise software, so demo mode is limited to questions it can
# answer well. Any other question would retrieve unrelated sources and produce a misleading report.
DEMO_QUESTIONS = [
    "How will agentic AI transform enterprise software over the next five years?",
    "Which emerging technologies could create new business opportunities in India?",
    "What evidence supports or contradicts the claim that AI agents will replace traditional SaaS workflows?",
]


def is_demo_question(question: str) -> bool:
    normalized = " ".join(question.split()).casefold()
    return any(normalized == q.casefold() for q in DEMO_QUESTIONS)


# Shown whenever a question is rejected as meaningless, by the cheap checks below or the LLM sense check at submit.
INVALID_QUESTION = "This research question seems to be invalid! Please check input."

QUESTION_EVIDENCE_NOTICE = (
    "Include a specific topic, timeframe or geography where useful. Answers depend on available evidence; "
    "forecasts are uncertain. Verify important conclusions against the original sources."
)


# Letter pairs that almost never occur inside English words (from a ~10k-word English sample). Keyboard mashing
# produces them constantly; a real word rarely has more than one, at a compound boundary like "lock|down".
_RARE_PAIRS = frozenset(
    "bf bg bk bn bq bv bw bx bz cb cg cj cn cq cv cw cx dq dx dz fb fd fg fh fj fk fm fn fp fq fv fw fx fz gb gj gk "
    "gq gv gx gz hb hc hf hg hh hj hk hp hq hv hx hz ih iw jb jc jd jf jg jh jj jk jl jm jn jq jr jt jv jx jy jz kc "
    "kf kj km kq kv kx kz lj lq lr lw lx mf mh mj mq mv mw mx mz nq nx oq pj pm pn pq pv pw pz qa qb qc qd qe qf qg "
    "qh qi qj qk qm qn qo qp qq qr qs qt qv qw qx qy qz rj rq rx sb sj sx tj tk tq tv tx uh uj uq uw vb vd vf vh vj "
    "vk vl vm vn vp vq vr vt vu vv vw vx vy vz wb wc wf wg wj wk wm wp wq wt wu wv ww wx wy wz xb xf xg xj xk xn xo "
    "xq xr xu xv xw xy yf yh yj yq yx yy zb zc zf zg zj zk zn zp zq zr zs zt zv zw zx".split())
_KEYBOARD_ROWS = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]
_KEYBOARD_RUNS = frozenset(row[i:i + 4] for row in _KEYBOARD_ROWS + [r[::-1] for r in _KEYBOARD_ROWS]
                           for i in range(len(row) - 3)) - {"erty", "trew"}  # property, strewn
_REAL_ODD_WORDS = frozenset({"http", "https", "html", "qwerty"})
_STOPWORDS = frozenset(
    "a an and are as at be but by can could did do does for from had has have how i if in into is it its may me "
    "might my of on or our should so than that the their them then there these they this those to was we were "
    "what when where which who whom whose why will with would you your about any some".split())


def _looks_like_gibberish(word: str) -> bool:
    """Cheap keyboard-mashing check for one lowercase word; tuned to (almost) never flag real English words."""
    if len(word) < 4 or word in _REAL_ODD_WORDS:
        return False
    return (any(run in word for run in _KEYBOARD_RUNS)                       # asdf, qwer, lkjh
            or bool(re.search(r"(.)\1\1", word))                              # aaaa
            or bool(re.fullmatch(r"(.{1,3})\1{2,}|([^aeiouy]{2})\2", word))   # jkjkjk, hjhj
            or (len(word) >= 5 and not re.search(r"[aeiouy]", word))          # sdfgh
            or sum(word[i:i + 2] in _RARE_PAIRS for i in range(len(word) - 1)) >= 2)  # ajdkf


def gibberish_words(question: str) -> tuple[list[str], int]:
    """Words that look like random typing, and how many words were checked. Acronyms (all caps) are skipped."""
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", question) if len(w) >= 4 and not w.isupper()]
    return list(dict.fromkeys(w for w in words if _looks_like_gibberish(w))), len(words)


def question_problem(question: str) -> str | None:
    """Why the question would be rejected, for a live alert while typing; None when empty or valid."""
    if not (question or "").strip():
        return None
    try:
        ResearchRequest(question=question)
    except ValidationError as exc:
        return next((e["msg"].removeprefix("Value error, ") for e in exc.errors() if e["loc"] == ("question",)), None)
    return None


def question_guidance(question: str) -> str | None:
    """Conservative, advisory scope hints; never rewrite or block a broad topic."""
    if re.search(r"\b(?:tell me everything|everything about|all about)\b", question, re.IGNORECASE):
        return "This question may be too broad. Consider a specific comparison, timeframe, geography or outcome."
    return None


class ResearchRequest(BaseModel):
    question: str
    context: str = ""
    objectives: list[str] = Field(default_factory=list)
    depth: Depth = Depth.STANDARD
    source_preferences: list[SourcePreference] = Field(
        default_factory=lambda: [SourcePreference.WEB, SourcePreference.ACADEMIC, SourcePreference.NEWS,
                                 SourcePreference.OFFICIAL, SourcePreference.UPLOADED])
    date_from: date | None = None
    date_to: date | None = None
    max_iterations: int | None = Field(None, ge=1, le=6)
    mode: ResearchMode = ResearchMode.DEMO
    use_llm: bool = True
    document_ids: list[str] = Field(default_factory=list)  # uploaded documents to include
    parent_run_id: str | None = None  # set when refining a previous investigation

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        value = re.sub(r"\s+", " ", _CONTROL_CHARS.sub("", value or "")).strip()
        if len(value) < 12:
            raise ValueError("Please enter a research question of at least 12 characters.")
        if len(value) > 600:
            raise ValueError("Research questions are limited to 600 characters. Put extra detail in the context field.")
        if len(re.findall(r"[A-Za-z]{3,}", value)) < 3:
            raise ValueError("The question needs at least three words to plan an investigation.")
        words = re.findall(r"[^\W\d_]+", value.casefold())
        greeting_words = {"hello", "hi", "hey", "there", "friend", "good", "morning", "afternoon", "evening"}
        if len(set(words)) < 2 or (words and set(words) <= greeting_words):
            raise ValueError(f"{INVALID_QUESTION} Enter a research topic or question, rather than greetings or "
                             "repeated words.")
        suspicious, _ = gibberish_words(value)
        if suspicious:
            quoted = ", ".join(f"“{w}”" for w in suspicious[:3])
            verb = "looks" if len(suspicious) == 1 else "look"
            raise ValueError(f"{INVALID_QUESTION} {quoted} {verb} like random typing or a typo. Please correct it "
                             "and enter your question again, e.g. “How is AI changing drug discovery?”")
        if re.search(r"\blorem ipsum\b", value, re.IGNORECASE):
            raise ValueError(f"{INVALID_QUESTION} It looks like placeholder text; describe the topic you want "
                             "researched.")
        if not any(w.casefold() not in _STOPWORDS for w in re.findall(r"[A-Za-z]{3,}", value)):
            raise ValueError(f"{INVALID_QUESTION} The question needs a subject to research, e.g. “electric "
                             "vehicle adoption in India”.")
        return value

    @field_validator("context")
    @classmethod
    def _clean_context(cls, value: str) -> str:
        return _CONTROL_CHARS.sub("", value or "").strip()[:4000]

    @field_validator("objectives")
    @classmethod
    def _clean_objectives(cls, value: list[str]) -> list[str]:
        return [o.strip()[:300] for o in value if o and o.strip()][:8]

    @model_validator(mode="after")
    def _check_dates(self) -> ResearchRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("The publication start date must be before the end date.")
        return self

    def budget(self) -> ResearchBudget:
        budget = DEPTH_PRESETS[self.depth]["budget"].model_copy()
        if self.max_iterations:
            budget.max_iterations = self.max_iterations
        return budget

    def preset(self, key: str):
        return DEPTH_PRESETS[self.depth][key]


class BudgetUsage(BaseModel):
    iterations: int = 0
    searches: int = 0
    sources: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0  # observed total tokens
    llm_attempts: int = 0  # includes failed requests and retries
    llm_unreported_tokens: int = 0  # conservative token charge for failed/usage-less attempts
    llm_reserved_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    cost_estimate_complete: bool = False
    cache_hits: int = 0
    elapsed_seconds: float = 0.0
    exhausted: list[str] = Field(default_factory=list)  # which limits were hit


class ResearchScope(BaseModel):
    timeframe: str = "not specified"
    entities: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)
    geography: str | None = None
    exclusions: list[str] = Field(default_factory=list)
    intended_outcome: str = ""


class SubQuestionStatus(StrEnum):
    OPEN = "open"
    PARTIAL = "partial"
    ANSWERED = "answered"


class SubQuestion(BaseModel):
    id: str
    text: str
    perspective: str
    priority: int = Field(2, ge=1, le=3)  # 1 = highest
    queries: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    expected_evidence: str = ""
    status: SubQuestionStatus = SubQuestionStatus.OPEN
    added_in_iteration: int = 0

    @field_validator("queries")
    @classmethod
    def _queries(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(q.strip()[:200] for q in value if q and q.strip()))
        if not cleaned:
            raise ValueError("Each subquestion needs at least one search query.")
        return cleaned[:4]


class ResearchPlan(BaseModel):
    question: str
    objectives: list[str]
    scope: ResearchScope
    subquestions: list[SubQuestion]
    search_strategy: str
    completion_criteria: list[str]
    version: int = 1
    revisions: list[str] = Field(default_factory=list)
    generated_by: str = "heuristic"  # heuristic | llm

    @field_validator("subquestions")
    @classmethod
    def _subquestions(cls, value: list[SubQuestion]) -> list[SubQuestion]:
        if not 2 <= len(value) <= 12:
            raise ValueError("A plan needs between 2 and 12 subquestions.")
        if len({s.id for s in value}) != len(value):
            raise ValueError("Subquestion ids must be unique.")
        return value

    def get(self, subquestion_id: str) -> SubQuestion | None:
        return next((s for s in self.subquestions if s.id == subquestion_id), None)
