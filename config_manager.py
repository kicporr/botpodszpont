import json
import os
from typing import Any, Dict

# Bazowy katalog = tam, gdzie leży ten plik
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config_live.json")
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
TRADE_RESULTS_FILE = os.path.join(BASE_DIR, "trade_results.log")


def load_config() -> Dict[str, Any]:
    """Ładuje config z pliku, uzupełnia domyślne wartości."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    else:
        cfg = {}

    # domyślne wartości
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

    # dzienny limit ryzyka / pauza
    cfg.setdefault("daily_risk_limit_enabled", False)
    cfg.setdefault("daily_risk_limit_usd", -50.0)
    cfg.setdefault("trading_paused_for_today", False)

    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """Zapisuje config do pliku."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_positions() -> Dict[str, Any]:
    """Ładuje otwarte pozycje z positions.json."""
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_positions(positions: Dict[str, Any]) -> None:
    """Zapisuje otwarte pozycje do positions.json."""
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, indent=2)


def load_trade_results() -> Dict[str, Any]:
    """
    Zwraca statystyki z trade_results.log:
    - total_trades, winning_trades, losing_trades, total_pnl, trades (lista P&L).
    """
    results: Dict[str, Any] = {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0.0,
        "trades": [],
    }

    if not os.path.exists(TRADE_RESULTS_FILE):
        return results

    try:
        with open(TRADE_RESULTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                # interesują nas tylko zamknięte trady z P&L
                if "CLOSED @" not in line or "P&L:" not in line:
                    continue

                try:
                    # P&L USD: po "P&L: $" do " ("
                    pnl_start = line.find("P&L: $") + len("P&L: $")
                    pnl_end = line.find(" (", pnl_start)
                    if pnl_start <= len("P&L: $") - 1 or pnl_end == -1:
                        continue

                    pnl_usd = float(line[pnl_start:pnl_end])

                    results["total_trades"] += 1
                    results["total_pnl"] += pnl_usd

                    if pnl_usd > 0:
                        results["winning_trades"] += 1
                    elif pnl_usd < 0:
                        results["losing_trades"] += 1

                    results["trades"].append(pnl_usd)
                except Exception:
                    # pojedyncza zła linia nie psuje całości
                    continue
    except Exception:
        # w razie problemu zwracamy to, co się udało policzyć
        return results

    return results
