# Stock Setup Scanner

Watches your watchlist during market hours and alerts you when a ticker
meets your setup. Scanner only — no trading yet (that's phase 2).

## The logic

**Trend filter (15-min chart):**
- Price above 200 MA *and* above VWAP → bullish regime
- Price below 200 MA *and* below VWAP → bearish regime
- Anything mixed → ignored, no trade

**Trigger (5-min chart):**
- 5 MA crosses above 10 MA → long trigger
- 5 MA crosses below 10 MA → short trigger

**Confirmation (5-min chart), must match the trigger direction:**
- MACD line above/below its signal line
- RSI(14) above/below 50

A signal only fires when trend + trigger + both confirmations line up,
and each bar only fires once per ticker so you're not spammed every
minute.

## Setup

1. Install Python 3.9+ if you don't have it.
2. In this folder, install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Open `stock_scanner.py` and edit the `WATCHLIST` list near the top
   to your tickers.

## Running it

```
python stock_scanner.py
```

Leave the window open. It checks every 60 seconds (configurable via
`POLL_INTERVAL_SECONDS`), only during 9:30am–4:00pm ET, Monday–Friday.
When a setup fires you'll get:
- A line printed in the terminal
- A line written to `scanner_log_YYYYMMDD.txt` in this folder
- A desktop popup notification (if `plyer` is installed and your OS
  supports it)

Stop it any time with `Ctrl+C`.

## Things worth knowing

- **Data is yfinance (free), typically ~15–20 minutes delayed.** Fine
  for spotting setups, not fine for split-second entries. Treat every
  alert as "go check the real chart," not "the trade already happened."
- **Weekends/holidays:** the script checks weekday + time only — it
  doesn't know about market holidays, so it'll try to scan and just get
  empty/stale data on holidays. Not harmful, just noisy in the log.
- **First run each day** may take a few extra seconds while it pulls
  15-min history for the 200 MA.
- **Tuning:** all the knobs (MA periods, RSI midline, timeframes,
  lookback windows, poll interval) are in the CONFIG section at the top
  of `stock_scanner.py`.

## Dashboard (nicer UI)

Instead of the terminal version, you can run a live browser dashboard:

```
streamlit run scanner_dashboard.py
```

This opens a local web page (usually `http://localhost:8501`) showing:
- A market-open/closed banner with the current ET time
- A card per ticker with its trend bias (BULL/BEAR/MIXED), price, MACD,
  RSI, and a green/red "LONG SIGNAL" / "SHORT SIGNAL" badge when a setup
  fires
- An activity log of every signal fired this session
- Sidebar controls to edit your watchlist and refresh interval live,
  without editing code

It reuses the exact same logic from `stock_scanner.py` (imports it as a
module), so both versions will always agree — pick whichever interface
you prefer, or run both.

Note: while the dashboard tab is open, it auto-refreshes on its own
timer, so you don't need the terminal version running at the same time
unless you specifically want the plain log file too.

## Crypto mode

The scanner defaults to crypto now (`CRYPTO_MODE = True` at the top of
`stock_scanner.py`). What changes:

- **Watchlist** defaults to `BTC-USD`, `ETH-USD`, `SOL-USD`, `XRP-USD`,
  `DOGE-USD` — yfinance's crypto ticker format is `<SYMBOL>-USD`.
- **24/7 scanning** — the market-hours gate is bypassed entirely, so it
  scans around the clock, weekends included. The "market closed"
  banner and the "scan even when closed" checkbox go away in crypto
  mode since they don't apply.
- **GEX (call resistance / put support) has no data source for
  crypto** — that feature is built from options-chain data, which
  yfinance doesn't provide for crypto pairs. Those two fields will
  just show "not set" unless you type in a manual value yourself.
  Leash Top/Bottom and River are unaffected — they were always manual
  entry only.
- Everything else — trend filter, MA crossover trigger, MACD/RSI
  confirmation, paper trading, sparklines, activity log — works
  exactly the same on crypto pairs.

To switch back to stocks, set `CRYPTO_MODE = False` at the top of
`stock_scanner.py` (the watchlist default and market-hours behavior
switch back automatically).

## Paper trading (simulated balance & P&L)

The dashboard now runs a simulated account alongside the scanner:

- When a signal fires, it "opens" a simulated position at that price.
- Positions close automatically on a **stop-loss**, a **take-profit**,
  a **trend-flip** (the 15m bias no longer supports the position), or
  an **opposite signal** (reversal).
- Balance, total P&L, win rate, and an equity curve are shown at the
  top of the dashboard, all computed from these simulated trades —
  nothing is invented for looks.
- Position size, stop-loss %, and take-profit % are adjustable in the
  sidebar. A "Reset paper account" button (behind a confirmation
  checkbox) wipes the simulated history and starts fresh.
- State is stored in `paper_trades.json`, so it survives restarts.

**This places no real orders and involves no real money.** It also
isn't a guarantee — the simulation ignores real-world slippage, fees,
partial fills, and liquidity, so live results with an actual broker
would differ. Treat it as a sanity check on the scanner's logic, not
proof the strategy is profitable.

## Cosmetic agent avatars

Each ticker card now has a small colored shape-avatar (matching its
callsign) purely for visual flavor — no functional effect.

## Custom levels: leash top/bottom, river, GEX

Each ticker card in the dashboard now has an "Edit levels" section where
you can manually enter:

- **Leash Top / Leash Bottom / River** — no automatic source for these
  (they aren't standard indicators I can calculate), so they're manual
  entry only. Type a number and it saves automatically; clear the field
  to remove it.
- **Call Resistance / Put Support (GEX)** — auto-computed as a rough
  approximation from the nearest available options expiration (treated
  as the "1-day" proxy): it derives each contract's gamma from its
  implied volatility (Black-Scholes), weights it by open interest, and
  picks the strike with the largest call-side exposure above the
  current price (resistance) and the largest put-side exposure below it
  (support). **This is not the same as a paid dealer-positioning feed**
  (SpotGamma, Unusual Whales, etc.) — those use real dealer books, this
  is a free-data estimate. You can type your own number in the same
  field to override the auto value any time; a manual entry always
  wins, and clearing the field goes back to auto.

All five levels are stored in `levels_overrides.json` in the project
folder, shared between the dashboard and the terminal scanner — enter a
level once in the dashboard and the terminal version's signal log will
include it too. These levels are currently **informational only**; they
show up on the card and in the signal log but don't yet gate whether a
signal fires. Say the word if you want them wired into the trigger
logic (e.g., "only long if price is above the river").

## Chop filters (reducing false signals)

The original trigger logic (raw MA5/MA10 crossover) whipsawed badly in
sideways markets -- a crossover by a hair's width counted the same as
a real move. Five filters were added to `get_trigger_signal` /
`get_trend_bias` in `stock_scanner.py` to address that:

- **ADX floor + rising** -- ADX (a standard trend-strength measure)
  must be above a modest floor (`ADX_FLOOR = 8`) AND higher than it
  was `ADX_RISING_LOOKBACK` (5) bars ago. Earlier version of this
  required ADX above a high static threshold (20) at the exact bar a
  cross confirmed -- backtesting showed that eliminated ~95% of
  otherwise-valid signals, because ADX is a lagging/smoothed
  indicator that hasn't caught up yet right when a fresh cross
  happens. The floor+rising version fixes that.
- **Minimum cross separation** (`MIN_CROSS_SEPARATION_ATR = 0.15`) --
  the MA5/MA10 gap must exceed this multiple of ATR, not just be
  positive. Skips razor-thin crosses.
- **Confirm bars** (`CROSS_CONFIRM_BARS = 2`) -- the crossed state
  must hold for 2 consecutive bars before firing, and fires exactly
  once (not every bar after).
- **Trend buffer** (`TREND_BUFFER_ATR = 0.25`) -- price must clear the
  200MA/VWAP by this multiple of ATR before counting as bull/bear,
  instead of flipping bias every time price grazes the line.
- **Cooldown** (`SIGNAL_COOLDOWN_BARS = 6`) -- no new signal on the
  same ticker within 6 bars of the last one, live in both
  `stock_scanner.py` and `scanner_dashboard.py`.

**Honest caveat, found via testing, not assumed:** stacking several
independent filters multiplies their individual pass rates together.
In backtesting against synthetic data, this occasionally reduced a
ticker's signal count to zero for a given window, even with the
corrected ADX design. That's an inherent property of filter-stacking,
not a bug -- but it means you should actually run the backtester
below against your real watchlist rather than assume the defaults are
right, and know which knob to loosen first if it's too strict:
1. Increase `ADX_RISING_LOOKBACK` (gives ADX more time to show it's rising)
2. Lower `MIN_CROSS_SEPARATION_ATR`
3. Lower `CROSS_CONFIRM_BARS` to 1 (back toward the original raw-cross behavior)

## Backtesting (`backtest.py`)

Replays the exact same trend/trigger/confirmation logic against
historical data -- no lookahead, causal multi-timeframe alignment --
and drives it through the same paper-trading engine used by the live
dashboard, so you get a real trade list instead of eyeballing a few
days of live signals.

```
python backtest.py                  # current filter config, all tickers in WATCHLIST
python backtest.py --ticker BTC-USD # a single ticker
python backtest.py --compare        # baseline (unfiltered, old behavior) vs current config, side by side
```

Output is a trade count, win rate, total simulated P&L, and a
breakdown of exit reasons (stop-loss / take-profit / trend-flip /
reversal) per ticker.

**Limits worth knowing:**
- yfinance's intraday history is capped at roughly 60 days regardless
  of what's asked for -- that's a data-source limit, not a setting
  here.
- This evaluates the RULES on past data. It is not a live simulation,
  and it ignores real slippage, fees, latency, and fills. Use it to
  compare rule sets against each other (baseline vs. filtered, or one
  filter value vs. another), not to forecast a real-money return.

## Next step: execution

Once you're happy with how the scanner is calling setups, phase 2 is
wiring a signal straight into an order — most straightforward path is
Alpaca's API (free paper trading to test against first). We can build
that whenever you're ready; the scanner is already structured so the
signal-firing point is a single, easy-to-hook place in `scan_once()`.
