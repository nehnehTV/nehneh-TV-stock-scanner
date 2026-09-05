"""
Stock/Crypto Trend-Following Scanner
======================================
Daily-bar Donchian breakout system with a 200-day trend filter and
ATR-based position sizing/stops -- a simplified version of the classic
Turtle Trading approach. Replaces an earlier 5m/15m MA-crossover setup
that whipsawed badly in chop; this is built around fewer rules and a
higher timeframe on purpose (see the reasoning in README.md).

  TREND FILTER (daily chart)
    - Close above 200-day MA -> bull regime (only take longs)
    - Close below 200-day MA -> bear regime (only take shorts)

  ENTRY (Donchian breakout)
    - Close breaks above the highest high of the last ENTRY_BREAKOUT_DAYS
      days, AND regime is bull -> long
    - Close breaks below the lowest low of the last ENTRY_BREAKOUT_DAYS
      days, AND regime is bear -> short

  POSITION SIZING (volatility-adjusted, not a fixed dollar amount)
    - shares = (equity x RISK_PER_TRADE_PCT) / (ATR_STOP_MULTIPLIER x ATR)
    - so a stop-out loses roughly RISK_PER_TRADE_PCT of equity
      regardless of how volatile the instrument is

  EXIT (two independent exits, whichever triggers first)
    - Channel exit: close breaks the OPPOSITE side of the shorter
      EXIT_BREAKOUT_DAYS channel (lets winners run with the trend)
    - ATR hard stop: price moves ATR_STOP_MULTIPLIER x ATR against
      entry (caps worst-case loss on any single trade)

This is a SCANNER (plus a paper-trading simulator). It does not place
real trades. Data comes from yfinance. For stocks, a daily bar is
provisional until market close -- checking mid-day shows where things
stand right now, not a final signal.

Run it with:  python stock_scanner.py
Stop it with: Ctrl+C
"""

import time
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import math
import numpy as np
import pandas as pd
import yfinance as yf

import levels_store

try:
    from plyer import notification
    DESKTOP_NOTIFICATIONS_AVAILABLE = True
except Exception:
    DESKTOP_NOTIFICATIONS_AVAILABLE = False


# ============================================================
# CONFIG -- edit this section to tune the strategy
# ============================================================

# Set True for crypto (24/7, no market-hours gate) or False for stocks.
# Crypto tickers use yfinance's format: "<SYMBOL>-USD", e.g. "BTC-USD".
CRYPTO_MODE = True

WATCHLIST = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD"] if CRYPTO_MODE else \
            ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]
# NOTE on diversification: a real edge in trend-following comes partly
# from spreading risk across UNCORRELATED instruments. Most crypto
# alts move with BTC most of the time, so this crypto watchlist is
# less diversified than it looks -- worth knowing, not something the
# code can fix for you.

# Daily bars only now -- no separate trend/trigger timeframes needed.
DAILY_INTERVAL = "1d"
DAILY_LOOKBACK = "2y"   # yfinance's daily history isn't capped like intraday is

# How often to re-check the watchlist, in seconds. Daily bars don't
# change until the next day's close, so polling every 60s mostly just
# re-confirms the same still-forming "today" bar -- an hourly check is
# plenty for stocks; for crypto (24/7, no clean daily close) more
# frequent checks are reasonable if you want to react sooner.
POLL_INTERVAL_SECONDS = 3600

TREND_MA_PERIOD = 200        # 200-day MA: long/short regime filter
ENTRY_BREAKOUT_DAYS = 20     # N-day high/low breakout triggers entry
EXIT_BREAKOUT_DAYS = 10      # shorter channel triggers exit (lets winners run)
ATR_PERIOD = 20              # "N" in Turtle-system terms
ATR_STOP_MULTIPLIER = 2.0    # hard stop = this many ATRs from entry
RISK_PER_TRADE_PCT = 0.01    # risk ~1% of equity per trade; position size scales with this

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)

LOG_FILE_PREFIX = "scanner_log"


# ============================================================
# Indicator math
# ============================================================

def add_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.DataFrame:
    """Wilder's ATR -- average true range, used for both position
    sizing (bigger ATR = smaller position) and the hard stop distance."""
    df = df.copy()
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / period, adjust=False).mean()
    return df


def add_moving_averages(df: pd.DataFrame, periods) -> pd.DataFrame:
    df = df.copy()
    for p in periods:
        df[f"MA{p}"] = df["Close"].rolling(p).mean()
    return df


def add_donchian_channels(df: pd.DataFrame, entry_days: int = ENTRY_BREAKOUT_DAYS,
                           exit_days: int = EXIT_BREAKOUT_DAYS) -> pd.DataFrame:
    """Rolling N-day high/low channels. shift(1) excludes today's own
    bar from its own channel -- otherwise a big move today would trivially
    'break out' of a level that includes today, which is meaningless."""
    df = df.copy()
    df["entry_high"] = df["High"].shift(1).rolling(entry_days).max()
    df["entry_low"] = df["Low"].shift(1).rolling(entry_days).min()
    df["exit_high"] = df["High"].shift(1).rolling(exit_days).max()
    df["exit_low"] = df["Low"].shift(1).rolling(exit_days).min()
    return df


# ============================================================
# Data fetching
# ============================================================

def fetch(ticker: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"No data returned for {ticker} ({interval})")
    return df


def get_daily_frame(ticker: str) -> pd.DataFrame:
    """The one fetch/compute per ticker -- everything else derives from
    this. Reused as-is by backtest.py so live and backtest logic can
    never drift apart."""
    df = fetch(ticker, DAILY_INTERVAL, DAILY_LOOKBACK)
    df = add_moving_averages(df, [TREND_MA_PERIOD])
    df = add_atr(df, ATR_PERIOD)
    df = add_donchian_channels(df, ENTRY_BREAKOUT_DAYS, EXIT_BREAKOUT_DAYS)
    return df


def evaluate_bar(row) -> dict:
    """Pure function: given one row of a daily frame (MA/ATR/Donchian
    columns already computed), returns the trend regime + breakout
    decision for that bar. Used by both analyze_ticker (live) and
    backtest.py (historical replay) -- kept as one function so the two
    can never disagree with each other."""
    ma_col = f"MA{TREND_MA_PERIOD}"
    ma = row.get(ma_col)
    if pd.isna(ma):
        trend = "insufficient_data"
    elif row["Close"] > ma:
        trend = "bull"
    elif row["Close"] < ma:
        trend = "bear"
    else:
        trend = "neutral"

    direction = None
    entry_high, entry_low = row.get("entry_high"), row.get("entry_low")
    if trend == "bull" and pd.notna(entry_high) and row["Close"] > entry_high:
        direction = "long"
    elif trend == "bear" and pd.notna(entry_low) and row["Close"] < entry_low:
        direction = "short"

    return {"trend": trend, "direction": direction}


def analyze_ticker(ticker: str) -> dict:
    """Live analysis for one ticker: trend, breakout direction (if any),
    current price/ATR/exit-channel levels, and the bar timestamp."""
    df = get_daily_frame(ticker)
    min_len = max(TREND_MA_PERIOD, ENTRY_BREAKOUT_DAYS, ATR_PERIOD) + 2
    if len(df) < min_len:
        return {"trend": "insufficient_data", "direction": None, "bar_ts": None,
                "price": None, "atr": None, "exit_high": None, "exit_low": None}

    last = df.iloc[-1]
    decision = evaluate_bar(last)
    return {
        "trend": decision["trend"],
        "direction": decision["direction"],
        "bar_ts": last.name,
        "price": last["Close"],
        "atr": last["ATR"],
        "exit_high": last["exit_high"],
        "exit_low": last["exit_low"],
    }


# ============================================================
# GEX / manual levels (independent of the trading strategy above --
# these are informational annotations you enter or that get a rough
# free-data estimate, not inputs to the entry/exit rules)
# ============================================================

def _bs_gamma(spot: float, strike: float, years_to_expiry: float, iv: float, r: float = 0.05) -> float:
    """Black-Scholes gamma. yfinance's option chain gives IV/open interest
    but not the Greeks directly, so gamma is derived from those inputs."""
    if spot <= 0 or strike <= 0 or years_to_expiry <= 0 or iv <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * years_to_expiry) / (iv * math.sqrt(years_to_expiry))
    return math.exp(-d1 ** 2 / 2) / (math.sqrt(2 * math.pi) * spot * iv * math.sqrt(years_to_expiry))


def get_gex_levels(ticker: str) -> dict:
    """
    DIY approximation of a 1-day call resistance / put support gamma
    exposure (GEX) level, built from free options-chain data (the
    nearest available expiration, treated as the '1D' proxy).

    IMPORTANT: this is NOT the same as a paid dealer-positioning feed
    (SpotGamma, Unusual Whales, etc.), which uses actual dealer books.
    This estimates gamma per strike from Black-Scholes using each
    contract's implied volatility and open interest, then calls the
    strike with the largest call-side exposure above spot "resistance"
    and the strike with the largest put-side exposure below spot
    "support". Treat it as a rough guide, not gospel -- manual entry
    always overrides it (see levels_store.py).
    """
    result = {"call_resistance": None, "put_support": None, "expiry": None}
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        if not expirations:
            return result

        expiry_str = expirations[0]  # soonest listed expiration
        chain = tk.option_chain(expiry_str)
        spot = tk.fast_info["last_price"]

        expiry_dt = datetime.strptime(expiry_str, "%Y-%m-%d").replace(
            hour=16, minute=0, tzinfo=MARKET_TZ
        )
        years_to_expiry = max(
            (expiry_dt - datetime.now(MARKET_TZ)).total_seconds() / (365 * 24 * 3600),
            1e-4,  # floor so 0DTE contracts near expiry don't blow up the formula
        )

        def gex_by_strike(df):
            totals = {}
            for _, row in df.iterrows():
                iv = row.get("impliedVolatility") or 0
                oi = row.get("openInterest") or 0
                strike = row["strike"]
                if iv <= 0 or oi <= 0:
                    continue
                gamma = _bs_gamma(spot, strike, years_to_expiry, iv)
                exposure = gamma * oi * 100 * spot ** 2 * 0.01  # exposure per 1% move
                totals[strike] = totals.get(strike, 0.0) + exposure
            return totals

        call_gex = gex_by_strike(chain.calls)
        put_gex = gex_by_strike(chain.puts)

        calls_above_spot = {k: v for k, v in call_gex.items() if k >= spot}
        puts_below_spot = {k: v for k, v in put_gex.items() if k <= spot}

        if calls_above_spot:
            result["call_resistance"] = max(calls_above_spot, key=calls_above_spot.get)
        if puts_below_spot:
            result["put_support"] = max(puts_below_spot, key=puts_below_spot.get)
        result["expiry"] = expiry_str
    except Exception:
        pass  # options chain may be unavailable for this ticker -- fail quietly
    return result


def get_effective_levels(ticker: str) -> dict:
    """
    Merges manually entered levels (leash top/bottom, river, and
    optional GEX overrides) on top of auto-computed GEX. Manual entry
    always wins when present. Returns a dict of
    {field: {"value": ..., "source": "manual"|"auto"|None}}.
    """
    gex = get_gex_levels(ticker)
    auto_values = {"call_resistance": gex["call_resistance"], "put_support": gex["put_support"]}
    return levels_store.get_effective_levels(ticker, auto_values)


# ============================================================
# Market hours check
# ============================================================

def is_market_hours(now=None) -> bool:
    if CRYPTO_MODE:
        return True  # crypto trades 24/7 -- no session gate
    now = now or datetime.now(MARKET_TZ)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


# ============================================================
# Alerting
# ============================================================

def log_line(text: str):
    stamp = datetime.now(MARKET_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    line = f"[{stamp}] {text}"
    print(line, flush=True)
    fname = f"{LOG_FILE_PREFIX}_{datetime.now(MARKET_TZ).strftime('%Y%m%d')}.txt"
    with open(fname, "a") as f:
        f.write(line + "\n")


def desktop_alert(title: str, message: str):
    if DESKTOP_NOTIFICATIONS_AVAILABLE:
        try:
            notification.notify(title=title, message=message, timeout=10)
        except Exception:
            pass  # never let a notification failure break the scan loop


# ============================================================
# Main scan loop
# ============================================================

@dataclass
class ScannerState:
    # Tracks last fired (direction, bar timestamp) per ticker so we don't
    # re-alert on the same bar every poll.
    last_signal: dict = field(default_factory=dict)


def scan_once(state: ScannerState):
    for ticker in WATCHLIST:
        try:
            result = analyze_ticker(ticker)
            direction, bar_ts = result["direction"], result["bar_ts"]
            if direction is None:
                continue

            already_fired = state.last_signal.get(ticker)  # (direction, bar_ts) or None
            if already_fired is not None and already_fired[1] == bar_ts:
                continue  # already alerted on this exact bar

            state.last_signal[ticker] = (direction, bar_ts)
            msg = (
                f"{ticker}: {direction.upper()} breakout -- "
                f"trend={result['trend']}, {ENTRY_BREAKOUT_DAYS}-day channel broken "
                f"(bar {bar_ts}, price {result['price']:.4f}, ATR {result['atr']:.4f})"
            )

            levels = get_effective_levels(ticker)
            level_bits = [
                f"{levels_store.LABELS[f]}={levels[f]['value']} ({levels[f]['source']})"
                for f in levels_store.FIELDS
                if levels[f]["value"] is not None
            ]
            if level_bits:
                msg += " | Levels: " + ", ".join(level_bits)

            log_line("SIGNAL: " + msg)
            desktop_alert(f"{ticker} {direction.upper()} breakout", msg)

        except Exception as e:
            log_line(f"ERROR scanning {ticker}: {e}")


def main():
    log_line(f"Scanner starting. Watchlist: {', '.join(WATCHLIST)}")
    log_line(f"Strategy: {ENTRY_BREAKOUT_DAYS}-day Donchian breakout, "
              f"{TREND_MA_PERIOD}-day trend filter, ATR-sized positions.")
    if not DESKTOP_NOTIFICATIONS_AVAILABLE:
        log_line("Note: desktop notifications unavailable (plyer not installed) -- "
                 "signals will still print/log.")

    state = ScannerState()
    try:
        while True:
            if is_market_hours():
                scan_once(state)
            else:
                log_line("Outside market hours (9:30-16:00 ET, Mon-Fri) -- sleeping.")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log_line("Scanner stopped by user.")
        sys.exit(0)
    except Exception:
        log_line("FATAL ERROR:\n" + traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
