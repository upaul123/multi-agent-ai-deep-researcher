"""Agent B: Contextual Retriever. Searches configured providers, normalises and deduplicates sources.

- Picks at most two providers per query, matched to the subquestion's preferred source types.
- Runs searches concurrently, each one reserved against the run budget first.
- Uses the search cache (24 h) for live providers; cached results are labelled `cache`, never `live`.
- Optionally fetches full page text for web results through the SSRF-safe fetcher (bounded).
- In follow-up iterations, follows research leads (DOIs/URLs cited inside retrieved sources).
"""
from __future__ import annotations

import hashlib
from src.services.observability import map_with_context, submit_with_context, operation
from concurrent.futures import ThreadPoolExecutor

from src.agents.base import Agent, RunContext
from src.evidence.store import EvidenceStore, normalize_doi
from src.ingestion.extract import extract_html
from src.models.evidence import Origin
from src.orchestration.state import QueryTask, ResearchState, SearchRecord
from src.search.base import ProviderError, SearchResult, result_from_dict, result_to_dict

TYPE_TO_PREFERENCE = {"academic": "academic", "preprint": "academic", "official": "official", "news": "news",
                      "documentation": "web", "analyst": "web", "company": "web", "opinion": "web", "blog": "web"}
PROVIDER_RANK = {"academic": ["openalex", "arxiv", "crossref"], "official": ["tavily", "crossref"],
                 "news": ["tavily_news", "tavily"], "web": ["tavily"], "uploaded": ["upload"], "dataset": ["upload"]}
MAX_FETCH_PER_ITERATION = 6
MAX_LEADS_PER_ITERATION = 3


def cache_key(provider: str, query: str, task_filters: str, max_results: int) -> str:
    return hashlib.sha1(f"{provider}|{query.lower().strip()}|{task_filters}|{max_results}".encode()).hexdigest()


class RetrieverAgent(Agent):
    name = "retriever"
    title = "Contextual Retriever"
    responsibility = "Searches configured sources, extracts passages with provenance, deduplicates and follows leads."

    def choose_providers(self, task: QueryTask, preferences: set[str], providers: list, turn: int = 0) -> list:
        """Up to two providers matched to the subquestion's source types, rotating between equivalent providers."""
        wanted = {TYPE_TO_PREFERENCE.get(t, "web") for t in task.source_types} & preferences or set(preferences)
        by_name = {p.name: p for p in providers}
        chosen = []
        if "demo" in by_name:
            chosen.append(by_name["demo"])
        else:
            for pref in ("academic", "official", "news", "web"):
                if pref in wanted and len(chosen) < 2:
                    names = [n for n in PROVIDER_RANK[pref] if n in by_name and by_name[n] not in chosen]
                    if names:
                        chosen.append(by_name[names[turn % len(names)]])
            if not chosen:  # nothing matched the preferred types: use whatever is enabled
                chosen = [p for p in providers if p.name != "upload"][:1]
        if "upload" in by_name:
            chosen.append(by_name["upload"])
        return chosen[:3]

    @operation("search.provider")
    def _search_one(self, provider, task: QueryTask, state: ResearchState, ctx: RunContext,
                    max_results: int) -> tuple[QueryTask, object, list[SearchResult], Origin | None, SearchRecord]:
        request = state["request"]
        iteration = state.get("iteration", 0)
        record = SearchRecord(iteration=iteration, subquestion_id=task.subquestion_id, query=task.query,
                              provider=provider.name, status="ok")
        if not ctx.budget.try_search():
            record.status, record.message = "skipped_budget", "Search budget or time limit reached."
            return task, provider, [], None, record
        filters = f"{request.date_from}|{request.date_to}"
        key = cache_key(provider.name, task.query, filters, max_results)
        if provider.live and ctx.cache is not None:
            cached = ctx.cache.get_cached(key)
            if cached is not None:
                ctx.budget.record_cache_hit()
                record.status, record.results = "cached", len(cached)
                return task, provider, [result_from_dict(r) for r in cached], Origin.CACHE, record
        try:
            results = provider.search(task.query, max_results=max_results, date_from=request.date_from,
                                      date_to=request.date_to)
        except ProviderError as exc:
            record.status, record.message = "error", str(exc)[:200]
            return task, provider, [], None, record
        except Exception as exc:  # malformed responses etc.: never crash the run
            record.status, record.message = "error", f"{type(exc).__name__}: {str(exc)[:160]}"
            return task, provider, [], None, record
        if provider.live and ctx.cache is not None:
            ctx.cache.put_cached(key, provider.name, task.query, [result_to_dict(r) for r in results])
        origin = Origin.DEMO if provider.name == "demo" else Origin.UPLOAD if provider.name == "upload" else Origin.LIVE
        record.results = len(results)
        return task, provider, results, origin, record

    def run(self, state: ResearchState, ctx: RunContext) -> dict:
        request = state["request"]
        iteration = state.get("iteration", 0)
        store = EvidenceStore(state.get("sources", []), state.get("passages", []))
        tasks: list[QueryTask] = state.get("pending_queries", [])
        preferences = {p.value for p in request.source_preferences}
        max_results = request.preset("results_per_search")
        jobs = [(provider, task) for turn, task in enumerate(tasks)
                for provider in self.choose_providers(task, preferences, ctx.providers.providers, turn)]
        if not ctx.providers.providers:
            ctx.event(self.name, "info", "No search providers are available for this run.", iteration)

        records: list[SearchRecord] = []
        new_count = 0
        with ThreadPoolExecutor(max_workers=ctx.max_parallel) as pool:
            futures = [submit_with_context(pool, self._search_one, provider, task, state, ctx, max_results) for provider, task in jobs]
            for future in futures:  # consume in submission order: deterministic source order
                task, provider, results, origin, record = future.result()
                added = 0
                for result in results:
                    source, is_new = store.add_result(result, origin, task.subquestion_id, task.query, iteration)
                    if is_new:
                        if ctx.budget.add_sources(1) == 0:
                            store.sources.pop(source.id, None)
                            store.passages.pop(source.id, None)
                            record.message = "Source limit reached; remaining results discarded."
                            break
                        added += 1
                record.new_sources = added
                new_count += added
                records.append(record)
                ctx.event(self.name, "search", f"{provider.label}: '{task.query}' → {record.results} result(s), "
                          f"{added} new" + (f" ({record.status})" if record.status != "ok" else ""), iteration,
                          provider=provider.name, search_status=record.status, subquestion=task.subquestion_id)

        if iteration > 0:
            new_count += self._follow_leads(store, state, ctx, records)
        fetched = self._fetch_full_text(store, ctx, iteration)
        warnings = [f"{r.provider}: {r.message}" for r in records if r.status == "error"]
        if any(r.status == "skipped_budget" for r in records):
            warnings.append("Some searches were skipped because the research budget was reached.")
        sources, passages = store.as_lists()
        ctx.event(self.name, "completed", f"{len(records)} searches, {new_count} new sources"
                  + (f", {fetched} full texts fetched" if fetched else "") + f" ({len(sources)} total).", iteration,
                  searches=len(records), new_sources=new_count, total_sources=len(sources))
        return {"sources": sources, "passages": passages, "search_log": records, "pending_queries": [],
                "searched_queries": state.get("searched_queries", []) + [t.query for t in tasks],
                "new_sources_last_iteration": new_count, "warnings": warnings,
                "usage": ctx.budget.snapshot(iteration)}

    def _follow_leads(self, store: EvidenceStore, state: ResearchState, ctx: RunContext,
                      records: list[SearchRecord]) -> int:
        """Resolve DOIs/URLs cited by the most relevant sources (bounded)."""
        known = {normalize_doi(s.doc_ref) for s in store.sources.values() if s.doc_ref} | {
            s.url for s in store.sources.values() if s.url}
        assessments = state.get("assessments", {})
        ranked = sorted(store.sources.values(), key=lambda s: -(assessments[s.id].relevance if s.id in assessments else 0))
        leads = []
        for source in ranked[:8]:
            for ref in source.references_found:
                norm = normalize_doi(ref) if not ref.startswith("http") else ref
                if norm not in known and (source.id, ref) not in leads:
                    leads.append((source.id, ref))
        added = 0
        demo = next((p for p in ctx.providers.providers if p.name == "demo"), None)
        for parent_id, ref in leads[:MAX_LEADS_PER_ITERATION]:
            if not ctx.budget.try_search():
                break
            result, origin = None, None
            if demo is not None:
                match = next((r for r in demo.records if normalize_doi(r.get("doi")) == normalize_doi(ref)
                              or r.get("url") == ref), None)
                if match:
                    result = next((x for x in demo.search(match["title"], 3) if x.title == match["title"]), None)
                    origin = Origin.DEMO
            elif ctx.providers.crossref is not None and not ref.startswith("http"):
                result, origin = ctx.providers.crossref.resolve_doi(normalize_doi(ref)), Origin.LIVE
            record = SearchRecord(iteration=state.get("iteration", 0), subquestion_id=None, query=f"lead: {ref}",
                                  provider="lead", status="ok" if result else "error",
                                  message="" if result else "Lead could not be resolved.")
            if result:
                parent = store.sources[parent_id]
                source, is_new = store.add_result(result, origin, (parent.subquestion_ids or [None])[0], f"lead:{ref}",
                                                  state.get("iteration", 0))
                if is_new and ctx.budget.add_sources(1):
                    source.metadata["lead_from"] = parent_id
                    added += 1
                    record.new_sources = 1
                    ctx.event(self.name, "search", f"Followed a reference in '{parent.title[:60]}' → '{source.title[:60]}'",
                              state.get("iteration", 0), provider="lead")
            records.append(record)
        return added

    def _fetch_full_text(self, store: EvidenceStore, ctx: RunContext, iteration: int) -> int:
        if not (ctx.provider_settings.fetch_full_text and ctx.providers.http):
            return 0
        targets = [s for s in store.sources.values() if s.retrieval_method.startswith("tavily")
                   and not s.full_text_available and s.url and s.iteration == iteration][:MAX_FETCH_PER_ITERATION]
        if not targets or ctx.budget.out_of_time():
            return 0
        http = ctx.providers.http

        def fetch(source):
            html = http.safe_fetch(source.url)
            return source.id, (extract_html(html, source.url) if html else None)

        count = 0
        with ThreadPoolExecutor(max_workers=ctx.max_parallel) as pool:
            for source_id, text in map_with_context(pool, fetch, targets):
                if text:
                    store.replace_content(source_id, text)
                    count += 1
                else:
                    store.sources[source_id].limitations.append("Full page could not be fetched or extracted; "
                                                                "analysis uses the search snippet only.")
        return count
