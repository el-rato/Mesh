import { useEffect, useRef, useState } from "react";
import { fetchJSON } from "../api.js";

let createChart = null;
let ColorType = null;
let chartsImported = false;

async function importCharts() {
  if (!chartsImported) {
    const mod = await import("lightweight-charts");
    createChart = mod.createChart;
    ColorType = mod.ColorType;
    chartsImported = true;
  }
}

// Convert "YYYY-MM-DD HH:MM" (UTC) -> epoch seconds for lightweight-charts.
function toTime(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s || "");
  if (m) {
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000;
  }
  const d = Date.parse(s);
  return isNaN(d) ? null : Math.floor(d / 1000);
}

export default function PriceChart({
  url,
  height = 120,
  color,
  up,
  hideAxes = false,
  candles = false,
  showVolume = false,
  showSma = false,
}) {
  const ref = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const volumeRef = useRef(null);
  const sma50Ref = useRef(null);
  const sma200Ref = useRef(null);
  const [visible, setVisible] = useState(false);
  const [initError, setInitError] = useState(false);
  const [dataError, setDataError] = useState(false);
  const [chartReady, setChartReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const createdRef = useRef(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: "200px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || createdRef.current || initError) return;
    let mounted = true;

    async function initChart() {
      try {
        await importCharts();
        if (!mounted || !ref.current) return;
        const chart = createChart(ref.current, {
          height,
          layout: {
            background: { type: ColorType.Solid, color: "transparent" },
            textColor: "#8ba0ab",
            fontSize: 10,
            fontFamily: "'IBM Plex Mono', monospace",
          },
          grid: {
            vertLines: { color: "rgba(35,43,48,0.4)" },
            horzLines: { color: "rgba(35,43,48,0.4)" },
          },
          rightPriceScale: { borderVisible: false },
          timeScale: { borderVisible: false },
          crosshair: {
            mode: 1,
            vertLine: { color: "#f5a623", width: 1, style: 3, labelBackgroundColor: "#f5a623" },
            horzLine: { color: "#f5a623", width: 1, style: 3, labelBackgroundColor: "#f5a623" },
          },
          handleScroll: hideAxes ? false : true,
          handleScale: hideAxes ? false : true,
          logo: { show: false },
        });

        if (!mounted) {
          chart.remove();
          return;
        }

        chartRef.current = chart;
        seriesRef.current = candles
          ? chart.addCandlestickSeries({
              upColor: "#00e676",
              downColor: "#ff5252",
              borderVisible: false,
              wickUpColor: "#00e676",
              wickDownColor: "#ff5252",
            })
          : chart.addLineSeries({
              color: color || "#f5a623",
              lineWidth: 2,
              priceLineVisible: false,
              crosshairMarkerRadius: 3,
            });

        if (showVolume) {
          volumeRef.current = chart.addHistogramSeries({
            color: "rgba(79, 156, 249, 0.35)",
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
          });
          chart.priceScale("volume").applyOptions({
            scaleMargins: { top: 0.78, bottom: 0 },
          });
        }
        if (showSma) {
          sma50Ref.current = chart.addLineSeries({
            color: "#4f9cf9",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
          });
          sma200Ref.current = chart.addLineSeries({
            color: "#b06bff",
            lineWidth: 1,
            priceLineVisible: false,
            lastValueVisible: false,
          });
        }
        if (hideAxes) {
          chart.applyOptions({
            rightPriceScale: { visible: false },
            timeScale: { visible: false },
          });
        }

        createdRef.current = true;
        setChartReady(true);
      } catch (e) {
        console.error("Chart init failed:", e);
        setInitError(true);
      }
    }

    initChart();
    return () => {
      mounted = false;
      if (chartRef.current) {
        try { chartRef.current.remove(); } catch {}
        chartRef.current = null;
      }
      seriesRef.current = null;
      volumeRef.current = null;
      sma50Ref.current = null;
      sma200Ref.current = null;
      createdRef.current = false;
      setChartReady(false);
    };
  }, [visible, height, hideAxes, candles, showVolume, showSma, initError]);

  useEffect(() => {
    const chart = chartRef.current;
    const element = ref.current;
    if (!chart || !element || !chartReady || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: Math.floor(entry.contentRect.width) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [chartReady]);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series || !visible || !chartReady || initError) return;
    let cancelled = false;
    setDataError(false);
    setLoading(true);
    fetchJSON(url)
      .then((d) => {
        if (cancelled) return;
        const raw = d.data || [];
        const mapped = raw
          .filter((r) => r.close != null)
          .map((r) => {
            const point = candles
              ? {
                  time: toTime(r.date),
                  open: Number(r.open),
                  high: Number(r.high),
                  low: Number(r.low),
                  close: Number(r.close),
                }
              : { time: toTime(r.date), value: Number(r.close) };
            return {
              point,
              open: Number(r.open),
              close: Number(r.close),
              volume: Number(r.volume || 0),
              sma50: Number(r.sma_50),
              sma200: Number(r.sma_200),
            };
          })
          .filter((r) => {
            const point = r.point;
            const values = candles
              ? [point.open, point.high, point.low, point.close]
              : [point.value];
            return point.time != null && values.every(Number.isFinite);
          })
          .sort((a, b) => a.point.time - b.point.time);
        const seen = new Set();
        const rows = mapped.filter((r) => {
          if (seen.has(r.point.time)) return false;
          seen.add(r.point.time);
          return true;
        });
        if (!rows.length) {
          setLoading(false);
          setDataError(true);
          return;
        }
        series.setData(rows.map((r) => r.point));
        if (volumeRef.current) {
          volumeRef.current.setData(rows.map((r) => ({
              time: r.point.time,
              value: r.volume,
              color: r.close >= r.open
                ? "rgba(0, 230, 118, 0.28)"
                : "rgba(255, 82, 82, 0.28)",
            })).filter((r) => Number.isFinite(r.value)));
        }
        if (sma50Ref.current) {
          sma50Ref.current.setData(rows.map((r) => ({ time: r.point.time, value: r.sma50 }))
            .filter((r) => Number.isFinite(r.value)));
        }
        if (sma200Ref.current) {
          sma200Ref.current.setData(rows.map((r) => ({ time: r.point.time, value: r.sma200 }))
            .filter((r) => Number.isFinite(r.value)));
        }
        chart.timeScale().fitContent();
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
        setDataError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [url, candles, visible, chartReady, initError]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !visible || initError) return;
    if (!candles) series.applyOptions({ color: color || (up ? "#00e676" : "#ff5252") });
  }, [color, up, candles, visible, initError]);

  if (initError || dataError) {
    return <div style={{ width: "100%", height, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-muted)", fontSize: 10 }}>CHART UNAVAILABLE</div>;
  }

  return (
    <div className="chart-shell" style={{ width: "100%", height }}>
      <div ref={ref} style={{ width: "100%", height }} />
      {loading && <div className="chart-loading">LOADING CHART…</div>}
    </div>
  );
}
