from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent / "sentiment_lstm"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

VOCAB_FILE = MODEL_DIR / "vocab.json"
MODEL_FILE = MODEL_DIR / "sentiment_lstm.pt"
CONFIG_FILE = MODEL_DIR / "config.json"

MAX_VOCAB = 20000
MAX_SEQ = 64
PAD_IDX = 0
UNK_IDX = 1
TOKEN_RE = re.compile(r"[a-z0-9$%&.,'-]+|[^a-z0-9$%&.,'-]")

LABELS = ["negative", "neutral", "positive"]
LABEL_TO_IDX = {label: i for i, label in enumerate(LABELS)}


@dataclass
class LSTMSentimentConfig:
    vocab_size: int = MAX_VOCAB
    embed_dim: int = 128
    hidden_size: int = 128
    num_layers: int = 2
    dropout: float = 0.3
    max_seq: int = MAX_SEQ

    def as_dict(self) -> dict:
        return {
            "vocab_size": self.vocab_size,
            "embed_dim": self.embed_dim,
            "hidden_size": self.hidden_size,
            "num_layers": self.num_layers,
            "dropout": self.dropout,
            "max_seq": self.max_seq,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LSTMSentimentConfig:
        return cls(
            vocab_size=data.get("vocab_size", MAX_VOCAB),
            embed_dim=data.get("embed_dim", 128),
            hidden_size=data.get("hidden_size", 128),
            num_layers=data.get("num_layers", 2),
            dropout=data.get("dropout", 0.3),
            max_seq=data.get("max_seq", MAX_SEQ),
        )


class SentimentLSTM(nn.Module):
    """Embedding + BiLSTM + attention-free mean-pooled classifier for sentiment."""

    def __init__(self, config: LSTMSentimentConfig) -> None:
        super().__init__()
        self.config = config
        self.embedding = nn.Embedding(
            config.vocab_size, config.embed_dim, padding_idx=PAD_IDX
        )
        self.lstm = nn.LSTM(
            config.embed_dim,
            config.hidden_size,
            config.num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=config.dropout if config.num_layers > 1 else 0,
        )
        self.dropout = nn.Dropout(config.dropout)
        self.fc = nn.Linear(config.hidden_size * 2, len(LABELS))

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        emb = self.embedding(x)
        packed = nn.utils.rnn.pack_padded_sequence(
            emb, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        output, _ = self.lstm(packed)
        output, _ = nn.utils.rnn.pad_packed_sequence(output, batch_first=True)
        mask = (x != PAD_IDX).unsqueeze(-1).float()
        summed = (output * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1)
        pooled = summed / counts
        return self.fc(self.dropout(pooled))


class SentimentTokenizer:
    def __init__(self, vocab: dict[str, int]) -> None:
        self.vocab = vocab
        self.itos = {i: w for w, i in vocab.items()}

    @classmethod
    def build(cls, texts: list[str], max_vocab: int = MAX_VOCAB) -> SentimentTokenizer:
        counter: Counter[str] = Counter()
        for text in texts:
            counter.update(_tokenize(text))
        most = counter.most_common(max_vocab - 2)
        vocab = {"<PAD>": PAD_IDX, "<UNK>": UNK_IDX}
        for word, _ in most:
            vocab[word] = len(vocab)
        return cls(vocab)

    def encode(self, text: str, max_seq: int = MAX_SEQ) -> tuple[list[int], int]:
        tokens = _tokenize(text)[:max_seq]
        ids = [self.vocab.get(tok, UNK_IDX) for tok in tokens]
        return ids, len(ids)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.vocab), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> SentimentTokenizer:
        return cls(json.loads(path.read_text(encoding="utf-8")))


def _tokenize(text: str) -> list[str]:
    text = text.lower().strip()
    if not text:
        return []
    return [tok for tok in TOKEN_RE.findall(text) if tok.strip()]


def _collate(
    texts: list[str], tokenizer: SentimentTokenizer, max_seq: int
) -> tuple[torch.Tensor, torch.Tensor]:
    encoded = [tokenizer.encode(t, max_seq) for t in texts]
    max_len = max((ln for _, ln in encoded), default=1)
    batch_ids = np.zeros((len(encoded), max_len), dtype=np.int64)
    lengths: list[int] = []
    for i, (ids, ln) in enumerate(encoded):
        batch_ids[i, :ln] = ids
        lengths.append(ln)
    return (
        torch.from_numpy(batch_ids).to(DEVICE),
        torch.tensor(lengths, dtype=torch.long).to(DEVICE),
    )


def _load_training_data(
    db_path: str | None = None, max_rows: int = 30000
) -> list[tuple[str, str]]:
    """Load (text, label) pairs from the sentiment_scores table, preferring FinBERT
    labels when multiple models scored the same article."""
    from ..config import settings
    from ..db import Database

    db = Database(db_path or settings.db_path)
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT n.title, n.summary, s.label
               FROM news_items n
               JOIN (
                   SELECT news_item_id, label,
                          ROW_NUMBER() OVER (
                              PARTITION BY news_item_id
                              ORDER BY CASE model WHEN 'finbert' THEN 0 ELSE 1 END
                          ) AS rn
                   FROM sentiment_scores
               ) s ON s.news_item_id = n.id AND s.rn = 1
               WHERE s.label IN ('positive', 'negative', 'neutral')
               ORDER BY RANDOM()
               LIMIT ?""",
            (max_rows,),
        ).fetchall()
    data: list[tuple[str, str]] = []
    for title, summary, label in rows:
        text = f"{title or ''} {summary or ''}".strip()
        if text:
            data.append((text, label))
    return data


def train_sentiment_lstm(
    db_path: str | None = None,
    epochs: int = 5,
    batch_size: int = 64,
    lr: float = 1e-3,
    max_rows: int = 30000,
) -> dict[str, float]:
    """Train the LSTM sentiment model on labeled data stored in the database."""
    data = _load_training_data(db_path, max_rows)
    if len(data) < 200:
        raise RuntimeError(
            f"Not enough labeled data to train (found {len(data)}). "
            "Run the sentiment pipeline first to populate labels."
        )

    labels = [label for _, label in data]
    counts = Counter(labels)
    logger.info("Training data: %d rows (%s)", len(data), dict(counts))
    weights = torch.tensor(
        [1.0 / max(counts.get(label, 1), 1) for label in LABELS],
        dtype=torch.float32,
        device=DEVICE,
    )
    weights = weights / weights.sum()

    texts = [t for t, _ in data]
    tokenizer = SentimentTokenizer.build(texts)
    config = LSTMSentimentConfig(vocab_size=len(tokenizer.vocab))
    model = SentimentLSTM(config).to(DEVICE)

    y = torch.tensor([LABEL_TO_IDX[label] for _, label in data], dtype=torch.long).to(
        DEVICE
    )

    n = len(data)
    split = int(n * 0.9)
    perm = np.random.permutation(n)
    train_idx, val_idx = perm[:split], perm[split:]

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss(weight=weights)

    def make_batches(indices: list[int], shuffle: bool):
        if shuffle:
            indices = list(indices)
            np.random.shuffle(indices)
        for i in range(0, len(indices), batch_size):
            yield indices[i : i + batch_size]

    best_val_acc = 0.0
    for epoch in range(epochs):
        model.train()
        total_loss = 0.0
        num_batches = 0
        for batch in make_batches(list(train_idx), shuffle=True):
            b_texts = [texts[i] for i in batch]
            b_y = y[batch]
            x, lengths = _collate(b_texts, tokenizer, config.max_seq)
            optimizer.zero_grad()
            logits = model(x, lengths)
            loss = criterion(logits, b_y)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            optimizer.step()
            total_loss += loss.item()
            num_batches += 1

        val_acc = _evaluate(model, tokenizer, config, texts, y, list(val_idx))
        logger.info(
            "Epoch %d/%d: loss=%.4f val_acc=%.4f",
            epoch + 1,
            epochs,
            total_loss / max(num_batches, 1),
            val_acc,
        )
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            _save_model(model, tokenizer, config)

    tokenizer.save(VOCAB_FILE)
    logger.info(
        "Saved sentiment LSTM to %s (best val_acc=%.4f)", MODEL_DIR, best_val_acc
    )
    return {"best_val_acc": float(best_val_acc), "train_rows": n}


def _evaluate(
    model: nn.Module,
    tokenizer: SentimentTokenizer,
    config: LSTMSentimentConfig,
    texts: list[str],
    y: torch.Tensor,
    indices: list[int],
) -> float:
    if not indices:
        return 0.0
    model.eval()
    correct = 0
    with torch.no_grad():
        for i in range(0, len(indices), 64):
            batch = indices[i : i + 64]
            b_texts = [texts[j] for j in batch]
            b_y = y[batch]
            x, lengths = _collate(b_texts, tokenizer, config.max_seq)
            logits = model(x, lengths)
            preds = logits.argmax(dim=1)
            correct += (preds == b_y).sum().item()
    model.train()
    return correct / len(indices)


def _save_model(
    model: nn.Module, tokenizer: SentimentTokenizer, config: LSTMSentimentConfig
) -> None:
    torch.save(model.state_dict(), MODEL_FILE)
    CONFIG_FILE.write_text(json.dumps(config.as_dict()), encoding="utf-8")
    tokenizer.save(VOCAB_FILE)


def load_sentiment_lstm() -> tuple[
    SentimentLSTM | None, SentimentTokenizer | None, LSTMSentimentConfig | None
]:
    if not MODEL_FILE.exists():
        return None, None, None
    try:
        config = LSTMSentimentConfig.from_dict(
            json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        )
        model = SentimentLSTM(config).to(DEVICE)
        model.load_state_dict(torch.load(MODEL_FILE, map_location=DEVICE))
        model.eval()
        tokenizer = SentimentTokenizer.load(VOCAB_FILE)
        return model, tokenizer, config
    except Exception as exc:
        logger.warning("Failed to load sentiment LSTM: %s", exc)
        return None, None, None


def predict_sentiment(text: str) -> tuple[list[float], str, str]:
    """Run inference. Returns (probs, label, model_id)."""
    model, tokenizer, config = load_sentiment_lstm()
    if model is None:
        raise RuntimeError(
            "No trained sentiment LSTM found. Run train_sentiment_lstm first."
        )
    if not text:
        return [0.0, 1.0, 0.0], "neutral", "lstm"
    x, lengths = _collate([text], tokenizer, config.max_seq)
    with torch.no_grad():
        logits = model(x, lengths)
        probs = torch.softmax(logits, dim=-1).squeeze(0).tolist()
    label = LABELS[int(torch.argmax(logits, dim=-1).item())]
    return probs, label, "lstm"
