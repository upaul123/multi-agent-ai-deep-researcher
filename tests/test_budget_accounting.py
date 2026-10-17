"""Actual request limits, concurrent reservations and deadline-aware retries."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from src.models.research import ResearchBudget
from src.orchestration.budget import RunBudget
from src.services.llm import LLMClient, LLMUnavailable
from tests.conftest import fake_openai
from tests.test_orchestration import TransientError


def test_retry_attempt_cap_includes_failures(settings):
    client, completions = fake_openai([TransientError(), TransientError(), {"ok": True}])
    budget = RunBudget(ResearchBudget(max_llm_calls=2))
    with pytest.raises(LLMUnavailable, match="budget"):
        LLMClient(settings, client=client, sleep=lambda _: None).complete("hi", budget)
    assert len(completions.prompts) == 2 and budget.usage.llm_attempts == 2
    assert budget.usage.llm_reserved_tokens == 0 and budget.usage.llm_unreported_tokens > 0


def test_atomic_token_reservations_prevent_parallel_oversubscription():
    budget = RunBudget(ResearchBudget(max_llm_tokens=100, max_llm_calls=100))
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(lambda _: budget.reserve_llm_attempt(60), range(8)))
    assert sum(admitted) == 1 and budget.usage.llm_reserved_tokens == 60
    budget.finish_llm_attempt(60, SimpleNamespace(total_tokens=10, prompt_tokens=5, completion_tokens=5))
    assert budget.reserve_llm_attempt(60)


def test_timeout_and_backoff_respect_remaining_deadline(settings):
    clock = [0.0]
    budget = RunBudget(ResearchBudget(max_seconds=10), clock=lambda: clock[0])
    seen = []
    def create(**kwargs):
        seen.append(kwargs["timeout"])
        clock[0] = 9.9
        raise TransientError()
    def sleep(seconds):
        assert seconds <= 0.100001
        clock[0] += seconds
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    with pytest.raises(LLMUnavailable):
        LLMClient(settings, client=client, sleep=sleep).complete("test", budget)
    assert seen == [10.0] and budget.usage.llm_attempts == 1


def test_unknown_usage_is_conservatively_charged(settings):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    budget = RunBudget(ResearchBudget())
    assert LLMClient(settings, client=client).complete("hi", budget) == "ok"
    assert budget.usage.llm_tokens == 0 and budget.usage.llm_unreported_tokens > 0
    assert not budget.usage.cost_estimate_complete and budget.usage.llm_reserved_tokens == 0


def test_known_prices_estimate_cost():
    budget = RunBudget(ResearchBudget())
    assert budget.reserve_llm_attempt(300)
    budget.finish_llm_attempt(300, SimpleNamespace(total_tokens=150, prompt_tokens=100, completion_tokens=50),
                              input_price=1, output_price=2)
    assert budget.usage.estimated_cost_usd == pytest.approx(0.0002)
    assert budget.usage.cost_estimate_complete


@pytest.mark.parametrize("total", ["invalid", -1, float("nan"), float("inf")])
def test_malformed_usage_retains_unknown_allowance_without_double_release(total):
    budget = RunBudget(ResearchBudget())
    assert budget.reserve_llm_attempt(300)
    budget.finish_llm_attempt(300, SimpleNamespace(total_tokens=total, prompt_tokens=100, completion_tokens=50))
    assert budget.usage.llm_reserved_tokens == 0
    assert budget.usage.llm_unreported_tokens == 300
    assert not budget.usage.cost_estimate_complete


def test_parallel_source_admission_remains_bounded():
    budget = RunBudget(ResearchBudget(max_sources=3))
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(lambda _: budget.add_sources(1), range(20)))
    assert sum(admitted) == 3
    assert budget.snapshot().sources == 3
    assert "sources" in budget.snapshot().exhausted
    assert budget.add_sources(1) == 0
