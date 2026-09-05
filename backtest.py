"""
Offline backtester for the daily trend-following strategy.

Fetches historical daily data, replays the exact same trend-filter /
Donchian-breakout logic used live in stock_scanner.py (via
core.evaluate_bar -- the SAME function, not a reimplementation, so
live and backtest can never drift apart), and drives it through the
same paper-trading engine (paper_trader.py) used by the live
dashboard. No lookahead: pandas rolling/ewm functions only look
backward, so computing indicators once on the full historical frame
and then replaying row by row is valid.

Run with:
    python backtest.py                     # current config, all tickers in WATCHLIST
    python backtest.py --ticker BTC-USD    # a single ticker

yfinance's daily history isn't capped the way intraday is, but
DAILY_LOOKBACK (2 years by default) still limits how far back this
looks -- edit that in stock_scanner.py for a longer test.

IMPORTANT: this evaluates the STRATEGY on past data. It is not a live
simulation and isn't a promise -- markets change, and this ignores
real slippage, fees, latency, and fills. A trend-following system's
backtest can also look very different depending on which years it
covers (it needs actual trends to work at all) -- a short or
unlucky window isn't the whole story.
"""

import argparse

import pandas as pd

import stock_scanner as core
import paper_trader as pt


def run_backtest(ticker: str, starting_balance: float = 10000.0) -> dict:
    df = core.get_daily_frame(ticker)
    state = pt.new_state(starting_balance)
    last_fired_ts = None

    for ts, row in df.iterrows():
        price = row["Close"]
        if pd.isna(price):
            continue

        decision = core.evaluate_bar(row)
        direction = decision["direction"]
        is_new_signal = direction is not None and last_fired_ts != ts
        if is_new_signal:
            last_fired_ts = ts

        pt.apply_breakout_signal(
            state, ticker, direction, is_new_signal, price,
            row.get("ATR"), row.get("exit_high"), row.get("exit_low"),
            ts.isoformat(),
        )
        pt.mark_to_market(state, {ticker: price}, ts.isoformat(), cap=None)

    return state


def summarize(ticker: str, state: dict) -> dict:
    trades = state["closed_trades"]
    equity = state["equity_curve"][-1][1] if state["equity_curve"] else state["cash"]
    total_pnl = equity - state["settings"]["starting_balance"]
    wr = pt.win_rate(state)
    reasons = pd.Series([t["reason"] for t in trades]).value_counts().to_dict() if trades else {}
    return {
        "ticker": ticker,
        "trades": len(trades),
        "win_rate": wr,
        "total_pnl": total_pnl,
        "final_equity": equity,
        "exit_reasons": reasons,
    }


def print_summary(summary: dict):
    wr = f"{summary['win_rate']:.1f}%" if summary["win_rate"] is not None else "--"
    print(f"  {summary['ticker']}: {summary['trades']} trades, win rate {wr}, "
          f"P&L {summary['total_pnl']:+,.2f}, exits={summary['exit_reasons']}")


def main():
    parser = argparse.ArgumentParser(description="Backtest the daily trend-following strategy.")
    parser.add_argument("--ticker", help="Single ticker to test (default: all of WATCHLIST)")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting simulated balance per ticker")
    args = parser.parse_args()

    tickers = [args.ticker] if args.ticker else core.WATCHLIST

    print(f"Backtesting {len(tickers)} ticker(s): {', '.join(tickers)}")
    print(f"Daily lookback: {core.DAILY_LOOKBACK} | "
          f"{core.ENTRY_BREAKOUT_DAYS}-day entry / {core.EXIT_BREAKOUT_DAYS}-day exit channels | "
          f"{core.TREND_MA_PERIOD}-day trend filter | ATR stop x{core.ATR_STOP_MULTIPLIER} | "
          f"risk {core.RISK_PER_TRADE_PCT*100:.1f}%/trade")
    print()

    for ticker in tickers:
        try:
            result = run_backtest(ticker, starting_balance=args.balance)
            print_summary(summarize(ticker, result))
        except Exception as e:
            print(f"  [{ticker}] ERROR: {e}")


if __name__ == "__main__":
    main()
