from __future__ import annotations

import logging
import math
import pickle
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
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

_global_lock = threading.Lock()
_global_model: "PriceLSTM | None" = None
_global_scaler: "RobustStandardScaler | None" = None


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
    model_version: str = "2.0"

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
            return X
        return (X - self.mean) / self.scale

    def fit_transform(self, X: np.ndarray) -> np.ndarray:
        return self.fit(X).transform(X)


def prepare_features(
    arr: np.ndarray, window: int = 30, horizon: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Prepare sequential input windows from OHLCV and derived features.
    Returns unscaled (X, y). Caller must fit scaler on training data only.
    Uses only stationary features (returns, ratios, momentum).
    """
    if len(arr) < window + horizon + 60:
        return np.array([]), np.array([])

    opens = arr[:, 0]
    highs = arr[:, 1]
    lows = arr[:, 2]
    closes = arr[:, 3]
    volumes = arr[:, 4]
    n = len(closes)

    # Derived technical features (all stationary, same length n)
    daily_ret = (closes - opens) / opens
    hl_range = (highs - lows) / lows
    vol_sma = np.convolve(volumes, np.ones(10) / 10, mode="same")
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

    feat = np.column_stack(
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
    )

    X, y = [], []
    for i in range(window, len(feat) - horizon + 1):
        X.append(feat[i - window : i])
        # Target: future log return over horizon
        future_log_ret = np.log(closes[i + horizon - 1] + 1e-8) - np.log(
            closes[i - 1] + 1e-8
        )
        y.append(future_log_ret)

    X_arr = np.array(X, dtype=np.float32)
    y_arr = np.array(y, dtype=np.float32)
    return X_arr, y_arr


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


def fetch_history_array(symbol: str, period: str = "2y") -> np.ndarray | None:
    from ..price_providers import fetch_ohlcv

    try:
        hist = fetch_ohlcv(symbol, period=period, interval="1d")
        if hist is None or hist.empty or len(hist) < 60:
            return None
        return hist[["Open", "High", "Low", "Close", "Volume"]].values.astype(
            np.float32
        )
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
    return predict_price_lstm(symbol, period=period, window=window)


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
    from ..config import settings
    from ..markets import load_markets, scan_market_codes

    symbols: list[str] = []
    try:
        mkts = load_markets(settings.markets_dir)
        for code in scan_market_codes(settings.markets_dir):
            m = mkts.get(code)
            if m:
                symbols.extend(m.tickers.keys())
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
    for sym in uniq:
        arr = fetch_history_array(sym, period)
        if arr is None or len(arr) < window + horizon + 60:
            continue
        X, y = prepare_features(arr, window, horizon)
        if len(X) < 50:
            continue
        X_pool.append(X)
        y_pool.append(y)
        if sum(len(x) for x in X_pool) >= max_samples:
            break
    if not X_pool:
        logger.warning("Global LSTM: no usable training data found")
        return False

    X_all = np.concatenate(X_pool, axis=0)
    y_all = np.concatenate(y_pool, axis=0)
    if len(X_all) > max_samples:
        keep = np.random.permutation(len(X_all))[:max_samples]
        X_all, y_all = X_all[keep], y_all[keep]
    n = len(X_all)
    perm = np.random.permutation(n)
    train_n = int(n * 0.85)
    X_tr = X_all[perm[:train_n]]
    X_val = X_all[perm[train_n:]]
    y_tr = y_all[perm[:train_n]]
    y_val = y_all[perm[train_n:]]

    scaler = RobustStandardScaler().fit(X_tr.reshape(-1, X_tr.shape[2]))
    X_tr_s, _ = scale_features(X_tr, scaler=scaler, fit=False)
    X_val_s, _ = scale_features(X_val, scaler=scaler, fit=False)

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
            best_state = {k: v.cpu() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    torch.save(model.state_dict(), GLOBAL_MODEL_PATH)
    with open(GLOBAL_SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)
    logger.info("Global LSTM trained on %d samples from %d symbols", n, len(X_pool))
    return True


def _load_global_artifacts() -> bool:
    """Load the shared model + scaler into the process cache (once per process).

    Cheap to call repeatedly: it only touches disk when the model is not already
    in memory and the checkpoint files exist.
    """
    global _global_model, _global_scaler
    if _global_model is not None:
        return True
    with _global_lock:
        if _global_model is not None:
            return True
        if not (GLOBAL_MODEL_PATH.exists() and GLOBAL_SCALER_PATH.exists()):
            return False
        try:
            with open(GLOBAL_SCALER_PATH, "rb") as f:
                scaler = pickle.load(f)
            feat_dim = int(scaler.mean.shape[0])
            model = PriceLSTM(input_size=feat_dim).to(DEVICE)
            model.load_state_dict(torch.load(GLOBAL_MODEL_PATH, map_location=DEVICE))
            model.eval()
            _global_model, _global_scaler = model, scaler
            return True
        except Exception as exc:
            logger.warning("Global LSTM load failed: %s", exc)
            _global_model, _global_scaler = None, None
            return False
    return False


def _ensure_global(period: str, window: int) -> tuple["PriceLSTM | None", "RobustStandardScaler | None"]:
    """Return the (model, scaler), training the global model lazily if missing."""
    if _load_global_artifacts():
        return _global_model, _global_scaler
    # Train on first use (network/CPU heavy, but only ever once per process).
    try:
        train_global_lstm(period=period, window=window)
    except Exception as exc:
        logger.warning("Global LSTM training failed: %s", exc)
        return None, None
    if _load_global_artifacts():
        return _global_model, _global_scaler
    return None, None


def predict_price_lstm(
    symbol: str, period: str = "2y", window: int = 30
) -> LSTMResult | None:
    """Predict next-day direction for ``symbol`` using the SINGLE shared model.

    The model + scaler are loaded once per process (and trained lazily on first
    use if absent), so every subsequent ticker reuses the same weights — no
    per-ticker checkpoint files are created.
    """
    model, scaler = _ensure_global(period, window)
    if model is None or scaler is None:
        logger.warning("Global LSTM unavailable; cannot predict %s", symbol)
        return None

    arr = fetch_history_array(symbol, period)
    if arr is None or len(arr) < window + 10:
        return None

    try:
        # Prepare features and apply the shared scaler (transform, never fit).
        X, _ = prepare_features(arr, window=window)
        if len(X) == 0:
            return None
        X_scaled, _ = scale_features(X, scaler=scaler, fit=False)

        with torch.no_grad():
            latest = torch.from_numpy(X_scaled[-1:]).to(DEVICE)
            pred_ret = float(model(latest).item())

        if not math.isfinite(pred_ret):
            logger.warning("LSTM produced non-finite prediction for %s", symbol)
            return None

        prob_up = float(1.0 / (1.0 + np.exp(-pred_ret * 50)))
        confidence = float(
            max(0.0, min(1.0, prob_up if prob_up > 0.5 else 1.0 - prob_up))
        )

        if pred_ret > 0.002:
            signal = "BULL"
        elif pred_ret < -0.002:
            signal = "BEAR"
        else:
            signal = "NEUTRAL"

        return LSTMResult(
            ticker=symbol,
            predicted_return=pred_ret,
            probability_up=prob_up,
            confidence=confidence,
            signal=signal,
        )
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
