import { useEffect, useRef, useState } from "react";
import { Chart, registerables } from "chart.js";
import {
  CandlestickController,
  CandlestickElement,
  OhlcController,
  OhlcElement,
} from "chartjs-chart-financial";
import "chartjs-adapter-date-fns";
import { fetchJSON } from "../api.js";

Chart.register(
  ...registerables,
  CandlestickController,
  CandlestickElement,
  OhlcController,
  OhlcElement,
);

function finite(value) {
  return Number.isFinite(Number(value));
}

function color(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function parseRows(raw) {
  const seen = new Set();
  return (raw || [])
    .filter((r) => r.date && finite(r.close))
    .map((r) => ({
      ...r,
      time: new Date(r.date),
      close: Number(r.close),
      open: Number(r.open),
      high: Number(r.high),
      low: Number(r.low),
      volume: Number(r.volume || 0),
      sma50: Number(r.sma_50),
      sma200: Number(r.sma_200),
    }))
    .filter((r) => {
      if (Number.isNaN(r.time.getTime()) || seen.has(r.time.getTime())) return false;
      if (!finite(r.open) || !finite(r.high) || !finite(r.low)) return false;
      seen.add(r.time.getTime());
      return true;
    })
    .sort((a, b) => a.time - b.time);
}

export default function PriceChart({
  url,
  height = 120,
  color: lineColor,
  up,
  hideAxes = false,
  candles = false,
  showVolume = false,
  showSma = false,
}) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);
  const [visible, setVisible] = useState(false);
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const element = canvasRef.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "200px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible) return undefined;
    let cancelled = false;
    setLoading(true);
    setError(false);
    setRows(null);
    fetchJSON(url)
      .then((payload) => {
        if (cancelled) return;
        const parsed = parseRows(payload.data);
        if (!parsed.length) {
          setError(true);
          setLoading(false);
          return;
        }
        setRows(parsed);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [url, visible]);

  useEffect(() => {
    if (!canvasRef.current || !rows || error) return undefined;
    if (chartRef.current) chartRef.current.destroy();

    const bull = color("--bull", "#00e676");
    const bear = color("--bear", "#ff5252");
    const amber = color("--amber", "#f5a623");
    const blue = color("--blue", "#4f9cf9");
    const purple = color("--purple", "#b06bff");
    const muted = color("--text-muted", "#5a6b73");
    const border = color("--border", "#232b30");
    const line = lineColor || (up ? bull : bear);

    const datasets = candles
      ? [{
          type: "candlestick",
          label: "PRICE",
          data: rows.map((r) => ({ x: r.time, o: r.open, h: r.high, l: r.low, c: r.close })),
          color: { up: bull, down: bear, unchanged: amber },
          borderColor: { up: bull, down: bear, unchanged: amber },
        }]
      : [{
          type: "line",
          label: "PRICE",
          data: rows.map((r) => ({ x: r.time, y: r.close })),
          borderColor: line,
          backgroundColor: line,
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.12,
        }];

    if (showSma) {
      datasets.push(
        {
          type: "line",
          label: "SMA 50",
          data: rows.filter((r) => finite(r.sma50)).map((r) => ({ x: r.time, y: r.sma50 })),
          borderColor: blue,
          borderWidth: 1,
          pointRadius: 0,
          tension: 0,
        },
        {
          type: "line",
          label: "SMA 200",
          data: rows.filter((r) => finite(r.sma200)).map((r) => ({ x: r.time, y: r.sma200 })),
          borderColor: purple,
          borderWidth: 1,
          pointRadius: 0,
          tension: 0,
        },
      );
    }
    if (showVolume) {
      datasets.push({
        type: "bar",
        label: "VOLUME",
        yAxisID: "volume",
        data: rows.map((r) => ({ x: r.time, y: r.volume })),
        backgroundColor: rows.map((r) => r.close >= r.open ? `${bull}66` : `${bear}66`),
        borderWidth: 0,
        barPercentage: 1,
        categoryPercentage: 1,
      });
    }

    chartRef.current = new Chart(canvasRef.current, {
      type: candles ? "candlestick" : "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: !hideAxes && (showSma || showVolume), labels: { color: muted, boxWidth: 12, font: { size: 10, family: "IBM Plex Mono" } } },
          tooltip: {
            enabled: !hideAxes,
            callbacks: {
              label(context) {
                const value = context.raw;
                if (context.dataset.label === "PRICE" && value.o != null) {
                  return ` O ${value.o.toFixed(2)} H ${value.h.toFixed(2)} L ${value.l.toFixed(2)} C ${value.c.toFixed(2)}`;
                }
                return ` ${context.dataset.label}: ${Number(value.y).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
              },
            },
          },
        },
        scales: {
          x: {
            type: "time",
            display: !hideAxes,
            grid: { color: `${border}66` },
            ticks: { color: muted, maxTicksLimit: 7, font: { size: 9, family: "IBM Plex Mono" } },
          },
          y: {
            display: !hideAxes,
            position: "right",
            grid: { color: `${border}66` },
            ticks: { color: muted, font: { size: 9, family: "IBM Plex Mono" } },
          },
          volume: {
            display: showVolume && !hideAxes,
            position: "left",
            grid: { drawOnChartArea: false },
            ticks: { color: muted, font: { size: 8, family: "IBM Plex Mono" }, maxTicksLimit: 3 },
            suggestedMax: Math.max(...rows.map((r) => r.volume || 0), 1) * 4,
          },
        },
      },
    });

    return () => {
      if (chartRef.current) {
        chartRef.current.destroy();
        chartRef.current = null;
      }
    };
  }, [rows, error, candles, showVolume, showSma, hideAxes, lineColor, up]);

  return (
    <div className="chart-shell" style={{ width: "100%", height }}>
      <canvas ref={canvasRef} />
      {loading && <div className="chart-loading">LOADING CHART…</div>}
      {error && <div className="chart-loading">CHART UNAVAILABLE</div>}
    </div>
  );
}
