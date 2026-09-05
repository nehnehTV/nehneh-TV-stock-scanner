"""
Stock Setup Scanner
====================
Watches a list of tickers during market hours and flags when they meet a
multi-timeframe long/short setup:

  TREND FILTER (15-minute chart)
    - Price above 200 MA AND above VWAP  -> bullish regime
    - Price below 200 MA AND below VWAP  -> bearish regime
    - Anything mixed                     -> no trade, sit out

  TRIGGER (5-minute chart)
    - 5 MA crosses above 10 MA -> long trigger
    - 5 MA crosses below 10 MA -> short trigger

  CONFIRMATION (5-minute chart)
    - MACD line above/below its signal line, matching direction
    - RSI(14) above/below 50, matching direction

A signal only fires when ALL THREE agree. Each (ticker, direction, bar)
only fires once, so you don't get spammed every poll.

This is a SCANNER ONLY. It does not place trades. Data comes from
yfinance, which is free but typically ~15-20 minutes delayed, and not
suitable for a live order-execution engine -- treat every alert as
something to verify on your own broker/chart before acting.

Run it with:  python stock_scanner.py
Stop it with: Ctrl+C
"""

import math
import time
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

import levels_store

try:
    from plyer import notification
    DESKTOP_NOTIFICATIONS_AVAILABLE = True
except Exception:
    DESKTOP_NOTIFICATIONS_AVAILABLE = False


# ============================================================
# CONFIG -- edit this section to tune the scanner
# ============================================================

WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]

# How often to re-check the whole watchlist, in seconds.
# 5-min/15-min bars don't update faster than the underlying candle anyway,
# but polling every 60s means you catch a new bar close quickly.
POLL_INTERVAL_SECONDS = 60

# Trend filter timeframe/lookback
TREND_INTERVAL = "15m"
TREND_LOOKBACK = "60d"     # yfinance max history for 15m bars
TREND_MA_PERIOD = 200

# Trigger/confirmation timeframe/lookback
TRIGGER_INTERVAL = "5m"
TRIGGER_LOOKBACK = "5d"    # plenty of 5m bars for MA10/MACD/RSI warmup
FAST_MA_PERIOD = 5
SLOW_MA_PERIOD = 10

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
RSI_MIDLINE = 50  # RSI above this = bullish tilt, below = bearish tilt

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)

LOG_FILE_PREFIX = "scanner_log"


# ============================================================
# Indicator math
# ============================================================

def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Session VWAP, resetting each calendar day."""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    df = df.copy()
    df["_date"] = df.index.date
    df["_tpv"] = typical_price * df["Volume"]
    df["cum_tpv"] = df.groupby("_date")["_tpv"].cumsum()
    df["cum_vol"] = df.groupby("_date")["Volume"].cumsum()
    df["VWAP"] = df["cum_tpv"] / df["cum_vol"]
    return df


def add_moving_averages(df: pd.DataFrame, periods) -> pd.DataFrame:
    df = df.copy()
    for p in periods:
        df[f"MA{p}"] = df["Close"].rolling(p).mean()
    return df


def add_macd(df: pd.DataFrame, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL) -> pd.DataFrame:
    df = df.copy()
    ema_fast = df["Close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=slow, adjust=False).mean()
    df["MACD"] = ema_fast - ema_slow
    df["MACD_signal"] = df["MACD"].ewm(span=signal, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
    return df


def add_rsi(df: pd.DataFrame, period=RSI_PERIOD) -> pd.DataFrame:
    df = df.copy()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(100)  # no losses at all -> maxed out RSI
    return df


# ============================================================
# Data fetching
# ============================================================

def fetch(ticker: str, interval: str, period: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"No data returned for {ticker} ({interval})")
    # yfinance intraday index is already tz-aware in exchange local time
    return df


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


def get_trend_bias(ticker: str) -> str:
    """Returns 'bull', 'bear', or 'mixed' based on the 15m chart."""
    df = fetch(ticker, TREND_INTERVAL, TREND_LOOKBACK)
    df = add_moving_averages(df, [TREND_MA_PERIOD])
    df = add_vwap(df)
    last = df.iloc[-1]

    if pd.isna(last[f"MA{TREND_MA_PERIOD}"]):
        return "insufficient_data"

    price = last["Close"]
    above_ma = price > last[f"MA{TREND_MA_PERIOD}"]
    above_vwap = price > last["VWAP"]

    if above_ma and above_vwap:
        return "bull"
    if not above_ma and not above_vwap:
        return "bear"
    return "mixed"


def get_trigger_signal(ticker: str):
    """
    Checks the 5m chart for a fresh MA5/MA10 crossover confirmed by
    MACD + RSI direction. Returns (direction, bar_timestamp) or (None, None).
    direction is 'long', 'short', or None.
    """
    df = fetch(ticker, TRIGGER_INTERVAL, TRIGGER_LOOKBACK)
    df = add_moving_averages(df, [FAST_MA_PERIOD, SLOW_MA_PERIOD])
    df = add_macd(df)
    df = add_rsi(df)

    if len(df) < SLOW_MA_PERIOD + 2:
        return None, None

    prev = df.iloc[-2]
    last = df.iloc[-1]

    fast_col, slow_col = f"MA{FAST_MA_PERIOD}", f"MA{SLOW_MA_PERIOD}"
    if pd.isna(prev[fast_col]) or pd.isna(prev[slow_col]):
        return None, None

    crossed_up = prev[fast_col] <= prev[slow_col] and last[fast_col] > last[slow_col]
    crossed_down = prev[fast_col] >= prev[slow_col] and last[fast_col] < last[slow_col]

    macd_bullish = last["MACD"] > last["MACD_signal"]
    macd_bearish = last["MACD"] < last["MACD_signal"]
    rsi_bullish = last["RSI"] > RSI_MIDLINE
    rsi_bearish = last["RSI"] < RSI_MIDLINE

    if crossed_up and macd_bullish and rsi_bullish:
        return "long", last.name
    if crossed_down and macd_bearish and rsi_bearish:
        return "short", last.name

    return None, None


# ============================================================
# Market hours check
# ============================================================

def is_market_hours(now=None) -> bool:
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
            bias = get_trend_bias(ticker)
            if bias not in ("bull", "bear"):
                continue  # mixed regime or not enough data -> sit out

            direction, bar_ts = get_trigger_signal(ticker)
            if direction is None:
                continue

            # Trigger must agree with the higher-timeframe bias
            if (direction == "long" and bias != "bull") or (direction == "short" and bias != "bear"):
                continue

            key = ticker
            already_fired = state.last_signal.get(key)
            if already_fired == (direction, bar_ts):
                continue  # already alerted on this exact bar

            state.last_signal[key] = (direction, bar_ts)
            msg = (
                f"{ticker}: {direction.upper()} setup -- "
                f"15m trend={bias}, MA5/MA10 cross, MACD+RSI confirmed "
                f"(bar {bar_ts})"
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
            desktop_alert(f"{ticker} {direction.upper()} setup", msg)

        except Exception as e:
            log_line(f"ERROR scanning {ticker}: {e}")


def main():
    log_line(f"Scanner starting. Watchlist: {', '.join(WATCHLIST)}")
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
