from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .lstm import LSTMPrediction, batch_predict_lstm
from .black_litterman import BLPortfolio, BLRiskMetrics, black_litterman_optimize, risk_analyze_watchlist

logger = logging.getLogger(__name__)


@dataclass
class RiskAnalysis:
    ticker: str
    lstm_signal: str | None
    lstm_predicted_return: float | None
    lstm_confidence: float | None
    bl_weight: float | None
    bl_expected_return: float | None
    portfolio_sharpe: float | None
    portfolio_vol: float | None
    var_95: float | None
    risk_level: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "lstm": {
                "signal": self.lstm_signal,
                "predicted_return": self.lstm_predicted_return,
                "confidence": self.lstm_confidence,
            } if self.lstm_signal else None,
            "black_litterman": {
                "weight": self.bl_weight,
                "expected_return": self.bl_expected_return,
            } if self.bl_weight is not None else None,
            "portfolio_metrics": {
                "sharpe": self.portfolio_sharpe,
                "volatility": self.portfolio_vol,
                "var_95": self.var_95,
            } if self.portfolio_sharpe is not None else None,
            "risk_level": self.risk_level,
        }


def _risk_level_from_metrics(sharpe: float | None, vol: float | None, var95: float | None) -> str:
    if sharpe is None:
        return "UNKNOWN"
    if sharpe > 1.5 and (vol is None or vol < 0.02):
        return "LOW"
    if sharpe > 0.8:
        return "MODERATE"
    if sharpe > 0.3:
        return "ELEVATED"
    return "HIGH"


def run_risk_analysis(
    tickers: list[str],
    market: str = "NYSE",
    period: str = "2y",
    risk_aversion: float = 3.0,
) -> list[RiskAnalysis]:
    """Run combined LSTM + Black-Litterman risk analysis for a list of tickers."""
    results: list[RiskAnalysis] = []

    lstm_preds = batch_predict_lstm(tickers, period=period)

    bl_result = black_litterman_optimize(
        tickers=tickers,
        risk_aversion=risk_aversion,
        period=period,
    )
    bl_portfolio: BLPortfolio | None = bl_result[0] if bl_result else None
    bl_risk: BLRiskMetrics | None = bl_result[1] if bl_result else None

    for ticker in tickers:
        lstm = lstm_preds.get(ticker)
        bl_w = bl_portfolio.weights[bl_portfolio.tickers.index(ticker)] if bl_portfolio and ticker in bl_portfolio.tickers else None
        bl_ret = bl_portfolio.expected_returns[bl_portfolio.tickers.index(ticker)] if bl_portfolio and ticker in bl_portfolio.tickers else None

        risk_level = _risk_level_from_metrics(
            bl_risk.sharpe_ratio if bl_risk else None,
            bl_risk.portfolio_vol if bl_risk else None,
            bl_risk.var_95 if bl_risk else None,
        )

        results.append(RiskAnalysis(
            ticker=ticker,
            lstm_signal=lstm.signal if lstm else None,
            lstm_predicted_return=lstm.predicted_return if lstm else None,
            lstm_confidence=lstm.confidence if lstm else None,
            bl_weight=bl_w,
            bl_expected_return=bl_ret,
            portfolio_sharpe=bl_risk.sharpe_ratio if bl_risk else None,
            portfolio_vol=bl_risk.portfolio_vol if bl_risk else None,
            var_95=bl_risk.var_95 if bl_risk else None,
            risk_level=risk_level,
        ))
    return results


def run_portfolio_risk_analysis(
    tickers: list[str],
    period: str = "2y",
    risk_aversion: float = 3.0,
) -> dict[str, Any] | None:
    """Run portfolio-level risk analysis (Black-Litterman optimization)."""
    return risk_analyze_watchlist(tickers, period, risk_aversion)