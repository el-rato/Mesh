from __future__ import annotations

import pytest

from stock_alert_app import signals
from stock_alert_app.dossier import committee_signals


def _res(name, score=None, conf=None, status="ok"):
    return signals.SignalResult(
        model_name=name,
        direction=signals.direction_of(score),
        score=score,
        confidence=conf,
        status=status,
        analyzed_at="2026-01-01T00:00:00Z",
    )


class TestQuantitativeEnsemble:
    def test_all_models_available(self, monkeypatch):
        monkeypatch.setattr(signals, "lstm_signal", lambda s: _res("lstm", 0.6, 0.8))
        monkeypatch.setattr(signals, "gbm_signal", lambda df: _res("gbm", 0.4, 0.6))
        monkeypatch.setattr(signals, "momentum_signal", lambda p: _res("momentum", 0.2, 0.5))
        ens, models = signals.quantitative_ensemble("T")
        assert ens.status == "ok"
        assert ens.direction == "BULL"
        # weighted: (0.6*0.8*0.4 + 0.4*0.6*0.3 + 0.2*0.5*0.3)/(0.8*0.4+0.6*0.3+0.5*0.3)
        assert 0.3 < ens.score < 0.5
        assert len(models) == 3

    def test_lstm_unavailable_others_continue(self, monkeypatch):
        monkeypatch.setattr(signals, "lstm_signal", lambda s: _res("lstm", status="no_data"))
        monkeypatch.setattr(signals, "gbm_signal", lambda df: _res("gbm", 0.4, 0.6))
        monkeypatch.setattr(signals, "momentum_signal", lambda p: _res("momentum", 0.2, 0.5))
        ens, models = signals.quantitative_ensemble("T")
        assert ens.status == "ok"  # ensemble survives without LSTM
        assert ens.direction == "BULL"
        assert any(m.model_name == "lstm" and m.status == "no_data" for m in models)

    def test_tree_model_unavailable(self, monkeypatch):
        monkeypatch.setattr(signals, "lstm_signal", lambda s: _res("lstm", 0.6, 0.8))
        monkeypatch.setattr(signals, "gbm_signal", lambda df: _res("gbm", status="error"))
        monkeypatch.setattr(signals, "momentum_signal", lambda p: _res("momentum", 0.2, 0.5))
        ens, models = signals.quantitative_ensemble("T")
        assert ens.status == "ok"

    def test_all_unavailable_is_no_data(self, monkeypatch):
        monkeypatch.setattr(signals, "lstm_signal", lambda s: _res("lstm", status="no_data"))
        monkeypatch.setattr(signals, "gbm_signal", lambda df: _res("gbm", status="no_data"))
        monkeypatch.setattr(signals, "momentum_signal", lambda p: _res("momentum", status="error"))
        ens, _ = signals.quantitative_ensemble("T")
        assert ens.status == "no_data"
        assert ens.score is None
        assert ens.direction is None

    def test_models_disagree_reduces_direction(self, monkeypatch):
        monkeypatch.setattr(signals, "lstm_signal", lambda s: _res("lstm", 0.8, 0.9))
        monkeypatch.setattr(signals, "gbm_signal", lambda df: _res("gbm", -0.8, 0.9))
        monkeypatch.setattr(signals, "momentum_signal", lambda p: _res("momentum", -0.4, 0.8))
        ens, _ = signals.quantitative_ensemble("T")
        # both BULL and BEAR present -> ensemble leans to a lower-magnitude score
        assert abs(ens.score) < 0.8


class TestSocialMomentum:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        signals.clear_social_cache()
        yield
        signals.clear_social_cache()

    def test_no_data_when_unconfigured(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="", reddit_client_secret="", social_cache_ttl=3600),
        )
        result = signals.social_momentum_signal("AAPL")
        assert result.status == "no_data"
        assert result.score is None

    def test_strong_sentiment_change(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=3600),
        )

        class FakeSub:
            def search(self, query, sort="new", time_filter="week", limit=25):
                import time as _t

                now = _t.time()
                return [
                    _Submission(now - 3600, 100, 0.6, "wallstreetbets"),
                    _Submission(now - 7200, 90, 0.5, "stocks"),
                    _Submission(now - 50000, 10, -0.4, "investing"),
                    _Submission(now - 60000, 5, -0.3, "stocks"),
                ]

        class _Submission:
            def __init__(self, created, score, sentiment, subreddit):
                self.created_utc = created
                self.score = score
                self.num_comments = 3
                self.title = "AAPL"
                self.selftext = ""
                self.subreddit = subreddit

            def __iter__(self):
                return iter([])

        class FakeReddit:
            def subreddit(self, name):
                return FakeSub()

        class FakeScanner:
            def _get_reddit(self):
                return FakeReddit()

            def _score_sentiment(self, text):
                # derive sentiment from the submission list we pass through search
                return 0.5, "positive"

        monkeypatch.setattr(
            "stock_alert_app.reddit_scanner.RedditScanner", lambda: FakeScanner()
        )
        result = signals.social_momentum_signal("AAPL", "Apple Inc.")
        assert result.status == "ok"
        assert result.score is not None
        assert -1.0 <= result.score <= 1.0
        assert result.direction is not None

    def test_valid_result_cached(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=3600),
        )
        calls = {"n": 0}

        def fake_impl(ticker, company=""):
            calls["n"] += 1
            return _res("social_momentum", 0.5, 0.6)

        monkeypatch.setattr(signals, "_social_momentum_impl", fake_impl)
        signals.social_momentum_signal("AAPL")
        signals.social_momentum_signal("AAPL")
        assert calls["n"] == 1  # second call served from cache

    def test_expired_result_recomputes(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=0),
        )
        calls = {"n": 0}

        def fake_impl(ticker, company=""):
            calls["n"] += 1
            return _res("social_momentum", 0.5, 0.6)

        monkeypatch.setattr(signals, "_social_momentum_impl", fake_impl)
        signals.social_momentum_signal("AAPL")
        signals.social_momentum_signal("AAPL")
        assert calls["n"] == 2  # TTL 0 -> always recompute

    def test_no_data_cached_not_neutral(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=3600),
        )
        calls = {"n": 0}

        def fake_impl(ticker, company=""):
            calls["n"] += 1
            return _res("social_momentum", status="no_data")

        monkeypatch.setattr(signals, "_social_momentum_impl", fake_impl)
        r1 = signals.social_momentum_signal("AAPL")
        r2 = signals.social_momentum_signal("AAPL")
        assert calls["n"] == 1  # NO_DATA cached
        assert r1.status == "no_data" and r2.status == "no_data"
        assert r1.direction is None  # not coerced to neutral

    def test_error_not_cached(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=3600),
        )
        calls = {"n": 0}

        def fake_impl(ticker, company=""):
            calls["n"] += 1
            return _res("social_momentum", status="error")

        monkeypatch.setattr(signals, "_social_momentum_impl", fake_impl)
        signals.social_momentum_signal("AAPL")
        signals.social_momentum_signal("AAPL")
        assert calls["n"] == 2  # transient ERROR is retried, not cached as valid

    def test_concurrent_calls_do_not_duplicate(self, monkeypatch):
        import threading
        import time as _t
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=3600),
        )
        calls = {"n": 0}

        def fake_impl(ticker, company=""):
            calls["n"] += 1
            _t.sleep(0.05)
            return _res("social_momentum", 0.5, 0.6)

        monkeypatch.setattr(signals, "_social_momentum_impl", fake_impl)
        barrier = threading.Barrier(2)
        results = []

        def run():
            barrier.wait()
            results.append(signals.social_momentum_signal("AAPL"))

        t1 = threading.Thread(target=run)
        t2 = threading.Thread(target=run)
        t1.start(); t2.start(); t1.join(); t2.join()
        assert calls["n"] == 1  # in-flight guard prevented duplicate fetch
        assert len(results) == 2
        assert results[0].status == results[1].status == "ok"

    def test_cache_is_bounded(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr(
            signals,
            "settings",
            SimpleNamespace(reddit_client_id="id", reddit_client_secret="secret", social_cache_ttl=3600),
        )
        monkeypatch.setattr(
            signals, "_social_momentum_impl", lambda t, c="": _res("social_momentum", 0.5, 0.6)
        )
        for i in range(signals._SOCIAL_MAX + 50):
            signals.social_momentum_signal(f"TK{i}")
        assert len(signals._social_cache) <= signals._SOCIAL_MAX  # bounded, safe eviction
        signals.clear_social_cache()


class TestCommitteeIntegration:
    def test_social_and_quant_in_committee(self):
        v = {
            "quantitative": {"status": "ok", "score": 0.6, "confidence": 0.8, "model_name": "quantitative_ensemble"},
            "models": [{"model_name": "lstm", "status": "ok", "score": 0.6, "confidence": 0.8}],
            "technical": {"score": 0.2},
            "news_available": True,
            "news_score": 0.3,
            "news": {"score": 0.3, "article_count": 5},
            "social": {"status": "ok", "score": 0.5, "confidence": 0.6, "model_name": "social_momentum"},
            "market_regime": {"status": "no_data"},
            "price": {},
        }
        c = committee_signals(v)
        by = {s["key"]: s for s in c["signals"]}
        assert by["quant"]["state"] == "BULL"
        assert by["social"]["state"] == "BULL"
        assert by["regime"]["state"] == "N/A"
        assert c["verdict"] == "BULL"

    def test_missing_signal_excluded_not_neutral(self):
        v = {
            "quantitative": {"status": "ok", "score": 0.8, "confidence": 0.9, "model_name": "quantitative_ensemble"},
            "technical": {"score": 0.6, "confidence": 0.7},
            "news_available": False,
            "social": {"status": "no_data"},
            "market_regime": {"status": "no_data"},
            "price": {},
        }
        c = committee_signals(v)
        by = {s["key"]: s for s in c["signals"]}
        assert by["quant"]["state"] == "BULL"
        assert by["technical"]["state"] == "BULL"
        assert by["news"]["state"] == "N/A"
        assert by["social"]["state"] == "N/A"
        assert c["verdict"] == "BULL"
        assert c["confidence"] is not None
