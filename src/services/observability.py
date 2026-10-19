"""Optional metadata-only LangSmith tracing. Never capture function arguments or result bodies."""
from contextlib import contextmanager
from contextvars import copy_context
from functools import wraps
import logging
import os

logger = logging.getLogger(__name__)


def enabled():
    return os.getenv("LANGSMITH_TRACING", "false").lower() in {"true", "1", "yes"}


def api_enabled():
    return enabled() and os.getenv("APP_API_TRACING", "true").lower() in {"true", "1", "yes"}


@contextmanager
def span(name, run_type="tool", metadata=None, active=None):
    """Isolate tracing setup/teardown errors while preserving application exceptions unchanged."""
    output = {}
    manager = run = None
    if api_enabled() if active is None else active:
        try:
            from langsmith.run_helpers import trace
            manager = trace(name, run_type=run_type, inputs={}, metadata=metadata or {})
            run = manager.__enter__()
        except Exception:
            manager = None
            logger.warning("Tracing setup unavailable; continuing without telemetry.")
    try:
        yield output
    except BaseException as exc:
        if run:
            try:
                run.end(outputs={}, error=type(exc).__name__)
            except Exception:
                pass
        raise
    finally:
        if manager:
            try:
                if run and run.end_time is None:
                    run.end(outputs=output, metadata={"request_metrics": output})
                manager.__exit__(None, None, None)
            except Exception:
                logger.warning("Tracing export unavailable; research results are unaffected.")


def operation(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with span(name) as output:
                result = fn(*args, **kwargs)
                output["outcome"] = "unavailable" if result is None else "completed"
                if name == "search.provider":
                    record = result[-1]
                    output.update(provider=record.provider, outcome=record.status, result_count=record.results)
                return result
        return wrapped
    return decorate


def research_trace(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        # Explicit context also disables inherited LangGraph tracing when the master switch is off.
        manager = None
        try:
            from langsmith.run_helpers import tracing_context
            manager = tracing_context(enabled=enabled())
            manager.__enter__()
        except Exception:
            manager = None
            logger.warning("Tracing context unavailable; continuing research.")
        try:
            return fn(*args, **kwargs)
        finally:
            if manager:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    logger.warning("Tracing context cleanup unavailable.")
    return wrapped


def submit_with_context(pool, fn, *args, **kwargs):
    """A distinct context per submitted job: Context objects cannot run concurrently."""
    return pool.submit(copy_context().run, fn, *args, **kwargs)


def map_with_context(pool, fn, items):
    futures = [submit_with_context(pool, fn, item) for item in items]
    return (future.result() for future in futures)
