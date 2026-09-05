"""
Offline backtester for the scanner's rules.

Fetches historical 15m (trend) and 5m (trigger) data, replays the exact
same trend-bias / crossover-trigger / MACD+RSI-confirmation logic used
live in stock_scanner.py -- bar by bar, with NO lookahead -- and drives
it through the same paper-trading engine (paper_trader.py) that the
live dashboard uses. This lets you compare rule sets on real historical
data instead of eyeballing a few days of live signals.

Run with:
    python backtest.py                     # current config, all tickers in WATCHLIST
    python backtest.py --ticker BTC-USD    # a single ticker
    python backtest.py --compare           # baseline (no chop filters) vs current config, side by side

yfinance's intraday history is capped at ~60 days regardless of what
you ask for -- that's a data-source limit, not something this script
controls, so a backtest window can't go back further than that.

IMPORTANT: this evaluates the RULES on past data. It is not a live
simulation and isn't a promise -- markets change, and this ignores
real slippage, fees, latency, and fills. Use it to compare rule sets
against each other, not to forecast a real-money return.
"""

import argparse
import sys

import pandas as pd

import stock_scanner as core
import paper_trader as pt


def compute_15m_frame(ticker: str) -> pd.DataFrame:
    df = core.fetch(ticker, core.TREND_INTERVAL, core.TREND_LOOKBACK)
    df = core.add_moving_averages(df, [core.TREND_MA_PERIOD])
    df = core.add_vwap(df)
    df = core.add_atr_adx(df, core.ADX_PERIOD)
    return df


def compute_5m_frame(ticker: str) -> pd.DataFrame:
    df = core.fetch(ticker, core.TRIGGER_INTERVAL, core.TRIGGER_LOOKBACK)
    df = core.add_moving_averages(df, [core.FAST_MA_PERIOD, core.SLOW_MA_PERIOD])
    df = core.add_macd(df)
    df = core.add_rsi(df)
    df = core.add_atr_adx(df, core.ADX_PERIOD)
    return df


def bias_series(df15: pd.DataFrame, buffer_atr: float) -> pd.Series:
    """Vectorized version of get_trend_bias -- same math, computed for
    every historical bar at once instead of just 'the latest one'."""
    price = df15["Close"]
    ma = df15[f"MA{core.TREND_MA_PERIOD}"]
    vwap = df15["VWAP"]
    buf = buffer_atr * df15["ATR"]

    bull = (price > ma + buf) & (price > vwap + buf)
    bear = (price < ma - buf) & (price < vwap - buf)

    bias = pd.Series("mixed", index=df15.index)
    bias[bull] = "bull"
    bias[bear] = "bear"
    bias[ma.isna() | df15["ATR"].isna()] = "insufficient_data"
    return bias


def signal_series(df5: pd.DataFrame, adx_floor: float, adx_rising_lookback: int,
                   min_sep_atr: float, confirm_bars: int) -> pd.Series:
    """Vectorized version of get_trigger_signal -- same math as the live
    function, computed for every historical bar at once. Set
    adx_floor=0, adx_rising_lookback=0, min_sep_atr=0, confirm_bars=1
    to reproduce the OLD (pre-chop-filter) behavior for comparison."""
    fast_col, slow_col = f"MA{core.FAST_MA_PERIOD}", f"MA{core.SLOW_MA_PERIOD}"
    fast, slow = df5[fast_col], df5[slow_col]

    state = pd.Series(0, index=df5.index)
    state[fast > slow] = 1
    state[fast < slow] = -1
    state[fast.isna() | slow.isna()] = 0

    # "Held for confirm_bars and NOT held confirm_bars+1 ago" -> fires
    # exactly once, on the bar the hold requirement is first satisfied.
    held_long = state.rolling(confirm_bars).apply(lambda x: (x == 1).all(), raw=True).fillna(0).astype(bool)
    held_short = state.rolling(confirm_bars).apply(lambda x: (x == -1).all(), raw=True).fillna(0).astype(bool)
    prior_state = state.shift(confirm_bars)

    fresh_long = held_long & (prior_state != 1)
    fresh_short = held_short & (prior_state != -1)

    separation_ok = (fast - slow).abs() >= (min_sep_atr * df5["ATR"])
    if adx_rising_lookback > 0:
        adx_ok = (df5["ADX"] >= adx_floor) & (df5["ADX"] > df5["ADX"].shift(adx_rising_lookback))
    else:
        adx_ok = df5["ADX"] >= adx_floor
    macd_bull = df5["MACD"] > df5["MACD_signal"]
    macd_bear = df5["MACD"] < df5["MACD_signal"]
    rsi_bull = df5["RSI"] > core.RSI_MIDLINE
    rsi_bear = df5["RSI"] < core.RSI_MIDLINE

    long_ok = fresh_long & separation_ok & adx_ok & macd_bull & rsi_bull
    short_ok = fresh_short & separation_ok & adx_ok & macd_bear & rsi_bear

    direction = pd.Series([None] * len(df5), index=df5.index, dtype=object)
    direction[long_ok.fillna(False)] = "long"
    direction[short_ok.fillna(False)] = "short"
    return direction


def align_bias_to_5m(df5: pd.DataFrame, df15: pd.DataFrame, buffer_atr: float) -> pd.Series:
    """As-of join: for each 5m bar, use the most recently COMPLETED 15m
    bar's bias -- never a future one. This is the causal join that
    makes the multi-timeframe backtest valid."""
    bias15 = bias_series(df15, buffer_atr)
    left = df5[[]].copy()
    left.index = left.index.rename("Datetime")
    left = left.reset_index()
    right = pd.DataFrame({"bias": bias15})
    right.index = right.index.rename("Datetime")
    right = right.reset_index()
    merged = pd.merge_asof(
        left.sort_values("Datetime"), right.sort_values("Datetime"),
        on="Datetime", direction="backward",
    )
    return merged.set_index("Datetime")["bias"]


def run_backtest(ticker: str, adx_floor: float, adx_rising_lookback: int, min_sep_atr: float,
                  confirm_bars: int, cooldown_bars: int, starting_balance: float = 10000.0) -> dict:
    df15 = compute_15m_frame(ticker)
    df5 = compute_5m_frame(ticker)

    bias5 = align_bias_to_5m(df5, df15, core.TREND_BUFFER_ATR)
    direction5 = signal_series(df5, adx_floor, adx_rising_lookback, min_sep_atr, confirm_bars)

    state = pt.new_state(starting_balance)
    last_fired_ts = None

    for ts, row in df5.iterrows():
        price = row["Close"]
        if pd.isna(price):
            continue
        bias = bias5.get(ts, "insufficient_data")
        direction = direction5.get(ts)

        confirmed = direction in ("long", "short") and (
            (direction == "long" and bias == "bull") or (direction == "short" and bias == "bear")
        )
        is_new_signal = False
        if confirmed and not core.cooldown_active(last_fired_ts, ts, cooldown_bars=cooldown_bars):
            is_new_signal = True
            last_fired_ts = ts

        pt.apply_signal(state, ticker, bias, direction if confirmed else None,
                         is_new_signal, price, ts.isoformat())
        pt.mark_to_market(state, {ticker: price}, ts.isoformat(), cap=None)

    return state


def summarize(ticker: str, label: str, state: dict) -> dict:
    trades = state["closed_trades"]
    equity = state["equity_curve"][-1][1] if state["equity_curve"] else state["cash"]
    total_pnl = equity - state["settings"]["starting_balance"]
    wr = pt.win_rate(state)
    reasons = pd.Series([t["reason"] for t in trades]).value_counts().to_dict() if trades else {}
    return {
        "ticker": ticker,
        "label": label,
        "trades": len(trades),
        "win_rate": wr,
        "total_pnl": total_pnl,
        "final_equity": equity,
        "exit_reasons": reasons,
    }


def print_summary(summary: dict):
    wr = f"{summary['win_rate']:.1f}%" if summary["win_rate"] is not None else "--"
    print(f"  [{summary['label']}] {summary['ticker']}: "
          f"{summary['trades']} trades, win rate {wr}, "
          f"P&L {summary['total_pnl']:+,.2f}, exits={summary['exit_reasons']}")


def main():
    parser = argparse.ArgumentParser(description="Backtest the scanner's rules against historical data.")
    parser.add_argument("--ticker", help="Single ticker to test (default: all of WATCHLIST)")
    parser.add_argument("--compare", action="store_true",
                         help="Run baseline (no chop filters) vs current config side by side")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting simulated balance")
    args = parser.parse_args()

    tickers = [args.ticker] if args.ticker else core.WATCHLIST

    print(f"Backtesting {len(tickers)} ticker(s): {', '.join(tickers)}")
    print(f"15m lookback: {core.TREND_LOOKBACK} | 5m lookback: {core.TRIGGER_LOOKBACK} "
          f"(yfinance's intraday history cap, not a choice made here)")
    print()

    for ticker in tickers:
        try:
            if args.compare:
                baseline = run_backtest(ticker, adx_floor=0, adx_rising_lookback=0, min_sep_atr=0,
                                         confirm_bars=1, cooldown_bars=0, starting_balance=args.balance)
                filtered = run_backtest(ticker, adx_floor=core.ADX_FLOOR,
                                         adx_rising_lookback=core.ADX_RISING_LOOKBACK,
                                         min_sep_atr=core.MIN_CROSS_SEPARATION_ATR,
                                         confirm_bars=core.CROSS_CONFIRM_BARS,
                                         cooldown_bars=core.SIGNAL_COOLDOWN_BARS,
                                         starting_balance=args.balance)
                print_summary(summarize(ticker, "baseline (unfiltered)", baseline))
                print_summary(summarize(ticker, "filtered (current config)", filtered))
            else:
                result = run_backtest(ticker, adx_floor=core.ADX_FLOOR,
                                       adx_rising_lookback=core.ADX_RISING_LOOKBACK,
                                       min_sep_atr=core.MIN_CROSS_SEPARATION_ATR,
                                       confirm_bars=core.CROSS_CONFIRM_BARS,
                                       cooldown_bars=core.SIGNAL_COOLDOWN_BARS,
                                       starting_balance=args.balance)
                print_summary(summarize(ticker, "current config", result))
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")
        print()


if __name__ == "__main__":
    main()
