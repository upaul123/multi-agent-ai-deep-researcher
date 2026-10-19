"""Thread-safe research budget tracker: searches, sources, LLM calls/tokens and wall-clock time."""
from __future__ import annotations

import threading
import time

from src.models.research import BudgetUsage, ResearchBudget


class RunBudget:
    def __init__(self, budget: ResearchBudget, clock=time.monotonic):
        self.budget = budget
        self.clock = clock
        self.started = clock()
        self._lock = threading.Lock()
        self.usage = BudgetUsage()

    # ------------------------------------------------------------------ checks
    @property
    def elapsed(self) -> float:
        return self.clock() - self.started

    def time_left(self) -> float:
        return self.budget.max_seconds - self.elapsed

    def out_of_time(self) -> bool:
        return self.time_left() <= 0

    def _mark(self, reason: str) -> None:
        if reason not in self.usage.exhausted:
            self.usage.exhausted.append(reason)

    def cancel(self) -> None:
        """Stop gathering: no further searches or LLM calls. The workflow then reports what it already has."""
        with self._lock:
            self._mark("cancelled")

    def interrupt(self) -> None:
        """Stop gathering because the app is shutting down. Unlike a user's stop, the run then ends as failed."""
        with self._lock:
            self._mark("interrupted")

    @property
    def cancelled(self) -> bool:
        """Stopped by the user or interrupted: either way, no further searches or LLM calls."""
        return "cancelled" in self.usage.exhausted or self.interrupted

    @property
    def interrupted(self) -> bool:
        return "interrupted" in self.usage.exhausted

    def try_search(self) -> bool:
        """Reserve one search. Returns False (and records why) when a limit is reached."""
        with self._lock:
            if self.cancelled:
                return False
            if self.out_of_time():
                self._mark("time")
                return False
            if self.usage.searches >= self.budget.max_searches:
                self._mark("searches")
                return False
            self.usage.searches += 1
            return True

    def record_cache_hit(self) -> None:
        with self._lock:
            self.usage.cache_hits += 1

    def add_sources(self, count: int) -> int:
        """Admit up to `count` new sources; returns how many were admitted."""
        with self._lock:
            admitted = min(count, self.budget.max_sources - self.usage.sources)
            admitted = max(0, admitted)
            self.usage.sources += admitted
            if admitted < count:
                self._mark("sources")
            return admitted

    def try_llm(self) -> bool:
        with self._lock:
            if self.cancelled:
                return False
            if self.out_of_time():
                self._mark("time")
                return False
            if self.usage.llm_calls >= self.budget.max_llm_calls:
                self._mark("llm_calls")
                return False
            if self.usage.llm_tokens >= self.budget.max_llm_tokens:
                self._mark("llm_tokens")
                return False
            self.usage.llm_calls += 1
            return True

    def reserve_llm_attempt(self, tokens: int) -> bool:
        """Atomically reserve a conservative token bound for one actual API request."""
        with self._lock:
            if self.cancelled:
                return False
            if self.out_of_time():
                self._mark("time")
                return False
            if self.usage.llm_attempts >= self.budget.max_llm_calls:
                self._mark("llm_calls")
                return False
            charged = self.usage.llm_tokens + self.usage.llm_unreported_tokens + self.usage.llm_reserved_tokens
            if charged + tokens > self.budget.max_llm_tokens:
                self._mark("llm_tokens")
                return False
            self.usage.llm_attempts += 1
            self.usage.llm_reserved_tokens += tokens
            return True

    def finish_llm_attempt(self, reserved: int, usage=None, input_price=0.0, output_price=0.0) -> None:
        with self._lock:
            def token_count(value):
                try:
                    count = int(value)
                    return count if count >= 0 and count == value else None
                except (TypeError, ValueError, OverflowError):
                    return None
            reported = token_count(getattr(usage, "total_tokens", None)) if usage else None
            inp = token_count(getattr(usage, "prompt_tokens", None)) if usage else None
            out = token_count(getattr(usage, "completion_tokens", None)) if usage else None
            self.usage.llm_reserved_tokens -= reserved
            if reported is None:
                # A timeout may still have been billed: retain the full bound as an uncertain charge.
                self.usage.llm_unreported_tokens += reserved
            else:
                self.usage.llm_tokens += max(0, int(reported))
                if inp is not None and out is not None:
                    self.usage.input_tokens += max(0, int(inp))
                    self.usage.output_tokens += max(0, int(out))
                    self.usage.estimated_cost_usd += (max(0, int(inp)) * input_price + max(0, int(out)) * output_price) / 1_000_000
            self.usage.cost_estimate_complete = bool(input_price and output_price and inp is not None and out is not None
                    and not self.usage.llm_unreported_tokens
                    and self.usage.input_tokens + self.usage.output_tokens == self.usage.llm_tokens)

    def snapshot(self, iterations: int | None = None) -> BudgetUsage:
        with self._lock:
            usage = self.usage.model_copy(deep=True)
        usage.elapsed_seconds = round(self.elapsed, 1)
        if iterations is not None:
            usage.iterations = iterations
        return usage
