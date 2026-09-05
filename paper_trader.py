"""
Paper-trading simulator layered on top of the scanner's signals.

Opens a SIMULATED position when a confirmed signal fires, and closes it
on a stop-loss, a take-profit, a trend-bias flip, or an opposite signal
(reversal). Tracks a virtual cash balance and an equity curve so the
dashboard can show an honest "what if I took every signal" balance
chart.

IMPORTANT: this places no real orders and involves no real money. A
simulated result also isn't a guarantee -- it ignores real-world
slippage, fees, partial fills, and liquidity, so live results with
real money would differ. Treat it as a way to sanity-check the
scanner's logic, not as proof the strategy will make money.

Persisted to paper_trades.json in the project folder so the account
survives dashboard restarts.
"""

import json
import os

STATE_FILE = "paper_trades.json"

DEFAULT_SETTINGS = {
    "starting_balance": 10000.0,
    "position_notional": 1000.0,  # $ notional per simulated trade
    "stop_loss_pct": 0.015,       # 1.5%
    "take_profit_pct": 0.03,      # 3%
}


def _default_state():
    return {
        "cash": DEFAULT_SETTINGS["starting_balance"],
        "positions": {},       # ticker -> {direction, entry_price, shares, opened_at}
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


def open_position(state: dict, ticker: str, direction: str, price: float, timestamp: str):
    if ticker in state["positions"]:
        return
    notional = state["settings"]["position_notional"]
    state["positions"][ticker] = {
        "direction": direction,
        "entry_price": price,
        "shares": notional / price,
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


def _check_stop_take(state: dict, position: dict, current_price: float):
    settings = state["settings"]
    pnl_pct = (current_price - position["entry_price"]) / position["entry_price"]
    if position["direction"] == "short":
        pnl_pct = -pnl_pct
    if pnl_pct <= -settings["stop_loss_pct"]:
        return "stop-loss"
    if pnl_pct >= settings["take_profit_pct"]:
        return "take-profit"
    return None


def apply_signal(state: dict, ticker: str, bias: str, direction, is_new_signal: bool,
                  price: float, timestamp: str) -> str:
    """
    Mutates state in place based on this cycle's scan result for one
    ticker. direction is 'long'/'short' if a confirmed signal fired
    this cycle, else None. Returns a short human-readable description
    of any action taken, or None if nothing changed.
    """
    pos = state["positions"].get(ticker)
    action_msgs = []

    # 1) Protective exits run every cycle, independent of new signals.
    if pos:
        reason = _check_stop_take(state, pos, price)
        if reason:
            direction_word = pos["direction"].upper()
            close_position(state, ticker, price, timestamp, reason)
            action_msgs.append(f"Closed {direction_word} ({reason}) @ {price:.2f}")
        elif (pos["direction"] == "long" and bias != "bull") or (pos["direction"] == "short" and bias != "bear"):
            direction_word = pos["direction"].upper()
            close_position(state, ticker, price, timestamp, "trend-flip")
            action_msgs.append(f"Closed {direction_word} (trend-flip) @ {price:.2f}")

    # 2) Handle a freshly confirmed signal this cycle (may follow a
    # protective exit above, e.g. stop-loss immediately followed by a
    # fresh signal in the new direction -- both can happen in one cycle).
    if is_new_signal and direction:
        pos = state["positions"].get(ticker)  # re-check: may have just closed above
        if pos and pos["direction"] != direction:
            close_position(state, ticker, price, timestamp, "reversal")
            open_position(state, ticker, direction, price, timestamp)
            action_msgs.append(f"Reversed to {direction.upper()} @ {price:.2f}")
        elif not pos:
            open_position(state, ticker, direction, price, timestamp)
            action_msgs.append(f"Opened {direction.upper()} @ {price:.2f}")

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
