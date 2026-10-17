"""Submit-time LLM sense check of research questions."""
from dataclasses import replace

import pytest

from src.models.research import INVALID_QUESTION, ResearchRequest
from src.services.llm import LLMClient
from src.services.question_check import InvalidQuestion, assess_question
from tests.conftest import fake_openai

LIVE = ResearchRequest(question="Why does the colour seven taste louder than Tuesday", mode="live")


def _llm(settings, *responses):
    client, completions = fake_openai(list(responses))
    return LLMClient(replace(settings, openrouter_api_key="test"), client=client), completions


def test_meaningless_question_is_rejected_with_the_invalid_message(settings):
    llm, _ = _llm(settings, '{"meaningful": false, "reason": "It combines unrelated words with no clear topic."}')
    with pytest.raises(InvalidQuestion) as exc:
        assess_question(LIVE, replace(settings, openrouter_api_key="test"), llm)
    assert str(exc.value).startswith(INVALID_QUESTION) and "no clear topic" in str(exc.value)


def test_meaningful_question_passes(settings):
    llm, _ = _llm(settings, '{"meaningful": true, "reason": ""}')
    assess_question(LIVE, replace(settings, openrouter_api_key="test"), llm)


def test_check_fails_open_when_the_llm_output_is_unusable(settings):
    llm, _ = _llm(settings, "not json", "not json", "not json")
    assess_question(LIVE, replace(settings, openrouter_api_key="test"), llm)


@pytest.mark.parametrize("request_kwargs", [{"mode": "live", "use_llm": False}, {"mode": "demo"}])
def test_no_llm_call_when_llm_is_off_or_in_demo(settings, request_kwargs):
    from src.models.research import DEMO_QUESTIONS
    llm, completions = _llm(settings, '{"meaningful": false, "reason": "x"}')
    assess_question(ResearchRequest(question=DEMO_QUESTIONS[0], **request_kwargs),
                    replace(settings, openrouter_api_key="test"), llm)
    assess_question(LIVE, settings, llm)  # no API key configured
    assert not completions.prompts
