"""Shared fixtures. All data is synthetic; nothing touches the network or a real API key."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from src.config import get_settings
from src.database.repository import Repository
from src.models.research import ResearchMode, ResearchRequest
from src.search.base import SearchResult
from src.search.registry import ProviderSet

TODAY = date(2026, 10, 10)
QUESTION = "How will agentic AI transform enterprise software over the next five years?"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to reach the real network."""
    def blocked(self, request, *args, **kwargs):
        raise AssertionError(f"Network access attempted in tests: {request.url}")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


@pytest.fixture
def settings(tmp_path):
    return replace(get_settings(), openrouter_api_key=None, tavily_api_key=None, pinecone_api_key=None,
                   db_path=tmp_path / "test.db")


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "test.db")


@pytest.fixture
def demo_request():
    return ResearchRequest(question=QUESTION)


class FakeProvider:
    """Deterministic in-memory search provider for live-mode tests."""

    def __init__(self, name="fake", results=None, live=True, serves=None, fail=False, rotate=False):
        self.name, self.label, self.live = name, f"Fake {name}", live
        self.serves = serves or {"web", "academic", "news", "official"}
        self.available, self.unavailable_reason = True, ""
        self.results, self.fail, self.calls, self.rotate = results or [], fail, [], rotate

    def search(self, query, max_results=5, date_from=None, date_to=None):
        self.calls.append(query)
        if self.fail:
            from src.search.base import ProviderError
            raise ProviderError("simulated outage")
        start = (len(self.calls) - 1) * max_results if self.rotate else 0  # different results per query
        return self.results[start:start + max_results]


def make_result(title, content, url=None, source_type="academic_paper", published="2026-01-15", provider="fake",
                **kw) -> SearchResult:
    from src.search.base import parse_date
    return SearchResult(title=title, provider=provider, url=url or f"https://example.org/{title.lower().replace(' ', '-')}",
                        snippet=content[:200], content=content, full_text_available=True, publisher="Example Publisher",
                        published_date=parse_date(published), source_type=source_type, **kw)


def provider_set(*providers) -> ProviderSet:
    return ProviderSet(providers=list(providers))


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][-1]["content"])
        item = self.responses.pop(0) if self.responses else "{}"
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=SimpleNamespace(total_tokens=123))


def fake_openai(responses):
    completions = FakeCompletions(responses)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def live_request(**kw):
    return ResearchRequest(question=kw.pop("question", QUESTION), mode=ResearchMode.LIVE, **kw)
