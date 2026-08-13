"""Canonical market capability model.

Each market exposes a fixed set of capability keys, each with an explicit
state. ``NO_DATA`` means the source/capability is genuinely unavailable for
that market — it is never fabricated and never treated as a bullish/bearish
signal. ``STALE`` means the capability exists but its most recent data is out
of date. ``ERROR`` means the capability exists but the provider failed.
"""

from __future__ import annotations

from typing import Any

AVAILABLE = "AVAILABLE"
NO_DATA = "NO_DATA"
ERROR = "ERROR"
STALE = "STALE"

STATES: tuple[str, ...] = (AVAILABLE, NO_DATA, ERROR, STALE)

# Canonical capability keys (order is meaningful for display).
PRICE = "price"
HISTORICAL_PRICE = "historical_price"
NEWS = "news"
SOCIAL = "social"
FUNDAMENTALS = "fundamentals"
INSTITUTIONAL = "institutional"
RESEARCH = "research"

CAPABILITIES: tuple[str, ...] = (
    PRICE,
    HISTORICAL_PRICE,
    NEWS,
    SOCIAL,
    FUNDAMENTALS,
    INSTITUTIONAL,
    RESEARCH,
)


def normalize_state(value: Any) -> str:
    """Map a bool / None / string value to a canonical capability state.

    ``True`` -> AVAILABLE, ``False``/``None`` -> NO_DATA; the lowercase
    signal-style ``ok``/``error``/``no_data`` values are also accepted.
    Anything unrecognised is treated as NO_DATA (never assumed available).
    """
    if value is None:
        return NO_DATA
    if isinstance(value, bool):
        return AVAILABLE if value else NO_DATA
    s = str(value).strip().upper()
    if s in STATES:
        return s
    if s in ("OK", "AVAILABLE"):
        return AVAILABLE
    if s in ("UNAVAILABLE", "MISSING"):
        return NO_DATA
    return NO_DATA


def capability_map(
    declared: dict[str, Any] | None = None,
    *,
    price: bool = False,
    news: bool = False,
    social: bool = False,
    fundamentals: bool = False,
    institutional: bool = False,
) -> dict[str, str]:
    """Build a complete canonical capability map for a market.

    Declared values (from the market config) take precedence; otherwise the
    inferred boolean flags decide. Every key is present in the result so the
    system can distinguish AVAILABLE from NO_DATA without guessing.
    """
    merged: dict[str, str] = {
        PRICE: AVAILABLE if price else NO_DATA,
        HISTORICAL_PRICE: AVAILABLE if price else NO_DATA,
        NEWS: AVAILABLE if news else NO_DATA,
        SOCIAL: AVAILABLE if social else NO_DATA,
        FUNDAMENTALS: AVAILABLE if fundamentals else NO_DATA,
        INSTITUTIONAL: AVAILABLE if institutional else NO_DATA,
        RESEARCH: AVAILABLE if (news or price) else NO_DATA,
    }
    for key, value in (declared or {}).items():
        if key in CAPABILITIES:
            merged[key] = normalize_state(value)
    return {key: merged[key] for key in CAPABILITIES}
