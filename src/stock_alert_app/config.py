from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore

    # Always load .env from the project root (repo root), regardless of the
    # directory uvicorn was launched from, so GEMINI_API_KEY etc. resolve.
    _env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(dotenv_path=_env_file, override=False)
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


def _env_csv(name: str, default: str) -> list[str]:
    value = os.getenv(name) or default
    return [p.strip() for p in value.split(",") if p.strip()]


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
    alpha_vantage_key: str = field(default_factory=lambda: os.getenv("ALPHA_VANTAGE_KEY", ""))
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "gemma4:latest")
    )
    # OpenCode GO — an OpenAI-compatible chat endpoint (base URL + key + model).
    # Base URL: https://opencode.ai/zen/go/v1  (OpenAI-compatible, paths appended).
    # Models are referenced by their bare slug (no "opencode-go/" prefix).
    opencode_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1"
        )
    )
    opencode_api_key: str = field(default_factory=lambda: os.getenv("OPENCODE_API_KEY", ""))
    opencode_model: str = field(
        default_factory=lambda: os.getenv(
            "OPENCODE_MODEL", "deepseek-v4-flash-vision-exp"
        )
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

    # Quantitative ensemble weights (per model). Calibratable later using
    # historical out-of-sample performance; LSTM is not hardcoded as dominant.
    model_weight_lstm: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_MODEL_WEIGHT_LSTM", 0.40)
    )
    model_weight_gbm: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_MODEL_WEIGHT_GBM", 0.30)
    )
    model_weight_momentum: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_MODEL_WEIGHT_MOMENTUM", 0.30)
    )

    # Investment Committee signal weights.
    quant_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_QUANT_WEIGHT", 0.55)
    )
    social_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_SOCIAL_WEIGHT", 0.05)
    )
    social_cache_ttl: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_SOCIAL_CACHE_TTL", 1800)
    )

    # Paper trading (simulation only) — no real brokerage ever.
    paper_starting_cash: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PAPER_CASH", 100000.0)
    )
    paper_commission: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PAPER_COMMISSION", 1.0)
    )
    paper_slippage: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PAPER_SLIPPAGE", 0.0005)
    )
    # Simple simulated risk rules (assumptions).
    paper_max_gross_ratio: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PAPER_MAX_GROSS", 2.0)
    )
    paper_max_position_ratio: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PAPER_MAX_POSITION", 0.5)
    )
    paper_short_margin: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_PAPER_SHORT_MARGIN", 1.0)
    )
    paper_min_stats: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_PAPER_MIN_STATS", 3)
    )
    # Intraday session window (local clock; used for display + end-of-session).
    paper_session_start: str = field(default_factory=lambda: os.getenv("STOCK_ALERT_PAPER_SESSION_START", "09:30"))
    paper_session_end: str = field(default_factory=lambda: os.getenv("STOCK_ALERT_PAPER_SESSION_END", "16:00"))
    # Demo competitors are clearly labelled simulated accounts.
    paper_demo_players: bool = field(
        default_factory=lambda: os.getenv("STOCK_ALERT_PAPER_DEMO", "1") != "0"
    )

    # Historical data provider chain for backtesting, tried in order.
    historical_providers: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            _env_csv("STOCK_ALERT_HIST_PROVIDERS", "primary,secondary,tertiary")
        )
    )

    # Minimum simulated trade notional that triggers a "significant trade"
    # notification. Reversals always notify; small trades below this are silent.
    notification_trade_threshold: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_NOTIFY_TRADE_THRESHOLD", 25000.0)
    )
    regime_weight: float = field(
        default_factory=lambda: _env_float("STOCK_ALERT_REGIME_WEIGHT", 0.05)
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

    # Institutional / 13F hedge-fund coverage. The tracked fund universe is
    # derived dynamically from SEC EDGAR's 13F-filer list (paginated) rather
    # than a small hardcoded list; these caps keep ingestion within API/
    # rate-limit bounds while allowing the universe to scale far beyond the
    # previous hardcoded set.
    max_institutional_funds: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_MAX_INSTITUTIONAL_FUNDS", 200)
    )
    institutional_filer_pages: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_INSTITUTIONAL_PAGES", 10)
    )

    # Authentication. Session cookies are HTTP-only. Set STOCK_ALERT_AUTH_SECURE=1
    # when serving over HTTPS; local/dev HTTP deployments keep Secure disabled.
    auth_cookie_secure: bool = field(
        default_factory=lambda: os.getenv("STOCK_ALERT_AUTH_SECURE", "0") == "1"
    )

    # ---- Production deployment ------------------------------------------------
    #: Deployment environment: development (default) | staging | production.
    #: production enables secure-by-default behaviour (see auth_cookie_secure note).
    environment: str = field(
        default_factory=lambda: os.getenv("STOCK_ALERT_ENV", "development").lower()
    )
    #: Database URL. SQLite (sqlite:///path/to.db) is the supported, default
    #: engine. A non-SQLite DATABASE_URL (e.g. postgresql://...) is accepted as
    #: configuration but REJECTED at service startup until the PostgreSQL
    #: dialect migration lands (see DEPLOYMENT.md) — fail fast, never half-work.
    database_url: str = field(
        default_factory=lambda: os.getenv("DATABASE_URL", "")
    )
    #: Web server bind address (used by `stock-alert-app serve`).
    serve_host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    serve_port: int = field(default_factory=lambda: _env_int("PORT", 8000))
    #: Override the built frontend directory (defaults to <repo>/frontend/dist).
    #: Stored as a string: empty means "not overridden" (Path("") would
    #: normalize to "." and silently point the static dir at the CWD).
    frontend_dist: str = field(
        default_factory=lambda: os.getenv("FRONTEND_DIST", "").strip()
    )
    #: Logging: LOG_LEVEL=INFO|DEBUG|WARNING..., LOG_JSON=1 for structured logs.
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_json: bool = field(default_factory=lambda: os.getenv("LOG_JSON", "0") == "1")
    #: Default socket timeout (s) applied at service start — bounds any straggler
    #: provider library that lacks an explicit timeout (e.g. feedparser).
    http_timeout_s: int = field(default_factory=lambda: _env_int("HTTP_TIMEOUT", 20))
    #: Chart-history in-memory cache TTL (seconds).
    chart_history_ttl: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_HISTORY_TTL", 1800)
    )
    #: Price-provider circuit-breaker cooldown after a rate limit (seconds).
    provider_cooldown_s: int = field(
        default_factory=lambda: _env_int("STOCK_ALERT_PROVIDER_COOLDOWN", 120)
    )
    #: Enable the API's on-refresh background top-up when a dedicated worker runs.
    worker_managed: bool = field(
        default_factory=lambda: os.getenv("WORKER_MANAGED", "0") == "1"
    )

    def validate_runtime(self) -> None:
        """Fail-fast production checks. Called by the API and worker at startup."""
        if self.environment == "production" and not self.auth_cookie_secure:
            raise RuntimeError(
                "STOCK_ALERT_AUTH_SECURE=1 is required when STOCK_ALERT_ENV=production "
                "(session cookies must be Secure over HTTPS)."
            )
        url = (self.database_url or "").strip()
        if url and not url.lower().startswith(("sqlite", "file:")):
            raise RuntimeError(
                "DATABASE_URL points at a non-SQLite engine. PostgreSQL support is "
                "documented in DEPLOYMENT.md but not yet enabled at runtime; start "
                "with sqlite:/// or remove DATABASE_URL."
            )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
