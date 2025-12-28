# download_binance_portfolio_15m.py
import os
import time
import requests
import pandas as pd

BINANCE_BASE_URL = "https://api.binance.com"
DATA_BINANCE_DIR = "./data_binance"

PORTFOLIO_SYMBOLS = {
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT",
    "XRP-USDT": "XRPUSDT",
    "SOL-USDT": "SOLUSDT",
}

MAX_BINANCE_LIMIT = 1000  # max na jedno wywołanie


def fetch_binance_klines_once(symbol: str, interval: str, limit: int = 1000, end_time: int | None = None):
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    if end_time is not None:
        params["endTime"] = end_time

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def download_last_n_klines_to_csv(
    symbol_binance: str,
    symbol_human: str,
    interval: str,
    n_candles: int,
):
    os.makedirs(DATA_BINANCE_DIR, exist_ok=True)

    remaining = n_candles
    all_rows = []
    end_time = None  # zaczynamy od "teraz"

    while remaining > 0:
        batch_size = min(MAX_BINANCE_LIMIT, remaining)
        klines = fetch_binance_klines_once(
            symbol=symbol_binance,
            interval=interval,
            limit=batch_size,
            end_time=end_time,
        )
        if not klines:
            break

        for k in klines:
            all_rows.append(
                {
                    "ts": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                }
            )

        first_open_time = int(klines[0][0])
        end_time = first_open_time - 1

        remaining -= len(klines)
        time.sleep(0.2)

        if len(klines) < batch_size:
            break

    if not all_rows:
        print(f"Brak danych dla {symbol_binance}")
        return

    df = pd.DataFrame(all_rows)
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms")
    df = df.sort_values("datetime").reset_index(drop=True)

    if len(df) > n_candles:
        df = df.iloc[-n_candles:].reset_index(drop=True)

    base = symbol_human.replace("-", "_")
    fname = f"data_{base}_{interval}_binance.csv"
    out_path = os.path.join(DATA_BINANCE_DIR, fname)
    df.to_csv(out_path, index=False)
    print(f"{symbol_human} {interval}: zapisano {len(df)} świec do {out_path}")


def main():
    n_candles = 5000
    interval = "15m"

    for human_symbol, binance_symbol in PORTFOLIO_SYMBOLS.items():
        download_last_n_klines_to_csv(
            symbol_binance=binance_symbol,
            symbol_human=human_symbol,
            interval=interval,
            n_candles=n_candles,
        )


if __name__ == "__main__":
    main()
