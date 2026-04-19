/* ── StockPulse – Frontend Logic ──────────────────────────────── */

const API = "";
let currentSymbol = "";
let priceChart = null;
let zscoreChart = null;
let volumeChart = null;

// ── DOM refs ────────────────────────────────────────────────────
const $ = (s) => document.querySelector(s);
const searchInput = $("#search-input");
const searchBtn = $("#search-btn");
const loader = $("#loader");
const dashboard = $("#dashboard");
const periodSelect = $("#period-select");
const zThreshold = $("#z-threshold");
const zWindow = $("#z-window");
const refreshBtn = $("#refresh-btn");
const alertSymbol = $("#alert-symbol");
const alertType = $("#alert-type");
const alertThreshold = $("#alert-threshold");
const createAlertBtn = $("#create-alert-btn");
const clearTriggeredBtn = $("#clear-triggered-btn");
const bellCount = $("#bell-count");

// ── Toast ───────────────────────────────────────────────────────
function showToast(message, type = "success") {
  const container = $("#toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const iconSvg =
    type === "success"
      ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
      : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
  toast.innerHTML = `<span class="toast-icon">${iconSvg}</span><span class="toast-text">${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("removing");
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// ── Formatting helpers ──────────────────────────────────────────
function formatNumber(n) {
  if (n == null) return "—";
  if (n >= 1e12) return (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return n.toLocaleString();
}

// ── Chart colors ────────────────────────────────────────────────
const COLORS = {
  line: "#818cf8",
  lineFill: "rgba(129,140,248,.08)",
  anomalyRed: "#ef4444",
  anomalyGreen: "#10b981",
  grid: "rgba(255,255,255,.04)",
  zeroLine: "rgba(255,255,255,.08)",
  volume: "rgba(99,102,241,.35)",
  zLine: "#a78bfa",
  zFill: "rgba(167,139,250,.08)",
};

// ── Fetch stock data ────────────────────────────────────────────
async function fetchStock(symbol) {
  currentSymbol = symbol.toUpperCase();
  alertSymbol.value = currentSymbol;
  dashboard.style.display = "none";
  loader.style.display = "flex";

  const period = periodSelect.value;
  const zt = zThreshold.value;
  const zw = zWindow.value;

  try {
    const res = await fetch(
      `${API}/api/stock/${symbol}?period=${period}&z_threshold=${zt}&z_window=${zw}`
    );
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Failed to fetch");
    }
    const data = await res.json();
    renderDashboard(data);
    loadTriggeredAlerts();
    showToast(`Loaded ${data.company_name} (${data.symbol})`);
  } catch (e) {
    showToast(e.message, "error");
    loader.style.display = "none";
  }
}

// ── Render Dashboard ────────────────────────────────────────────
function renderDashboard(data) {
  loader.style.display = "none";
  dashboard.style.display = "block";

  // Stat cards
  $("#stat-price-val").textContent = `$${data.current_price.toFixed(2)}`;
  const changeEl = $("#stat-price-change");
  const sign = data.price_change >= 0 ? "+" : "";
  changeEl.textContent = `${sign}$${data.price_change.toFixed(2)} (${sign}${data.price_change_pct.toFixed(2)}%)`;
  changeEl.className = `stat-change ${data.price_change >= 0 ? "positive" : "negative"}`;

  $("#stat-zscore-val").textContent = data.latest_zscore.toFixed(2);
  const zStatus = $("#stat-zscore-status");
  if (data.is_anomaly) {
    zStatus.textContent = "⚠ ANOMALY";
    zStatus.style.color = "var(--red)";
    $("#stat-zscore").style.borderColor = "rgba(239,68,68,.3)";
  } else {
    zStatus.textContent = "Normal range";
    zStatus.style.color = "var(--green)";
    $("#stat-zscore").style.borderColor = "var(--border)";
  }

  $("#stat-anomalies-val").textContent = data.anomaly_count;
  $("#stat-anomalies-period").textContent = `in selected period`;

  const avgVol =
    data.data.reduce((s, d) => s + d.volume, 0) / data.data.length;
  $("#stat-volume-val").textContent = formatNumber(Math.round(avgVol));
  $("#stat-sector").textContent = data.sector || "N/A";

  // Anomaly banner
  const banner = $("#anomaly-banner");
  if (data.is_anomaly) {
    banner.style.display = "flex";
    const latest = data.data[data.data.length - 1];
    $("#anomaly-banner-detail").textContent = `Latest Z-Score: ${data.latest_zscore.toFixed(2)} | Price: $${data.current_price.toFixed(2)} — This ${latest.anomaly_type === "spike" ? "spike" : "drop"} exceeds the ±${data.z_threshold} threshold.`;
  } else {
    banner.style.display = "none";
  }

  $("#price-chart-badge").textContent = data.company_name;

  // Charts
  renderPriceChart(data);
  renderZScoreChart(data);
  renderVolumeChart(data);

  // Anomaly table
  renderAnomalyTable(data);
}

// ── Price Chart ─────────────────────────────────────────────────
function renderPriceChart(data) {
  const ctx = $("#price-chart").getContext("2d");
  if (priceChart) priceChart.destroy();

  const labels = data.data.map((d) => d.date);
  const prices = data.data.map((d) => d.close);
  const anomalyPoints = data.data
    .map((d, i) => (d.anomaly ? { x: i, y: d.close, type: d.anomaly_type, z: d.zscore } : null))
    .filter(Boolean);

  priceChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Close Price",
          data: prices,
          borderColor: COLORS.line,
          backgroundColor: COLORS.lineFill,
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHoverRadius: 5,
          borderWidth: 2,
        },
        {
          label: "Anomaly (Spike)",
          data: anomalyPoints.filter((p) => p.type === "spike").map((p) => ({ x: labels[p.x], y: p.y })),
          type: "scatter",
          pointRadius: 7,
          pointBackgroundColor: COLORS.anomalyRed,
          pointBorderColor: "#fff",
          pointBorderWidth: 2,
          showLine: false,
        },
        {
          label: "Anomaly (Drop)",
          data: anomalyPoints.filter((p) => p.type === "drop").map((p) => ({ x: labels[p.x], y: p.y })),
          type: "scatter",
          pointRadius: 7,
          pointBackgroundColor: COLORS.anomalyGreen,
          pointBorderColor: "#fff",
          pointBorderWidth: 2,
          showLine: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, position: "top", labels: { color: "#8888a0", font: { size: 11 }, usePointStyle: true, pointStyle: "circle" } },
        tooltip: {
          backgroundColor: "rgba(22,22,31,.95)",
          titleColor: "#e8e8f0",
          bodyColor: "#8888a0",
          borderColor: "rgba(30,30,46,1)",
          borderWidth: 1,
          cornerRadius: 8,
          padding: 12,
        },
      },
      scales: {
        x: { ticks: { color: "#55556a", maxTicksLimit: 12, font: { size: 10 } }, grid: { color: COLORS.grid } },
        y: { ticks: { color: "#55556a", font: { size: 10 }, callback: (v) => "$" + v.toFixed(0) }, grid: { color: COLORS.grid } },
      },
    },
  });
}

// ── Z-Score Chart ───────────────────────────────────────────────
function renderZScoreChart(data) {
  const ctx = $("#zscore-chart").getContext("2d");
  if (zscoreChart) zscoreChart.destroy();

  const labels = data.data.map((d) => d.date);
  const zscores = data.data.map((d) => d.zscore);
  const threshold = data.z_threshold;

  const colors = zscores.map((z) => {
    if (z === null) return COLORS.zLine;
    if (z > threshold) return COLORS.anomalyRed;
    if (z < -threshold) return COLORS.anomalyGreen;
    return COLORS.zLine;
  });

  zscoreChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Z-Score",
          data: zscores,
          backgroundColor: colors.map((c) => c + "88"),
          borderColor: colors,
          borderWidth: 1,
          borderRadius: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        annotation: {
          annotations: {
            upperLine: { type: "line", yMin: threshold, yMax: threshold, borderColor: "rgba(239,68,68,.5)", borderWidth: 1, borderDash: [6, 4], label: { display: true, content: `+${threshold}`, position: "end", backgroundColor: "rgba(239,68,68,.15)", color: "#ef4444", font: { size: 10 } } },
            lowerLine: { type: "line", yMin: -threshold, yMax: -threshold, borderColor: "rgba(16,185,129,.5)", borderWidth: 1, borderDash: [6, 4], label: { display: true, content: `-${threshold}`, position: "end", backgroundColor: "rgba(16,185,129,.15)", color: "#10b981", font: { size: 10 } } },
            zeroLine: { type: "line", yMin: 0, yMax: 0, borderColor: COLORS.zeroLine, borderWidth: 1 },
          },
        },
        tooltip: { backgroundColor: "rgba(22,22,31,.95)", titleColor: "#e8e8f0", bodyColor: "#8888a0", borderColor: "rgba(30,30,46,1)", borderWidth: 1, cornerRadius: 8, padding: 12 },
      },
      scales: {
        x: { ticks: { color: "#55556a", maxTicksLimit: 10, font: { size: 10 } }, grid: { color: COLORS.grid } },
        y: { ticks: { color: "#55556a", font: { size: 10 } }, grid: { color: COLORS.grid } },
      },
    },
  });
}

// ── Volume Chart ────────────────────────────────────────────────
function renderVolumeChart(data) {
  const ctx = $("#volume-chart").getContext("2d");
  if (volumeChart) volumeChart.destroy();

  const labels = data.data.map((d) => d.date);
  const volumes = data.data.map((d) => d.volume);

  volumeChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Volume",
          data: volumes,
          backgroundColor: COLORS.volume,
          borderRadius: 3,
          borderSkipped: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: "rgba(22,22,31,.95)", titleColor: "#e8e8f0", bodyColor: "#8888a0", borderColor: "rgba(30,30,46,1)", borderWidth: 1, cornerRadius: 8, padding: 12, callbacks: { label: (ctx) => "Vol: " + formatNumber(ctx.raw) } },
      },
      scales: {
        x: { ticks: { color: "#55556a", maxTicksLimit: 10, font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { color: "#55556a", font: { size: 10 }, callback: (v) => formatNumber(v) }, grid: { color: COLORS.grid } },
      },
    },
  });
}

// ── Anomaly Table ───────────────────────────────────────────────
function renderAnomalyTable(data) {
  const tbody = $("#anomaly-table-body");
  const anomalies = data.data.filter((d) => d.anomaly);
  const noAnomalies = $("#no-anomalies");
  const table = $("#anomaly-table");

  $("#anomaly-count-badge").textContent = `${anomalies.length} anomal${anomalies.length === 1 ? "y" : "ies"}`;

  if (anomalies.length === 0) {
    table.style.display = "none";
    noAnomalies.style.display = "flex";
    return;
  }

  table.style.display = "table";
  noAnomalies.style.display = "none";

  tbody.innerHTML = anomalies
    .reverse()
    .map((d) => {
      const absZ = Math.abs(d.zscore);
      let severity = "Low";
      let sevClass = "severity-low";
      if (absZ > 3) { severity = "High"; sevClass = "severity-high"; }
      else if (absZ > 2.5) { severity = "Medium"; sevClass = "severity-medium"; }
      const typeClass = d.anomaly_type === "spike" ? "type-spike" : "type-drop";
      const arrow = d.anomaly_type === "spike" ? "↑" : "↓";
      return `<tr>
        <td>${d.date}</td>
        <td>$${d.close.toFixed(2)}</td>
        <td style="font-family:var(--font-mono)">${d.zscore.toFixed(2)}</td>
        <td><span class="${typeClass}">${arrow} ${d.anomaly_type}</span></td>
        <td><span class="${sevClass}">${severity}</span></td>
      </tr>`;
    })
    .join("");
}

// ── Alert CRUD ──────────────────────────────────────────────────
async function loadAlerts() {
  try {
    const res = await fetch(`${API}/api/alerts`);
    const data = await res.json();
    renderAlerts(data.alerts);
  } catch (e) { /* ignore */ }
}

function renderAlerts(alerts) {
  const list = $("#alerts-list");
  const empty = $("#no-alerts");
  if (alerts.length === 0) {
    list.innerHTML = "";
    empty.style.display = "flex";
    return;
  }
  empty.style.display = "none";
  list.innerHTML = alerts
    .map((a) => {
      const typeLabel = { price_above: "Price ≥", price_below: "Price ≤", anomaly: "Any Anomaly", zscore_above: "Z ≥", zscore_below: "Z ≤" }[a.type] || a.type;
      const thresholdText = a.type === "anomaly" ? "" : ` ${ a.type.startsWith("price") ? "$" : "" }${a.threshold}`;
      return `<div class="alert-item" id="alert-item-${a.id}">
        <div class="alert-item-info">
          <span class="alert-item-title">${a.symbol} — ${typeLabel}${thresholdText}</span>
          <span class="alert-item-detail">${a.active ? "🟢 Active" : "⚪ Inactive"} · Created ${new Date(a.created_at).toLocaleDateString()}</span>
        </div>
        <button class="alert-delete" onclick="deleteAlert(${a.id})" title="Delete alert">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>
      </div>`;
    })
    .join("");
}

async function createAlert() {
  const symbol = alertSymbol.value.trim().toUpperCase();
  const type = alertType.value;
  const threshold = parseFloat(alertThreshold.value);

  if (!symbol) return showToast("Enter a symbol", "error");
  if (type !== "anomaly" && (isNaN(threshold) || threshold <= 0))
    return showToast("Enter a valid threshold", "error");

  try {
    const res = await fetch(`${API}/api/alerts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, type, threshold: type === "anomaly" ? 0 : threshold }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error);
    }
    showToast(`Alert created for ${symbol}`);
    alertThreshold.value = "";
    loadAlerts();
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function deleteAlert(id) {
  try {
    await fetch(`${API}/api/alerts/${id}`, { method: "DELETE" });
    showToast("Alert deleted");
    loadAlerts();
  } catch (e) {
    showToast("Failed to delete", "error");
  }
}

// ── Triggered Alerts ────────────────────────────────────────────
async function loadTriggeredAlerts() {
  try {
    const res = await fetch(`${API}/api/alerts/triggered`);
    const data = await res.json();
    renderTriggered(data.triggered);
  } catch (e) { /* ignore */ }
}

function renderTriggered(items) {
  const list = $("#triggered-list");
  const empty = $("#no-triggered");
  bellCount.textContent = items.length;

  if (items.length === 0) {
    list.innerHTML = "";
    empty.style.display = "flex";
    return;
  }
  empty.style.display = "none";
  list.innerHTML = items
    .reverse()
    .map(
      (t) => `<div class="triggered-item">
        <div class="triggered-dot"></div>
        <div class="triggered-info">
          <div class="triggered-reason">${t.reason}</div>
          <div class="triggered-meta">${t.symbol} · $${t.price.toFixed(2)} · Z: ${t.zscore.toFixed(2)} · ${new Date(t.triggered_at).toLocaleString()}</div>
        </div>
      </div>`
    )
    .join("");
}

async function clearTriggered() {
  try {
    await fetch(`${API}/api/alerts/triggered/clear`, { method: "POST" });
    showToast("Cleared triggered alerts");
    loadTriggeredAlerts();
  } catch (e) { /* ignore */ }
}

// ── Toggle threshold input ──────────────────────────────────────
alertType.addEventListener("change", () => {
  const group = $("#threshold-group");
  group.style.display = alertType.value === "anomaly" ? "none" : "flex";
});

// ── Event Listeners ─────────────────────────────────────────────
searchBtn.addEventListener("click", () => {
  const sym = searchInput.value.trim();
  if (sym) fetchStock(sym);
});
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const sym = searchInput.value.trim();
    if (sym) fetchStock(sym);
  }
});
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const sym = chip.dataset.symbol;
    searchInput.value = sym;
    fetchStock(sym);
  });
});
refreshBtn.addEventListener("click", () => {
  if (currentSymbol) fetchStock(currentSymbol);
});
periodSelect.addEventListener("change", () => {
  if (currentSymbol) fetchStock(currentSymbol);
});
createAlertBtn.addEventListener("click", createAlert);
clearTriggeredBtn.addEventListener("click", clearTriggered);

// ── Initial load ────────────────────────────────────────────────
loadAlerts();
loadTriggeredAlerts();
