"""Paper long/short fund dashboard.  Run:  streamlit run app.py"""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data import load_prices
from engine import load_config, load_trades, run

# Fixed categorical order (colour follows the sleeve, never its rank)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
MUTED, GRID, INK = "#898781", "#e1e0d9", "#52514e"

st.set_page_config(page_title="Paper Fund", layout="wide")
cfg = load_config()
trades = load_trades()
sleeve_names = list(cfg["sleeves"])
colour = {n: SERIES[i % len(SERIES)] for i, n in enumerate(sleeve_names)}
colour["FUND"] = "#0b0b0b"

# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Controls")
    force_demo = st.toggle("Use demo prices", value=os.getenv("FUND_DEMO") == "1",
                           help="Fake prices for testing. Turn off for real ASX data.")
    if st.button("Refresh prices"):
        st.cache_data.clear()
    st.caption("To trade: edit **trades.csv** on GitHub. Each edit is a timestamped commit.")


@st.cache_data(ttl=3600, show_spinner="Loading ASX prices…")
def get_prices(tickers, start, demo):
    return load_prices(list(tickers), start, demo=demo)


valid_dates = trades["date"].dropna()
if valid_dates.empty:
    st.info("No trades yet. Add rows to trades.csv to start the book.")
    st.stop()
# a year of history before the first trade, for beta
start = (valid_dates.min() - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
tickers = tuple(sorted(set(trades["ticker"]) | {cfg["fund"]["benchmark"]}))
prices, is_demo = get_prices(tickers, start, force_demo)
res = run(trades, prices, cfg)

# ---------------- header ----------------
st.title(cfg["fund"]["name"])
if is_demo:
    st.error("⚠ DEMO PRICES — not real market data. Numbers below are for testing only.")
if res["errors"]:
    with st.expander(f"⚠ {len(res['errors'])} problem(s) in trades.csv", expanded=True):
        for e in res["errors"]:
            st.write("• " + e)
if res.get("empty"):
    st.stop()

st.caption(f"Marked to close of {res['as_of']:%a %d %b %Y} · benchmark S&P/ASX 200 · "
           f"all figures in A$, after brokerage, borrow fees and dividends")

risk, pos = res["risk"], res["positions"]
f = risk.loc["FUND"]
b_ret = res["bench_index"].iloc[-1] / 100 - 1

c = st.columns(6)
c[0].metric("Fund NAV", f"${f['nav']:,.0f}")
c[1].metric("Return", f"{f['return']:+.2%}", f"{f['return'] - b_ret:+.2%} vs ASX 200")
c[2].metric("Gross", f"{f['gross']:.0%}")
c[3].metric("Net", f"{f['net']:+.0%}")
c[4].metric("Beta", f"{f['beta']:+.2f}")
c[5].metric("Worst drawdown", f"{f['max_drawdown']:.2%}")

# ---------------- limits ----------------
br = res["breaches"]
bad = br[br["status"] == "BREACH"]
if bad.empty:
    st.success("✓ OK — all risk limits within bounds")
else:
    st.error(f"✕ BREACH — {len(bad)} risk limit(s) broken")
    st.dataframe(bad.assign(value=bad["value"].map(lambda v: f"{v:.2%}" if abs(v) < 5 else f"{v:.2f}")),
                 hide_index=True, width="stretch")


def style(fig, ytitle, pct=False):
    fig.update_layout(height=360, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="#fcfcfb",
                      paper_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
                      legend=dict(orientation="h", y=1.12, x=0, font=dict(color=INK)),
                      font=dict(color=INK))
    fig.update_xaxes(showgrid=False, linecolor="#c3c2b7", tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont=dict(color=MUTED),
                     title=dict(text=ytitle, font=dict(color=MUTED)),
                     tickformat=".0%" if pct else None)
    return fig


show = st.multiselect("Sleeves on charts", sleeve_names, default=sleeve_names)

left, right = st.columns(2)
with left:
    st.subheader("Performance (start = 100)")
    fig = go.Figure()
    fig.add_scatter(x=res["bench_index"].index, y=res["bench_index"], name="ASX 200",
                    line=dict(color=MUTED, width=2, dash="dot"))
    for n in show:
        fig.add_scatter(x=res["index"].index, y=res["index"][n], name=n,
                        line=dict(color=colour[n], width=2))
    fig.add_scatter(x=res["index"].index, y=res["index"]["FUND"], name="Fund",
                    line=dict(color=colour["FUND"], width=2.5))
    fig.update_traces(hovertemplate="%{y:.1f}")
    st.plotly_chart(style(fig, "Index"), width="stretch")
with right:
    st.subheader("Drawdown from peak")
    fig = go.Figure()
    for n in show:
        fig.add_scatter(x=res["drawdown"].index, y=res["drawdown"][n], name=n,
                        line=dict(color=colour[n], width=2))
    fig.add_hline(y=-cfg["limits"]["max_drawdown"], line=dict(color="#d03b3b", width=1, dash="dash"),
                  annotation_text="stop-out", annotation_font_color="#d03b3b")
    fig.update_traces(hovertemplate="%{y:.2%}")
    st.plotly_chart(style(fig, "Drawdown", pct=True), width="stretch")

st.subheader("Exposure by sleeve (% of sleeve NAV)")
ex = risk.loc[[n for n in show] + ["FUND"]]
fig = go.Figure()
fig.add_bar(y=ex.index, x=ex["long"] / ex["nav"], name="Long", orientation="h",
            marker=dict(color=SERIES[0], cornerradius=4), hovertemplate="Long %{x:.0%}<extra></extra>")
fig.add_bar(y=ex.index, x=-ex["short"] / ex["nav"], name="Short", orientation="h",
            marker=dict(color=SERIES[1], cornerradius=4), hovertemplate="Short %{x:.0%}<extra></extra>")
fig.update_layout(barmode="relative", bargap=0.45)
fig = style(fig, "")
fig.update_layout(height=80 + 45 * len(ex), hovermode="closest")
fig.update_xaxes(tickformat=".0%", showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor="#c3c2b7")
st.plotly_chart(fig, width="stretch")

# ---------------- tables ----------------
st.subheader("Open positions")
if pos.empty:
    st.write("No open positions.")
else:
    st.dataframe(
        pos[pos["sleeve"].isin(show)],
        hide_index=True, width="stretch",
        column_config={
            "avg_entry": st.column_config.NumberColumn("Avg entry", format="$%.3f"),
            "last": st.column_config.NumberColumn("Last", format="$%.3f"),
            "market_value": st.column_config.NumberColumn("Market value", format="dollar"),
            "pct_nav": st.column_config.NumberColumn("% sleeve NAV", format="percent"),
            "unrealised_pnl": st.column_config.NumberColumn("Unrealised P&L", format="dollar"),
            "beta": st.column_config.NumberColumn("Beta", format="%.2f"),
        })

st.subheader("Sleeve scorecard")
sc = risk.copy()
st.dataframe(
    sc, width="stretch",
    column_config={
        "nav": st.column_config.NumberColumn("NAV", format="$%,.0f"),
        "return": st.column_config.NumberColumn("Return", format="percent"),
        "long": st.column_config.NumberColumn("Long $", format="$%,.0f"),
        "short": st.column_config.NumberColumn("Short $", format="$%,.0f"),
        "gross": st.column_config.NumberColumn("Gross", format="percent"),
        "net": st.column_config.NumberColumn("Net", format="percent"),
        "beta": st.column_config.NumberColumn("Beta", format="%.2f"),
        "drawdown": st.column_config.NumberColumn("Drawdown", format="percent"),
        "max_drawdown": st.column_config.NumberColumn("Worst DD", format="percent"),
        "realised": st.column_config.NumberColumn("Realised P&L", format="dollar"),
        "brokerage": st.column_config.NumberColumn("Brokerage", format="$%,.0f"),
        "borrow": st.column_config.NumberColumn("Borrow fees", format="$%,.0f"),
        "dividends": st.column_config.NumberColumn("Dividends", format="dollar"),
        "interest": st.column_config.NumberColumn("Interest", format="$%,.0f"),
    })

with st.expander("Trade log (fills and costs)"):
    st.dataframe(res["trades"], hide_index=True, width="stretch")
    st.download_button("Download trade log CSV", res["trades"].to_csv(index=False), "trade_log.csv")
