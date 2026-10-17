"""Builds the provider set for a run and explains what is (and isn't) available."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.config import Settings
from src.models.research import ResearchMode
from src.retrieval.vector import open_run_vectors
from src.search.http import HttpClient
from src.search.local import DemoProvider, UploadedDocumentProvider
from src.search.providers import ArxivProvider, CrossrefProvider, OpenAlexProvider, TavilyProvider


@dataclass
class ProviderSettings:
    """Non-secret, user-editable provider choices (stored in the settings table)."""

    tavily: bool = True
    openalex: bool = True
    crossref: bool = True
    arxiv: bool = True
    fetch_full_text: bool = True  # fetch and extract full pages for web results (bounded, SSRF-safe)


@dataclass
class ProviderSet:
    providers: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # capability explanations shown to the user
    http: HttpClient | None = None
    crossref: CrossrefProvider | None = None  # for resolving DOI leads
    vectors: Any = None  # RunVectors when Pinecone semantic search is on for this run; None means keyword search

    @property
    def live(self) -> bool:
        return any(p.live for p in self.providers)


def build_providers(settings: Settings, mode: ResearchMode, preferences: set[str], uploaded: list[dict],
                    choices: ProviderSettings | None = None, run_id: str | None = None) -> ProviderSet:
    choices = choices or ProviderSettings()
    result = ProviderSet()
    if mode == ResearchMode.DEMO:
        result.providers.append(DemoProvider())
        result.notes.append("Demo mode: searching a bundled SYNTHETIC corpus offline. No live web or academic "
                            "searches are performed, and all demo sources are fictional.")
    else:
        http = HttpClient(timeout=settings.http_timeout, contact_email=settings.contact_email)
        result.http = http
        wants_web = bool(preferences & {"web", "news", "official"})
        if wants_web and choices.tavily:
            tavily = TavilyProvider(settings.tavily_api_key, http)
            if tavily.available:
                result.providers.append(tavily)
                if "news" in preferences:
                    result.providers.append(TavilyProvider(settings.tavily_api_key, http, topic="news"))
                    result.providers[-1].name = "tavily_news"
                    result.providers[-1].label = "Tavily news search"
            else:
                result.notes.append(f"Web search unavailable: {tavily.unavailable_reason} Academic sources "
                                    "(OpenAlex, Crossref, arXiv) and uploaded documents can still be searched.")
        if "academic" in preferences:
            if choices.openalex:
                result.providers.append(OpenAlexProvider(http, settings.contact_email))
            if choices.crossref:
                result.crossref = CrossrefProvider(http, settings.contact_email)
                result.providers.append(result.crossref)
            if choices.arxiv:
                result.providers.append(ArxivProvider(http))
        if not result.providers and not uploaded:
            result.notes.append("No live source is enabled for the selected source types. Enable academic sources, "
                                "configure TAVILY_API_KEY, or upload documents.")
    result.vectors = open_run_vectors(settings, mode, run_id, result.notes)
    if uploaded:  # documents the user explicitly attached to this run
        result.providers.append(UploadedDocumentProvider(uploaded, result.vectors))
        result.notes.append(f"Including {len(uploaded)} uploaded document(s).")
    return result
