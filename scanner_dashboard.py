"""
Stock Setup Scanner -- Dashboard
==================================
A live browser dashboard for the scanner in stock_scanner.py. Same
setup logic (15m trend filter + 5m MA cross + MACD/RSI confirmation),
shown as a clean, auto-refreshing web UI instead of a scrolling log.

Run with:
    streamlit run scanner_dashboard.py

Requires stock_scanner.py to be in the same folder -- this file reuses
its indicator/scan functions rather than duplicating the logic.
"""

from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import stock_scanner as core
import levels_store

# ============================================================
# Page setup
# ============================================================

st.set_page_config(page_title="Setup Scanner", page_icon="\U0001F4C8", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 1.5rem; }
.ticker-card {
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 12px;
    border: 1px solid rgba(128,128,128,0.25);
    background: rgba(128,128,128,0.04);
}
.ticker-card.long { border-color: #2ecc71; background: rgba(46,204,113,0.10); }
.ticker-card.short { border-color: #e74c3c; background: rgba(231,76,60,0.10); }
.ticker-symbol { font-size: 1.4rem; font-weight: 700; }
.badge {
    display: inline-block; padding: 2px 10px; border-radius: 999px;
    font-size: 0.75rem; font-weight: 600; margin-left: 8px; vertical-align: middle;
}
.badge.bull { background: rgba(46,204,113,0.20); color: #1e8449; }
.badge.bear { background: rgba(231,76,60,0.20); color: #b03a2e; }
.badge.mixed { background: rgba(128,128,128,0.20); color: #666; }
.badge.long-signal { background: #2ecc71; color: white; }
.badge.short-signal { background: #e74c3c; color: white; }
.metric-row { font-size: 0.85rem; color: rgba(128,128,128,0.9); margin-top: 6px; }
.metric-row b { color: inherit; }
</style>
""", unsafe_allow_html=True)

st.title("\U0001F4C8 Setup Scanner")

# ============================================================
# Session state
# ============================================================

if "last_signal" not in st.session_state:
    st.session_state.last_signal = {}
if "activity_log" not in st.session_state:
    st.session_state.activity_log = []  # list of (timestamp_str, message)

# ============================================================
# Sidebar controls
# ============================================================

with st.sidebar:
    st.header("Settings")
    watchlist_text = st.text_area(
        "Watchlist (comma-separated)",
        value=", ".join(core.WATCHLIST),
        height=80,
    )
    watchlist = [t.strip().upper() for t in watchlist_text.split(",") if t.strip()]

    poll_seconds = st.slider("Refresh every (seconds)", 15, 300, core.POLL_INTERVAL_SECONDS, step=15)
    force_scan = st.checkbox(
        "Scan even when market is closed",
        value=False,
        help="For testing outside market hours. Data will look stale/frozen.",
    )
    desktop_notify = st.checkbox("Desktop notifications", value=True)

    st.divider()
    st.caption(
        "Trend: 15m price vs 200 MA & VWAP  \n"
        "Trigger: 5m MA5/MA10 crossover  \n"
        "Confirm: MACD + RSI(50) agree with direction"
    )
    st.caption("Data via yfinance -- typically ~15-20 min delayed.")

# ============================================================
# Auto-refresh
# ============================================================

st_autorefresh(interval=poll_seconds * 1000, key="autorefresh")

# ============================================================
# Market status banner
# ============================================================

now_et = datetime.now(core.MARKET_TZ)
market_open = core.is_market_hours(now_et)

status_col1, status_col2 = st.columns([3, 1])
with status_col1:
    if market_open:
        st.success(f"Market OPEN -- {now_et.strftime('%I:%M:%S %p %Z')}  |  scanning {len(watchlist)} tickers")
    else:
        st.warning(f"Market CLOSED -- {now_et.strftime('%I:%M:%S %p %Z, %A')}  |  waiting for 9:30am ET")
with status_col2:
    st.metric("Last check", now_et.strftime("%I:%M:%S %p"))

should_scan = market_open or force_scan

# ============================================================
# Scan + render ticker cards
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def cached_gex(ticker: str):
    # GEX depends on options open interest, which mostly updates once a
    # day -- cache for 5 min so every autorefresh doesn't re-hit the
    # options-chain endpoint for every ticker.
    return core.get_gex_levels(ticker)


def _save_level(ticker: str, field: str, key: str):
    raw = st.session_state[key].strip()
    if raw == "":
        levels_store.save_override(ticker, field, None)
        return
    try:
        levels_store.save_override(ticker, field, float(raw))
    except ValueError:
        pass  # ignore non-numeric input rather than crashing the app


def render_levels_row(ticker: str, levels: dict):
    parts = []
    for f in levels_store.FIELDS:
        info = levels[f]
        label = levels_store.LABELS[f]
        if info["value"] is None:
            parts.append(f"{label}: <i>not set</i>")
        else:
            tag = "M" if info["source"] == "manual" else "A"
            parts.append(f"{label}: <b>{info['value']:.2f}</b> <span style='opacity:0.6'>({tag})</span>")
    st.markdown(
        f'<div class="metric-row">{" &nbsp;|&nbsp; ".join(parts)}</div>',
        unsafe_allow_html=True,
    )


def render_levels_editor(ticker: str, levels: dict):
    with st.expander(f"Edit levels -- {ticker}"):
        st.caption("Blank clears a manual entry and falls back to auto (where available). M = manual, A = auto.")
        cols = st.columns(len(levels_store.FIELDS))
        for col, f in zip(cols, levels_store.FIELDS):
            key = f"{ticker}_{f}_override"
            current = levels[f]["value"] if levels[f]["source"] == "manual" else None
            with col:
                st.text_input(
                    levels_store.LABELS[f],
                    value="" if current is None else str(current),
                    key=key,
                    on_change=_save_level,
                    args=(ticker, f, key),
                )


def render_card(ticker: str):
    try:
        bias = core.get_trend_bias(ticker)
        direction, bar_ts = (None, None)
        macd_val = rsi_val = None

        if bias in ("bull", "bear"):
            direction, bar_ts = core.get_trigger_signal(ticker)

        # pull a bit of detail for display regardless of signal state
        df5 = core.fetch(ticker, core.TRIGGER_INTERVAL, core.TRIGGER_LOOKBACK)
        df5 = core.add_moving_averages(df5, [core.FAST_MA_PERIOD, core.SLOW_MA_PERIOD])
        df5 = core.add_macd(df5)
        df5 = core.add_rsi(df5)
        last5 = df5.iloc[-1]
        macd_val = last5["MACD"] - last5["MACD_signal"]
        rsi_val = last5["RSI"]
        price = last5["Close"]

        confirmed = False
        if direction == "long" and bias == "bull":
            confirmed = True
        elif direction == "short" and bias == "bear":
            confirmed = True

        card_class = "long" if (confirmed and direction == "long") else "short" if (confirmed and direction == "short") else ""
        bias_badge_class = {"bull": "bull", "bear": "bear"}.get(bias, "mixed")

        signal_html = ""
        if confirmed:
            signal_html = f'<span class="badge {direction}-signal">{direction.upper()} SIGNAL</span>'

            key = ticker
            already = st.session_state.last_signal.get(key)
            if already != (direction, str(bar_ts)):
                st.session_state.last_signal[key] = (direction, str(bar_ts))
                stamp = now_et.strftime("%Y-%m-%d %H:%M:%S")
                msg = f"{ticker}: {direction.upper()} setup (15m trend={bias}, bar {bar_ts})"
                st.session_state.activity_log.insert(0, (stamp, msg))
                st.session_state.activity_log = st.session_state.activity_log[:100]
                if desktop_notify:
                    core.desktop_alert(f"{ticker} {direction.upper()} setup", msg)

        gex = cached_gex(ticker)
        auto_values = {"call_resistance": gex["call_resistance"], "put_support": gex["put_support"]}
        levels = levels_store.get_effective_levels(ticker, auto_values)

        st.markdown(
            f"""
            <div class="ticker-card {card_class}">
                <span class="ticker-symbol">{ticker}</span>
                <span class="badge {bias_badge_class}">{bias.upper()}</span>
                {signal_html}
                <div class="metric-row">
                    Price: <b>${price:,.2f}</b> &nbsp;|&nbsp;
                    MACD-Signal: <b>{macd_val:+.3f}</b> &nbsp;|&nbsp;
                    RSI(14): <b>{rsi_val:.1f}</b>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        render_levels_row(ticker, levels)
        if gex["expiry"]:
            st.caption(f"GEX approx. from {gex['expiry']} options chain (nearest expiration) -- not a paid dealer-positioning feed.")
        render_levels_editor(ticker, levels)
    except Exception as e:
        st.markdown(
            f"""
            <div class="ticker-card">
                <span class="ticker-symbol">{ticker}</span>
                <span class="badge mixed">DATA ERROR</span>
                <div class="metric-row">{e}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


if not watchlist:
    st.info("Add at least one ticker in the sidebar to start scanning.")
elif should_scan:
    cols = st.columns(2)
    for i, ticker in enumerate(watchlist):
        with cols[i % 2]:
            render_card(ticker)
else:
    st.info("Market is closed. Check \"Scan even when market is closed\" in the sidebar to preview anyway.")

# ============================================================
# Activity log
# ============================================================

st.subheader("Activity Log")
if st.session_state.activity_log:
    log_df = pd.DataFrame(st.session_state.activity_log, columns=["Time", "Signal"])
    st.dataframe(log_df, use_container_width=True, hide_index=True)
else:
    st.caption("No signals fired yet this session.")
