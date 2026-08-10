import { useEffect, useState, useCallback } from "react";
import { fetchJSON } from "../api.js";
import { useApp } from "../App.jsx";
import PriceChart from "./PriceChart.jsx";
import { fmtNum, Row } from "./ui.jsx";

export default function IndexesTab() {
  const { market, refreshToken } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [range, setRange] = useState("1mo");

  const load = useCallback(() => {
    const q = market ? `?market=${encodeURIComponent(market)}` : "";
    fetchJSON("/api/indexes" + q)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [market]);

  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    if (refreshToken) load();
  }, [refreshToken]);

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const q = market ? `?market=${encodeURIComponent(market)}` : "";
      const d = await fetchJSON("/api/indexes/refresh" + q);
      setData(d);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="controls">
        <button className="primary" onClick={refresh} disabled={loading}>
          {loading ? "FETCHING…" : "⟳ REFRESH INDEXES"}
        </button>
        <div className="field">
          <label>Range</label>
          <select value={range} onChange={(e) => setRange(e.target.value)}>
            <option value="1d">1D</option>
            <option value="1w">1W</option>
            <option value="1mo">1MO</option>
            <option value="1y">1Y</option>
            <option value="all">ALL</option>
          </select>
        </div>
      </div>

      {error && <div className="error">ERROR: {error}</div>}
      {loading && <div className="empty">FETCHING INDEX DATA… <span className="spinner" /></div>}
      {data && !data.length && <div className="empty">NO INDEX SNAPSHOTS YET — CLICK REFRESH.</div>}

      {data && data.length > 0 && (
        <div className="grid">
          {data.map((s) => {
            const up = s.change_pct >= 0;
            return (
              <div key={s.symbol} className={`panel ${up ? "bull" : "bear"}`}>
                <div className="panel-head">
                  <div>
                    <div className="symbol">{s.name}</div>
                    <div className="name">
                      {s.market} · {s.symbol}
                    </div>
                  </div>
                  <span className={`badge ${up ? "bull" : "bear"}`}>
                    {up ? "+" : ""}
                    {(s.change_pct * 100).toFixed(2)}%
                  </span>
                </div>
                <Row k="CLOSE" v={fmtNum(s.close)} />
                <Row k="CHANGE" v={`${up ? "+" : ""}${(s.change_pct * 100).toFixed(2)}%`} cls={up ? "up" : "down"} />
                <Row
                  k="O / H / L"
                  v={`${fmtNum(s.open, 0)} / ${fmtNum(s.high, 0)} / ${fmtNum(s.low, 0)}`}
                />
                <div style={{ marginTop: 8, height: 110 }}>
                  <PriceChart
                    url={`/api/indexes/${encodeURIComponent(s.symbol)}/history?range=${range}`}
                    up={up}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
