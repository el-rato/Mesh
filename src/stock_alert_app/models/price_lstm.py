from __future__ import annotations

import logging
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yfinance as yf
from torch import nn

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent / "price_lstm_checkpoints"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


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
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 60:
            return None
        return hist[["Open", "High", "Low", "Close", "Volume"]].values.astype(
            np.float32
        )
    except Exception as exc:
        logger.warning("Failed to fetch history for %s: %s", symbol, exc)
        return None


def _model_path(symbol: str) -> Path:
    return MODEL_DIR / f"{symbol.replace('.', '_')}_model.pt"


def _scaler_path(symbol: str) -> Path:
    return MODEL_DIR / f"{symbol.replace('.', '_')}_scaler.pkl"


def train_price_lstm(
    symbol: str,
    period: str = "2y",
    window: int = 30,
    horizon: int = 1,
    epochs: int = 25,
    batch_size: int = 32,
    lr: float = 1e-3,
) -> LSTMResult | None:
    """Train LSTM with chronological train/val/test split and proper scaling (no leakage)."""
    arr = fetch_history_array(symbol, period)
    if arr is None:
        return None

    X, y = prepare_features(arr, window, horizon)
    if len(X) < 50:
        return None

    # Chronological split: 70% train, 15% val, 15% test
    n = len(X)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)

    X_train, y_train = X[:train_end], y[:train_end]
    X_val, y_val = X[train_end:val_end], y[train_end:val_end]
    X_test, y_test = X[val_end:], y[val_end:]

    # Fit scaler ONLY on training data
    X_train_scaled, scaler = scale_features(X_train, fit=True)
    X_val_scaled, _ = scale_features(X_val, scaler=scaler, fit=False)
    X_test_scaled, _ = scale_features(X_test, scaler=scaler, fit=False)

    model = PriceLSTM(input_size=X.shape[2]).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    criterion = nn.MSELoss()

    X_train_t = torch.from_numpy(X_train_scaled).to(DEVICE)
    y_train_t = torch.from_numpy(y_train).unsqueeze(1).to(DEVICE)
    X_val_t = torch.from_numpy(X_val_scaled).to(DEVICE)
    y_val_t = torch.from_numpy(y_val).unsqueeze(1).to(DEVICE)
    X_test_t = torch.from_numpy(X_test_scaled).to(DEVICE)
    y_test_t = torch.from_numpy(y_test).unsqueeze(1).to(DEVICE)

    best_val_loss = float("inf")
    best_state = None

    model.train()
    for epoch in range(epochs):
        perm = np.random.permutation(len(X_train))
        for i in range(0, len(X_train), batch_size):
            batch_idx = perm[i : i + batch_size]
            bx = X_train_t[batch_idx]
            by = y_train_t[batch_idx]

            optimizer.zero_grad()
            preds = model(bx)
            loss = criterion(preds, by)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()

        model.eval()
        with torch.no_grad():
            val_preds = model(X_val_t)
            val_loss = criterion(val_preds, y_val_t).item()
        model.train()

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.cpu() for k, v in model.state_dict().items()}

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        test_preds = model(X_test_t).squeeze(1).cpu().numpy()
        test_y = y_test

        mse = float(np.mean((test_preds - test_y) ** 2))
        mae = float(np.mean(np.abs(test_preds - test_y)))
        # Directional accuracy (% of correct sign prediction)
        correct_dir = np.sign(test_preds) == np.sign(test_y)
        dir_acc = float(np.mean(correct_dir))

        # Latest prediction for current state (use last test window, properly scaled)
        latest_window = X[-1:].copy()
        latest_scaled, _ = scale_features(latest_window, scaler=scaler, fit=False)
        latest_t = torch.from_numpy(latest_scaled).to(DEVICE)
        pred_ret = float(model(latest_t).item())

    if not math.isfinite(pred_ret):
        logger.warning("LSTM produced non-finite prediction for %s", symbol)
        return None

    # Save checkpoint & scaler
    torch.save(model.state_dict(), _model_path(symbol))
    with open(_scaler_path(symbol), "wb") as f:
        pickle.dump(scaler, f)

    # Probability up via sigmoid of prediction scaled
    prob_up = float(1.0 / (1.0 + np.exp(-pred_ret * 50)))
    confidence = float(max(0.0, min(1.0, 1.0 - mse * 50)))

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
        mse=mse,
        mae=mae,
        directional_accuracy=dir_acc,
    )


def predict_price_lstm(
    symbol: str, period: str = "2y", window: int = 30
) -> LSTMResult | None:
    model_file = _model_path(symbol)
    scaler_file = _scaler_path(symbol)

    arr = fetch_history_array(symbol, period)
    if arr is None or len(arr) < window + 10:
        return None

    if not model_file.exists() or not scaler_file.exists():
        return train_price_lstm(symbol, period, window=window)

    try:
        with open(scaler_file, "rb") as f:
            scaler = pickle.load(f)

        # Prepare features without fitting scaler
        X, _ = prepare_features(arr, window=window)
        if len(X) == 0:
            return train_price_lstm(symbol, period, window=window)

        # Apply SAVED scaler only (transform, not fit)
        X_scaled, _ = scale_features(X, scaler=scaler, fit=False)

        model = PriceLSTM(input_size=X.shape[2]).to(DEVICE)
        model.load_state_dict(torch.load(model_file, map_location=DEVICE))
        model.eval()

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
        logger.warning("Prediction failed for %s, retraining: %s", symbol, exc)
        return train_price_lstm(symbol, period, window=window)


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
