"""
Stock Alert App with Z-Score Anomaly Detection
"""

import json
import os
from datetime import datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory, render_template
import yfinance as yf
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR)

alerts_store: list[dict] = []
alert_id_counter = 0
triggered_alerts: list[dict] = []



def compute_zscore(series: pd.Series, window: int = 20) -> pd.Series:
    """Rolling Z-score: (x - rolling_mean) / rolling_std"""
    rolling_mean = series.rolling(window=window).mean()
    rolling_std = series.rolling(window=window).std()
    zscore = (series - rolling_mean) / rolling_std
    return zscore


def fetch_stock_data(symbol: str, period: str = "3mo", interval: str = "1d"):

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return None, "No data found for symbol"
        return df, None
    except Exception as e:
        return None, str(e)


def detect_anomalies(df: pd.DataFrame, zscore_threshold: float = 2.0, window: int = 20):

    df = df.copy()
    df["zscore"] = compute_zscore(df["Close"], window=window)
    df["anomaly"] = df["zscore"].abs() > zscore_threshold
    df["anomaly_type"] = df.apply(
        lambda row: "spike" if row["zscore"] > zscore_threshold
        else ("drop" if row["zscore"] < -zscore_threshold else "normal"),
        axis=1,
    )
    return df


def check_alerts(symbol: str, current_price: float, zscore_val: float, is_anomaly: bool):
    global triggered_alerts
    now = datetime.now().isoformat()

    for alert in alerts_store:
        if not alert["active"] or alert["symbol"].upper() != symbol.upper():
            continue

        triggered = False
        reason = ""

        if alert["type"] == "price_above" and current_price >= alert["threshold"]:
            triggered = True
            reason = f"Price ${current_price:.2f} is above ${alert['threshold']:.2f}"
        elif alert["type"] == "price_below" and current_price <= alert["threshold"]:
            triggered = True
            reason = f"Price ${current_price:.2f} is below ${alert['threshold']:.2f}"
        elif alert["type"] == "anomaly" and is_anomaly:
            triggered = True
            reason = f"Z-score anomaly detected (z={zscore_val:.2f})"
        elif alert["type"] == "zscore_above" and zscore_val >= alert["threshold"]:
            triggered = True
            reason = f"Z-score {zscore_val:.2f} is above {alert['threshold']:.2f}"
        elif alert["type"] == "zscore_below" and zscore_val <= -alert["threshold"]:
            triggered = True
            reason = f"Z-score {zscore_val:.2f} is below -{alert['threshold']:.2f}"

        if triggered:
            trigger_event = {
                "alert_id": alert["id"],
                "symbol": symbol.upper(),
                "price": current_price,
                "zscore": round(zscore_val, 4),
                "reason": reason,
                "triggered_at": now,
                "alert_type": alert["type"],
            }
            triggered_alerts.append(trigger_event)
            # One-shot alerts get deactivated
            if alert.get("one_shot", True):
                alert["active"] = False


# ── API Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stock/<symbol>")
def get_stock(symbol):
    """Get stock data with Z-score anomaly detection."""
    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")
    z_threshold = float(request.args.get("z_threshold", 2.0))
    z_window = int(request.args.get("z_window", 20))

    df, error = fetch_stock_data(symbol, period, interval)
    if error:
        return jsonify({"error": error}), 404

    df = detect_anomalies(df, zscore_threshold=z_threshold, window=z_window)

    # Get current price and latest zscore
    current_price = float(df["Close"].iloc[-1])
    latest_zscore = float(df["zscore"].iloc[-1]) if not np.isnan(df["zscore"].iloc[-1]) else 0.0
    is_anomaly = bool(df["anomaly"].iloc[-1])

    # Check alerts
    check_alerts(symbol, current_price, latest_zscore, is_anomaly)

    # Get ticker info
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        company_name = info.get("shortName", symbol.upper())
        market_cap = info.get("marketCap", None)
        sector = info.get("sector", "N/A")
        prev_close = info.get("previousClose", None)
    except Exception:
        company_name = symbol.upper()
        market_cap = None
        sector = "N/A"
        prev_close = None

    # Build response
    records = []
    for idx, row in df.iterrows():
        records.append({
            "date": idx.strftime("%Y-%m-%d"),
            "open": round(float(row["Open"]), 2),
            "high": round(float(row["High"]), 2),
            "low": round(float(row["Low"]), 2),
            "close": round(float(row["Close"]), 2),
            "volume": int(row["Volume"]),
            "zscore": round(float(row["zscore"]), 4) if not np.isnan(row["zscore"]) else None,
            "anomaly": bool(row["anomaly"]),
            "anomaly_type": row["anomaly_type"],
        })

    # Stats
    anomaly_count = int(df["anomaly"].sum())
    price_change = current_price - float(df["Close"].iloc[0])
    price_change_pct = (price_change / float(df["Close"].iloc[0])) * 100

    return jsonify({
        "symbol": symbol.upper(),
        "company_name": company_name,
        "sector": sector,
        "market_cap": market_cap,
        "current_price": round(current_price, 2),
        "previous_close": prev_close,
        "price_change": round(price_change, 2),
        "price_change_pct": round(price_change_pct, 2),
        "latest_zscore": round(latest_zscore, 4),
        "is_anomaly": is_anomaly,
        "anomaly_count": anomaly_count,
        "z_threshold": z_threshold,
        "z_window": z_window,
        "data": records,
    })


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    """Get all configured alerts."""
    return jsonify({"alerts": alerts_store})


@app.route("/api/alerts", methods=["POST"])
def create_alert():
    """Create a new alert."""
    global alert_id_counter
    body = request.json
    required = ["symbol", "type"]
    for field in required:
        if field not in body:
            return jsonify({"error": f"Missing field: {field}"}), 400

    alert_type = body["type"]
    valid_types = ["price_above", "price_below", "anomaly", "zscore_above", "zscore_below"]
    if alert_type not in valid_types:
        return jsonify({"error": f"Invalid type. Valid: {valid_types}"}), 400

    # anomaly type doesn't need a threshold
    threshold = body.get("threshold", 0)
    if alert_type != "anomaly" and threshold == 0:
        return jsonify({"error": "Threshold required for this alert type"}), 400

    alert_id_counter += 1
    alert = {
        "id": alert_id_counter,
        "symbol": body["symbol"].upper(),
        "type": alert_type,
        "threshold": float(threshold),
        "active": True,
        "one_shot": body.get("one_shot", True),
        "created_at": datetime.now().isoformat(),
    }
    alerts_store.append(alert)
    return jsonify({"alert": alert}), 201


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    """Delete an alert."""
    global alerts_store
    alerts_store = [a for a in alerts_store if a["id"] != alert_id]
    return jsonify({"success": True})


@app.route("/api/alerts/triggered")
def get_triggered():
    """Get triggered alerts history."""
    return jsonify({"triggered": triggered_alerts[-50:]})


@app.route("/api/alerts/triggered/clear", methods=["POST"])
def clear_triggered():
    """Clear triggered alerts history."""
    global triggered_alerts
    triggered_alerts = []
    return jsonify({"success": True})


@app.route("/api/search/<query>")
def search_symbol(query):
    """Search for stock symbols."""
    try:
        ticker = yf.Ticker(query.upper())
        info = ticker.info
        if info and info.get("symbol"):
            return jsonify({
                "results": [{
                    "symbol": info.get("symbol", query.upper()),
                    "name": info.get("shortName", "Unknown"),
                    "sector": info.get("sector", "N/A"),
                    "exchange": info.get("exchange", "N/A"),
                }]
            })
        return jsonify({"results": []})
    except Exception:
        return jsonify({"results": []})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
