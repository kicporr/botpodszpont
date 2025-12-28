import json
import os
from typing import Any, Dict


CONFIG_FILE = "config_live.json"
POSITIONS_FILE = "positions.json"
TRADE_RESULTS_FILE = "trade_results.log"


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def load_config() -> Dict[str, Any]:
    """
    Ładuje konfigurację z pliku JSON.
    Ustawia sensowne domyślne wartości, jeśli klucze nie istnieją.
    """
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if not isinstance(cfg, dict):
                    cfg = {}
        except Exception:
            cfg = {}
    else:
        cfg = {}

    # Główne parametry strategii / ryzyka
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

    # Dzienny limit ryzyka (paper/live)
    cfg.setdefault("daily_risk_limit_enabled", False)
    # U Ciebie w config_live.json jest np. -100, więc domyślnie też ujemna wartość
    cfg.setdefault("daily_risk_limit_usd", -50.0)
    cfg.setdefault("trading_paused_for_today", False)

    # Normalizacja typów liczbowych (gdyby ktoś edytował JSON ręcznie)
    cfg["max_open_positions"] = _safe_int(cfg.get("max_open_positions"), 3)
    cfg["default_position_size_usd"] = _safe_float(
        cfg.get("default_position_size_usd"), 100.0
    )
    cfg["account_equity_usd"] = _safe_float(cfg.get("account_equity_usd"), 1000.0)
    cfg["risk_per_trade_pct"] = _safe_float(cfg.get("risk_per_trade_pct"), 1.0)
    cfg["sl_pct_long"] = _safe_float(cfg.get("sl_pct_long"), 3.0)
    cfg["tp_pct_long"] = _safe_float(cfg.get("tp_pct_long"), 3.5)
    cfg["sl_pct_short"] = _safe_float(cfg.get("sl_pct_short"), 3.0)
    cfg["tp_pct_short"] = _safe_float(cfg.get("tp_pct_short"), 3.5)
    cfg["cooldown_hours"] = _safe_float(cfg.get("cooldown_hours"), 1.0)
    cfg["daily_risk_limit_usd"] = _safe_float(
        cfg.get("daily_risk_limit_usd"), -50.0
    )

    # symbol_position_size powinno być słownikiem
    if not isinstance(cfg.get("symbol_position_size"), dict):
        cfg["symbol_position_size"] = {}

    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    """
    Zapisuje konfigurację do pliku JSON.
    """
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        # W paper tradingu lepiej nie wywalać całej aplikacji na problemie z dyskiem,
        # ale w realu warto byłoby to zalogować do osobnego error.log.
        pass


def load_positions() -> Dict[str, Any]:
    """
    Ładuje otwarte pozycje z pliku JSON.
    Zwraca pusty słownik, jeśli plik nie istnieje lub jest uszkodzony.
    """
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            return {}
    return {}


def save_positions(positions: Dict[str, Any]) -> None:
    """
    Zapisuje otwarte pozycje do pliku JSON.
    """
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, indent=2)
    except Exception:
        # Jak wyżej – można dodać log do error.log, jeśli chcesz.
        pass


def load_trade_results() -> Dict[str, Any]:
    """
    Agreguje wyniki z trade_results.log (paper trading).
    Oczekuje linii w formacie:
      "[YYYY-MM-DD HH:MM:SS] SYMBOL: CLOSED @ TYPE | P&L: $X.XX (Y.YY%) | MFE: ... | MAE: ..."
    Zwraca:
      {
        "total_trades": int,
        "winning_trades": int,
        "losing_trades": int,
        "total_pnl": float,
        "trades": [pnl1, pnl2, ...]
      }
    """
    results = {
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
                if "P&L:" not in line:
                    continue
                try:
                    # Szukamy fragmentu "P&L: $123.45 (..."
                    part = line.split("P&L:")[1].strip()
                    usd_str = part.split(" ")[0]  # "$123.45"
                    pnl_usd = float(usd_str.replace("$", "").replace(",", ""))
                except Exception:
                    continue

                results["total_trades"] += 1
                results["total_pnl"] += pnl_usd
                if pnl_usd > 0:
                    results["winning_trades"] += 1
                else:
                    results["losing_trades"] += 1
                results["trades"].append(pnl_usd)
    except Exception:
        # W razie problemów zwracamy to, co zdołaliśmy policzyć
        return results

    return results
