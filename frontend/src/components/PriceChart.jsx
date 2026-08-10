import { useEffect, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";
import { fetchJSON } from "../api.js";

// Convert "YYYY-MM-DD HH:MM" (UTC) -> epoch seconds for lightweight-charts.
function toTime(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(s || "");
  if (m) {
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000;
  }
  const d = Date.parse(s);
  return isNaN(d) ? Math.floor(Date.now() / 1000) : Math.floor(d / 1000);
}

export default function PriceChart({
  url,
  height = 120,
  color,
  up,
  hideAxes = false,
  candles = false,
}) {
  const ref = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  useEffect(() => {
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
    });

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

    if (hideAxes) {
      chart.applyOptions({
        rightPriceScale: { visible: false },
        timeScale: { visible: false },
      });
    }

    return () => chart.remove();
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series) return;
    let cancelled = false;
    fetchJSON(url)
      .then((d) => {
        if (cancelled) return;
        const rows = (d.data || [])
          .filter((r) => r.close != null)
          .map((r) =>
            candles
              ? {
                  time: toTime(r.date),
                  open: r.open,
                  high: r.high,
                  low: r.low,
                  close: r.close,
                }
              : { time: toTime(r.date), value: r.close }
          );
        if (!rows.length) return;
        series.setData(rows);
        chart.timeScale().fitContent();
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [url, candles]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    if (!candles) series.applyOptions({ color: color || (up ? "#00e676" : "#ff5252") });
  }, [color, up, candles]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
