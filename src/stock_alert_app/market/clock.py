"""Explicit clocks for live time and deterministic replay time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class MarketClock:
    def now(self) -> datetime:  # pragma: no cover - interface
        raise NotImplementedError


class LiveClock(MarketClock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ReplayClock(MarketClock):
    def __init__(self, start: datetime, end: datetime | None = None):
        self._start = self._normalize(start)
        self._current = self._start
        self._end = self._normalize(end) if end else None
        self._speed = 1.0
        self._running = False

    @staticmethod
    def _normalize(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._current

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def running(self) -> bool:
        return self._running

    def play(self) -> None:
        self._running = True

    def pause(self) -> None:
        self._running = False

    def resume(self) -> None:
        self.play()

    def restart(self) -> None:
        self._current = self._start
        self._running = False

    def seek(self, timestamp: datetime) -> datetime:
        value = self._normalize(timestamp)
        if value < self._start:
            value = self._start
        if self._end and value > self._end:
            value = self._end
        self._current = value
        return value

    def set_speed(self, multiplier: float) -> float:
        self._speed = max(0.1, float(multiplier))
        return self._speed

    def step_forward(self, seconds: float = 1.0) -> datetime:
        return self.seek(self._current + timedelta(seconds=seconds))

    def step_backward(self, seconds: float = 1.0) -> datetime:
        return self.seek(self._current - timedelta(seconds=seconds))
