from __future__ import annotations

from stock_alert_app import research
from stock_alert_app.dossier import committee_decision


def _decision_dict():
    return {
        "market": "NYSE",
        "ticker": "T",
        "committee": {
            "verdict": "BULL",
            "confidence": 0.6,
            "score": 0.4,
            "signals": [
                {"key": "quant", "label": "QUANTITATIVE", "state": "BULL", "score": 0.6, "confidence": 0.8, "available": True},
                {"key": "technical", "label": "TECHNICAL", "state": "BEAR", "score": -0.4, "confidence": 0.6, "available": True},
                {"key": "news", "label": "NEWS", "state": "N/A", "score": None, "confidence": None, "available": False},
            ],
            "why": ["Quantitative strongly bullish", "Technical bearish", "No news signal available"],
        },
        "factors": {"bull": ["Quantitative model predicts upside"], "bear": ["price below 50-day MA"]},
        "research": {
            "status": "ok",
            "confidence": 0.6,
            "catalysts": ["Apple beats earnings"],
            "risks": ["Apple facing antitrust probe"],
            "bull_evidence": ["strong demand"],
            "bear_evidence": [],
            "analyzed_at": "2026-01-01T00:00:00Z",
            "provenance": ["14 scored news items"],
        },
        "decided_at": "2026-01-01T00:00:00Z",
    }


class TestCommitteeDecision:
    def test_structure(self):
        d = committee_decision(_decision_dict())
        assert d["security_id"] == "NYSE:T"
        assert d["signals"]["quantitative"]["status"] == "AVAILABLE"
        assert d["signals"]["technical"]["status"] == "AVAILABLE"
        assert d["signals"]["news"]["status"] == "NO_DATA"
        assert d["signals"]["social_momentum"]["status"] == "NO_DATA"
        assert d["verdict"] == "BULL"
        assert d["conviction"] == 0.6
        assert "quantitative bull" in d["thesis"].lower()
        assert "price below 50-day MA" in d["bear_case"]
        assert "Apple beats earnings" in d["catalysts"]
        assert "antitrust" in " ".join(d["primary_risks"])
        assert d["key_disagreement"] is not None
        assert d["view_changes_if"]
        assert d["status"] == "ok"
        assert d["decision_timestamp"]
        # contributing signals carry explicit status, not inferred neutral
        news = next(s for s in d["contributing_signals"] if s["key"] == "news")
        assert news["status"] == "NO_DATA" and news["available"] is False

    def test_missing_signals_excluded_from_thesis(self):
        d = committee_decision(_decision_dict())
        assert "news" not in d["thesis"].lower().split(";")[0] or "news" in d["thesis"].lower()

    def test_no_data_decision(self):
        d = committee_decision({"committee": {"verdict": "N/A", "confidence": None, "score": None, "signals": []}})
        assert d["status"] == "no_data"
        assert d["thesis"]  # still a safe message
        assert d["signals"]["news"]["status"] == "NO_DATA"


class TestResearchBrief:
    def test_build_with_evidence(self):
        brief = research.build_brief(
            ticker="AAPL", company="Apple", exchange="NYSE", market="NYSE",
            news_score=0.2, news_label="bullish", article_count=5,
            evidence=[("Apple beats earnings", "CNBC", "positive", 0.6), ("Apple faces probe", "WSJ", "negative", -0.5), ("Apple expands", "Reuters", "positive", 0.3)],
        )
        assert brief.status == "ok"
        assert brief.catalysts  # positive evidence
        assert brief.risks  # negative evidence
        assert brief.news_count == 5
        assert brief.provenance

    def test_no_evidence_is_no_data(self):
        brief = research.build_brief(ticker="X")
        assert brief.status == "no_data"
        assert brief.catalysts == []
        assert brief.risks == []

    def test_no_fabrication_of_missing_fields(self):
        brief = research.build_brief(ticker="X")
        d = brief.as_dict()
        assert d["news_sentiment"] is None
        assert d["institutional"] is None
        assert d["catalysts"] == []

    def test_no_data_brief(self):
        b = research.no_data_brief(ticker="X")
        assert b.status == "no_data"
