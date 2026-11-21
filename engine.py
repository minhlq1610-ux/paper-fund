"""Fund engine: turns trades.csv + prices into NAV, positions, exposures and risk.

Accounting rules (keep these in the README so anyone can audit the numbers):
- Each sleeve starts with its paper capital in cash.
- NAV = cash + sum(quantity x close). Shorts carry negative quantity, so a
  short adds its sale proceeds to cash and subtracts its market value.
- Fill price = the price you entered (must sit inside that day's low-high
  range) or, if left blank, that day's close. Trades on non-trading days fill
  on the next trading day.
- Brokerage = max(brokerage_min, brokerage_bps x trade value).
- Borrow fee accrues every calendar day on the value of each open short.
- Dividends: longs receive them, shorts pay them, on the ex-date.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ACTIONS = {"BUY": 1, "SELL": -1, "SHORT": -1, "COVER": 1}


def load_config(path="fund.toml") -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_trades(path="trades.csv") -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna("")
    df.columns = [c.strip().lower() for c in df.columns]
    df["row"] = np.arange(2, len(df) + 2)  # spreadsheet row number, for errors
    df = df[df["ticker"].str.strip() != ""].copy()
    df["date"] = pd.to_datetime(df["date"].str.strip(), errors="coerce")
    df["sleeve"] = df["sleeve"].str.strip()
    df["ticker"] = df["ticker"].str.strip().str.upper()
    df["action"] = df["action"].str.strip().str.upper()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"].replace("", np.nan), errors="coerce")
    for c in ("thesis", "kill_criteria"):
        if c not in df:
            df[c] = ""
    return df


@dataclass
class Sleeve:
    name: str
    capital: float
    cash: float = 0.0
    qty: dict = field(default_factory=dict)
    avg: dict = field(default_factory=dict)     # average entry price
    opened: dict = field(default_factory=dict)  # date position was opened
    meta: dict = field(default_factory=dict)    # latest thesis / kill criteria
    realised: float = 0.0
    fees: float = 0.0
    borrow: float = 0.0
    divs: float = 0.0
    interest: float = 0.0

    def __post_init__(self):
        self.cash = self.capital


def run(trades: pd.DataFrame, prices: dict, cfg: dict) -> dict:
    close = prices["close"].sort_index().ffill()
    high, low = prices["high"].reindex(close.index), prices["low"].reindex(close.index)
    divs = prices["dividends"].reindex(close.index).fillna(0.0)
    costs, limits = cfg["costs"], cfg["limits"]
    bench = cfg["fund"]["benchmark"]

    errors: list[str] = []
    sleeves = {n: Sleeve(n, float(c)) for n, c in cfg["sleeves"].items()}

    # ---------- validate and fill trades ----------
    fills = []
    for _, t in trades.iterrows():
        where = f"trades.csv row {t['row']}"
        if pd.isna(t["date"]):
            errors.append(f"{where}: date is not YYYY-MM-DD"); continue
        if t["sleeve"] not in sleeves:
            errors.append(f"{where}: sleeve '{t['sleeve']}' is not in fund.toml"); continue
        if t["action"] not in ACTIONS:
            errors.append(f"{where}: action must be BUY, SELL, SHORT or COVER"); continue
        if not (t["quantity"] > 0):
            errors.append(f"{where}: quantity must be a positive number"); continue
        if t["ticker"] not in close.columns or close[t["ticker"]].dropna().empty:
            errors.append(f"{where}: no price data for {t['ticker']} (ASX tickers end in .AX)"); continue
        later = close.index[close.index >= t["date"]]
        if len(later) == 0:
            errors.append(f"{where}: no trading day on/after {t['date'].date()} yet"); continue
        d = later[0]
        px_close = close.at[d, t["ticker"]]
        px = px_close
        if not pd.isna(t["price"]):
            lo, hi = low.at[d, t["ticker"]], high.at[d, t["ticker"]]
            if pd.notna(lo) and pd.notna(hi) and not (lo - 1e-9 <= t["price"] <= hi + 1e-9):
                errors.append(f"{where}: price {t['price']} is outside {t['ticker']}'s "
                              f"range on {d.date()} ({lo:.3f}-{hi:.3f}); trade ignored")
                continue
            px = float(t["price"])
        fills.append({**t.to_dict(), "fill_date": d, "fill_price": float(px)})
    fills = pd.DataFrame(fills)
    if fills.empty:
        return {"errors": errors + ["No valid trades yet."], "empty": True}
    fills = fills.sort_values(["fill_date", "row"], kind="stable")

    start = fills["fill_date"].min()
    dates = close.index[close.index >= start]
    tickers = sorted(fills["ticker"].unique())

    hist, trade_log = [], []
    prev = None
    for d in dates:
        days = (d - prev).days if prev is not None else 0
        for s in sleeves.values():
            # overnight costs on positions held since yesterday
            if days:
                for tk, q in s.qty.items():
                    if q < 0:
                        rate = costs.get("borrow_overrides", {}).get(tk, costs["default_borrow_rate"])
                        fee = -q * close.at[prev, tk] * rate * days / 365
                        s.cash -= fee; s.borrow += fee
                if s.cash > 0 and costs.get("cash_rate", 0):
                    i = s.cash * costs["cash_rate"] * days / 365
                    s.cash += i; s.interest += i
                # dividends on positions held into the ex-date
                for tk, q in s.qty.items():
                    dv = divs.at[d, tk] if tk in divs.columns else 0.0
                    if dv:
                        s.cash += q * dv; s.divs += q * dv
        # today's trades
        for _, f in fills[fills["fill_date"] == d].iterrows():
            s = sleeves[f["sleeve"]]
            tk, n, px = f["ticker"], float(f["quantity"]), f["fill_price"]
            q0 = s.qty.get(tk, 0.0)
            act = f["action"]
            bad = ((act == "BUY" and q0 < 0) or (act == "SHORT" and q0 > 0) or
                   (act == "SELL" and n > q0 + 1e-9) or (act == "COVER" and n > -q0 + 1e-9))
            if bad:
                errors.append(f"trades.csv row {f['row']}: {act} {n:g} {tk} doesn't match the "
                              f"open position ({q0:g}); use BUY/SELL for longs, SHORT/COVER for shorts")
                continue
            dq = ACTIONS[act] * n
            value = n * px
            fee = max(costs["brokerage_min"], costs["brokerage_bps"] / 1e4 * value)
            s.cash -= dq * px + fee
            s.fees += fee
            realised = 0.0
            if act in ("SELL", "COVER"):
                realised = (px - s.avg[tk]) * n * (1 if act == "SELL" else -1)
                s.realised += realised
            else:
                s.avg[tk] = (s.avg.get(tk, 0) * abs(q0) + px * n) / (abs(q0) + n)
                if q0 == 0:
                    s.opened[tk] = d
            q1 = q0 + dq
            if abs(q1) < 1e-9:
                s.qty.pop(tk, None); s.avg.pop(tk, None); s.opened.pop(tk, None)
            else:
                s.qty[tk] = q1
            s.meta[tk] = (f["thesis"], f["kill_criteria"])
            trade_log.append({"date": d.date(), "sleeve": s.name, "ticker": tk, "action": act,
                              "quantity": n, "fill_price": px, "value": value,
                              "brokerage": fee, "realised_pnl": realised,
                              "thesis": f["thesis"], "kill_criteria": f["kill_criteria"]})
        # mark to market
        for s in sleeves.values():
            lmv = sum(q * close.at[d, tk] for tk, q in s.qty.items() if q > 0)
            smv = -sum(q * close.at[d, tk] for tk, q in s.qty.items() if q < 0)
            hist.append({"date": d, "sleeve": s.name, "nav": s.cash + lmv - smv,
                         "long": lmv, "short": smv, "cash": s.cash})
        prev = d

    hist = pd.DataFrame(hist)
    daily = hist.pivot(index="date", columns="sleeve", values="nav")
    daily["FUND"] = daily.sum(axis=1)
    capital = {n: s.capital for n, s in sleeves.items()}
    capital["FUND"] = sum(capital.values())
    idx = daily / pd.Series(capital) * 100
    dd = daily / daily.cummax() - 1

    # ---------- beta ----------
    rets = close.pct_change()
    lb = cfg["fund"].get("beta_lookback_days", 252)
    betas = {}
    for tk in tickers:
        if tk == bench:
            betas[tk] = 1.0; continue
        pair = rets[[tk, bench]].dropna().tail(lb)
        betas[tk] = (pair[tk].cov(pair[bench]) / pair[bench].var()) if len(pair) > 20 else np.nan

    # ---------- current positions & risk ----------
    last = dates[-1]
    pos_rows, risk_rows = [], []
    for s in sleeves.values():
        nav = daily[s.name].iloc[-1]
        lmv = smv = bexp = 0.0
        for tk, q in s.qty.items():
            px = close.at[last, tk]
            mv = q * px
            lmv += max(mv, 0); smv += max(-mv, 0)
            b = betas.get(tk, np.nan)
            bexp += mv * (0 if np.isnan(b) else b)
            th, kc = s.meta.get(tk, ("", ""))
            pos_rows.append({"sleeve": s.name, "ticker": tk, "side": "Long" if q > 0 else "Short",
                             "quantity": q, "avg_entry": s.avg[tk], "last": px,
                             "market_value": mv, "pct_nav": mv / nav if nav else np.nan,
                             "unrealised_pnl": (px - s.avg[tk]) * q, "beta": b,
                             "days_held": (last - s.opened[tk]).days,
                             "thesis": th, "kill_criteria": kc})
        risk_rows.append({"sleeve": s.name, "nav": nav, "return": nav / s.capital - 1,
                          "long": lmv, "short": smv, "gross": (lmv + smv) / nav,
                          "net": (lmv - smv) / nav, "beta": bexp / nav,
                          "drawdown": dd[s.name].iloc[-1], "max_drawdown": dd[s.name].min(),
                          "realised": s.realised, "brokerage": s.fees, "borrow": s.borrow,
                          "dividends": s.divs, "interest": s.interest})
    risk = pd.DataFrame(risk_rows).set_index("sleeve")
    tot = risk[["nav", "long", "short", "realised", "brokerage", "borrow", "dividends", "interest"]].sum()
    bexp_total = (risk["beta"] * risk["nav"]).sum()
    tot["return"] = tot["nav"] / capital["FUND"] - 1
    tot["gross"] = (tot["long"] + tot["short"]) / tot["nav"]
    tot["net"] = (tot["long"] - tot["short"]) / tot["nav"]
    tot["beta"] = bexp_total / tot["nav"]
    tot["drawdown"] = dd["FUND"].iloc[-1]
    tot["max_drawdown"] = dd["FUND"].min()
    risk.loc["FUND"] = tot

    positions = pd.DataFrame(pos_rows)
    breaches = check_limits(risk, positions, limits)

    b = close[bench].reindex(daily.index)
    bench_idx = b / b.iloc[0] * 100

    return {"empty": False, "errors": errors, "as_of": last, "index": idx, "nav": daily,
            "drawdown": dd, "bench_index": bench_idx, "risk": risk, "positions": positions,
            "trades": pd.DataFrame(trade_log), "breaches": breaches, "betas": betas}


def check_limits(risk: pd.DataFrame, positions: pd.DataFrame, L: dict) -> pd.DataFrame:
    rows = []

    def add(scope, rule, value, limit, ok):
        rows.append({"scope": scope, "rule": rule, "value": value, "limit": limit,
                     "status": "OK" if ok else "BREACH"})

    for name, r in risk.iterrows():
        add(name, "Gross exposure", r["gross"], f"<= {L['max_gross']:.0%}", r["gross"] <= L["max_gross"])
        add(name, "Net exposure", r["net"], f"{L['min_net']:.0%} to {L['max_net']:.0%}",
            L["min_net"] <= r["net"] <= L["max_net"])
        add(name, "Beta", r["beta"], f"{L['min_beta']:.2f} to {L['max_beta']:.2f}",
            L["min_beta"] <= r["beta"] <= L["max_beta"])
        if name != "FUND":
            # worst drawdown ever, not today's: once stopped out, always stopped out
            add(name, "Worst drawdown (stop-out)", r["max_drawdown"], f">= -{L['max_drawdown']:.0%}",
                r["max_drawdown"] >= -L["max_drawdown"])
    if not positions.empty:
        for _, p in positions.iterrows():
            cap = L["max_long_position"] if p["side"] == "Long" else L["max_short_position"]
            w = abs(p["pct_nav"])
            if w > cap:
                add(p["sleeve"], f"{p['side']} size: {p['ticker']}", w, f"<= {cap:.0%}", False)
    return pd.DataFrame(rows)
