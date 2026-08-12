import { useEffect, useRef, useState } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";

function badgeCls(v) {
  return v === "BULL" ? "bull" : v === "BEAR" ? "bear" : "neutral";
}

export default function SearchBox() {
  const { openDrawer, addToPortfolio, removeFromPortfolio, inPortfolio } = useApp();
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    const query = q.trim();
    if (!query) {
      setResults(null);
      setSearching(false);
      setError("");
      return;
    }
    setSearching(true);
    setError("");
    const t = setTimeout(() => {
      fetchJSON(`/api/search?q=${encodeURIComponent(query)}`)
        .then((r) => {
          setResults(Array.isArray(r) ? r : []);
          setSearching(false);
        })
        .catch((e) => {
          setError(e.message);
          setResults([]);
          setSearching(false);
        });
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const onDoc = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const choose = (r) => {
    if (r.supported === false) return;
    setOpen(false);
    setQ("");
    setResults(null);
    setError("");
    openDrawer({
      type: "stock",
      v: {
        market: r.market,
        ticker: r.ticker,
        symbol: r.symbol || r.ticker,
        company: r.company || "",
        exchange: r.exchange || "",
        quote_type: r.quote_type || "",
        featured: !!r.featured,
        verdict: r.verdict || "NEUTRAL",
        confidence: r.confidence || 0,
        news_score: 0,
        price_score: 0,
        combined_score: r.combined_score || 0,
        reason: r.reason ? [r.reason] : ["SEARCH RESULT — RUN PRICE FETCH TO REFRESH THE VERDICT"],
      },
    });
  };

  const hasQuery = q.trim().length > 0;

  return (
    <div className="search-box" ref={boxRef}>
      <input
        type="text"
        className="search-input"
        placeholder="Search ticker / company…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => hasQuery && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results && results.length) choose(results[0]);
          if (e.key === "Escape") setOpen(false);
        }}
        aria-label="Search stocks"
      />
      {open && hasQuery && (
        <div className="search-drop">
          {error && <div className="search-msg">ERROR: {error}</div>}
          {!error && searching && <div className="search-msg">SEARCHING…</div>}
          {!error && !searching && results && results.length === 0 && (
            <div className="search-msg">NO MATCHES FOR “{q.trim()}”</div>
          )}
          {!error && !searching && results && results.length > 0 && (
            <div className="search-results">
              {results.map((r) => {
                const unsupported = r.supported === false;
                return (
                  <button
                    key={`${r.market || ""}:${r.ticker || ""}:${r.symbol || ""}`}
                    className={`search-item ${unsupported ? "disabled" : ""}`}
                    disabled={unsupported}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      choose(r);
                    }}
                  >
                    <span className="t">{r.ticker}</span>
                    <span className="n">{r.company || r.symbol || r.market || ""}</span>
                    <span className="m">
                      {r.market || "—"}
                      {r.quote_type && r.quote_type !== "EQUITY" ? ` · ${r.quote_type}` : ""}
                    </span>
                    {r.featured && <span className="badge featured">FEATURED</span>}
                    {r.verdict && (
                      <span className={`badge ${badgeCls(r.verdict)}`}>{r.verdict}</span>
                    )}
                    {unsupported && <span className="badge avoid">UNSUPPORTED</span>}
                    {!unsupported && r.market && r.ticker && (
                      <span
                        className={`search-add ${inPortfolio(r.market, r.ticker) ? "on" : ""}`}
                        title={inPortfolio(r.market, r.ticker) ? "In portfolio — click to remove" : "Add to portfolio"}
                        onMouseDown={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          if (inPortfolio(r.market, r.ticker)) removeFromPortfolio(r.market, r.ticker);
                          else addToPortfolio(r.market, r.ticker, r.company || "");
                        }}
                      >
                        {inPortfolio(r.market, r.ticker) ? "✓" : "+"}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}