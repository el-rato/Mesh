import { useCallback, useEffect, useMemo, useState } from "react";
import { newsFeed } from "../api.js";
import { useApp } from "../App.jsx";
import SecurityLink from "./SecurityLink.jsx";
import { RefreshStatus } from "./ui.jsx";

function timeLabel(iso) {
  if (!iso) return "";
  const s = String(iso);
  return s.length >= 19 ? s.slice(11, 19) : s;
}

function dateLabel(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch (e) {
    return String(iso).slice(0, 10);
  }
}

function sentClass(label) {
  const l = String(label || "").toLowerCase();
  if (l === "positive" || l === "bullish") return "up";
  if (l === "negative" || l === "bearish") return "down";
  return "neutral";
}

const SORTS = [
  { key: "newest", label: "NEWEST" },
  { key: "relevance", label: "RELEVANCE" },
];

const SENTIMENT_FILTERS = [
  { key: "all", label: "ALL" },
  { key: "positive", label: "BULLISH" },
  { key: "negative", label: "BEARISH" },
  { key: "neutral", label: "NEUTRAL" },
];

export default function NewsTab() {
  const { refreshToken, refreshStatus, openDrawer } = useApp();
  const [items, setItems] = useState(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("newest");
  const [sentFilter, setSentFilter] = useState("all");
  const [selected, setSelected] = useState(null);

  const load = useCallback(() => {
    setError("");
    newsFeed(150)
      .then((data) => setItems(data || []))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken, load]);

  const filtered = useMemo(() => {
    if (!items) return [];
    let out = items;
    const q = query.trim().toLowerCase();
    if (q) {
      out = out.filter(
        (n) =>
          (n.title || "").toLowerCase().includes(q) ||
          (n.ticker || "").toLowerCase().includes(q) ||
          (n.market || "").toLowerCase().includes(q) ||
          (n.source || "").toLowerCase().includes(q)
      );
    }
    if (sentFilter !== "all") {
      out = out.filter((n) => {
        const l = String(n.sentiment_label || "").toLowerCase();
        if (sentFilter === "positive") return l === "positive" || l === "bullish";
        if (sentFilter === "negative") return l === "negative" || l === "bearish";
        if (sentFilter === "neutral") return !l || l === "neutral";
        return true;
      });
    }
    if (sort === "newest") {
      out = [...out].sort((a, b) => {
        const ta = a.published_at || a.fetched_at || "";
        const tb = b.published_at || b.fetched_at || "";
        return tb.localeCompare(ta);
      });
    } else {
      out = [...out].sort(
        (a, b) => Math.abs(Number(b.sentiment_score) || 0) - Math.abs(Number(a.sentiment_score) || 0)
      );
    }
    return out;
  }, [items, query, sort, sentFilter]);

  useEffect(() => {
    if (filtered.length && !selected) setSelected(filtered[0]);
  }, [filtered, selected]);

  const openDossier = (n) =>
    openDrawer({
      type: "stock",
      v: { market: n.market || "", ticker: n.ticker || "", company: "", reason: ["NEWS FEED"] },
    });

  return (
    <div className="news-tab">
      {/* Command bar */}
      <div className="news-commandbar">
        <input
          className="news-search"
          placeholder="Search headlines, tickers, sources…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="news-pills">
          {SORTS.map((s) => (
            <button
              key={s.key}
              className={`news-pill ${sort === s.key ? "active" : ""}`}
              onClick={() => setSort(s.key)}
            >
              {s.label}
            </button>
          ))}
          <span className="news-pill-sep" />
          {SENTIMENT_FILTERS.map((s) => (
            <button
              key={s.key}
              className={`news-pill ${sentFilter === s.key ? "active" : ""}`}
              onClick={() => setSentFilter(s.key)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <RefreshStatus status={refreshStatus} />
        <span className="news-count dim">{filtered.length} ARTICLES</span>
      </div>

      {error && <div className="scan-warning">⚠ {error}</div>}

      <div className="news-body">
        {/* Feed list */}
        <div className="news-feed">
          {!items ? (
            <div className="empty">LOADING NEWS…</div>
          ) : filtered.length === 0 ? (
            <div className="empty">NO ARTICLES MATCH — ADJUST SEARCH OR RUN A REFRESH.</div>
          ) : (
            filtered.map((n, i) => {
              const isSel = selected && selected.url === n.url && selected.title === n.title;
              return (
                <div
                  key={`${n.url}-${i}`}
                  className={`news-feed-item ${isSel ? "selected" : ""}`}
                  onClick={() => setSelected(n)}
                >
                  <div className="news-feed-time dim">{timeLabel(n.published_at || n.fetched_at)}</div>
                  <div className="news-feed-main">
                    <div className="news-feed-headline">{n.title}</div>
                    <div className="news-feed-meta">
                      <SecurityLink market={n.market} ticker={n.ticker} className="news-feed-tk">
                        {n.security_id || `${n.market}:${n.ticker}`}
                      </SecurityLink>
                      <span className="dim">· {n.source}</span>
                      {n.sentiment_label && (
                        <span className={`news-sent ${sentClass(n.sentiment_label)}`}>
                          {n.sentiment_label.toUpperCase()}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Detail panel */}
        <div className="news-detail">
          {!selected ? (
            <div className="empty" style={{ padding: 40 }}>SELECT AN ARTICLE TO PREVIEW.</div>
          ) : (
            <>
              <div className="news-detail-head">
                <div className="news-detail-title">{selected.title}</div>
                <div className="news-detail-meta">
                  <SecurityLink market={selected.market} ticker={selected.ticker}>
                    {selected.security_id || `${selected.market}:${selected.ticker}`}
                  </SecurityLink>
                  <span className="dim">· {selected.source} · {dateLabel(selected.published_at || selected.fetched_at)} {timeLabel(selected.published_at || selected.fetched_at)}</span>
                  {selected.sentiment_label && (
                    <span className={`news-sent ${sentClass(selected.sentiment_label)}`}>
                      {selected.sentiment_label.toUpperCase()}
                    </span>
                  )}
                </div>
              </div>

              {selected.summary && (
                <div className="news-detail-summary">{selected.summary}</div>
              )}

              <div className="news-detail-actions">
                {selected.url && (
                  <a
                    className="primary news-open-btn"
                    href={selected.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    OPEN ARTICLE ⟶
                  </a>
                )}
                <button className="ghost" onClick={() => openDossier(selected)}>OPEN DOSSIER</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
