"""The LangGraph research workflow.

    START → planner → retriever → source_evaluator → critical_analyst → gap_analyst
                                        ↑                                   │
                                        └──── follow_up_retrieval ◄── "follow_up"
                                                                            │ "synthesize" (fan-out)
                                                              ┌─────────────┴─────────────┐
                                                      insight_generator           data_visualizer
                                                              └─────────────┬─────────────┘
                                                                     report_builder → END (fan-in: waits for both)

The Insight Generator and the Data Visualizer are independent (neither reads the other's output), so they run in
the same LangGraph step, in parallel. Within nodes, searches, page fetches and per-source claim extraction also run
concurrently (see the retriever and the critical analyst).

The loop is bounded by `ResearchBudget.max_iterations` plus search/source/time budgets, all enforced in
deterministic code (`gap_analyst.decide_route` and `RunBudget`). Each node is wrapped so that failures are
recorded as errors, and the run continues with the evidence collected so far rather than fabricating output.
"""
from __future__ import annotations

import logging
import threading
import time

from langgraph.graph import END, START, StateGraph

from src.agents.base import Agent, RunContext
from src.agents.critical_analyst import CriticalAnalystAgent
from src.agents.data_visualizer import DataVisualizationAgent
from src.agents.gap_analyst import GapAnalystAgent
from src.agents.insight_generator import InsightGeneratorAgent
from src.agents.planner import PlannerAgent
from src.agents.report_builder import ReportBuilderAgent
from src.agents.retriever import RetrieverAgent
from src.agents.source_evaluator import SourceEvaluatorAgent
from src.models.evidence import AgentEvent, AgentStatus
from src.orchestration.state import ResearchState

logger = logging.getLogger(__name__)

AGENTS: dict[str, Agent] = {a.name: a for a in (PlannerAgent(), RetrieverAgent(), SourceEvaluatorAgent(),
                                                CriticalAnalystAgent(), GapAnalystAgent(), InsightGeneratorAgent(),
                                                DataVisualizationAgent(), ReportBuilderAgent())}
NODE_AGENT = {"planner": "planner", "retriever": "retriever", "follow_up_retrieval": "retriever",
              "source_evaluator": "source_evaluator", "critical_analyst": "critical_analyst",
              "gap_analyst": "gap_analyst", "insight_generator": "insight_generator",
              "data_visualizer": "data_visualizer", "report_builder": "report_builder"}
NODE_LABELS = {"planner": "Research Planner", "retriever": "Contextual Retriever",
               "follow_up_retrieval": "Contextual Retriever (follow-up)", "source_evaluator": "Source Evaluation",
               "critical_analyst": "Critical Analysis", "gap_analyst": "Research Gap & Follow-up",
               "insight_generator": "Insight Generation", "data_visualizer": "Data Visualization", "report_builder": "Report Builder"}


class EventLog:
    """Collects events emitted during a node so they are also stored in graph state (audit trail)."""

    def __init__(self, ctx: RunContext):
        self.lock = threading.Lock()
        self.buffer: list[AgentEvent] = []
        downstream = ctx.emit

        def emit(event: AgentEvent) -> None:
            with self.lock:
                self.buffer.append(event)
            downstream(event)
        ctx.emit = emit

    def drain(self) -> list[AgentEvent]:
        with self.lock:
            events, self.buffer = self.buffer, []
        return events


def _node(node_name: str, ctx: RunContext, log: EventLog):
    agent = AGENTS[NODE_AGENT[node_name]]

    def run(state: ResearchState) -> dict:
        iteration = state.get("iteration", 0)
        status = state.get("statuses", {}).get(agent.name, AgentStatus(agent=agent.name)).model_copy()
        ctx.event(agent.name, "started", f"{NODE_LABELS[node_name]} started.", iteration)
        started = time.perf_counter()
        try:
            update = agent.run(state, ctx)
            status.status, status.last_message = "completed", ""
            errors = []
        except Exception as exc:  # preserve evidence; report the failure instead of inventing results
            logger.exception("Agent %s failed", agent.name)
            message = f"{NODE_LABELS[node_name]} failed: {type(exc).__name__}: {str(exc)[:200]}"
            ctx.event(agent.name, "failed", message, iteration)
            status.status, status.last_message = "failed", message
            update, errors = {}, [message]
            if agent.name == "gap_analyst":
                update = {"route": "synthesize", "route_reason": "Gap analysis failed; synthesising available evidence.",
                          "stop_reason": "Gap analysis failed."}
        status.runs += 1
        status.duration_ms += int((time.perf_counter() - started) * 1000)
        update["statuses"] = {agent.name: status}  # merged by the reducer; parallel nodes never clash
        update["errors"] = errors + update.get("errors", [])
        update["events"] = log.drain()
        return update
    return run


SYNTHESIS_BRANCHES = ["insight_generator", "data_visualizer"]


def route_after_gaps(state: ResearchState) -> str:
    if state.get("plan") is None:
        return "synthesize"
    return "follow_up" if state.get("route") == "follow_up" else "synthesize"


def next_after_gaps(state: ResearchState) -> list[str]:
    """Follow-up retrieval, or both synthesis branches at once (a parallel fan-out)."""
    return ["follow_up_retrieval"] if route_after_gaps(state) == "follow_up" else SYNTHESIS_BRANCHES


def route_after_planner(state: ResearchState) -> str:
    return "retrieve" if state.get("plan") is not None else "report"


def build_graph(ctx: RunContext):
    log = EventLog(ctx)
    graph = StateGraph(ResearchState)
    for node_name in NODE_AGENT:
        graph.add_node(node_name, _node(node_name, ctx, log))
    graph.add_edge(START, "planner")
    graph.add_conditional_edges("planner", route_after_planner, {"retrieve": "retriever", "report": "report_builder"})
    graph.add_edge("retriever", "source_evaluator")
    graph.add_edge("source_evaluator", "critical_analyst")
    graph.add_edge("critical_analyst", "gap_analyst")
    graph.add_conditional_edges("gap_analyst", next_after_gaps, ["follow_up_retrieval", *SYNTHESIS_BRANCHES])
    graph.add_edge("follow_up_retrieval", "source_evaluator")
    graph.add_edge(SYNTHESIS_BRANCHES, "report_builder")  # fan-in: the report waits for both branches
    graph.add_edge("report_builder", END)
    return graph.compile()
