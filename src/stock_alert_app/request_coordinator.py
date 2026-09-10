from __future__ import annotations

import itertools
import logging
import queue
import random
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

logger = logging.getLogger(__name__)

PRIORITIES = {"deep_analysis": 0, "foreground": 1, "background": 2}


@dataclass(frozen=True)
class ProviderPolicy:
    per_minute: int
    daily_budget: int
    burst: int = 1
    failure_threshold: int = 3


@dataclass(frozen=True)
class MarketDataSnapshot:
    symbol: str
    period: str
    interval: str
    frame: pd.DataFrame
    provider: str
    fetched_at: float
    data_timestamp: str
    stale: bool = False
    cache_status: str = "provider"


class TokenBucket:
    def __init__(self, capacity: float, refill_per_second: float, *, clock: Callable[[], float] = time.time):
        self.capacity = max(1.0, float(capacity))
        self.refill_per_second = max(0.0, float(refill_per_second))
        self.tokens = self.capacity
        self.updated_at = clock()
        self._clock = clock
        self._lock = threading.Lock()

    def try_consume(self, amount: float = 1.0) -> bool:
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self.updated_at)
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            self.updated_at = now
            if self.tokens + 1e-9 < amount:
                return False
            self.tokens -= amount
            return True

    def wait_time(self, amount: float = 1.0) -> float:
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self.updated_at)
            available = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
            if available >= amount:
                return 0.0
            if self.refill_per_second <= 0:
                return float("inf")
            return (amount - available) / self.refill_per_second


@dataclass
class _ProviderState:
    policy: ProviderPolicy
    bucket: TokenBucket
    failures: int = 0
    open_until: float = 0.0
    day: str = ""
    daily_used: int = 0
    minute_window: int = -1
    minute_used: int = 0


@dataclass(frozen=True)
class _Request:
    symbol: str
    period: str
    interval: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.symbol.upper(), self.period.lower(), self.interval.lower())


class RequestCoordinator:
    """Cache, scheduling, quotas, and resilience between callers and providers."""

    def __init__(
        self,
        providers: Iterable[Any],
        *,
        policies: Mapping[str, ProviderPolicy] | None = None,
        fresh_ttl: float = 300.0,
        stale_ttl: float = 3600.0,
        cooldown_s: float = 120.0,
        max_retries: int = 2,
        backoff_base_s: float = 0.25,
        backoff_cap_s: float = 4.0,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ):
        self.providers = list(providers)
        self.fresh_ttl = max(0.0, fresh_ttl)
        self.stale_ttl = max(self.fresh_ttl, stale_ttl)
        self.cooldown_s = max(0.0, cooldown_s)
        self.max_retries = max(0, max_retries)
        self.backoff_base_s = max(0.0, backoff_base_s)
        self.backoff_cap_s = max(self.backoff_base_s, backoff_cap_s)
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng or random.Random()
        self._cache: dict[tuple[str, str, str], MarketDataSnapshot] = {}
        self._inflight: dict[tuple[str, str, str], Future] = {}
        self._lock = threading.RLock()
        self._queue: queue.PriorityQueue[tuple[int, int, list[_Request] | None]] = queue.PriorityQueue()
        self._sequence = itertools.count()
        configured = policies or {}
        self._states: dict[str, _ProviderState] = {}
        for provider in self.providers:
            policy = configured.get(provider.name, ProviderPolicy(30, 2000, 5))
            capacity = max(1, min(policy.burst, policy.per_minute))
            self._states[provider.name] = _ProviderState(
                policy=policy,
                bucket=TokenBucket(capacity, policy.per_minute / 60.0, clock=clock),
            )
        self._worker = threading.Thread(target=self._run, name="market-data-coordinator", daemon=True)
        self._worker.start()

    @staticmethod
    def _copy(snapshot: MarketDataSnapshot, **changes: Any) -> MarketDataSnapshot:
        return replace(snapshot, frame=snapshot.frame.copy(deep=True), **changes)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._inflight.clear()

    def shutdown(self) -> None:
        self._queue.put((99, next(self._sequence), None))
        self._worker.join(timeout=2.0)

    def get_snapshot(
        self,
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
        *,
        priority: str = "foreground",
    ) -> MarketDataSnapshot:
        request = _Request(symbol.strip().upper(), period, interval)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(request.key)
            if cached and now - cached.fetched_at <= self.fresh_ttl:
                return self._copy(cached, stale=False, cache_status="fresh_cache")
            if cached and now - cached.fetched_at <= self.stale_ttl:
                self._schedule_locked([request], "background")
                return self._copy(cached, stale=True, cache_status="stale_cache")
            future = self._schedule_locked([request], priority)[request.key]
        return self._copy(future.result())

    def get_many(
        self,
        symbols: Iterable[str],
        period: str = "6mo",
        interval: str = "1d",
        *,
        priority: str = "background",
    ) -> dict[str, MarketDataSnapshot]:
        requests = [_Request(s.strip().upper(), period, interval) for s in dict.fromkeys(symbols) if s.strip()]
        now = self._clock()
        results: dict[str, MarketDataSnapshot] = {}
        missing: list[_Request] = []
        stale: list[_Request] = []
        with self._lock:
            for request in requests:
                cached = self._cache.get(request.key)
                if cached and now - cached.fetched_at <= self.fresh_ttl:
                    results[request.symbol] = self._copy(cached, stale=False, cache_status="fresh_cache")
                elif cached and now - cached.fetched_at <= self.stale_ttl:
                    results[request.symbol] = self._copy(cached, stale=True, cache_status="stale_cache")
                    stale.append(request)
                else:
                    missing.append(request)
            if stale:
                self._schedule_locked(stale, "background")
            futures = self._schedule_locked(missing, priority) if missing else {}
        for request in missing:
            results[request.symbol] = self._copy(futures[request.key].result())
        return results

    def _schedule_locked(self, requests: list[_Request], priority: str) -> dict[tuple[str, str, str], Future]:
        futures: dict[tuple[str, str, str], Future] = {}
        new: list[_Request] = []
        for request in requests:
            future = self._inflight.get(request.key)
            if future is None:
                future = Future()
                self._inflight[request.key] = future
                new.append(request)
            futures[request.key] = future
        if new:
            self._queue.put((PRIORITIES.get(priority, PRIORITIES["foreground"]), next(self._sequence), new))
        return futures

    def _run(self) -> None:
        while True:
            _, _, requests = self._queue.get()
            if requests is None:
                self._queue.task_done()
                return
            try:
                produced = self._execute(requests)
                for request in requests:
                    snapshot = produced.get(request.key) or self._snapshot(request, pd.DataFrame(), "")
                    with self._lock:
                        if not snapshot.frame.empty:
                            self._cache[request.key] = snapshot
                        future = self._inflight.pop(request.key, None)
                    if future is not None and not future.done():
                        future.set_result(snapshot)
            except BaseException as exc:
                with self._lock:
                    futures = [self._inflight.pop(request.key, None) for request in requests]
                for future in futures:
                    if future is not None and not future.done():
                        future.set_exception(exc)
            finally:
                self._queue.task_done()

    def _execute(self, requests: list[_Request]) -> dict[tuple[str, str, str], MarketDataSnapshot]:
        remaining = {request.key: request for request in requests}
        result: dict[tuple[str, str, str], MarketDataSnapshot] = {}
        for provider in self.providers:
            supported = [request for request in remaining.values() if self._supports(provider, request)]
            if not supported or not self._available(provider):
                continue
            if bool(getattr(provider, "supports_batch", False)) and len(supported) > 1:
                frames = self._call(provider, lambda: provider.fetch_many(
                    [request.symbol for request in supported], supported[0].period, supported[0].interval
                )) or {}
                for request in supported:
                    frame = frames.get(request.symbol)
                    if self._valid(frame):
                        result[request.key] = self._snapshot(request, frame, provider.name)
                        remaining.pop(request.key, None)
            else:
                for request in supported:
                    if not self._available(provider):
                        break
                    frame = self._call(provider, lambda request=request: provider.fetch(
                        request.symbol, request.period, request.interval
                    ))
                    if self._valid(frame):
                        result[request.key] = self._snapshot(request, frame, provider.name)
                        remaining.pop(request.key, None)
            if not remaining:
                break
        return result

    @staticmethod
    def _supports(provider: Any, request: _Request) -> bool:
        try:
            return bool(provider.enabled()) and bool(provider.supports(request.period, request.interval))
        except AttributeError:
            return bool(provider.enabled())

    @staticmethod
    def _valid(frame: Any) -> bool:
        return isinstance(frame, pd.DataFrame) and not frame.empty and len(frame) >= 2 and "Close" in frame.columns

    def _snapshot(self, request: _Request, frame: pd.DataFrame, provider: str) -> MarketDataSnapshot:
        timestamp = ""
        if not frame.empty:
            try:
                timestamp = pd.Timestamp(frame.index[-1]).isoformat()
            except (TypeError, ValueError):
                timestamp = ""
        return MarketDataSnapshot(
            symbol=request.symbol,
            period=request.period,
            interval=request.interval,
            frame=frame.copy(deep=True),
            provider=provider,
            fetched_at=self._clock(),
            data_timestamp=timestamp,
        )

    def _available(self, provider: Any) -> bool:
        state = self._states[provider.name]
        now = self._clock()
        if state.open_until > now:
            return False
        day = datetime.fromtimestamp(now, UTC).date().isoformat()
        if state.day != day:
            state.day = day
            state.daily_used = 0
        minute_window = int(now // 60)
        if state.minute_window != minute_window:
            state.minute_window = minute_window
            state.minute_used = 0
        return state.daily_used < state.policy.daily_budget

    def _take_budget(self, provider: Any) -> bool:
        state = self._states[provider.name]
        if not self._available(provider):
            return False
        while not state.bucket.try_consume():
            wait = state.bucket.wait_time()
            if wait == float("inf"):
                return False
            self._sleep(wait)
            if not self._available(provider):
                return False
        state.daily_used += 1
        state.minute_used += 1
        return True

    def _call(self, provider: Any, operation: Callable[[], Any]) -> Any:
        for attempt in range(self.max_retries + 1):
            if not self._take_budget(provider):
                return None
            try:
                value = operation()
                self._record_success(provider.name)
                return value
            except Exception as exc:  # noqa: BLE001
                if self._expected_miss(exc):
                    return None
                retryable = self._retryable(exc)
                if retryable:
                    self._record_failure(provider.name)
                logger.warning("Provider %s request failed: %s", provider.name, exc)
                if not retryable or attempt >= self.max_retries or not self._available(provider):
                    return None
                delay = min(self.backoff_cap_s, self.backoff_base_s * (2 ** attempt))
                self._sleep(delay * (0.5 + self._rng.random()))
        return None

    @staticmethod
    def _status(exc: Exception) -> int | None:
        raw = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if raw is None:
            raw = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _expected_miss(cls, exc: Exception) -> bool:
        status = cls._status(exc)
        message = str(exc).lower()
        return status == 404 or any(token in message for token in ("404", "not found", "possibly delisted"))

    @classmethod
    def _retryable(cls, exc: Exception) -> bool:
        status = cls._status(exc)
        message = str(exc).lower()
        return status == 429 or bool(status and 500 <= status < 600) or any(
            token in message for token in ("429", "rate limit", "too many requests", "timeout", "temporarily unavailable", "connection reset")
        )

    def _record_failure(self, name: str) -> None:
        state = self._states[name]
        state.failures += 1
        if state.failures >= state.policy.failure_threshold:
            state.open_until = self._clock() + self.cooldown_s

    def _record_success(self, name: str) -> None:
        state = self._states[name]
        state.failures = 0
        state.open_until = 0.0

    def status(self) -> list[dict[str, object]]:
        now = self._clock()
        rows = []
        for provider in self.providers:
            state = self._states[provider.name]
            rows.append({
                "name": provider.name,
                "enabled": bool(provider.enabled()),
                "cooling_down": state.open_until > now,
                "cooldown_remaining_s": round(max(0.0, state.open_until - now), 1),
                "minute_used": state.minute_used,
                "per_minute": state.policy.per_minute,
                "daily_used": state.daily_used,
                "daily_budget": state.policy.daily_budget,
                "supports_batch": bool(getattr(provider, "supports_batch", False)),
            })
        return rows
