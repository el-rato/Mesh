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

Chart.register(...registerables, CandlestickController, CandlestickElement, OhlcController, OhlcElement);

function finite(value) {
  return Number.isFinite(Number(value));
}

function cssColor(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function parseRows(raw) {
  const seen = new Set();
  return (raw || [])
    .filter((r) => r.date && finite(r.close))
    .map((r) => ({
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
      const key = r.time.getTime();
      if (Number.isNaN(key) || seen.has(key)) return false;
      seen.add(key);
      return [r.open, r.high, r.low, r.close].every(Number.isFinite);
    })
    .sort((a, b) => a.time - b.time);
}

function rsiSeries(rows, window = 14) {
  return rows
    .map((row, i) => {
      if (i < window) return { x: row.time, y: null };
      let gains = 0;
      let losses = 0;
      for (let j = i - window + 1; j <= i; j += 1) {
        const change = rows[j].close - rows[j - 1].close;
        if (change >= 0) gains += change;
        else losses -= change;
      }
      if (losses === 0) return { x: row.time, y: 100 };
      const rs = gains / losses;
      return { x: row.time, y: 100 - 100 / (1 + rs) };
    })
    .filter((point) => point.y != null);
}

const currentPricePlugin = {
  id: "currentPriceLine",
  afterDraw(chart, _args, options) {
    if (options?.value == null || !chart.scales.y) return;
    const y = chart.scales.y.getPixelForValue(options.value);
    const area = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.strokeStyle = options.color || "#f5a623";
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(area.left, y);
    ctx.lineTo(area.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = options.color || "#f5a623";
    ctx.font = "10px IBM Plex Mono, monospace";
    ctx.fillText(Number(options.value).toFixed(2), area.left + 4, Math.max(area.top + 12, y - 4));
    ctx.restore();
  },
};

const crosshairPlugin = {
  id: "crosshair",
  afterDraw(chart) {
    if (chart.options.plugins.crosshair === false) return;
    const active = chart.tooltip?.getActiveElements?.();
    if (!active || !active.length) return;
    const element = active[0].element;
    const area = chart.chartArea;
    const ctx = chart.ctx;
    if (element.x < area.left || element.x > area.right) return;
    ctx.save();
    ctx.strokeStyle = "rgba(245,166,35,0.35)";
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(element.x, area.top);
    ctx.lineTo(element.x, area.bottom);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(area.left, element.y);
    ctx.lineTo(area.right, element.y);
    ctx.stroke();
    ctx.restore();
  },
};

export default function PriceChart({
  url,
  height = 120,
  color: lineColor,
  up,
  hideAxes = false,
  candles = false,
  chartType,
  showVolume = false,
  showSma = false,
  showSma50 = showSma,
  showSma200 = false,
  showMomentum = false,
  refreshKey = "",
}) {
  const mainCanvas = useRef(null);
  const subCanvas = useRef(null);
  const mainChart = useRef(null);
  const subChart = useRef(null);
  const dataExtent = useRef(null);
  const dragRef = useRef(null);
  const [visible, setVisible] = useState(false);
  const [rows, setRows] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [view, setView] = useState(null);

  const mode = chartType || (candles ? "candlestick" : "line");
  const financial = mode === "candlestick" || mode === "ohlc";
  const showSubpanel = showVolume || showMomentum;

  useEffect(() => {
    const element = mainCanvas.current;
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
    setView(null);
    fetchJSON(url)
      .then((payload) => {
        if (cancelled) return;
        const parsed = parseRows(payload.data);
        if (!parsed.length) setError(true);
        else setRows(parsed);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [url, visible, refreshKey]);

  useEffect(() => {
    if (!mainCanvas.current || !rows || error) return undefined;
    if (mainChart.current) mainChart.current.destroy();
    if (subChart.current) subChart.current.destroy();

    const bull = cssColor("--bull", "#00e676");
    const bear = cssColor("--bear", "#ff5252");
    const amber = cssColor("--amber", "#f5a623");
    const blue = cssColor("--blue", "#4f9cf9");
    const purple = cssColor("--purple", "#b06bff");
    const muted = cssColor("--text-muted", "#5a6b73");
    const border = cssColor("--border", "#232b30");
    const priceColor = lineColor || (up ? bull : bear);

    // chartjs-chart-financial inverts its color keys: a candle with close < open
    // uses the `up` color and close > open uses `down`. Swap them so up candles
    // are green (close > open) and down candles are red, matching convention.
    const candleColors = { up: bear, down: bull, unchanged: amber };

    const mainDataset = financial
      ? {
          type: mode,
          label: "PRICE",
          data: rows.map((r) => ({ x: r.time, o: r.open, h: r.high, l: r.low, c: r.close, volume: r.volume })),
          color: candleColors,
          borderColor: candleColors,
          borderWidth: 1,
          barPercentage: 0.95,
          categoryPercentage: 1,
        }
      : {
          type: "line",
          label: mode === "area" ? "AREA" : "PRICE",
          data: rows.map((r) => ({ x: r.time, y: r.close })),
          borderColor: priceColor,
          backgroundColor: mode === "area" ? `${priceColor}33` : priceColor,
          fill: mode === "area",
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.12,
        };
    const mainDatasets = [mainDataset];
    if (showSma50) {
      mainDatasets.push({ type: "line", label: "SMA 50", data: rows.filter((r) => finite(r.sma50)).map((r) => ({ x: r.time, y: r.sma50 })), borderColor: blue, borderWidth: 1.5, pointRadius: 0, tension: 0 });
    }
    if (showSma200) {
      mainDatasets.push({ type: "line", label: "SMA 200", data: rows.filter((r) => finite(r.sma200)).map((r) => ({ x: r.time, y: r.sma200 })), borderColor: purple, borderWidth: 1.5, pointRadius: 0, tension: 0 });
    }

    const dataMin = rows[0].time.getTime();
    const dataMax = rows[rows.length - 1].time.getTime();
    dataExtent.current = { min: dataMin, max: dataMax };

    const xScaleConfig = {
      type: "time",
      offset: true,
      display: !hideAxes,
      grid: { color: `${border}66` },
      ticks: {
        color: muted,
        maxTicksLimit: 10,
        font: { size: 9, family: "IBM Plex Mono" },
        maxRotation: 0,
        autoSkip: true,
      },
      time: {
        displayFormats: {
          minute: "HH:mm",
          hour: "HH:mm",
          day: "MMM d",
          week: "MMM d",
          month: "MMM yyyy",
        },
      },
      min: view?.min,
      max: view?.max,
    };

    mainChart.current = new Chart(mainCanvas.current, {
      type: financial ? mode : "line",
      data: { datasets: mainDatasets },
      plugins: [currentPricePlugin, crosshairPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        interaction: { mode: "index", intersect: false },
        onHover: (event) => {
          const chart = mainChart.current;
          if (!chart) return;
          const scale = chart.scales.x;
          if (scale && event.native?.offsetX != null) {
            chart.canvas.style.cursor = "crosshair";
          }
        },
        plugins: {
          currentPriceLine: { value: rows[rows.length - 1].close, color: amber },
          crosshair: true,
          legend: { display: !hideAxes && (showSma50 || showSma200), labels: { color: muted, boxWidth: 12, font: { size: 10, family: "IBM Plex Mono" } } },
          tooltip: {
            enabled: !hideAxes,
            mode: "index",
            intersect: false,
            backgroundColor: "rgba(7,9,11,0.92)",
            borderColor: border,
            borderWidth: 1,
            titleColor: amber,
            bodyColor: muted,
            titleFont: { size: 10, family: "IBM Plex Mono" },
            bodyFont: { size: 10, family: "IBM Plex Mono" },
            callbacks: {
              title(context) {
                const raw = context[0]?.raw;
                if (!raw || !(raw.x instanceof Date)) return "";
                return raw.x.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
              },
              label(context) {
                const value = context.raw;
                if (financial && context.dataset.label === "PRICE") {
                  return `O ${value.o.toFixed(2)}  H ${value.h.toFixed(2)}  L ${value.l.toFixed(2)}  C ${value.c.toFixed(2)}`;
                }
                return ` ${context.dataset.label}: ${Number(value.y).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
              },
              afterLabel(context) {
                if (financial && context.dataset.label === "PRICE" && finite(context.raw?.volume)) {
                  return `VOL ${Number(context.raw.volume).toLocaleString()}`;
                }
                return undefined;
              },
            },
          },
        },
        scales: {
          x: xScaleConfig,
          y: { display: !hideAxes, position: "right", grid: { color: `${border}66` }, ticks: { color: muted, font: { size: 9, family: "IBM Plex Mono" } } },
        },
      },
    });

    if (showSubpanel && subCanvas.current) {
      const datasets = [];
      if (showVolume) {
        datasets.push({ type: "bar", label: "VOLUME", yAxisID: "volume", data: rows.map((r) => ({ x: r.time, y: r.volume })), backgroundColor: rows.map((r) => (r.close >= r.open ? `${bull}99` : `${bear}99`)), barPercentage: 1, categoryPercentage: 1 });
      }
      if (showMomentum) {
        datasets.push({ type: "line", label: "RSI 14", yAxisID: "momentum", data: rsiSeries(rows), borderColor: purple, backgroundColor: `${purple}22`, borderWidth: 1.5, pointRadius: 0, tension: 0.15 });
      }
      subChart.current = new Chart(subCanvas.current, {
        type: "line",
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          parsing: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: { display: !hideAxes, labels: { color: muted, boxWidth: 12, font: { size: 9, family: "IBM Plex Mono" } } },
            tooltip: { enabled: !hideAxes, mode: "index", intersect: false, backgroundColor: "rgba(7,9,11,0.92)", titleFont: { size: 9, family: "IBM Plex Mono" }, bodyFont: { size: 9, family: "IBM Plex Mono" } },
          },
          scales: {
            x: { type: "time", display: !hideAxes, offset: true, grid: { color: `${border}44` }, ticks: { color: muted, maxTicksLimit: 8, font: { size: 8, family: "IBM Plex Mono" }, maxRotation: 0 } },
            volume: { display: showVolume && !hideAxes, position: "left", grid: { drawOnChartArea: false }, ticks: { color: muted, maxTicksLimit: 3, font: { size: 8, family: "IBM Plex Mono" } } },
            momentum: { display: showMomentum && !hideAxes, position: "right", min: 0, max: 100, grid: { color: `${border}44` }, ticks: { color: purple, stepSize: 20, font: { size: 8, family: "IBM Plex Mono" } } },
          },
        },
      });
    }

    const canvas = mainCanvas.current;
    const onWheel = (event) => {
      event.preventDefault();
      const chart = mainChart.current;
      if (!chart || !dataExtent.current) return;
      const xScale = chart.scales.x;
      const centerValue = xScale.getValueForPixel(event.offsetX);
      const span = xScale.max - xScale.min;
      const factor = event.deltaY > 0 ? 1.25 : 0.8;
      const newSpan = Math.max(span * factor, 30 * 60 * 1000);
      let newMin = centerValue - (centerValue - xScale.min) * factor;
      let newMax = newMin + newSpan;
      if (newMin < dataExtent.current.min) {
        newMin = dataExtent.current.min;
        newMax = newMin + newSpan;
      }
      if (newMax > dataExtent.current.max) {
        newMax = dataExtent.current.max;
        newMin = newMax - newSpan;
      }
      if (newMax - newMin >= dataExtent.current.max - dataExtent.current.min) {
        setView(null);
        return;
      }
      setView({ min: newMin, max: newMax });
    };
    const onPointerDown = (event) => {
      const chart = mainChart.current;
      if (!chart) return;
      const xScale = chart.scales.x;
      dragRef.current = { startX: event.offsetX, startMin: xScale.min, startMax: xScale.max, active: true };
    };
    const onPointerMove = (event) => {
      const drag = dragRef.current;
      const chart = mainChart.current;
      if (!drag?.active || !chart || !dataExtent.current) return;
      const xScale = chart.scales.x;
      const delta = xScale.getValueForPixel(event.offsetX) - xScale.getValueForPixel(drag.startX);
      let newMin = drag.startMin - delta;
      let newMax = drag.startMax - delta;
      const extent = dataExtent.current;
      if (newMin < extent.min) {
        newMin = extent.min;
        newMax = newMin + (drag.startMax - drag.startMin);
      }
      if (newMax > extent.max) {
        newMax = extent.max;
        newMin = newMax - (drag.startMax - drag.startMin);
      }
      setView({ min: newMin, max: newMax });
    };
    const onPointerUp = () => {
      if (dragRef.current) dragRef.current.active = false;
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);

    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      mainChart.current?.destroy();
      subChart.current?.destroy();
      mainChart.current = null;
      subChart.current = null;
    };
  }, [rows, error, mode, showVolume, showSma50, showSma200, showMomentum, hideAxes, lineColor, up]);

  useEffect(() => {
    const chart = mainChart.current;
    if (!chart || !rows) return;
    if (view) {
      chart.options.scales.x.min = view.min;
      chart.options.scales.x.max = view.max;
    } else {
      chart.options.scales.x.min = undefined;
      chart.options.scales.x.max = undefined;
    }
    chart.update();
  }, [view, rows]);

  return (
    <div className={`chart-shell ${showSubpanel ? "chart-with-momentum" : ""}`} style={{ width: "100%", height }}>
      <div className="chart-main-canvas"><canvas ref={mainCanvas} /></div>
      {showSubpanel && <div className="chart-sub-canvas"><canvas ref={subCanvas} /></div>}
      {loading && <div className="chart-loading">LOADING CHART…</div>}
      {error && <div className="chart-loading">CHART UNAVAILABLE</div>}
    </div>
  );
}
