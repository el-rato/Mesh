from __future__ import annotations

import math

import pytest

from stock_alert_app.analysis import _news_available_from_reason
from stock_alert_app.dossier import committee_signals
from stock_alert_app.sentiment.aggregate import SourceSentiment
from stock_alert_app.sentiment.scorers import LexiconScorer, default_scorer
from stock_alert_app.verdict import build_verdict


def _news(score: float) -> SourceSentiment:
    label = "bullish" if score > 0.15 else "bearish" if score < -0.15 else "neutral"
    return SourceSentiment(
        score=score,
        label=label,
        article_count=14,
        positive_count=max(0, int(score > 0) * 10),
        negative_count=max(0, int(score < 0) * 9),
        neutral_count=4,
        avg_confidence=0.72,
        freshness=0.9,
    )


def _run(monkeypatch, sentiment):
    from stock_alert_app.models import price_lstm

    from stock_alert_app import verdict as verdict_module

    monkeypatch.setattr(price_lstm, "predict_price_lstm", lambda sym: None)
    return verdict_module.build_verdict(
        market="NYSE", ticker="T", sentiment=sentiment, price=None, yahoo_symbol="T"
    )


class TestNewsReasonParsing:
    def test_new_format_available(self):
        assert _news_available_from_reason("... News: bearish (100 articles, score -0.21); ...") is True

    def test_legacy_format_available(self):
        assert _news_available_from_reason("... Auxiliary News Sentiment: bullish (+0.154); ...") is True

    def test_new_format_unavailable(self):
        assert _news_available_from_reason("... News: unavailable ...") is False

    def test_legacy_none_unavailable(self):
        assert _news_available_from_reason("... Auxiliary News Sentiment: None (+0.000); ...") is False

    def test_absent_is_unavailable(self):
        assert _news_available_from_reason("LSTM Model only") is False


class TestScorerFallback:
    def test_lexicon_fallback_when_models_unavailable(self, monkeypatch):
        def raise_runtime():
            raise RuntimeError("no model")

        monkeypatch.setattr("stock_alert_app.sentiment.scorers.LSTMSentimentScorer", raise_runtime)
        monkeypatch.setattr("stock_alert_app.sentiment.scorers.FinBERTScorer", raise_runtime)
        scorer = default_scorer()
        assert isinstance(scorer, LexiconScorer)

    def test_lexicon_positive(self):
        result = LexiconScorer().score("Earnings beat estimates; revenue surges; stock rallies to record high.")
        assert result.score > 0.15
        assert result.label == "positive"

    def test_lexicon_negative(self):
        result = LexiconScorer().score("Company plunges on missed earnings; shares crash after downgrade.")
        assert result.score < -0.15
        assert result.label == "negative"


class TestNewsScenarios:
    def test_positive_news_is_bull(self, monkeypatch):
        v = _run(monkeypatch, _news(0.6))
        assert v.news_available is True
        assert v.news_score > 0.15
        committee = committee_signals(v.as_dict(), None)
        news = next(s for s in committee["signals"] if s["key"] == "news")
        assert news["state"] == "BULL"
        assert news["article_count"] == 14
        assert news["confidence"] is not None

    def test_negative_news_is_bear(self, monkeypatch):
        v = _run(monkeypatch, _news(-0.5))
        assert v.news_available is True
        committee = committee_signals(v.as_dict(), None)
        news = next(s for s in committee["signals"] if s["key"] == "news")
        assert news["state"] == "BEAR"

    def test_mixed_news_is_neutral(self, monkeypatch):
        v = _run(monkeypatch, _news(0.0))
        assert v.news_available is True  # scored, just neutral
        committee = committee_signals(v.as_dict(), None)
        news = next(s for s in committee["signals"] if s["key"] == "news")
        assert news["state"] == "NEUTRAL"
        assert news["available"] is True

    def test_no_news_is_na(self, monkeypatch):
        v = _run(monkeypatch, None)
        assert v.news_available is False
        committee = committee_signals(v.as_dict(), None)
        news = next(s for s in committee["signals"] if s["key"] == "news")
        assert news["state"] == "N/A"

    def test_na_news_excluded_from_denominator(self, monkeypatch):
        # With news N/A, the committee still produces a verdict from the other
        # signals instead of treating the missing news as a neutral vote.
        v = _run(monkeypatch, None)
        committee = committee_signals(v.as_dict(), None)
        assert committee["verdict"] in ("BULL", "BEAR", "NEUTRAL", "N/A")
        news = next(s for s in committee["signals"] if s["key"] == "news")
        assert news["contribution"] is None

    def test_no_nan_leaks(self, monkeypatch):
        v = _run(monkeypatch, _news(0.3))
        for field in (v.news_score, v.technical_score, v.combined_score, v.confidence):
            assert math.isfinite(field)
