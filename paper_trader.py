"""
Paper-trading simulator for the daily trend-following strategy.

Opens a SIMULATED position on a confirmed Donchian breakout, sized by
volatility (ATR) rather than a fixed dollar amount, and closes it on
either of two independent exits:
  - a Donchian channel exit (opposite side of the shorter exit channel
    -- lets winners run with the trend)
  - an ATR hard stop (caps the worst case on any single trade)

IMPORTANT: this places no real orders and involves no real money. A
simulated result also isn't a guarantee -- it ignores real-world
slippage, fees, partial fills, and liquidity, so live results with
real money would differ. Treat it as a way to sanity-check the
strategy's logic, not as proof it will make money.

Persisted to paper_trades.json in the project folder so the account
survives dashboard restarts.
"""

import json
import os

STATE_FILE = "paper_trades.json"

DEFAULT_SETTINGS = {
    "starting_balance": 10000.0,
    "risk_per_trade_pct": 0.01,   # risk ~1% of equity per trade
    "atr_stop_multiplier": 2.0,   # hard stop distance = this many ATRs from entry
}


def _default_state():
    return {
        "cash": DEFAULT_SETTINGS["starting_balance"],
        "positions": {},       # ticker -> {direction, entry_price, shares, hard_stop, opened_at}
        "closed_trades": [],   # list of dicts, most recent first
        "equity_curve": [],    # list of [timestamp_iso, equity]
        "settings": dict(DEFAULT_SETTINGS),
    }


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            state.setdefault("settings", dict(DEFAULT_SETTINGS))
            for k, v in DEFAULT_SETTINGS.items():
                state["settings"].setdefault(k, v)
            return state
        except Exception:
            pass
    return _default_state()


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def new_state(starting_balance: float = None) -> dict:
    """In-memory state, never touches disk -- for backtesting so a
    backtest run never clobbers your live paper-trading account."""
    state = _default_state()
    if starting_balance is not None:
        state["cash"] = starting_balance
        state["settings"]["starting_balance"] = starting_balance
    return state


def reset_state(starting_balance: float = None):
    fresh = _default_state()
    if starting_balance is not None:
        fresh["cash"] = starting_balance
        fresh["settings"]["starting_balance"] = starting_balance
    save_state(fresh)
    return fresh


def _position_pnl(position: dict, current_price: float) -> float:
    diff = current_price - position["entry_price"]
    if position["direction"] == "short":
        diff = -diff
    return diff * position["shares"]


def open_position(state: dict, ticker: str, direction: str, price: float, atr: float, timestamp: str):
    if ticker in state["positions"]:
        return
    if atr is None or atr <= 0:
        return  # can't size or set a stop safely without a valid ATR
    settings = state["settings"]
    equity = state["cash"]  # simplification: sizing off cash, not full mark-to-market equity
    stop_distance = settings["atr_stop_multiplier"] * atr
    dollar_risk = equity * settings["risk_per_trade_pct"]
    shares = dollar_risk / stop_distance
    hard_stop = price - stop_distance if direction == "long" else price + stop_distance
    state["positions"][ticker] = {
        "direction": direction,
        "entry_price": price,
        "shares": shares,
        "hard_stop": hard_stop,
        "opened_at": timestamp,
    }


def close_position(state: dict, ticker: str, price: float, timestamp: str, reason: str):
    pos = state["positions"].pop(ticker, None)
    if not pos:
        return
    pnl = _position_pnl(pos, price)
    state["cash"] += pnl
    state["closed_trades"].insert(0, {
        "ticker": ticker,
        "direction": pos["direction"],
        "entry_price": pos["entry_price"],
        "exit_price": price,
        "pnl": pnl,
        "opened_at": pos["opened_at"],
        "closed_at": timestamp,
        "reason": reason,
    })
    state["closed_trades"] = state["closed_trades"][:200]


def apply_breakout_signal(state: dict, ticker: str, direction, is_new_signal: bool, price: float,
                           atr, exit_high, exit_low, timestamp: str) -> str:
    """
    Mutates state in place based on this bar's analysis for one ticker.
    direction is 'long'/'short' if a confirmed breakout fired this bar,
    else None. exit_high/exit_low are the shorter Donchian channel's
    current levels (may be None/NaN early in a series). Returns a short
    human-readable description of any action taken, or None.
    """
    pos = state["positions"].get(ticker)
    action_msgs = []

    if pos:
        hit_hard_stop = (
            (pos["direction"] == "long" and price <= pos["hard_stop"]) or
            (pos["direction"] == "short" and price >= pos["hard_stop"])
        )
        if hit_hard_stop:
            direction_word = pos["direction"].upper()
            close_position(state, ticker, price, timestamp, "atr-stop")
            action_msgs.append(f"Closed {direction_word} (atr-stop) @ {price:.4f}")
        else:
            hit_channel_exit = (
                (pos["direction"] == "long" and exit_low is not None and price < exit_low) or
                (pos["direction"] == "short" and exit_high is not None and price > exit_high)
            )
            if hit_channel_exit:
                direction_word = pos["direction"].upper()
                close_position(state, ticker, price, timestamp, "channel-exit")
                action_msgs.append(f"Closed {direction_word} (channel-exit) @ {price:.4f}")

    if is_new_signal and direction:
        pos = state["positions"].get(ticker)  # re-check: may have just closed above
        if pos and pos["direction"] != direction:
            close_position(state, ticker, price, timestamp, "reversal")
            open_position(state, ticker, direction, price, atr, timestamp)
            action_msgs.append(f"Reversed to {direction.upper()} @ {price:.4f}")
        elif not pos:
            open_position(state, ticker, direction, price, atr, timestamp)
            action_msgs.append(f"Opened {direction.upper()} @ {price:.4f}")

    return " then ".join(action_msgs) if action_msgs else None


def mark_to_market(state: dict, current_prices: dict, timestamp: str, cap: int = 500) -> float:
    """Appends an equity snapshot (cash + unrealized P&L) and returns it.
    cap limits how much history is kept (live dashboard doesn't need
    thousands of points); pass cap=None for a backtest, where you want
    the full curve."""
    equity = state["cash"]
    for ticker, pos in state["positions"].items():
        if ticker in current_prices:
            equity += _position_pnl(pos, current_prices[ticker])
    state["equity_curve"].append([timestamp, round(equity, 2)])
    if cap:
        state["equity_curve"] = state["equity_curve"][-cap:]
    return equity


def win_rate(state: dict):
    trades = state["closed_trades"]
    if not trades:
        return None
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return wins / len(trades) * 100
