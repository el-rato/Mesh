from __future__ import annotations

import hashlib
import json
import logging
import math
import pickle
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent / "price_lstm_checkpoints"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

#: A SINGLE shared model + scaler for the ENTIRE universe. Training one model
#: (instead of one checkpoint per ticker) is dramatically cheaper: a single load
#: per process, a single training pass over pooled data, and one set of weights
#: to maintain. Stationary features (returns/ratios/momentum) make a cross-asset
#: model viable, so per-ticker `.pt`/`.pkl` files are no longer created.
GLOBAL_MODEL_PATH = MODEL_DIR / "global_lstm_model.pt"
GLOBAL_SCALER_PATH = MODEL_DIR / "global_lstm_scaler.pkl"
GLOBAL_METADATA_PATH = MODEL_DIR / "global_lstm_metadata.json"

MODEL_VERSION = "3.0"
FEATURE_VERSION = "price-features-v2"
FORECAST_HORIZON = "1 trading day"
FEATURE_WARMUP = 30
MODEL_MAX_AGE_DAYS = 90
FEATURE_NAMES = (
    "intraday_return",
    "high_low_range",
    "volume_ratio_10d",
    "log_return_1d",
    "simple_return_1d",
    "momentum_5d",
    "momentum_10d",
    "momentum_20d",
    "momentum_30d",
    "volatility_10d",
    "volatility_20d",
)


def _model_version(horizon: int) -> str:
    return MODEL_VERSION if horizon == 1 else f"{MODEL_VERSION}-h{horizon}"


def _artifact_paths(horizon: int) -> tuple[Path, Path, Path]:
    """Return independent model/scaler/calibration artifacts per horizon."""
    if horizon == 1:
        return GLOBAL_MODEL_PATH, GLOBAL_SCALER_PATH, GLOBAL_METADATA_PATH
    suffix = "" if horizon == 1 else f"_{horizon}d"
    return (
        MODEL_DIR / f"global_lstm_model{suffix}.pt",
        MODEL_DIR / f"global_lstm_scaler{suffix}.pkl",
        MODEL_DIR / f"global_lstm_metadata{suffix}.json",
    )

_global_lock = threading.Lock()
_global_model: "PriceLSTM | None" = None
_global_scaler: "RobustStandardScaler | None" = None
_global_metadata: dict[str, Any] | None = None


@dataclass
class LSTMResult:
    ticker: str
    predicted_return: float
    probability_up: float
    confidence: float
    signal: str
    mse: float = 0.0
    mae: float = 0.0
    directional_accuracy: float = 0.0
    model_version: str = MODEL_VERSION
    forecast_horizon: str = FORECAST_HORIZON
    as_of: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "predicted_return": round(self.predicted_return, 6),
            "probability_up": round(self.probability_up, 4),
            "confidence": round(self.confidence, 4),
            "signal": self.signal,
            "metrics": {
                "mse": round(self.mse, 6),
                "mae": round(self.mae, 6),
                "directional_accuracy": round(self.directional_accuracy, 4),
            },
            "model_version": self.model_version,
            "forecast_horizon": self.forecast_horizon,
            "as_of": self.as_of,
        }


class PriceLSTM(nn.Module):
    def __init__(
        self,
        input_size: int = 6,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size,
            hidden_size,
            num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0,
        )
        self.fc = nn.Linear(hidden_size, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        out = self.dropout(out[:, -1, :])
        return self.fc(out)


class RobustStandardScaler:
    def __init__(self) -> None:
        self.mean: np.ndarray | None = None
        self.scale: np.ndarray | None = None

    def fit(self, X: np.ndarray) -> RobustStandardScaler:
        self.mean = np.mean(X, axis=0)
        self.scale = np.std(X, axis=0)
        self.scale[self.scale == 0.0] = 1.0
        return self

    def transform(self, X: np.ndarray) -> np.ndarray:
        if self.mean is None or self.scale is None:
            raise ValueError("Scaler has not been fitted")
        if X.shape[-1] != self.mean.shape[0]:
            raise ValueError("Feature count does not match fitted scaler")
        return (X - self.mean) / self.scale

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)


def _feature_matrix(arr: np.ndarray) -> np.ndarray:
    """Build causal features shared by training and inference."""
    arr = np.asarray(arr, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 5:
        raise ValueError("OHLCV input must have shape (n, 5)")
    if not np.all(np.isfinite(arr)):
        raise ValueError("OHLCV input contains non-finite values")
    if np.any(arr[:, :4] <= 0.0) or np.any(arr[:, 4] < 0.0):
        raise ValueError("OHLC prices must be positive and volume non-negative")

    opens = arr[:, 0]
    highs = arr[:, 1]
    lows = arr[:, 2]
    closes = arr[:, 3]
    volumes = arr[:, 4]
    n = len(closes)

    # Derived technical features (all stationary, same length n)
    daily_ret = (closes - opens) / opens
    hl_range = (highs - lows) / lows
    # Trailing mean only. ``mode='same'`` is centred and leaks future volume.
    vol_cumsum = np.cumsum(volumes, dtype=np.float64)
    vol_cumsum[10:] -= vol_cumsum[:-10]
    vol_sma = vol_cumsum / np.minimum(np.arange(n) + 1, 10)
    vol_ratio = volumes / (vol_sma + 1e-8)

    # Log returns (more stable than simple returns)
    log_returns = np.diff(np.log(closes + 1e-8))
    log_returns = np.concatenate([[0], log_returns])

    # Simple returns for reference
    simple_returns = np.diff(closes) / closes[:-1]
    simple_returns = np.concatenate([[0], simple_returns])

    # Momentum features (padded with zeros to maintain length n)
    momentum_5 = np.zeros(n)
    momentum_5[5:] = (closes[5:] - closes[:-5]) / closes[:-5]
    momentum_10 = np.zeros(n)
    momentum_10[10:] = (closes[10:] - closes[:-10]) / closes[:-10]
    momentum_20 = np.zeros(n)
    momentum_20[20:] = (closes[20:] - closes[:-20]) / closes[:-20]
    momentum_30 = np.zeros(n)
    momentum_30[30:] = (closes[30:] - closes[:-30]) / closes[:-30]

    # Volatility (rolling std of returns)
    vol_10 = np.zeros(n)
    for i in range(10, n):
        vol_10[i] = np.std(simple_returns[i - 10 : i])
    vol_20 = np.zeros(n)
    for i in range(20, n):
        vol_20[i] = np.std(simple_returns[i - 20 : i])

    return np.column_stack(
        [
            daily_ret,
            hl_range,
            vol_ratio,
            log_returns,
            simple_returns,
            momentum_5,
            momentum_10,
            momentum_20,
            momentum_30,
            vol_10,
            vol_20,
        ]
    ).astype(np.float32)


def prepare_features(
    arr: np.ndarray, window: int = 30, horizon: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Build supervised windows ending before their future-return labels."""
    if window <= 0 or horizon <= 0:
        raise ValueError("window and horizon must be positive")
    if len(arr) < window + horizon + FEATURE_WARMUP:
        return np.array([]), np.array([])
    feat = _feature_matrix(arr)
    closes = np.asarray(arr)[:, 3]

    X, y = [], []
    for i in range(window + FEATURE_WARMUP, len(feat) - horizon + 1):
        X.append(feat[i - window : i])
        # Information ends at close[i - 1]; target ends horizon bars later.
        future_log_ret = np.log(closes[i + horizon - 1] + 1e-8) - np.log(
            closes[i - 1] + 1e-8
        )
        y.append(future_log_ret)

    X_arr = np.array(X, dtype=np.float32)
    y_arr = np.array(y, dtype=np.float32)
    return X_arr, y_arr


def prepare_inference_window(arr: np.ndarray, window: int = 30) -> np.ndarray:
    """Build the next-horizon input ending at the latest observed bar."""
    if window <= 0:
        raise ValueError("window must be positive")
    if len(arr) < window + FEATURE_WARMUP:
        return np.array([])
    feat = _feature_matrix(arr)
    return feat[-window:][None, ...]


def scale_features(
    X: np.ndarray, scaler: RobustStandardScaler | None = None, fit: bool = False
) -> tuple[np.ndarray, RobustStandardScaler]:
    """Scale features. If fit=True, fit scaler on X. Otherwise use provided scaler."""
    if scaler is None:
        scaler = RobustStandardScaler()
    n_samples, w, f_dim = X.shape
    X_flat = X.reshape(-1, f_dim)
    if fit:
        X_scaled_flat = scaler.fit_transform(X_flat)
    else:
        X_scaled_flat = scaler.transform(X_flat)
    X_scaled = X_scaled_flat.reshape(n_samples, w, f_dim)
    return X_scaled, scaler


def _validated_history(
    symbol: str, period: str
) -> tuple[np.ndarray, str, np.ndarray] | None:
    from ..price_providers import fetch_ohlcv

    hist = fetch_ohlcv(symbol, period=period, interval="1d")
    required = ["Open", "High", "Low", "Close", "Volume"]
    if hist is None or hist.empty or len(hist) < 60 or any(c not in hist for c in required):
        return None
    try:
        index = hist.index
        if index.has_duplicates or not index.is_monotonic_increasing:
            raise ValueError("timestamps are duplicated or unordered")
        timestamps = pd.to_datetime(index, utc=True, errors="coerce")
        if timestamps.isna().any():
            raise ValueError("timestamps are malformed")
        latest = timestamps[-1].to_pydatetime()
        now = datetime.now(UTC)
        if latest > now + timedelta(days=1) or now - latest > timedelta(days=7):
            raise ValueError(f"latest bar is stale or future-dated: {latest.isoformat()}")
        arr = hist[required].to_numpy(dtype=np.float32)
        _feature_matrix(arr)
        high = arr[:, 1]
        low = arr[:, 2]
        if np.any(high < np.maximum.reduce([arr[:, 0], arr[:, 2], arr[:, 3]])):
            raise ValueError("high is below another OHLC value")
        if np.any(low > np.minimum.reduce([arr[:, 0], arr[:, 1], arr[:, 3]])):
            raise ValueError("low is above another OHLC value")
        return arr, latest.isoformat(), timestamps.normalize().asi8
    except (TypeError, ValueError) as exc:
        logger.warning("Rejected malformed history for %s: %s", symbol, exc)
        return None


def fetch_history_array(symbol: str, period: str = "2y") -> np.ndarray | None:
    try:
        validated = _validated_history(symbol, period)
        return validated[0] if validated is not None else None
    except Exception as exc:
        logger.warning("Failed to fetch history for %s: %s", symbol, exc)
        return None


def train_price_lstm(
    symbol: str,
    period: str = "2y",
    window: int = 30,
    horizon: int = 1,
    epochs: int = 25,
    batch_size: int = 32,
    lr: float = 1e-3,
) -> LSTMResult | None:
    """Train the SINGLE shared (global) LSTM and return a prediction for ``symbol``.

    We deliberately do NOT train a per-ticker model anymore: that created one
    checkpoint + scaler file per stock (hundreds of files, one model load and
    one training pass each) which is wasteful. A single cross-asset model is
    trained over pooled universe data and reused for every ticker.
    """
    train_global_lstm(period=period, window=window, horizon=horizon, epochs=epochs, batch_size=batch_size, lr=lr)
    return predict_price_lstm(symbol, period=period, window=window, horizon=horizon)


def _chronological_split(
    X: np.ndarray, y: np.ndarray, timestamps: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Keep validation and test samples strictly after training."""
    if timestamps is not None:
        order = np.argsort(timestamps, kind="stable")
        X, y, timestamps = X[order], y[order], timestamps[order]
        unique_dates = np.unique(timestamps)
        train_cut = unique_dates[int(len(unique_dates) * 0.8)]
        val_cut = unique_dates[int(len(unique_dates) * 0.9)]
        train_mask = timestamps < train_cut
        val_mask = (timestamps >= train_cut) & (timestamps < val_cut)
        test_mask = timestamps >= val_cut
        return (
            X[train_mask],
            y[train_mask],
            X[val_mask],
            y[val_mask],
            X[test_mask],
            y[test_mask],
        )
    train_end = int(len(X) * 0.8)
    val_end = int(len(X) * 0.9)
    return (
        X[:train_end],
        y[:train_end],
        X[train_end:val_end],
        y[train_end:val_end],
        X[val_end:],
        y[val_end:],
    )


def _calibrate_probability(predictions: np.ndarray, targets: np.ndarray) -> tuple[float, float]:
    """Fit a bounded one-dimensional Platt-style calibration on validation only."""
    outcomes = (targets > 0.0).astype(np.float64)
    base = float(np.clip(np.mean(outcomes), 1e-4, 1.0 - 1e-4))
    best = (float("inf"), 0.0, math.log(base / (1.0 - base)))
    for slope in np.linspace(0.0, 200.0, 81):
        lo, hi = -12.0, 12.0
        for _ in range(40):
            intercept = (lo + hi) / 2.0
            z = np.clip(slope * predictions + intercept, -40.0, 40.0)
            if float(np.mean(1.0 / (1.0 + np.exp(-z)))) > base:
                hi = intercept
            else:
                lo = intercept
        intercept = (lo + hi) / 2.0
        probs = 1.0 / (1.0 + np.exp(-np.clip(slope * predictions + intercept, -40.0, 40.0)))
        brier = float(np.mean((probs - outcomes) ** 2))
        if brier < best[0]:
            best = (brier, float(slope), float(intercept))
    return best[1], best[2]


def train_global_lstm(
    period: str = "2y",
    window: int = 30,
    horizon: int = 1,
    epochs: int = 20,
    batch_size: int = 32,
    lr: float = 1e-3,
    max_symbols: int = 60,
    max_samples: int = 30000,
) -> bool:
    """Train ONE LSTM over pooled data from the configured universe.

    Returns True if a global model was trained and saved. The shared scaler is
    fit on the pooled training features so every ticker is scored on the same
    scale (no per-ticker scaler files).
    """
    if window <= 0 or horizon <= 0 or epochs <= 0 or batch_size <= 0:
        raise ValueError("window, horizon, epochs, and batch_size must be positive")
    from ..config import settings
    from ..markets import load_markets, scan_market_codes

    symbols: list[str] = []
    try:
        mkts = load_markets(settings.markets_dir)
        for code in scan_market_codes(settings.markets_dir):
            m = mkts.get(code)
            if m:
                for ticker, spec in m.tickers.items():
                    suffix = spec.yahoo_suffix or m.yahoo_suffix or ""
                    symbols.append(f"{ticker}{suffix}".upper())
    except Exception as exc:
        logger.warning("Global LSTM: could not enumerate markets: %s", exc)

    # De-duplicate, then deterministically sample a manageable training pool.
    seen: set[str] = set()
    uniq: list[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    if len(uniq) > max_symbols:
        step = max(1, len(uniq) // max_symbols)
        uniq = uniq[::step][:max_symbols]

    X_pool: list[np.ndarray] = []
    y_pool: list[np.ndarray] = []
    timestamp_pool: list[np.ndarray] = []
    data_as_of: list[str] = []
    for sym in uniq:
        try:
            validated = _validated_history(sym, period)
        except Exception as exc:
            logger.warning("Failed to fetch history for %s: %s", sym, exc)
            continue
        if validated is None:
            continue
        arr, as_of, timestamps = validated
        X, y = prepare_features(arr, window, horizon)
        if len(X) < 50:
            continue
        target_timestamps = timestamps[
            window + FEATURE_WARMUP + horizon - 1 :
        ]
        if len(target_timestamps) != len(X):
            logger.warning("Timestamp alignment failed for %s", sym)
            continue
        X_pool.append(X)
        y_pool.append(y)
        timestamp_pool.append(target_timestamps)
        data_as_of.append(as_of)
        if sum(len(x) for x in X_pool) >= max_samples:
            break
    if not X_pool:
        logger.warning("Global LSTM: no usable training data found")
        return False

    X_all = np.concatenate(X_pool, axis=0)
    y_all = np.concatenate(y_pool, axis=0)
    timestamps_all = np.concatenate(timestamp_pool, axis=0)
    X_tr, y_tr, X_val, y_val, X_test, y_test = _chronological_split(
        X_all, y_all, timestamps_all
    )
    if not len(X_tr) or not len(X_val) or not len(X_test):
        logger.warning("Global LSTM: chronological split produced an empty partition")
        return False
    if len(X_tr) > max_samples:
        keep = np.linspace(0, len(X_tr) - 1, max_samples, dtype=int)
        X_tr, y_tr = X_tr[keep], y_tr[keep]
    n = len(X_tr) + len(X_val) + len(X_test)

    scaler = RobustStandardScaler().fit(X_tr.reshape(-1, X_tr.shape[2]))
    X_tr_s, _ = scale_features(X_tr, scaler=scaler, fit=False)
    X_val_s, _ = scale_features(X_val, scaler=scaler, fit=False)
    X_test_s, _ = scale_features(X_test, scaler=scaler, fit=False)

    model = PriceLSTM(input_size=X_tr.shape[2]).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = nn.MSELoss()

    X_tr_t = torch.from_numpy(X_tr_s).to(DEVICE)
    y_tr_t = torch.from_numpy(y_tr).unsqueeze(1).to(DEVICE)
    X_val_t = torch.from_numpy(X_val_s).to(DEVICE)
    y_val_t = torch.from_numpy(y_val).unsqueeze(1).to(DEVICE)

    best_val_loss = float("inf")
    best_state = None
    model.train()
    for epoch in range(epochs):
        p = np.random.permutation(len(X_tr_s))
        for i in range(0, len(X_tr_s), batch_size):
            b = p[i : i + batch_size]
            optimizer.zero_grad()
            loss = criterion(model(X_tr_t[b]), y_tr_t[b])
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = criterion(model(X_val_t), y_val_t).item()
        model.train()
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        val_predictions = model(torch.from_numpy(X_val_s).to(DEVICE)).squeeze(1).cpu().numpy()
        test_predictions = model(torch.from_numpy(X_test_s).to(DEVICE)).squeeze(1).cpu().numpy()
    calibration_slope, calibration_intercept = _calibrate_probability(val_predictions, y_val)
    metrics = {
        "mse": float(np.mean((test_predictions - y_test) ** 2)),
        "mae": float(np.mean(np.abs(test_predictions - y_test))),
        "directional_accuracy": float(np.mean((test_predictions > 0.0) == (y_test > 0.0))),
    }

    model_path, scaler_path, metadata_path = _artifact_paths(horizon)
    model_tmp = model_path.with_suffix(model_path.suffix + ".tmp")
    scaler_tmp = scaler_path.with_suffix(scaler_path.suffix + ".tmp")
    metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    torch.save(model.state_dict(), model_tmp)
    with open(scaler_tmp, "wb") as f:
        pickle.dump(scaler, f)
    metadata = {
        "model_version": _model_version(horizon),
        "feature_version": FEATURE_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "input_size": len(FEATURE_NAMES),
        "window": window,
        "horizon": horizon,
        "trained_at": datetime.now(UTC).isoformat(),
        "data_as_of": max(data_as_of),
        "calibration_slope": calibration_slope,
        "calibration_intercept": calibration_intercept,
        "metrics": metrics,
        "model_sha256": _sha256(model_tmp),
        "scaler_sha256": _sha256(scaler_tmp),
    }
    metadata_tmp.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    model_tmp.replace(model_path)
    scaler_tmp.replace(scaler_path)
    metadata_tmp.replace(metadata_path)
    global _global_model, _global_scaler, _global_metadata
    _global_model = _global_scaler = _global_metadata = None
    logger.info("Global LSTM trained on %d samples from %d symbols", n, len(X_pool))
    return True


def rebuild_global_metadata(
    period: str = "2y",
    window: int = 30,
    horizon: int = 1,
    max_symbols: int = 60,
    max_samples: int = 30000,
) -> bool:
    """Recreate metadata/calibration for an existing model without retraining.

    This is intentionally limited to the legacy 1D artifact recovery path:
    model weights and scaler are read from disk, while calibration and metrics
    are recomputed from fresh chronological validation/test windows.
    """
    if window <= 0 or horizon != 1 or max_symbols <= 0 or max_samples <= 0:
        raise ValueError("1D metadata rebuild requires positive window, max_symbols, and max_samples")
    model_path, scaler_path, metadata_path = _artifact_paths(horizon)
    if not model_path.exists() or not scaler_path.exists():
        return False

    try:
        with scaler_path.open("rb") as f:
            scaler = pickle.load(f)
        if not isinstance(scaler, RobustStandardScaler):
            raise ValueError("unexpected scaler type")
        if scaler.mean is None or scaler.scale is None:
            raise ValueError("scaler is not fitted")
        if scaler.mean.shape != (len(FEATURE_NAMES),) or scaler.scale.shape != scaler.mean.shape:
            raise ValueError("scaler feature shape mismatch")
        if not np.all(np.isfinite(scaler.mean)) or not np.all(np.isfinite(scaler.scale)):
            raise ValueError("scaler contains invalid parameters")
        model = PriceLSTM(input_size=len(FEATURE_NAMES)).to(DEVICE)
        model.load_state_dict(torch.load(model_path, map_location=DEVICE, weights_only=True))
        model.eval()
    except Exception as exc:
        logger.warning("Global LSTM metadata rebuild cannot load artifacts: %s", exc)
        return False

    from ..config import settings
    from ..markets import load_markets, scan_market_codes

    symbols: list[str] = []
    try:
        mkts = load_markets(settings.markets_dir)
        for code in scan_market_codes(settings.markets_dir):
            market = mkts.get(code)
            if market:
                for ticker, spec in market.tickers.items():
                    suffix = spec.yahoo_suffix or market.yahoo_suffix or ""
                    symbols.append(f"{ticker}{suffix}".upper())
    except Exception as exc:
        logger.warning("Global LSTM metadata rebuild could not enumerate markets: %s", exc)
    symbols = list(dict.fromkeys(symbols))
    if len(symbols) > max_symbols:
        step = max(1, len(symbols) // max_symbols)
        symbols = symbols[::step][:max_symbols]

    X_pool: list[np.ndarray] = []
    y_pool: list[np.ndarray] = []
    timestamp_pool: list[np.ndarray] = []
    data_as_of: list[str] = []
    for symbol in symbols:
        validated = _validated_history(symbol, period)
        if validated is None:
            continue
        arr, as_of, timestamps = validated
        X, y = prepare_features(arr, window, horizon)
        if len(X) < 50:
            continue
        target_timestamps = timestamps[window + FEATURE_WARMUP + horizon - 1 :]
        if len(target_timestamps) != len(X):
            continue
        X_pool.append(X)
        y_pool.append(y)
        timestamp_pool.append(target_timestamps)
        data_as_of.append(as_of)
        if sum(len(item) for item in X_pool) >= max_samples:
            break
    if not X_pool:
        logger.warning("Global LSTM metadata rebuild found no usable data")
        return False

    X_all = np.concatenate(X_pool, axis=0)
    y_all = np.concatenate(y_pool, axis=0)
    timestamps_all = np.concatenate(timestamp_pool, axis=0)
    _, _, X_val, y_val, X_test, y_test = _chronological_split(
        X_all, y_all, timestamps_all
    )
    if not len(X_val) or not len(X_test):
        logger.warning("Global LSTM metadata rebuild produced an empty partition")
        return False
    X_val_s, _ = scale_features(X_val, scaler=scaler, fit=False)
    X_test_s, _ = scale_features(X_test, scaler=scaler, fit=False)
    with torch.no_grad():
        val_predictions = model(torch.from_numpy(X_val_s).to(DEVICE)).squeeze(1).cpu().numpy()
        test_predictions = model(torch.from_numpy(X_test_s).to(DEVICE)).squeeze(1).cpu().numpy()
    calibration_slope, calibration_intercept = _calibrate_probability(val_predictions, y_val)
    metadata = {
        "model_version": _model_version(horizon),
        "feature_version": FEATURE_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "input_size": len(FEATURE_NAMES),
        "window": window,
        "horizon": horizon,
        "trained_at": datetime.now(UTC).isoformat(),
        "data_as_of": max(data_as_of),
        "calibration_slope": calibration_slope,
        "calibration_intercept": calibration_intercept,
        "metrics": {
            "mse": float(np.mean((test_predictions - y_test) ** 2)),
            "mae": float(np.mean(np.abs(test_predictions - y_test))),
            "directional_accuracy": float(
                np.mean((test_predictions > 0.0) == (y_test > 0.0))
            ),
        },
        "model_sha256": _sha256(model_path),
        "scaler_sha256": _sha256(scaler_path),
    }
    metadata_tmp = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    metadata_tmp.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
    metadata_tmp.replace(metadata_path)
    global _global_model, _global_scaler, _global_metadata
    _global_model = _global_scaler = _global_metadata = None
    logger.info("Global LSTM metadata rebuilt for horizon=%d", horizon)
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_global_artifacts(window: int = 30, horizon: int = 1) -> bool:
    """Load the shared model + scaler into the process cache (once per process).

    Cheap to call repeatedly: it only touches disk when the model is not already
    in memory and the checkpoint files exist.
    """
    global _global_model, _global_scaler, _global_metadata
    model_path, scaler_path, metadata_path = _artifact_paths(horizon)
    if _global_model is not None and _global_metadata is not None:
        if _global_metadata.get("window") == window and _global_metadata.get("horizon") == horizon:
            return True
        _global_model = _global_scaler = _global_metadata = None
    with _global_lock:
        if _global_model is not None and _global_metadata is not None:
            if _global_metadata.get("window") == window and _global_metadata.get("horizon") == horizon:
                return True
            _global_model = _global_scaler = _global_metadata = None
        if not all(p.exists() for p in (model_path, scaler_path, metadata_path)):
            return False
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            trained_at = datetime.fromisoformat(metadata["trained_at"])
            if trained_at.tzinfo is None:
                trained_at = trained_at.replace(tzinfo=UTC)
            expected = {
                "model_version": _model_version(horizon),
                "feature_version": FEATURE_VERSION,
                "feature_names": list(FEATURE_NAMES),
                "input_size": len(FEATURE_NAMES),
                "window": window,
                "horizon": horizon,
            }
            if any(metadata.get(key) != value for key, value in expected.items()):
                raise ValueError("artifact metadata does not match runtime preprocessing")
            if datetime.now(UTC) - trained_at.astimezone(UTC) > timedelta(days=MODEL_MAX_AGE_DAYS):
                raise ValueError("model artifact is stale")
            if metadata.get("model_sha256") != _sha256(model_path):
                raise ValueError("model checksum mismatch")
            if metadata.get("scaler_sha256") != _sha256(scaler_path):
                raise ValueError("scaler checksum mismatch")
            with open(scaler_path, "rb") as f:
                scaler = pickle.load(f)
            if not isinstance(scaler, RobustStandardScaler):
                raise ValueError("unexpected scaler type")
            if scaler.mean is None or scaler.scale is None:
                raise ValueError("scaler is not fitted")
            feat_dim = int(scaler.mean.shape[0])
            if feat_dim != len(FEATURE_NAMES) or scaler.scale.shape != scaler.mean.shape:
                raise ValueError("scaler feature shape mismatch")
            if not np.all(np.isfinite(scaler.mean)) or not np.all(np.isfinite(scaler.scale)) or np.any(scaler.scale <= 0.0):
                raise ValueError("scaler contains invalid parameters")
            model = PriceLSTM(input_size=feat_dim).to(DEVICE)
            model.load_state_dict(torch.load(model_path, map_location=DEVICE, weights_only=True))
            model.eval()
            _global_model, _global_scaler, _global_metadata = model, scaler, metadata
            return True
        except Exception as exc:
            logger.warning("Global LSTM load failed: %s", exc)
            _global_model, _global_scaler, _global_metadata = None, None, None
            return False


def _ensure_global(period: str, window: int, horizon: int = 1) -> tuple["PriceLSTM | None", "RobustStandardScaler | None"]:
    """Load validated artifacts without hidden training during inference."""
    if _load_global_artifacts(window=window, horizon=horizon):
        return _global_model, _global_scaler
    return None, None


def predict_price_lstm(
    symbol: str, period: str = "2y", window: int = 30, horizon: int = 1
) -> LSTMResult | None:
    """Predict next-day direction for ``symbol`` using the SINGLE shared model.

    The model + scaler are loaded once per process after artifact validation;
    incompatible artifacts cause abstention. Valid weights are reused, so no
    per-ticker checkpoint files are created.
    """
    model, scaler = _ensure_global(period, window, horizon)
    if model is None or scaler is None:
        logger.warning("Global LSTM unavailable; cannot predict %s", symbol)
        return None

    try:
        validated = _validated_history(symbol, period)
    except Exception as exc:
        logger.warning("Failed to fetch history for %s: %s", symbol, exc)
        return None
    if validated is None:
        return None
    arr, as_of, _ = validated

    try:
        # End at the latest known bar so the output forecasts beyond observed data.
        X = prepare_inference_window(arr, window=window)
        if len(X) == 0:
            return None
        X_scaled, _ = scale_features(X, scaler=scaler, fit=False)

        with torch.no_grad():
            latest = torch.from_numpy(X_scaled).to(DEVICE)
            pred_ret = float(model(latest).item())

        if not math.isfinite(pred_ret):
            logger.warning("LSTM produced non-finite prediction for %s", symbol)
            return None

        metadata = _global_metadata or {}
        slope = float(metadata.get("calibration_slope", 0.0))
        intercept = float(metadata.get("calibration_intercept", 0.0))
        prob_up = float(
            1.0
            / (
                1.0
                + np.exp(
                    -np.clip(slope * pred_ret + intercept, -40.0, 40.0)
                )
            )
        )
        confidence = float(
            max(0.0, min(1.0, prob_up if prob_up > 0.5 else 1.0 - prob_up))
        )

        if prob_up > 0.55:
            signal = "BULL"
        elif prob_up < 0.45:
            signal = "BEAR"
        else:
            signal = "NEUTRAL"

        metrics = metadata.get("metrics") or {}
        result = LSTMResult(
            ticker=symbol,
            predicted_return=pred_ret,
            probability_up=prob_up,
            confidence=confidence,
            signal=signal,
            mse=float(metrics.get("mse", 0.0)),
            mae=float(metrics.get("mae", 0.0)),
            directional_accuracy=float(metrics.get("directional_accuracy", 0.0)),
            model_version=str(metadata.get("model_version", _model_version(horizon))),
            forecast_horizon=(
                FORECAST_HORIZON if horizon == 1 else f"{horizon} trading days"
            ),
            as_of=as_of,
        )
        logger.info(
            "LSTM prediction symbol=%s version=%s as_of=%s horizon=%s return=%.6f probability_up=%.4f confidence=%.4f",
            symbol,
            result.model_version,
            result.as_of,
            result.forecast_horizon,
            result.predicted_return,
            result.probability_up,
            result.confidence,
        )
        return result
    except Exception as exc:
        logger.warning("Prediction failed for %s: %s", symbol, exc)
        return None


def batch_predict_lstm(
    symbols: list[str],
    period: str = "2y",
    window: int = 30,
) -> dict[str, LSTMResult]:
    """Predict for multiple symbols."""
    results: dict[str, LSTMResult] = {}
    for sym in symbols:
        res = predict_price_lstm(sym, period=period, window=window)
        if res:
            results[sym] = res
    return results
