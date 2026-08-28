from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from pathlib import Path

from .agent_tools import (
    parse_tool_call,
    run_tool,
    tool_result_block,
    tools_system_text,
)
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


def _extract_security(text: str) -> tuple[str, str] | None:
    """Find a known (market, ticker) referenced in the user's message, if any."""
    from .db import Database as _DB

    try:
        secs = _DB(settings.db_path).all_securities()
    except Exception:
        return None
    tickers = {(s["market"], s["ticker"].upper()) for s in secs}
    if not tickers:
        return None
    words = re.findall(r"[A-Za-z]{1,6}", text or "")
    for w in words:
        wu = w.upper()
        hit = next(((m, t) for (m, t) in tickers if t == wu), None)
        if hit:
            return hit
    return None


_ROTATION_RE = re.compile(r"\b(rotation|sector|industry|theme|setup|strateg|thesis|trade plan|what to watch|leader|laggard)\b", re.I)


def _detect_market(text: str) -> str | None:
    """Detect an explicit market code (BSE, LSE, TSE, XETRA, ...) in the request."""
    from .markets import scan_market_codes

    t = (text or "").upper()
    for code in scan_market_codes(settings.markets_dir):
        if re.search(r"\b" + re.escape(code) + r"\b", t):
            return code
    return None


def _mentions_theme(text: str) -> bool:
    """True if the request names a known thematic basket (word-bounded)."""
    from .strategy import list_themes

    low = (text or "").lower()
    for name in list_themes()["themes"]:
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return True
    return False


def _local_rotation(theme: str, market: str | None) -> str | None:
    """Build a readable rotation/strategy brief from the structured snapshot."""
    from .strategy import analyze_rotation, list_themes

    snap = analyze_rotation(theme=theme, market=market)
    if snap.get("error"):
        return None
    if not snap.get("covered"):
        return (
            f"I resolved '{theme}' to a basket of {snap.get('basket_size', 0)} names "
            f"(via {snap.get('resolved_from')}) but there's no analysis stored yet. "
            "Run a refresh and ask again — I'll then break down the setup and what to watch."
        )

    lines = [f"## {snap['theme']} rotation"]
    b = snap.get("breadth", {})
    lines.append(
        f"- Breadth: {b.get('bull')} bullish / {b.get('bear')} bearish / "
        f"{b.get('neutral')} neutral ({int((b.get('pct_bull') or 0) * 100)}% bullish)."
    )
    comm = snap.get("committee", {})
    if comm.get("verdict"):
        lines.append(
            f"- Investment Committee (LSTM/technical/news): {comm['verdict']} "
            f"(avg {comm.get('avg_score'):+.2f}, {int((comm.get('pct_bull') or 0) * 100)}% bullish)."
        )
    reg = snap.get("regime", {})
    if reg.get("label") not in (None, "unknown"):
        lines.append(
            f"- Market regime: {reg['label']} (S&P {reg.get('spx_change_pct')}%, VIX {reg.get('vix')})."
        )
    am = snap.get("avg_momentum")
    if am is not None:
        lines.append(f"- Avg 20-day momentum: {am:+.2%}.")
    an = snap.get("avg_news")
    if an is not None:
        lines.append(f"- Avg news sentiment: {an:+.2f}.")
    sm = snap.get("smart_money") or []
    if sm:
        names = ", ".join(f"{s['ticker']}({'+' if s['net'] > 0 else ''}{s['net']})" for s in sm[:4])
        lines.append(f"- Smart-money (13F) flows: {names}.")
    lines.append("")
    lines.append("**Setup**")
    for s in snap.get("setup", []):
        lines.append(f"- {s}")
    lines.append("")
    lines.append("**What to watch**")
    for s in snap.get("what_to_watch", []):
        lines.append(f"- {s}")
    return "\n".join(lines)


def _local_respond_with_tools(
    text: str, ctx: dict, market: str | None, mode: str = "AUTO"
) -> str:
    """Local responder that can call terminal tools for a referenced ticker.

    When the user names a specific security and asks about news / a verdict /
    analysis, this refreshes and pulls the real data from the terminal itself,
    then formats it — so the offline agent also never has to bluff.
    """
    t = (text or "").lower()

    # Rotation / sector / strategy requests (also triggered by naming a theme).
    if _ROTATION_RE.search(t) or _mentions_theme(text):
        from .strategy import list_themes

        theme = (text or "").strip()
        # Prefer a known theme whose words all appear in the request.
        matched = [
            name
            for name in list_themes()["themes"]
            if set(name.split()).issubset(set(theme.lower().split()))
        ]
        if not matched:
            # Otherwise use the longest known theme that is a substring.
            matched = [name for name in list_themes()["themes"] if name in theme.lower()]
        if matched:
            theme = max(matched, key=len)
        mkt = _detect_market(text) or market
        result = _local_rotation(theme, mkt)
        if result:
            return result

    if any(k in t for k in ("research", "deep dive", "deepdive", "catalyst", "why", "brief", "thesis on")):
        sec = _extract_security(text)
        if sec:
            res = run_tool("research_ticker", {"market": sec[0], "ticker": sec[1]})
            r = res.get("result") or {}
            brief = r.get("brief") or {}
            if r.get("verdict") or brief.get("status") == "ok":
                lines = [
                    f"{sec[1]} ({sec[0]}): verdict **{r.get('verdict') or 'N/A'}**, "
                    f"committee {r.get('committee_verdict')} ({r.get('committee_score')})."
                ]
                if brief.get("catalysts"):
                    lines.append("\n**Catalysts**\n" + "\n".join(f"- {x}" for x in brief["catalysts"][:5]))
                if brief.get("risks"):
                    lines.append("\n**Risks**\n" + "\n".join(f"- {x}" for x in brief["risks"][:5]))
                if r.get("institutional"):
                    lines.append(
                        f"\n**13F**: {r['institutional'].get('holding_funds', 0)} funds hold; net {r['institutional'].get('net', 0)} buys/sells."
                    )
                if not brief.get("catalysts") and not brief.get("risks"):
                    lines.append("\nNo scored news evidence stored yet — run a refresh for a fuller brief.")
                return "\n".join(lines)

    if any(k in t for k in ("news", "headline", "article", "happening", "latest", "report")):
        sec = _extract_security(text)
        if sec:
            res = run_tool("refresh_news", {"market": sec[0], "ticker": sec[1], "limit": 6})
            items = (res.get("result") or {}).get("news", [])
            if items:
                lines = [
                    f"• {n.get('title','')}" + (f" — {n.get('source','')}" if n.get("source") else "")
                    for n in items
                ]
                return f"Latest news for {sec[1]} ({sec[0]}):\n\n" + "\n".join(lines)
            return f"No fresh news found for {sec[1]} ({sec[0]}) right now — try again after a refresh."

    if any(k in t for k in ("verdict", "analy", "rating", "recommend", "bull", "bear", "neutral")):
        sec = _extract_security(text)
        if sec:
            res = run_tool("get_verdict", {"market": sec[0], "ticker": sec[1]})
            v = res.get("result") or {}
            if v.get("verdict"):
                conf = int(round(float(v.get("confidence", 0) or 0) * 100))
                return (
                    f"{sec[1]} ({sec[0]}): **{v['verdict']}** (conf {conf}%) — "
                    f"{(v.get('reason') or '')[:300]}"
                )
            if v.get("note"):
                return f"{sec[1]} ({sec[0]}): {v['note']}."

    # Add / remove a ticker to a portfolio group directly from the chat.
    add_m = re.search(
        r"\b(add|put|move|save|throw|stick)\b.*?\b([A-Za-z]{1,6})\b.*?\b(group|watchlist|portfolio|bucket|list)\b",
        text or "",
        re.I,
    )
    remove_m = re.search(
        r"\b(remove|delete|drop|take)\b.*?\b([A-Za-z]{1,6})\b.*?\b(group|watchlist|portfolio|bucket|list|from)\b",
        text or "",
        re.I,
    )
    if add_m or remove_m:
        m = add_m or remove_m
        tkr = m.group(2).upper()
        sec = _extract_security(text) or (None, tkr)
        if sec and sec[1].upper() == tkr:
            # Pull the group name from a few common phrasings:
            #   "my Growth group" / "the Tech bucket"
            #   "add AAPL to Watchlist" (name == keyword)
            #   "group called Growth" / "put it in Value"
            #   "remove MSFT from Growth" (name after 'from', no trailing keyword)
            # NOTE: the bare token "list" is intentionally NOT a keyword so that the
            # common group name "Watchlist" is captured as a whole, not split as
            # "Watch" + the "list" keyword.
            group_name = ""
            gm = re.search(
                r"\b(my|the)\s+([A-Za-z0-9 &_-]+?)\s*(group|watchlist|portfolio|bucket)\b",
                text or "",
                re.I,
            )
            if not gm:
                gm = re.search(
                    r"\b(to|in|into|from)\s+([A-Za-z0-9 &_-]+)(?:\s+(group|watchlist|portfolio|bucket)\b)?",
                    text or "",
                    re.I,
                )
            if not gm:
                gm = re.search(
                    r"\b(group|watchlist|portfolio|bucket)\b\s*(?:called|named)?\s*['\"]?([A-Za-z0-9 &_-]+)['\"]?",
                    text or "",
                    re.I,
                )
            if not gm and remove_m:
                # "remove MSFT from Growth" — name follows 'from'/'out of' with no keyword.
                gm = re.search(r"\b(from|out of)\s+([A-Za-z0-9 &_-]{1,24})", text or "", re.I)
            if gm:
                group_name = gm.group(2).strip().strip("'\"")
            # Clean the captured name: drop leading determiners, a trailing
            # space-separated keyword (e.g. "Value list" -> "Value") while leaving
            # the single-word group name "Watchlist" intact, and any polite filler.
            group_name = re.sub(r"^(my|the|a)\s+", "", group_name, flags=re.I).strip()
            group_name = re.sub(
                r"\s+(group|watchlist|portfolio|bucket|list)$", "", group_name, flags=re.I
            ).strip()
            group_name = re.sub(
                r"\b(please|now|thanks|thank you|for me)\b.*$", "", group_name, flags=re.I
            ).strip().strip("'\" ")
            if group_name:
                action = "add_to_group" if add_m else "remove_from_group"
                res = run_tool(action, {"ticker": tkr, "market": sec[0] or "", "group": group_name})
                r = res.get("result") or {}
                if r.get("error"):
                    return f"Couldn't update the group: {r['error']}"
                verb = "Added" if add_m else "Removed"
                return (
                    f"{verb} **{tkr}** ({sec[0]}) {'to' if add_m else 'from'} "
                    f"**{r.get('group')}**"
                    + ("" if r.get("added", True) or not add_m else " (already in the group).")
                )

    return _local_respond(text, ctx, market, mode or "AUTO")


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


def _prompt(history: list[dict], context: dict, search: str = "") -> str:
    s = (search or "").lower()
    if s == "deep":
        style = (
            "\n\nSEARCH MODE: DEEP SEARCH — the user wants an exhaustive answer. "
            "Cast a wide net: use `web_search` (Fincept-style worldwide asset + news "
            "search) for anything not fully covered by CONTEXT, and layer additional "
            "tools (`refresh_news`, `global_news`, `research_ticker`, `scanner`, "
            "`analyze_rotation`) as needed. Cross-check multiple sources, then give "
            "a thorough, well-organised answer with short section headers and the "
            "key facts behind every claim."
        )
    elif s == "low":
        style = (
            "\n\nSEARCH MODE: LOW-TOKEN — be extremely economical. Use at most ONE "
            "tool call (prefer `web_search`) only if CONTEXT cannot answer at all. "
            "Reply in at most 3 short sentences of plain text — no headers, no "
            "bullet lists, no preamble."
        )
    else:
        style = ""
    system = (
        "You are StockVerdict AI, a warm but sharp markets assistant embedded in a "
        "trading terminal. Answer concisely and in plain language. Use the supplied "
        "CONTEXT (verdicts + recent news) to ground your replies; never invent prices "
        "or headlines that aren't in the context. Keep it cozy and helpful.\n\n"
        "TOOL USE — you can reach into the terminal's OWN data when the CONTEXT is "
        "insufficient. If you need data not present in CONTEXT (e.g. the latest news "
        "for a specific ticker, a fresh analysis, a price chart, index levels, hedge-fund "
        "filings, Reddit sentiment, or a universe search), emit EXACTLY one line:\n"
        "TOOL_CALL: <tool_name> {json arguments}\n"
        "then stop. You will receive TOOL_RESULT(name): <json>. Read it, call another "
        "tool if still missing data, or give the final answer once you have enough. "
        "Only call a tool when genuinely needed; if CONTEXT already answers the "
        "question, answer directly without a TOOL_CALL."
        + style
        + "\n\n"
        "STRATEGY / ROTATION — when the user asks you to analyze a rotation, "
        "sector, or theme, or to formulate a trading strategy, setup, thesis, or "
        "'what to watch' (e.g. 'Analyze the Healthcare & Biotech rotation — "
        "describe the setup and what to watch'), reason like the terminal's "
        "INVESTMENT COMMITTEE. Call `analyze_rotation` (run `list_themes` first if "
        "unsure of the theme/market) — it already blends the multi-signal "
        "Committee score (LSTM/technical/news, renormalized), breadth, leadership "
        "vs laggards, 13F 'smart money' flows, and the market regime. You can pass "
        "a `market` to scope a rotation to a specific exchange (BSE, LSE, TSE, "
        "XETRA, HKEX) — the basket is derived from that market's own listings, so "
        "rotations work in ANY supported market. Also call `market_regime` for the "
        "macro tape and `research_ticker` to deep-dive a key name's catalysts/"
        "risks. Then synthesize a clear strategy: describe the macro SETUP "
        "(regime + Committee verdict + breadth + leadership), state the THESIS, and "
        "give a concrete WHAT TO WATCH list (confirmation triggers, invalidation "
        "levels, institutional-flow risk, key catalysts). Cite specific tickers "
        "and the data behind your read — never invent prices or verdicts that "
        "aren't in the tool results.\n\n"
        "PORTFOLIO GROUPS — the user can organise securities into named groups "
        "(e.g. 'Growth', 'Watchlist'). When the user asks to add, put, save, move "
        "or remove a ticker in/from a group, CALL `add_to_group` (or "
        "`remove_from_group`) with the ticker and the group name. You do NOT need "
        "a group id — pass the group NAME and it is matched case-insensitively (and "
        "created automatically if it does not exist yet). Resolve the market from "
        "the ticker when you can; if you are unsure of the group, call `list_groups` "
        "first. Always confirm the action back to the user in plain language.\n\n"
        + tools_system_text()
    )
    n_verdicts, n_news = (40, 15) if s == "deep" else (10, 5) if s == "low" else (25, 10)
    ctx_blob = (
        "VERDICTS (market, ticker, verdict, confidence):\n"
        + "\n".join(
            f"- {v.get('market','')} {v.get('ticker','')} {v.get('verdict','')} "
            f"{int(round(float(v.get('confidence',0) or 0)*100))}%"
            for v in context["verdicts"][:n_verdicts]
        )
        + "\n\nRECENT NEWS:\n"
        + "\n".join(f"- {n.get('title','')}" for n in context["news"][:n_news])
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


def _select_provider(p: str, c: dict) -> str | None:
    """Pick a single concrete provider to drive the tool loop (auto = first available)."""
    if p in ("auto", "gemini") and c["gemini_key"]:
        return "gemini"
    if p in ("auto", "ollama") and c["ollama_base"]:
        return "ollama"
    if p in ("auto", "opencode") and c["opencode_base"] and c["opencode_key"]:
        return "opencode"
    return None


def _call_provider(prompt: str, name: str, c: dict, model: str) -> str | None:
    """Invoke one concrete provider and return its raw text (or None on failure)."""
    try:
        if name == "gemini":
            return _try_gemini(prompt, c["gemini_key"], model or c["gemini_model"])
        if name == "ollama":
            return _call_ollama(prompt, c["ollama_base"], model or c["ollama_model"])
        if name == "opencode":
            return _call_openode_chat(
                prompt, c["opencode_base"], c["opencode_key"], model or c["opencode_model"]
            )
    except Exception as exc:  # pragma: no cover - import/network/deps
        logger.warning("Provider %s failed: %s", name, exc)
    return None


def _run_with_tools(
    history: list[dict],
    context: dict,
    provider: str = "auto",
    model: str = "",
    search: str = "",
) -> tuple[str, str] | None:
    """Drive an LLM with the terminal's tool-calling loop.

    The model may emit ``TOOL_CALL:`` lines; each is executed against the live
    terminal and the result is fed back, so the agent can fetch (and reason over)
    real data it didn't already have in CONTEXT. Returns ``(reply, provider_name)``.

    Search modes tune the loop budget: ``deep`` allows many more tool hops for
    exhaustive research, ``low`` caps it to a couple of cheap calls.
    """
    p = (provider or "auto").lower()
    c = _load_env()
    name = _select_provider(p, c)
    if name is None:
        return None

    max_iterations = 5
    if (search or "").lower() == "deep":
        max_iterations = 10
    elif (search or "").lower() == "low":
        max_iterations = 2

    base = _prompt(history, context, search)
    tool_notes: list[str] = []
    reply_text: str | None = None
    for _ in range(max_iterations):
        prompt = (
            base
            if not tool_notes
            else base + "\n\nTOOL RESULTS SO FAR:\n" + "\n".join(tool_notes)
        )
        reply_text = _call_provider(prompt, name, c, model)
        if not reply_text:
            return None
        call = parse_tool_call(reply_text)
        if call is None:
            return reply_text.strip(), name
        tool_name, tool_args = call
        result = run_tool(tool_name, tool_args)
        tool_notes.append(tool_result_block(tool_name, result))
    # Iteration budget exhausted (e.g. model kept calling tools): surface the
    # last response so the user still gets something grounded in the tool data.
    return (reply_text.strip(), name) if reply_text else None


def _try_llm(
    history: list[dict],
    context: dict,
    provider: str = "auto",
    model: str = "",
    search: str = "",
) -> tuple[str, str] | None:
    """Try the requested provider(s) and return ``(reply, provider_name)``.

    Delegates to the tool-calling loop so the agent can fetch missing data from
    the terminal's own tools (NEWS RSS, search, verdicts, …) when needed.
    """
    return _run_with_tools(history, context, provider=provider, model=model, search=search)


def chat(
    messages: list[dict],
    market: str | None = None,
    mode: str = "AUTO",
    provider: str = "auto",
    model: str = "",
    search: str = "",
) -> dict:
    """Respond to a chat turn.

    Routes to the requested LLM provider (gemini / ollama / local / auto);
    ``local`` always uses the data-driven responder, ``auto`` tries any
    configured LLM then falls back. ``search`` selects the search style:
    "" default, "deep" (exhaustive multi-tool research) or "low" (low-token,
    minimal calls). Both the used provider and the available provider config
    are returned so the UI can show live status.
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
        llm = _try_llm(messages, ctx, provider or "auto", model or "", search or "")
        if llm:
            reply, used = llm

    if not reply:
        reply = _local_respond_with_tools(last_user, ctx, market, mode or "AUTO")

    return {
        "role": "assistant",
        "content": reply,
        "provider": used,
        "available": config(),
    }
