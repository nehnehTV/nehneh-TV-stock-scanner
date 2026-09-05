# Trend-Following Scanner

Watches your watchlist and alerts you when a ticker breaks out of a
trend-following setup on the daily chart. Scanner + paper trading only
— no real trades placed (that's a future phase).

**This replaced an earlier 5m/15m MA-crossover + MACD/RSI setup**,
which whipsawed constantly in choppy markets even after several rounds
of chop filters. The problems were structural: short timeframes have
poor signal-to-noise ratio, and stacking many independent filters
together turned out to reduce signal count more than expected (see
git history / prior conversation for the debugging that led here).
This strategy trades those problems for a different, more
well-established one: it's slower, produces far fewer signals, and can
have long stretches with no trades while waiting for a real trend.

## The logic

**Trend filter (daily chart):**
- Close above the 200-day MA → bull regime, only take longs
- Close below the 200-day MA → bear regime, only take shorts

**Entry — Donchian breakout:**
- Close breaks above the highest high of the last 20 days, in a bull
  regime → long
- Close breaks below the lowest low of the last 20 days, in a bear
  regime → short

**Position sizing — volatility-adjusted, not a fixed dollar amount:**
- `shares = (equity x risk_per_trade_pct) / (ATR_stop_multiplier x ATR)`
- A stop-out loses roughly the same % of equity regardless of how
  volatile the instrument is, instead of a flat dollar amount ignoring
  volatility.

**Exit — two independent triggers, whichever comes first:**
- **Channel exit:** close breaks the opposite side of a *shorter*
  10-day channel. This is what lets winners run — the exit channel
  trails behind the trend rather than firing on the first pullback.
- **ATR hard stop:** price moves 2x ATR against entry. This caps the
  worst case on any single trade if the channel exit is too slow
  (e.g. a gap move).

This is a simplified version of the classic Turtle Trading system —
public, well-documented, deliberately simple (few rules is a feature,
not a limitation — every added rule is a chance to overfit).

**Known, tested limitations, not hidden:**
- It still occasionally produces a false signal in genuine chop (tested
  at roughly 2% of days in realistic mean-reverting synthetic data) —
  just far less often than the old fast-crossover system, not "never."
- A trend-following system needs actual trends to work. A backtest
  window with no real trends will show few or losing trades, and
  that's expected, not necessarily a bug in the strategy.
- For stocks, a daily bar is *provisional* until market close — a
  mid-day check shows where things currently stand, not a settled
  signal.
- Diversification matters for this style of strategy, and the default
  crypto watchlist (BTC/ETH/SOL/XRP/DOGE) is less diversified than it
  looks — most alts move with BTC most of the time.

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

Leave the window open. Daily bars only change once a day, so the
default poll interval is hourly (`POLL_INTERVAL_SECONDS`, adjustable);
checking more often just re-confirms the same still-forming bar. When
a breakout fires you'll get:
- A line printed in the terminal
- A line written to `scanner_log_YYYYMMDD.txt` in this folder
- A desktop popup notification (if `plyer` is installed and your OS
  supports it)

Stop it any time with `Ctrl+C`.

## Things worth knowing

- **Data is yfinance (free).** Daily history isn't capped the way
  intraday data is, but `DAILY_LOOKBACK` (2 years by default) still
  limits how far back the strategy looks — increase it in
  `stock_scanner.py` for a longer effective backtest/warmup window.
- **Weekends/holidays:** for stocks, the script checks weekday + time
  only — it doesn't know about market holidays, so it'll try to scan
  and just get stale data on holidays. Not harmful, just a no-op.
- **Tuning:** all the knobs (trend MA period, entry/exit channel
  lengths, ATR period/multiplier, risk per trade) are in the CONFIG
  section at the top of `stock_scanner.py`.

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

The dashboard runs a simulated account alongside the scanner:

- When a breakout fires, it "opens" a simulated position sized off
  ATR and your risk-per-trade % (see the strategy logic above).
- Positions close automatically on a **Donchian channel exit** (lets
  winners run) or an **ATR hard stop** (caps the worst case), or an
  **opposite breakout** (reversal).
- Balance, total P&L, win rate, and an equity curve are shown at the
  top of the dashboard, all computed from these simulated trades —
  nothing is invented for looks.
- Risk per trade % and ATR stop multiplier are adjustable in the
  sidebar. A "Reset paper account" button (behind a confirmation
  checkbox) wipes the simulated history and starts fresh.
- State is stored in `paper_trades.json`, so it survives restarts.
  **If you're switching from the old MA-crossover strategy, reset the
  paper account** — old trades in there belong to a different, now
  removed, strategy and will skew the numbers if left in.

**This places no real orders and involves no real money.** It also
isn't a guarantee — the simulation ignores real-world slippage, fees,
partial fills, and liquidity, so live results with an actual broker
would differ. Treat it as a sanity check on the strategy's logic, not
proof it's profitable. Trend-following in particular needs a real
trend to show a real trade; a quiet backtest window isn't necessarily
a broken strategy.

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

## Prior strategy (removed)

An earlier version of this scanner used 5m/15m MA-crossover + MACD/RSI
+ VWAP + a stack of ADX/separation/confirm-bar chop filters. It's
gone now, replaced by the daily trend-following strategy described at
the top of this file, because after several rounds of tuning it kept
producing either too many whipsaws or (once filtered) too few or zero
signals — a symptom of fighting noise on too short a timeframe rather
than a fixable parameter problem. If you have an old `paper_trades.json`
from that version, reset the paper account (sidebar button) before
trusting the new dashboard's numbers.

## Backtesting (`backtest.py`)

Replays the exact same trend-filter/breakout/exit logic against
historical daily data -- via `core.evaluate_bar`, the SAME function
the live scanner uses, so backtest and live can never quietly drift
apart -- and drives it through the same paper-trading engine used by
the live dashboard, so you get a real trade list instead of guessing.

```
python backtest.py                     # current config, all tickers in WATCHLIST
python backtest.py --ticker BTC-USD    # a single ticker
```

Output is a trade count, win rate, total simulated P&L, and a
breakdown of exit reasons (channel-exit / atr-stop / reversal) per
ticker.

**Limits worth knowing:**
- `DAILY_LOOKBACK` (2 years by default, in `stock_scanner.py`) limits
  how far back this looks — not a hard data-source cap like intraday
  data has, just a setting you can raise for a longer test.
- This evaluates the STRATEGY on past data. It is not a live
  simulation, and it ignores real slippage, fees, latency, and fills.
- Trend-following backtests are sensitive to which years they cover —
  a window with no real trends will show few or losing trades. That's
  expected behavior for this style of strategy, not necessarily a
  sign something's broken.

## Next step: execution

Once you're happy with how the scanner is calling setups, phase 2 is
wiring a signal straight into an order — most straightforward path is
Alpaca's API (free paper trading to test against first). We can build
that whenever you're ready; the scanner is already structured so the
signal-firing point is a single, easy-to-hook place in `scan_once()`.
