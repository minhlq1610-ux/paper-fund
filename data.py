"""Price data: live from Yahoo Finance, or synthetic demo data for testing.

Returns a dict of DataFrames indexed by trading date, one column per ticker:
    close, high, low, dividends
Raw (unadjusted) prices are used so fills can be checked against the day's
real high/low. Dividends are handled separately as cash flows in the engine.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd


def load_prices(tickers: list[str], start: str, end: str | None = None,
                demo: bool = False) -> tuple[dict[str, pd.DataFrame], bool]:
    """Return (prices, is_demo). Falls back to demo data if the download fails."""
    tickers = sorted(set(tickers))
    if not demo:
        try:
            prices = _load_yahoo(tickers, start, end)
            missing = [t for t in tickers if t not in prices["close"].columns
                       or prices["close"][t].dropna().empty]
            if missing:
                raise ValueError(f"No data for: {', '.join(missing)}")
            return prices, False
        except Exception as exc:  # network blocked, ticker typo, Yahoo outage
            print(f"[data] live download failed ({exc}); using demo data")
    return _demo_prices(tickers, start, end), True


def _load_yahoo(tickers, start, end):
    import yfinance as yf

    raw = yf.download(tickers, start=start, end=end, auto_adjust=False,
                      actions=True, progress=False, group_by="column",
                      threads=True)
    if raw.empty:
        raise ValueError("empty download")
    out = {}
    for field, key in [("Close", "close"), ("High", "high"), ("Low", "low"),
                       ("Dividends", "dividends")]:
        if field in raw.columns.get_level_values(0):
            df = raw[field].copy()
        else:
            df = pd.DataFrame(0.0, index=raw.index, columns=tickers)
        if isinstance(df, pd.Series):
            df = df.to_frame(tickers[0])
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        out[key] = df
    out["dividends"] = out["dividends"].fillna(0.0)
    return out


def _demo_prices(tickers, start, end):
    """Deterministic fake prices (same ticker -> same path every run)."""
    end = end or pd.Timestamp.today().normalize()
    idx = pd.bdate_range(start, end)
    rng_mkt = np.random.default_rng(42)
    mkt = rng_mkt.normal(0.0003, 0.009, len(idx))
    close, high, low = {}, {}, {}
    for t in tickers:
        seed = zlib.crc32(t.encode())
        rng = np.random.default_rng(seed)
        beta = 1.0 if t.startswith("^") else 0.6 + (seed % 90) / 100
        idio = 0.0 if t.startswith("^") else rng.normal(0, 0.012, len(idx))
        rets = beta * mkt + idio
        p0 = 8000.0 if t.startswith("^") else 10 + seed % 90
        px = p0 * np.exp(np.cumsum(rets))
        spread = np.abs(rng.normal(0, 0.008, len(idx))) + 0.002
        close[t] = px
        high[t] = px * (1 + spread)
        low[t] = px * (1 - spread)
    mk = lambda d: pd.DataFrame(d, index=idx)
    return {"close": mk(close), "high": mk(high), "low": mk(low),
            "dividends": pd.DataFrame(0.0, index=idx, columns=tickers)}
