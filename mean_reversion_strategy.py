import numpy as np
import pandas as pd
import time
import os


# =======================
# LOADERY DANYCH
# =======================

def load_from_binance(symbol: str, interval: str) -> pd.DataFrame:
    """
    Wczytuje dane OHLCV z pliku CSV wygenerowanego przez download_binance_portfolio.py.

    symbol: np. "BTC-USDT"
    interval: np. "1h" -> plik data_BTC_USDT_1h_binance.csv
    """
    base = symbol.replace("-", "_")
    fname = os.path.join("data_binance", f"data_{base}_{interval}_binance.csv")
    df = pd.read_csv(fname, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    # wymagane kolumny: open, high, low, close, volume, datetime
    return df

def split_into_segments(df: pd.DataFrame, n_segments: int) -> list[pd.DataFrame]:
    """
    Dzieli df na n_segments kolejnych, mniej więcej równych odcinków czasowych.
    """
    length = len(df)
    seg_size = length // n_segments
    segments = []

    for i in range(n_segments):
        start = i * seg_size
        end = (i + 1) * seg_size if i < n_segments - 1 else length
        seg = df.iloc[start:end].reset_index(drop=True)
        if len(seg) > 0:
            segments.append(seg)

    return segments


# =======================
# INDYKATORY
# =======================

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_bollinger_bands(series, period=20, std_dev=2):
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower


def compute_atr(high, low, close, period=14):
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr


# =======================
# FEATURES + SYGNAŁY
# =======================

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["rsi_14"] = compute_rsi(df["close"], period=14)
    df["bb_upper"], df["bb_middle"], df["bb_lower"] = compute_bollinger_bands(
        df["close"], period=20, std_dev=2
    )
    df["atr_14"] = compute_atr(df["high"], df["low"], df["close"], period=14)
    df["vol_20"] = df["close"].pct_change().rolling(20).std()
    df["bb_position"] = (df["close"] - df["bb_lower"]) / (
        df["bb_upper"] - df["bb_lower"]
    )

    df = df.dropna().reset_index(drop=True)
    return df


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    RSI 20/80 + Bollinger + filtr ATR:

    - LONG:  RSI < 20 i bb_position < 0.4 i ATR_14 > k * median_ATR_14_200
    - SHORT: RSI > 80 i bb_position > 0.6 i ATR_14 > k * median_ATR_14_200
    - jeśli brak nowego sygnału → utrzymuj poprzednią pozycję
    """

    df = df.copy()

    # rolling median ATR z 200 świec (możesz zmienić na rolling mean)
    df["atr_14_med_200"] = df["atr_14"].rolling(200).median()

    # współczynnik progu – na start np. 0.8–1.0
    atr_k = 0.6

    df["signal"] = 0
    for i in range(1, len(df)):
        rsi = df.iloc[i]["rsi_14"]
        bb_pos = df.iloc[i]["bb_position"]
        atr_now = df.iloc[i]["atr_14"]
        atr_med = df.iloc[i]["atr_14_med_200"]
        prev_signal = df.iloc[i - 1]["signal"]

        # jeśli nie mamy jeszcze sensownego ATR median – brak nowego sygnału
        if pd.isna(atr_now) or pd.isna(atr_med) or atr_now <= atr_k * atr_med:
            df.loc[i, "signal"] = prev_signal
            continue

        if rsi < 20 and bb_pos < 0.4:
            df.loc[i, "signal"] = 1
        elif rsi > 80 and bb_pos > 0.6:
            df.loc[i, "signal"] = -1
        else:
            df.loc[i, "signal"] = prev_signal

    return df



# =======================
# BACKTEST
# =======================

def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 100.0,
    position_size_pct: float = 0.30,
    max_loss_pct: float = 0.03,
) -> dict:
    capital = initial_capital
    position = None  # {entry_price, size, type, entry_time, stop_loss, take_profit}
    equity_curve = [initial_capital]
    trades = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        price = row["close"]
        signal = row["signal"]
        atr = row["atr_14"]

        # OPEN LONG
        if signal == 1 and position is None:
            pos_cap = capital * position_size_pct
            size = pos_cap / price

            sl_price = price * (1 - max_loss_pct)
            atr_sl = price - (1.2 * atr) if not pd.isna(atr) else sl_price
            stop_loss = max(sl_price, atr_sl)

            tp_price = price * 1.035
            bb_upper = row["bb_upper"]
            take_profit = min(tp_price, bb_upper) if not pd.isna(bb_upper) else tp_price

            position = {
                "entry_price": price,
                "size": size,
                "type": "long",
                "entry_time": i,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

        # OPEN SHORT
        elif signal == -1 and position is None:
            pos_cap = capital * position_size_pct
            size = pos_cap / price

            sl_price = price * (1 + max_loss_pct)
            atr_sl = price + (1.2 * atr) if not pd.isna(atr) else sl_price
            stop_loss = min(sl_price, atr_sl)

            tp_price = price * 0.965
            bb_lower = row["bb_lower"]
            take_profit = max(tp_price, bb_lower) if not pd.isna(bb_lower) else tp_price

            position = {
                "entry_price": price,
                "size": size,
                "type": "short",
                "entry_time": i,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

        # MANAGE OPEN POSITION
        elif position is not None:
            pnl = 0
            exit_price = price
            exit_reason = None

            if position["type"] == "long":
                if price <= position["stop_loss"]:
                    exit_price = position["stop_loss"]
                    pnl = (exit_price - position["entry_price"]) * position["size"]
                    exit_reason = "SL"
                elif price >= position["take_profit"]:
                    exit_price = position["take_profit"]
                    pnl = (exit_price - position["entry_price"]) * position["size"]
                    exit_reason = "TP"
                elif signal <= 0:
                    pnl = (exit_price - position["entry_price"]) * position["size"]
                    exit_reason = "Signal"

            elif position["type"] == "short":
                if price >= position["stop_loss"]:
                    exit_price = position["stop_loss"]
                    pnl = (position["entry_price"] - exit_price) * position["size"]
                    exit_reason = "SL"
                elif price <= position["take_profit"]:
                    exit_price = position["take_profit"]
                    pnl = (position["entry_price"] - exit_price) * position["size"]
                    exit_reason = "TP"
                elif signal >= 0:
                    pnl = (position["entry_price"] - exit_price) * position["size"]
                    exit_reason = "Signal"

            if exit_reason:
                capital += pnl
                trades.append(
                    {
                        "type": position["type"],
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price,
                        "size": position["size"],
                        "pnl": pnl,
                        "pnl_pct": pnl
                        / (position["entry_price"] * position["size"])
                        if position["entry_price"] > 0
                        else 0,
                        "entry_idx": position["entry_time"],
                        "exit_idx": i,
                        "exit_reason": exit_reason,
                    }
                )
                position = None

        equity = capital
        if position is not None:
            if position["type"] == "long":
                unrealized = (price - position["entry_price"]) * position["size"]
            else:
                unrealized = (position["entry_price"] - price) * position["size"]
            equity = capital + unrealized

        equity_curve.append(equity)

    # Close any open position at the end
    if position is not None:
        last_price = df.iloc[-1]["close"]
        if position["type"] == "long":
            pnl = (last_price - position["entry_price"]) * position["size"]
        else:
            pnl = (position["entry_price"] - last_price) * position["size"]
        capital += pnl
        trades.append(
            {
                "type": position["type"],
                "entry_price": position["entry_price"],
                "exit_price": last_price,
                "size": position["size"],
                "pnl": pnl,
                "pnl_pct": pnl
                / (position["entry_price"] * position["size"])
                if position["entry_price"] > 0
                else 0,
                "entry_idx": position["entry_time"],
                "exit_idx": len(df) - 1,
                "exit_reason": "End",
            }
        )

    equity_curve = np.array(equity_curve)
    total_return = (equity_curve[-1] / initial_capital) - 1
    max_equity = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - max_equity) / max_equity
    max_drawdown = drawdown.min() if len(drawdown) > 0 else 0

    if len(trades) > 0:
        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(trades) if trades else 0
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) < 0 else 0
    else:
        win_rate = 0
        profit_factor = 0

    rets = np.diff(equity_curve) / equity_curve[:-1]
    sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(252)) if np.std(rets) > 0 else 0

    return {
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "final_equity": equity_curve[-1],
        "num_trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "trades": trades,
        "equity_curve": equity_curve,
    }



def compute_features_for_live(df: pd.DataFrame) -> pd.DataFrame:
    """
    Przygotowuje feature'y tak jak w backteście, ale zostawia pełny df.
    Zakładamy kolumny: open, high, low, close, volume, datetime.
    """
    df = df.copy()
    df["rsi_14"] = compute_rsi(df["close"], period=14)
    df["bb_upper"], df["bb_middle"], df["bb_lower"] = compute_bollinger_bands(
        df["close"], period=20, std_dev=2
    )
    df["atr_14"] = compute_atr(df["high"], df["low"], df["close"], period=14)
    df["vol_20"] = df["close"].pct_change().rolling(20).std()
    df["bb_position"] = (df["close"] - df["bb_lower"]) / (
        df["bb_upper"] - df["bb_lower"]
    )
    return df


def generate_last_signal(df: pd.DataFrame, prev_signal: int = 0) -> int:
    """
    Zwraca sygnał dla ostatniej świecy:

    1  -> LONG
    -1 -> SHORT
    0  -> FLAT / brak zmiany (utrzymaj prev_signal)

    Z filtrem: ATR_14 bieżące musi być > k * rolling_median_ATR_14 z 200 świec.
    """
    # liczymy wskaźniki (na wszelki wypadek)
    df_feat = compute_features_for_live(df).dropna().reset_index(drop=True)
    if len(df_feat) < 200:
        # za mało danych na sensowną medianę ATR – nic nie zmieniamy
        return 0

    # rolling median ATR z 200 świec
    df_feat["atr_14_med_200"] = df_feat["atr_14"].rolling(200).median()
    atr_k = 0.6  # ten sam próg co w backteście

    rsi = df_feat.iloc[-1]["rsi_14"]
    bb_pos = df_feat.iloc[-1]["bb_position"]
    atr_now = df_feat.iloc[-1]["atr_14"]
    atr_med = df_feat.iloc[-1]["atr_14_med_200"]

    # jeśli niska zmienność – brak nowego sygnału
    if pd.isna(atr_now) or pd.isna(atr_med) or atr_now <= atr_k * atr_med:
        return prev_signal

    if rsi < 20 and bb_pos < 0.4:
        return 1
    elif rsi > 80 and bb_pos > 0.6:
        return -1
    else:
        return prev_signal



# =======================
# PORTFOLIO 1H (BINANCE, 5000 ŚWIEC)
# =======================

def run_portfolio_backtest_binance_1h():
    symbols = ["BTC-USDT", "ETH-USDT", "XRP-USDT", "BCH-USDT", "BNB-USDT"]
    timeframe = "1H"
    interval_binance = "1h"
    initial_capital_per_symbol = 100.0
    total_initial = initial_capital_per_symbol * len(symbols)

    print("\n" + "=" * 100)
    print(f"MEAN-REVERSION AGGRESSIVE PORTFOLIO ({timeframe}, RSI 20/80, LONG+SHORT, Binance 5000 bars)")
    print("=" * 100 + "\n")

    total_equity = 0.0
    results = []

    # ile segmentów chcesz (np. 3 okresy)
    n_segments = 3
    per_symbol_segments = {s: [] for s in symbols}

    start = time.time()

    for symbol in symbols:
        print(f" {symbol}...", end=" ", flush=True)

        df = load_from_binance(symbol, interval_binance)
        df_feat = build_features(df)
        df_sig = generate_signals(df_feat)

        split_idx = int(len(df_sig) * 0.7)
        test_df = df_sig.iloc[split_idx:].reset_index(drop=True)

        # pełny okres
        res_full = run_backtest(
            test_df,
            initial_capital=initial_capital_per_symbol,
            position_size_pct=0.50,
            max_loss_pct=0.03,
        )
        total_equity += res_full["final_equity"]

        print(
            f"Return: {res_full['total_return']*100:+.2f}% | "
            f"DD: {res_full['max_drawdown']*100:.2f}% | "
            f"Trades: {res_full['num_trades']} | "
            f"WR: {res_full['win_rate']*100:.1f}% | "
            f"PF: {res_full['profit_factor']:.2f} | "
            f"Final: ${res_full['final_equity']:.2f}"
        )

        results.append({"symbol": symbol, **res_full})

        # podział na segmenty
        segments = split_into_segments(test_df, n_segments)
        seg_results = []
        for j, seg_df in enumerate(segments, start=1):
            seg_res = run_backtest(
                seg_df,
                initial_capital=initial_capital_per_symbol,
                position_size_pct=0.30,
                max_loss_pct=0.03,
            )
            seg_results.append(seg_res)

        per_symbol_segments[symbol] = seg_results

    elapsed = time.time() - start

    print("\n" + "=" * 100)
    print("PORTFOLIO SUMMARY")
    print("=" * 100)
    print(f"Initial Capital: ${total_initial:.2f}")
    print(f"Final Equity:    ${total_equity:.2f}")
    print(f"Total Return:    {((total_equity / total_initial) - 1)*100:+.2f}%")
    print(f"Total Time:      {elapsed:.2f}s\n")

    print("=" * 100)
    print("INDIVIDUAL RESULTS (FULL PERIOD)")
    print("=" * 100)
    for r in results:
        print(
            f"{r['symbol']}: Return {r['total_return']*100:+.2f}% | "
            f"DD {r['max_drawdown']*100:.2f}% | Trades {r['num_trades']} | "
            f"WR {r['win_rate']*100:.1f}% | Sharpe {r['sharpe']:.2f}"
        )

    print("\n" + "=" * 100)
    print("SEGMENTED RESULTS PER SYMBOL")
    print("=" * 100)
    for symbol in symbols:
        segs = per_symbol_segments[symbol]
        print(f"\n{symbol}:")
        for idx, seg_res in enumerate(segs, start=1):
            print(
                f"  Segment {idx}: Return {seg_res['total_return']*100:+.2f}% | "
                f"DD {seg_res['max_drawdown']*100:.2f}% | "
                f"Trades {seg_res['num_trades']} | "
                f"WR {seg_res['win_rate']*100:.1f}% | "
                f"PF {seg_res['profit_factor']:.2f} | "
                f"Sharpe {seg_res['sharpe']:.2f}"
            )



def main():
    run_portfolio_backtest_binance_1h()





if __name__ == "__main__":
    main()
