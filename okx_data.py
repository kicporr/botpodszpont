import os
import pandas as pd

DATA_SHRIMPY_DIR = "./data_shrimpy"

def load_1h_from_shrimpy(symbol: str) -> pd.DataFrame:
    fname = os.path.join(DATA_SHRIMPY_DIR, f"data_{symbol.replace('-', '_')}_1h_shrimpy.csv")
    df = pd.read_csv(fname, parse_dates=["datetime"])
    df = df.rename(columns={"vol": "volume"})
    return df.sort_values("datetime").reset_index(drop=True)





