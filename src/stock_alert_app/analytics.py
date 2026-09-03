"""Decision measurement: Committee performance analytics + data health.

Everything here is computed from REAL stored data (decision_snapshots,
decision_evaluations, price_snapshots, verdicts, securities). Nothing is
fabricated: a bucket without enough samples is reported with its sample size
and ``reliable: false`` instead of a headline number.

Committee analytics answer: was the system actually useful?
* directional accuracy (BULL/BEAR verdicts)
* per-verdict accuracy (BULL / BEAR / NEUTRAL)
* conviction vs accuracy (does higher conviction predict better outcomes?)
* forecast error (|realized move| after the decision)
* performance by market, by signal, by regime
Every bucket always carries its sample size ``n``.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

from .db import Database

logger = logging.getLogger(__name__)

#: Buckets with fewer evaluated samples than this are reported but marked
#: ``reliable: false`` (never presented as meaningful accuracy).
MIN_SAMPLE = 5

#: Move band for scoring NEUTRAL verdicts (|realized move| within band = correct).
NEUTRAL_BAND = 0.005


def _move_pct(ev: dict[str, Any]) -> float | None:
    """Realized move after the decision from stored evaluation prices (no fabrication)."""
    ref = ev.get("reference_price")
    fwd = ev.get("p30") or ev.get("p15") or ev.get("p5") or ev.get("p60") or ev.get("close_price")
    try:
        ref = float(ref)
        fwd = float(fwd)
    except (TypeError, ValueError):
        return None
    if not ref or not math.isfinite(ref) or not math.isfinite(fwd):
        return None
    return fwd / ref - 1.0


def _bucket(n: int, correct: int, min_sample: int = MIN_SAMPLE) -> dict[str, Any]:
    acc = round(correct / n, 4) if n else None
    return {"n": n, "correct": correct, "accuracy": acc, "reliable": n >= min_sample}


def _add(buckets: dict[str, dict[str, list[int]]], key: str, ok: bool | None) -> None:
    if ok is None:
        return
    b = buckets.setdefault(key, [0, 0])
    b[0] += 1
    if ok:
        b[1] += 1


def _fin(buckets: dict[str, dict[str, list[int]]], min_sample: int = MIN_SAMPLE) -> dict[str, Any]:
    return {k: _bucket(v[0], v[1], min_sample) for k, v in sorted(buckets.items())}


def committee_analytics(db: Database, min_sample: int = MIN_SAMPLE, limit: int = 500) -> dict[str, Any]:
    """Committee performance from stored decision snapshots + real outcomes."""
    snaps = db.decision_snapshots(limit=limit)
    evals = db.decision_evaluations()

    per_verdict: dict[str, list[int]] = {}
    directional = [0, 0]
    conviction_buckets: dict[str, list[int]] = {}
    by_market: dict[str, list[int]] = {}
    by_signal: dict[str, list[int]] = {}
    by_regime: dict[str, list[int]] = {}
    forecast_errors: list[float] = []
    evaluated_n = 0

    for s in snaps:
        ev = evals.get(s["decision_id"])
        if ev is None or ev.get("status") != "ok":
            continue
        move = _move_pct(ev)
        if move is None:
            continue
        evaluated_n += 1
        forecast_errors.append(abs(move))

        verdict = str(s.get("verdict") or "").upper()
        try:
            decision = json.loads(s.get("decision_json") or "")
        except (TypeError, ValueError):
            decision = {}
        signals = decision.get("signals") or {}
        regime = (signals.get("market_regime") or {}).get("direction")

        if verdict == "BULL":
            ok = move > 0
        elif verdict == "BEAR":
            ok = move < 0
        else:
            ok = abs(move) <= NEUTRAL_BAND
        _add(per_verdict, verdict or "UNKNOWN", ok)
        if verdict in ("BULL", "BEAR"):
            directional[0] += 1
            if ok:
                directional[1] += 1

        conv = s.get("conviction")
        try:
            conv = float(conv)
        except (TypeError, ValueError):
            conv = None
        if conv is not None:
            band = "conviction<50" if conv < 0.5 else "conviction 50-70" if conv < 0.7 else "conviction>=70"
            _add(conviction_buckets, band, ok)

        _add(by_market, str(s.get("market") or "?"), ok)
        if regime in ("BULL", "BEAR", "NEUTRAL"):
            _add(by_regime, regime, ok)

        for key, sig in signals.items():
            st = (sig or {}).get("status")
            direction = (sig or {}).get("direction")
            if st != "AVAILABLE" or direction not in ("BULL", "BEAR"):
                continue  # only signals that actually voted are measured
            aligned = (direction == verdict) if verdict in ("BULL", "BEAR") else direction == "NEUTRAL"
            # A signal's value is measured by whether TRUSTING it alone was right.
            solo_ok = (move > 0) if direction == "BULL" else (move < 0)
            _add(by_signal, key, solo_ok)

    def _acc(n: int, c: int) -> float | None:
        return round(c / n, 4) if n else None

    forecast_error = (
        round(sum(forecast_errors) / len(forecast_errors), 6) if forecast_errors else None
    )

    return {
        "min_sample": min_sample,
        "snapshots_total": len(snaps),
        "evaluated": evaluated_n,
        "note": (
            "Accuracy is computed only from decisions with a stored real-price outcome. "
            f"Buckets with n < {min_sample} are shown with reliable=false and must not be read as meaningful accuracy."
        ),
        "directional_accuracy": {**_bucket(directional[0], directional[1], min_sample), "scope": "BULL+BEAR verdicts"},
        "per_verdict": _fin(per_verdict, min_sample),
        "conviction_vs_accuracy": _fin(conviction_buckets, min_sample),
        "forecast_error": {
            "metric": "mean |realized move| after decision (close-to-close, stored outcomes)",
            "n": len(forecast_errors),
            "value": forecast_error,
            "reliable": len(forecast_errors) >= min_sample,
        },
        "by_market": _fin(by_market, min_sample),
        "by_signal": _fin(by_signal, min_sample),
        "by_regime": _fin(by_regime, min_sample),
    }


# ---------------------------------------------------------------------------
# Data health (lightweight, read-only)
# ---------------------------------------------------------------------------


def data_health(db: Database) -> dict[str, Any]:
    """Provider status, data-quality counts, coverage and worker/job health."""
    from . import refresh
    from .price_providers import provider_status

    with db.connect() as conn:
        sec_total = conn.execute("SELECT COUNT(*) c FROM securities").fetchone()["c"]
        with_snap = conn.execute(
            """SELECT COUNT(DISTINCT market || ':' || ticker) c FROM price_snapshots"""
        ).fetchone()["c"]
        stale = conn.execute(
            """SELECT COUNT(*) c FROM price_snapshots p
               WHERE p.fetched_at = (SELECT MAX(p2.fetched_at) FROM price_snapshots p2
                                     WHERE p2.market = p.market AND p2.ticker = p.ticker)
                 AND p.data_status = 'stale'"""
        ).fetchone()["c"]
        errored = conn.execute(
            "SELECT COUNT(*) c FROM securities WHERE data_status = 'error'"
        ).fetchone()["c"]
        last_snap = conn.execute("SELECT MAX(fetched_at) m FROM price_snapshots").fetchone()["m"]
        verdict_total = conn.execute(
            """SELECT COUNT(*) c FROM verdicts v
               WHERE v.decided_at = (SELECT MAX(v2.decided_at) FROM verdicts v2
                                     WHERE v2.market = v.market AND v2.ticker = v.ticker)"""
        ).fetchone()["c"]
        markets_rows = conn.execute(
            """SELECT s.market,
                      COUNT(DISTINCT s.market || ':' || s.ticker) securities,
                      (SELECT COUNT(DISTINCT p.market || ':' || p.ticker) FROM price_snapshots p
                        WHERE p.market = s.market) with_data
               FROM securities s GROUP BY s.market"""
        ).fetchall()
        # Signal coverage from the latest stored verdict signal payloads.
        cov = {"quant": 0, "technical": 0, "social": 0, "market_regime": 0}
        rows = conn.execute(
            """SELECT signals FROM verdicts v
               WHERE v.decided_at = (SELECT MAX(v2.decided_at) FROM verdicts v2
                                     WHERE v2.market = v.market AND v2.ticker = v.ticker)"""
        ).fetchall()
    for r in rows:
        try:
            payload = json.loads(r["signals"] or "")
        except (TypeError, ValueError):
            continue
        if (payload.get("quantitative") or {}).get("status") == "ok":
            cov["quant"] += 1
        models = payload.get("models") or []
        if any(m.get("status") == "ok" for m in models):
            cov["technical"] += 1
        social = payload.get("social") or {}
        regime = payload.get("market_regime") or {}
        if social.get("status") == "ok":
            cov["social"] += 1
        if regime.get("status") == "ok":
            cov["market_regime"] += 1

    # News-based coverage: latest verdicts whose reason marks news as available.
    news_available = 0
    with db.connect() as conn2:
        news_rows = conn2.execute(
            """SELECT reason FROM verdicts v
               WHERE v.decided_at = (SELECT MAX(v2.decided_at) FROM verdicts v2
                                     WHERE v2.market = v.market AND v2.ticker = v.ticker)"""
        ).fetchall()
    for r in news_rows:
        reason = r["reason"] or ""
        if "News:" in reason and "News: unavailable" not in reason:
            news_available += 1
    cov["news"] = news_available

    rs = {}
    try:
        rs = refresh.refresh_status() or {}
    except Exception as exc:  # never fail health on the status endpoint
        logger.warning("refresh_status failed: %s", exc)
    warm = {"pending": None, "in_flight": None}
    try:
        warm["pending"] = int(refresh._warm_queue.qsize())
        warm["in_flight"] = len(refresh._in_flight)
    except Exception:
        pass

    no_data_count = max(0, sec_total - with_snap)
    return {
        "providers": provider_status(),
        "counts": {
            "securities": sec_total,
            "with_price_data": with_snap,
            "stale": stale,
            "no_data": no_data_count,
            "error": errored,
            "analyzed_verdicts": verdict_total,
        },
        "signal_coverage": {
            "of_analyzed": verdict_total,
            **cov,
        },
        "market_coverage": [
            {"market": r["market"], "securities": r["securities"], "with_data": r["analyzed"]}
            for r in markets_rows
        ],
        "last_price_snapshot": last_snap or "",
        "refresh": {
            "running": rs.get("running"),
            "last_fast_at": rs.get("last_fast_at"),
            "last_slow_at": rs.get("last_slow_at"),
            "next_fast_in_s": rs.get("next_fast_in"),
            "next_slow_in_s": rs.get("next_slow_in"),
            "error": rs.get("error") or "",
        },
        "workers": {"warm_queue_pending": warm["pending"], "warm_in_flight": warm["in_flight"]},
    }
