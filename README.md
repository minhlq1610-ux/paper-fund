# Paper Long/Short Fund (ASX)

A simulated long/short equity fund for a small group. Each member runs a sleeve.
Trades are logged in `trades.csv` on GitHub, so every trade carries a commit
timestamp nobody can edit afterwards. The dashboard marks everything to market
on real ASX closing prices, after brokerage, borrow fees and dividends.

## Files

| File | What it is |
|---|---|
| `trades.csv` | The trade blotter. **The only file you touch day to day.** |
| `fund.toml` | Sleeves, starting capital, cost assumptions, risk limits |
| `app.py` | The dashboard |
| `engine.py` | All the accounting (NAV, costs, exposures, beta, limit checks) |
| `data.py` | Price download from Yahoo Finance, plus demo prices for testing |
| `tests/` | Hand-checkable tests of the accounting |

## Set it up (about 20 minutes, no coding)

1. **Make a GitHub account** and create a new **public** repository, e.g. `paper-fund`.
   Public matters: anyone can audit your trade history.
2. **Upload all these files** (Add file → Upload files). Keep the `tests` folder.
3. **Deploy free on Streamlit Community Cloud** (share.streamlit.io): sign in with
   GitHub → *Create app* → pick the repo → main file `app.py` → Deploy.
   You get a link to send your friends and put on your CV.
4. **Edit `fund.toml`**: rename the sleeves to your names, set the fund name.
5. **Delete the EXAMPLE rows** from `trades.csv` (keep the header line).

## How to trade

Edit `trades.csv` on GitHub (pencil icon), add a row, commit. The app updates
within a minute or two.

```
date,sleeve,ticker,action,quantity,price,thesis,kill_criteria
2026-12-03,lucian,BHP.AX,BUY,500,,Iron ore floor at $90 + buyback,Exit if Chinese steel output falls 3 months running
```

- **date**: the day you trade, `YYYY-MM-DD`. House rule: it must match the commit date.
- **ticker**: ASX codes end in `.AX` (BHP.AX, CBA.AX). For an index hedge, short `STW.AX` (ASX 200 ETF).
- **action**: `BUY` / `SELL` for longs, `SHORT` / `COVER` for shorts.
- **price**: leave blank to fill at that day's close. If you enter a price, it must sit
  inside that day's low–high range or the trade is rejected.
- **thesis / kill_criteria**: required by house rules. Wrap in quotes if you use a comma.

## Accounting rules (so anyone can check the numbers)

- NAV = cash + longs − shorts, marked at the daily close.
- Brokerage = the higher of 0.08% or $10 per trade (change in `fund.toml`).
- Borrow fee: 2% a year on every short, charged daily. Add hard-to-borrow names under `borrow_overrides`.
- Dividends: longs receive them and shorts pay them on the ex-date. Franking credits are ignored.
- Trades on weekends or holidays fill on the next trading day.
- A sleeve whose worst drawdown passes the stop-out limit shows as a breach permanently.

## Weekly audit (do not skip)

Once a week, pick two positions and recompute their P&L by hand in Excel from
the trade log and the closing price. If the app and Excel disagree, stop and fix
it before trading further. A single accounting bug invalidates the track record.

## Run it on your own computer (optional)

```
pip install -r requirements.txt
streamlit run app.py
python -m pytest -q          # accounting tests
```
Set `FUND_DEMO=1` or use the sidebar toggle to run on fake prices.

## Limits of v1

- Stocks and ETFs only. No options or futures yet (free ASX options data doesn't exist).
- Prices come from Yahoo Finance through an unofficial library. It occasionally breaks; the
  app then shows a red DEMO banner rather than wrong numbers silently.
- Short proceeds sit in cash earning `cash_rate` (default 0%). Real prime brokers pay a rebate below cash rate.
