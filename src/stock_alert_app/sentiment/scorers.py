from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

POSITIVE_WORDS = {
    "beat", "beats", "surge", "surges", "surged", "rally", "rallies", "rallied",
    "soar", "soars", "soared", "jump", "jumps", "jumped", "gain", "gains", "gained",
    "rise", "rises", "rose", "record", "strong", "stronger", "outperform", "outperforms",
    "upgrade", "upgrades", "upgraded", "buy", "overweight", "growth", "grew", "profit",
    "profits", "profitable", "earnings beat", "exceed", "exceeds", "exceeded",
    "bullish", "positive", "recover", "recovers", "recovered", "recovery",
    "opportunity", "opportunities", "expansion", "expands", "expanded", "boost",
    "boosts", "boosted", "milestone", "breakthrough", "win", "wins", "dividend",
    "dividends", "raised", "raise", "raises", "improve", "improves", "improved",
    "best", "highest", "success", "successful", "momentum", "climb", "climbs",
    "climbed", "accelerate", "accelerates", "accelerated", "top", "up", "higher",
    "new high", "all-time high", "optimistic", "award", "awards",
    "partnership", "partners", "launches", "launch", "launched", "unveils", "unveiled",
    "debut", "approval", "approved", "approves", "winning", "demand", "strong demand",
    "return", "returns", "positive outlook", "cheap", "undervalued", "sold out",
    "good", "great", "excellent", "superior", "dominant", "market leader",
}

NEGATIVE_WORDS = {
    "plunge", "plunges", "plunged", "crash", "crashes", "crashed", "slump", "slumps",
    "slumped", "drop", "drops", "dropped", "fall", "falls", "fell", "decline",
    "declines", "declined", "loss", "losses", "lost", "miss", "misses", "missed",
    "misses estimates", "downgrade", "downgrades", "downgraded", "sell", "underweight",
    "weak", "weaker", "weakness", "bearish", "negative", "lawsuit", "lawsuits", "sued",
    "probe", "probes", "investigation", "investigations", "fined", "fine", "fraud",
    "scandal", "scandals", "recall", "recalls", "recalled", "layoff", "layoffs",
    "fired", "resign", "resigns", "resigned", "exits", "exit", "shut", "shutdown",
    "shuts", "bankrupt", "bankruptcy", "insolvent", "default", "defaults", "defaulted",
    "debt", "restructuring", "worst", "lowest", "bear", "bears", "underperform",
    "underperforms", "risk", "risks", "threat", "threats", "uncertainty", "concern",
    "concerns", "worry", "worries", "caution", "cautions", "cautious", "warning",
    "warns", "warned", "trouble", "troubled", "struggle", "struggles", "struggled",
    "headwind", "headwinds", "drag", "drags", "dragged", "pressure", "pressures",
    "cuts", "cut", "cutting", "reduces", "reduce", "reduced", "slashes", "slash",
    "halt", "halts", "halted", "suspension", "suspended", "delisted",
    "delisting", "fraudulent", "misconduct", "penalty", "penalties", "sanctions",
    "sanctioned", "tariff", "tariffs", "trade war", "recession", "slowdown", "shrinks",
    "shrink", "shrank", "low", "down", "lower", "worst-ever", "selloff", "sell-off",
    "dip", "dips", "dipped", "wiped", "tumble", "tumbles", "tumbled", "nosedive",
    "nosedives", "freefall", "collapse", "collapses", "collapsed", "bankruptcy risk",
}

NEGATORS = {"not", "no", "never", "without", "despite", "misses", "missed", "against", "below", "under"}

AMP = {"and", "but", "or", "while", "as", "however", "despite", "although"}


@dataclass(frozen=True)
class SentimentResult:
    score: float
    label: str
    positive: float
    negative: float
    neutral: float

    def as_dict(self) -> dict[str, float | str]:
        return {
            "score": self.score,
            "label": self.label,
            "positive": self.positive,
            "negative": self.negative,
            "neutral": self.neutral,
        }


class Scorer(Protocol):
    name: str

    def score(self, text: str) -> SentimentResult: ...


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class LexiconScorer:
    """Zero-dependency keyword sentiment scorer."""

    name = "lexicon"

    def __init__(self) -> None:
        self._pos = {w.lower() for w in POSITIVE_WORDS}
        self._neg = {w.lower() for w in NEGATIVE_WORDS}

    def score(self, text: str) -> SentimentResult:
        if not text:
            return SentimentResult(0.0, "neutral", 0.0, 0.0, 1.0)

        words = re.findall(r"[a-zA-Z][a-zA-Z-]+", text.lower())
        pos = neg = 0
        negated_window = False
        for word in words:
            if word in NEGATORS:
                negated_window = not negated_window
            if word in self._pos:
                pos += 0 if negated_window else 1
                negated_window = False
            elif word in self._neg:
                neg += 1 if negated_window else 0
                negated_window = False
            else:
                if word in AMP:
                    negated_window = False

        total = pos + neg
        if total == 0:
            return SentimentResult(0.0, "neutral", 0.0, 0.0, 1.0)

        raw = (pos - neg) / total
        score = _clamp(raw)
        if score > 0.15:
            label = "positive"
        elif score < -0.15:
            label = "negative"
        else:
            label = "neutral"
        return SentimentResult(
            score=round(score, 4),
            label=label,
            positive=round(pos / total, 4),
            negative=round(neg / total, 4),
            neutral=0.0,
        )


class FinBERTScorer:
    """Transformer-based financial sentiment. Requires torch + transformers installed."""

    name = "finbert"

    def __init__(self) -> None:
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "FinBERTScorer requires 'torch' and 'transformers'. "
                "Install with: uv add torch transformers"
            ) from exc

        self._model_name = "ProsusAI/finbert"
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(self._model_name)

    def score(self, text: str) -> SentimentResult:
        if not text:
            return SentimentResult(0.0, "neutral", 0.0, 0.0, 1.0)

        import torch  # type: ignore

        inputs = self._tokenizer(text[:1024], return_tensors="pt", truncation=True)
        with torch.no_grad():
            logits = self._model(**inputs).logits
        probs = torch.softmax(logits, dim=-1).squeeze().tolist()
        positive, negative, neutral = probs

        score = _clamp(positive - negative)
        if score > 0.15:
            label = "positive"
        elif score < -0.15:
            label = "negative"
        else:
            label = "neutral"
        return SentimentResult(
            score=round(score, 4),
            label=label,
            positive=round(positive, 4),
            negative=round(negative, 4),
            neutral=round(neutral, 4),
        )


class LLMScorer:
    """Optional LLM-based scorer for nuance. Requires LLM_API_KEY + provider package."""

    name = "llm"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise RuntimeError("LLMScorer requires an LLM_API_KEY")
        self._api_key = api_key
        self._model = model

    def score(self, text: str) -> SentimentResult:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "LLMScorer requires the 'openai' package. Install with: uv add openai"
            ) from exc

        client = OpenAI(api_key=self._api_key)
        prompt = (
            "You are a financial sentiment analyst. Score the sentiment of this "
            "financial news headline/summary from -1 (very bearish) to +1 (very bullish). "
            "Return ONLY JSON: {\"score\": float, \"label\": \"positive|negative|neutral\"}\n\n"
            f"Text: {text[:2000]}"
        )
        resp = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = (resp.choices[0].message.content or "").strip()
        import json

        data = json.loads(content)
        score = _clamp(float(data.get("score", 0.0)))
        label = data.get("label", "neutral")
        if score > 0.15 and label == "neutral":
            label = "positive"
        elif score < -0.15 and label == "neutral":
            label = "negative"
        return SentimentResult(
            score=round(score, 4),
            label=label,
            positive=max(score, 0.0),
            negative=max(-score, 0.0),
            neutral=1.0 - abs(score),
        )


class OllamaScorer:
    """Local LLM-based scorer using Ollama (e.g., Gemma, Llama, etc.)."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "gemma4:latest") -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    def score(self, text: str) -> SentimentResult:
        import json
        import urllib.request

        prompt = (
            "You are a financial sentiment analyst. Score the sentiment of this "
            "financial news headline/summary from -1 (very bearish) to +1 (very bullish). "
            "Return ONLY JSON: {\"score\": float, \"label\": \"positive|negative|neutral\"}\n\n"
            f"Text: {text[:2000]}"
        )

        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 100},
        }

        req = urllib.request.Request(
            f"{self._base_url}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())

        response_text = data.get("response", "").strip()

        # Parse JSON from response
        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from text
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start != -1 and end != -1:
                result = json.loads(response_text[start:end + 1])
            else:
                raise RuntimeError(f"Ollama returned non-JSON: {response_text[:200]}")

        score = _clamp(float(result.get("score", 0.0)))
        label = result.get("label", "neutral")
        if score > 0.15 and label == "neutral":
            label = "positive"
        elif score < -0.15 and label == "neutral":
            label = "negative"
        return SentimentResult(
            score=round(score, 4),
            label=label,
            positive=max(score, 0.0),
            negative=max(-score, 0.0),
            neutral=1.0 - abs(score),
        )
