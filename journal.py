import os
from datetime import datetime


def parse_timestamp_from_line(line: str) -> str:
    try:
        start = line.find("[") + 1
        end = line.find("]", start)
        return line[start:end]
    except Exception:
        return ""


def load_journal_records(page_size: int = 50):
    entries = []

    # 1) Odczyt otwarć z trade_signals.log
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

                        left = parts[0].strip()  # "BTCUSDT BUY @ $12345.00"
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
            pass

    # 2) Odczyt zamknięć z trade_results.log (z MFE/MAE)
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

                        # EXIT TYPE
                        exit_type = rest.split("@")[1].split("|")[0].strip()

                        # P&L USD
                        pnl_start = rest.find("P&L: $") + len("P&L: $")
                        pnl_end = rest.find(" (", pnl_start)
                        pnl_usd = float(rest[pnl_start:pnl_end])

                        # P&L %
                        pct_start = rest.find("(", pnl_end) + 1
                        pct_end = rest.find("%", pct_start)
                        pnl_pct = float(rest[pct_start:pct_end])

                        # MFE / MAE są opcjonalnie na końcu, np.:
                        # "... | P&L: $10.00 (1.00%) | MFE: 2.50% | MAE: -1.20%"
                        mfe_pct = None
                        mae_pct = None
                        if "MFE:" in rest and "MAE:" in rest:
                            try:
                                mfe_part = rest.split("MFE:")[1].split("|")[0].strip()
                                mfe_pct = float(mfe_part.replace("%", "").strip())
                            except Exception:
                                mfe_pct = None
                            try:
                                mae_part = rest.split("MAE:")[1].strip()
                                mae_pct = float(mae_part.replace("%", "").strip())
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
