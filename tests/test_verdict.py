from __future__ import annotations

import math

import pytest

from stock_alert_app.models.price_lstm import LSTMResult
from stock_alert_app.price import PriceState
from stock_alert_app.sentiment.aggregate import SourceSentiment
from stock_alert_app.verdict import (
    _agreement_label,
    _decide,
    _verdict_confidence,
    combine_signals,
    normalize_lstm_signal,
    normalize_news_score,
)


def _lstm(
    prob_up: float | None = 0.55, ret: float | None = 0.01, signal: str = "BULL"
) -> LSTMResult:
    return LSTMResult(
        ticker="TEST",
        predicted_return=ret or 0.0,
        probability_up=prob_up or 0.0,
        confidence=0.7,
        signal=signal,
    )


def _price(
    momentum: float = 0.05,
    rsi: float = 55.0,
    above_sma: bool = True,
    trend: float = 0.03,
) -> PriceState:
    return PriceState(
        market="NYSE",
        ticker="TEST",
        close=100.0,
        open=99.0,
        high=101.0,
        low=98.5,
        volume=1_000_000,
        momentum_20=momentum,
        rsi_14=rsi,
        sma_50=98.0,
        sma_200=95.0,
        trend_50_200=trend,
        price_above_sma_50=above_sma,
    )


def _news(score: float) -> SourceSentiment:
    label = "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral"
    return SourceSentiment(
        score=score,
        label=label,
        article_count=10,
        positive_count=7,
        negative_count=2,
        neutral_count=1,
        avg_confidence=0.8,
    )


class TestNormalizeLstmSignal:
    def test_probability_up_maps_linear(self):
        # 70% -> +0.40, 30% -> -0.40, 50% -> 0.0 (the intended two-delta mapping)
        assert normalize_lstm_signal(_lstm(0.70))[0] == pytest.approx(0.40)
        assert normalize_lstm_signal(_lstm(0.30))[0] == pytest.approx(-0.40)
        assert normalize_lstm_signal(_lstm(0.50))[0] == pytest.approx(0.0)

    def test_clamped_to_unit(self):
        assert normalize_lstm_signal(_lstm(1.0))[0] == pytest.approx(1.0)
        assert normalize_lstm_signal(_lstm(0.0))[0] == pytest.approx(-1.0)

    def test_nan_probability_falls_back_to_return(self):
        res = _lstm(prob_up=float("nan"), ret=0.03)
        score, source = normalize_lstm_signal(res)
        assert source == "predicted_return"
        assert 0.0 < score <= 1.0

    def test_none_returns_zero(self):
        assert normalize_lstm_signal(None) == (0.0, None)

    def test_signal_fallback(self):
        res = LSTMResult(
            ticker="TEST",
            predicted_return=None,
            probability_up=None,
            confidence=0.7,
            signal="BEAR",
        )
        score, source = normalize_lstm_signal(res)
        assert source == "signal"
        assert score == -1.0


class TestNormalizeNewsScore:
    def test_unavailable_flag(self):
        assert normalize_news_score(None) == (0.0, False)

    def test_available(self):
        score, available = normalize_news_score(_news(0.3))
        assert available is True
        assert score == pytest.approx(0.3)


class TestCombineSignals:
    def test_full_signal_weighted_sum(self):
        # 0.6*0.8 + 0.25*0.6 + 0.15*0.4 = 0.69
        c = combine_signals(0.8, 0.6, 0.4, True, True, True)
        assert c == pytest.approx(0.69)

    def test_missing_news_renormalized(self):
        # (0.6*0.8 + 0.25*0.6) / 0.85 = 0.7412
        c = combine_signals(0.8, 0.6, 0.0, True, True, False)
        assert c == pytest.approx(0.74117647)

    def test_missing_all(self):
        assert combine_signals(0.0, 0.0, 0.0, False, False, False) == 0.0


class TestDecide:
    def test_bull_threshold(self):
        verdict, _ = _decide(0.26)
        assert verdict == "BULL"

    def test_bear_threshold(self):
        verdict, _ = _decide(-0.26)
        assert verdict == "BEAR"

    def test_neutral_band(self):
        assert _decide(0.0)[0] == "NEUTRAL"
        assert _decide(0.24)[0] == "NEUTRAL"
        assert _decide(-0.24)[0] == "NEUTRAL"


class TestVerdictConfidence:
    def test_strong_bullish_agreement_is_high(self):
        c = _verdict_confidence(0.69, 0.8, 0.6, 0.4, True, True, True)
        assert c > 0.7

    def test_strong_bearish_agreement_is_high(self):
        c = _verdict_confidence(-0.6, -0.8, -0.6, -0.4, True, True, True)
        assert c > 0.6

    def test_conflicting_signals_reduce_confidence(self):
        agreed = _verdict_confidence(0.69, 0.8, 0.6, 0.4, True, True, True)
        conflicted = _verdict_confidence(0.295, 0.8, -0.5, -0.4, True, True, True)
        assert conflicted < agreed
        assert conflicted < 0.5

    def test_neutral_combined_is_zero(self):
        assert _verdict_confidence(0.0, 0.0, 0.0, 0.0, True, True, True) == 0.0

    def test_single_available_signal_capped_by_availability(self):
        c = _verdict_confidence(0.7, 0.7, 0.0, 0.0, True, False, False)
        assert c < 0.7


class TestAgreementLabel:
    def test_strong(self):
        assert _agreement_label(3, 0, True) == "strong"

    def test_weak(self):
        assert _agreement_label(1, 2, True) == "weak"

    def test_none(self):
        assert _agreement_label(0, 0, False) == "none"


class TestBuildVerdict:
    def _run(self, monkeypatch, lstm=None, price=None, news=None):
        from stock_alert_app import verdict as verdict_module
        from stock_alert_app.models import price_lstm

        monkeypatch.setattr(price_lstm, "predict_price_lstm", lambda sym: lstm)
        return verdict_module.build_verdict(
            market="NYSE",
            ticker="TEST",
            sentiment=news,
            price=price,
            yahoo_symbol="TEST",
        )

    def test_strong_bullish_agreement(self, monkeypatch):
        v = self._run(
            monkeypatch,
            lstm=_lstm(0.90, 0.03),
            price=_price(momentum=0.10),
            news=_news(0.30),
        )
        assert v.verdict == "BULL"
        assert v.signal_agreement == "strong"
        assert v.lstm_score == pytest.approx(0.8, abs=0.01)
        assert v.technical_score > 0
        assert v.news_available is True
        assert v.confidence > 0.7
        # Final verdict confidence is separate from LSTM model confidence.
        assert v.lstm_confidence == pytest.approx(0.7)

    def test_strong_bearish_agreement(self, monkeypatch):
        v = self._run(
            monkeypatch,
            lstm=_lstm(0.10, -0.03, "BEAR"),
            price=_price(momentum=-0.10, rsi=40.0, above_sma=False, trend=-0.02),
            news=_news(-0.30),
        )
        assert v.verdict == "BEAR"
        assert v.confidence > 0.6

    def test_conflicting_signals(self, monkeypatch):
        v = self._run(
            monkeypatch,
            lstm=_lstm(0.90, 0.03),
            price=_price(momentum=-0.08, rsi=35.0, above_sma=False, trend=-0.02),
            news=_news(-0.30),
        )
        # LSTM strong but technical/news oppose -> lower than the agreeing case.
        agreeing = self._run(
            monkeypatch,
            lstm=_lstm(0.90, 0.03),
            price=_price(momentum=0.08),
            news=_news(0.30),
        )
        assert v.confidence < agreeing.confidence
        assert v.signal_agreement in ("mixed", "weak")

    def test_neutral(self, monkeypatch):
        v = self._run(
            monkeypatch, lstm=_lstm(0.50, 0.0, "NEUTRAL"), price=None, news=None
        )
        assert v.verdict == "NEUTRAL"
        assert v.confidence == 0.0

    def test_missing_news_still_valid(self, monkeypatch):
        v = self._run(monkeypatch, lstm=_lstm(0.65, 0.01), price=_price(), news=None)
        assert v.verdict == "BULL"
        assert v.news_available is False
        assert "News: unavailable" in v.reason

    def test_missing_lstm_falls_back_to_price_and_news(self, monkeypatch):
        v = self._run(
            monkeypatch, lstm=None, price=_price(momentum=0.08), news=_news(0.30)
        )
        assert "LSTM: unavailable" in v.reason
        assert v.verdict in ("BULL", "NEUTRAL")
        assert v.lstm_confidence is None
        assert isinstance(v.reason, str)

    def test_invalid_nan_model_output_uses_fallback(self, monkeypatch):
        v = self._run(
            monkeypatch,
            lstm=_lstm(prob_up=float("nan"), ret=float("nan"), signal="NEUTRAL"),
            price=_price(momentum=0.08),
            news=_news(0.30),
        )
        assert "LSTM: unavailable" in v.reason
        assert v.verdict in ("BULL", "NEUTRAL")

    def test_missing_price_and_news_with_lstm(self, monkeypatch):
        v = self._run(monkeypatch, lstm=_lstm(0.70, 0.02), price=None, news=None)
        assert v.verdict == "BULL"
        assert v.price_score == 0.0
        assert (
            "no price data" in " ".join(v.technical_reasons)
            or "News: unavailable" in v.reason
        )

    def test_no_nan_leaks(self, monkeypatch):
        v = self._run(
            monkeypatch, lstm=_lstm(0.70, 0.02), price=_price(), news=_news(0.2)
        )
        for field in (
            v.lstm_score,
            v.technical_score,
            v.news_score,
            v.combined_score,
            v.confidence,
        ):
            assert math.isfinite(field)
