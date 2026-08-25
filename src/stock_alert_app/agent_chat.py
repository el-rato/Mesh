from __future__ import annotations

import json
import logging
import urllib.request

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)


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


def _local_respond(text: str, ctx: dict, market: str | None, mode: str = "AUTO") -> str:
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


def _try_llm(history: list[dict], context: dict, market: str | None) -> str | None:
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
    prompt = f"{system}\n\nCONTEXT:\n{ctx_blob}\n\nCONVERSATION:\n{convo}\nassistant:"

    if settings.gemini_api_key:
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            resp = client.models.generate_content(
                model=settings.gemini_model, contents=prompt
            )
            return (resp.text or "").strip() or None
        except Exception as exc:  # pragma: no cover - depends on creds/network
            logger.warning("Gemini chat failed, falling back: %s", exc)

    if settings.ollama_base_url:
        try:
            return _call_ollama(prompt, settings.ollama_base_url, settings.ollama_model)
        except Exception as exc:  # pragma: no cover - depends on local server
            logger.warning("Ollama chat failed, falling back: %s", exc)

    return None


def chat(messages: list[dict], market: str | None = None, mode: str = "AUTO") -> dict:
    """Respond to a chat turn. Uses an LLM when configured, else a local
    data-driven responder so the agent always works offline."""
    db = Database(settings.db_path)
    ctx = _gather_context(db, market)
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break

    reply = _try_llm(messages, ctx, market)
    provider = "llm"
    if not reply:
        reply = _local_respond(last_user, ctx, market, mode or "AUTO")
        provider = "local"

    return {"role": "assistant", "content": reply, "provider": provider}
