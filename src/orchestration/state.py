"""Typed LangGraph state shared by all agents.

Append-only channels (events, search log, warnings, errors) use an `add` reducer, so every node's
contributions are kept as an audit trail. `statuses` merges per agent, because the Insight Generator and the Data
Visualizer run in parallel and each reports only its own status. All other channels are replaced by the node that
owns them.
"""
from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from pydantic import BaseModel, Field

from src.models.evidence import (AgentEvent, AgentStatus, Claim, Contradiction, EvidenceLink, Insight, MatrixRow,
                                 Passage, Report, ResearchGap, Source, SourceAssessment)
from src.models.visualization import EvidenceChart
from src.models.research import BudgetUsage, ResearchBudget, ResearchPlan, ResearchRequest, now


def merge_statuses(current: dict | None, update: dict | None) -> dict:
    """Reducer: each node reports only its own agent's status, so parallel nodes never overwrite each other."""
    return {**(current or {}), **(update or {})}


class QueryTask(BaseModel):
    subquestion_id: str | None
    query: str
    purpose: str = "initial"  # initial | follow_up | lead
    source_types: list[str] = Field(default_factory=list)


class SearchRecord(BaseModel):
    iteration: int
    subquestion_id: str | None
    query: str
    provider: str
    status: str  # ok | cached | error | skipped_budget | unavailable
    results: int = 0
    new_sources: int = 0
    message: str = ""
    timestamp: str = Field(default_factory=lambda: now().isoformat())


class ResearchState(TypedDict, total=False):
    run_id: str
    request: ResearchRequest
    budget: ResearchBudget
    plan: ResearchPlan | None
    iteration: int
    pending_queries: list[QueryTask]
    searched_queries: list[str]
    sources: list[Source]
    passages: list[Passage]
    assessments: dict[str, SourceAssessment]
    claims: list[Claim]
    extracted_source_ids: list[str]
    matrix: list[MatrixRow]
    links: list[EvidenceLink]
    contradictions: list[Contradiction]
    gaps: list[ResearchGap]
    insights: list[Insight]
    charts: list[EvidenceChart]
    visualization_notes: list[str]
    report: Report | None
    statuses: Annotated[dict[str, AgentStatus], merge_statuses]
    usage: BudgetUsage
    route: str  # follow_up | synthesize
    route_reason: str
    stop_reason: str
    new_sources_last_iteration: int
    provider_notes: list[str]
    events: Annotated[list[AgentEvent], add]
    search_log: Annotated[list[SearchRecord], add]
    warnings: Annotated[list[str], add]
    errors: Annotated[list[str], add]


AGENT_ORDER = ["planner", "retriever", "source_evaluator", "critical_analyst", "gap_analyst", "insight_generator",
               "data_visualizer", "report_builder"]
