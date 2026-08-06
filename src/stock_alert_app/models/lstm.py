from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import yfinance as yf

from ..config import settings
from ..db import Database

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "lstm_checkpoints"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


@dataclass
class LSTMPrediction:
    ticker: str
    predicted_return: float
    confidence: float
    signal: str
    model_version: str = "1.0"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "predicted_return": round(self.predicted_return, 6),
            "confidence": round(self.confidence, 4),
            "signal": self.signal,
            "model_version": self.model_version,
        }


class LSTMModel(nn.Module):
    def __init__(
        self,
        input_size: int = 5,
        hidden_size: int = 64,
        num_layers: int = 2,
        dropout: float = 0.2,
    ) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
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
        _, (h_n, _) = self.lstm(x)
        out = self.dropout(h_n[-1])
        return self.fc(out)


def _prepare_features(df: np.ndarray, window: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """Create sliding windows from price data.
    Returns (X, y) where X is (n_samples, window, features) and y is next-day return.
    """
    if len(df) < window + 10:
        return np.array([]), np.array([])

    opens = df[:, 0]
    highs = df[:, 1]
    lows = df[:, 2]
    closes = df[:, 3]
    volumes = df[:, 4]

    daily_ret = (closes - opens) / opens
    hl_range = (highs - lows) / lows
    vol_ratio = volumes / (np.mean(volumes) if np.mean(volumes) > 0 else 1)
    returns = np.diff(closes) / closes[:-1]

    feat_len = len(df) - 1
    feat = np.column_stack([
        daily_ret[1:],
        hl_range[1:],
        vol_ratio[1:],
        np.concatenate([[0], returns[:-1]]),
        np.concatenate([[0, 0], returns[:-2]]) if len(returns) > 1 else np.zeros(feat_len),
    ])

    X, y = [], []
    for i in range(window, len(feat)):
        X.append(feat[i - window:i])
        y.append(returns[i])
    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


def _fetch_history_array(symbol: str, period: str = "2y") -> np.ndarray | None:
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 50:
            return None
        arr = hist[["Open", "High", "Low", "Close", "Volume"]].values
        return arr.astype(np.float32)
    except Exception as exc:
        logger.warning("LSTM fetch failed for %s: %s", symbol, exc)
        return None


def _model_path(symbol: str) -> Path:
    return MODEL_DIR / f"{symbol.replace('.', '_')}_lstm.pt"


def _scaler_path(symbol: str) -> Path:
    return MODEL_DIR / f"{symbol.replace('.', '_')}_scaler.pkl"


def train_lstm(symbol: str, period: str = "2y", epochs: int = 20, window: int = 30) -> LSTMPrediction | None:
    """Train LSTM on symbol's historical data and save checkpoint."""
    arr = _fetch_history_array(symbol, period)
    if arr is None:
        return None

    X, y = _prepare_features(arr, window)
    if len(X) == 0:
        return None

    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    model = LSTMModel(input_size=X.shape[2]).to(DEVICE)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.MSELoss()

    X_train_t = torch.from_numpy(X_train).to(DEVICE)
    y_train_t = torch.from_numpy(y_train).unsqueeze(1).to(DEVICE)
    X_val_t = torch.from_numpy(X_val).to(DEVICE)
    y_val_t = torch.from_numpy(y_val).unsqueeze(1).to(DEVICE)

    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        pred = model(X_train_t)
        loss = criterion(pred, y_train_t)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        val_pred = model(X_val_t)
        val_loss = criterion(val_pred, y_val_t).item()
        last_pred = model(torch.from_numpy(X[-1:]).to(DEVICE)).item()

    torch.save(model.state_dict(), _model_path(symbol))
    logger.info("LSTM trained for %s: val_loss=%.6f, last_pred=%.6f", "epochs", epochs)
    return LSTMPrediction(
        ticker=symbol,
        predicted_return=last_pred,
        confidence=max(0.0, 1.0 - val_loss * 10),
        signal="BULLISH" if last_pred > 0.005 else "BEARISH" if last_pred < -0.005 else "NEUTRAL",
    )


def load_lstm(symbol: str) -> tuple[LSTMModel | None, np.ndarray | None]:
    path = _model_path(symbol)
    if not path.exists():
        return None, None
    try:
        model = LSTMModel().to(DEVICE)
        model.load_state_dict(torch.load(path, map_location=DEVICE))
        model.eval()
        scaler = None
        sp = _scaler_path(symbol)
        if sp.exists():
            with open(sp, "rb") as f:
                scaler = pickle.load(f)
        return model, scaler
    except Exception as exc:
        logger.warning("Failed to load LSTM for %s: %s", symbol, exc)
        return None, None


def predict_lstm(symbol: str, period: str = "2y", window: int = 30) -> LSTMPrediction | None:
    """Load or train LSTM and predict next-day return."""
    model, scaler = load_lstm(symbol)
    arr = _fetch_history_array(symbol, period)
    if arr is None or len(arr) < window + 5:
        return None

    if model is None:
        result = train_lstm(symbol, period, epochs=10, window=window)
        if result:
            return result
        return None

    X, _ = _prepare_features(arr, window)
    if len(X) == 0:
        return None

    with torch.no_grad():
        pred = model(torch.from_numpy(X[-1:]).to(DEVICE)).item()

    return LSTMPrediction(
        ticker=symbol,
        predicted_return=pred,
        confidence=0.6,
        signal="BULLISH" if pred > 0.005 else "BEARISH" if pred < -0.005 else "NEUTRAL",
    )


def batch_predict_lstm(symbols: list[str], period: str = "2y", window: int = 30) -> dict[str, LSTMPrediction]:
    results: dict[str, LSTMPrediction] = {}
    for sym in symbols:
        pred = predict_lstm(sym, period, window)
        if pred:
            results[sym] = pred
    return results