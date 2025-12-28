import os
from datetime import datetime
from typing import Any, Dict, List


TRADE_SIGNALS_FILE = "trade_signals.log"
TRADE_RESULTS_FILE = "trade_results.log"


def parse_timestamp_from_line(line: str) -> str:
    """
    Wyciąga timestamp z linii w formacie:
      "[YYYY-MM-DD HH:MM:SS] ..."
    """
    try:
        start = line.find("[") + 1
        end = line.find("]", start)
        return line[start:end]
    except Exception:
        return ""


def _safe_float(text: str, default: float | None = None) -> float | None:
    try:
        return float(text)
    except Exception:
        return default


def load_journal_records(page_size: int = 50) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []

    # 1) Odczyt otwarć z trade_signals.log
    # Format z aktualnego strategy_engine:
    # "[ts] OPEN: SYMBOL SIDE @ $ENTRY | SL: $SL | TP: $TP | Size: $X | Risk: $Y (Z% to SL) | Risk/Reward: 1:R"
    if os.path.exists(TRADE_SIGNALS_FILE):
        try:
            with open(TRADE_SIGNALS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if "OPEN:" not in line:
                        continue

                    ts = parse_timestamp_from_line(line)
                    if not ts:
                        continue

                    try:
                        after = line.split("OPEN:")[1].strip()
                        parts = [p.strip() for p in after.split("|")]

                        # Część 0: "BTCUSDT BUY @ $12345.00"
                        left = parts[0]
                        symbol_side, at_part = left.split("@")
                        symbol_side = symbol_side.strip()
                        symbol, side = symbol_side.split()
                        entry_price = _safe_float(
                            at_part.replace("$", "").strip(), None
                        )
                        if entry_price is None:
                            continue

                        # Część 1: "SL: $xxxxx"
                        sl = None
                        if len(parts) > 1 and "SL:" in parts[1]:
                            sl_txt = (
                                parts[1]
                                .replace("SL:", "")
                                .replace("$", "")
                                .strip()
                            )
                            sl = _safe_float(sl_txt, None)

                        # Część 2: "TP: $xxxxx"
                        tp = None
                        if len(parts) > 2 and "TP:" in parts[2]:
                            tp_txt = (
                                parts[2]
                                .replace("TP:", "")
                                .replace("$", "")
                                .strip()
                            )
                            tp = _safe_float(tp_txt, None)

                        # Risk/Reward jest zazwyczaj w ostatniej części
                        rr = None
                        for p in parts:
                            if "Risk/Reward" in p:
                                # "Risk/Reward: 1:R"
                                try:
                                    rr_txt = p.replace("Risk/Reward:", "").strip()
                                    # po "1:" bierzemy drugą wartość
                                    rr_val_txt = rr_txt.split(":")[1]
                                    rr = _safe_float(rr_val_txt, None)
                                except Exception:
                                    rr = None
                                break

                    except Exception:
                        continue

                    entries.append(
                        {
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
                            "mfe_pct": None,
                            "mae_pct": None,
                        }
                    )
        except Exception:
            # W razie problemów po prostu nie mamy części danych z sygnałów
            pass

    # 2) Odczyt zamknięć z trade_results.log (z MFE/MAE)
    # Format z strategy_engine:
    # "[ts] SYMBOL: CLOSED @ TYPE | P&L: $X.XX (Y.YY%) | MFE: A.AA% | MAE: B.BB%"
    closes: List[Dict[str, Any]] = []

    if os.path.exists(TRADE_RESULTS_FILE):
        try:
            with open(TRADE_RESULTS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if "CLOSED @" not in line or "P&L:" not in line:
                        continue

                    ts = parse_timestamp_from_line(line)
                    if not ts:
                        continue

                    try:
                        after = line.split("]")[1].strip()
                        symbol_part, rest = after.split(":", 1)
                        symbol = symbol_part.strip()

                        # EXIT TYPE
                        # "... CLOSED @ TYPE | P&L: ..."
                        try:
                            exit_type = rest.split("CLOSED @")[1].split("|")[0].strip()
                        except Exception:
                            exit_type = "UNKNOWN"

                        # P&L USD
                        pnl_usd = None
                        if "P&L:" in rest:
                            pnl_start = rest.find("P&L: $") + len("P&L: $")
                            pnl_end = rest.find(" (", pnl_start)
                            pnl_usd = _safe_float(rest[pnl_start:pnl_end], None)

                        # P&L %
                        pnl_pct = None
                        if "(" in rest and "%" in rest:
                            pct_start = rest.find("(", pnl_end) + 1
                            pct_end = rest.find("%", pct_start)
                            pnl_pct = _safe_float(rest[pct_start:pct_end], None)

                        # MFE / MAE są opcjonalne na końcu
                        mfe_pct = None
                        mae_pct = None

                        if "MFE:" in rest:
                            try:
                                mfe_part = rest.split("MFE:")[1].split("|")[0].strip()
                                mfe_pct = _safe_float(
                                    mfe_part.replace("%", "").strip(), None
                                )
                            except Exception:
                                mfe_pct = None

                        if "MAE:" in rest:
                            try:
                                mae_part = rest.split("MAE:")[1].strip()
                                mae_pct = _safe_float(
                                    mae_part.replace("%", "").strip(), None
                                )
                            except Exception:
                                mae_pct = None

                    except Exception:
                        continue

                    closes.append(
                        {
                            "timestamp": ts,
                            "symbol": symbol,
                            "exit_type": exit_type,
                            "pnl": pnl_usd,
                            "pnl_pct": pnl_pct,
                            "mfe_pct": mfe_pct,
                            "mae_pct": mae_pct,
                        }
                    )
        except Exception:
            pass

    # 3) Dopasowanie zamknięć do otwarć (po symbolu, pierwsze niezamknięte)
    closes_sorted = sorted(closes, key=lambda x: x["timestamp"])

    for cl in closes_sorted:
        for e in entries:
            if e["symbol"] == cl["symbol"] and e["closed_at"] is None:
                e["closed_at"] = cl["timestamp"]
                e["exit_type"] = cl["exit_type"]
                e["pnl"] = cl["pnl"]
                e["pnl_pct"] = cl["pnl_pct"]
                e["mfe_pct"] = cl["mfe_pct"]
                e["mae_pct"] = cl["mae_pct"]

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
