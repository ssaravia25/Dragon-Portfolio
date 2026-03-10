#!/usr/bin/env python3
"""
Iberic Centinela — Dual SMA — Live Dashboard (2026)
UCITS-compliant Dragon Portfolio. Dual SMA: SMA200 exposure + SMA50 exit.
$10,000 initial capital. Daily email with SMA50 alerts + monthly rebalancing.
ALERT_MODE=1 env var → pre-close lightweight run (skip HTML, only send alerts).

SFinance-alicIA
"""
import yfinance as yf
import numpy as np
import datetime, os, math, json, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email_recipients import get_recipients

# ═══════════════════════════════════════════════════════════════════
# 1. CONFIG
# ═══════════════════════════════════════════════════════════════════
UNIVERSES = {
    "Equity":      ["CSPX.L", "EQQQ.L", "XRS2.DE", "EIMI.L", "IMEU.L", "IQQK.DE", "IQQB.DE"],
    "Bonds":       ["IBTS.L", "IBTM.L", "IDTL.L", "ITPS.L", "LQDE.L"],
    "HardAssets":  ["IGLN.L", "ISLN.L", "COPA.L"],
    "LongVol":     ["0P0001A2HU.F"],
    "Commodities": ["ICOM.L"],
}
ALL_TICKERS = sorted(set(t for lst in UNIVERSES.values() for t in lst))
LATE_JOINERS = set()
CORE_TICKERS = sorted(t for t in ALL_TICKERS if t not in LATE_JOINERS)

N_SELECT = 3
MOM_LOOKBACK = 126
SMA_LONG = 200
SMA_CMDTY = 50
MIN_EXPOSURE = 0.30
SMA_EXIT = 50
TX_COST_BPS = 30
EXIT_BLOCKS = {"Equity", "HardAssets"}

START = "2018-01-02"
TODAY = datetime.date.today()
TOMORROW = TODAY + datetime.timedelta(days=1)
LIVE_YEAR = TODAY.year
RF_ANNUAL = 0.043
LONGVOL_LEVERAGE = 1.0
INITIAL_CAPITAL = 10_000
ALERT_MODE = os.environ.get("ALERT_MODE") == "1"

# ── Email config ──
GMAIL_SENDER = "sgseaux@gmail.com"
EMAIL_RECIPIENTS = get_recipients("Iberic")

W_DRAGON = {"Equity": 0.24, "Bonds": 0.18, "HardAssets": 0.19, "LongVol": 0.21, "CmdtyTrend": 0.18}
W_6040 = {"Equity": 0.60, "Bonds": 0.40}

BLOCK_LABELS = {"Equity": "Equity", "Bonds": "Bonds", "HardAssets": "Hard Assets",
                "LongVol": "Long Vol", "CmdtyTrend": "Cmdty Trend"}
BLOCK_COLORS = {
    "Equity": "#10b981", "Bonds": "#06b6d4", "HardAssets": "#f59e0b",
    "LongVol": "#ef4444", "CmdtyTrend": "#a855f7",
}

TICKER_LABELS = {
    "CSPX.L": "S&P 500", "EQQQ.L": "Nasdaq-100", "XRS2.DE": "Russell 2000",
    "EIMI.L": "Emergentes", "IMEU.L": "Europa", "IQQK.DE": "Korea", "IQQB.DE": "Brasil",
    "IBTS.L": "Treasury 1-3Y", "IBTM.L": "Treasury 7-10Y", "IDTL.L": "Treasury 20+Y",
    "ITPS.L": "TIPS", "LQDE.L": "IG Corp",
    "IGLN.L": "Oro", "ISLN.L": "Plata", "COPA.L": "Cobre",
    "0P0001A2HU.F": "JPM Abs Alpha", "ICOM.L": "Commodities",
}

TICKER_COLORS = {
    "CSPX.L": "#3b82f6", "EQQQ.L": "#8b5cf6", "XRS2.DE": "#f97316",
    "EIMI.L": "#f59e0b", "IMEU.L": "#06b6d4", "IQQK.DE": "#ec4899", "IQQB.DE": "#22c55e",
    "IBTS.L": "#94a3b8", "IBTM.L": "#38bdf8", "IDTL.L": "#0ea5e9", "ITPS.L": "#f97316", "LQDE.L": "#10b981",
    "IGLN.L": "#f59e0b", "ISLN.L": "#94a3b8", "COPA.L": "#f97316",
    "0P0001A2HU.F": "#ef4444", "ICOM.L": "#a855f7",
}

# ═══════════════════════════════════════════════════════════════════
# 2. DATA FETCHING (with local cache)
# ═══════════════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_cache_iberic.json")

print(f"═══ Iberic Centinela — Dual SMA — Live Dashboard {LIVE_YEAR} ═══\n")

def _market_is_closed():
    """True if European markets are closed (after 16:35 GMT / LSE close)."""
    from zoneinfo import ZoneInfo
    london = datetime.datetime.now(ZoneInfo("Europe/London"))
    if london.weekday() >= 5:
        return True
    return london.hour >= 17  # after 5pm London (LSE closes 16:30)

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        cached_date = cache.get("date")
        last_price_date = cache["dates"][-1] if cache.get("dates") else None
        if cached_date == str(TODAY):
            if last_price_date == str(TODAY):
                print(f"Using cached data (today's close included, {last_price_date})...")
                return cache
            elif not _market_is_closed():
                print(f"Using cached data (market still open, last: {last_price_date})...")
                return cache
            else:
                print(f"Cache stale: last price {last_price_date}, market closed — re-downloading...")
                return None
    except:
        pass
    return None

def save_cache(dates_list, data_dict):
    cache = {
        "date": str(TODAY),
        "last_price_date": str(dates_list[-1]) if dates_list else "",
        "dates": [str(d) for d in dates_list],
        "prices": {t: [None if (isinstance(v, float) and np.isnan(v)) else v
                       for v in data_dict[t].tolist()] for t in data_dict},
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)
    print(f"  Cache saved: {CACHE_FILE} (last: {dates_list[-1]})")

cached = load_cache()

if cached:
    dates = [datetime.date.fromisoformat(d) for d in cached["dates"]]
    N = len(dates)
    price_data = {}
    for t in cached["prices"]:
        if t in ALL_TICKERS:
            price_data[t] = np.array([np.nan if v is None else v for v in cached["prices"][t]])
    for t in price_data:
        valid = np.count_nonzero(~np.isnan(price_data[t]))
        print(f"  + {t}: {valid}/{len(price_data[t])} days (cached)")
    for t in ALL_TICKERS:
        if t not in price_data:
            print(f"  ! {t} missing from cache — forcing re-download")
            cached = None
            break

if not cached:
    print("Fetching data from Yahoo Finance...")
    prices = {}
    failed_tickers = []
    for ticker in ALL_TICKERS:
        try:
            df = yf.download(ticker, start=START, end=str(TOMORROW), progress=False, auto_adjust=True)
            if len(df) > 100:
                prices[ticker] = df[["Close"]].copy()
                prices[ticker].columns = [ticker]
                print(f"  + {ticker}: {len(df)} days")
            else:
                print(f"  x {ticker}: insufficient data ({len(df)} days)")
                failed_tickers.append(ticker)
        except Exception as e:
            print(f"  x {ticker}: download failed ({e})")
            failed_tickers.append(ticker)

    if failed_tickers:
        print(f"\n  WARNING: Failed tickers: {', '.join(failed_tickers)}")
        for ft in failed_tickers:
            ALL_TICKERS = [t for t in ALL_TICKERS if t != ft]
            CORE_TICKERS = [t for t in CORE_TICKERS if t != ft]
            for block in UNIVERSES:
                UNIVERSES[block] = [t for t in UNIVERSES[block] if t != ft]
            if ft in LATE_JOINERS:
                LATE_JOINERS.discard(ft)
        print(f"  Continuing with {len(ALL_TICKERS)} tickers\n")

    if not CORE_TICKERS:
        raise RuntimeError("No valid tickers available. Check your internet connection.")

    common_idx = prices[CORE_TICKERS[0]].index
    for t in CORE_TICKERS[1:]:
        if t in prices:
            common_idx = common_idx.intersection(prices[t].index)
    common_idx = common_idx.sort_values()

    price_data = {}
    for t in CORE_TICKERS:
        if t in prices:
            price_data[t] = prices[t].loc[common_idx, t].values.astype(float)

    for t in LATE_JOINERS:
        if t in prices:
            merged = prices[t].reindex(common_idx)
            price_data[t] = merged[t].values.astype(float)
            valid = np.count_nonzero(~np.isnan(price_data[t]))
            print(f"  * {t}: {valid}/{len(common_idx)} days (late joiner)")
        else:
            price_data[t] = np.full(len(common_idx), np.nan)

    dates = [d.date() if hasattr(d, "date") else d for d in common_idx]
    N = len(dates)
    save_cache(dates, price_data)

print(f"\n  Aligned: {N} trading days ({dates[0]} -> {dates[-1]})")

# Daily returns
ret = {}
for t in ALL_TICKERS:
    if t in price_data:
        p = price_data[t]
        ret[t] = np.diff(p) / p[:-1]
dates_ret = dates[1:]
N_ret = len(dates_ret)

# ═══════════════════════════════════════════════════════════════════
# 3. SIGNALS: Momentum + SMA200 + SMA50
# ═══════════════════════════════════════════════════════════════════
print("\nComputing signals...")

mom = {}
for t in ALL_TICKERS:
    if t not in price_data:
        continue
    p = price_data[t]
    m = np.full(N_ret, np.nan)
    for i in range(N_ret):
        if i >= MOM_LOOKBACK:
            p_now = p[i + 1]
            p_prev = p[i + 1 - MOM_LOOKBACK]
            if not np.isnan(p_now) and not np.isnan(p_prev) and p_prev > 0:
                m[i] = p_now / p_prev - 1
    mom[t] = m

sma200_above = {}
sma200_values = {}
for t in ALL_TICKERS:
    if t not in price_data:
        continue
    p = price_data[t]
    signal = np.full(N, False)
    sma_v = np.full(N, np.nan)
    for i in range(N):
        if i >= SMA_LONG:
            window = p[i - SMA_LONG + 1:i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) >= SMA_LONG * 0.8:
                sma_v[i] = np.mean(valid)
                signal[i] = p[i] > sma_v[i]
    sma200_above[t] = signal[1:]
    sma200_values[t] = sma_v

sma_exit_above = {}
sma50_values = {}
for t in ALL_TICKERS:
    if t not in price_data:
        continue
    p = price_data[t]
    signal = np.full(N, False)
    sma_v = np.full(N, np.nan)
    for i in range(N):
        if i >= SMA_EXIT:
            window = p[i - SMA_EXIT + 1:i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) >= SMA_EXIT * 0.8:
                sma_v[i] = np.mean(valid)
                signal[i] = p[i] > sma_v[i]
    sma_exit_above[t] = signal[1:]
    sma50_values[t] = sma_v
print(f"  SMA{SMA_EXIT} exit signal computed for {len(ALL_TICKERS)} tickers")

# ═══════════════════════════════════════════════════════════════════
# 4. SELECTION + SMA200 EXPOSURE SCALING
# ═══════════════════════════════════════════════════════════════════
print("\nComputing selections + SMA200 filter...")

selections = {block: [] for block in UNIVERSES}
exposure_scale = {block: [] for block in UNIVERSES}
current_sel = {}

for block, candidates in UNIVERSES.items():
    ns = min(N_SELECT, len(candidates))
    defaults = [t for t in candidates if t not in LATE_JOINERS and t in ret][:ns]
    if len(defaults) < ns:
        defaults = [t for t in candidates if t in ret][:ns]
    current_sel[block] = defaults

selection_log = []

for i in range(N_ret):
    d = dates_ret[i]
    is_rebal = (i == 0) or (d.month != dates_ret[i - 1].month)

    if is_rebal:
        log_entry = {"date": d}
        for block, candidates in UNIVERSES.items():
            ns = min(N_SELECT, len(candidates))
            scores = {t: mom[t][i] if t in mom else np.nan for t in candidates}
            valid_candidates = [(t, scores[t]) for t in candidates if not np.isnan(scores[t])]
            valid_candidates.sort(key=lambda x: -x[1])

            if len(valid_candidates) >= ns:
                current_sel[block] = [t for t, _ in valid_candidates[:ns]]
            elif len(valid_candidates) > 0:
                current_sel[block] = [t for t, _ in valid_candidates]

            log_entry[block] = {"picks": list(current_sel[block]), "scores": scores}
        selection_log.append(log_entry)

    for block in UNIVERSES:
        selections[block].append(list(current_sel[block]))
        picks_b = current_sel[block]
        above_b = sum(1 for t in picks_b if t in sma200_above and sma200_above[t][i])
        sc = above_b / max(len(picks_b), 1)
        sc = max(sc, MIN_EXPOSURE)
        exposure_scale[block].append(sc)

# ═══════════════════════════════════════════════════════════════════
# 5. STRATEGY CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════
print("\nConstructing strategies...")

shy_ucits = "IBTS.L" if "IBTS.L" in ret else CORE_TICKERS[0]
shy_ret = ret[shy_ucits]
print(f"  Cash destination: {shy_ucits} (UCITS SHY equivalent)")

def dynamic_block_returns_dual_sma(block_name, use_sma_filter=True, use_exit_signal=False):
    """Equal-weight average of top-N selected, with SMA200 exposure scaling.
    If use_exit_signal=True, assets below SMA50 exit to IBTS.L with TX_COST_BPS per switch."""
    r = np.zeros(N_ret)
    exit_count = 0
    switch_count = 0
    prev_exited = {}

    for i in range(N_ret):
        picks = selections[block_name][i]
        shy_r = shy_ret[i] if not np.isnan(shy_ret[i]) else 0.0
        is_rebal = (i == 0) or (dates_ret[i].month != dates_ret[i - 1].month)

        if is_rebal:
            prev_exited = {}

        if use_exit_signal:
            valid = []
            for t in picks:
                if t not in ret or np.isnan(ret[t][i]):
                    continue
                t_ret = ret[t][i]
                was_exited = prev_exited.get(t, False)
                is_below = (t in sma_exit_above and not sma_exit_above[t][i])

                if is_below:
                    asset_ret = shy_r
                    if not was_exited:
                        switch_count += 1
                        asset_ret -= TX_COST_BPS / 10000
                    prev_exited[t] = True
                    exit_count += 1
                else:
                    asset_ret = t_ret
                    if was_exited:
                        switch_count += 1
                        asset_ret -= TX_COST_BPS / 10000
                    prev_exited[t] = False
                valid.append(asset_ret)
            risk_ret = np.mean(valid) if valid else 0.0
        else:
            valid = [ret[t][i] for t in picks if t in ret and not np.isnan(ret[t][i])]
            risk_ret = np.mean(valid) if valid else 0.0

        if use_sma_filter:
            sc = exposure_scale[block_name][i]
            r[i] = sc * risk_ret + (1 - sc) * shy_r
        else:
            r[i] = risk_ret
    return r, exit_count, switch_count

ret_equity_sma, eq_exits, eq_sw = dynamic_block_returns_dual_sma("Equity", True, "Equity" in EXIT_BLOCKS)
ret_bonds, _, _ = dynamic_block_returns_dual_sma("Bonds", True, "Bonds" in EXIT_BLOCKS)
ret_hard_sma, ha_exits, ha_sw = dynamic_block_returns_dual_sma("HardAssets", True, "HardAssets" in EXIT_BLOCKS)
total_switches = eq_sw + ha_sw
print(f"  SMA{SMA_EXIT} exits: Equity={eq_exits}, HardAssets={ha_exits}, switches={total_switches}")

# Long Vol: JPM Absolute Alpha (no SMA filter — market neutral fund)
longvol_ticker = UNIVERSES["LongVol"][0] if UNIVERSES["LongVol"] else None
if longvol_ticker and longvol_ticker in ret:
    ret_longvol = ret[longvol_ticker].copy() * LONGVOL_LEVERAGE
    longvol_source = longvol_ticker
    print(f"  + Long Volatility (JPM Abs Alpha {longvol_ticker} x{LONGVOL_LEVERAGE:.2f}): {N_ret} days")
else:
    spy_ucits = "CSPX.L" if "CSPX.L" in ret else CORE_TICKERS[0]
    ret_longvol = -ret[spy_ucits] * 0.3
    longvol_source = f"Synthetic (inv. {spy_ucits} x0.3)"
    print(f"  + Long Volatility (SYNTHETIC — no JPM fund data): {N_ret} days")

# Commodity Trend with SMA200 gate + SMA50 deviation sizing
cmdty_ticker = UNIVERSES["Commodities"][0] if UNIVERSES["Commodities"] else None
if cmdty_ticker and cmdty_ticker in ret:
    cmdty_prices = price_data[cmdty_ticker]
    ret_cmdty_trend = np.zeros(N_ret)
    for i in range(N_ret):
        day_idx = i + 1
        if day_idx >= SMA_LONG:
            sma200_cmdty = np.mean(cmdty_prices[day_idx - SMA_LONG:day_idx])
            if cmdty_prices[day_idx] < sma200_cmdty:
                ret_cmdty_trend[i] = 0.0
                continue
        if day_idx >= SMA_CMDTY:
            sma50_cmdty = np.mean(cmdty_prices[day_idx - SMA_CMDTY:day_idx])
            deviation = (cmdty_prices[day_idx] / sma50_cmdty) - 1
            if deviation > 0:
                weight = min(deviation / 0.05, 1.0)
                ret_cmdty_trend[i] = ret[cmdty_ticker][i] * weight
            else:
                ret_cmdty_trend[i] = 0.0
        else:
            ret_cmdty_trend[i] = ret[cmdty_ticker][i] * 0.5
    print(f"  + Commodity Trend (SMA200 gate + SMA{SMA_CMDTY} deviation on {cmdty_ticker}): {N_ret} days")
else:
    ret_cmdty_trend = np.zeros(N_ret)
    print(f"  + Commodity Trend: NO DATA — flat returns")

# ═══════════════════════════════════════════════════════════════════
# 6. PORTFOLIO CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════
print("\nBuilding portfolio...")

comp_ret_sma = {
    "Equity": ret_equity_sma, "Bonds": ret_bonds, "HardAssets": ret_hard_sma,
    "LongVol": ret_longvol, "CmdtyTrend": ret_cmdty_trend,
}

def monthly_rebal_portfolio(weights_dict, comp_returns, dates_list):
    comps = list(weights_dict.keys())
    n = len(dates_list)
    target_w = np.array([weights_dict[c] for c in comps])
    alloc = target_w.copy()
    port_ret = np.zeros(n)
    for i in range(n):
        if i == 0 or dates_list[i].month != dates_list[i - 1].month:
            total_val = alloc.sum()
            alloc = target_w * total_val
        total_before = alloc.sum()
        for j, c in enumerate(comps):
            alloc[j] *= (1 + comp_returns[c][i])
        total_after = alloc.sum()
        port_ret[i] = (total_after / total_before) - 1 if total_before > 0 else 0
    return port_ret

dragon_ret = monthly_rebal_portfolio(W_DRAGON, comp_ret_sma, dates_ret)

spy_ucits = "CSPX.L" if "CSPX.L" in ret else CORE_TICKERS[0]
tlt_ucits = "IDTL.L" if "IDTL.L" in ret else (UNIVERSES["Bonds"][-1] if UNIVERSES["Bonds"] else CORE_TICKERS[0])
comp_ret_6040 = {"Equity": ret[spy_ucits], "Bonds": ret[tlt_ucits]}
port_6040_ret = monthly_rebal_portfolio(W_6040, comp_ret_6040, dates_ret)
spy_ret_arr = ret[spy_ucits]

# ═══════════════════════════════════════════════════════════════════
# 7. FILTER TO LIVE YEAR
# ═══════════════════════════════════════════════════════════════════
print(f"\nFiltering to {LIVE_YEAR}...")

ytd_start = None
for i, d in enumerate(dates_ret):
    if d.year == LIVE_YEAR:
        ytd_start = i
        break

if ytd_start is None:
    print(f"  ! No {LIVE_YEAR} data found. Exiting.")
    exit(1)

dates_live = dates_ret[ytd_start:]
dragon_live = dragon_ret[ytd_start:]
N_live = len(dates_live)

# NAV from $10,000
nav_live = np.full(N_live + 1, float(INITIAL_CAPITAL))
for i in range(N_live):
    nav_live[i + 1] = nav_live[i] * (1 + dragon_live[i])

# Today's return
today_ret = dragon_live[-1] * 100
today_dollar = nav_live[-1] - nav_live[-2]

# MTD
mtd_start = None
for i, d in enumerate(dates_live):
    if d.month == dates_live[-1].month and d.year == dates_live[-1].year:
        mtd_start = i
        break
if mtd_start is not None:
    mtd_ret = (np.prod(1 + dragon_live[mtd_start:]) - 1) * 100
    mtd_dollar = nav_live[-1] - nav_live[mtd_start]
else:
    mtd_ret, mtd_dollar = 0.0, 0.0

# YTD
ytd_ret = (nav_live[-1] / nav_live[0] - 1) * 100
ytd_dollar = nav_live[-1] - nav_live[0]

# Max Drawdown (live period)
peak = np.maximum.accumulate(nav_live[1:])
dd = (nav_live[1:] - peak) / peak * 100
max_dd = float(np.min(dd))
curr_dd = float(dd[-1])

# ─── Benchmark NAVs for live period ───
spy_live = spy_ret_arr[ytd_start:]
nav_spy = np.full(N_live + 1, float(INITIAL_CAPITAL))
for i in range(N_live):
    nav_spy[i + 1] = nav_spy[i] * (1 + spy_live[i])

p6040_live = port_6040_ret[ytd_start:]
nav_6040 = np.full(N_live + 1, float(INITIAL_CAPITAL))
for i in range(N_live):
    nav_6040[i + 1] = nav_6040[i] * (1 + p6040_live[i])

def live_metrics(ret_arr, nav_arr):
    ytd_r = (nav_arr[-1] / nav_arr[0] - 1) * 100
    vol = float(np.std(ret_arr) * np.sqrt(252) * 100)
    rf_d = RF_ANNUAL / 252
    excess = ret_arr - rf_d
    sharpe = float(np.mean(excess) / np.std(excess) * np.sqrt(252)) if np.std(excess) > 0 else 0
    down = excess[excess < 0]
    sortino = float(np.mean(excess) / np.std(down) * np.sqrt(252)) if len(down) > 0 and np.std(down) > 0 else 0
    pk = np.maximum.accumulate(nav_arr)
    mdd_val = float(np.min((nav_arr - pk) / pk) * 100)
    return {"ytd": ytd_r, "vol": vol, "sharpe": sharpe, "sortino": sortino, "mdd": mdd_val, "nav": float(nav_arr[-1])}

metrics_dragon = live_metrics(dragon_live, nav_live)
metrics_spy = live_metrics(spy_live, nav_spy)
metrics_6040 = live_metrics(p6040_live, nav_6040)

# Commodity trend exposure
cmdty_above_sma = False
if cmdty_ticker and cmdty_ticker in price_data and N >= SMA_LONG:
    sma200_cmdty_val = np.mean(cmdty_prices[N - SMA_LONG:N])
    cmdty_above_sma = cmdty_prices[N - 1] > sma200_cmdty_val
cmdty_exp = 1.0 if cmdty_above_sma else 0.0

# Current exposures
curr_exp = {}
for block in UNIVERSES:
    curr_exp[block] = exposure_scale[block][-1]
curr_exp["Commodities"] = cmdty_exp

# Cash percentage
cash_pct = 0.0
for block, w in W_DRAGON.items():
    bkey = block if block != "CmdtyTrend" else "Commodities"
    if bkey in curr_exp:
        cash_pct += w * (1 - curr_exp[bkey])

# Position count
pos_tickers = set()
last_rebal = selection_log[-1]
for block in UNIVERSES:
    pos_tickers.update(last_rebal[block]["picks"])
n_positions = len(pos_tickers)

# SMA50 exit status
sma50_exit_status = {}
for block in EXIT_BLOCKS:
    for t in last_rebal[block]["picks"]:
        if t in sma_exit_above and len(sma_exit_above[t]) > 0:
            is_above = bool(sma_exit_above[t][-1])
            price = float(price_data[t][-1])
            sma50_val = float(sma50_values[t][-1]) if not np.isnan(sma50_values[t][-1]) else None
            pct_from_sma = (price / sma50_val - 1) * 100 if sma50_val else None
            sma50_exit_status[t] = {
                "exited": not is_above,
                "price": price,
                "sma50": sma50_val,
                "pct_from_sma": pct_from_sma,
                "block": block,
            }

# ─── Real weights & share counts ───
rebal_date = last_rebal["date"]
rebal_idx_live = None
for i, d in enumerate(dates_live):
    if d >= rebal_date:
        rebal_idx_live = i
        break
if rebal_idx_live is None:
    rebal_idx_live = 0

nav_at_rebal = nav_live[rebal_idx_live]
nav_now = nav_live[-1]

rebal_idx_ret = None
for i, d in enumerate(dates_ret):
    if d >= rebal_date:
        rebal_idx_ret = i
        break
if rebal_idx_ret is None:
    rebal_idx_ret = len(dates_ret) - 1

shares_map = {}
target_alloc = {}
real_weight = {}
cash_residual = 0.0
shy_from_exits = 0.0
shy_price_now = float(price_data[shy_ucits][-1])
shy_price_rebal = float(price_data[shy_ucits][rebal_idx_ret + 1])

for block in UNIVERSES:
    picks = last_rebal[block]["picks"]
    bw = W_DRAGON.get(block, W_DRAGON.get("CmdtyTrend", 0.18))
    exp = curr_exp.get(block, 1.0)
    target_w = bw * exp / len(picks) if picks else 0
    for t in picks:
        is_exited = t in sma50_exit_status and sma50_exit_status[t]["exited"]
        price_at_rebal = price_data[t][rebal_idx_ret + 1]
        price_now = price_data[t][-1]
        alloc_dollars = nav_at_rebal * target_w
        target_alloc[t] = alloc_dollars

        if is_exited:
            shares_map[t] = 0
            real_weight[t] = 0.0
            shy_from_exits += alloc_dollars * (shy_price_now / shy_price_rebal) if shy_price_rebal > 0 else alloc_dollars
        elif not np.isnan(price_at_rebal) and price_at_rebal > 0:
            frac_shares = alloc_dollars / price_at_rebal
            n_shares = math.floor(frac_shares)
            cash_residual += (frac_shares - n_shares) * price_at_rebal
            shares_map[t] = n_shares
            if nav_now > 0 and not np.isnan(price_now):
                real_weight[t] = (n_shares * price_now / nav_now) * 100
            else:
                real_weight[t] = target_w * 100
        else:
            shares_map[t] = 0
            real_weight[t] = target_w * 100

# SHY from SMA200 de-risking
shy_from_sma200 = 0.0
for block, w in W_DRAGON.items():
    bkey = block if block != "CmdtyTrend" else "Commodities"
    if bkey in curr_exp:
        unexposed = 1 - curr_exp[bkey]
        if unexposed > 0:
            shy_from_sma200 += nav_at_rebal * w * unexposed * (shy_price_now / shy_price_rebal) if shy_price_rebal > 0 else 0

total_shy_dollars = shy_from_exits + shy_from_sma200 + cash_residual
shy_shares_total = math.floor(total_shy_dollars / shy_price_now) if shy_price_now > 0 else 0
total_shy_dollars = shy_shares_total * shy_price_now
cash_residual_final = (shy_from_exits + shy_from_sma200 + cash_residual) - total_shy_dollars
shy_real_weight = (total_shy_dollars / nav_now * 100) if nav_now > 0 else 0

# Detect NEW exits/entries today
sma50_new_exits = []
sma50_new_entries = []
for t, status in sma50_exit_status.items():
    if len(sma_exit_above[t]) >= 2:
        was_above = bool(sma_exit_above[t][-2])
        is_above = bool(sma_exit_above[t][-1])
        if was_above and not is_above:
            sma50_new_exits.append(t)
        elif not was_above and is_above:
            sma50_new_entries.append(t)

# Watch list
sma50_watch = []
for t, s in sma50_exit_status.items():
    if s["pct_from_sma"] is not None and abs(s["pct_from_sma"]) < 2.0 and not s["exited"]:
        sma50_watch.append((t, s["pct_from_sma"], s["block"]))
sma50_watch.sort(key=lambda x: abs(x[1]))

exited_tickers = [t for t, s in sma50_exit_status.items() if s["exited"]]
print(f"  SMA50: {len(exited_tickers)} exited, {len(sma50_new_exits)} new exits, {len(sma50_new_entries)} new entries, {len(sma50_watch)} on watch")

# Next rebalancing
import calendar
last_day = calendar.monthrange(TODAY.year, TODAY.month)[1]
next_rebal = datetime.date(TODAY.year, TODAY.month, last_day)
days_to_rebal = (next_rebal - TODAY).days

# Rebalancing history
rebal_history = [e for e in selection_log if e["date"].year == LIVE_YEAR]
prev_rebal = rebal_history[-2] if len(rebal_history) >= 2 else None

# Per-asset YTD data
ytd_data = {}
for t in ALL_TICKERS:
    if t not in price_data:
        ytd_data[t] = np.full(N_live, np.nan)
        continue
    p = price_data[t]
    p0_idx = ytd_start + 1
    p0 = p[p0_idx - 1]
    if not np.isnan(p0) and p0 > 0:
        segment = p[p0_idx:p0_idx + N_live]
        if len(segment) == N_live:
            ytd_data[t] = (segment / p0 - 1) * 100
        else:
            ytd_data[t] = np.full(N_live, np.nan)
    else:
        ytd_data[t] = np.full(N_live, np.nan)

ytd_dragon_pct = (nav_live[1:] / nav_live[0] - 1) * 100

print(f"  {LIVE_YEAR}: {N_live} trading days")
print(f"  NAV: ${nav_live[-1]:,.2f} | YTD: {ytd_ret:+.2f}% | Today: {today_ret:+.2f}%")

# ─── ALERT MODE EARLY EXIT ───
if ALERT_MODE:
    print("\nPre-close alert mode — building status email...")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if gmail_pass:
        if sma50_new_exits:
            subject = f"PRE-CLOSE — SELL {', '.join(sma50_new_exits)} MOC | Iberic Centinela"
        elif sma50_new_entries:
            subject = f"PRE-CLOSE — BUY {', '.join(sma50_new_entries)} MOC | Iberic Centinela"
        elif sma50_watch:
            subject = f"PRE-CLOSE WATCH — Assets near SMA50 | Iberic Centinela"
        else:
            subject = f"Pre-Close — No signals | NAV ${nav_live[-1]:,.0f} | YTD {ytd_ret:+.1f}%"

        signal_html = ""
        if sma50_new_exits:
            for t in sma50_new_exits:
                s = sma50_exit_status[t]
                label = TICKER_LABELS.get(t, t)
                signal_html += f'<div style="background:rgba(239,68,68,0.12);padding:10px;border-radius:6px;margin-bottom:8px;border-left:3px solid #ef4444"><b style="color:#ef4444">EXIT — SELL {t} ({label}) → {shy_ucits}</b> | ${s["price"]:.2f} | SMA50: ${s["sma50"]:.2f}</div>'
        if sma50_new_entries:
            for t in sma50_new_entries:
                s = sma50_exit_status[t]
                label = TICKER_LABELS.get(t, t)
                signal_html += f'<div style="background:rgba(16,185,129,0.12);padding:10px;border-radius:6px;margin-bottom:8px;border-left:3px solid #10b981"><b style="color:#10b981">RE-ENTRY — BUY {t} ({label}) ← {shy_ucits}</b> | ${s["price"]:.2f} | SMA50: ${s["sma50"]:.2f}</div>'
        if sma50_watch:
            for t, pct, block in sma50_watch:
                label = TICKER_LABELS.get(t, t)
                signal_html += f'<div style="background:rgba(245,158,11,0.12);padding:10px;border-radius:6px;margin-bottom:8px;border-left:3px solid #f59e0b"><b style="color:#f59e0b">WATCH — {t} ({label})</b> — {pct:+.1f}% from SMA50</div>'
        if exited_tickers:
            exited_list = ", ".join(exited_tickers)
            signal_html += f'<div style="background:rgba(100,116,139,0.12);padding:10px;border-radius:6px;margin-bottom:8px;border-left:3px solid #64748b"><span style="color:#94a3b8">Positions in {shy_ucits}:</span> <b>{exited_list}</b></div>'
        if not signal_html:
            signal_html = f'<div style="background:rgba(16,185,129,0.12);padding:10px;border-radius:6px;margin-bottom:8px;border-left:3px solid #10b981"><span style="color:#10b981">All clear — no SMA50 signals</span></div>'

        today_color = "#10b981" if today_ret >= 0 else "#ef4444"
        ytd_color = "#10b981" if ytd_ret >= 0 else "#ef4444"
        mtd_color = "#10b981" if mtd_ret >= 0 else "#ef4444"

        html_body = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#1e293b;color:#e2e8f0;padding:24px;border-radius:8px">
            <h2 style="color:#f59e0b;margin-bottom:4px">Iberic Centinela — Pre-Close Status</h2>
            <p style="font-size:11px;color:#64748b;margin-bottom:16px">{TODAY.strftime("%Y-%m-%d")} — Based on European market close prices</p>
            <div style="background:#0f172a;padding:16px;border-radius:8px;margin-bottom:16px">
                <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                    <span style="color:#94a3b8">NAV</span>
                    <b style="font-size:18px">${nav_live[-1]:,.2f}</b>
                </div>
                <table style="width:100%;font-size:13px;color:#e2e8f0">
                    <tr><td>Today</td><td style="text-align:right;color:{today_color};font-weight:bold">{today_ret:+.2f}%</td></tr>
                    <tr><td>MTD</td><td style="text-align:right;color:{mtd_color};font-weight:bold">{mtd_ret:+.2f}%</td></tr>
                    <tr><td>YTD</td><td style="text-align:right;color:{ytd_color};font-weight:bold">{ytd_ret:+.2f}%</td></tr>
                </table>
            </div>
            <h3 style="color:#94a3b8;font-size:11px;text-transform:uppercase;margin-bottom:8px">SMA50 Signals</h3>
            {signal_html}
            <p style="margin-top:16px;font-size:10px;color:#475569">Based on European market close prices. Final confirmation at post-close email.</p>
            <p style="font-size:10px;color:#475569">SFinance-alicIA | Iberic Centinela Dual SMA</p>
        </div>"""

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_SENDER, gmail_pass)
                for rcpt in EMAIL_RECIPIENTS:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"] = subject
                    msg["From"] = GMAIL_SENDER
                    msg["To"] = rcpt
                    msg.attach(MIMEText(html_body, "html"))
                    server.sendmail(GMAIL_SENDER, [rcpt], msg.as_string())
            print(f"  Pre-close email sent to {len(EMAIL_RECIPIENTS)} recipients (individual BCC)")
        except Exception as e:
            print(f"  ! Email error: {e}")
    else:
        print("  ! GMAIL_APP_PASSWORD not set — skipping pre-close email")
    print("\n=== Alert Mode Done ===")
    exit(0)

# ═══════════════════════════════════════════════════════════════════
# 8. SVG CHARTS
# ═══════════════════════════════════════════════════════════════════
print("\nGenerating charts...")

def build_nav_chart():
    vw, vh = 900, 340
    ml, mr, mt, mb = 60, 20, 15, 40
    pw, ph = vw - ml - mr, vh - mt - mb
    n_pts = len(nav_live)
    y_min = float(np.min(nav_live))
    y_max = float(np.max(nav_live))
    pad = (y_max - y_min) * 0.06
    y_min -= pad; y_max += pad
    y_range = y_max - y_min if y_max > y_min else 1
    svg = ""
    step = 100 if (y_max - y_min) < 800 else 200 if (y_max - y_min) < 2000 else 500
    pct = int(math.floor(y_min / step)) * step
    while pct <= y_max + step:
        yp = mt + ph - ((pct - y_min) / y_range) * ph
        if mt - 5 <= yp <= vh - mb + 5:
            svg += f'<line x1="{ml}" y1="{yp:.0f}" x2="{vw-mr}" y2="{yp:.0f}" stroke="#334155" stroke-width="0.5"/>'
            svg += f'<text x="{ml-8}" y="{yp:.0f}" text-anchor="end" fill="#94a3b8" font-size="10" dominant-baseline="middle">${pct:,.0f}</text>'
        pct += step
    dates_nav = [dates_live[0]] + list(dates_live)
    seen = set()
    for i, d in enumerate(dates_nav):
        week = d.isocalendar()[1]
        key = (d.year, week)
        if key not in seen and i > 0:
            seen.add(key)
            x = ml + (i / max(n_pts - 1, 1)) * pw
            if len(seen) % 2 == 0:
                svg += f'<text x="{x:.0f}" y="{vh-8}" text-anchor="middle" fill="#64748b" font-size="9">{d.strftime("%Y-%m-%d")}</text>'
                svg += f'<line x1="{x:.0f}" y1="{mt}" x2="{x:.0f}" y2="{vh-mb}" stroke="#1e293b" stroke-width="0.5"/>'
    pts_line = []
    for i in range(n_pts):
        x = ml + (i / max(n_pts - 1, 1)) * pw
        y = mt + ph - ((nav_live[i] - y_min) / y_range) * ph
        pts_line.append(f"{x:.1f},{y:.1f}")
    bottom_y = mt + ph
    svg += f'<polygon points="{ml},{bottom_y} {" ".join(pts_line)} {ml + pw},{bottom_y}" fill="url(#grad)" opacity="0.3"/>'
    svg += f'<polyline points="{" ".join(pts_line)}" fill="none" stroke="#06b6d4" stroke-width="2" stroke-linejoin="round"/>'
    grad = f'<defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#06b6d4" stop-opacity="0.4"/><stop offset="100%" stop-color="#06b6d4" stop-opacity="0.02"/></linearGradient></defs>'
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{grad}{svg}</svg>'

def build_donut():
    cx, cy, r, ri = 120, 120, 100, 60
    comps = [("CmdtyTrend",0.18,BLOCK_COLORS["CmdtyTrend"]),("Equity",0.24,BLOCK_COLORS["Equity"]),
             ("HardAssets",0.19,BLOCK_COLORS["HardAssets"]),("Bonds",0.18,BLOCK_COLORS["Bonds"]),
             ("LongVol",0.21,BLOCK_COLORS["LongVol"])]
    svg = ""; angle = -90
    for name, w, color in comps:
        sweep = w * 360
        a1, a2 = math.radians(angle), math.radians(angle + sweep)
        x1o,y1o = cx+r*math.cos(a1), cy+r*math.sin(a1)
        x2o,y2o = cx+r*math.cos(a2), cy+r*math.sin(a2)
        x1i,y1i = cx+ri*math.cos(a2), cy+ri*math.sin(a2)
        x2i,y2i = cx+ri*math.cos(a1), cy+ri*math.sin(a1)
        lg = 1 if sweep > 180 else 0
        svg += f'<path d="M {x1o:.1f},{y1o:.1f} A {r},{r} 0 {lg},1 {x2o:.1f},{y2o:.1f} L {x1i:.1f},{y1i:.1f} A {ri},{ri} 0 {lg},0 {x2i:.1f},{y2i:.1f} Z" fill="{color}" stroke="#0f172a" stroke-width="2"/>'
        angle += sweep
    legend_y = cy + r + 25
    legend_items = comps
    lx = 10
    for name, w, color in legend_items:
        label = BLOCK_LABELS.get(name, name)
        svg += f'<circle cx="{lx}" cy="{legend_y}" r="5" fill="{color}"/>'
        svg += f'<text x="{lx+10}" y="{legend_y+4}" fill="#94a3b8" font-size="10">{label}</text>'
        lx += len(label) * 7 + 30
    return f'<svg viewBox="0 0 240 {legend_y + 20}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

def build_ytd_chart():
    if not ytd_data or N_live < 2:
        return ''
    vw, vh = 900, 420
    ml, mr, mt, mb = 50, 100, 15, 30
    pw, ph = vw - ml - mr, vh - mt - mb
    n_pts = N_live
    all_vals = [0.0]
    for t in ytd_data:
        valid = ytd_data[t][~np.isnan(ytd_data[t])]
        if len(valid) > 0:
            all_vals.extend([float(np.min(valid)), float(np.max(valid))])
    if len(ytd_dragon_pct) > 0:
        all_vals.extend([float(np.min(ytd_dragon_pct)), float(np.max(ytd_dragon_pct))])
    y_min_raw, y_max_raw = min(all_vals), max(all_vals)
    pad = max(abs(y_max_raw - y_min_raw) * 0.08, 2)
    y_min, y_max = y_min_raw - pad, y_max_raw + pad
    y_range = y_max - y_min if y_max > y_min else 1
    svg = ""
    span = y_max_raw - y_min_raw
    step = 2 if span < 15 else 5 if span < 40 else 10 if span < 80 else 20
    pct = int(math.floor(y_min / step)) * step
    while pct <= y_max + step:
        yp = mt + ph - ((pct - y_min) / y_range) * ph
        if yp < mt - 5 or yp > vh - mb + 5:
            pct += step; continue
        w_s, op = ("1", "0.4") if pct == 0 else ("0.5", "0.15")
        svg += f'<line x1="{ml}" y1="{yp:.0f}" x2="{vw-mr}" y2="{yp:.0f}" stroke="rgba(148,163,184,{op})" stroke-width="{w_s}"/>'
        svg += f'<text x="{ml-5}" y="{yp:.0f}" text-anchor="end" fill="#94a3b8" font-size="9" dominant-baseline="middle">{pct:+.0f}%</text>'
        pct += step
    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    seen = set()
    for i, d in enumerate(dates_live):
        if d.month not in seen:
            seen.add(d.month)
            x = ml + (i / max(n_pts - 1, 1)) * pw
            svg += f'<text x="{x:.0f}" y="{vh-5}" text-anchor="middle" fill="#94a3b8" font-size="9">{month_names[d.month-1]}</text>'
    end_vals = {}
    for t in ALL_TICKERS:
        if t not in ytd_data: continue
        vals = ytd_data[t]
        for v in reversed(vals):
            if not np.isnan(v):
                end_vals[t] = float(v); break
    for t in end_vals:
        vals = ytd_data[t]
        color = TICKER_COLORS.get(t, "#9ca3af")
        pts = []
        for i in range(len(vals)):
            if not np.isnan(vals[i]):
                x = ml + (i / max(n_pts - 1, 1)) * pw
                y = mt + ph - ((vals[i] - y_min) / y_range) * ph
                pts.append(f"{x:.1f},{y:.1f}")
        if pts:
            svg += f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.3" stroke-linejoin="round" opacity="0.5"/>'
    if len(ytd_dragon_pct) > 0:
        pts = []
        for i in range(len(ytd_dragon_pct)):
            x = ml + (i / max(n_pts - 1, 1)) * pw
            y = mt + ph - ((ytd_dragon_pct[i] - y_min) / y_range) * ph
            pts.append(f"{x:.1f},{y:.1f}")
        svg += f'<polyline points="{" ".join(pts)}" fill="none" stroke="#06b6d4" stroke-width="2.5" stroke-linejoin="round"/>'
    all_labels = [(t, end_vals[t], TICKER_COLORS.get(t, "#9ca3af"), "8", "500") for t in end_vals]
    if len(ytd_dragon_pct) > 0:
        all_labels.append(("Iberic Centinela", float(ytd_dragon_pct[-1]), "#06b6d4", "9", "700"))
    all_labels.sort(key=lambda x: -x[1])
    placed = []
    for name, val, color, fsize, fweight in all_labels:
        target_y = mt + ph - ((val - y_min) / y_range) * ph
        final_y = target_y
        for py in placed:
            if abs(final_y - py) < 10: final_y = py + 10
        placed.append(final_y)
        svg += f'<text x="{vw-mr+5}" y="{final_y:.0f}" fill="{color}" font-size="{fsize}" font-weight="{fweight}" dominant-baseline="middle">{name} {val:+.1f}%</text>'
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

# ═══════════════════════════════════════════════════════════════════
# 9. HTML HELPERS
# ═══════════════════════════════════════════════════════════════════
def pct_cls(v):
    return "pos" if v > 0 else "neg" if v < 0 else ""

def fmt_dollar(v):
    return f"${abs(v):,.0f}" if abs(v) >= 1 else f"${abs(v):,.2f}"

def signal_blocks_html():
    blocks = [
        ("Equity", "24%", BLOCK_COLORS["Equity"], True),
        ("Bonds", "18%", BLOCK_COLORS["Bonds"], False),
        ("HardAssets", "19%", BLOCK_COLORS["HardAssets"], True),
        ("LongVol", "21%", BLOCK_COLORS["LongVol"], False),
        ("Commodities", "18%", BLOCK_COLORS["CmdtyTrend"], False),
    ]
    html = '<div class="signal-grid">'
    for block, wpct, color, has_sma in blocks:
        picks = last_rebal[block]["picks"]
        scores = last_rebal[block]["scores"]
        bkey = block if block != "Commodities" else "Commodities"
        exp = curr_exp.get(bkey, 1.0)
        label = BLOCK_LABELS.get(block, block)
        html += f'<div class="signal-card" style="border-left:3px solid {color}">'
        html += f'<div class="signal-header"><span style="color:{color};font-weight:700">{label}</span> <span class="signal-wpct">({wpct})</span></div>'
        for t in picks:
            s = scores.get(t, np.nan)
            s_txt = f"{s*100:.1f}%" if not np.isnan(s) else "—"
            tlabel = TICKER_LABELS.get(t, t)
            html += f'<div class="signal-row"><span class="signal-ticker" style="color:{color}">{t}</span> <span class="signal-name">{tlabel}</span> <span class="signal-mom">{s_txt}</span></div>'
        exp_txt = f"{exp:.0%}" if block not in ["LongVol"] else ""
        if exp_txt:
            html += f'<div class="signal-exp">Exposure {exp_txt}</div>'
        html += '</div>'
    html += '</div>'
    return html

def positions_table_html():
    rows = ""
    weight_map = {}
    for block in UNIVERSES:
        picks = last_rebal[block]["picks"]
        bw = W_DRAGON.get(block, W_DRAGON.get("CmdtyTrend", 0.18))
        exp = curr_exp.get(block, 1.0)
        per_pick = (bw * exp / len(picks)) * 100 if picks else 0
        for t in picks:
            weight_map[t] = (per_pick, block)

    active = [(t, weight_map[t]) for t in weight_map if real_weight.get(t, 0) > 0]
    exited = [(t, weight_map[t]) for t in weight_map if real_weight.get(t, 0) == 0 and t in sma50_exit_status and sma50_exit_status[t]["exited"]]
    active.sort(key=lambda x: -real_weight.get(x[0], x[1][0]))

    for t, (per_pick, block) in active:
        color = TICKER_COLORS.get(t, "#6b7280")
        label = TICKER_LABELS.get(t, t)
        blabel = BLOCK_LABELS.get(block, block)
        price = price_data[t][-1]
        m = mom[t][-1] if t in mom and not np.isnan(mom[t][-1]) else 0
        above = sma200_above[t][-1] if t in sma200_above else False
        sma_icon = "▲" if above else "▼"
        sma_clr = "#10b981" if above else "#ef4444"
        pfmt = f"${price:.2f}"
        m_clr = "#10b981" if m >= 0 else "#ef4444"
        n_sh = shares_map.get(t, 0)
        rw = real_weight.get(t, per_pick)
        val = n_sh * price if not np.isnan(price) else 0
        drift = rw - per_pick
        drift_clr = "#f59e0b" if abs(drift) > 1.0 else "#94a3b8"
        sh_fmt = f"{int(n_sh)}"
        if t in sma50_exit_status:
            s50 = sma50_exit_status[t]
            s50_txt = "SALIDA" if s50["exited"] else "OK"
            s50_clr = "#ef4444" if s50["exited"] else "#10b981"
            s50_pct = f' ({s50["pct_from_sma"]:+.1f}%)' if s50["pct_from_sma"] is not None else ""
            row_bg = "background:rgba(239,68,68,0.1);" if s50["exited"] else ""
        else:
            s50_txt, s50_clr, s50_pct, row_bg = "—", "#9ca3af", "", ""
        rows += f'''<tr style="{row_bg}">
            <td style="color:{color};font-weight:700">{t}</td>
            <td>{label}</td>
            <td>{blabel}</td>
            <td class="num">{per_pick:.1f}%</td>
            <td class="num" style="color:{drift_clr};font-weight:700">{rw:.1f}%</td>
            <td class="num">{sh_fmt}</td>
            <td class="num">${val:,.0f}</td>
            <td class="num">{pfmt}</td>
            <td class="num" style="color:{m_clr}">{m*100:+.1f}%</td>
            <td class="num" style="color:{sma_clr}">{sma_icon}</td>
            <td class="num" style="color:{s50_clr};font-weight:700">{s50_txt}{s50_pct}</td>
        </tr>'''

    if total_shy_dollars > 0:
        shy_pfmt = f"${shy_price_now:.2f}"
        rows += f'''<tr style="background:rgba(148,163,184,0.08);border-top:2px solid #334155;">
            <td style="color:#94a3b8;font-weight:700">{shy_ucits}</td>
            <td>Cash (1-3Y)</td>
            <td>Protection</td>
            <td class="num">{cash_pct*100:.1f}%</td>
            <td class="num" style="color:#94a3b8;font-weight:700">{shy_real_weight:.1f}%</td>
            <td class="num">{int(shy_shares_total)}</td>
            <td class="num">${total_shy_dollars:,.0f}</td>
            <td class="num">{shy_pfmt}</td>
            <td class="num" style="color:#94a3b8">—</td>
            <td class="num" style="color:#94a3b8">—</td>
            <td class="num" style="color:#94a3b8">—</td>
        </tr>'''

    for t, (per_pick, block) in exited:
        color = TICKER_COLORS.get(t, "#6b7280")
        label = TICKER_LABELS.get(t, t)
        blabel = BLOCK_LABELS.get(block, block)
        price = price_data[t][-1]
        pfmt = f"${price:.2f}"
        s50 = sma50_exit_status[t]
        s50_pct = f'({s50["pct_from_sma"]:+.1f}%)' if s50["pct_from_sma"] is not None else ""
        rows += f'''<tr style="background:rgba(239,68,68,0.06);opacity:0.6;">
            <td style="color:{color};font-weight:700"><s>{t}</s></td>
            <td>{label} → {shy_ucits}</td>
            <td>{blabel}</td>
            <td class="num">{per_pick:.1f}%</td>
            <td class="num" style="color:#ef4444;font-weight:700">0.0%</td>
            <td class="num">0</td>
            <td class="num">$0</td>
            <td class="num">{pfmt}</td>
            <td class="num" style="color:#94a3b8">—</td>
            <td class="num" style="color:#94a3b8">—</td>
            <td class="num" style="color:#ef4444;font-weight:700">SALIDA {s50_pct}</td>
        </tr>'''

    return rows

def sma50_alert_html():
    html = ""
    if sma50_new_exits:
        html += '<div class="alert-banner alert-exit">'
        html += '<div class="alert-title">EXIT SIGNAL TODAY — Execute MOC</div>'
        for t in sma50_new_exits:
            s = sma50_exit_status[t]
            label = TICKER_LABELS.get(t, t)
            html += f'<div class="alert-action">SELL {t} ({label}) → {shy_ucits}'
            if s["pct_from_sma"] is not None:
                html += f' | Price: ${s["price"]:.2f} | SMA50: ${s["sma50"]:.2f} ({s["pct_from_sma"]:+.1f}%)'
            html += '</div>'
        html += '</div>'
    if sma50_new_entries:
        html += '<div class="alert-banner alert-entry">'
        html += '<div class="alert-title">RE-ENTRY SIGNAL TODAY — Execute MOC</div>'
        for t in sma50_new_entries:
            s = sma50_exit_status[t]
            label = TICKER_LABELS.get(t, t)
            html += f'<div class="alert-action">BUY {t} ({label}) ← {shy_ucits}'
            if s["pct_from_sma"] is not None:
                html += f' | Price: ${s["price"]:.2f} | SMA50: ${s["sma50"]:.2f} ({s["pct_from_sma"]:+.1f}%)'
            html += '</div>'
        html += '</div>'
    if sma50_watch:
        html += '<div class="alert-banner alert-watch">'
        html += '<div class="alert-title">SMA50 WATCH — Assets near threshold (&lt;2%)</div>'
        for t, pct, block in sma50_watch:
            label = TICKER_LABELS.get(t, t)
            html += f'<div class="alert-action">{t} ({label}) — {pct:+.1f}% from SMA50</div>'
        html += '</div>'
    if exited_tickers:
        html += '<div class="alert-banner alert-info">'
        html += f'<div class="alert-title">POSITIONS IN {shy_ucits} (SMA50 exits)</div>'
        for t in exited_tickers:
            s = sma50_exit_status[t]
            label = TICKER_LABELS.get(t, t)
            pct_txt = f'{s["pct_from_sma"]:+.1f}%' if s["pct_from_sma"] is not None else "—"
            html += f'<div class="alert-action">{t} ({label}) — {pct_txt} below SMA50</div>'
        html += '</div>'
    if not sma50_new_exits and not sma50_new_entries and not exited_tickers and not sma50_watch:
        html += '<div class="alert-banner alert-ok">'
        html += '<div class="alert-title">SMA50 — No active signals</div>'
        html += '<div class="alert-action">All assets above SMA50. No action required.</div>'
        html += '</div>'
    return html

def trade_blotter_html():
    is_rebal_day = len(dates_live) >= 2 and dates_live[-1].month != dates_live[-2].month
    has_signals = bool(sma50_new_exits or sma50_new_entries)
    orders = []

    for t in sma50_new_exits:
        price = float(price_data[t][-1])
        price_at_rb = float(price_data[t][rebal_idx_ret + 1])
        alloc = target_alloc.get(t, 0)
        n_sh = math.floor(alloc / price_at_rb) if price_at_rb > 0 else 0
        sell_amount = n_sh * price
        shy_buy = math.floor(sell_amount / shy_price_now) if shy_price_now > 0 else 0
        label = TICKER_LABELS.get(t, t)
        orders.append(("SELL", t, label, n_sh, price, sell_amount, f"SMA50 Exit → {shy_ucits}"))
        orders.append(("BUY", shy_ucits, "Cash 1-3Y", shy_buy, shy_price_now, shy_buy * shy_price_now, f"From {t} exit"))

    for t in sma50_new_entries:
        price = float(price_data[t][-1])
        target_w = 0
        for block in EXIT_BLOCKS:
            if t in last_rebal[block]["picks"]:
                bw = W_DRAGON.get(block, 0.19)
                exp = curr_exp.get(block, 1.0)
                target_w = bw * exp / len(last_rebal[block]["picks"])
                break
        alloc = nav_now * target_w if target_w else 0
        n_buy = math.floor(alloc / price) if price > 0 else 0
        shy_sell = math.ceil(alloc / shy_price_now) if shy_price_now > 0 else 0
        label = TICKER_LABELS.get(t, t)
        orders.append(("SELL", shy_ucits, "Cash 1-3Y", shy_sell, shy_price_now, shy_sell * shy_price_now, f"Funding for {t}"))
        orders.append(("BUY", t, label, n_buy, price, n_buy * price, f"SMA50 Re-entry"))

    if is_rebal_day and prev_rebal:
        for block in UNIVERSES:
            curr_picks = set(last_rebal[block]["picks"])
            prev_picks = set(prev_rebal[block]["picks"])
            exits = prev_picks - curr_picks
            entries = curr_picks - prev_picks
            bw = W_DRAGON.get(block, W_DRAGON.get("CmdtyTrend", 0.18))
            exp = curr_exp.get(block, 1.0)
            tw = bw * exp / len(last_rebal[block]["picks"]) if last_rebal[block]["picks"] else 0
            for t in exits:
                price = float(price_data[t][-1])
                label = TICKER_LABELS.get(t, t)
                prev_sh = shares_map.get(t, 0)
                orders.append(("SELL", t, label, prev_sh, price, prev_sh * price, f"Dropped from {BLOCK_LABELS.get(block, block)}"))
            for t in entries:
                price = float(price_data[t][-1])
                label = TICKER_LABELS.get(t, t)
                alloc = nav_now * tw
                n_buy = math.floor(alloc / price) if price > 0 else 0
                orders.append(("BUY", t, label, n_buy, price, n_buy * price, f"New pick in {BLOCK_LABELS.get(block, block)}"))

    if not orders:
        return f'''<div class="alert-banner alert-ok">
            <div class="alert-title">TRADE ORDERS</div>
            <div class="alert-action">No pending orders. Next review: {next_rebal.strftime("%Y-%m-%d")} (rebalance) or intraday SMA50 signal.</div>
        </div>'''

    html = '<div style="margin-bottom:20px">'
    html += '<div class="section-title">Trade Orders — MOC (Market on Close)</div>'
    html += '<table class="data-table">'
    html += '<tr><th>Action</th><th>Ticker</th><th>Name</th><th class="num">Shares</th><th class="num">Est. Price</th><th class="num">Est. Amount</th><th>Reason</th></tr>'
    for action, ticker, name, shares, price, amount, reason in orders:
        a_clr = "#ef4444" if action == "SELL" else "#10b981"
        a_bg = "rgba(239,68,68,0.08)" if action == "SELL" else "rgba(16,185,129,0.08)"
        t_clr = TICKER_COLORS.get(ticker, "#94a3b8")
        pfmt = f"${price:.2f}"
        sh_fmt = f"{int(shares)}"
        html += f'''<tr style="background:{a_bg}">
            <td style="color:{a_clr};font-weight:700">{action}</td>
            <td style="color:{t_clr};font-weight:700">{ticker}</td>
            <td>{name}</td>
            <td class="num">{sh_fmt}</td>
            <td class="num">{pfmt}</td>
            <td class="num">${amount:,.0f}</td>
            <td style="font-size:10px;color:#94a3b8">{reason}</td>
        </tr>'''
    html += '</table></div>'
    return html

def operational_status_html():
    is_rebal_day = len(dates_live) >= 2 and dates_live[-1].month != dates_live[-2].month
    has_signals = bool(sma50_new_exits or sma50_new_entries)

    if has_signals and is_rebal_day:
        status = "DOUBLE ACTION"
        status_clr = "#ef4444"
        msg = "SMA50 signals + Monthly rebalance. Execute ALL MOC orders before close."
        banner_cls = "alert-exit"
    elif has_signals:
        status = "ACTION REQUIRED"
        status_clr = "#ef4444"
        msg = "SMA50 signal detected. Execute MOC orders before close."
        banner_cls = "alert-exit"
    elif is_rebal_day:
        status = "REBALANCE"
        status_clr = "#f59e0b"
        msg = "First day of month. Execute monthly rotation MOC."
        banner_cls = "alert-watch"
    elif days_to_rebal <= 3:
        status = "PREPARE"
        status_clr = "#f59e0b"
        msg = f"Rebalance in {days_to_rebal} days ({next_rebal.strftime('%d/%m')}). Review liquidity and positions."
        banner_cls = "alert-watch"
    else:
        status = "MONITOR"
        status_clr = "#10b981"
        msg = "No action required. Monitor SMA50 watch list."
        banner_cls = "alert-ok"

    protocol = '''<div style="margin-top:8px;font-size:10px;color:#64748b;line-height:1.6">
        <strong style="color:#94a3b8">Protocol:</strong>
        LSE Close → Review dashboard (auto-update) |
        SMA50 signal → Execute MOC same day |
        Monthly rebal → Rotate on 1st of month MOC |
        Email → Pre-close alert if signal detected
    </div>'''

    return f'''<div class="alert-banner {banner_cls}" style="margin-bottom:16px">
        <div class="alert-title" style="display:flex;align-items:center;gap:8px">
            <span style="background:{status_clr};color:#0f172a;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:800">{status}</span>
            <span>{msg}</span>
        </div>
        {protocol}
    </div>'''

def benchmark_table_html():
    def row(label, d, s, b, higher_better=True, is_pct=True):
        vals = [d, s, b]
        best = max(vals) if higher_better else min(vals)
        suffix = "%" if is_pct else ""
        cells = ""
        for v in vals:
            bold = "font-weight:700;" if v == best else ""
            clr = "#10b981" if v == best else "#e2e8f0"
            cells += f'<td class="num" style="{bold}color:{clr}">{v:+.2f}{suffix}</td>'
        return f'<tr><td style="color:#94a3b8;font-weight:600">{label}</td>{cells}</tr>'

    return f'''<table class="data-table" style="max-width:600px">
        <tr><th></th><th class="num" style="color:#06b6d4">Iberic Centinela</th><th class="num" style="color:#3b82f6">{spy_ucits}</th><th class="num" style="color:#f59e0b">60/40</th></tr>
        {row("YTD", metrics_dragon["ytd"], metrics_spy["ytd"], metrics_6040["ytd"])}
        {row("Volatility", metrics_dragon["vol"], metrics_spy["vol"], metrics_6040["vol"], higher_better=False)}
        {row("Sharpe", metrics_dragon["sharpe"], metrics_spy["sharpe"], metrics_6040["sharpe"], is_pct=False)}
        {row("Sortino", metrics_dragon["sortino"], metrics_spy["sortino"], metrics_6040["sortino"], is_pct=False)}
        {row("Max Drawdown", metrics_dragon["mdd"], metrics_spy["mdd"], metrics_6040["mdd"])}
        <tr style="border-top:2px solid #334155">
            <td style="color:#94a3b8;font-weight:600">NAV ${INITIAL_CAPITAL:,}</td>
            <td class="num" style="font-weight:700;color:#06b6d4">${metrics_dragon["nav"]:,.2f}</td>
            <td class="num" style="color:#3b82f6">${metrics_spy["nav"]:,.2f}</td>
            <td class="num" style="color:#f59e0b">${metrics_6040["nav"]:,.2f}</td>
        </tr>
    </table>'''

def sma200_cards_html():
    html = ""
    for t in sorted(ALL_TICKERS):
        if t not in price_data:
            continue
        above = sma200_above[t][-1] if t in sma200_above else False
        label = TICKER_LABELS.get(t, t)
        color = TICKER_COLORS.get(t, "#6b7280")
        price = price_data[t][-1]
        sma_val = sma200_values[t][-1] if t in sma200_values else np.nan
        status = "ABOVE" if above else "BELOW"
        status_clr = "#10b981" if above else "#ef4444"
        icon = "▲" if above else "▼"
        pfmt = f"${price:,.2f}"
        sfmt = f"${sma_val:,.2f}" if not np.isnan(sma_val) else "—"
        html += f'''<div class="sma-card">
            <div class="sma-card-header">
                <span style="color:{color};font-weight:700;font-size:13px">{t}</span>
                <span style="color:{status_clr};font-weight:600;font-size:11px">{icon} {status}</span>
            </div>
            <div class="sma-card-name">{label}</div>
            <div class="sma-card-price">{pfmt}</div>
            <div class="sma-card-sma">SMA: {sfmt}</div>
        </div>'''
    return html

def rebal_history_html():
    rows = ""
    for entry in reversed(rebal_history):
        d = entry["date"]
        eq = ", ".join(entry["Equity"]["picks"])
        bo = ", ".join(entry["Bonds"]["picks"])
        ha = ", ".join(entry["HardAssets"]["picks"])
        eq_exp = 1.0
        ha_exp = 1.0
        cm_exp = 1.0
        for i, dd in enumerate(dates_ret):
            if dd >= d:
                eq_exp = exposure_scale["Equity"][i]
                ha_exp = exposure_scale["HardAssets"][i]
                day_idx = i + 1
                if cmdty_ticker and cmdty_ticker in price_data and day_idx >= SMA_LONG:
                    sma200_d = np.mean(cmdty_prices[day_idx - SMA_LONG:day_idx])
                    cm_exp = 1.0 if cmdty_prices[day_idx] > sma200_d else 0.0
                break
        rows += f'''<tr>
            <td>{d.strftime("%Y-%m-%d")}</td>
            <td style="color:{BLOCK_COLORS["Equity"]}">{eq}</td>
            <td style="color:{BLOCK_COLORS["Bonds"]}">{bo}</td>
            <td style="color:{BLOCK_COLORS["HardAssets"]}">{ha}</td>
            <td class="num">{eq_exp:.0%}</td>
            <td class="num">{ha_exp:.0%}</td>
            <td class="num">{cm_exp:.0%}</td>
        </tr>'''
    return rows

# ═══════════════════════════════════════════════════════════════════
# 10. HTML REPORT
# ═══════════════════════════════════════════════════════════════════
print("\nGenerating HTML report...")

today_sign = "+" if today_ret >= 0 else ""
mtd_sign = "+" if mtd_ret >= 0 else ""
ytd_sign = "+" if ytd_ret >= 0 else ""

longvol_label = TICKER_LABELS.get(longvol_ticker, "JPM AbsA") if longvol_ticker else "Synthetic"

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Iberic Centinela LIVE | SFinance-alicIA</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Inter', -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; }}
.container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}
.tabs {{ display:flex; gap:0; border-bottom:2px solid #334155; margin-bottom:28px; }}
.tab {{ padding:10px 20px; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; cursor:pointer; border-bottom:2px solid transparent; margin-bottom:-2px; color:#64748b; text-decoration:none; }}
.tab.active {{ color:#06b6d4; border-bottom-color:#06b6d4; }}
.header {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:24px; }}
.header-left h1 {{ font-size:28px; font-weight:800; letter-spacing:-0.5px; }}
.header-left h1 span {{ color:#06b6d4; }}
.live-badge {{ display:inline-block; background:rgba(16,185,129,0.15); color:#34d399; font-size:10px; font-weight:700; padding:2px 10px; border-radius:12px; margin-left:10px; vertical-align:middle; }}
.header-left .subtitle {{ font-size:11px; color:#94a3b8; margin-top:4px; }}
.header-right {{ text-align:right; font-size:11px; color:#94a3b8; }}
.header-right strong {{ color:#cbd5e1; }}
.nav-card {{ background:#1e293b; border:1px solid #334155; border-radius:12px; padding:24px 32px; margin-bottom:28px; display:flex; align-items:center; gap:40px; flex-wrap:wrap; }}
.nav-main {{ }}
.nav-label {{ font-size:9px; text-transform:uppercase; letter-spacing:1px; color:#64748b; font-weight:600; margin-bottom:4px; }}
.nav-value {{ font-size:36px; font-weight:800; letter-spacing:-1px; color:#f1f5f9; }}
.nav-change {{ font-size:13px; margin-top:2px; }}
.nav-kpis {{ display:flex; gap:32px; flex-wrap:wrap; }}
.nav-kpi {{ text-align:center; }}
.nav-kpi-label {{ font-size:9px; text-transform:uppercase; color:#64748b; font-weight:600; letter-spacing:0.5px; }}
.nav-kpi-value {{ font-size:20px; font-weight:800; margin-top:2px; }}
.nav-kpi-sub {{ font-size:10px; color:#64748b; }}
.pos {{ color:#34d399; }} .neg {{ color:#f87171; }}
.chart-container {{ margin-bottom:28px; }}
.section-title {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.8px; color:#94a3b8; margin-bottom:12px; }}
.exp-strip {{ display:grid; grid-template-columns:repeat(9, 1fr); gap:8px; margin-bottom:28px; }}
.exp-item {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:12px 8px; text-align:center; }}
.exp-label {{ font-size:8px; text-transform:uppercase; color:#64748b; font-weight:600; letter-spacing:0.5px; }}
.exp-value {{ font-size:18px; font-weight:800; color:#06b6d4; margin:4px 0; }}
.exp-sub {{ font-size:8px; color:#64748b; }}
.signal-grid {{ display:grid; grid-template-columns:repeat(5, 1fr); gap:12px; margin-bottom:28px; }}
.signal-card {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:14px; }}
.signal-header {{ margin-bottom:10px; font-size:13px; }}
.signal-wpct {{ color:#64748b; font-size:11px; }}
.signal-row {{ display:flex; justify-content:space-between; align-items:center; padding:3px 0; font-size:11px; }}
.signal-ticker {{ font-weight:700; }}
.signal-name {{ color:#94a3b8; font-size:10px; }}
.signal-mom {{ font-weight:600; }}
.signal-exp {{ margin-top:8px; padding-top:8px; border-top:1px solid #334155; font-size:10px; color:#94a3b8; }}
.pos-donut-grid {{ display:grid; grid-template-columns:1fr 280px; gap:24px; margin-bottom:28px; }}
.data-table {{ width:100%; border-collapse:collapse; font-size:11px; }}
.data-table th {{ background:#1e293b; color:#94a3b8; font-weight:600; text-transform:uppercase; font-size:9px; letter-spacing:0.5px; padding:10px 8px; text-align:left; border-bottom:1px solid #334155; }}
.data-table td {{ padding:8px; border-bottom:1px solid #1e293b; }}
.data-table .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.data-table tr:hover {{ background:#1e293b; }}
.sma-grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:10px; margin-bottom:28px; }}
.sma-card {{ background:#1e293b; border:1px solid #334155; border-radius:8px; padding:12px; }}
.sma-card-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }}
.sma-card-name {{ font-size:10px; color:#64748b; }}
.sma-card-price {{ font-size:12px; font-weight:600; color:#f1f5f9; margin-top:2px; }}
.sma-card-sma {{ font-size:10px; color:#64748b; }}
.alert-banner {{ border-radius:8px; padding:14px 18px; margin-bottom:16px; border-left:4px solid; }}
.alert-exit {{ background:rgba(239,68,68,0.12); border-color:#ef4444; }}
.alert-entry {{ background:rgba(16,185,129,0.12); border-color:#10b981; }}
.alert-watch {{ background:rgba(245,158,11,0.12); border-color:#f59e0b; }}
.alert-info {{ background:rgba(100,116,139,0.12); border-color:#64748b; }}
.alert-ok {{ background:rgba(16,185,129,0.08); border-color:#10b981; }}
.alert-title {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:8px; }}
.alert-exit .alert-title {{ color:#f87171; }}
.alert-entry .alert-title {{ color:#34d399; }}
.alert-watch .alert-title {{ color:#fbbf24; }}
.alert-info .alert-title {{ color:#94a3b8; }}
.alert-ok .alert-title {{ color:#34d399; }}
.alert-action {{ font-size:12px; font-family:'Courier New',monospace; color:#cbd5e1; padding:3px 0; }}
.footer {{ display:flex; justify-content:space-between; font-size:9px; color:#64748b; padding:16px 0; border-top:1px solid #334155; margin-top:24px; }}
.tab-panel {{ display:none; }}
.tab-panel.active {{ display:block; }}
.backtest-frame {{ width:100%; border:none; border-radius:8px; min-height:90vh; background:#0f172a; }}
@media (max-width: 900px) {{
    .exp-strip {{ grid-template-columns:repeat(4, 1fr); }}
    .signal-grid {{ grid-template-columns:repeat(2, 1fr); }}
    .pos-donut-grid {{ grid-template-columns:1fr; }}
    .nav-card {{ flex-direction:column; gap:16px; }}
}}
</style>
</head>
<body>
<div class="container">

  <div class="tabs">
    <span class="tab active" onclick="switchTab(0)">Live Panel</span>
    <span class="tab" onclick="switchTab(1)">Philosophy & Backtest</span>
  </div>

  <div id="tab-live" class="tab-panel active">

  <div class="header">
    <div class="header-left">
      <h1><span>Iberic</span> Centinela <span class="live-badge">LIVE</span></h1>
      <div class="subtitle">Dual SMA (SMA200 + Exit SMA{SMA_EXIT}) | Top-{N_SELECT} Momentum {MOM_LOOKBACK}d | Monthly Rebalance | ${INITIAL_CAPITAL:,} initial | UCITS</div>
    </div>
    <div class="header-right">
      <strong>SFinance-alicIA</strong><br>
      Signal: {dates_live[-1].strftime("%Y-%m-%d")}<br>
      Next: {next_rebal.strftime("%Y-%m-%d")} ({days_to_rebal}d)
    </div>
  </div>

  {operational_status_html()}

  {sma50_alert_html()}

  {trade_blotter_html()}

  <div class="nav-card">
    <div class="nav-main">
      <div class="nav-label">Portfolio Value</div>
      <div class="nav-value">${nav_live[-1]:,.2f}</div>
      <div class="nav-change {pct_cls(today_ret)}">{today_sign}{today_ret:.2f}% today (${abs(today_dollar):,.0f})</div>
    </div>
    <div class="nav-kpis">
      <div class="nav-kpi">
        <div class="nav-kpi-label">Today</div>
        <div class="nav-kpi-value {pct_cls(today_ret)}">{today_sign}{today_ret:.2f}%</div>
        <div class="nav-kpi-sub">{"-" if today_dollar < 0 else ""}${abs(today_dollar):,.0f}</div>
      </div>
      <div class="nav-kpi">
        <div class="nav-kpi-label">MTD</div>
        <div class="nav-kpi-value {pct_cls(mtd_ret)}">{mtd_sign}{mtd_ret:.2f}%</div>
        <div class="nav-kpi-sub">{"-" if mtd_dollar < 0 else ""}${abs(mtd_dollar):,.0f}</div>
      </div>
      <div class="nav-kpi">
        <div class="nav-kpi-label">YTD</div>
        <div class="nav-kpi-value {pct_cls(ytd_ret)}">{ytd_sign}{ytd_ret:.2f}%</div>
        <div class="nav-kpi-sub">{"-" if ytd_dollar < 0 else "+"}${abs(ytd_dollar):,.0f}</div>
      </div>
      <div class="nav-kpi">
        <div class="nav-kpi-label">Since Inception</div>
        <div class="nav-kpi-value {pct_cls(ytd_ret)}">{ytd_sign}{ytd_ret:.2f}%</div>
        <div class="nav-kpi-sub">{"-" if ytd_dollar < 0 else "+"}${abs(ytd_dollar):,.0f}</div>
      </div>
      <div class="nav-kpi">
        <div class="nav-kpi-label">Drawdown</div>
        <div class="nav-kpi-value neg">{max_dd:.1f}%</div>
        <div class="nav-kpi-sub">current: {curr_dd:.1f}%</div>
      </div>
    </div>
  </div>

  <div class="section-title">Portfolio Evolution — Since Inception</div>
  <div class="chart-container">{build_nav_chart()}</div>

  <div class="exp-strip">
    <div class="exp-item"><div class="exp-label">Equity Exp</div><div class="exp-value">{curr_exp.get("Equity",1):.0%}</div><div class="exp-sub">SMA200 filter</div></div>
    <div class="exp-item"><div class="exp-label">Bonds Exp</div><div class="exp-value">{curr_exp.get("Bonds",1):.0%}</div><div class="exp-sub">SMA200 filter</div></div>
    <div class="exp-item"><div class="exp-label">Hard Assets Exp</div><div class="exp-value">{curr_exp.get("HardAssets",1):.0%}</div><div class="exp-sub">SMA200 filter</div></div>
    <div class="exp-item"><div class="exp-label">Long Vol</div><div class="exp-value" style="font-size:14px;color:#ef4444">{longvol_label}</div><div class="exp-sub">fixed 21%</div></div>
    <div class="exp-item"><div class="exp-label">Cmdty Trend</div><div class="exp-value">{cmdty_exp:.0%}</div><div class="exp-sub">{cmdty_ticker} SMA200</div></div>
    <div class="exp-item"><div class="exp-label">SMA50 Exits</div><div class="exp-value" style="color:{'#ef4444' if exited_tickers else '#10b981'}">{len(exited_tickers)}</div><div class="exp-sub">in {shy_ucits}</div></div>
    <div class="exp-item"><div class="exp-label">Positions</div><div class="exp-value" style="color:#f1f5f9">{n_positions}</div><div class="exp-sub">active tickers</div></div>
    <div class="exp-item"><div class="exp-label">Cash ({shy_ucits})</div><div class="exp-value" style="color:#f1f5f9">{cash_pct:.0%}</div><div class="exp-sub">protected capital</div></div>
    <div class="exp-item"><div class="exp-label">Next Rebal</div><div class="exp-value" style="color:#f1f5f9">{days_to_rebal}d</div><div class="exp-sub">{next_rebal.strftime("%Y-%m-%d")}</div></div>
  </div>

  <div class="section-title">Signals by Block — Latest Signal</div>
  {signal_blocks_html()}

  <div class="section-title">Current Positions — Rebal {rebal_date.strftime("%d/%m/%Y")}</div>
  <div class="pos-donut-grid">
    <div style="overflow-x:auto">
      <table class="data-table">
        <tr><th>Ticker</th><th>Name</th><th>Block</th><th class="num">Target</th><th class="num">Actual</th><th class="num">Shares</th><th class="num">Value</th><th class="num">Price</th><th class="num">Mom 126d</th><th class="num">SMA200</th><th class="num">SMA50 Exit</th></tr>
        {positions_table_html()}
      </table>
    </div>
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center">
      <div class="section-title" style="text-align:center">Block Distribution</div>
      {build_donut()}
    </div>
  </div>

  <div class="section-title">SMA200 Status — All Assets</div>
  <div class="sma-grid">
    {sma200_cards_html()}
  </div>

  <div class="section-title">YTD Evolution {LIVE_YEAR} — All Assets (Base 0%)</div>
  <div class="chart-container" style="border:1px solid #334155;border-radius:8px;padding:12px">{build_ytd_chart()}</div>
  <div style="margin-top:6px;font-size:9px;color:#64748b">
    Cumulative return since January 1, {LIVE_YEAR}. <span style="color:#06b6d4;font-weight:700">Cyan line = Iberic Centinela.</span>
  </div>

  <div style="margin-top:24px">
    <div class="section-title">Performance vs Benchmarks — YTD {LIVE_YEAR}</div>
    {benchmark_table_html()}
  </div>

  <div style="margin-top:24px">
    <div class="section-title">Rebalancing History</div>
    <table class="data-table">
      <tr><th>Date</th><th>Equity</th><th>Bonds</th><th>Hard Assets</th><th class="num">Eq Exp</th><th class="num">HA Exp</th><th class="num">Cmdty</th></tr>
      {rebal_history_html()}
    </table>
  </div>

  <div class="footer">
    <span>SFinance-alicIA | Iberic Centinela LIVE | UCITS compliant | For informational purposes only, not financial advice</span>
    <span>Signal: {dates_live[-1].strftime("%Y-%m-%d")} | {n_positions} positions | {N_live} NAV days | SMA200 min {MIN_EXPOSURE:.0%}</span>
  </div>

  </div>

  <div id="tab-backtest" class="tab-panel">
    <iframe class="backtest-frame" id="backtest-iframe" data-src="Iberic_Centinela_Backtest.html"></iframe>
  </div>

</div>
<script>
function switchTab(idx) {{
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.tab-panel');
  tabs.forEach((t, i) => {{ t.classList.toggle('active', i === idx); }});
  panels.forEach((p, i) => {{ p.classList.toggle('active', i === idx); }});
  if (idx === 1) {{
    const iframe = document.getElementById('backtest-iframe');
    if (!iframe.src) iframe.src = iframe.dataset.src;
  }}
}}
</script>
</body>
</html>'''

# ═══════════════════════════════════════════════════════════════════
# 11. OUTPUT
# ═══════════════════════════════════════════════════════════════════
outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Iberic_Centinela_Live.html")
with open(outpath, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\n  Report saved: {outpath}")

# ═══════════════════════════════════════════════════════════════════
# 12. DAILY EMAIL NOTIFICATION
# ═══════════════════════════════════════════════════════════════════
REBAL_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_rebal_iberic.json")

def is_new_rebalancing():
    current_month = f"{last_rebal['date'].year}-{last_rebal['date'].month:02d}"
    if os.path.exists(REBAL_STATE_FILE):
        try:
            with open(REBAL_STATE_FILE) as f:
                state = json.load(f)
            if state.get("last_month") == current_month:
                return False
        except:
            pass
    return True

def build_sma50_email_section():
    if not sma50_new_exits and not sma50_new_entries and not exited_tickers and not sma50_watch:
        return '<p style="color:#10b981;font-size:13px">SMA50: All assets OK. No action required.</p>'
    html = ""
    if sma50_new_exits:
        html += '<div style="background:rgba(239,68,68,0.1);padding:12px;border-radius:6px;margin-bottom:10px;border-left:3px solid #ef4444">'
        html += '<p style="color:#ef4444;font-weight:bold;font-size:12px;margin-bottom:6px">EXIT SIGNAL — Execute MOC</p>'
        for t in sma50_new_exits:
            s = sma50_exit_status[t]
            label = TICKER_LABELS.get(t, t)
            html += f'<p style="font-family:monospace;font-size:13px;color:#e2e8f0">SELL {t} ({label}) → {shy_ucits}'
            if s["pct_from_sma"] is not None:
                html += f' | ${s["price"]:.2f} | SMA50: ${s["sma50"]:.2f} ({s["pct_from_sma"]:+.1f}%)'
            html += '</p>'
        html += '</div>'
    if sma50_new_entries:
        html += '<div style="background:rgba(16,185,129,0.1);padding:12px;border-radius:6px;margin-bottom:10px;border-left:3px solid #10b981">'
        html += '<p style="color:#10b981;font-weight:bold;font-size:12px;margin-bottom:6px">RE-ENTRY SIGNAL — Execute MOC</p>'
        for t in sma50_new_entries:
            s = sma50_exit_status[t]
            label = TICKER_LABELS.get(t, t)
            html += f'<p style="font-family:monospace;font-size:13px;color:#e2e8f0">BUY {t} ({label}) ← {shy_ucits}'
            if s["pct_from_sma"] is not None:
                html += f' | ${s["price"]:.2f} | SMA50: ${s["sma50"]:.2f} ({s["pct_from_sma"]:+.1f}%)'
            html += '</p>'
        html += '</div>'
    if exited_tickers:
        html += '<div style="background:rgba(100,116,139,0.1);padding:12px;border-radius:6px;margin-bottom:10px;border-left:3px solid #64748b">'
        html += f'<p style="color:#94a3b8;font-weight:bold;font-size:12px;margin-bottom:6px">POSITIONS IN {shy_ucits}</p>'
        for t in exited_tickers:
            s = sma50_exit_status[t]
            label = TICKER_LABELS.get(t, t)
            pct = f'{s["pct_from_sma"]:+.1f}%' if s["pct_from_sma"] is not None else "—"
            html += f'<p style="font-size:12px;color:#e2e8f0">{t} ({label}) — {pct} below SMA50</p>'
        html += '</div>'
    if sma50_watch:
        html += '<div style="background:rgba(245,158,11,0.1);padding:12px;border-radius:6px;margin-bottom:10px;border-left:3px solid #f59e0b">'
        html += '<p style="color:#f59e0b;font-weight:bold;font-size:12px;margin-bottom:6px">WATCH LIST (&lt;2% from SMA50)</p>'
        for t, pct, block in sma50_watch:
            label = TICKER_LABELS.get(t, t)
            html += f'<p style="font-size:12px;color:#e2e8f0">{t} ({label}) — {pct:+.1f}% from SMA50</p>'
        html += '</div>'
    return html

def build_rebal_section():
    changes_html = ""
    if prev_rebal:
        for block in ["Equity", "Bonds", "HardAssets"]:
            prev_p = set(prev_rebal[block]["picks"])
            curr_p = set(last_rebal[block]["picks"])
            entered = curr_p - prev_p
            exited_p = prev_p - curr_p
            if entered or exited_p:
                bname = BLOCK_LABELS.get(block, block)
                changes_html += f"<p><b>{bname}:</b> "
                if entered: changes_html += " ".join(f'<span style="color:#10b981">+{t}</span>' for t in entered)
                if exited_p: changes_html += " " + " ".join(f'<span style="color:#ef4444">-{t}</span>' for t in exited_p)
                changes_html += "</p>"
    if not changes_html:
        changes_html = "<p>No position changes.</p>"
    pos_rows = ""
    for block in UNIVERSES:
        picks = last_rebal[block]["picks"]
        bname = BLOCK_LABELS.get(block, block)
        pos_rows += f'<tr><td style="font-weight:bold;color:#06b6d4;padding:6px">{bname}</td><td style="padding:6px">{", ".join(picks)}</td></tr>'
    return f"""<h3 style="color:#f59e0b;font-size:14px;margin:16px 0 8px">Monthly Rebalance</h3>
        <h4 style="color:#94a3b8;font-size:11px;text-transform:uppercase;margin-bottom:8px">Changes vs Prior Month</h4>
        <div style="background:#0f172a;padding:12px;border-radius:6px;margin-bottom:12px;font-size:13px">{changes_html}</div>
        <h4 style="color:#94a3b8;font-size:11px;text-transform:uppercase;margin-bottom:8px">Positions</h4>
        <table style="width:100%;font-size:13px;background:#0f172a;border-radius:6px;border-collapse:collapse;color:#e2e8f0">{pos_rows}</table>"""

def build_trade_blotter_email():
    orders = []
    for t in sma50_new_exits:
        price = float(price_data[t][-1])
        price_at_rb = float(price_data[t][rebal_idx_ret + 1])
        alloc = target_alloc.get(t, 0)
        n_sh = math.floor(alloc / price_at_rb) if price_at_rb > 0 else 0
        sell_amt = n_sh * price
        shy_buy = math.floor(sell_amt / shy_price_now) if shy_price_now > 0 else 0
        label = TICKER_LABELS.get(t, t)
        orders.append(("SELL", t, label, n_sh, price, sell_amt))
        orders.append(("BUY", shy_ucits, "Cash 1-3Y", shy_buy, shy_price_now, shy_buy * shy_price_now))
    for t in sma50_new_entries:
        price = float(price_data[t][-1])
        for block in EXIT_BLOCKS:
            if t in last_rebal[block]["picks"]:
                bw = W_DRAGON.get(block, 0.19)
                exp = curr_exp.get(block, 1.0)
                tw = bw * exp / len(last_rebal[block]["picks"])
                alloc = nav_now * tw
                n_buy = math.floor(alloc / price) if price > 0 else 0
                shy_sell = math.ceil(alloc / shy_price_now) if shy_price_now > 0 else 0
                label = TICKER_LABELS.get(t, t)
                orders.append(("SELL", shy_ucits, "Cash 1-3Y", shy_sell, shy_price_now, shy_sell * shy_price_now))
                orders.append(("BUY", t, label, n_buy, price, n_buy * price))
                break
    if not orders:
        return ""
    html = '<h3 style="color:#f59e0b;font-size:12px;text-transform:uppercase;margin:16px 0 8px">Orders to Execute — MOC</h3>'
    html += '<table style="width:100%;font-size:12px;background:#0f172a;border-radius:6px;border-collapse:collapse;color:#e2e8f0">'
    html += '<tr style="color:#64748b;font-size:10px"><th style="padding:8px;text-align:left">Action</th><th style="padding:8px">Ticker</th><th style="padding:8px;text-align:right">Shares</th><th style="padding:8px;text-align:right">Price</th><th style="padding:8px;text-align:right">Amount</th></tr>'
    for action, ticker, name, shares, price, amount in orders:
        a_clr = "#ef4444" if action == "SELL" else "#10b981"
        sh_fmt = f"{int(shares)}"
        pfmt = f"${price:.2f}"
        html += f'<tr><td style="padding:6px 8px;color:{a_clr};font-weight:bold">{action}</td><td style="padding:6px 8px;font-weight:bold">{ticker} <span style="color:#64748b;font-weight:normal">({name})</span></td><td style="padding:6px 8px;text-align:right">{sh_fmt}</td><td style="padding:6px 8px;text-align:right">{pfmt}</td><td style="padding:6px 8px;text-align:right">${amount:,.0f}</td></tr>'
    html += '</table>'
    return html

def build_portfolio_email_section():
    tbl = '<h3 style="color:#94a3b8;font-size:12px;text-transform:uppercase;margin:16px 0 8px">Portfolio Holdings</h3>'
    tbl += '<table style="width:100%;font-size:11px;background:#0f172a;border-radius:6px;border-collapse:collapse;color:#e2e8f0">'
    tbl += '<tr style="color:#64748b;font-size:9px;text-transform:uppercase"><th style="padding:6px;text-align:left">Ticker</th><th style="padding:6px;text-align:left">Block</th><th style="padding:6px;text-align:right">Shares</th><th style="padding:6px;text-align:right">Price</th><th style="padding:6px;text-align:right">Value</th><th style="padding:6px;text-align:right">Weight</th><th style="padding:6px;text-align:right">SMA50</th></tr>'
    rows = []
    for t in shares_map:
        if shares_map[t] == 0 and t not in sma50_exit_status:
            continue
        block = None
        for b in UNIVERSES:
            if t in last_rebal[b]["picks"]:
                block = b
                break
        blabel = BLOCK_LABELS.get(block, block or "")
        price = float(price_data[t][-1])
        sh = shares_map[t]
        sh_fmt = f"{int(sh)}"
        pfmt = f"${price:.2f}"
        val = sh * price
        w = real_weight.get(t, 0)
        is_exited = t in sma50_exit_status and sma50_exit_status[t]["exited"]
        s50 = ""
        if t in sma50_exit_status:
            s = sma50_exit_status[t]
            s50 = f'<span style="color:#ef4444">EXIT</span>' if is_exited else f'<span style="color:#10b981">OK</span>'
            if s["pct_from_sma"] is not None:
                s50 += f' ({s["pct_from_sma"]:+.1f}%)'
        row_style = "color:#64748b;font-style:italic" if is_exited else ""
        rows.append((w, f'<tr style="{row_style}"><td style="padding:5px 6px;font-weight:bold">{t}</td><td style="padding:5px 6px">{blabel}</td><td style="padding:5px 6px;text-align:right">{sh_fmt}</td><td style="padding:5px 6px;text-align:right">{pfmt}</td><td style="padding:5px 6px;text-align:right">${val:,.0f}</td><td style="padding:5px 6px;text-align:right">{w:.1f}%</td><td style="padding:5px 6px;text-align:right">{s50}</td></tr>'))
    rows.append((shy_real_weight, f'<tr style="border-top:1px solid #334155"><td style="padding:5px 6px;font-weight:bold;color:#94a3b8">{shy_ucits}</td><td style="padding:5px 6px;color:#94a3b8">Cash</td><td style="padding:5px 6px;text-align:right">{shy_shares_total}</td><td style="padding:5px 6px;text-align:right">${shy_price_now:.2f}</td><td style="padding:5px 6px;text-align:right">${total_shy_dollars:,.0f}</td><td style="padding:5px 6px;text-align:right">{shy_real_weight:.1f}%</td><td style="padding:5px 6px;text-align:right">—</td></tr>'))
    rows.sort(key=lambda x: -x[0])
    tbl += "".join(r[1] for r in rows)
    tbl += '</table>'
    return tbl

def build_daily_email_html():
    new_rebal = is_new_rebalancing()
    bench_html = f'''<div style="background:#0f172a;padding:12px;border-radius:6px;margin:8px 0">
        <table style="width:100%;font-size:12px;color:#e2e8f0;border-collapse:collapse">
        <tr style="color:#64748b;font-size:9px;text-transform:uppercase"><th style="padding:4px;text-align:left"></th><th style="padding:4px;text-align:right;color:#06b6d4">Iberic</th><th style="padding:4px;text-align:right;color:#3b82f6">{spy_ucits}</th><th style="padding:4px;text-align:right;color:#f59e0b">60/40</th></tr>
        <tr><td style="padding:4px;color:#94a3b8">YTD</td><td style="padding:4px;text-align:right;font-weight:bold;color:#06b6d4">{metrics_dragon["ytd"]:+.2f}%</td><td style="padding:4px;text-align:right">{metrics_spy["ytd"]:+.2f}%</td><td style="padding:4px;text-align:right">{metrics_6040["ytd"]:+.2f}%</td></tr>
        <tr><td style="padding:4px;color:#94a3b8">Sharpe</td><td style="padding:4px;text-align:right;font-weight:bold;color:#06b6d4">{metrics_dragon["sharpe"]:.2f}</td><td style="padding:4px;text-align:right">{metrics_spy["sharpe"]:.2f}</td><td style="padding:4px;text-align:right">{metrics_6040["sharpe"]:.2f}</td></tr>
        <tr><td style="padding:4px;color:#94a3b8">Max DD</td><td style="padding:4px;text-align:right;color:#ef4444">{metrics_dragon["mdd"]:.2f}%</td><td style="padding:4px;text-align:right">{metrics_spy["mdd"]:.2f}%</td><td style="padding:4px;text-align:right">{metrics_6040["mdd"]:.2f}%</td></tr>
        </table></div>'''

    return f"""<div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;background:#1e293b;color:#e2e8f0;padding:24px;border-radius:8px">
        <h2 style="color:#06b6d4;margin-bottom:4px">Iberic Centinela — Dual SMA (UCITS)</h2>
        <p style="font-size:11px;color:#64748b;margin-bottom:16px">{TODAY.strftime("%Y-%m-%d")} | Post-close</p>

        <div style="background:#0f172a;padding:16px;border-radius:6px;margin-bottom:16px">
            <table style="width:100%;font-size:14px;color:#e2e8f0;border-collapse:collapse">
            <tr><td style="padding:4px 0">NAV</td><td style="padding:4px 0;text-align:right;font-weight:bold;color:#06b6d4">${nav_live[-1]:,.2f}</td></tr>
            <tr><td style="padding:4px 0">Today</td><td style="padding:4px 0;text-align:right;color:{'#10b981' if today_ret>=0 else '#ef4444'}">{today_ret:+.2f}% <span style="font-size:12px;color:#94a3b8">({'-' if today_dollar<0 else '+'}${abs(today_dollar):,.0f})</span></td></tr>
            <tr><td style="padding:4px 0">MTD</td><td style="padding:4px 0;text-align:right;color:{'#10b981' if mtd_ret>=0 else '#ef4444'}">{mtd_ret:+.2f}% <span style="font-size:12px;color:#94a3b8">({'-' if mtd_dollar<0 else '+'}${abs(mtd_dollar):,.0f})</span></td></tr>
            <tr><td style="padding:4px 0">YTD</td><td style="padding:4px 0;text-align:right;color:{'#10b981' if ytd_ret>=0 else '#ef4444'}">{ytd_ret:+.2f}% <span style="font-size:12px;color:#94a3b8">({'-' if ytd_dollar<0 else '+'}${abs(ytd_dollar):,.0f})</span></td></tr>
            <tr><td style="padding:4px 0">Drawdown</td><td style="padding:4px 0;text-align:right;color:#ef4444">{curr_dd:.1f}% <span style="font-size:12px;color:#94a3b8">(max: {max_dd:.1f}%)</span></td></tr>
            </table>
        </div>

        <h3 style="color:#94a3b8;font-size:12px;text-transform:uppercase;margin-bottom:8px">SMA50 Signals — Equity & Hard Assets</h3>
        {build_sma50_email_section()}
        {build_trade_blotter_email()}

        {build_rebal_section() if new_rebal else ''}

        {build_portfolio_email_section()}

        <h3 style="color:#94a3b8;font-size:12px;text-transform:uppercase;margin:16px 0 8px">vs Benchmarks YTD</h3>
        {bench_html}

        <p style="margin-top:20px;font-size:10px;color:#475569">SFinance-alicIA | Iberic Centinela Dual SMA (UCITS) | {TODAY.strftime("%Y-%m-%d")}</p>
    </div>"""

def send_daily_email():
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_pass:
        print("  ! GMAIL_APP_PASSWORD not set — skipping email")
        return False
    if sma50_new_exits:
        subject = f"ACTION — SELL {', '.join(sma50_new_exits)} → {shy_ucits} | Iberic Centinela | {TODAY}"
    elif sma50_new_entries:
        subject = f"ACTION — BUY {', '.join(sma50_new_entries)} ← {shy_ucits} | Iberic Centinela | {TODAY}"
    elif is_new_rebalancing():
        month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        subject = f"Iberic Centinela — Rebalance {month_names[last_rebal['date'].month-1]} | NAV ${nav_live[-1]:,.0f} | {TODAY}"
    else:
        subject = f"Iberic Centinela — NAV ${nav_live[-1]:,.0f} | YTD {ytd_ret:+.1f}% | {TODAY}"
    html_body = build_daily_email_html()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, gmail_pass)
            for rcpt in EMAIL_RECIPIENTS:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = GMAIL_SENDER
                msg["To"] = rcpt
                msg.attach(MIMEText(html_body, "html"))
                server.sendmail(GMAIL_SENDER, [rcpt], msg.as_string())
        print(f"  Email sent to {len(EMAIL_RECIPIENTS)} recipients (individual BCC)")
        return True
    except Exception as e:
        print(f"  ! Email error: {e}")
        return False

# Daily email (always send after full run)
print("\nSending daily email...")
sent = send_daily_email()
if is_new_rebalancing():
    current_month = f"{last_rebal['date'].year}-{last_rebal['date'].month:02d}"
    with open(REBAL_STATE_FILE, "w") as f:
        json.dump({"last_month": current_month, "date": str(TODAY), "sent": sent}, f)
    print(f"  Rebalancing state saved: {REBAL_STATE_FILE}")

if not os.environ.get("CI"):
    os.system(f'open "{outpath}"')
print("\n=== Done ===")
