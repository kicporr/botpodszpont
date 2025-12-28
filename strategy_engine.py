import os
from datetime import datetime, timedelta, date

from mean_reversion_strategy import build_features, generate_signals
from config_manager import (
    load_config,
    save_config,
    load_positions,
    save_positions,
    load_trade_results,
)
from data_providers import fetch_candles_binance

SYMBOLS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "BCHUSDT", "BNBUSDT"]
TIMEFRAME = "1h"
LIMIT = 300


def get_today_pnl_from_log() -> float:
    """Sumuje dzisiejszy P&L z trade_results.log w USD."""
    today_str = date.today().strftime("%Y-%m-%d")
    pnl_today = 0.0
    if not os.path.exists("trade_results.log"):
        return 0.0

    with open("trade_results.log", "r", encoding="utf-8") as f:
        for line in f:
            if today_str not in line:
                continue
            if "P&L:" not in line:
                continue
            try:
                part = line.split("P&L:")[1].strip()
                usd_str = part.split(" ")[0]  # "$123.45"
                usd_val = float(usd_str.replace("$", "").replace(",", ""))
                pnl_today += usd_val
            except Exception:
                continue
    return pnl_today


def check_position_status(current_price, position):
    entry = position["entry"]
    sl = position["sl"]
    tp = position["tp"]
    action = position["action"]

    if action == "BUY":
        if current_price <= sl:
            return "hit_sl"
        elif current_price >= tp:
            return "hit_tp"
    else:
        if current_price >= sl:
            return "hit_sl"
        elif current_price <= tp:
            return "hit_tp"
    return "open"


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


def manage_positions():
    """
    Sprawdza otwarte pozycje i zamyka te, które trafiły SL/TP.
    Zwraca listę zamkniętych tradów + zaktualizowane positions.
    Loguje MFE/MAE do trade_results.log, żeby journal mógł to odczytać.
    """
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
            status = check_position_status(current_price, position)

            if status in ("hit_tp", "hit_sl"):
                entry = position["entry"]
                action = position["action"]
                size_usd = float(position.get("position_size_usd", 0.0))

                if action == "BUY":
                    pnl_pct = ((current_price - entry) / entry) * 100
                else:
                    pnl_pct = ((entry - current_price) / entry) * 100

                pnl_usd = size_usd * (pnl_pct / 100.0)

                mfe_pct = float(position.get("mfe_pct", 0.0))
                mae_pct = float(position.get("mae_pct", 0.0))

                closed_trades.append(
                    (
                        symbol,
                        "TP" if status == "hit_tp" else "SL",
                        pnl_usd,
                        pnl_pct,
                        mfe_pct,
                        mae_pct,
                    )
                )
                del positions[symbol]
        except Exception:
            continue

    if positions:
        save_positions(positions)

    if closed_trades:
        with open("trade_results.log", "a", encoding="utf-8") as f:
            for symbol, exit_type, pnl_usd, pnl_pct, mfe_pct, mae_pct in closed_trades:
                log_msg = (
                    f"[{timestamp}] {symbol}: CLOSED @ {exit_type} | "
                    f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%) | "
                    f"MFE: {mfe_pct:.2f}% | MAE: {mae_pct:.2f}%"
                )
                f.write(log_msg + "\n")

    return closed_trades, positions


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


def run_live_cycle(dashboard_data, force_closed_until):
    cfg = load_config()
    DEFAULT_POSITION_SIZE_USD = cfg.get("default_position_size_usd", 100)
    SYMBOL_POSITION_SIZE = cfg.get("symbol_position_size", {})
    MAX_OPEN_POSITIONS = cfg.get("max_open_positions", 3)

    account_equity = float(cfg.get("account_equity_usd", 1000.0))
    risk_per_trade_pct = float(cfg.get("risk_per_trade_pct", 1.0))

    sl_pct_long = cfg.get("sl_pct_long", 3.0) / 100.0
    tp_pct_long = cfg.get("tp_pct_long", 3.5) / 100.0
    sl_pct_short = cfg.get("sl_pct_short", 3.0) / 100.0
    tp_pct_short = cfg.get("tp_pct_short", 3.5) / 100.0

    dashboard_data["live_trading_mode"] = bool(cfg.get("live_trading_mode", False))

    # dzienny limit ryzyka
    daily_risk_limit_enabled = bool(cfg.get("daily_risk_limit_enabled", False))
    daily_risk_limit_usd = float(cfg.get("daily_risk_limit_usd", -50.0))
    trading_paused_for_today = bool(cfg.get("trading_paused_for_today", False))

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # dzienny P&L i ewentualna pauza
    pnl_today = get_today_pnl_from_log()
    if daily_risk_limit_enabled and pnl_today <= daily_risk_limit_usd:
        trading_paused_for_today = True

    dashboard_data["risk"] = {
        "daily_risk_limit_enabled": daily_risk_limit_enabled,
        "daily_risk_limit_usd": round(daily_risk_limit_usd, 2),
        "pnl_today": round(pnl_today, 2),
        "trading_paused_for_today": trading_paused_for_today,
    }

    # zarządzanie otwartymi pozycjami
    closed_trades, current_positions = manage_positions()

    if closed_trades:
        for symbol, exit_type, pnl_usd, pnl_pct, mfe_pct, mae_pct in closed_trades:
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

    # otwarte pozycje z aktualizacją MFE/MAE
    dashboard_data["open_positions"] = {}
    for symbol, position in current_positions.items():
        try:
            df_last = fetch_candles_binance(symbol=symbol, interval=TIMEFRAME, limit=1)
            if df_last is not None and not df_last.empty:
                current_price = float(df_last.iloc[-1]["close"])
            else:
                current_price = position["entry"]
        except Exception:
            current_price = position["entry"]

        entry = position["entry"]
        side = position["action"]
        size_usd = float(position.get("position_size_usd", 0.0))

        if side == "BUY":
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100

        pnl_usd = size_usd * (pnl_pct / 100.0)
        opened_at = position.get("opened_at", timestamp)
        age_str = format_age(opened_at)

        mfe = float(position.get("mfe_pct", 0.0))
        mae = float(position.get("mae_pct", 0.0))
        if pnl_pct > mfe:
            mfe = pnl_pct
        if pnl_pct < mae:
            mae = pnl_pct
        position["mfe_pct"] = mfe
        position["mae_pct"] = mae

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
            "mfe_pct": round(mfe, 2),
            "mae_pct": round(mae, 2),
        }

    trades_to_execute = []
    current_signals = []

    for symbol in SYMBOLS:
        result = get_current_signal(symbol)
        if not result:
            continue

        price = result["price"]
        signal = result["signal"]

        # pauza bota po dziennym limicie
        if trading_paused_for_today:
            current_signals.append(
                {
                    "symbol": symbol,
                    "price": round(price, 2),
                    "signal": signal,
                    "confidence": 100.0,
                    "status": "PAUSED",
                }
            )
            continue

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

        stop_pct = abs(price - sl) / price
        risk_amount = account_equity * (risk_per_trade_pct / 100.0)

        dynamic_size_usd = 0.0
        if stop_pct > 0 and risk_amount > 0:
            dynamic_size_usd = risk_amount / stop_pct

        base_size = SYMBOL_POSITION_SIZE.get(symbol, DEFAULT_POSITION_SIZE_USD)
        position_size_usd = dynamic_size_usd if dynamic_size_usd > 0 else base_size

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

    # log sygnałów + zapis nowych pozycji z MFE/MAE
    if trades_to_execute:
        with open("trade_signals.log", "a", encoding="utf-8") as f:
            for trade in trades_to_execute:
                entry = trade["entry"]
                sl = trade["sl"]
                tp = trade["tp"]
                action = trade["action"]
                symbol = trade["symbol"]
                position_size = trade["position_size_usd"]

                # tu risk_per_trade_usd jest przybliżeniem; dokładny risk wynika z SL
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
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
            }
        save_positions(positions)

    # statystyki zamkniętych tradów
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

    # equity live = equity start + realized + unrealized
    unrealized_pnl = 0.0
    for pos in dashboard_data["open_positions"].values():
        unrealized_pnl += pos["pnl_usd"]

    equity_live = account_equity + total_pnl + unrealized_pnl
    dashboard_data["equity"] = round(equity_live, 2)
    dashboard_data["equity_unrealized"] = round(unrealized_pnl, 2)

    # zapis pozycji (w tym zaktualizowane MFE/MAE)
    save_positions(current_positions)

    # zapis stanu pauzy w configu
    cfg["trading_paused_for_today"] = trading_paused_for_today
    save_config(cfg)

    dashboard_data["last_update"] = timestamp
