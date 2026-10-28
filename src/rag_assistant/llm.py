"""Groq chat models with client-side throttling, retry on 429 and a SQLite cache.

Throttling and retries live in a custom ``httpx`` transport, so they apply only to
real network calls: LangChain's cache answers repeated prompts before any HTTP
request is made, and cached calls are never slowed down.

The transport also counts, per model, how many HTTP attempts it made and how long it
spent waiting (throttle and retry sleeps). ``timed_calls`` turns those counters into
per-call timings, so evaluation latency can exclude waits that say nothing about the
pipeline, and can tell cache hits (no HTTP request at all) apart.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
from langchain_community.cache import SQLiteCache
from langchain_core.globals import get_llm_cache, set_llm_cache
from langchain_groq import ChatGroq

from rag_assistant.config import CacheSettings, RateLimitSettings, Settings

WINDOW_SECONDS = 60.0
CHARS_PER_TOKEN = 4
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RateLimitExhaustedError(RuntimeError):
    """Groq asked us to wait longer than we are willing to (usually the daily quota)."""


@dataclass
class _Event:
    """One request in the sliding window."""

    started: float
    tokens: int
    waited: float = 0.0


@dataclass
class RateLimiter:
    """Sliding 60-second window limiting both requests and tokens per minute."""

    requests_per_minute: int
    tokens_per_minute: int
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _events: list[_Event] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _prune(self, now: float) -> None:
        """Forget requests older than the window."""
        self._events = [e for e in self._events if now - e.started < WINDOW_SECONDS]

    def _wait_needed(self, now: float, tokens: int) -> float:
        """Seconds to wait before a request of ``tokens`` fits, or 0."""
        used = sum(e.tokens for e in self._events)
        fits_requests = len(self._events) < self.requests_per_minute
        fits_tokens = used + tokens <= self.tokens_per_minute or not self._events
        if fits_requests and fits_tokens:
            return 0.0
        return max(0.0, self._events[0].started + WINDOW_SECONDS - now) + 0.05

    def acquire(self, estimated_tokens: int) -> _Event:
        """Block until a request fits, then reserve ``estimated_tokens`` for it.

        The returned event records how long this call slept (``waited``).
        """
        waited = 0.0
        while True:
            with self._lock:
                now = self.clock()
                self._prune(now)
                wait = self._wait_needed(now, estimated_tokens)
                if wait == 0.0:
                    event = _Event(now, estimated_tokens, waited)
                    self._events.append(event)
                    return event
            self.sleep(wait)
            waited += wait

    def record(self, event: _Event, actual_tokens: int) -> None:
        """Replace the reservation with the tokens the request really used."""
        with self._lock:
            event.tokens = actual_tokens


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter, honouring ``retry-after`` when present."""

    max_retries: int
    base_seconds: float
    max_seconds: float
    max_retry_after_seconds: float

    def delay(self, attempt: int, retry_after: float | None, jitter: float) -> float:
        """Seconds to wait before retry number ``attempt`` (0-based)."""
        if retry_after is not None:
            if retry_after > self.max_retry_after_seconds:
                raise RateLimitExhaustedError(
                    f"Groq asked to wait {retry_after:.0f}s (over the "
                    f"{self.max_retry_after_seconds:.0f}s limit). The daily token or request "
                    "quota is probably used up; try again later."
                )
            return retry_after
        backoff = min(self.max_seconds, self.base_seconds * 2**attempt)
        return backoff * (1 + 0.25 * jitter)


def parse_retry_after(headers: httpx.Headers) -> float | None:
    """Read ``retry-after`` (seconds) from response headers, if valid."""
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def estimate_tokens(body: bytes, expected_output: int) -> int:
    """Rough token estimate for a request: prompt characters / 4 plus expected output."""
    return len(body) // CHARS_PER_TOKEN + expected_output


def usage_tokens(content: bytes) -> int | None:
    """Total tokens reported in a chat-completion response body, if present."""
    try:
        return int(json.loads(content)["usage"]["total_tokens"])
    except (ValueError, KeyError, TypeError):
        return None


@dataclass
class TransportStats:
    """Running totals for one model: HTTP attempts and seconds spent waiting."""

    requests: int = 0
    wait_seconds: float = 0.0


_STATS: dict[str, TransportStats] = {}
_STATS_LOCK = threading.Lock()


def transport_stats(model: str) -> TransportStats:
    """The shared counters for ``model`` (created on first use)."""
    with _STATS_LOCK:
        return _STATS.setdefault(model, TransportStats())


@dataclass
class CallTiming:
    """Wall time of a block of LLM calls, the waits inside it, and its HTTP attempts."""

    wall_ms: float = 0.0
    wait_ms: float = 0.0
    requests: int = 0

    @property
    def net_ms(self) -> float:
        """Wall time minus throttle and retry waits."""
        return max(0.0, self.wall_ms - self.wait_ms)

    @property
    def cached(self) -> bool:
        """True if no HTTP request was made (every call was a cache hit)."""
        return self.requests == 0


@contextmanager
def timed_calls(model: str | None) -> Iterator[CallTiming]:
    """Time the LLM calls made to ``model`` inside the block (no-op stats if None)."""
    stats = transport_stats(model) if model else TransportStats()
    requests, waited = stats.requests, stats.wait_seconds
    timing = CallTiming()
    start = time.perf_counter()
    try:
        yield timing
    finally:
        timing.wall_ms = round((time.perf_counter() - start) * 1000, 1)
        timing.wait_ms = round((stats.wait_seconds - waited) * 1000, 1)
        timing.requests = stats.requests - requests


class ThrottledTransport(httpx.BaseTransport):
    """httpx transport that rate-limits requests and retries 429/5xx responses."""

    def __init__(
        self,
        limiter: RateLimiter,
        policy: RetryPolicy,
        expected_output_tokens: int,
        inner: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        stats: TransportStats | None = None,
    ) -> None:
        """Wrap ``inner`` (a real HTTP transport by default), counting into ``stats``."""
        self._stats = stats or TransportStats()
        self._limiter = limiter
        self._policy = policy
        self._expected_output = expected_output_tokens
        self._inner = inner or httpx.HTTPTransport()
        self._sleep = sleep
        self._jitter = jitter

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Send ``request``, waiting for capacity and retrying when Groq pushes back."""
        estimate = estimate_tokens(request.read(), self._expected_output)
        for attempt in range(self._policy.max_retries + 1):
            event = self._limiter.acquire(estimate)
            self._stats.wait_seconds += event.waited
            self._stats.requests += 1
            response = self._inner.handle_request(request)
            content = response.read()
            response.close()
            self._limiter.record(event, usage_tokens(content) or estimate)
            last_try = attempt == self._policy.max_retries
            if response.status_code not in RETRYABLE_STATUS or last_try:
                return _rebuild(response, content, request)
            retry_after = parse_retry_after(response.headers)
            delay = self._policy.delay(attempt, retry_after, self._jitter())
            self._stats.wait_seconds += delay
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def close(self) -> None:
        """Close the wrapped transport."""
        self._inner.close()


def _rebuild(response: httpx.Response, content: bytes, request: httpx.Request) -> httpx.Response:
    """Return an already-read response with headers matching its decoded body."""
    headers = [
        (k, v)
        for k, v in response.headers.multi_items()
        if k.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
    ]
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=content,
        request=request,
        extensions={"http_version": response.extensions.get("http_version", b"HTTP/1.1")},
    )


_LIMITERS: dict[str, RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def limiter_for(model: str, cfg: RateLimitSettings) -> RateLimiter:
    """One shared limiter per model, because Groq's limits are per model."""
    with _LIMITERS_LOCK:
        if model not in _LIMITERS:
            _LIMITERS[model] = RateLimiter(cfg.requests_per_minute, cfg.tokens_per_minute)
        return _LIMITERS[model]


def configure_cache(cfg: CacheSettings) -> None:
    """Install LangChain's global SQLite cache once per process (if enabled)."""
    if not cfg.enabled or isinstance(get_llm_cache(), SQLiteCache):
        return
    path = cfg.resolved_path
    path.parent.mkdir(parents=True, exist_ok=True)
    set_llm_cache(SQLiteCache(database_path=str(path)))


def make_chat_model(settings: Settings, model: str) -> ChatGroq:
    """Build a throttled, retrying, cached ChatGroq for ``model``."""
    configure_cache(settings.cache)
    rl = settings.rate_limit
    policy = RetryPolicy(
        rl.max_retries, rl.backoff_base_seconds, rl.backoff_max_seconds, rl.max_retry_after_seconds
    )
    transport = ThrottledTransport(
        limiter_for(model, rl), policy, rl.expected_output_tokens, stats=transport_stats(model)
    )
    return ChatGroq(
        model=model,
        api_key=settings.groq_api_key,
        temperature=settings.llm.temperature,
        reasoning_effort=settings.llm.reasoning_effort,
        max_tokens=settings.llm.max_completion_tokens,
        timeout=settings.llm.timeout_seconds,
        max_retries=0,  # retries happen in ThrottledTransport
        http_client=httpx.Client(transport=transport, timeout=settings.llm.timeout_seconds),
    )


def complete(model: ChatGroq, prompt: str) -> str:
    """Send a single user message and return the reply text (reasoning excluded)."""
    return str(model.invoke(prompt).content).strip()
