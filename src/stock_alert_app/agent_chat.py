from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

#: Known-good Gemini model ids tried in order as a fallback if the configured
#: (or user-selected) model name is no longer valid.
_GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

_ENV_FILE = Path(__file__).resolve().parent.parent.parent / ".env"


def _load_env() -> dict[str, str]:
    """Re-read the project `.env` on every call so a key added while the server
    is running is detected without a restart. Falls back to the frozen settings.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=_ENV_FILE, override=True)
    except Exception:  # dotenv optional; env vars still work
        pass
    return {
        "gemini_key": os.getenv("GEMINI_API_KEY") or settings.gemini_api_key or "",
        "gemini_model": os.getenv("GEMINI_MODEL") or settings.gemini_model,
        "ollama_base": os.getenv("OLLAMA_BASE_URL") or settings.ollama_base_url,
        "ollama_model": os.getenv("OLLAMA_MODEL") or settings.ollama_model,
        "opencode_base": os.getenv("OPENCODE_BASE_URL") or settings.opencode_base_url or "",
        "opencode_key": os.getenv("OPENCODE_API_KEY") or settings.opencode_api_key or "",
        "opencode_model": os.getenv("OPENCODE_MODEL") or settings.opencode_model,
    }


def config() -> dict[str, object]:
    """What the agent can currently use, for the UI's provider selector."""
    c = _load_env()
    return {
        "providers": ["auto", "gemini", "ollama", "opencode", "local"],
        "default_provider": "auto",
        "gemini_configured": bool(c["gemini_key"]),
        "gemini_model": c["gemini_model"],
        "ollama_configured": bool(c["ollama_base"]),
        "ollama_model": c["ollama_model"],
        "opencode_configured": bool(c["opencode_base"] and c["opencode_key"]),
        "opencode_model": c["opencode_model"],
    }


def _gather_context(db: Database, market: str | None) -> dict:
    verdicts = db.latest_recommendations(market)
    news = db.recent_news_feed(8)
    return {"verdicts": verdicts, "news": news}


def _verdict_lines(verdicts: list[dict], verdict: str, limit: int = 5) -> list[str]:
    out = []
    for v in verdicts:
        if str(v.get("verdict", "")).upper() == verdict.upper():
            ticker = v.get("ticker", "?")
            mkt = v.get("market", "")
            conf = int(round(float(v.get("confidence", 0) or 0) * 100))
            out.append(f"**{ticker}** ({mkt}) — conf {conf}%")
        if len(out) >= limit:
            break
    return out


def _news_lines(news: list[dict], limit: int = 6) -> list[str]:
    out = []
    for n in news:
        title = (n.get("title") or "").strip()
        src = (n.get("source") or "").strip()
        if title:
            out.append(f"• {title}" + (f" — {src}" if src else ""))
        if len(out) >= limit:
            break
    return out


def _local_respond(
    text: str, ctx: dict, market: str | None, mode: str = "AUTO"
) -> str:
    t = (text or "").lower()
    verdicts = ctx["verdicts"]
    news = ctx["news"]
    mode = (mode or "AUTO").upper()
    scope = f" for **{market}**" if market else " across all markets"

    # A focus mode biases a short answer toward that topic without a query.
    if len(t) < 4 and mode != "AUTO":
        if mode == "NEWS":
            lines = _news_lines(news)
            return ("Here's the latest news:\n\n" + "\n".join(lines)) if lines else "No fresh headlines right now."
        if mode == "EQUITY":
            lines = _verdict_lines(verdicts, "BULL") or _verdict_lines(verdicts, "BEAR")
            return ("Top conviction equity calls:\n\n" + "\n".join(lines)) if lines else "No equity committee calls stored yet."
        if mode == "MACRO":
            return "Macro view: the committee's stored signals and the live news flow are the best proxy right now — ask for **news** or **verdicts** to dig in."
        return "AUTO mode picks the most relevant data — ask about **news**, **verdicts**, or a specific market."

    if mode == "NEWS" and any(k in t for k in ("news", "headline", "article", "happening", "report")):
        lines = _news_lines(news)
        return ("Here's the latest:\n\n" + "\n".join(lines)) if lines else "The wires are quiet right now — run a refresh."

    if any(k in t for k in ("news", "headline", "article", "what's happening", "whats happening")):
        lines = _news_lines(news)
        if not lines:
            return (
                "The wires are quiet right now — no fresh headlines in the terminal. "
                "Run a refresh (or open the NEWS tab) and I'll surface the latest."
            )
        return "Here's the latest crossing the wires:\n\n" + "\n".join(lines)

    if any(k in t for k in ("bull", "bullish", "buy", "long", "up", "green")):
        lines = _verdict_lines(verdicts, "BULL")
        if not lines:
            return f"No bullish committee calls stored{scope} yet. Try a refresh."
        return f"Most confident BULLISH calls{scope}:\n\n" + "\n".join(lines)

    if any(k in t for k in ("bear", "bearish", "sell", "short", "down", "red")):
        lines = _verdict_lines(verdicts, "BEAR")
        if not lines:
            return f"No bearish committee calls stored{scope} yet. Try a refresh."
        return f"Most confident BEARISH calls{scope}:\n\n" + "\n".join(lines)

    if any(k in t for k in ("neutral", "hold", "flat")):
        lines = _verdict_lines(verdicts, "NEUTRAL")
        if not lines:
            return f"No neutral calls stored{scope} yet."
        return f"Neutral / HOLD calls{scope}:\n\n" + "\n".join(lines)

    if any(k in t for k in ("verdict", "committee", "recommend", "rating", "opinion")):
        bull = _verdict_lines(verdicts, "BULL", 3)
        bear = _verdict_lines(verdicts, "BEAR", 3)
        parts = [f"Committee snapshot{scope}:"]
        parts.append("Bullish:\n" + ("\n".join(bull) if bull else "  — none"))
        parts.append("Bearish:\n" + ("\n".join(bear) if bear else "  — none"))
        return "\n\n".join(parts)

    if any(k in t for k in ("help", "what can you", "who are you", "how can", "capabilities", "your job")):
        return (
            "I'm **StockVerdict AI** — your cozy markets desk. I can:\n\n"
            "• Summarise the latest **news** crossing the wires\n"
            "• Pull the committee's **bullish / bearish / neutral** calls\n"
            "• Read out the **top verdicts** and confidence levels\n"
            "• Explain what the OVERVIEW, SCANNER, and PAPER tabs do\n\n"
            "Just ask, e.g. *\"what's bullish on NYSE?\"* or *\"show me the news\"*."
        )

    if any(k in t for k in ("hi", "hello", "hey", "gm", "good morning", "yo")):
        return (
            "Hey — I'm StockVerdict AI. Markets are open in my head, quiet everywhere "
            "else. Ask me about **news**, **verdicts**, or anything on the terminal."
        )

    return (
        "I can help with live terminal data — try asking for the **news**, the "
        "**bullish** or **bearish** committee calls, or the top **verdicts**. "
        "I work off the same data you see on the OVERVIEW tab."
    )


def _call_ollama(prompt: str, base_url: str, model: str) -> str | None:
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    return (data.get("response") or "").strip() or None


def _bare_model(model: str) -> str:
    """OpenCode GO expects a bare slug (no ``opencode-go/`` prefix)."""
    m = (model or "").strip()
    if "/" in m and m.split("/", 1)[0] in ("opencode", "opencode-go"):
        return m.split("/", 1)[1]
    return m


def _call_openode_chat(prompt: str, base_url: str, api_key: str, model: str) -> str | None:
    """OpenCode GO is an OpenAI-compatible chat endpoint. POST to /chat/completions."""
    import httpx

    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    body = {
        "model": _bare_model(model),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
    }
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
        if content:
            return content
        # Reasoning models may emit reasoning before content; fall back if empty.
        reasoning = (data["choices"][0]["message"].get("reasoning_content") or "").strip()
        return reasoning or None
    except (KeyError, IndexError, TypeError):
        logger.warning("OpenCode GO returned an unexpected shape: %s", data)
        return None


def _prompt(history: list[dict], context: dict) -> str:
    system = (
        "You are StockVerdict AI, a warm but sharp markets assistant embedded in a "
        "trading terminal. Answer concisely and in plain language. Use the supplied "
        "CONTEXT (verdicts + recent news) to ground your replies; never invent prices "
        "or headlines that aren't in the context. Keep it cozy and helpful."
    )
    ctx_blob = (
        "VERDICTS (market, ticker, verdict, confidence):\n"
        + "\n".join(
            f"- {v.get('market','')} {v.get('ticker','')} {v.get('verdict','')} "
            f"{int(round(float(v.get('confidence',0) or 0)*100))}%"
            for v in context["verdicts"][:25]
        )
        + "\n\nRECENT NEWS:\n"
        + "\n".join(f"- {n.get('title','')}" for n in context["news"][:10])
    )
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    return f"{system}\n\nCONTEXT:\n{ctx_blob}\n\nCONVERSATION:\n{convo}\nassistant:"


def _try_gemini(prompt: str, api_key: str, model: str) -> str | None:
    from google import genai

    client = genai.Client(api_key=api_key)
    candidates = [m for m in [model, *_GEMINI_FALLBACK_MODELS] if m]
    last_err: Exception | None = None
    for m in candidates:
        try:
            resp = client.models.generate_content(model=m, contents=prompt)
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as exc:  # noqa: BLE001 - try the next candidate model
            last_err = exc
            logger.debug("Gemini model %s failed: %s", m, exc)
    if last_err is not None:
        logger.warning("Gemini chat failed on all models: %s", last_err)
    return None


def _try_llm(
    history: list[dict],
    context: dict,
    provider: str = "auto",
    model: str = "",
) -> tuple[str, str] | None:
    """Try the requested provider(s) and return ``(reply, provider_name)``."""
    p = (provider or "auto").lower()
    c = _load_env()
    prompt = _prompt(history, context)

    if p in ("auto", "gemini") and c["gemini_key"]:
        try:
            reply = _try_gemini(prompt, c["gemini_key"], model or c["gemini_model"])
            if reply:
                return reply, "gemini"
        except Exception as exc:  # pragma: no cover - import/deps
            logger.warning("Gemini path failed: %s", exc)

    if p in ("auto", "ollama") and c["ollama_base"]:
        try:
            reply = _call_ollama(prompt, c["ollama_base"], model or c["ollama_model"])
            if reply:
                return reply, "ollama"
        except Exception as exc:  # pragma: no cover - local server
            logger.warning("Ollama path failed: %s", exc)

    if p in ("auto", "opencode") and c["opencode_base"] and c["opencode_key"]:
        try:
            reply = _call_openode_chat(
                prompt, c["opencode_base"], c["opencode_key"], model or c["opencode_model"]
            )
            if reply:
                return reply, "opencode"
        except Exception as exc:  # pragma: no cover - network/creds
            logger.warning("OpenCode GO path failed: %s", exc)

    return None


def chat(
    messages: list[dict],
    market: str | None = None,
    mode: str = "AUTO",
    provider: str = "auto",
    model: str = "",
) -> dict:
    """Respond to a chat turn.

    Routes to the requested LLM provider (gemini / ollama / local / auto);
    ``local`` always uses the data-driven responder, ``auto`` tries any
    configured LLM then falls back. Both the used provider and the available
    provider config are returned so the UI can show live status.
    """
    db = Database(settings.db_path)
    ctx = _gather_context(db, market)
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break

    used = "local"
    reply: str | None = None
    if (provider or "auto").lower() != "local":
        llm = _try_llm(messages, ctx, provider or "auto", model or "")
        if llm:
            reply, used = llm

    if not reply:
        reply = _local_respond(last_user, ctx, market, mode or "AUTO")

    return {
        "role": "assistant",
        "content": reply,
        "provider": used,
        "available": config(),
    }
