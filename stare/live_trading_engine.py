import time
import pandas as pd
import json
import os
from datetime import datetime, timedelta

import requests
from flask import Flask, render_template_string, jsonify, request
from threading import Thread

from mean_reversion_strategy import build_features, generate_signals

# =======================
# KONFIGURACJA
# =======================

CONFIG_FILE = "config_live.json"
POSITIONS_FILE = "positions.json"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BCHUSDT", "BNBUSDT"]
TIMEFRAME = "1h"
LIMIT = 300
BINANCE_BASE_URL = "https://api.binance.com"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    cfg.setdefault("confidence_threshold", 0.60)
    cfg.setdefault("max_open_positions", 3)
    cfg.setdefault("default_position_size_usd", 100)
    cfg.setdefault("symbol_position_size", {})

    cfg.setdefault("sl_pct_long", 3.0)
    cfg.setdefault("tp_pct_long", 3.5)
    cfg.setdefault("sl_pct_short", 3.0)
    cfg.setdefault("tp_pct_short", 3.5)

    cfg.setdefault("cooldown_hours", 1.0)

    # nowe
    cfg.setdefault("live_trading_mode", False)
    cfg.setdefault("journal_page_size", 50)

    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

# Flask app
app = Flask(__name__)

dashboard_data = {
    "last_update": "",
    "open_positions": {},
    "closed_trades": [],
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0.0,
        "win_rate": 0.0,
        "avg_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
    },
    "current_signals": [],
    "live_trading_mode": False,
}

# cooldown po force close: {symbol: "YYYY-MM-DD HH:MM:SS"}
force_closed_until = {}

# =======================
# DANE I PLIKI
# =======================

def fetch_candles_binance(symbol: str, interval: str = "1h", limit: int = 300) -> pd.DataFrame:
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    klines = r.json()

    rows = []
    for k in klines:
        rows.append(
            {
                "ts": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.sort_values("datetime").reset_index(drop=True)
    return df

def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_positions(positions):
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2)

def load_trade_results():
    results = {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0.0,
        "trades": [],
    }

    if os.path.exists("trade_results.log"):
        try:
            with open("trade_results.log", "r", encoding="utf-8") as f:
                for line in f:
                    if "CLOSED @" in line and "P&L:" in line:
                        try:
                            pnl_start = line.find("P&L: $") + 6
                            pnl_end = line.find(" (", pnl_start)
                            pnl_usd = float(line[pnl_start:pnl_end])
                            results["total_trades"] += 1
                            results["total_pnl"] += pnl_usd
                            if pnl_usd > 0:
                                results["winning_trades"] += 1
                            else:
                                results["losing_trades"] += 1
                            results["trades"].append(pnl_usd)
                        except Exception:
                            pass
        except Exception:
            pass

    return results

# =======================
# SYGNAŁ MEAN‑REVERSION
# =======================

def get_current_signal(symbol: str):
    try:
        df = fetch_candles_binance(symbol=symbol, interval=TIMEFRAME, limit=LIMIT)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    df_feat = build_features(df)
    df_sig = generate_signals(df_feat)

    if len(df_sig) == 0:
        return None

    last_row = df_sig.iloc[-1]
    price = float(last_row["close"])
    signal_val = int(last_row["signal"])

    if signal_val > 0:
        signal = "BUY"
    elif signal_val < 0:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "symbol": symbol,
        "price": price,
        "signal": signal,
        "confidence": 1.0,
    }

# =======================
# ZARZĄDZANIE POZYCJAMI
# =======================

def check_position_status(symbol, current_price, position):
    entry = position["entry"]
    sl = position["sl"]
    tp = position["tp"]
    action = position["action"]

    if action == "BUY":
        if current_price <= sl:
            return "hit_sl"
        elif current_price >= tp:
            return "hit_tp"
    else:  # SELL
        if current_price >= sl:
            return "hit_sl"
        elif current_price <= tp:
            return "hit_tp"

    return "open"

def manage_positions():
    positions = load_positions()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    closed_trades = []

    for symbol in list(positions.keys()):
        try:
            df = fetch_candles_binance(symbol=symbol, interval=TIMEFRAME, limit=10)
            if df is None or df.empty:
                continue

            current_price = df.iloc[-1]["close"]
            position = positions[symbol]
            status = check_position_status(symbol, current_price, position)

            if status in ("hit_tp", "hit_sl"):
                entry = position["entry"]
                action = position["action"]
                size_usd = position.get("position_size_usd", 0)

                if action == "BUY":
                    pnl_pct = ((current_price - entry) / entry) * 100
                else:
                    pnl_pct = ((entry - current_price) / entry) * 100

                pnl_usd = size_usd * (pnl_pct / 100.0)
                closed_trades.append(
                    (symbol, "TP" if status == "hit_tp" else "SL", pnl_usd, pnl_pct)
                )
                del positions[symbol]
        except Exception:
            continue

    if positions:
        save_positions(positions)
    elif os.path.exists(POSITIONS_FILE):
        os.remove(POSITIONS_FILE)

    if closed_trades:
        with open("trade_results.log", "a", encoding="utf-8") as f:
            for symbol, exit_type, pnl_usd, pnl_pct in closed_trades:
                log_msg = (
                    f"[{timestamp}] {symbol}: CLOSED @ {exit_type} | "
                    f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%)"
                )
                f.write(log_msg + "\n")

    return closed_trades, positions

# =======================
# GŁÓWNY CYKL
# =======================

def format_age(opened_at_str: str) -> str:
    try:
        opened = datetime.strptime(opened_at_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"
    delta = datetime.now() - opened
    minutes = int(delta.total_seconds() // 60)
    hours = minutes // 60
    rem_min = minutes % 60
    if hours == 0:
        return f"{rem_min}m"
    return f"{hours}h {rem_min}m"

def run_live_cycle():
    global dashboard_data

    cfg = load_config()
    CONFIDENCE_THRESHOLD = cfg.get("confidence_threshold", 0.60)
    DEFAULT_POSITION_SIZE_USD = cfg.get("default_position_size_usd", 100)
    SYMBOL_POSITION_SIZE = cfg.get("symbol_position_size", {})
    MAX_OPEN_POSITIONS = cfg.get("max_open_positions", 3)

    sl_pct_long = cfg.get("sl_pct_long", 3.0) / 100.0
    tp_pct_long = cfg.get("tp_pct_long", 3.5) / 100.0
    sl_pct_short = cfg.get("sl_pct_short", 3.0) / 100.0
    tp_pct_short = cfg.get("tp_pct_short", 3.5) / 100.0

    dashboard_data["live_trading_mode"] = bool(cfg.get("live_trading_mode", False))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    closed_trades, current_positions = manage_positions()

    if closed_trades:
        for symbol, exit_type, pnl_usd, pnl_pct in closed_trades:
            dashboard_data["closed_trades"].insert(
                0,
                {
                    "symbol": symbol,
                    "exit_type": exit_type,
                    "pnl": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "timestamp": timestamp,
                },
            )
        dashboard_data["closed_trades"] = dashboard_data["closed_trades"][:10]

    dashboard_data["open_positions"] = {}
    for symbol, position in current_positions.items():
        try:
            df_last = fetch_candles_binance(symbol=symbol, interval=TIMEFRAME, limit=1)
            current_price = float(df_last.iloc[-1]["close"]) if not df_last.empty else position["entry"]
        except Exception:
            current_price = position["entry"]

        entry = position["entry"]
        side = position["action"]
        size_usd = position.get("position_size_usd", 0)

        if side == "BUY":
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100

        pnl_usd = size_usd * (pnl_pct / 100.0)
        opened_at = position.get("opened_at", timestamp)
        age_str = format_age(opened_at)

        dashboard_data["open_positions"][symbol] = {
            "entry": round(entry, 2),
            "sl": round(position["sl"], 2),
            "tp": round(position["tp"], 2),
            "action": side,
            "conf": round(position.get("conf", 1.0) * 100, 1),
            "current_price": round(current_price, 2),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "age": age_str,
        }

    trades_to_execute = []
    current_signals = []

    for symbol in SYMBOLS:
        result = get_current_signal(symbol)
        if not result:
            continue

        price = result["price"]
        signal = result["signal"]

        if symbol in force_closed_until:
            t_str = force_closed_until[symbol]
            try:
                t_unblock = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")
                if datetime.now() < t_unblock:
                    current_signals.append(
                        {
                            "symbol": symbol,
                            "price": round(price, 2),
                            "signal": signal,
                            "confidence": 100.0,
                            "status": "COOLDOWN",
                        }
                    )
                    continue
                else:
                    del force_closed_until[symbol]
            except Exception:
                del force_closed_until[symbol]

        if symbol in current_positions:
            status = "IN POSITION"
        else:
            status = "READY" if signal in ("BUY", "SELL") else "HOLD"

        current_signals.append(
            {
                "symbol": symbol,
                "price": round(price, 2),
                "signal": signal,
                "confidence": 100.0,
                "status": status,
            }
        )

        if symbol in current_positions or signal not in ("BUY", "SELL"):
            continue

        if len(current_positions) + len(trades_to_execute) >= MAX_OPEN_POSITIONS:
            break

        if signal == "BUY":
            sl = price * (1.0 - sl_pct_long)
            tp = price * (1.0 + tp_pct_long)
            action = "BUY"
        else:
            sl = price * (1.0 + sl_pct_short)
            tp = price * (1.0 - tp_pct_short)
            action = "SELL"

        position_size_usd = SYMBOL_POSITION_SIZE.get(symbol, DEFAULT_POSITION_SIZE_USD)

        trades_to_execute.append(
            {
                "symbol": symbol,
                "action": action,
                "entry": price,
                "sl": sl,
                "tp": tp,
                "conf": 1.0,
                "position_size_usd": position_size_usd,
            }
        )

    dashboard_data["current_signals"] = current_signals

    if trades_to_execute:
        with open("trade_signals.log", "a", encoding="utf-8") as f:
            for trade in trades_to_execute:
                entry = trade["entry"]
                sl = trade["sl"]
                tp = trade["tp"]
                action = trade["action"]
                symbol = trade["symbol"]
                position_size = trade["position_size_usd"]

                risk_per_trade_usd = position_size * 0.03
                if action == "BUY":
                    reward_per_trade_usd = position_size * ((tp - entry) / entry)
                else:
                    reward_per_trade_usd = position_size * ((entry - tp) / entry)
                rr_ratio = (
                    reward_per_trade_usd / risk_per_trade_usd
                    if risk_per_trade_usd > 0
                    else 0
                )

                log_msg = (
                    f"[{timestamp}] OPEN: {symbol} {action} @ ${entry:.2f} | "
                    f"SL: ${sl:.2f} | TP: ${tp:.2f} | "
                    f"Risk/Reward: 1:{rr_ratio:.2f}"
                )
                f.write(log_msg + "\n")

        positions = load_positions()
        for trade in trades_to_execute:
            positions[trade["symbol"]] = {
                "entry": trade["entry"],
                "sl": trade["sl"],
                "tp": trade["tp"],
                "action": trade["action"],
                "conf": trade["conf"],
                "position_size_usd": trade["position_size_usd"],
                "opened_at": timestamp,
            }
        save_positions(positions)

    trade_stats = load_trade_results()
    total = trade_stats["total_trades"]
    wins = trade_stats["winning_trades"]
    losses = trade_stats["losing_trades"]
    total_pnl = trade_stats["total_pnl"]

    dashboard_data["stats"] = {
        "total_trades": total,
        "winning_trades": wins,
        "losing_trades": losses,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round((wins / total) * 100 if total > 0 else 0, 1),
        "avg_pnl": round(total_pnl / total if total > 0 else 0, 2),
        "best_trade": round(max(trade_stats["trades"]) if trade_stats["trades"] else 0, 2),
        "worst_trade": round(min(trade_stats["trades"]) if trade_stats["trades"] else 0, 2),
    }

    dashboard_data["last_update"] = timestamp

# =======================
# FORCE CLOSE ENDPOINT
# =======================

@app.route("/close_position", methods=["POST"])
def close_position():
    data = request.json or {}
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    positions = load_positions()
    if symbol not in positions:
        return jsonify({"error": "no such open position"}), 404

    position = positions.pop(symbol)
    save_positions(positions)

    cfg = load_config()
    cooldown_hours = cfg.get("cooldown_hours", 1.0)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        df_last = fetch_candles_binance(symbol=symbol, interval=TIMEFRAME, limit=1)
        current_price = float(df_last.iloc[-1]["close"]) if not df_last.empty else position["entry"]
    except Exception:
        current_price = position["entry"]

    entry = position["entry"]
    side = position["action"]
    size_usd = position.get("position_size_usd", 0)

    if side == "BUY":
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_pct = (entry - current_price) / entry * 100

    pnl_usd = size_usd * (pnl_pct / 100.0)

    with open("trade_results.log", "a", encoding="utf-8") as f:
        log_msg = (
            f"[{timestamp}] {symbol}: CLOSED @ FORCE | "
            f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%)"
        )
        f.write(log_msg + "\n")

    unblock_time = datetime.now() + timedelta(hours=float(cooldown_hours))
    force_closed_until[symbol] = unblock_time.strftime("%Y-%m-%d %H:%M:%S")

    dashboard_data["closed_trades"].insert(
        0,
        {
            "symbol": symbol,
            "exit_type": "FORCE",
            "pnl": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "timestamp": timestamp,
        },
    )
    dashboard_data["closed_trades"] = dashboard_data["closed_trades"][:10]

    return jsonify({
        "symbol": symbol,
        "closed_at": timestamp,
        "pnl_usd": round(pnl_usd, 2),
        "pnl_pct": round(pnl_pct, 2),
    })

# =======================
# PARSOWANIE LOGÓW DO JOURNAL
# =======================

def parse_timestamp_from_line(line: str) -> str:
    try:
        start = line.find("[") + 1
        end = line.find("]", start)
        return line[start:end]
    except Exception:
        return ""

def load_journal_records(page_size: int = 50):
    entries = []
    if os.path.exists("trade_signals.log"):
        try:
            with open("trade_signals.log", "r", encoding="utf-8") as f:
                for line in f:
                    if "OPEN:" not in line:
                        continue
                    ts = parse_timestamp_from_line(line)
                    try:
                        after = line.split("OPEN:")[1].strip()
                        parts = after.split("|")
                        left = parts[0].strip()
                        symbol_side, at_part = left.split("@")
                        symbol_side = symbol_side.strip()
                        symbol, side = symbol_side.split()
                        entry_price = float(at_part.replace("$", "").strip())
                        sl_txt = parts[1].replace("SL:", "").replace("$", "").strip()
                        tp_txt = parts[2].replace("TP:", "").replace("$", "").strip()
                        sl = float(sl_txt)
                        tp = float(tp_txt)
                        rr_txt = parts[3].replace("Risk/Reward:", "").strip()
                        rr = float(rr_txt.split(":")[1])
                    except Exception:
                        continue
                    entries.append({
                        "opened_at": ts,
                        "symbol": symbol,
                        "side": side,
                        "entry": entry_price,
                        "sl": sl,
                        "tp": tp,
                        "rr": rr,
                        "closed_at": None,
                        "exit_type": None,
                        "pnl": None,
                        "pnl_pct": None,
                        "duration": None,
                    })
        except Exception:
            pass

    closes = []
    if os.path.exists("trade_results.log"):
        try:
            with open("trade_results.log", "r", encoding="utf-8") as f:
                for line in f:
                    if "CLOSED @" not in line or "P&L:" not in line:
                        continue
                    ts = parse_timestamp_from_line(line)
                    try:
                        after = line.split("]")[1].strip()
                        symbol_part, rest = after.split(":", 1)
                        symbol = symbol_part.strip()
                        exit_type = rest.split("@")[1].split("|")[0].strip()
                        pnl_start = rest.find("P&L: $") + 6
                        pnl_end = rest.find(" (", pnl_start)
                        pnl_usd = float(rest[pnl_start:pnl_end])
                        pct_start = rest.find("(", pnl_end) + 1
                        pct_end = rest.find("%", pct_start)
                        pnl_pct = float(rest[pct_start:pct_end])
                    except Exception:
                        continue
                    closes.append({
                        "timestamp": ts,
                        "symbol": symbol,
                        "exit_type": exit_type,
                        "pnl": pnl_usd,
                        "pnl_pct": pnl_pct,
                    })
        except Exception:
            pass

    closes_sorted = sorted(closes, key=lambda x: x["timestamp"])
    for cl in closes_sorted:
        for e in entries:
            if e["symbol"] == cl["symbol"] and e["closed_at"] is None:
                e["closed_at"] = cl["timestamp"]
                e["exit_type"] = cl["exit_type"]
                e["pnl"] = cl["pnl"]
                e["pnl_pct"] = cl["pnl_pct"]
                try:
                    t_open = datetime.strptime(e["opened_at"], "%Y-%m-%d %H:%M:%S")
                    t_close = datetime.strptime(cl["timestamp"], "%Y-%m-%d %H:%M:%S")
                    minutes = int((t_close - t_open).total_seconds() // 60)
                    hours = minutes // 60
                    rem = minutes % 60
                    e["duration"] = f"{hours}h {rem}m" if hours > 0 else f"{rem}m"
                except Exception:
                    e["duration"] = "-"
                break

    entries_sorted = sorted(entries, key=lambda x: x["opened_at"], reverse=True)
    return entries_sorted[:page_size]

# =======================
# DASHBOARD HTML + JOURNAL + API
# =======================

@app.route("/")
def dashboard():
    cfg = load_config()
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mean-Reversion 1H Live Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root {
      --bg-main: #020617;
      --bg-card: #020617;
      --bg-page: #020617;
      --border-soft: #1f2937;
      --text-main: #e5e7eb;
      --text-muted: #9ca3af;
      --accent: #38bdf8;
      --accent-soft: rgba(56,189,248,0.1);
      --danger: #f87171;
      --success: #4ade80;
      --warning: #facc15;
    }

    body {
      background: radial-gradient(circle at top, #020617 0, #020617 35%, #020617 100%);
      color: var(--text-main);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    .navbar {
      background: linear-gradient(90deg, #020617, #0f172a);
      border-bottom: 1px solid #1f2937;
    }

    .navbar-brand {
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      font-size: .9rem;
      color: var(--text-main);
    }

    .brand-pill {
      border-radius: 999px;
      padding: .2rem .8rem;
      border: 1px solid rgba(148,163,184,.5);
      font-size: .75rem;
      color: var(--text-muted);
    }

    .last-update {
      font-size: .75rem;
      color: var(--text-muted);
    }

    .mode-pill {
      border-radius: 999px;
      padding: .2rem .8rem;
      font-size: .75rem;
      border: 1px solid rgba(52,211,153,.6);
      color: #22c55e;
    }

    .mode-pill.dry {
      border-color: rgba(248,250,252,.6);
      color: #e5e7eb;
    }

    .card {
      background-color: var(--bg-card);
      border-radius: .9rem;
      border: 1px solid var(--border-soft);
      box-shadow: 0 18px 40px rgba(15,23,42,0.5);
    }

    .card-header {
      border-bottom: 1px solid rgba(31,41,55,.8);
      padding: .6rem 1rem;
    }

    .card-header h5 {
      font-size: .9rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin: 0;
    }

    .metric-card {
      position: relative;
      overflow: hidden;
    }

    .metric-card::before {
      content: "";
      position: absolute;
      inset: 0;
      background: radial-gradient(circle at top, rgba(56,189,248,.13), transparent 55%);
      opacity: .8;
      pointer-events: none;
    }

    .metric-body {
      position: relative;
      z-index: 1;
      padding: .85rem 1rem;
    }

    .metric-label {
      font-size: .75rem;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: var(--text-muted);
      margin-bottom: .4rem;
    }

    .metric-value {
      font-size: 1.5rem;
      font-weight: 600;
    }

    .metric-sub {
      font-size: .75rem;
      color: var(--text-muted);
    }

    .metric-value.pos {
      color: var(--success);
    }

    .metric-value.neg {
      color: var(--danger);
    }

    .table-container {
      max-height: 340px;
      overflow-y: auto;
    }

    .table-dark {
      --bs-table-bg: transparent;
      --bs-table-border-color: rgba(55,65,81,.7);
      font-size: .8rem;
    }

    .table-dark thead th {
      border-bottom-color: rgba(75,85,99,.9);
      text-transform: uppercase;
      font-size: .7rem;
      letter-spacing: .06em;
      color: var(--text-muted);
    }

    .table-dark tbody tr:nth-child(even) {
      background-color: rgba(15,23,42,.7);
    }

    .table-dark tbody tr:hover {
      background-color: rgba(30,64,175,.35);
    }

    .badge-buy {
      background: rgba(34,197,94,.15);
      border: 1px solid rgba(34,197,94,.7);
      color: var(--success);
      font-weight: 500;
      border-radius: 999px;
    }

    .badge-sell {
      background: rgba(248,113,113,.15);
      border: 1px solid rgba(248,113,113,.7);
      color: var(--danger);
      font-weight: 500;
      border-radius: 999px;
    }

    .badge-hold {
      background: rgba(148,163,184,.08);
      border: 1px solid rgba(148,163,184,.6);
      color: var(--text-muted);
      font-weight: 500;
      border-radius: 999px;
    }

    .status-pill {
      border-radius: 999px;
      padding: .1rem .55rem;
      font-size: .65rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }

    .status-ready {
      background: rgba(52,211,153,.12);
      color: var(--success);
      border: 1px solid rgba(52,211,153,.7);
    }

    .status-hold {
      background: rgba(148,163,184,.1);
      color: var(--text-muted);
      border: 1px solid rgba(148,163,184,.5);
    }

    .status-position {
      background: rgba(56,189,248,.1);
      color: var(--accent);
      border: 1px solid rgba(56,189,248,.6);
    }

    .status-cooldown {
      background: rgba(250,204,21,.1);
      color: var(--warning);
      border: 1px solid rgba(250,204,21,.7);
    }

    .btn-outline-warning {
      border-radius: 999px;
      padding: .18rem .7rem;
      font-size: .7rem;
      border-width: 1px;
    }

    .btn-outline-warning:hover {
      background-color: rgba(250,204,21,.1);
      border-color: var(--warning);
      color: var(--warning);
    }

    .pnl-pos {
      color: var(--success);
      font-weight: 500;
    }

    .pnl-neg {
      color: var(--danger);
      font-weight: 500;
    }

    .pnl-small {
      font-size: .72rem;
      color: var(--text-muted);
    }

    ::-webkit-scrollbar {
      width: 6px;
    }

    ::-webkit-scrollbar-track {
      background: transparent;
    }

    ::-webkit-scrollbar-thumb {
      background: #1f2937;
      border-radius: 999px;
    }

    @media (max-width: 992px) {
      .metric-value {
        font-size: 1.2rem;
      }
    }

    .nav-link {
      font-size: .8rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }

    .settings-label {
      font-size: .75rem;
      color: var(--text-muted);
    }

    .form-control,
    .form-select {
      background-color: #020617;
      border-color: #1f2937;
      color: #e5e7eb;
      font-size: .8rem;
    }

    .form-control:focus,
    .form-select:focus {
      box-shadow: 0 0 0 1px #38bdf8;
      border-color: #38bdf8;
      background-color: #020617;
      color: #e5e7eb;
    }

    .form-check-label {
      font-size: .8rem;
    }
  </style>
</head>
<body>
  <nav class="navbar navbar-dark mb-3">
    <div class="container-fluid d-flex justify-content-between align-items-center">
      <div class="d-flex align-items-center gap-3">
        <span class="navbar-brand mb-0 h1">
            1H LIVE DASHBOARD
        </span>
        <span class="brand-pill">BINANCE · RSI 20/80 · ATR FILTER</span>
        {% if data.live_trading_mode %}
          <span class="mode-pill">LIVE MODE</span>
        {% else %}
          <span class="mode-pill dry">DRY RUN</span>
        {% endif %}
      </div>
      <span class="last-update">Last update: {{ data.last_update }}</span>
    </div>
  </nav>

  <div class="container-fluid mb-3">
    <ul class="nav nav-tabs">
      <li class="nav-item">
        <a class="nav-link active" href="/">Dashboard</a>
      </li>
      <li class="nav-item">
        <a class="nav-link" href="/journal">Journal</a>
      </li>
    </ul>
  </div>

  <!-- SETTINGS PANEL -->
  <div class="container-fluid mb-4">
    <div class="card">
      <div class="card-header d-flex justify-content-between align-items-center">
        <h5 class="mb-0">Settings</h5>
        <small class="text-muted">Changes apply from next cycle</small>
      </div>
      <div class="card-body">
        <form id="settings-form" class="row g-3 align-items-end">
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">Max positions</label>
            <input type="number" min="1" max="20" step="1" class="form-control" id="max_open_positions">
          </div>
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">Default size (USD)</label>
            <input type="number" min="1" step="1" class="form-control" id="default_position_size_usd">
          </div>
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">SL long %</label>
            <input type="number" min="0.1" step="0.1" class="form-control" id="sl_pct_long">
          </div>
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">TP long %</label>
            <input type="number" min="0.1" step="0.1" class="form-control" id="tp_pct_long">
          </div>
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">SL short %</label>
            <input type="number" min="0.1" step="0.1" class="form-control" id="sl_pct_short">
          </div>
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">TP short %</label>
            <input type="number" min="0.1" step="0.1" class="form-control" id="tp_pct_short">
          </div>
          <div class="col-6 col-md-2">
            <label class="settings-label mb-1">Cooldown (h)</label>
            <input type="number" min="0" step="0.5" class="form-control" id="cooldown_hours">
          </div>
          <div class="col-6 col-md-2">
            <div class="form-check mt-4">
              <input class="form-check-input" type="checkbox" id="live_trading_mode">
              <label class="form-check-label" for="live_trading_mode">
                Live trading mode
              </label>
            </div>
          </div>
          <div class="col-12 col-md-3 text-md-end">
            <button type="button" class="btn btn-sm btn-primary" onclick="saveSettings()">
              Save settings
            </button>
          </div>
          <div class="col-12">
            <small id="settings-status" class="text-muted"></small>
          </div>
        </form>
      </div>
    </div>
  </div>

  <!-- METRYKI -->
  <div class="container-fluid mb-4">
    <div class="row g-3">
      <div class="col-6 col-md-3">
        <div class="card metric-card">
          <div class="metric-body">
            <div class="metric-label">Total trades</div>
            <div class="metric-value">{{ data.stats.total_trades }}</div>
            <div class="metric-sub">Closed since start</div>
          </div>
        </div>
      </div>

      <div class="col-6 col-md-3">
        <div class="card metric-card">
          <div class="metric-body">
            <div class="metric-label">Win rate</div>
            <div class="metric-value">{{ data.stats.win_rate }}%</div>
            <div class="metric-sub">All closed trades</div>
          </div>
        </div>
      </div>

      <div class="col-6 col-md-3">
        <div class="card metric-card">
          <div class="metric-body">
            <div class="metric-label">Total P&L</div>
            <div class="metric-value {% if data.stats.total_pnl >= 0 %}pos{% else %}neg{% endif %}">
              {{ data.stats.total_pnl }}
            </div>
            <div class="metric-sub">Realized profit in USD</div>
          </div>
        </div>
      </div>

      <div class="col-6 col-md-3">
        <div class="card metric-card">
          <div class="metric-body">
            <div class="metric-label">Best / Worst</div>
            <div class="metric-value">
              <span class="pnl-pos">{{ data.stats.best_trade }}</span>
              <span class="pnl-small"> / </span>
              <span class="pnl-neg">{{ data.stats.worst_trade }}</span>
            </div>
            <div class="metric-sub">Best / worst trade USD</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- TABEL
       CURRENT SIGNALS / OPEN POSITIONS / CLOSED TRADES
  -->
  <div class="container-fluid pb-4">
    <div class="row g-4">
      <!-- CURRENT SIGNALS -->
      <div class="col-lg-4">
        <div class="card h-100">
          <div class="card-header">
            <h5 class="mb-0">Current Signals</h5>
          </div>
          <div class="card-body p-0 table-container">
            {% if data.current_signals %}
            <table class="table table-dark table-sm mb-0 align-middle">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Signal</th>
                  <th>Price</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {% for s in data.current_signals %}
                <tr>
                  <td>{{ s.symbol }}</td>
                  <td>
                    {% if s.signal == 'BUY' %}
                      <span class="badge badge-buy">BUY</span>
                    {% elif s.signal == 'SELL' %}
                      <span class="badge badge-sell">SELL</span>
                    {% else %}
                      <span class="badge badge-hold">HOLD</span>
                    {% endif %}
                  </td>
                  <td>{{ s.price }}</td>
                  <td>
                    {% if s.status == 'READY' %}
                      <span class="status-pill status-ready">Ready</span>
                    {% elif s.status == 'IN POSITION' %}
                      <span class="status-pill status-position">In position</span>
                    {% elif s.status == 'COOLDOWN' %}
                      <span class="status-pill status-cooldown">Cooldown</span>
                    {% else %}
                      <span class="status-pill status-hold">{{ s.status }}</span>
                    {% endif %}
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            {% else %}
              <div class="p-3 text-muted">No signals</div>
            {% endif %}
          </div>
        </div>
      </div>

      <!-- OPEN POSITIONS -->
      <div class="col-lg-4">
        <div class="card h-100">
          <div class="card-header">
            <h5 class="mb-0">Open Positions</h5>
          </div>
          <div class="card-body p-0 table-container">
            {% if data.open_positions %}
            <table class="table table-dark table-sm mb-0 align-middle">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>SL</th>
                  <th>TP</th>
                  <th>Price</th>
                  <th>P&L</th>
                  <th>Age</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {% for sym, pos in data.open_positions.items() %}
                <tr>
                  <td>{{ sym }}</td>
                  <td>
                    {% if pos.action == 'BUY' %}
                      <span class="badge badge-buy">LONG</span>
                    {% else %}
                      <span class="badge badge-sell">SHORT</span>
                    {% endif %}
                  </td>
                  <td>{{ pos.entry }}</td>
                  <td class="text-danger">{{ pos.sl }}</td>
                  <td class="text-success">{{ pos.tp }}</td>
                  <td>{{ pos.current_price }}</td>
                  <td>
                    {% if pos.pnl_usd >= 0 %}
                      <span class="pnl-pos">{{ pos.pnl_usd }} ({{ pos.pnl_pct }}%)</span>
                    {% else %}
                      <span class="pnl-neg">{{ pos.pnl_usd }} ({{ pos.pnl_pct }}%)</span>
                    {% endif %}
                  </td>
                  <td>{{ pos.age }}</td>
                  <td>
                    <button class="btn btn-sm btn-outline-warning"
                            onclick="forceClose('{{ sym }}')">
                      Close
                    </button>
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            {% else %}
              <div class="p-3 text-muted">No open positions</div>
            {% endif %}
          </div>
        </div>
      </div>

      <!-- CLOSED TRADES -->
      <div class="col-lg-4">
        <div class="card h-100">
          <div class="card-header">
            <h5 class="mb-0">Recent Closed Trades</h5>
          </div>
          <div class="card-body p-0 table-container">
            {% if data.closed_trades %}
            <table class="table table-dark table-sm mb-0 align-middle">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Type</th>
                  <th>P&L</th>
                </tr>
              </thead>
              <tbody>
                {% for t in data.closed_trades %}
                <tr>
                  <td>{{ t.timestamp }}</td>
                  <td>{{ t.symbol }}</td>
                  <td>{{ t.exit_type }}</td>
                  <td>
                    {% if t.pnl >= 0 %}
                      <span class="pnl-pos">{{ t.pnl }} ({{ t.pnl_pct }}%)</span>
                    {% else %}
                      <span class="pnl-neg">{{ t.pnl }} ({{ t.pnl_pct }}%)</span>
                    {% endif %}
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
            {% else %}
              <div class="p-3 text-muted">No closed trades yet</div>
            {% endif %}
          </div>
        </div>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    async function loadSettings() {
      try {
        const res = await fetch("/config");
        const cfg = await res.json();
        document.getElementById("max_open_positions").value = cfg.max_open_positions ?? 3;
        document.getElementById("default_position_size_usd").value = cfg.default_position_size_usd ?? 100;
        document.getElementById("sl_pct_long").value = cfg.sl_pct_long ?? 3.0;
        document.getElementById("tp_pct_long").value = cfg.tp_pct_long ?? 3.5;
        document.getElementById("sl_pct_short").value = cfg.sl_pct_short ?? 3.0;
        document.getElementById("tp_pct_short").value = cfg.tp_pct_short ?? 3.5;
        document.getElementById("cooldown_hours").value = cfg.cooldown_hours ?? 1.0;
        document.getElementById("live_trading_mode").checked = cfg.live_trading_mode === true;
      } catch (e) {
        document.getElementById("settings-status").textContent = "Failed to load settings";
      }
    }

    async function saveSettings() {
      const statusEl = document.getElementById("settings-status");
      statusEl.textContent = "Saving...";
      const payload = {
        max_open_positions: Number(document.getElementById("max_open_positions").value),
        default_position_size_usd: Number(document.getElementById("default_position_size_usd").value),
        sl_pct_long: Number(document.getElementById("sl_pct_long").value),
        tp_pct_long: Number(document.getElementById("tp_pct_long").value),
        sl_pct_short: Number(document.getElementById("sl_pct_short").value),
        tp_pct_short: Number(document.getElementById("tp_pct_short").value),
        cooldown_hours: Number(document.getElementById("cooldown_hours").value),
        live_trading_mode: document.getElementById("live_trading_mode").checked
      };
      try {
        const res = await fetch("/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          statusEl.textContent = "Error saving settings";
          return;
        }
        statusEl.textContent = "Settings saved – will apply from next cycle.";
      } catch (e) {
        statusEl.textContent = "Error saving settings";
      }
    }

    async function forceClose(symbol) {
      if (!confirm("Force close " + symbol + " ?")) return;
      try {
        const res = await fetch("/close_position", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: symbol })
        });
        const data = await res.json();
        if (!res.ok) {
          alert("Error: " + (data.error || res.status));
          return;
        }
        alert("Closed " + symbol + " P&L: " + data.pnl_usd + " USD (" + data.pnl_pct + "%)");
        location.reload();
      } catch (e) {
        alert("Request failed");
      }
    }

    document.addEventListener("DOMContentLoaded", loadSettings);
  </script>
</body>
</html>"""
    return render_template_string(html, data=dashboard_data)


@app.route("/journal")
def journal():
    cfg = load_config()
    page_size = int(cfg.get("journal_page_size", 50))
    records = load_journal_records(page_size=page_size)

    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Trade Journal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      background-color: #020617;
      color: #e5e7eb;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .navbar {
      background: linear-gradient(90deg, #020617, #0f172a);
      border-bottom: 1px solid #1f2937;
    }
    .navbar-brand {
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      font-size: .9rem;
    }
    .card {
      background-color: #020617;
      border-radius: .9rem;
      border: 1px solid #1f2937;
      box-shadow: 0 18px 40px rgba(15,23,42,0.5);
    }
    .card-header {
      border-bottom: 1px solid #1f2937;
      padding: .6rem 1rem;
    }
    .card-header h5 {
      font-size: .9rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #9ca3af;
      margin: 0;
    }
    .table-dark {
      --bs-table-bg: transparent;
      --bs-table-border-color: rgba(55,65,81,.7);
      font-size: .8rem;
    }
    .table-dark thead th {
      border-bottom-color: rgba(75,85,99,.9);
      text-transform: uppercase;
      font-size: .7rem;
      letter-spacing: .06em;
      color: #9ca3af;
    }
    .table-dark tbody tr:nth-child(even) {
      background-color: rgba(15,23,42,.7);
    }
    .table-dark tbody tr:hover {
      background-color: rgba(30,64,175,.35);
    }
    .pnl-pos { color: #4ade80; font-weight: 500; }
    .pnl-neg { color: #f87171; font-weight: 500; }
    .nav-link {
      font-size: .8rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
  </style>
</head>
<body>
  <nav class="navbar navbar-dark mb-3">
    <div class="container-fluid d-flex justify-content-between align-items-center">
      <div class="d-flex align-items-center gap-3">
        <span class="navbar-brand mb-0 h1">
          TRADE JOURNAL
        </span>
      </div>
      <a href="/" class="btn btn-sm btn-outline-light">Back to dashboard</a>
    </div>
  </nav>

  <div class="container-fluid mb-3">
    <div class="card">
      <div class="card-header">
        <h5 class="mb-0">Recent trades ({{ records|length }})</h5>
      </div>
      <div class="card-body p-0">
        {% if records %}
        <div class="table-responsive">
          <table class="table table-dark table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Opened</th>
                <th>Closed</th>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry</th>
                <th>SL</th>
                <th>TP</th>
                <th>Exit type</th>
                <th>P&L</th>
                <th>R:R</th>
                <th>Duration</th>
              </tr>
            </thead>
            <tbody>
              {% for r in records %}
              <tr>
                <td>{{ r.opened_at }}</td>
                <td>{{ r.closed_at or "-" }}</td>
                <td>{{ r.symbol }}</td>
                <td>{{ r.side }}</td>
                <td>{{ r.entry }}</td>
                <td>{{ r.sl }}</td>
                <td>{{ r.tp }}</td>
                <td>{{ r.exit_type or "-" }}</td>
                <td>
                  {% if r.pnl is not none %}
                    {% if r.pnl >= 0 %}
                      <span class="pnl-pos">{{ r.pnl }} ({{ r.pnl_pct }}%)</span>
                    {% else %}
                      <span class="pnl-neg">{{ r.pnl }} ({{ r.pnl_pct }}%)</span>
                    {% endif %}
                  {% else %}
                    -
                  {% endif %}
                </td>
                <td>{{ r.rr }}</td>
                <td>{{ r.duration or "-" }}</td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% else %}
          <div class="p-3 text-muted">No trades in journal yet</div>
        {% endif %}
      </div>
    </div>
  </div>
</body>
</html>"""
    return render_template_string(html, records=records)

@app.route("/api/status")
def api_status():
    return jsonify(dashboard_data)

@app.route("/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        return jsonify(load_config())
    cfg = load_config()
    data = request.json or {}
    for key in [
        "confidence_threshold",
        "max_open_positions",
        "default_position_size_usd",
        "sl_pct_long",
        "tp_pct_long",
        "sl_pct_short",
        "tp_pct_short",
        "cooldown_hours",
        "live_trading_mode",
        "journal_page_size",
    ]:
        if key in data:
            cfg[key] = data[key]
    if "symbol_position_size" in data and isinstance(data["symbol_position_size"], dict):
        cfg.setdefault("symbol_position_size", {}).update(data["symbol_position_size"])
    save_config(cfg)
    return jsonify(cfg)

# =======================
# WORKER + MAIN
# =======================

def live_worker():
    while True:
        try:
            run_live_cycle()
        except Exception as e:
            print("Error in live cycle:", e)
        time.sleep(10)  # zawsze 10

def main():
    t = Thread(target=live_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    main()
