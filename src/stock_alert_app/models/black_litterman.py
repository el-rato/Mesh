from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf

from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class BLPortfolio:
    tickers: list[str]
    weights: np.ndarray
    expected_returns: np.ndarray
    cov_matrix: np.ndarray
    risk_aversion: float = 3.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tickers": self.tickers,
            "weights": {t: round(float(w), 6) for t, w in zip(self.tickers, self.weights)},
            "expected_returns": {t: round(float(r), 6) for t, r in zip(self.tickers, self.expected_returns)},
            "risk_aversion": self.risk_aversion,
        }


@dataclass
class BLRiskMetrics:
    portfolio_var: float
    portfolio_vol: float
    sharpe_ratio: float
    max_drawdown_est: float
    var_95: float
    cvar_95: float

    def as_dict(self) -> dict[str, float]:
        return {
            "portfolio_var": round(self.portfolio_var, 6),
            "portfolio_vol": round(self.portfolio_vol, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown_est": round(self.max_drawdown_est, 6),
            "var_95": round(self.var_95, 6),
            "cvar_95": round(self.cvar_95, 6),
        }


def _fetch_price_history(tickers: list[str], period: str = "2y") -> tuple[np.ndarray | None, list[str]]:
    """Fetch adjusted close prices for tickers. Returns (n_days, n_tickers) array and valid tickers."""
    try:
        data = yf.download(tickers, period=period, interval="1d", auto_adjust=True, progress=False)
        if data is None or data.empty:
            return None, []
        # yfinance returns MultiIndex columns (OHLCV, ticker) for multiple tickers
        if isinstance(data.columns, pd.MultiIndex):
            if 'Close' in data.columns.get_level_values(0):
                closes = data['Close']
            else:
                return None, []
        else:
            closes = data
        if closes.empty:
            return None, []
        closes = closes.dropna(axis=1, how="all")
        valid_tickers = list(closes.columns)
        return closes.values.astype(np.float32), valid_tickers
    except Exception as exc:
        logger.warning("BL price fetch failed: %s", exc)
        return None, []


def _compute_returns(prices: np.ndarray) -> np.ndarray:
    """Compute daily log returns from prices."""
    return np.diff(np.log(prices), axis=0)


def _shrink_covariance(cov: np.ndarray, delta: float = 0.5) -> np.ndarray:
    """Ledoit-Wolf style shrinkage toward diagonal."""
    n = cov.shape[0]
    var = np.diag(cov)
    shrunk = delta * cov + (1 - delta) * np.diag(var)
    return shrunk


def black_litterman_optimize(
    tickers: list[str],
    views: dict[int, float] | None = None,
    view_confidences: dict[int, float] | None = None,
    risk_aversion: float = 3.0,
    period: str = "2y",
    market_caps: dict[str, float] | None = None,
) -> tuple[BLPortfolio, BLRiskMetrics] | None:
    """Black-Litterman portfolio optimization.

    Args:
        tickers: List of ticker symbols
        views: Dict of {asset_index: expected_return} for investor views
        view_confidences: Dict of {asset_index: confidence (0-1)} for views
        risk_aversion: Risk aversion parameter (lambda)
        period: Lookback period for historical data
        market_caps: Optional market cap weights for equilibrium
    """
    prices, valid_tickers = _fetch_price_history(tickers, period)
    if prices is None or prices.shape[0] < 30 or len(valid_tickers) < 2:
        logger.warning("Insufficient price data for BL optimization")
        return None

    returns = _compute_returns(prices)
    n = len(valid_tickers)

    mu = np.mean(returns, axis=0)
    cov = np.cov(returns.T)
    cov = _shrink_covariance(cov)

    if market_caps:
        total_cap = sum(market_caps.get(t, 0) for t in valid_tickers)
        if total_cap > 0:
            w_market = np.array([market_caps.get(t, 0) / total_cap for t in valid_tickers])
        else:
            w_market = np.ones(n) / n
    else:
        w_market = np.ones(n) / n

    pi = risk_aversion * cov @ w_market

    if views:
        k = len(views)
        P = np.zeros((k, n))
        Q = np.zeros(k)
        omega_diag = []
        for i, (asset_idx, view_return) in enumerate(views.items()):
            if 0 <= asset_idx < n:
                P[i, asset_idx] = 1.0
                Q[i] = view_return
                conf = view_confidences.get(asset_idx, 0.5) if view_confidences else 0.5
                omega_diag.append((1 - conf) / max(conf, 0.01) * cov[asset_idx, asset_idx])
        if k > 0:
            Omega = np.diag(omega_diag)
            tau = 1.0 / len(returns)
            M1 = np.linalg.inv(tau * cov)
            M2 = P.T @ np.linalg.inv(Omega) @ P
            M3 = np.linalg.inv(tau * cov) @ pi
            M4 = P.T @ np.linalg.inv(Omega) @ Q
            mu_bl = np.linalg.inv(M1 + M2) @ (M3 + M4)
            cov_bl = np.linalg.inv(M1 + M2)
        else:
            mu_bl = pi
            cov_bl = cov
    else:
        mu_bl = pi
        cov_bl = cov

    inv_cov = np.linalg.inv(cov_bl)
    ones = np.ones(n)
    w_opt = (inv_cov @ mu_bl) / (ones @ inv_cov @ mu_bl)
    w_opt = np.maximum(w_opt, 0)
    w_opt = w_opt / w_opt.sum() if w_opt.sum() > 0 else w_market

    port_ret = w_opt @ mu_bl
    port_vol = np.sqrt(w_opt @ cov_bl @ w_opt)
    sharpe = port_ret / port_vol if port_vol > 0 else 0

    port_returns = returns @ w_opt
    var_95 = -np.percentile(port_returns, 5)
    cvar_95 = -port_returns[port_returns <= -var_95].mean() if np.any(port_returns <= -var_95) else var_95
    cum_returns = np.cumprod(1 + port_returns)
    running_max = np.maximum.accumulate(cum_returns)
    drawdown = (cum_returns - running_max) / running_max
    max_dd = drawdown.min()

    portfolio = BLPortfolio(
        tickers=valid_tickers,
        weights=w_opt,
        expected_returns=mu_bl,
        cov_matrix=cov_bl,
        risk_aversion=risk_aversion,
    )
    risk = BLRiskMetrics(
        portfolio_var=float(port_ret),
        portfolio_vol=float(port_vol),
        sharpe_ratio=float(sharpe),
        max_drawdown_est=float(max_dd),
        var_95=float(var_95),
        cvar_95=float(cvar_95),
    )
    return portfolio, risk


def risk_analyze_watchlist(
    tickers: list[str],
    period: str = "2y",
    risk_aversion: float = 3.0,
) -> dict[str, Any] | None:
    """Full risk analysis for a watchlist using Black-Litterman."""
    result = black_litterman_optimize(
        tickers=tickers,
        risk_aversion=risk_aversion,
        period=period,
    )
    if result is None:
        return None
    portfolio, risk = result
    return {
        "portfolio": portfolio.as_dict(),
        "risk_metrics": risk.as_dict(),
        "recommendation": "increase_exposure" if risk.sharpe_ratio > 1.0 else "reduce_exposure" if risk.sharpe_ratio < 0.3 else "hold",
    }