#!/usr/bin/env python3
"""
Iberic Centinel — Dragon Portfolio UCITS + SMA50 Exit Filter (REALISTIC)
Based on "The Allegory of the Hawk and Serpent" (Artemis Capital, Jan 2020)

UCITS-compliant ETFs for European retail investors (PRIIPs/KID compliant).
Momentum top-3 selection + SMA50 exit at REBALANCING ONLY (no daily switching).

REALISTIC IMPLEMENTATION:
  - SMA50 filter checked only at monthly rebalancing (not daily)
  - 1-day lag on SMA50 signal (uses previous close, not current)
  - Transaction costs: 15bps per trade (spread + commission)
  - No intra-month switching — positions held until next rebalance

Key substitutions vs US version:
  BTAL → JPM Europe Eq Absolute Alpha D (Perf) Acc EUR (LU1176912761)
         Fondo long/short equity europeo, market neutral
  SPY  → CSPX.L | QQQ → EQQQ.L | IWM → XRS2.DE | DBC → ICOM.L
  GLD  → IGLN.L | SLV → ISLN.L | CPER → COPA.L

SFinance-alicIA
"""
import yfinance as yf
import numpy as np
import datetime, os, math, json

# ═══════════════════════════════════════════════════════════════════
# 1. CONFIG — UCITS ETFs + JPM Absolute Alpha
# ═══════════════════════════════════════════════════════════════════
UNIVERSES = {
    "Equity":      ["CSPX.L", "EQQQ.L", "XRS2.DE", "EIMI.L", "IMEU.L", "IQQK.DE", "IQQB.DE"],
    "Bonds":       ["IBTS.L", "IBTM.L", "IDTL.L", "ITPS.L", "LQDE.L"],
    "HardAssets":  ["IGLN.L", "ISLN.L", "COPA.L"],
    "LongVol":     ["0P0001A2HU.F"],
    "Commodities": ["ICOM.L"],
}

TICKER_LABELS = {
    "CSPX.L": "S&P 500", "EQQQ.L": "Nasdaq-100", "XRS2.DE": "Russell 2000",
    "EIMI.L": "Emergentes", "IMEU.L": "Europa", "IQQK.DE": "Korea", "IQQB.DE": "Brasil",
    "IBTS.L": "Treasury 1-3Y", "IBTM.L": "Treasury 7-10Y", "IDTL.L": "Treasury 20+Y",
    "ITPS.L": "TIPS", "LQDE.L": "IG Corp",
    "IGLN.L": "Oro", "ISLN.L": "Plata", "COPA.L": "Cobre", "BTC-USD": "Bitcoin",
    "0P0001A2HU.F": "JPM Abs Alpha", "ICOM.L": "Commodities",
}

US_TO_UCITS = {
    "SPY": "CSPX.L", "QQQ": "EQQQ.L", "IWM": "XRS2.DE",
    "EEM": "EIMI.L", "VGK": "IMEU.L", "EWY": "IQQK.DE", "EWZ": "IQQB.DE",
    "SHY": "IBTS.L", "IEF": "IBTM.L", "TLT": "IDTL.L", "TIP": "ITPS.L", "LQD": "LQDE.L",
    "GLD": "IGLN.L", "SLV": "ISLN.L", "CPER": "COPA.L",
    "BTAL": "0P0001A2HU.F", "DBC": "ICOM.L",
}

TICKER_COLORS = {
    "CSPX.L": "#3b82f6", "EQQQ.L": "#8b5cf6", "XRS2.DE": "#f97316",
    "EIMI.L": "#f59e0b", "IMEU.L": "#06b6d4", "IQQK.DE": "#ec4899", "IQQB.DE": "#22c55e",
    "IBTS.L": "#94a3b8", "IBTM.L": "#38bdf8", "IDTL.L": "#0ea5e9", "ITPS.L": "#f97316", "LQDE.L": "#10b981",
    "IGLN.L": "#f59e0b", "ISLN.L": "#94a3b8", "COPA.L": "#f97316", "BTC-USD": "#f7931a",
    "0P0001A2HU.F": "#ef4444", "ICOM.L": "#a855f7",
}

ALL_TICKERS = sorted(set(t for lst in UNIVERSES.values() for t in lst))
LATE_JOINERS = set()
CORE_TICKERS = sorted(t for t in ALL_TICKERS if t not in LATE_JOINERS)

N_SELECT = 3
MOM_LOOKBACK = 126
SMA_LONG = 200            # SMA200 trend filter (exposure scaling)
SMA_EXIT = 50             # SMA50 intra-month exit signal
SMA_CMDTY = 50            # Commodity trend deviation sizing
MIN_EXPOSURE = 0.30       # Minimum exposure floor when all picks below SMA200
TX_COST_BPS = 30          # Transaction cost per exit/re-entry (basis points)
EXIT_BLOCKS = {"Equity", "HardAssets"}  # Blocks with SMA50 exit (not Bonds)

START = "2018-01-02"
TODAY = datetime.date.today()
RF_ANNUAL = 0.043
LONGVOL_LEVERAGE = 1.0

W_DRAGON = {"Equity": 0.24, "Bonds": 0.18, "HardAssets": 0.19, "LongVol": 0.21, "CmdtyTrend": 0.18}
W_6040 = {"Equity": 0.60, "Bonds": 0.40}

COLORS = {
    "Equity":     "#10b981",
    "Bonds":      "#06b6d4",
    "HardAssets": "#f59e0b",
    "LongVol":    "#ef4444",
    "CmdtyTrend": "#a855f7",
    "Dragon":     "#fbbf24",
    "6040":       "#94a3b8",
    "SPY":        "#3b82f6",
}

# ═══════════════════════════════════════════════════════════════════
# 2. DATA FETCHING (with local cache + late-joiner support)
# ═══════════════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_cache_iberic.json")

print("═══ Iberic Centinel — Dragon UCITS + JPM Abs Alpha + SMA50 Exit ═══\n")

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        if cache.get("date") == str(TODAY):
            print("Using cached data (same day)...")
            return cache
    except:
        pass
    return None

def save_cache(dates_list, data_dict):
    cache = {
        "date": str(TODAY),
        "dates": [str(d) for d in dates_list],
        "prices": {t: [None if (isinstance(v, float) and np.isnan(v)) else v
                       for v in data_dict[t].tolist()] for t in data_dict},
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)
    print(f"  Cache saved: {CACHE_FILE}")

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
            df = yf.download(ticker, start=START, end=str(TODAY), progress=False, auto_adjust=True)
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
            print(f"  * {t}: no data available")

    dates = [d.date() if hasattr(d, "date") else d for d in common_idx]
    N = len(dates)
    save_cache(dates, price_data)

print(f"\n  Aligned: {N} trading days ({dates[0]} -> {dates[-1]})")
print(f"  Tickers: {len(ALL_TICKERS)} ({len(CORE_TICKERS)} core + {len(LATE_JOINERS)} late-joiner)")

# Daily returns
ret = {}
for t in ALL_TICKERS:
    if t in price_data:
        p = price_data[t]
        ret[t] = np.diff(p) / p[:-1]
dates_ret = dates[1:]
N_ret = len(dates_ret)

for t in LATE_JOINERS:
    if t in ret:
        first_valid = None
        for i in range(N_ret):
            if not np.isnan(ret[t][i]):
                first_valid = dates_ret[i]
                break
        if first_valid:
            print(f"  {t} first valid return: {first_valid}")

# ═══════════════════════════════════════════════════════════════════
# 3. DUAL SMA COMPUTATION (SMA200 trend + SMA50 exit — same as Centinela v3)
# ═══════════════════════════════════════════════════════════════════
print("\nComputing Dual SMA signals (SMA200 trend + SMA50 exit)...")

# Momentum
mom = {}
for t in ALL_TICKERS:
    if t not in price_data:
        continue
    p = price_data[t]
    m = np.full(N_ret, np.nan)
    for i in range(N_ret):
        if i >= MOM_LOOKBACK:
            p_now = p[i]
            p_prev = p[i - MOM_LOOKBACK]
            if not np.isnan(p_now) and not np.isnan(p_prev) and p_prev > 0:
                m[i] = p_now / p_prev - 1
    mom[t] = m

# SMA200 for exposure scaling
sma200_above = {}
for t in ALL_TICKERS:
    if t not in price_data:
        continue
    p = price_data[t]
    above = np.full(N_ret, False)
    for i in range(N_ret):
        day_idx = i + 1  # price index for return i
        if day_idx >= SMA_LONG:
            window = p[day_idx - SMA_LONG:day_idx]
            valid_vals = window[~np.isnan(window)]
            if len(valid_vals) >= SMA_LONG * 0.8:
                sma_v = np.mean(valid_vals)
                if not np.isnan(p[day_idx]):
                    above[i] = p[day_idx] > sma_v
    sma200_above[t] = above

# SMA50 for exit signal (intra-month)
sma_exit_above = {}
for t in ALL_TICKERS:
    if t not in price_data:
        continue
    p = price_data[t]
    above = np.full(N_ret, True)
    for i in range(N_ret):
        day_idx = i + 1
        if day_idx >= SMA_EXIT:
            window = p[day_idx - SMA_EXIT:day_idx]
            valid_vals = window[~np.isnan(window)]
            if len(valid_vals) >= SMA_EXIT * 0.8:
                sma_v = np.mean(valid_vals)
                if not np.isnan(p[day_idx]):
                    above[i] = p[day_idx] > sma_v
    sma_exit_above[t] = above

# Monthly selection: top-N_SELECT per block + SMA200 exposure scaling
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

    # SMA200 exposure scaling (daily, like Centinela v3)
    for block in UNIVERSES:
        selections[block].append(list(current_sel[block]))
        picks_b = current_sel[block]
        above_b = sum(1 for t in picks_b if t in sma200_above and sma200_above[t][i])
        sc = above_b / max(len(picks_b), 1)
        sc = max(sc, MIN_EXPOSURE)
        exposure_scale[block].append(sc)

for block in ["Equity", "Bonds", "HardAssets"]:
    all_picks = set()
    for entry in selection_log:
        all_picks.update(entry[block]["picks"])
    print(f"  {block:12s} top-{N_SELECT}: {len(all_picks)} unique — {', '.join(sorted(all_picks))}")
print(f"  Selection periods: {len(selection_log)} months")

# Exposure stats
for block in ["Equity", "Bonds", "HardAssets"]:
    exp = np.array(exposure_scale[block])
    avg_exp = np.mean(exp)
    full_pct = np.sum(exp >= 0.99) / len(exp) * 100
    min_pct = np.sum(exp <= MIN_EXPOSURE + 0.01) / len(exp) * 100
    print(f"  {block:12s} SMA200 exposure: avg {avg_exp:.0%} | 100%: {full_pct:.0f}% days | {MIN_EXPOSURE:.0%}: {min_pct:.0f}% days")

# ═══════════════════════════════════════════════════════════════════
# 4. STRATEGY CONSTRUCTION (Dual SMA — same logic as Centinela v3)
# ═══════════════════════════════════════════════════════════════════
print("\nConstructing strategies (Dual SMA)...")

# Cash destination: short-term bonds (IBTS.L = 1-3Y Treasury UCITS equivalent of SHY)
shy_ucits = "IBTS.L" if "IBTS.L" in ret else CORE_TICKERS[0]
shy_ret = ret[shy_ucits]
print(f"  Cash destination: {shy_ucits} (UCITS SHY equivalent)")

def dynamic_block_returns_dual_sma(block_name, use_sma_filter=True, use_exit_signal=False):
    """Equal-weight average of top-N selected, with SMA200 exposure scaling.
    If use_exit_signal=True, assets below SMA50 exit to SHY with TX_COST_BPS per switch.
    Same architecture as Centinela v3."""
    r = np.zeros(N_ret)
    exit_count = 0
    switch_count = 0
    prev_exited = {}

    for i in range(N_ret):
        picks = selections[block_name][i]
        shy_r = shy_ret[i] if not np.isnan(shy_ret[i]) else 0.0
        is_rebal = (i == 0) or (dates_ret[i].month != dates_ret[i - 1].month)

        if is_rebal:
            prev_exited = {}  # Fresh start on rebalance

        if use_exit_signal:
            valid = []
            for t in picks:
                if t not in ret or np.isnan(ret[t][i]):
                    continue
                t_ret = ret[t][i]
                was_exited = prev_exited.get(t, False)
                is_below = (t in sma_exit_above and not sma_exit_above[t][i])

                if is_below:
                    # Asset below SMA50 → exit to SHY
                    asset_ret = shy_r
                    if not was_exited:
                        switch_count += 1
                        asset_ret -= TX_COST_BPS / 10000  # exit cost
                    prev_exited[t] = True
                    exit_count += 1
                else:
                    # Asset above SMA50 → stay/re-enter
                    asset_ret = t_ret
                    if was_exited:
                        switch_count += 1
                        asset_ret -= TX_COST_BPS / 10000  # re-entry cost
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

# SMA200-filtered + SMA50 exit on Equity & Hard Assets (not Bonds — same as Centinela)
ret_equity, eq_exits, eq_sw = dynamic_block_returns_dual_sma("Equity", True, "Equity" in EXIT_BLOCKS)
eq_label = f"SMA200 + SMA{SMA_EXIT} exit @{TX_COST_BPS}bp" if "Equity" in EXIT_BLOCKS else "SMA200"
print(f"  + Equity ({eq_label}, top-{N_SELECT} from {len(UNIVERSES['Equity'])}): {N_ret} days, {eq_exits} exits, {eq_sw} switches")

ret_bonds, bo_exits, bo_sw = dynamic_block_returns_dual_sma("Bonds", True, "Bonds" in EXIT_BLOCKS)
bo_label = f"SMA200 + SMA{SMA_EXIT} exit @{TX_COST_BPS}bp" if "Bonds" in EXIT_BLOCKS else "SMA200"
print(f"  + Bonds ({bo_label}, top-{N_SELECT} from {len(UNIVERSES['Bonds'])}): {N_ret} days, {bo_exits} exits, {bo_sw} switches")

ret_hard, ha_exits, ha_sw = dynamic_block_returns_dual_sma("HardAssets", True, "HardAssets" in EXIT_BLOCKS)
ha_label = f"SMA200 + SMA{SMA_EXIT} exit @{TX_COST_BPS}bp" if "HardAssets" in EXIT_BLOCKS else "SMA200"
print(f"  + Hard Assets ({ha_label}, top-{N_SELECT} from {len(UNIVERSES['HardAssets'])}): {N_ret} days, {ha_exits} exits, {ha_sw} switches")

total_switches = eq_sw + bo_sw + ha_sw
print(f"  Total switches: {total_switches} ({total_switches/max(N_ret/252,1):.0f}/year) @ {TX_COST_BPS}bp each")

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
    print(f"    Using inverse {spy_ucits} x0.3 as crude proxy")

# Commodity Trend with SMA200 gate + SMA50 deviation sizing (same as Centinela)
cmdty_ticker = UNIVERSES["Commodities"][0] if UNIVERSES["Commodities"] else None
if cmdty_ticker and cmdty_ticker in ret:
    cmdty_prices = price_data[cmdty_ticker]
    ret_cmdty_trend = np.zeros(N_ret)
    for i in range(N_ret):
        day_idx = i + 1
        # SMA200 gate: commodity must be above SMA200
        if day_idx >= SMA_LONG:
            sma200_cmdty = np.mean(cmdty_prices[day_idx - SMA_LONG:day_idx])
            if cmdty_prices[day_idx] < sma200_cmdty:
                ret_cmdty_trend[i] = 0.0
                continue
        # SMA50 deviation sizing
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
# 5. PORTFOLIO CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════
print("\nBuilding portfolios (monthly rebalancing)...")

comp_ret = {
    "Equity":     ret_equity,
    "Bonds":      ret_bonds,
    "HardAssets": ret_hard,
    "LongVol":    ret_longvol,
    "CmdtyTrend": ret_cmdty_trend,
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

dragon_ret = monthly_rebal_portfolio(W_DRAGON, comp_ret, dates_ret)
# TX costs already embedded in block returns via SMA50 exit switches

# 60/40 benchmark (NO SMA filter — pure benchmark)
spy_ucits = "CSPX.L" if "CSPX.L" in ret else CORE_TICKERS[0]
tlt_ucits = "IDTL.L" if "IDTL.L" in ret else (UNIVERSES["Bonds"][-1] if UNIVERSES["Bonds"] else CORE_TICKERS[0])
comp_ret_6040 = {"Equity": ret[spy_ucits], "Bonds": ret[tlt_ucits]}
port_6040_ret = monthly_rebal_portfolio(W_6040, comp_ret_6040, dates_ret)

spy_ret = ret[spy_ucits]

def cum_nav(returns):
    nav = np.ones(len(returns) + 1)
    for i, r in enumerate(returns):
        nav[i + 1] = nav[i] * (1 + r)
    return nav

nav_dragon = cum_nav(dragon_ret)
nav_6040 = cum_nav(port_6040_ret)
nav_spy = cum_nav(spy_ret)

nav_comp = {}
for comp in comp_ret:
    nav_comp[comp] = cum_nav(comp_ret[comp])

# Serpent & Hawk sub-portfolios
W_SERPENT = {"Equity": 0.24 / 0.42, "Bonds": 0.18 / 0.42}
comp_ret_serpent = {"Equity": comp_ret["Equity"], "Bonds": comp_ret["Bonds"]}
serpent_ret = monthly_rebal_portfolio(W_SERPENT, comp_ret_serpent, dates_ret)

W_HAWK = {"HardAssets": 0.19 / 0.58, "LongVol": 0.21 / 0.58, "CmdtyTrend": 0.18 / 0.58}
comp_ret_hawk = {"HardAssets": comp_ret["HardAssets"], "LongVol": comp_ret["LongVol"], "CmdtyTrend": comp_ret["CmdtyTrend"]}
hawk_ret = monthly_rebal_portfolio(W_HAWK, comp_ret_hawk, dates_ret)

nav_serpent = cum_nav(serpent_ret)
nav_hawk = cum_nav(hawk_ret)

rebal_count = sum(1 for i in range(1, N_ret) if dates_ret[i].month != dates_ret[i-1].month) + 1
print(f"  + Iberic Centinel: ${nav_dragon[-1]:.2f} (from $1) [{rebal_count} rebalances]")
print(f"  + 60/40 UCITS:     ${nav_6040[-1]:.2f}")
print(f"  + {spy_ucits}:         ${nav_spy[-1]:.2f}")

# ═══════════════════════════════════════════════════════════════════
# 6. PERFORMANCE METRICS
# ═══════════════════════════════════════════════════════════════════
print("\nCalculating metrics...")

def calc_metrics(returns, name=""):
    n = len(returns)
    years = n / 252
    total = np.prod(1 + returns) - 1
    cagr = (1 + total) ** (1 / years) - 1
    vol = np.std(returns) * np.sqrt(252)
    sharpe = (cagr - RF_ANNUAL) / vol if vol > 1e-8 else 0
    downside = returns[returns < 0]
    downside_vol = np.std(downside) * np.sqrt(252) if len(downside) > 0 else 1e-8
    sortino = (cagr - RF_ANNUAL) / downside_vol
    nav = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / peak
    mdd = np.min(dd) * 100
    calmar = cagr / abs(mdd / 100) if abs(mdd) > 0.01 else 0
    ret_to_risk = cagr / vol if vol > 1e-8 else 0
    return {
        "name": name, "cagr": cagr, "vol": vol, "sharpe": sharpe,
        "sortino": sortino, "mdd": mdd, "calmar": calmar,
        "ret_to_risk": ret_to_risk, "total": total * 100, "years": years,
    }

m_dragon = calc_metrics(dragon_ret, "Iberic Centinel")
m_6040 = calc_metrics(port_6040_ret, "60/40 UCITS")
m_spy = calc_metrics(spy_ret, spy_ucits)
m_serpent = calc_metrics(serpent_ret, "Serpiente")
m_hawk = calc_metrics(hawk_ret, "Halcon")

m_comp = {}
for comp in comp_ret:
    m_comp[comp] = calc_metrics(comp_ret[comp], comp)

comp_names = ["Equity", "Bonds", "HardAssets", "LongVol", "CmdtyTrend"]
corr_data = np.column_stack([comp_ret[c] for c in comp_names])
corr_matrix = np.corrcoef(corr_data, rowvar=False)

print(f"  Iberic:  CAGR {m_dragon['cagr']*100:+.1f}%  Sharpe {m_dragon['sharpe']:.2f}  MDD {m_dragon['mdd']:.1f}%")
print(f"  60/40:   CAGR {m_6040['cagr']*100:+.1f}%  Sharpe {m_6040['sharpe']:.2f}  MDD {m_6040['mdd']:.1f}%")
print(f"  {spy_ucits}: CAGR {m_spy['cagr']*100:+.1f}%  Sharpe {m_spy['sharpe']:.2f}  MDD {m_spy['mdd']:.1f}%")

# ═══════════════════════════════════════════════════════════════════
# 7. REGIME ANALYSIS
# ═══════════════════════════════════════════════════════════════════
REGIME_LOOKBACK = 252
regime_labels = []
spy_prices = price_data.get(spy_ucits, price_data[CORE_TICKERS[0]])
for i in range(N_ret):
    day_idx = i + 1
    if day_idx >= REGIME_LOOKBACK:
        r1y = spy_prices[day_idx] / spy_prices[day_idx - REGIME_LOOKBACK] - 1
        if r1y > 0.15: regime_labels.append("Bull")
        elif r1y < -0.15: regime_labels.append("Bear")
        else: regime_labels.append("Flat")
    else:
        regime_labels.append("N/A")

regime_stats = {}
for regime in ["Bull", "Bear", "Flat"]:
    mask = np.array([r == regime for r in regime_labels])
    if mask.sum() < 20: continue
    regime_stats[regime] = {
        "days": mask.sum(),
        "Dragon": calc_metrics(dragon_ret[mask], f"Iberic ({regime})"),
        "6040": calc_metrics(port_6040_ret[mask], f"60/40 ({regime})"),
        "Serpent": calc_metrics(serpent_ret[mask], f"Serpent ({regime})"),
        "Hawk": calc_metrics(hawk_ret[mask], f"Hawk ({regime})"),
    }

# ═══════════════════════════════════════════════════════════════════
# 8. ANNUAL RETURNS
# ═══════════════════════════════════════════════════════════════════
annual_returns = {}
for i, d in enumerate(dates_ret):
    yr = d.year
    if yr not in annual_returns:
        annual_returns[yr] = {"Dragon": [], "6040": [], "SPY": [], "Serpent": [], "Hawk": []}
    annual_returns[yr]["Dragon"].append(dragon_ret[i])
    annual_returns[yr]["6040"].append(port_6040_ret[i])
    annual_returns[yr]["SPY"].append(spy_ret[i])
    annual_returns[yr]["Serpent"].append(serpent_ret[i])
    annual_returns[yr]["Hawk"].append(hawk_ret[i])

annual_table = {}
for yr in sorted(annual_returns.keys()):
    annual_table[yr] = {}
    for port in annual_returns[yr]:
        annual_table[yr][port] = (np.prod(1 + np.array(annual_returns[yr][port])) - 1) * 100

# ═══════════════════════════════════════════════════════════════════
# 8b. STRESS PERIOD ANALYSIS
# ═══════════════════════════════════════════════════════════════════
from datetime import datetime as _dt

STRESS_PERIODS = [
    ("Volmageddon",         "2018-01-26", "2018-02-08"),
    ("Q4 2018 Selloff",     "2018-10-01", "2018-12-24"),
    ("COVID Crash",         "2020-02-19", "2020-03-23"),
    ("2022 Bear / Rates",   "2022-01-03", "2022-10-12"),
    ("Trump Tariffs",       "2025-02-19", "2025-04-08"),
]

def period_return(returns, dates, start_str, end_str):
    start_d = _dt.strptime(start_str, "%Y-%m-%d").date()
    end_d = _dt.strptime(end_str, "%Y-%m-%d").date()
    mask = np.array([(d >= start_d and d <= end_d) for d in dates])
    if mask.sum() == 0:
        return None
    return (np.prod(1 + returns[mask]) - 1) * 100

stress_results = []
for sp_name, sp_start, sp_end in STRESS_PERIODS:
    r_d = period_return(dragon_ret, dates_ret, sp_start, sp_end)
    if r_d is None:
        continue
    stress_results.append({
        "name": sp_name, "start": sp_start, "end": sp_end,
        "Dragon": r_d,
        "6040": period_return(port_6040_ret, dates_ret, sp_start, sp_end),
        "SPY": period_return(spy_ret, dates_ret, sp_start, sp_end),
        "Serpent": period_return(serpent_ret, dates_ret, sp_start, sp_end),
        "Hawk": period_return(hawk_ret, dates_ret, sp_start, sp_end),
    })

# ═══════════════════════════════════════════════════════════════════
# 8c. EXPOSURE TRACKING (Dual SMA — SMA200 scaling + SMA50 exits)
# ═══════════════════════════════════════════════════════════════════
exposure_total = np.zeros(N_ret)

for i in range(N_ret):
    # Weighted average exposure across blocks (SMA200 scaling)
    total_exp = 0.0
    for block, w in W_DRAGON.items():
        if block == "LongVol":
            total_exp += w  # JPM Abs Alpha always fully invested (market neutral)
        elif block == "CmdtyTrend":
            # Cmdty: check if SMA200 gate allows it
            day_idx = i + 1
            if day_idx >= SMA_LONG and cmdty_ticker in price_data:
                sma200_c = np.mean(price_data[cmdty_ticker][day_idx - SMA_LONG:day_idx])
                if price_data[cmdty_ticker][day_idx] >= sma200_c:
                    total_exp += w
                else:
                    total_exp += w * MIN_EXPOSURE  # Floor
            else:
                total_exp += w * 0.5
        else:
            sc = exposure_scale[block][i] if i < len(exposure_scale[block]) else 1.0
            total_exp += w * sc
    exposure_total[i] = total_exp * 100

avg_exposure = np.mean(exposure_total)
print(f"\n  Average portfolio exposure: {avg_exposure:.1f}% (SMA200 scaling + SMA50 exits to {shy_ucits})")

# ═══════════════════════════════════════════════════════════════════
# 9. SVG CHART GENERATORS
# ═══════════════════════════════════════════════════════════════════
print("\nGenerating charts...")

def svg_line(nav_arr, dates_arr, color, width=2.0, dashed=False, vw=720, vh=300,
             log_scale=True, y_min=None, y_max=None):
    n = len(nav_arr)
    if n == 0: return ""
    vals = np.log(nav_arr) if log_scale else nav_arr
    if y_min is None: y_min = np.min(vals)
    if y_max is None: y_max = np.max(vals)
    y_range = y_max - y_min if y_max > y_min else 1
    ml, mr, mt, mb = 50, 15, 15, 30
    pw, ph = vw - ml - mr, vh - mt - mb
    pts = []
    for i in range(n):
        x = ml + (i / max(n - 1, 1)) * pw
        y = mt + ph - ((vals[i] - y_min) / y_range) * ph
        pts.append(f"{x:.1f},{y:.1f}")
    dash = ' stroke-dasharray="6,4"' if dashed else ""
    return f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{width}"{dash} stroke-linejoin="round" stroke-linecap="round"/>'

def svg_grid_and_labels(nav_dict, dates_arr, vw=720, vh=300, log_scale=True, n_y_labels=5):
    all_vals = []
    for nav in nav_dict.values():
        all_vals.extend(np.log(nav) if log_scale else nav)
    y_min, y_max = min(all_vals), max(all_vals)
    y_range = y_max - y_min if y_max > y_min else 1
    ml, mr, mt, mb = 50, 15, 15, 30
    pw, ph = vw - ml - mr, vh - mt - mb
    svg = ""
    for i in range(n_y_labels + 1):
        frac = i / n_y_labels
        y_val = y_min + frac * y_range
        y_px = mt + ph - frac * ph
        dv = math.exp(y_val) if log_scale else y_val
        label = f"${dv:.1f}" if dv < 10 else f"${dv:.0f}"
        svg += f'<line x1="{ml}" y1="{y_px:.0f}" x2="{vw-mr}" y2="{y_px:.0f}" stroke="rgba(148,163,184,0.12)" stroke-width="0.5"/>'
        svg += f'<text x="{ml-5}" y="{y_px:.0f}" text-anchor="end" fill="#64748b" font-size="9" dominant-baseline="middle">{label}</text>'
    dl = dates_arr if isinstance(dates_arr, list) else list(dates_arr)
    nd = len(dl)
    seen = set()
    for i, d in enumerate(dl):
        yr = d.year if hasattr(d, "year") else d
        if yr not in seen and (i == 0 or yr != dl[i-1].year):
            seen.add(yr)
            x = ml + (i / max(nd - 1, 1)) * pw
            svg += f'<text x="{x:.0f}" y="{vh-5}" text-anchor="middle" fill="#64748b" font-size="8">{yr}</text>'
            svg += f'<line x1="{x:.0f}" y1="{mt}" x2="{x:.0f}" y2="{vh-mb}" stroke="rgba(148,163,184,0.06)" stroke-width="0.5"/>'
    return svg, y_min, y_max

def build_main_chart():
    vw, vh = 720, 320
    nd = {"Dragon": nav_dragon, "6040": nav_6040, "SPY": nav_spy}
    g, ymn, ymx = svg_grid_and_labels(nd, dates, vw, vh)
    l = svg_line(nav_6040, dates, COLORS["6040"], 1.5, True, vw, vh, True, ymn, ymx)
    l += svg_line(nav_spy, dates, COLORS["SPY"], 1.5, True, vw, vh, True, ymn, ymx)
    l += svg_line(nav_dragon, dates, COLORS["Dragon"], 2.5, False, vw, vh, True, ymn, ymx)
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{g}{l}</svg>'

def build_component_chart():
    vw, vh = 720, 300
    nd = {c: nav_comp[c] for c in comp_names}
    g, ymn, ymx = svg_grid_and_labels(nd, dates, vw, vh)
    l = ""
    for c in comp_names:
        l += svg_line(nav_comp[c], dates, COLORS[c], 1.8, False, vw, vh, True, ymn, ymx)
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{g}{l}</svg>'

def build_type_chart():
    vw, vh = 720, 280
    nd = {"S": nav_serpent, "H": nav_hawk, "D": nav_dragon}
    g, ymn, ymx = svg_grid_and_labels(nd, dates, vw, vh)
    l = svg_line(nav_serpent, dates, "#10b981", 2.0, False, vw, vh, True, ymn, ymx)
    l += svg_line(nav_hawk, dates, "#f59e0b", 2.0, False, vw, vh, True, ymn, ymx)
    l += svg_line(nav_dragon, dates, COLORS["Dragon"], 2.5, True, vw, vh, True, ymn, ymx)
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{g}{l}</svg>'

def build_drawdown_chart():
    vw, vh = 720, 200
    def dd_s(r):
        n = np.cumprod(1+r); p = np.maximum.accumulate(n); return ((n-p)/p)*100
    dd1 = np.insert(dd_s(dragon_ret), 0, 0)
    dd2 = np.insert(dd_s(port_6040_ret), 0, 0)
    ymn = min(np.min(dd1), np.min(dd2)); ymx = 0
    ml, mr, mt, mb = 50, 15, 10, 25
    pw, ph = vw-ml-mr, vh-mt-mb
    yr = ymx - ymn if ymx > ymn else 1
    svg = ""
    for pct in [0,-10,-20,-30,-40,-50,-60]:
        if pct < ymn - 5: continue
        yp = mt + ph - ((pct-ymn)/yr)*ph
        svg += f'<line x1="{ml}" y1="{yp:.0f}" x2="{vw-mr}" y2="{yp:.0f}" stroke="rgba(148,163,184,0.12)" stroke-width="0.5"/>'
        svg += f'<text x="{ml-5}" y="{yp:.0f}" text-anchor="end" fill="#64748b" font-size="8" dominant-baseline="middle">{pct}%</text>'
    yz = mt
    svg += f'<line x1="{ml}" y1="{yz}" x2="{vw-mr}" y2="{yz}" stroke="rgba(148,163,184,0.25)" stroke-width="1"/>'
    def dp(dd, color, w, fo=0):
        pts = [f"{ml+(i/max(len(dd)-1,1))*pw:.1f},{mt+ph-((dd[i]-ymn)/yr)*ph:.1f}" for i in range(len(dd))]
        s = f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="{w}" stroke-linejoin="round"/>'
        if fo > 0:
            s += f'<polygon points="{ml},{yz} {" ".join(pts)} {ml+pw},{yz}" fill="{color}" opacity="{fo}"/>'
        return s
    svg += dp(dd2, COLORS["6040"], 1.2, 0.08)
    svg += dp(dd1, COLORS["Dragon"], 1.8, 0.12)
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

def build_exposure_chart():
    """Chart showing portfolio exposure % over time (SMA50 filter effect)."""
    vw, vh = 720, 180
    ml, mr, mt, mb = 50, 15, 10, 25
    pw, ph = vw-ml-mr, vh-mt-mb
    svg = ""
    for pct in [0, 25, 50, 75, 100]:
        yp = mt + ph - (pct/100)*ph
        svg += f'<line x1="{ml}" y1="{yp:.0f}" x2="{vw-mr}" y2="{yp:.0f}" stroke="rgba(148,163,184,0.12)" stroke-width="0.5"/>'
        svg += f'<text x="{ml-5}" y="{yp:.0f}" text-anchor="end" fill="#64748b" font-size="8" dominant-baseline="middle">{pct}%</text>'
    # Date labels
    nd = len(dates_ret)
    seen = set()
    for i, d in enumerate(dates_ret):
        yr = d.year
        if yr not in seen and (i == 0 or yr != dates_ret[i-1].year):
            seen.add(yr)
            x = ml + (i / max(nd - 1, 1)) * pw
            svg += f'<text x="{x:.0f}" y="{vh-5}" text-anchor="middle" fill="#64748b" font-size="8">{yr}</text>'
    # Area fill
    pts_top = []
    for i in range(N_ret):
        x = ml + (i / max(N_ret - 1, 1)) * pw
        y = mt + ph - (exposure_total[i]/100)*ph
        pts_top.append(f"{x:.1f},{y:.1f}")
    svg += f'<polygon points="{ml},{mt+ph} {" ".join(pts_top)} {ml+pw},{mt+ph}" fill="#fbbf24" opacity="0.15"/>'
    svg += f'<polyline points="{" ".join(pts_top)}" fill="none" stroke="#fbbf24" stroke-width="1.5" stroke-linejoin="round"/>'
    # Average line
    avg_y = mt + ph - (avg_exposure/100)*ph
    svg += f'<line x1="{ml}" y1="{avg_y:.0f}" x2="{vw-mr}" y2="{avg_y:.0f}" stroke="#ef4444" stroke-width="1" stroke-dasharray="4,3"/>'
    svg += f'<text x="{vw-mr+2}" y="{avg_y:.0f}" fill="#ef4444" font-size="8" dominant-baseline="middle">avg {avg_exposure:.0f}%</text>'
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

def build_donut():
    # Offset center to leave margin for labels on all sides
    cx, cy, r, ri = 120, 110, 70, 42
    comps = [("Equity",0.24,COLORS["Equity"]),("Bonds",0.18,COLORS["Bonds"]),
             ("HardAssets",0.19,COLORS["HardAssets"]),("LongVol",0.21,COLORS["LongVol"]),
             ("CmdtyTrend",0.18,COLORS["CmdtyTrend"])]
    LABELS = {"Equity":"Equity","Bonds":"Bonds","HardAssets":"Hard Assets",
              "LongVol":"Long Vol","CmdtyTrend":"Cmdty Trend"}
    svg = ""; angle = -90
    for name, w, color in comps:
        sweep = w * 360
        a1_rad = math.radians(angle)
        a2_rad = math.radians(angle + sweep)
        x1o = cx + r * math.cos(a1_rad)
        y1o = cy + r * math.sin(a1_rad)
        x2o = cx + r * math.cos(a2_rad)
        y2o = cy + r * math.sin(a2_rad)
        x1i = cx + ri * math.cos(a2_rad)
        y1i = cy + ri * math.sin(a2_rad)
        x2i = cx + ri * math.cos(a1_rad)
        y2i = cy + ri * math.sin(a1_rad)
        lg = 1 if sweep > 180 else 0
        svg += (f'<path d="M {x1o:.1f},{y1o:.1f} '
                f'A {r},{r} 0 {lg},1 {x2o:.1f},{y2o:.1f} '
                f'L {x1i:.1f},{y1i:.1f} '
                f'A {ri},{ri} 0 {lg},0 {x2i:.1f},{y2i:.1f} Z" '
                f'fill="{color}" stroke="#0f172a" stroke-width="1.5"/>')
        # Label outside the donut with name + percentage
        ma = math.radians(angle + sweep / 2)
        lx = cx + (r + 22) * math.cos(ma)
        ly = cy + (r + 22) * math.sin(ma)
        pct_text = f"{LABELS[name]} {int(w*100)}%"
        svg += f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle" dominant-baseline="central" fill="{color}" font-size="7" font-weight="600">{pct_text}</text>'
        angle += sweep
    svg += f'<text x="{cx}" y="{cy-5}" text-anchor="middle" fill="#e2e8f0" font-size="9" font-weight="700">IBERIC</text>'
    svg += f'<text x="{cx}" y="{cy+8}" text-anchor="middle" fill="#94a3b8" font-size="6">CENTINEL</text>'
    return f'<svg viewBox="0 0 240 220" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

def build_correlation_heatmap():
    labels = comp_names
    display = ["Equity", "Bonds", "Hard Assets", "Long Vol", "Cmdty Trend"]
    rows = "<tr><th></th>" + "".join(f'<th class="corr-th">{d}</th>' for d in display) + "</tr>"
    for i in range(5):
        rows += f'<tr><td class="corr-label" style="color:{COLORS[labels[i]]}">{display[i]}</td>'
        for j in range(5):
            v = corr_matrix[i,j]
            if i==j: bg,clr = "rgba(148,163,184,0.15)","#e2e8f0"
            elif v>0.3: bg,clr = f"rgba(239,68,68,{min(abs(v)*0.5,0.4):.2f})","#fca5a5"
            elif v<-0.15: bg,clr = f"rgba(16,185,129,{min(abs(v)*0.6,0.4):.2f})","#6ee7b7"
            else: bg,clr = "rgba(148,163,184,0.06)","#94a3b8"
            rows += f'<td class="corr-cell" style="background:{bg};color:{clr}">{v:.2f}</td>'
        rows += "</tr>"
    return f'<table class="corr-table">{rows}</table>'

# ═══════════════════════════════════════════════════════════════════
# 10. HELPER FORMATTERS
# ═══════════════════════════════════════════════════════════════════
def pct_cls(v):
    if isinstance(v, float) and np.isnan(v): return ""
    return "pos" if v > 0 else "neg" if v < 0 else ""

def regime_rows():
    rows = ""
    for regime in ["Bull", "Bear", "Flat"]:
        if regime not in regime_stats: continue
        rs = regime_stats[regime]
        sym = "+" if regime=="Bull" else "-" if regime=="Bear" else "="
        clr = "#10b981" if regime=="Bull" else "#ef4444" if regime=="Bear" else "#94a3b8"
        rows += f'<tr><td style="color:{clr};font-weight:700">{sym} {regime}</td><td class="num">{rs["days"]}</td>'
        for p in ["Dragon","6040","Serpent","Hawk"]:
            rows += f'<td class="num {pct_cls(rs[p]["cagr"])}">{rs[p]["cagr"]*100:+.1f}%</td>'
        rows += '</tr>'
    return rows

def annual_rows():
    rows = ""
    for yr in sorted(annual_table.keys()):
        a = annual_table[yr]
        rows += f'<tr><td>{yr}</td>'
        for p in ["Dragon","6040","SPY","Serpent","Hawk"]:
            fw = "font-weight:600;" if p=="Dragon" else ""
            rows += f'<td class="num {pct_cls(a[p]/100)}" style="{fw}">{a[p]:+.1f}%</td>'
        rows += '</tr>'
    return rows

def build_momentum_scorecard(block, tickers):
    header = '<tr><th>Mes</th>'
    for t in tickers:
        c = TICKER_COLORS.get(t, "#94a3b8")
        lbl = TICKER_LABELS.get(t, t)
        header += f'<th class="num" style="color:{c};font-size:7px">{t}<br><span style="color:#475569">{lbl}</span></th>'
    header += '<th class="num" style="font-size:7px;color:#fbbf24">SMA50</th>'
    header += '</tr>'
    rows = ""
    log_idx = 0
    for entry in selection_log:
        d = entry["date"]
        if block not in entry:
            continue
        data = entry[block]
        picks = data["picks"]
        scores = data["scores"]
        # Find the return index for this date
        ret_i = None
        for ri in range(N_ret):
            if dates_ret[ri] == d:
                ret_i = ri
                break
        rows += f'<tr><td style="color:#64748b;white-space:nowrap;font-size:9px">{d.strftime("%Y-%m")}</td>'
        sma_info = []
        for t in tickers:
            s = scores.get(t, np.nan)
            picked = t in picks
            above = (sma_exit_above[t][ret_i] if t in sma_exit_above else True) if ret_i is not None else True
            if picked:
                sma_info.append((t, above))
            if np.isnan(s):
                rows += '<td class="num" style="color:#334155;font-size:9px">--</td>'
            else:
                bg = "rgba(251,191,36,0.10)" if picked else "transparent"
                if picked and not above:
                    bg = "rgba(239,68,68,0.15)"
                clr = ("#fbbf24" if s>=0 else "#f87171") if picked else ("#10b981" if s>0 else "#ef4444" if s<-0.05 else "#64748b")
                if picked and not above:
                    clr = "#ef4444"
                fw = "700" if picked else "400"
                bd = "border:1px solid rgba(251,191,36,0.3);" if picked and above else ("border:1px solid rgba(239,68,68,0.3);" if picked else "")
                rows += f'<td class="num" style="background:{bg};color:{clr};font-weight:{fw};font-size:9px;{bd}">{s*100:+.1f}%</td>'
        # SMA50 column: show exposure
        if sma_info:
            above_count = sum(1 for _, a in sma_info if a)
            total = len(sma_info)
            exp_pct = above_count / total * 100
            exp_clr = "#10b981" if exp_pct == 100 else "#f59e0b" if exp_pct > 0 else "#ef4444"
            rows += f'<td class="num" style="color:{exp_clr};font-size:9px;font-weight:700">{exp_pct:.0f}%</td>'
        else:
            rows += '<td class="num" style="color:#64748b;font-size:9px">--</td>'
        rows += '</tr>'
    return f'{header}{rows}'

# Selection frequencies
block_freq = {}
for block in ["Equity", "Bonds", "HardAssets"]:
    freq = {}
    for entry in selection_log:
        if block in entry:
            for t in entry[block]["picks"]:
                freq[t] = freq.get(t, 0) + 1
    block_freq[block] = freq

def selection_freq_html(freq_dict):
    total = len(selection_log)
    items = sorted(freq_dict.items(), key=lambda x: -x[1])
    html = ""
    for ticker, count in items:
        pct = count / total * 100
        color = TICKER_COLORS.get(ticker, "#94a3b8")
        label = TICKER_LABELS.get(ticker, ticker)
        html += f'''<div style="display:flex;align-items:center;gap:6px;margin:3px 0">
            <span style="width:65px;font-size:8px;font-weight:700;color:{color}">{ticker}</span>
            <span style="width:65px;font-size:8px;color:#64748b">{label}</span>
            <div style="flex:1;height:14px;background:rgba(148,163,184,0.06);border-radius:3px;overflow:hidden">
                <div style="width:{pct:.0f}%;height:100%;background:{color};opacity:0.5;border-radius:3px"></div>
            </div>
            <span style="width:60px;text-align:right;font-size:9px;color:#94a3b8">{count}m ({pct:.0f}%)</span>
        </div>'''
    return html

# ═══════════════════════════════════════════════════════════════════
# 11. UCITS MAPPING TABLE
# ═══════════════════════════════════════════════════════════════════
def build_ucits_mapping_html():
    rows = ""
    for us, ucits in sorted(US_TO_UCITS.items(), key=lambda x: x[0]):
        label = TICKER_LABELS.get(ucits, ucits)
        color = TICKER_COLORS.get(ucits, "#94a3b8")
        available = "si" if ucits in price_data else "no"
        avail_color = "#10b981" if ucits in price_data else "#ef4444"
        rows += f'<tr><td style="color:#94a3b8">{us}</td><td style="color:{color};font-weight:700">{ucits}</td><td style="color:#64748b">{label}</td><td style="color:{avail_color};text-align:center">{available}</td></tr>'
    return rows

# ═══════════════════════════════════════════════════════════════════
# 12. HTML REPORT
# ═══════════════════════════════════════════════════════════════════
print("\nGenerating HTML report...")

def universe_tags(block):
    tags = ""
    for t in UNIVERSES[block]:
        c = TICKER_COLORS.get(t, "#94a3b8")
        tags += f'<span class="comp-tag" style="background:rgba(148,163,184,0.08);color:{c}">{t}</span> '
    return tags

longvol_desc = f"{longvol_source}"
if "Synthetic" in str(longvol_source):
    longvol_note = '<div style="margin-top:4px;font-size:8px;color:#f97316;font-weight:600">JPM Abs Alpha no disponible — usando proxy sintetico (inv. S&P500 x0.3)</div>'
else:
    longvol_note = f'<div style="margin-top:4px;font-size:8px;color:#64748b">JPM Europe Eq Absolute Alpha D (LU1176912761) — L/S Equity Market Neutral</div>'

html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Iberic Centinel — Dragon UCITS + JPM + SMA50 | SFinance</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Inter', -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; }}
.container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}
.header {{ display:flex; justify-content:space-between; align-items:center; padding:16px 0; border-bottom:1px solid rgba(148,163,184,0.12); margin-bottom:24px; }}
.header-title {{ font-size:22px; font-weight:800; letter-spacing:-0.5px; }}
.header-title span {{ color:#fbbf24; }}
.header-sub {{ font-size:11px; color:#64748b; text-align:right; }}
.header-sub strong {{ color:#94a3b8; }}
.alloc-section {{ display:grid; grid-template-columns: 200px 1fr; gap:24px; align-items:center; margin-bottom:28px; padding:20px; background:rgba(30,41,59,0.5); border:1px solid rgba(148,163,184,0.08); border-radius:8px; }}
.alloc-desc {{ font-size:10px; color:#94a3b8; line-height:1.6; }}
.alloc-desc .type-label {{ display:inline-block; padding:2px 8px; border-radius:3px; font-weight:700; font-size:9px; margin-right:6px; }}
.serpent-label {{ background:rgba(16,185,129,0.15); color:#10b981; }}
.hawk-label {{ background:rgba(245,158,11,0.15); color:#f59e0b; }}
.kpi-strip {{ display:grid; grid-template-columns:repeat(8, 1fr); gap:8px; margin-bottom:24px; }}
.kpi {{ background:rgba(30,41,59,0.6); border:1px solid rgba(148,163,184,0.08); border-radius:6px; padding:12px 8px; text-align:center; }}
.kpi-label {{ font-size:8px; text-transform:uppercase; color:#64748b; letter-spacing:0.5px; margin-bottom:4px; }}
.kpi-value {{ font-size:18px; font-weight:800; letter-spacing:-0.5px; }}
.kpi-sub {{ font-size:8px; color:#475569; margin-top:2px; }}
.pos {{ color:#10b981; }} .neg {{ color:#ef4444; }}
.chart-container {{ background:rgba(15,23,42,0.5); border:1px solid rgba(148,163,184,0.06); border-radius:6px; padding:12px; margin-bottom:8px; }}
.section-title {{ font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:0.8px; color:#94a3b8; margin-bottom:12px; padding-left:2px; }}
.legend-row {{ display:flex; gap:16px; justify-content:center; padding:8px 0; flex-wrap:wrap; }}
.legend-item {{ display:flex; align-items:center; gap:5px; font-size:9px; color:#94a3b8; }}
.legend-dot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }}
.grid-2 {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:24px; }}
.grid-3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; margin-bottom:24px; }}
.section {{ margin-bottom:24px; }}
.card {{ background:rgba(30,41,59,0.4); border:1px solid rgba(148,163,184,0.08); border-radius:8px; padding:16px; }}
.type-card {{ border-radius:8px; padding:16px; }}
.type-card.serpent {{ background:rgba(16,185,129,0.04); border:1px solid rgba(16,185,129,0.15); }}
.type-card.hawk {{ background:rgba(245,158,11,0.04); border:1px solid rgba(245,158,11,0.15); }}
.type-header {{ font-size:14px; font-weight:800; margin-bottom:8px; }}
.type-kpis {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin:12px 0; }}
.type-kpi {{ text-align:center; }}
.type-kpi .tk-label {{ font-size:7px; text-transform:uppercase; color:#64748b; }}
.type-kpi .tk-value {{ font-size:15px; font-weight:700; }}
.type-components {{ font-size:9px; color:#64748b; line-height:1.7; }}
.type-components .comp-tag {{ display:inline-block; padding:1px 6px; border-radius:3px; margin:1px 2px; font-weight:600; }}
.data-table {{ width:100%; border-collapse:collapse; font-size:10px; }}
.data-table th {{ background:rgba(30,41,59,0.8); color:#94a3b8; font-weight:600; text-transform:uppercase; font-size:8px; letter-spacing:0.5px; padding:8px 6px; text-align:left; border-bottom:1px solid rgba(148,163,184,0.12); position:sticky; top:0; z-index:1; }}
.data-table td {{ padding:6px; border-bottom:1px solid rgba(148,163,184,0.06); }}
.data-table .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.data-table tr:hover {{ background:rgba(148,163,184,0.04); }}
.table-scroll {{ overflow-x:auto; max-height:400px; overflow-y:auto; }}
.corr-table {{ width:100%; border-collapse:collapse; font-size:10px; }}
.corr-table th, .corr-table td {{ padding:8px 6px; text-align:center; }}
.corr-th {{ font-size:8px; text-transform:uppercase; color:#94a3b8; font-weight:600; }}
.corr-label {{ font-size:9px; font-weight:700; text-align:left !important; }}
.corr-cell {{ font-weight:600; font-variant-numeric:tabular-nums; border-radius:3px; }}
.ucits-badge {{ display:inline-block; padding:3px 10px; border-radius:4px; font-size:9px; font-weight:700; background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.3); }}
.sma-badge {{ display:inline-block; padding:3px 10px; border-radius:4px; font-size:9px; font-weight:700; background:rgba(251,191,36,0.15); color:#fbbf24; border:1px solid rgba(251,191,36,0.3); }}
.footer {{ display:flex; justify-content:space-between; font-size:8px; color:#475569; padding:16px 0; border-top:1px solid rgba(148,163,184,0.08); margin-top:20px; }}
@media (max-width: 900px) {{ .kpi-strip {{ grid-template-columns:repeat(4, 1fr); }} .grid-2,.grid-3 {{ grid-template-columns:1fr; }} .alloc-section {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <div class="header-title"><span>Iberic Centinel</span> — Dragon UCITS + SMA50 Exit</div>
      <div style="font-size:10px;color:#64748b;margin-top:2px">
        <span class="ucits-badge">UCITS / PRIIPs</span>
        <span class="sma-badge">SMA50 EXIT</span>
        Artemis Capital (2020) | JPM Abs Alpha + ETFs europeos | {len(ALL_TICKERS)} activos
      </div>
    </div>
    <div class="header-sub"><strong>SFinance-alicIA</strong><br>{TODAY.strftime("%d %b %Y")} | {dates[0]} &rarr; {dates[-1]}</div>
  </div>

  <div class="card" style="margin-bottom:24px;border-left:3px solid #fbbf24;padding:16px 20px">
    <div style="font-size:12px;font-weight:800;color:#fbbf24;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px">Iberic Centinel — Dragon Portfolio + JPM Absolute Alpha + SMA50 Exit</div>
    <div style="font-size:10px;color:#cbd5e1;line-height:1.8">
      <p>Combina el <strong style="color:#e2e8f0">Dragon Portfolio</strong> (Artemis Capital) con <strong style="color:#60a5fa">ETFs UCITS</strong> y el fondo <strong style="color:#fbbf24">JPM Europe Equity Absolute Alpha D (Perf) Acc EUR</strong> (ISIN: LU1176912761) como sustituto de BTAL en el bloque Long Volatility.</p>
      <p style="margin-top:6px"><strong style="color:#06b6d4">Doble SMA (Centinela v3):</strong> <strong style="color:#e2e8f0">SMA200</strong> escala la exposicion por bloque (30%-100%) segun cuantos picks estan sobre su SMA200. <strong style="color:#fbbf24">SMA{SMA_EXIT}</strong> genera senales de salida intra-mes a <strong style="color:#e2e8f0">{shy_ucits}</strong> (bonos cortos) en bloques Equity y Hard Assets, con {TX_COST_BPS}bp por switch.</p>
      <p style="margin-top:6px"><strong style="color:#10b981">Exposicion media:</strong> {avg_exposure:.0f}% | Floor: {MIN_EXPOSURE:.0%} minimo (nunca 0%). Cash → {shy_ucits} (~bonos cortos, no 0%).</p>
    </div>
  </div>

  <div class="alloc-section">
    <div style="text-align:center">{build_donut()}</div>
    <div class="alloc-desc">
      <div style="margin-bottom:8px;font-size:12px;font-weight:700;color:#e2e8f0">Diversificacion secular + Momentum top-{N_SELECT} + SMA50 Exit (UCITS)</div>
      <p style="margin-bottom:8px">Cada mes se eligen los <strong style="color:#fbbf24">{N_SELECT} mejores</strong> por retorno trailing {MOM_LOOKBACK}d. <strong style="color:#06b6d4">SMA200</strong> escala exposicion. <strong style="color:#fbbf24">SMA{SMA_EXIT}</strong> exit intra-mes → {shy_ucits}.</p>
      <div><span class="type-label serpent-label">SERPIENTE 42%</span> Equity (24%): {len(UNIVERSES["Equity"])} cand. | Bonds (18%): {len(UNIVERSES["Bonds"])} cand.</div>
      <div style="margin-top:4px"><span class="type-label hawk-label">HALCON 58%</span> Hard Assets (19%): {len(UNIVERSES["HardAssets"])} cand. | Long Vol (21%): JPM Abs Alpha | Cmdty Trend (18%)</div>
    </div>
  </div>

  <div class="kpi-strip">
    <div class="kpi"><div class="kpi-label">CAGR</div><div class="kpi-value {pct_cls(m_dragon['cagr'])}">{m_dragon['cagr']*100:+.1f}%</div><div class="kpi-sub">60/40: {m_6040['cagr']*100:+.1f}%</div></div>
    <div class="kpi"><div class="kpi-label">Sharpe</div><div class="kpi-value" style="color:#fbbf24">{m_dragon['sharpe']:.2f}</div><div class="kpi-sub">60/40: {m_6040['sharpe']:.2f}</div></div>
    <div class="kpi"><div class="kpi-label">Sortino</div><div class="kpi-value" style="color:#fbbf24">{m_dragon['sortino']:.2f}</div><div class="kpi-sub">60/40: {m_6040['sortino']:.2f}</div></div>
    <div class="kpi"><div class="kpi-label">Max Drawdown</div><div class="kpi-value neg">{m_dragon['mdd']:.1f}%</div><div class="kpi-sub">60/40: {m_6040['mdd']:.1f}%</div></div>
    <div class="kpi"><div class="kpi-label">Volatilidad</div><div class="kpi-value">{m_dragon['vol']*100:.1f}%</div><div class="kpi-sub">60/40: {m_6040['vol']*100:.1f}%</div></div>
    <div class="kpi"><div class="kpi-label">Calmar</div><div class="kpi-value">{m_dragon['calmar']:.2f}</div><div class="kpi-sub">60/40: {m_6040['calmar']:.2f}</div></div>
    <div class="kpi"><div class="kpi-label">Exposicion</div><div class="kpi-value" style="color:#fbbf24">{avg_exposure:.0f}%</div><div class="kpi-sub">SMA50 filter</div></div>
    <div class="kpi"><div class="kpi-label">Periodo</div><div class="kpi-value" style="font-size:14px;color:#94a3b8">{m_dragon['years']:.1f}y</div><div class="kpi-sub">{N_ret} trading days</div></div>
  </div>

  <div class="section">
    <div class="section-title">Crecimiento de $1 — Escala Logaritmica</div>
    <div class="chart-container">{build_main_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:#fbbf24"></div><strong style="color:#fbbf24">Iberic Centinel</strong> ${nav_dragon[-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#94a3b8"></div>60/40 ${nav_6040[-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#3b82f6"></div>{spy_ucits} ${nav_spy[-1]:.2f}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Exposicion al Mercado (%) — Efecto Filtro SMA50</div>
    <div class="chart-container">{build_exposure_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:#fbbf24"></div><span style="color:#fbbf24">Exposicion total</span></div>
      <div class="legend-item"><svg width="20" height="8"><line x1="0" y1="4" x2="20" y2="4" stroke="#ef4444" stroke-width="1" stroke-dasharray="4,3"/></svg><span style="color:#ef4444">Media: {avg_exposure:.0f}%</span></div>
    </div>
  </div>

  <div class="grid-2">
    <div class="type-card serpent">
      <div class="type-header" style="color:#10b981">Serpiente <span style="font-size:10px;font-weight:400;color:#64748b">— Crecimiento Secular (42%)</span></div>
      <div class="type-kpis">
        <div class="type-kpi"><div class="tk-label">CAGR</div><div class="tk-value {pct_cls(m_serpent['cagr'])}">{m_serpent['cagr']*100:+.1f}%</div></div>
        <div class="type-kpi"><div class="tk-label">Sharpe</div><div class="tk-value" style="color:#10b981">{m_serpent['sharpe']:.2f}</div></div>
        <div class="type-kpi"><div class="tk-label">Max DD</div><div class="tk-value neg">{m_serpent['mdd']:.1f}%</div></div>
      </div>
      <div class="type-components">
        <div style="margin-bottom:4px"><span class="comp-tag" style="background:rgba(16,185,129,0.15);color:#10b981">Equity 24%</span> Top-{N_SELECT} de: {universe_tags("Equity")}</div>
        <div><span class="comp-tag" style="background:rgba(6,182,212,0.15);color:#06b6d4">Bonds 18%</span> Top-{N_SELECT} de: {universe_tags("Bonds")}</div>
      </div>
    </div>
    <div class="type-card hawk">
      <div class="type-header" style="color:#f59e0b">Halcon <span style="font-size:10px;font-weight:400;color:#64748b">— Cambio Secular (58%)</span></div>
      <div class="type-kpis">
        <div class="type-kpi"><div class="tk-label">CAGR</div><div class="tk-value {pct_cls(m_hawk['cagr'])}">{m_hawk['cagr']*100:+.1f}%</div></div>
        <div class="type-kpi"><div class="tk-label">Sharpe</div><div class="tk-value" style="color:#f59e0b">{m_hawk['sharpe']:.2f}</div></div>
        <div class="type-kpi"><div class="tk-label">Max DD</div><div class="tk-value neg">{m_hawk['mdd']:.1f}%</div></div>
      </div>
      <div class="type-components">
        <div style="margin-bottom:4px"><span class="comp-tag" style="background:rgba(245,158,11,0.15);color:#f59e0b">Hard Assets 19%</span> Top-{N_SELECT} de: {universe_tags("HardAssets")}</div>
        <span class="comp-tag" style="background:rgba(239,68,68,0.15);color:#ef4444">Long Vol 21%</span> {longvol_desc}
        {longvol_note}
        <span class="comp-tag" style="background:rgba(168,85,247,0.15);color:#a855f7">Cmdty Trend 18%</span> {cmdty_ticker or 'N/A'} + SMA200+SMA{SMA_CMDTY}
      </div>
    </div>
  </div>

  <div class="grid-3">
    <div class="card"><div class="section-title">Frecuencia — Equity</div>{selection_freq_html(block_freq["Equity"])}</div>
    <div class="card"><div class="section-title">Frecuencia — Bonds</div>{selection_freq_html(block_freq["Bonds"])}</div>
    <div class="card"><div class="section-title">Frecuencia — Hard Assets</div>{selection_freq_html(block_freq["HardAssets"])}</div>
  </div>

  <div class="section">
    <div class="section-title">Momentum Scorecard — Hard Assets | Dorado = seleccionado sobre SMA50 | Rojo = bajo SMA50 (cash)</div>
    <div class="table-scroll" style="max-height:350px"><table class="data-table">{build_momentum_scorecard("HardAssets", UNIVERSES["HardAssets"])}</table></div>
  </div>

  <div class="section">
    <div class="section-title">Momentum Scorecard — Equity</div>
    <div class="table-scroll" style="max-height:350px"><table class="data-table">{build_momentum_scorecard("Equity", UNIVERSES["Equity"])}</table></div>
  </div>

  <div class="section">
    <div class="section-title">Momentum Scorecard — Bonds</div>
    <div class="table-scroll" style="max-height:300px"><table class="data-table">{build_momentum_scorecard("Bonds", UNIVERSES["Bonds"])}</table></div>
  </div>

  <div class="section">
    <div class="section-title">Serpiente vs Halcon</div>
    <div class="chart-container">{build_type_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:#10b981"></div><span style="color:#10b981">Serpiente</span> ${nav_serpent[-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f59e0b"></div><span style="color:#f59e0b">Halcon</span> ${nav_hawk[-1]:.2f}</div>
      <div class="legend-item"><svg width="20" height="8"><line x1="0" y1="4" x2="20" y2="4" stroke="#fbbf24" stroke-width="2" stroke-dasharray="4,3"/></svg><span style="color:#fbbf24">Iberic Centinel</span> ${nav_dragon[-1]:.2f}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Componentes Individuales</div>
    <div class="chart-container">{build_component_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['Equity']}"></div><span style="color:{COLORS['Equity']}">Equity</span> ${nav_comp['Equity'][-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['Bonds']}"></div><span style="color:{COLORS['Bonds']}">Bonds</span> ${nav_comp['Bonds'][-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['HardAssets']}"></div><span style="color:{COLORS['HardAssets']}">Hard Assets</span> ${nav_comp['HardAssets'][-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['LongVol']}"></div><span style="color:{COLORS['LongVol']}">Long Vol</span> ${nav_comp['LongVol'][-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['CmdtyTrend']}"></div><span style="color:{COLORS['CmdtyTrend']}">Cmdty Trend</span> ${nav_comp['CmdtyTrend'][-1]:.2f}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Drawdown — Iberic Centinel vs 60/40</div>
    <div class="chart-container">{build_drawdown_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:#fbbf24"></div><span style="color:#fbbf24">Iberic Centinel</span> MDD: {m_dragon['mdd']:.1f}%</div>
      <div class="legend-item"><div class="legend-dot" style="background:#94a3b8"></div><span style="color:#94a3b8">60/40</span> MDD: {m_6040['mdd']:.1f}%</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="section-title">Performance por Regimen (CAGR)</div>
      <table class="data-table">
        <tr><th>Regimen</th><th class="num">Dias</th><th class="num" style="color:#fbbf24">Iberic</th><th class="num">60/40</th><th class="num" style="color:#10b981">Serpiente</th><th class="num" style="color:#f59e0b">Halcon</th></tr>
        {regime_rows()}
      </table>
    </div>
    <div class="card">
      <div class="section-title">Correlacion entre Componentes</div>
      {build_correlation_heatmap()}
    </div>
  </div>

  <div class="section">
    <div class="section-title">Retornos Anuales</div>
    <table class="data-table">
      <tr><th>Ano</th><th class="num" style="color:#fbbf24">Iberic</th><th class="num">60/40</th><th class="num" style="color:#3b82f6">{spy_ucits}</th><th class="num" style="color:#10b981">Serpiente</th><th class="num" style="color:#f59e0b">Halcon</th></tr>
      {annual_rows()}
    </table>
  </div>

  <div class="section">
    <div class="section-title">Periodos de Stress</div>
    <table class="data-table">
      <tr>
        <th>Evento</th><th>Periodo</th>
        <th class="num" style="color:#fbbf24">Iberic</th>
        <th class="num">60/40</th>
        <th class="num" style="color:#3b82f6">{spy_ucits}</th>
        <th class="num" style="color:#10b981">Serpiente</th>
        <th class="num" style="color:#f59e0b">Halcon</th>
      </tr>
      {''.join(
          f'<tr>'
          f'<td style="font-weight:700;white-space:nowrap">{sr["name"]}</td>'
          f'<td style="color:#64748b;font-size:8px;white-space:nowrap">{sr["start"]} &rarr; {sr["end"]}</td>'
          f'<td class="num" style="font-weight:700;color:{"#10b981" if sr["Dragon"]>=0 else "#ef4444"}">{sr["Dragon"]:+.1f}%</td>'
          f'<td class="num" style="color:{"#10b981" if sr["6040"]>=0 else "#ef4444"}">{sr["6040"]:+.1f}%</td>'
          f'<td class="num" style="color:{"#10b981" if sr["SPY"]>=0 else "#ef4444"}">{sr["SPY"]:+.1f}%</td>'
          f'<td class="num" style="color:{"#10b981" if sr["Serpent"]>=0 else "#ef4444"}">{sr["Serpent"]:+.1f}%</td>'
          f'<td class="num" style="color:{"#10b981" if sr["Hawk"]>=0 else "#ef4444"}">{sr["Hawk"]:+.1f}%</td>'
          f'</tr>'
          for sr in stress_results
      )}
    </table>
  </div>

  <div class="section">
    <div class="section-title">Estadisticas Completas</div>
    <div class="table-scroll">
    <table class="data-table">
      <tr><th>Metrica</th><th class="num" style="color:#fbbf24">Iberic</th><th class="num">60/40</th><th class="num" style="color:#3b82f6">{spy_ucits}</th><th class="num" style="color:{COLORS['Equity']}">Equity</th><th class="num" style="color:{COLORS['Bonds']}">Bonds</th><th class="num" style="color:{COLORS['HardAssets']}">Hard Assets</th><th class="num" style="color:{COLORS['LongVol']}">Long Vol</th><th class="num" style="color:{COLORS['CmdtyTrend']}">Cmdty Trend</th></tr>
      <tr><td>CAGR</td><td class="num" style="font-weight:700;color:#fbbf24">{m_dragon['cagr']*100:+.1f}%</td><td class="num">{m_6040['cagr']*100:+.1f}%</td><td class="num">{m_spy['cagr']*100:+.1f}%</td><td class="num">{m_comp['Equity']['cagr']*100:+.1f}%</td><td class="num">{m_comp['Bonds']['cagr']*100:+.1f}%</td><td class="num">{m_comp['HardAssets']['cagr']*100:+.1f}%</td><td class="num">{m_comp['LongVol']['cagr']*100:+.1f}%</td><td class="num">{m_comp['CmdtyTrend']['cagr']*100:+.1f}%</td></tr>
      <tr><td>Volatilidad</td><td class="num" style="font-weight:700">{m_dragon['vol']*100:.1f}%</td><td class="num">{m_6040['vol']*100:.1f}%</td><td class="num">{m_spy['vol']*100:.1f}%</td><td class="num">{m_comp['Equity']['vol']*100:.1f}%</td><td class="num">{m_comp['Bonds']['vol']*100:.1f}%</td><td class="num">{m_comp['HardAssets']['vol']*100:.1f}%</td><td class="num">{m_comp['LongVol']['vol']*100:.1f}%</td><td class="num">{m_comp['CmdtyTrend']['vol']*100:.1f}%</td></tr>
      <tr><td>Sharpe</td><td class="num" style="font-weight:700;color:#fbbf24">{m_dragon['sharpe']:.2f}</td><td class="num">{m_6040['sharpe']:.2f}</td><td class="num">{m_spy['sharpe']:.2f}</td><td class="num">{m_comp['Equity']['sharpe']:.2f}</td><td class="num">{m_comp['Bonds']['sharpe']:.2f}</td><td class="num">{m_comp['HardAssets']['sharpe']:.2f}</td><td class="num">{m_comp['LongVol']['sharpe']:.2f}</td><td class="num">{m_comp['CmdtyTrend']['sharpe']:.2f}</td></tr>
      <tr><td>Sortino</td><td class="num" style="font-weight:700">{m_dragon['sortino']:.2f}</td><td class="num">{m_6040['sortino']:.2f}</td><td class="num">{m_spy['sortino']:.2f}</td><td class="num">{m_comp['Equity']['sortino']:.2f}</td><td class="num">{m_comp['Bonds']['sortino']:.2f}</td><td class="num">{m_comp['HardAssets']['sortino']:.2f}</td><td class="num">{m_comp['LongVol']['sortino']:.2f}</td><td class="num">{m_comp['CmdtyTrend']['sortino']:.2f}</td></tr>
      <tr><td>Max DD</td><td class="num neg" style="font-weight:700">{m_dragon['mdd']:.1f}%</td><td class="num neg">{m_6040['mdd']:.1f}%</td><td class="num neg">{m_spy['mdd']:.1f}%</td><td class="num neg">{m_comp['Equity']['mdd']:.1f}%</td><td class="num neg">{m_comp['Bonds']['mdd']:.1f}%</td><td class="num neg">{m_comp['HardAssets']['mdd']:.1f}%</td><td class="num neg">{m_comp['LongVol']['mdd']:.1f}%</td><td class="num neg">{m_comp['CmdtyTrend']['mdd']:.1f}%</td></tr>
      <tr><td>Calmar</td><td class="num" style="font-weight:700">{m_dragon['calmar']:.2f}</td><td class="num">{m_6040['calmar']:.2f}</td><td class="num">{m_spy['calmar']:.2f}</td><td class="num">{m_comp['Equity']['calmar']:.2f}</td><td class="num">{m_comp['Bonds']['calmar']:.2f}</td><td class="num">{m_comp['HardAssets']['calmar']:.2f}</td><td class="num">{m_comp['LongVol']['calmar']:.2f}</td><td class="num">{m_comp['CmdtyTrend']['calmar']:.2f}</td></tr>
      <tr><td>Total Return</td><td class="num" style="font-weight:700;color:#fbbf24">{m_dragon['total']:+.0f}%</td><td class="num">{m_6040['total']:+.0f}%</td><td class="num">{m_spy['total']:+.0f}%</td><td class="num">{m_comp['Equity']['total']:+.0f}%</td><td class="num">{m_comp['Bonds']['total']:+.0f}%</td><td class="num">{m_comp['HardAssets']['total']:+.0f}%</td><td class="num">{m_comp['LongVol']['total']:+.0f}%</td><td class="num">{m_comp['CmdtyTrend']['total']:+.0f}%</td></tr>
    </table>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Mapping US ETF → UCITS Equivalente</div>
    <table class="data-table">
      <tr><th>US ETF</th><th>UCITS Ticker</th><th>Descripcion</th><th class="num">Datos?</th></tr>
      {build_ucits_mapping_html()}
    </table>
  </div>

  <div class="card" style="margin-bottom:20px">
    <div class="section-title">Metodologia</div>
    <div style="font-size:9px;color:#94a3b8;line-height:1.7;columns:2;column-gap:24px">
      <p><strong style="color:#e2e8f0">Momentum {MOM_LOOKBACK}d</strong> — price[t] / price[t-{MOM_LOOKBACK}] - 1. Top-{N_SELECT} por bloque, equal-weight.</p>
      <p style="margin-top:6px"><strong style="color:#06b6d4">Doble SMA</strong> — SMA200 escala exposicion por bloque ({MIN_EXPOSURE:.0%}-100%). SMA{SMA_EXIT} exit intra-mes en Equity/HardAssets → {shy_ucits}. Reset en rebalanceo. Costes: {TX_COST_BPS}bp/switch.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Equity (24%)</strong> — {len(UNIVERSES["Equity"])} candidatos: {", ".join(UNIVERSES["Equity"])}.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Bonds (18%)</strong> — {len(UNIVERSES["Bonds"])} candidatos: {", ".join(UNIVERSES["Bonds"])}.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Hard Assets (19%)</strong> — {len(UNIVERSES["HardAssets"])} candidatos: {", ".join(UNIVERSES["HardAssets"])}.</p>
      <p style="margin-top:6px"><strong style="color:#ef4444">Long Vol (21%)</strong> — JPM Europe Eq Absolute Alpha D (LU1176912761). Fondo L/S equity market neutral.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Cmdty Trend (18%)</strong> — {cmdty_ticker or 'N/A'} + SMA200+SMA{SMA_CMDTY}.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">60/40 Benchmark</strong> — {spy_ucits} + {tlt_ucits} (sin filtro SMA50).</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Rf</strong> — {RF_ANNUAL*100:.1f}%.</p>
      <p style="margin-top:8px"><strong style="color:#f97316">Limitaciones:</strong> (1) JPM Abs Alpha puede tener datos limitados en Yahoo Finance. (2) Costes de transaccion estimados ({TX_COST_BPS}bps/trade). (3) Mezcla de divisas (EUR/USD/GBP). (4) SMA50 puede generar whipsaws en mercados laterales. (5) Hard Assets limitado a 3 candidatos (Oro, Plata, Cobre).</p>
    </div>
  </div>

  <div class="footer">
    <span>SFinance-alicIA | Iberic Centinel | Solo fines informativos — No es asesoramiento financiero</span>
    <span>{TODAY.strftime("%Y-%m-%d")} | {len(ALL_TICKERS)} activos UCITS | SMA50 Exit | Backtest {dates[0]} &rarr; {dates[-1]}</span>
  </div>

</div>
</body>
</html>'''

# ═══════════════════════════════════════════════════════════════════
# 13. OUTPUT
# ═══════════════════════════════════════════════════════════════════
outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Iberic_Centinel_Backtest.html")
with open(outpath, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\n  Report saved: {outpath}")

if not os.environ.get("CI"):
    os.system(f'open "{outpath}"')
print("\n=== Done ===")
