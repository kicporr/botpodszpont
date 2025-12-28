import json
import os

CONFIG_FILE = "config_live.json"
POSITIONS_FILE = "positions.json"


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
    cfg.setdefault("live_trading_mode", False)
    cfg.setdefault("journal_page_size", 50)
    
    cfg.setdefault("account_equity_usd", 1000.0)
    cfg.setdefault("risk_per_trade_pct", 1.0)


    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


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
