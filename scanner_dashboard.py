"""
Stock/Crypto Trend-Following Scanner -- Dashboard
====================================================
A live browser dashboard for the daily trend-following strategy in
stock_scanner.py (200-day trend filter + Donchian breakout entry +
ATR-based sizing/stops), shown as a command-center style UI with each
watchlist ticker styled as a scanning "agent," plus a paper-trading
simulator so the balance chart reflects real (simulated) outcomes.

Run with:
    streamlit run scanner_dashboard.py

Requires stock_scanner.py, levels_store.py, and paper_trader.py in the
same folder -- this file reuses their functions rather than
duplicating logic.

Honesty note: every number shown here is either pulled straight from
market data or computed by the paper-trading simulator in
paper_trader.py, which places no real orders. Nothing is fabricated
for effect. A simulated result also isn't a guarantee of anything --
it ignores real slippage, fees, and fills, so treat it as a sanity
check on the scanner's logic, not a promise of real-money performance.
"""

import time
from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import stock_scanner as core
import levels_store
import paper_trader as pt

# ============================================================
# Page setup
# ============================================================

st.set_page_config(page_title="Setup Scanner", page_icon="\U0001F6F0", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"], [data-testid="stHeader"] {
    background-color: #0a0e14 !important;
    color: #d7dde5;
    font-family: 'JetBrains Mono', 'SFMono-Regular', Menlo, monospace;
}
[data-testid="stSidebar"] { border-right: 1px solid rgba(0,255,163,0.15); }
.block-container { padding-top: 1.2rem; max-width: 1400px; }
h1, h2, h3, .stMarkdown p { color: #d7dde5 !important; }

/* ---- Header ---- */
.hdr-title { font-size: 1.7rem; font-weight: 700; letter-spacing: 2px; color: #eef1f5; margin-bottom: 0; }
.hdr-title span { color: #00ffa3; }
.hdr-subtitle { font-size: 0.78rem; letter-spacing: 3px; color: #6b7684; text-transform: uppercase; margin-top: 2px; }
.hdr-meta { text-align: right; font-size: 0.75rem; color: #8b96a5; line-height: 1.6; }
.hdr-meta b { color: #d7dde5; }
.live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.live-dot.on { background: #00ffa3; box-shadow: 0 0 8px #00ffa3; }
.live-dot.off { background: #ff3b5c; box-shadow: 0 0 8px #ff3b5c; }

/* ---- Stat cards ---- */
.stat-card {
    border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;
    background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
    padding: 14px 16px; height: 100%;
}
.stat-label { font-size: 0.68rem; letter-spacing: 2px; color: #6b7684; text-transform: uppercase; }
.stat-value { font-size: 1.5rem; font-weight: 700; color: #eef1f5; margin-top: 4px; }
.stat-value.up { color: #00ffa3; }
.stat-value.down { color: #ff3b5c; }
.stat-sub { font-size: 0.72rem; color: #8b96a5; margin-top: 2px; }
.section-label { font-size: 0.68rem; letter-spacing: 2px; color: #6b7684; text-transform: uppercase; margin: 4px 0 8px 0; }

/* ---- Agent cards ---- */
.agent-card {
    border: 1px solid rgba(255,255,255,0.10); border-radius: 12px;
    background: rgba(255,255,255,0.02);
    padding: 14px 16px 10px 16px; margin-bottom: 10px;
    transition: border-color 0.2s;
}
.agent-card.long-fire { border-color: #00ffa3; background: rgba(0,255,163,0.06); }
.agent-card.short-fire { border-color: #ff3b5c; background: rgba(255,59,92,0.06); }
.agent-header { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; }
.agent-codename { font-weight: 700; font-size: 0.85rem; letter-spacing: 1px; color: #eef1f5; }
.agent-role {
    font-size: 0.62rem; letter-spacing: 1.5px; color: #6b7684;
    border: 1px solid rgba(255,255,255,0.15); border-radius: 4px; padding: 1px 6px;
}
.agent-position-tag {
    font-size: 0.62rem; letter-spacing: 1px; color: #eef1f5;
    background: rgba(255,255,255,0.10); border-radius: 4px; padding: 1px 6px;
}
.agent-ticker { font-size: 1.3rem; font-weight: 700; color: #eef1f5; margin: 2px 0 8px 0; }
.spark-row { display: flex; align-items: flex-end; height: 36px; gap: 2px; margin-bottom: 8px; }
.spark-bar { width: 5px; border-radius: 1px; }
.agent-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 4px; }
.agent-status { font-size: 0.68rem; font-weight: 700; letter-spacing: 1px; padding: 2px 8px; border-radius: 999px; }
.agent-status.bull { background: rgba(0,255,163,0.15); color: #00ffa3; }
.agent-status.bear { background: rgba(255,59,92,0.15); color: #ff3b5c; }
.agent-status.mixed { background: rgba(255,255,255,0.08); color: #8b96a5; }
.agent-status.long-signal { background: #00ffa3; color: #06110c; }
.agent-status.short-signal { background: #ff3b5c; color: #1a0509; }
.agent-pct { font-size: 0.78rem; font-weight: 600; }
.agent-pct.up { color: #00ffa3; }
.agent-pct.down { color: #ff3b5c; }
.agent-meta { font-size: 0.68rem; color: #6b7684; margin-top: 8px; }
.agent-meta b { color: #a9b2bd; }
.agent-action { font-size: 0.68rem; color: #ffb020; margin-top: 6px; }

/* ---- Activity / trade log ---- */
.log-row { display: flex; justify-content: space-between; font-size: 0.78rem; padding: 6px 4px; border-bottom: 1px solid rgba(255,255,255,0.06); }
.log-ticker { font-weight: 700; width: 70px; flex-shrink: 0; }
.log-msg { color: #a9b2bd; flex-grow: 1; }
.log-time { color: #6b7684; flex-shrink: 0; margin-left: 12px; }
.log-dot { display:inline-block; width:6px; height:6px; border-radius:50%; margin-right:6px; }
.log-dot.long { background:#00ffa3; }
.log-dot.short { background:#ff3b5c; }
.log-dot.paper { background:#ffb020; }

section[data-testid="stExpander"] {
    background: rgba(255,255,255,0.015); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# Agent identity pool (space-robot callsigns + avatar shapes)
# ============================================================

CODENAMES = ["BLIP-9", "KLONK-3", "ZORP-X", "WHIRR-7", "BOOP-Q", "CLANK-5",
             "PING-2", "TIN-8", "GEAR-Z", "SPROK-4", "BEEP-11", "RUST-Y"]
ROLES = ["SCOUT", "NAV", "RADAR", "PULSE", "VECTOR", "ORBIT", "BOOST", "WARP"]
AVATAR_STYLES = [
    ("#4da3ff", "circle"), ("#ff3b5c", "triangle"), ("#ffb020", "hex"),
    ("#00ffa3", "circle"), ("#b56bff", "triangle"), ("#ff7edb", "hex"),
    ("#5eead4", "circle"), ("#f97316", "triangle"), ("#a3e635", "hex"),
    ("#38bdf8", "circle"), ("#f472b6", "triangle"), ("#fbbf24", "hex"),
]


def agent_identity(index: int):
    codename = CODENAMES[index % len(CODENAMES)]
    role = ROLES[index % len(ROLES)]
    color, shape = AVATAR_STYLES[index % len(AVATAR_STYLES)]
    return codename, role, color, shape


def avatar_svg(color: str, shape: str, size: int = 26) -> str:
    """A small beep-boop robot avatar: colored shape body, antenna, two dot eyes."""
    antenna = f'<line x1="16" y1="1" x2="16" y2="6" stroke="{color}" stroke-width="2"/><circle cx="16" cy="1" r="1.6" fill="{color}"/>'
    if shape == "triangle":
        body = f'<polygon points="16,7 29,27 3,27" fill="{color}" />'
        eyes = '<circle cx="12.5" cy="21" r="1.8" fill="#0a0e14"/><circle cx="19.5" cy="21" r="1.8" fill="#0a0e14"/>'
    elif shape == "hex":
        body = f'<polygon points="16,6 28,12 28,24 16,30 4,24 4,12" fill="{color}" />'
        eyes = '<circle cx="12" cy="18" r="1.8" fill="#0a0e14"/><circle cx="20" cy="18" r="1.8" fill="#0a0e14"/>'
    else:
        body = f'<rect x="4" y="8" width="24" height="22" rx="7" fill="{color}" />'
        eyes = '<circle cx="12" cy="18" r="1.8" fill="#0a0e14"/><circle cx="20" cy="18" r="1.8" fill="#0a0e14"/>'
    return f'<svg width="{size}" height="{size}" viewBox="0 0 32 32">{antenna}{body}{eyes}</svg>'


# ============================================================
# Session state
# ============================================================

if "last_signal" not in st.session_state:
    st.session_state.last_signal = {}
if "activity_log" not in st.session_state:
    st.session_state.activity_log = []  # (timestamp_str, ticker, kind, direction, message)
if "session_start" not in st.session_state:
    st.session_state.session_start = time.time()

paper_state = pt.load_state()

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

    poll_seconds = st.slider("Refresh every (seconds)", 60, 3600, core.POLL_INTERVAL_SECONDS, step=60)
    if core.CRYPTO_MODE:
        st.caption("Crypto mode: trades 24/7, always scanning.")
        force_scan = True
    else:
        force_scan = st.checkbox(
            "Scan even when market is closed",
            value=False,
            help="Daily bars don't change until the close anyway -- this just lets you preview mid-day.",
        )
    desktop_notify = st.checkbox("Desktop notifications", value=True)

    st.divider()
    st.subheader("Paper trading")
    st.caption("Simulated only -- no real orders are placed.")
    new_risk_pct = st.number_input("Risk per trade (% of equity)", min_value=0.1, max_value=10.0,
                                    value=float(paper_state["settings"]["risk_per_trade_pct"] * 100), step=0.1) / 100
    new_atr_mult = st.number_input("ATR stop multiplier", min_value=0.5, max_value=10.0,
                                    value=float(paper_state["settings"]["atr_stop_multiplier"]), step=0.5)
    if (new_risk_pct != paper_state["settings"]["risk_per_trade_pct"] or
            new_atr_mult != paper_state["settings"]["atr_stop_multiplier"]):
        paper_state["settings"]["risk_per_trade_pct"] = new_risk_pct
        paper_state["settings"]["atr_stop_multiplier"] = new_atr_mult
        pt.save_state(paper_state)

    confirm_reset = st.checkbox("I understand this clears simulated history")
    if st.button("Reset paper account", disabled=not confirm_reset):
        paper_state = pt.reset_state(paper_state["settings"]["starting_balance"])
        st.session_state.activity_log = []
        st.rerun()

    st.divider()
    st.caption(
        f"Trend: daily close vs {core.TREND_MA_PERIOD}-day MA  \n"
        f"Entry: {core.ENTRY_BREAKOUT_DAYS}-day Donchian breakout, with-trend only  \n"
        f"Exit: {core.EXIT_BREAKOUT_DAYS}-day opposite channel OR {core.ATR_STOP_MULTIPLIER}x ATR hard stop"
    )
    st.caption("Data via yfinance. For stocks, today's daily bar is provisional until market close.")
    if core.CRYPTO_MODE:
        st.caption("GEX (call resistance/put support) has no data source for crypto -- those two fields will show \"not set\" unless you enter them manually. Leash/River/GEX overrides still work as manual levels.")

# ============================================================
# Auto-refresh (its own counter doubles as our scan "cycle" count)
# ============================================================

cycle = st_autorefresh(interval=poll_seconds * 1000, key="autorefresh")

# ============================================================
# Header
# ============================================================

now_et = datetime.now(core.MARKET_TZ)
market_open = core.is_market_hours(now_et)
should_scan = market_open or force_scan

uptime_seconds = int(time.time() - st.session_state.session_start)
uptime_str = f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m"

hcol1, hcol2 = st.columns([3, 2])
with hcol1:
    st.markdown('<div class="hdr-title">SETUP <span>SCANNER</span></div>', unsafe_allow_html=True)
    mode_word = "CRYPTO &middot; 24/7" if core.CRYPTO_MODE else "EQUITIES"
    st.markdown(
        f'<div class="hdr-subtitle">DAILY TREND-FOLLOWING &middot; {len(watchlist)} AGENTS &middot; {mode_word} &middot; PAPER TRADING</div>',
        unsafe_allow_html=True,
    )
with hcol2:
    dot_class = "on" if market_open else "off"
    status_word = "LIVE" if market_open else "CLOSED"
    hdr_meta_html = (
        f'<div class="hdr-meta">'
        f'<span class="live-dot {dot_class}"></span><b>{status_word}</b> &middot; {now_et.strftime("%I:%M:%S %p %Z")}<br>'
        f'CYCLE <b>{cycle}</b> &middot; UPTIME <b>{uptime_str}</b> &middot; GRIP <b>SIMULATED</b></div>'
    )
    st.markdown(hdr_meta_html, unsafe_allow_html=True)

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ============================================================
# Scan + paper-trade logic
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def cached_gex(ticker: str):
    return core.get_gex_levels(ticker)


def _save_level(ticker: str, field: str, key: str):
    raw = st.session_state[key].strip()
    if raw == "":
        levels_store.save_override(ticker, field, None)
        return
    try:
        levels_store.save_override(ticker, field, float(raw))
    except ValueError:
        pass


def render_levels_editor(ticker: str, levels: dict):
    with st.expander(f"Levels -- {ticker}"):
        st.caption("Blank clears a manual entry and falls back to auto (where available). M = manual, A = auto.")
        cols = st.columns(len(levels_store.FIELDS))
        for col, f in zip(cols, levels_store.FIELDS):
            key = f"{ticker}_{f}_override"
            info = levels[f]
            current = info["value"] if info["source"] == "manual" else None
            with col:
                st.text_input(
                    levels_store.LABELS[f],
                    value="" if current is None else str(current),
                    key=key,
                    on_change=_save_level,
                    args=(ticker, f, key),
                )
        shown = [f"{levels_store.LABELS[f]}: {levels[f]['value']:.2f} ({levels[f]['source']})"
                 for f in levels_store.FIELDS if levels[f]["value"] is not None]
        if shown:
            st.caption(" | ".join(shown))


def scan_ticker(ticker: str) -> dict:
    result = core.analyze_ticker(ticker)
    df = core.get_daily_frame(ticker)

    pct_change = None
    price = result["price"]
    if price is not None and len(df) >= 2:
        prev_close = df["Close"].iloc[-2]
        if pd.notna(prev_close) and prev_close > 0:
            pct_change = (price - prev_close) / prev_close * 100

    gex = cached_gex(ticker)
    auto_values = {"call_resistance": gex["call_resistance"], "put_support": gex["put_support"]}
    levels = levels_store.get_effective_levels(ticker, auto_values)

    return {
        "trend": result["trend"],
        "direction": result["direction"],
        "bar_ts": result["bar_ts"],
        "price": price,
        "atr": result["atr"],
        "exit_high": result["exit_high"],
        "exit_low": result["exit_low"],
        "pct_change": pct_change,
        "chart_df": df[["Close"]].tail(150),
        "gex": gex,
        "levels": levels,
    }


current_prices = {}


def render_agent_card(index: int, ticker: str):
    codename, role, color, shape = agent_identity(index)
    avatar = avatar_svg(color, shape)
    try:
        data = scan_ticker(ticker)
        trend = data["trend"]
        direction = data["direction"]
        current_prices[ticker] = data["price"]

        is_new_signal = False
        if direction:
            already = st.session_state.last_signal.get(ticker)  # (direction, bar_ts) or None
            is_new_bar = already is None or already[1] != data["bar_ts"]
            if is_new_bar:
                is_new_signal = True
                st.session_state.last_signal[ticker] = (direction, data["bar_ts"])
                stamp = now_et.strftime("%Y-%m-%d %H:%M:%S")
                msg = f"trend={trend}, {core.ENTRY_BREAKOUT_DAYS}-day channel broken"
                st.session_state.activity_log.insert(0, (stamp, ticker, "signal", direction, msg))
                if desktop_notify:
                    core.desktop_alert(f"{ticker} {direction.upper()} breakout", msg)

        paper_action = pt.apply_breakout_signal(
            paper_state, ticker, direction, is_new_signal, data["price"], data["atr"],
            data["exit_high"], data["exit_low"], now_et.strftime("%Y-%m-%d %H:%M:%S"),
        )
        if paper_action:
            stamp = now_et.strftime("%Y-%m-%d %H:%M:%S")
            paper_dir = direction or "long"
            st.session_state.activity_log.insert(0, (stamp, ticker, "paper", paper_dir, paper_action))

        st.session_state.activity_log = st.session_state.activity_log[:150]

        if direction:
            card_class = "long-fire" if direction == "long" else "short-fire"
            status_class = f"{direction}-signal"
            status_text = f"{direction.upper()} SIGNAL"
        else:
            card_class = ""
            status_class = {"bull": "bull", "bear": "bear"}.get(trend, "mixed")
            status_text = f"{trend.upper()}" if trend in ("bull", "bear") else "STANDBY"

        pos = paper_state["positions"].get(ticker)
        position_tag = f'<span class="agent-position-tag">IN {pos["direction"].upper()}</span>' if pos else ""

        pct = data["pct_change"]
        pct_html = ""
        if pct is not None:
            pct_class = "up" if pct >= 0 else "down"
            pct_html = f'<span class="agent-pct {pct_class}">{pct:+.2f}%</span>'

        # NOTE: every HTML fragment below is built as a single line (or joined
        # with explicit \n only where needed) with NO leading indentation --
        # markdown treats 4+ leading spaces as a literal code block, which is
        # what previously caused raw "</div>" tags to show up on the page.
        header_html = (
            f'<div class="agent-card {card_class}">'
            f'<div class="agent-header">{avatar}'
            f'<span class="agent-codename">{codename}</span>'
            f'<span class="agent-role">{role}</span>'
            f'{position_tag}</div>'
            f'<div class="agent-ticker">{ticker}</div></div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        chart_color = "#00ffa3" if (pct is None or pct >= 0) else "#ff3b5c"
        st.line_chart(data["chart_df"]["Close"], height=110, color=chart_color)

        action_html = f'<div class="agent-action">PAPER: {paper_action}</div>' if paper_action else ""
        atr_html = f'ATR <b>{data["atr"]:,.4f}</b>' if data["atr"] is not None else ""
        footer_html = (
            '<div class="agent-card" style="margin-top:-14px;border-top:none;border-top-left-radius:0;border-top-right-radius:0;">'
            f'<div class="agent-footer"><span class="agent-status {status_class}">{status_text}</span>{pct_html}</div>'
            f'<div class="agent-meta">Price <b>${data["price"]:,.4f}</b> &nbsp;|&nbsp; {atr_html}</div>'
            f'{action_html}</div>'
        )
        st.markdown(footer_html, unsafe_allow_html=True)

        if data["gex"]["expiry"]:
            st.caption(f"GEX approx. from {data['gex']['expiry']} chain -- not a paid dealer feed.")
        render_levels_editor(ticker, data["levels"])

    except Exception as e:
        error_html = (
            f'<div class="agent-card"><div class="agent-header">{avatar}'
            f'<span class="agent-codename">{codename}</span>'
            f'<span class="agent-role">{role}</span></div>'
            f'<div class="agent-ticker">{ticker}</div>'
            '<div class="agent-status mixed">DATA ERROR</div>'
            f'<div class="agent-meta">{e}</div></div>'
        )
        st.markdown(error_html, unsafe_allow_html=True)


# ============================================================
# Account strip (paper trading)
# ============================================================

equity_now = paper_state["cash"]
if paper_state["equity_curve"]:
    equity_now = paper_state["equity_curve"][-1][1]
total_pnl = equity_now - paper_state["settings"]["starting_balance"]
wr = pt.win_rate(paper_state)

acct_cols = st.columns(4)
with acct_cols[0]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Balance (sim)</div>'
        f'<div class="stat-value">${equity_now:,.2f}</div>'
        f'<div class="stat-sub">start ${paper_state["settings"]["starting_balance"]:,.0f}</div></div>',
        unsafe_allow_html=True,
    )
with acct_cols[1]:
    pnl_class = "up" if total_pnl >= 0 else "down"
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Total P&amp;L (sim)</div>'
        f'<div class="stat-value {pnl_class}">{total_pnl:+,.2f}</div>'
        f'<div class="stat-sub">{len(paper_state["closed_trades"])} closed trades</div></div>',
        unsafe_allow_html=True,
    )
with acct_cols[2]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Win Rate (sim)</div>'
        f'<div class="stat-value">{f"{wr:.1f}%" if wr is not None else "--"}</div>'
        f'<div class="stat-sub">of closed trades</div></div>',
        unsafe_allow_html=True,
    )
with acct_cols[3]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Open Positions</div>'
        f'<div class="stat-value">{len(paper_state["positions"])}</div>'
        f'<div class="stat-sub">of {len(watchlist)} agents</div></div>',
        unsafe_allow_html=True,
    )

if paper_state["equity_curve"]:
    curve_df = pd.DataFrame(paper_state["equity_curve"], columns=["time", "equity"])
    curve_df["time"] = pd.to_datetime(curve_df["time"])
    curve_df = curve_df.set_index("time")
    st.line_chart(curve_df["equity"], height=180, color="#00FFA3")
st.caption("Simulated balance -- no real orders are placed. Past simulated results don't guarantee future performance.")

st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

# ============================================================
# Scanner stat strip
# ============================================================

today_str = now_et.strftime("%Y-%m-%d")
signals_today = [row for row in st.session_state.activity_log if row[0].startswith(today_str) and row[2] == "signal"]
longs_today = sum(1 for row in signals_today if row[3] == "long")
shorts_today = sum(1 for row in signals_today if row[3] == "short")

stat_cols = st.columns(4)
with stat_cols[0]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Cycle</div>'
        f'<div class="stat-value">{cycle}</div><div class="stat-sub">every {poll_seconds}s</div></div>',
        unsafe_allow_html=True,
    )
with stat_cols[1]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Uptime</div>'
        f'<div class="stat-value">{uptime_str}</div><div class="stat-sub">this session</div></div>',
        unsafe_allow_html=True,
    )
with stat_cols[2]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Signals Today</div>'
        f'<div class="stat-value">{len(signals_today)}</div>'
        f'<div class="stat-sub">{longs_today} long / {shorts_today} short</div></div>',
        unsafe_allow_html=True,
    )
with stat_cols[3]:
    st.markdown(
        f'<div class="stat-card"><div class="stat-label">Agents</div>'
        f'<div class="stat-value">{len(watchlist)}</div>'
        f'<div class="stat-sub">{"scanning" if should_scan else "standby"}</div></div>',
        unsafe_allow_html=True,
    )

st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

# ============================================================
# Agent grid
# ============================================================

if not watchlist:
    st.info("Add at least one ticker in the sidebar to start scanning.")
elif should_scan:
    n_cols = 3
    cols = st.columns(n_cols)
    for i, ticker in enumerate(watchlist):
        with cols[i % n_cols]:
            render_agent_card(i, ticker)
    pt.mark_to_market(paper_state, current_prices, now_et.strftime("%Y-%m-%d %H:%M:%S"))
    pt.save_state(paper_state)
else:
    st.info("Market is closed. Check \"Scan even when market is closed\" in the sidebar to preview anyway.")

# ============================================================
# Activity log
# ============================================================

st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
st.markdown('<div class="section-label">Activity Log</div>', unsafe_allow_html=True)

if st.session_state.activity_log:
    rows_html = ""
    for stamp, ticker, kind, direction, msg in st.session_state.activity_log[:30]:
        time_part = stamp.split(" ")[1] if " " in stamp else stamp
        dot_class = "paper" if kind == "paper" else direction
        prefix = "PAPER" if kind == "paper" else direction.upper()
        rows_html += (
            f'<div class="log-row">'
            f'<span><span class="log-dot {dot_class}"></span>'
            f'<span class="log-ticker">{ticker}</span>'
            f'<span class="log-msg">{prefix} -- {msg}</span></span>'
            f'<span class="log-time">{time_part}</span>'
            f'</div>'
        )
    st.markdown(f'<div class="stat-card">{rows_html}</div>', unsafe_allow_html=True)
else:
    st.caption("No activity yet this session.")

# ============================================================
# Trade history
# ============================================================

if paper_state["closed_trades"]:
    with st.expander(f"Trade History ({len(paper_state['closed_trades'])} closed)"):
        trades_df = pd.DataFrame(paper_state["closed_trades"])
        trades_df = trades_df[["ticker", "direction", "entry_price", "exit_price", "pnl", "reason", "opened_at", "closed_at"]]
        st.dataframe(trades_df, use_container_width=True, hide_index=True)
