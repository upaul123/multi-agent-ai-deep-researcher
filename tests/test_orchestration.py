"""LangGraph workflow: conditional routing, bounded loops, budgets, error handling, LLM retries, demo/offline mode."""
import pytest

from src.agents.gap_analyst import decide_route
from src.models.evidence import GapKind, ResearchGap
from src.models.research import Depth, ResearchBudget, ResearchRequest
from src.orchestration.budget import RunBudget
from src.orchestration.graph import AGENTS, build_graph, route_after_gaps
from src.services.llm import LLMClient, LLMUnavailable
from src.services.research_service import run_research
from tests.conftest import QUESTION, TODAY, FakeProvider, fake_openai, live_request, make_result, provider_set


def gap(value=0.8, queries=("q",)):
    return ResearchGap(id="g", subquestion_id="sq1", kind=GapKind.SINGLE_SOURCE, description="d",
                       follow_up_queries=list(queries), expected_value=value)


@pytest.mark.parametrize("args,route,reason", [
    ((2, 2, [], [gap()], 5), "synthesize", "Iteration limit"),
    ((0, 2, ["searches"], [gap()], 5), "synthesize", "budget exhausted"),
    ((0, 2, ["time"], [gap()], 5), "synthesize", "budget exhausted"),
    ((0, 2, [], [gap(0.1)], 5), "synthesize", "Completion criteria met"),
    ((0, 2, [], [gap(queries=())], 5), "synthesize", "Completion criteria met"),
    ((1, 3, [], [gap()], 0), "synthesize", "Diminishing returns"),
    ((0, 2, [], [gap()], 0), "follow_up", "worth pursuing"),
    ((1, 3, ["llm_calls"], [gap()], 4), "follow_up", "worth pursuing"),  # LLM budget alone does not stop searching
])
def test_decide_route(args, route, reason):
    decision, why = decide_route(*args)
    assert decision == route and reason in why


def test_route_function_reads_state():
    assert route_after_gaps({"plan": object(), "route": "follow_up"}) == "follow_up"
    assert route_after_gaps({"plan": object(), "route": "synthesize"}) == "synthesize"
    assert route_after_gaps({"plan": None, "route": "follow_up"}) == "synthesize"


def test_graph_has_bounded_follow_up_loop(settings):
    from src.agents.base import RunContext
    from src.search.registry import ProviderSet
    graph = build_graph(RunContext(settings=settings, budget=RunBudget(ResearchBudget()), providers=ProviderSet()))
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert ("gap_analyst", "follow_up_retrieval") in edges and ("follow_up_retrieval", "source_evaluator") in edges
    assert ("gap_analyst", "insight_generator") in edges and ("report_builder", "__end__") in edges


def test_demo_run_end_to_end_offline(settings, demo_request):
    events = []
    state = run_research(demo_request, settings, today=TODAY, on_event=events.append)
    assert state["report"] is not None and state["errors"] == []
    assert len(state["plan"].subquestions) >= 4 and all(s.queries for s in state["plan"].subquestions)
    assert state["sources"] and all(s.origin.value == "demo" for s in state["sources"])
    assert state["claims"] and state["matrix"] and state["insights"]
    assert all(s.status == "completed" for s in state["statuses"].values())
    agents_seen = {e.agent for e in events}
    assert set(AGENTS) <= agents_seen  # every specialist agent actually ran and emitted real events
    assert state["iteration"] <= demo_request.budget().max_iterations
    assert any(e.status == "decision" for e in events)


def test_maximum_iteration_enforcement(settings):
    request = ResearchRequest(question=QUESTION, max_iterations=1)
    state = run_research(request, settings, today=TODAY)
    retriever_runs = state["statuses"]["retriever"].runs
    assert state["iteration"] <= 1 and retriever_runs <= 2


def test_search_and_source_budget_limits(settings):
    provider = FakeProvider(results=[make_result(f"Paper {i}", f"Agents improve productivity by {i}% in study {i}. " * 3)
                                     for i in range(10)])
    request = live_request(depth=Depth.QUICK, use_llm=False)
    budget = request.budget()
    state = run_research(request, settings, today=TODAY, providers=provider_set(provider))
    assert len(provider.calls) <= budget.max_searches
    assert len(state["sources"]) <= budget.max_sources
    tight = live_request(depth=Depth.QUICK, use_llm=False)
    state = run_research(tight, settings, today=TODAY, providers=provider_set(FakeProvider(rotate=True, results=[
        make_result(f"Enterprise agent study {i}", f"Unique study number {i} finds agentic AI useful for enterprise "
                    f"software teams in case {i}. " * 3) for i in range(80)])))
    assert len(state["sources"]) <= tight.budget().max_sources
    assert "sources" in state["usage"].exhausted


def test_budget_tracker_limits():
    clock = iter([0, 0, 1, 2, 500, 500, 500, 500]).__next__
    budget = RunBudget(ResearchBudget(max_searches=2, max_sources=3, max_seconds=100, max_llm_calls=1), clock=clock)
    assert budget.try_search() and budget.try_search() and not budget.try_search()
    assert budget.add_sources(5) == 3 and "sources" in budget.usage.exhausted
    assert not budget.try_llm()  # out of time by now
    assert "time" in budget.snapshot().exhausted


def test_provider_errors_are_recorded_not_fatal(settings):
    state = run_research(live_request(use_llm=False), settings, today=TODAY,
                         providers=provider_set(FakeProvider(fail=True)))
    assert state["report"] is not None
    assert any(r.status == "error" for r in state["search_log"])
    assert any("simulated outage" in w for w in state["warnings"])


def test_agent_failure_preserves_evidence(settings, demo_request, monkeypatch):
    def boom(self, state, ctx):
        raise RuntimeError("insight engine crashed")
    monkeypatch.setattr(type(AGENTS["insight_generator"]), "run", boom)
    state = run_research(demo_request, settings, today=TODAY)
    assert any("Insight Generation failed" in e for e in state["errors"])
    assert state["statuses"]["insight_generator"].status == "failed"
    assert state["sources"] and state["claims"] and state["report"] is not None  # evidence kept, report still built
    assert "Errors during the run" in state["report"].section("limitations").content


def test_live_mode_without_credentials_or_providers(settings):
    request = live_request(use_llm=True)
    events = []
    state = run_research(request, settings, today=TODAY, providers=provider_set(), on_event=events.append)
    assert state["report"] is not None and state["sources"] == []
    messages = " ".join(e.message for e in events)
    assert "No search provider is available" in messages and "OPENROUTER_API_KEY is not configured" in messages
    assert "too thin" in state["report"].section("executive_summary").content


def test_cache_marks_results_as_cached(settings, repo):
    provider = FakeProvider(results=[make_result("Cached paper", "Agents improve support productivity by 14%. " * 3)])
    request = live_request(depth=Depth.QUICK, use_llm=False)
    run_research(request, settings, repo=repo, today=TODAY, providers=provider_set(provider))
    first_calls = len(provider.calls)
    state = run_research(request, settings, repo=repo, today=TODAY, providers=provider_set(provider))
    assert len(provider.calls) == first_calls  # second run served entirely from cache
    assert all(s.origin.value == "cache" for s in state["sources"])
    assert any(r.status == "cached" for r in state["search_log"]) and state["usage"].cache_hits > 0


# ------------------------------------------------------------------ LLM client
class TransientError(Exception):
    status_code = 429


class FatalError(Exception):
    status_code = 400


def test_llm_retries_transient_errors(settings):
    client, completions = fake_openai([TransientError(), TransientError(), {"ok": True}])
    sleeps = []
    llm = LLMClient(settings, client=client, sleep=sleeps.append)
    budget = RunBudget(ResearchBudget())
    assert llm.complete("hi", budget) == '{"ok": true}' and len(sleeps) == 2
    assert budget.usage.llm_calls == 1 and budget.usage.llm_tokens == 123
    assert budget.usage.llm_attempts == 3 and budget.usage.llm_unreported_tokens > 0
    assert budget.usage.llm_reserved_tokens == 0


def test_llm_non_transient_error_and_budget(settings):
    client, _ = fake_openai([FatalError()])
    llm = LLMClient(settings, client=client, sleep=lambda s: None)
    with pytest.raises(LLMUnavailable):
        llm.complete("hi", RunBudget(ResearchBudget()))
    exhausted = RunBudget(ResearchBudget(max_llm_calls=0))
    client, completions = fake_openai([{"ok": True}])
    with pytest.raises(LLMUnavailable, match="budget"):
        LLMClient(settings, client=client).complete("hi", exhausted)
    assert completions.prompts == []  # no call was made once the budget was exhausted


def test_missing_llm_key_is_graceful(settings):
    llm = LLMClient(settings)
    assert not llm.available
    assert llm.structured("x", ResearchBudget, RunBudget(ResearchBudget())) is None


def test_synthesis_branches_fan_out_and_join(settings):
    from src.agents.base import RunContext
    from src.search.registry import ProviderSet
    graph = build_graph(RunContext(settings=settings, budget=RunBudget(ResearchBudget()), providers=ProviderSet()))
    edges = {(e.source, e.target) for e in graph.get_graph().edges}
    assert {("gap_analyst", "insight_generator"), ("gap_analyst", "data_visualizer"),
            ("insight_generator", "report_builder"), ("data_visualizer", "report_builder")} <= edges
    assert ("insight_generator", "data_visualizer") not in edges  # no longer sequential


def test_insights_and_charts_run_in_parallel(settings, demo_request, monkeypatch):
    """Both synthesis agents must be inside their run() at the same time: a 2-party barrier only opens then."""
    import threading
    barrier = threading.Barrier(2, timeout=10)
    for name in ("insight_generator", "data_visualizer"):
        agent = AGENTS[name]
        original = agent.run

        def run(state, ctx, original=original):
            barrier.wait()  # raises BrokenBarrierError (an agent failure) if the other branch is not running
            return original(state, ctx)
        monkeypatch.setattr(agent, "run", run)
    state = run_research(demo_request, settings, today=TODAY)
    assert state["errors"] == [] and state["report"] is not None
    assert state["statuses"]["insight_generator"].status == "completed"
    assert state["statuses"]["data_visualizer"].status == "completed"
    assert all(s.status == "completed" for s in state["statuses"].values())  # merged, nothing lost
