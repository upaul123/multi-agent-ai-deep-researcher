"""Research-question validation, plan generation and plan schema validation."""
import pytest
from pydantic import ValidationError

from src.agents.base import RunContext
from src.agents.planner import PlannerAgent, classify_question, detect_timeframe, topic_phrase
from src.models.research import (Depth, ResearchBudget, ResearchPlan, ResearchRequest, ResearchScope, SubQuestion)
from src.orchestration.budget import RunBudget
from src.search.registry import ProviderSet
from src.services.llm import LLMClient
from tests.conftest import QUESTION, fake_openai


@pytest.mark.parametrize("question,message", [
    ("short?", "at least 12 characters"),
    ("x" * 700, "limited to 600"),
    ("12345 67890 ?? !!", "at least three words"),
])
def test_question_validation_rejects_bad_input(question, message):
    with pytest.raises(ValidationError, match=message):
        ResearchRequest(question=question)


def test_question_is_normalised_and_dates_checked():
    request = ResearchRequest(question="  What   is the\x07 impact of\tAI agents on SaaS?  ")
    assert request.question == "What is the impact of AI agents on SaaS?"
    from datetime import date
    with pytest.raises(ValidationError, match="start date"):
        ResearchRequest(question=QUESTION, date_from=date(2026, 1, 1), date_to=date(2025, 1, 1))


def test_depth_presets_and_iteration_override():
    quick = ResearchRequest(question=QUESTION, depth=Depth.QUICK)
    assert quick.budget().max_searches < ResearchRequest(question=QUESTION, depth=Depth.DEEP).budget().max_searches
    assert ResearchRequest(question=QUESTION, max_iterations=4).budget().max_iterations == 4


def test_plan_schema_validation():
    sq = SubQuestion(id="sq1", text="What?", perspective="p", queries=["q"])
    with pytest.raises(ValidationError, match="between 2 and 12"):
        ResearchPlan(question="q", objectives=["o"], scope=ResearchScope(), subquestions=[sq], search_strategy="s",
                     completion_criteria=["c"])
    with pytest.raises(ValidationError, match="unique"):
        ResearchPlan(question="q", objectives=["o"], scope=ResearchScope(), subquestions=[sq, sq], search_strategy="s",
                     completion_criteria=["c"])
    with pytest.raises(ValidationError, match="at least one search query"):
        SubQuestion(id="sq1", text="What?", perspective="p", queries=["  "])


@pytest.mark.parametrize("question,kind", [
    ("What evidence supports or contradicts the claim that AI agents will replace SaaS?", "evaluative"),
    ("What are the latest research developments in retrieval-augmented generation?", "research"),
    (QUESTION, "forward_looking"),
    ("Which companies lead the Indian payments market today?", "general"),
])
def test_question_classification(question, kind):
    assert classify_question(question) == kind


def test_heuristic_plan_structure():
    request = ResearchRequest(question=QUESTION, depth=Depth.STANDARD, context="Focus on CRM vendors")
    plan = PlannerAgent().heuristic_plan(request)
    assert plan.generated_by == "heuristic" and len(plan.subquestions) == 5
    assert [s.priority for s in plan.subquestions][:3] == [1, 1, 1]
    perspectives = {s.perspective for s in plan.subquestions}
    assert "barriers & risks" in perspectives  # the plan looks for evidence against the premise too
    assert all(s.queries and s.source_types and s.expected_evidence for s in plan.subquestions)
    assert any("crm" in q.lower() for q in plan.subquestions[0].queries)  # context informs the search strategy
    assert plan.completion_criteria and "next five years" in plan.scope.timeframe
    assert topic_phrase(QUESTION) == "agentic ai enterprise software"
    assert detect_timeframe("What changed by 2030?") == "forward-looking: to 2030"


def _ctx(settings, llm=None):
    return RunContext(settings=settings, budget=RunBudget(ResearchBudget()), providers=ProviderSet(), llm=llm,
                      use_llm=llm is not None)


def test_llm_plan_is_validated_and_used(settings):
    draft = {"objectives": ["Assess impact"], "timeframe": "2026-2031", "entities": ["SaaS"], "key_concepts": ["agents"],
             "subquestions": [{"text": f"Subquestion {i}?", "perspective": "p", "priority": "High",
                               "queries": [f"query {i}"], "source_types": ["academic"], "expected_evidence": "data"}
                              for i in range(5)],
             "search_strategy": "Primary sources first", "completion_criteria": ["Two sources per subquestion"]}
    client, completions = fake_openai([{"properties": draft}])  # schema-echo wrapper is recovered
    agent = PlannerAgent()
    update = agent.run({"request": ResearchRequest(question=QUESTION), "budget": ResearchBudget()},
                       _ctx(settings, LLMClient(settings, client=client)))
    assert update["plan"].generated_by == "llm" and update["plan"].subquestions[0].priority == 1
    assert QUESTION in completions.prompts[0]
    assert len(update["pending_queries"]) == 5


def test_invalid_llm_plan_falls_back_to_heuristic(settings):
    client, _ = fake_openai(["not json at all"])
    update = PlannerAgent().run({"request": ResearchRequest(question=QUESTION), "budget": ResearchBudget()},
                                _ctx(settings, LLMClient(settings, client=client)))
    assert update["plan"].generated_by == "heuristic"


@pytest.mark.parametrize("question", ["hello hello hello", "test test test", "hello there friend"])
def test_question_rejects_obvious_non_research_input(question):
    with pytest.raises(ValidationError, match="research topic"):
        ResearchRequest(question=question)


@pytest.mark.parametrize("question", [
    "How can we ignore irrelevant search results?",
    "Research ethical hacking in enterprise networks",
    "AI AI AI adoption impacts enterprises",
    "Tell me everything about artificial intelligence",
])
def test_legitimate_topics_and_broad_questions_remain_allowed(question):
    assert ResearchRequest(question=question).question == question


def test_broad_question_guidance_is_advisory():
    from src.models.research import question_guidance
    assert question_guidance("Tell me everything about AI")
    assert not question_guidance("Compare AI adoption in Indian banks since 2023")


def test_backend_revalidates_mutated_question():
    from src.services.research_service import validate_request
    request = ResearchRequest(question=QUESTION, mode="live")
    request.question = "test test test"
    with pytest.raises(ValidationError, match="research topic"):
        validate_request(request, "local")


@pytest.mark.parametrize("question,message", [
    ("asdf qwer zxcv", "random typing"),
    ("jkhgd lkjhs bnmvc", "random typing"),
    ("How do I fdjskl the wkdjfh market", "random typing"),
    ("aaaaa bbbbb ccccc", "random typing"),
    ("Lorem ipsum dolor sit amet", "placeholder text"),
    ("what is the this that", "needs a subject"),
])
def test_question_rejects_gibberish(question, message):
    with pytest.raises(ValidationError, match=message):
        ResearchRequest(question=question)


@pytest.mark.parametrize("question", [
    "Strengths and weaknesses of LLMs for legal research",
    "Kazakhstan and Uzbekistan pizza jazz rhythm economy",
    "Effect of lockdown on Ukraine and Qatar breakthrough tech",
    "What is the QWERTY keyboard layout history",
    "Awkward frenzy around oxygen supply chains in HTTPS era",
])
def test_unusual_real_words_are_not_gibberish(question):
    from src.models.research import question_guidance, question_problem
    assert ResearchRequest(question=question).question == question
    assert not question_guidance(question) and not question_problem(question)


def test_any_gibberish_word_rejects_the_question():
    from src.models.research import question_problem
    question = "How does asdf affect stock markets in Europe"
    with pytest.raises(ValidationError, match="enter your question again"):
        ResearchRequest(question=question)
    assert "“asdf”" in question_problem(question)
    assert question_problem("") is None and question_problem(QUESTION) is None
