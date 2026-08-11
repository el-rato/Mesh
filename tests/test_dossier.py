from __future__ import annotations

import pytest

from stock_alert_app.dossier import bull_bear_factors, committee_signals, signal_state


def _verdict(
    lstm_score=0.0,
    lstm_available=True,
    technical=0.0,
    news=0.0,
    news_available=False,
    reality=None,
):
    lstm_dict = {"score": lstm_score, "probability_up": 0.8, "predicted_return": 0.02, "model_confidence": 0.7}
    price = reality if reality is not None else {
        "close": 100.0,
        "momentum_20": 0.06,
        "rsi_14": 55.0,
        "above_sma_50": True,
        "trend_50_200": 0.02,
    }
    verdict = {
        "verdict": "BULL",
        "confidence": 0.7,
        "combined_score": 0.5,
        "news_score": news,
        "news_available": news_available,
        "news_label": "bullish" if news > 0.15 else "bearish" if news < -0.15 else "neutral",
        "lstm": lstm_dict if lstm_available else {"score": 0.0},
        "technical": {"score": technical},
        "price": price,
        "signal_agreement": "moderate",
        "forecast_horizon": "1 trading day",
    }
    return verdict


INST_BULLS = {
    "holding_funds": 4,
    "buy_count": 3,
    "sell_count": 1,
    "net": 2,
    "filing_date": "2026-05-15",
}


def _row(key, label, state, available, score):
    return {
        "key": key,
        "label": label,
        "state": state,
        "available": available,
        "score": score,
    }


class TestSignalState:
    def test_bull(self):
        assert signal_state(0.3) == "BULL"

    def test_bear(self):
        assert signal_state(-0.3) == "BEAR"

    def test_neutral_band(self):
        assert signal_state(0.0) == "NEUTRAL"
        assert signal_state(0.04) == "NEUTRAL"

    def test_nan(self):
        assert signal_state(float("nan")) == "NEUTRAL"


class TestCommitteeSignals:
    def test_all_available(self):
        c = committee_signals(_verdict(lstm_score=0.6, technical=0.2, news=0.3, news_available=True), INST_BULLS)
        by = {s["key"]: s for s in c["signals"]}
        assert by["quant"]["state"] == "BULL"
        assert by["technical"]["state"] == "BULL"
        assert by["news"]["state"] == "BULL"
        assert by["institutional"]["state"] == "BULL"
        assert c["verdict"] == "BULL"
        assert c["confidence"] > 0.5
        assert c["score"] > 0.25

    def test_institutional_unavailable_shows_na(self):
        c = committee_signals(_verdict(lstm_score=0.6, technical=0.2, news=0.3, news_available=True), None)
        by = {s["key"]: s for s in c["signals"]}
        assert by["institutional"]["state"] == "N/A"
        assert by["institutional"]["available"] is False

    def test_missing_signals_are_na(self):
        c = committee_signals(_verdict(lstm_available=False, technical=0.0, news=0.0, news_available=False), None)
        by = {s["key"]: s for s in c["signals"]}
        assert by["quant"]["state"] == "N/A"
        assert by["news"]["state"] == "N/A"
        assert by["technical"]["state"] == "NEUTRAL"

    def test_bear_signals(self):
        c = committee_signals(_verdict(lstm_score=-0.6, technical=-0.2, news=-0.3, news_available=True), None)
        by = {s["key"]: s for s in c["signals"]}
        assert by["quant"]["state"] == "BEAR"
        assert by["technical"]["state"] == "BEAR"
        assert by["news"]["state"] == "BEAR"

    def test_empty_verdict_is_safe(self):
        c = committee_signals(None, None)
        assert c["verdict"] == "N/A"
        assert c["score"] is None
        assert c["confidence"] is None
        assert len(c["signals"]) == 4

    def test_bull_bull_missing_news_is_bull(self):
        v = _verdict(lstm_score=0.8, technical=0.65, news_available=False)
        c = committee_signals(v, None)
        assert c["verdict"] == "BULL"
        assert c["score"] > 0.5
        assert {s["state"] for s in c["signals"] if not s["available"]} == {"N/A"}

    def test_bear_bear_missing_news_is_bear(self):
        v = _verdict(lstm_score=-0.8, technical=-0.65, news_available=False)
        c = committee_signals(v, None)
        assert c["verdict"] == "BEAR"
        assert c["score"] < -0.5

    def test_conflict_reduces_confidence(self):
        agreeing = committee_signals(_verdict(lstm_score=0.8, technical=0.6, news=0.4, news_available=True), None)
        conflicting = committee_signals(_verdict(lstm_score=0.8, technical=-0.6, news=0.4, news_available=True), None)
        assert conflicting["verdict"] == "BULL"
        assert conflicting["confidence"] < agreeing["confidence"]
        assert any("disagree" in reason.lower() for reason in conflicting["why"])

    def test_all_signals_unavailable_is_explicit(self):
        c = committee_signals({"lstm": {}, "technical": {}, "news_available": False}, None)
        assert c["verdict"] == "N/A"
        assert c["score"] is None
        assert c["confidence"] is None


class TestBullBearFactors:
    def test_bull_case_from_real_signals(self):
        v = _verdict(lstm_score=0.8, news_available=True, news=0.4)
        f = bull_bear_factors(v, INST_BULLS)
        joined = " ".join(f["bull"]).lower()
        assert "lstm predicts upside" in joined
        assert "momentum" in joined
        assert "50-day ma" in joined
        assert "uptrend" in joined
        assert "news sentiment" in joined
        assert "funds building" in joined

    def test_bear_case(self):
        v = _verdict(
            lstm_score=-0.8,
            news_available=True,
            news=-0.4,
            reality={"momentum_20": -0.08, "rsi_14": 78.0, "above_sma_50": False, "trend_50_200": -0.02},
        )
        f = bull_bear_factors(v, {"holding_funds": 2, "buy_count": 0, "sell_count": 2, "net": -2})
        joined = " ".join(f["bear"]).lower()
        assert "lstm predicts downside" in joined
        assert "momentum" in joined
        assert "overbought" in joined
        assert "below" in joined
        assert "downtrend" in joined
        assert "news sentiment" in joined
        assert "funds trimming" in joined

    def test_no_bull_factors_when_flat(self):
        v = _verdict(lstm_score=0.02, technical=0.0, news=0.0, news_available=False, reality={})
        f = bull_bear_factors(v, None)
        assert f["bull"] == []
        assert f["bear"] == []

    def test_rsi_oversold_is_bullish(self):
        v = _verdict(reality={"momentum_20": 0.0, "rsi_14": 25.0, "above_sma_50": None, "trend_50_200": 0.0})
        f = bull_bear_factors(v, None)
        assert any("oversold" in x for x in f["bull"])

    def test_never_fabricates_valuation(self):
        v = _verdict(lstm_score=0.8)
        f = bull_bear_factors(v, None)
        joined = " ".join(f["bull"] + f["bear"]).lower()
        assert "valuation" not in joined
        assert "p/e" not in joined
