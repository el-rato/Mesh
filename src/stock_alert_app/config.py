from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:
    pass


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value else default
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent.parent.parent
    )
    data_dir: Path = field(
        default_factory=lambda: _env_path("STOCK_ALERT_DATA", Path("data"))
    )
    db_path: Path = field(
        default_factory=lambda: _env_path(
            "STOCK_ALERT_DB", Path("data/stock_verdict.db")
        )
    )
    markets_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parent / "markets"
    )

    default_markets: tuple[str, ...] = (
        "BSE",
        "NYSE",
        "LSE",
        "KRX",
        "TSE",
        "HKEX",
        "ASX",
        "XETRA",
        "TSX",
        "SGX",
    )

    news_api_key: str = field(default_factory=lambda: os.getenv("NEWS_API_KEY", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini")
    )
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "gemma4:latest")
    )
    reddit_client_id: str = field(
        default_factory=lambda: os.getenv("REDDIT_CLIENT_ID", "")
    )
    reddit_client_secret: str = field(
        default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET", "")
    )
    reddit_user_agent: str = field(
        default_factory=lambda: os.getenv("REDDIT_USER_AGENT", "StockVerdict/0.1")
    )

    # Multi-signal combination weights (must sum to a positive value).
    # Missing signals are renormalized across the available signals at verdict time.
    lstm_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_LSTM_WEIGHT", 0.60)
    )
    technical_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_TECHNICAL_WEIGHT", 0.25)
    )
    news_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_NEWS_WEIGHT", 0.15)
    )
    # Legacy weight kept only for environment/file compatibility (unused by the engine).
    price_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PRICE_WEIGHT", 0.4)
    )

    # Verdict thresholds applied to the combined score in [-1, +1].
    bull_threshold: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_BULL_THRESHOLD", 0.25)
    )
    bear_threshold: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_BEAR_THRESHOLD", -0.25)
    )

    # Background refresh cadence (seconds). Fast refresh updates prices and
    # lightweight technicals; slow refresh re-runs LSTM/news/sentiment.
    scanner_refresh_fast: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_REFRESH_FAST", 300)
    )
    scanner_refresh_slow: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_REFRESH_SLOW", 1800)
    )
    scanner_refresh_batch: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_REFRESH_BATCH", 25)
    )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
