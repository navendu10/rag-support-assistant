"""Tests for the rate limiter, retry policy and throttled transport (fake clock, no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from rag_assistant.llm import (
    CallTiming,
    RateLimiter,
    RateLimitExhaustedError,
    RetryPolicy,
    ThrottledTransport,
    TransportStats,
    estimate_tokens,
    parse_retry_after,
    timed_calls,
    transport_stats,
    usage_tokens,
)


class FakeClock:
    """A controllable clock whose sleep advances time."""

    def __init__(self) -> None:
        """Start at t=0 with no sleeps."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """Current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record and advance."""
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(rpm: int, tpm: int) -> tuple[RateLimiter, FakeClock]:
    """A limiter driven by a fake clock."""
    clock = FakeClock()
    return RateLimiter(rpm, tpm, clock=clock, sleep=clock.sleep), clock


def test_limiter_blocks_on_requests_per_minute() -> None:
    """The third request in a 2-rpm window waits for the first to expire."""
    limiter, clock = _limiter(rpm=2, tpm=10_000)
    limiter.acquire(10)
    limiter.acquire(10)
    assert clock.sleeps == []
    limiter.acquire(10)
    assert clock.now == pytest.approx(60.05)


def test_limiter_blocks_on_tokens_per_minute() -> None:
    """A request that would exceed the token budget waits."""
    limiter, clock = _limiter(rpm=100, tpm=1000)
    limiter.acquire(800)
    limiter.acquire(300)
    assert clock.now == pytest.approx(60.05)


def test_limiter_uses_actual_usage() -> None:
    """Recording real usage frees reserved tokens."""
    limiter, clock = _limiter(rpm=100, tpm=1000)
    event = limiter.acquire(900)
    limiter.record(event, 100)
    limiter.acquire(800)
    assert clock.sleeps == []


def test_oversized_request_runs_when_window_empty() -> None:
    """A single request larger than the budget is not blocked forever."""
    limiter, clock = _limiter(rpm=10, tpm=100)
    limiter.acquire(5000)
    assert clock.sleeps == []


def test_retry_policy_backoff_and_retry_after() -> None:
    """Backoff doubles up to the cap; retry-after wins; huge retry-after aborts."""
    policy = RetryPolicy(max_retries=5, base_seconds=2, max_seconds=10, max_retry_after_seconds=60)
    assert [policy.delay(a, None, jitter=0.0) for a in range(4)] == [2, 4, 8, 10]
    assert policy.delay(0, None, jitter=1.0) == pytest.approx(2.5)
    assert policy.delay(3, 7.0, jitter=1.0) == 7.0
    with pytest.raises(RateLimitExhaustedError, match="daily"):
        policy.delay(0, 3600.0, jitter=0.0)


def test_header_and_body_helpers() -> None:
    """retry-after and usage parsing tolerate bad input."""
    assert parse_retry_after(httpx.Headers({"retry-after": "3"})) == 3.0
    assert parse_retry_after(httpx.Headers({"retry-after": "soon"})) is None
    assert parse_retry_after(httpx.Headers()) is None
    assert usage_tokens(b'{"usage": {"total_tokens": 42}}') == 42
    assert usage_tokens(b"not json") is None
    assert estimate_tokens(b"x" * 400, expected_output=50) == 150


class ScriptedTransport(httpx.BaseTransport):
    """Returns pre-baked responses in order and counts calls."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        """Queue ``responses``."""
        self.responses = responses
        self.calls = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Pop the next response."""
        self.calls += 1
        return self.responses.pop(0)


def _ok() -> httpx.Response:
    """A successful chat-completion response."""
    body = {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 12}}
    return httpx.Response(200, json=body)


def _client(inner: ScriptedTransport, clock: FakeClock, retries: int = 3) -> httpx.Client:
    """An httpx client using ThrottledTransport over ``inner``."""
    limiter = RateLimiter(100, 100_000, clock=clock, sleep=clock.sleep)
    policy = RetryPolicy(retries, base_seconds=1, max_seconds=8, max_retry_after_seconds=60)
    transport = ThrottledTransport(
        limiter, policy, 100, inner=inner, sleep=clock.sleep, jitter=lambda: 0.0
    )
    return httpx.Client(transport=transport)


def test_transport_counts_requests_and_waits() -> None:
    """Throttle waits and retry delays are added to the model's stats, per attempt."""
    clock = FakeClock()
    stats = TransportStats()
    limiter = RateLimiter(1, 100_000, clock=clock, sleep=clock.sleep)  # 1 request a minute
    policy = RetryPolicy(3, base_seconds=1, max_seconds=8, max_retry_after_seconds=60)
    transport = ThrottledTransport(
        limiter,
        policy,
        100,
        inner=ScriptedTransport([_ok(), httpx.Response(429), _ok()]),
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        stats=stats,
    )
    client = httpx.Client(transport=transport)
    client.post("https://api.example/chat", content=b"{}")
    assert (stats.requests, stats.wait_seconds) == (1, 0.0)
    client.post("https://api.example/chat", content=b"{}")  # throttled, 429, backoff, throttled
    assert stats.requests == 3
    assert stats.wait_seconds == pytest.approx(sum(clock.sleeps))
    assert 1.0 in clock.sleeps  # the retry backoff is counted as a wait


def test_timed_calls_reports_net_time_and_cache_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """net_ms = wall minus waits; no request in the block means a cache hit."""
    stats = transport_stats("timing-test-model")
    with timed_calls("timing-test-model") as cached:
        pass
    assert cached.cached and cached.requests == 0
    with timed_calls("timing-test-model") as call:
        stats.requests += 1
        stats.wait_seconds += 0.0
    assert not call.cached and call.requests == 1
    assert CallTiming(wall_ms=5_000.0, wait_ms=4_000.0).net_ms == 1_000.0
    assert CallTiming(wall_ms=10.0, wait_ms=20.0).net_ms == 0.0
    with timed_calls(None) as nothing:
        pass
    assert nothing.requests == 0


def test_transport_retries_429_honouring_retry_after() -> None:
    """A 429 with retry-after is retried after exactly that delay."""
    clock = FakeClock()
    inner = ScriptedTransport([httpx.Response(429, headers={"retry-after": "5"}), _ok()])
    response = _client(inner, clock).post("https://api.example/chat", content=b"{}")
    assert response.status_code == 200
    assert json.loads(response.content)["usage"]["total_tokens"] == 12
    assert inner.calls == 2
    assert clock.sleeps == [5.0]


def test_transport_exponential_backoff_without_retry_after() -> None:
    """Without retry-after, waits grow 1s, 2s, 4s."""
    clock = FakeClock()
    inner = ScriptedTransport([httpx.Response(429)] * 3 + [_ok()])
    response = _client(inner, clock).post("https://api.example/chat", content=b"{}")
    assert response.status_code == 200
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_transport_gives_up_after_max_retries() -> None:
    """After the last retry the 429 is returned for the SDK to raise."""
    clock = FakeClock()
    inner = ScriptedTransport([httpx.Response(429)] * 3)
    response = _client(inner, clock, retries=2).post("https://api.example/chat", content=b"{}")
    assert response.status_code == 429
    assert inner.calls == 3


def test_transport_does_not_retry_client_errors() -> None:
    """A 400 is returned immediately."""
    clock = FakeClock()
    inner = ScriptedTransport([httpx.Response(400, json={"error": "bad"})])
    response = _client(inner, clock).post("https://api.example/chat", content=b"{}")
    assert response.status_code == 400
    assert inner.calls == 1
