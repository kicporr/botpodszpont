from datetime import datetime
import pandas as pd
import requests

BINANCE_BASE_URL = "https://api.binance.com"


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
