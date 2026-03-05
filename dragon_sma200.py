#!/usr/bin/env python3
"""
Dragon Portfolio v3 — Doble SMA (SMA200 + Exit SMA50)
Based on Dragon v2 + Strategy B from Alternativas analysis.

Enhancement: Dual SMA system — SMA200 trend filter scales exposure per block,
SMA50 intra-month exit signal on Equity & Hard Assets exits to SHY when asset
breaks below SMA50 (MOC execution). 30bp transaction cost per switch.

SFinance-alicIA
"""
import yfinance as yf
import numpy as np
import datetime, os, math, json

# ═══════════════════════════════════════════════════════════════════
# 1. CONFIG
# ═══════════════════════════════════════════════════════════════════
UNIVERSES = {
    "Equity":      ["SPY", "QQQ", "IWM", "EEM", "VGK", "EWY", "EWP", "EWZ", "EPOL"],
    "Bonds":       ["SHY", "IEF", "TLT", "TIP", "LQD"],
    "HardAssets":  ["GLD", "SLV", "CPER", "BTC-USD"],
    "LongVol":     ["BTAL"],
    "Commodities": ["DBC"],
}
ALL_TICKERS = sorted(set(t for lst in UNIVERSES.values() for t in lst))
LATE_JOINERS = {"BTC-USD"}
CORE_TICKERS = sorted(t for t in ALL_TICKERS if t not in LATE_JOINERS)

N_SELECT = 3
MOM_LOOKBACK = 126
SMA_LONG = 200
SMA_CMDTY = 50
MIN_EXPOSURE = 0.30   # Minimum exposure when all picks below SMA200
SMA_EXIT = 50         # SMA period for intra-month exit signal
TX_COST_BPS = 30      # Transaction cost per exit/re-entry (basis points)
EXIT_BLOCKS = {"Equity", "HardAssets"}  # Blocks with exit signal (not Bonds)

START = "2006-03-01"
TODAY = datetime.date.today()
RF_ANNUAL = 0.043
BTAL_LEVERAGE = 1.0

W_DRAGON = {"Equity": 0.24, "Bonds": 0.18, "HardAssets": 0.19, "LongVol": 0.21, "CmdtyTrend": 0.18}
W_6040 = {"Equity": 0.60, "Bonds": 0.40}

COLORS = {
    "Equity":     "#10b981",
    "Bonds":      "#06b6d4",
    "HardAssets": "#f59e0b",
    "LongVol":    "#ef4444",
    "CmdtyTrend": "#a855f7",
    "Dragon":     "#06b6d4",  # Doble SMA = cyan branding
    "DragonBase": "#94a3b8",
    "6040":       "#475569",
    "SPY":        "#3b82f6",
}

TICKER_LABELS = {
    "SPY": "US Large", "QQQ": "Nasdaq", "IWM": "Small Cap",
    "EEM": "Emergentes", "VGK": "Europa", "EWY": "Korea",
    "EWP": "Espana", "EWZ": "Brasil", "EPOL": "Polonia",
    "SHY": "1-3Y", "IEF": "7-10Y", "TLT": "20+Y", "TIP": "TIPS", "LQD": "IG Corp",
    "GLD": "Oro", "SLV": "Plata", "CPER": "Cobre", "BTC-USD": "Bitcoin",
    "BTAL": "Anti-Beta", "DBC": "Commodities",
}

TICKER_COLORS = {
    "SPY": "#3b82f6", "QQQ": "#8b5cf6", "IWM": "#f97316",
    "EEM": "#f59e0b", "VGK": "#06b6d4", "EWY": "#ec4899",
    "EWP": "#ef4444", "EWZ": "#22c55e", "EPOL": "#a855f7",
    "SHY": "#94a3b8", "IEF": "#38bdf8", "TLT": "#0ea5e9", "TIP": "#f97316", "LQD": "#10b981",
    "GLD": "#f59e0b", "SLV": "#94a3b8", "CPER": "#f97316", "BTC-USD": "#f7931a",
    "BTAL": "#ef4444", "DBC": "#a855f7",
}

# ═══════════════════════════════════════════════════════════════════
# 2. DATA FETCHING (with local cache)
# ═══════════════════════════════════════════════════════════════════
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_cache.json")

print("═══ Dragon Portfolio v3 — Doble SMA (SMA200 + Exit SMA50) ═══\n")

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
    for ticker in ALL_TICKERS:
        df = yf.download(ticker, start=START, end=str(TODAY), progress=False, auto_adjust=True)
        if len(df) > 100:
            prices[ticker] = df[["Close"]].copy()
            prices[ticker].columns = [ticker]
            print(f"  + {ticker}: {len(df)} days")
        else:
            print(f"  x {ticker}: insufficient data ({len(df)} days)")

    common_idx = prices[CORE_TICKERS[0]].index
    for t in CORE_TICKERS[1:]:
        common_idx = common_idx.intersection(prices[t].index)
    common_idx = common_idx.sort_values()

    price_data = {}
    for t in CORE_TICKERS:
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
print(f"  Tickers: {len(ALL_TICKERS)} ({len(CORE_TICKERS)} core + {len(LATE_JOINERS)} late-joiner)")

# Daily returns
ret = {}
for t in ALL_TICKERS:
    p = price_data[t]
    ret[t] = np.diff(p) / p[:-1]
dates_ret = dates[1:]
N_ret = len(dates_ret)

for t in LATE_JOINERS:
    first_valid = None
    for i in range(N_ret):
        if not np.isnan(ret[t][i]):
            first_valid = dates_ret[i]
            break
    if first_valid:
        print(f"  {t} first valid return: {first_valid}")

# ═══════════════════════════════════════════════════════════════════
# 3. SIGNALS: Momentum + SMA200
# ═══════════════════════════════════════════════════════════════════
print("\nComputing signals...")

# 3a. Momentum (6-month)
mom = {}
for t in ALL_TICKERS:
    p = price_data[t]
    m = np.full(N_ret, np.nan)
    for i in range(N_ret):
        if i >= MOM_LOOKBACK:
            p_now = p[i + 1]
            p_prev = p[i + 1 - MOM_LOOKBACK]
            if not np.isnan(p_now) and not np.isnan(p_prev) and p_prev > 0:
                m[i] = p_now / p_prev - 1
    mom[t] = m

# 3b. SMA200 trend signal
sma200_above = {}
sma200_values = {}
for t in ALL_TICKERS:
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
    sma200_above[t] = signal[1:]  # align with returns
    sma200_values[t] = sma_v

# 3c. SMA intra-month exit signal
sma_exit_above = {}
for t in ALL_TICKERS:
    p = price_data[t]
    signal = np.full(N, False)
    for i in range(N):
        if i >= SMA_EXIT:
            window = p[i - SMA_EXIT + 1:i + 1]
            valid = window[~np.isnan(window)]
            if len(valid) >= SMA_EXIT * 0.8:
                signal[i] = p[i] > np.mean(valid)
    sma_exit_above[t] = signal[1:]  # align with returns
print(f"  SMA{SMA_EXIT} exit signal computed for {len(ALL_TICKERS)} tickers")

for t in LATE_JOINERS:
    first_mom = None
    for i in range(N_ret):
        if not np.isnan(mom[t][i]):
            first_mom = dates_ret[i]
            break
    if first_mom:
        print(f"  {t} first momentum signal: {first_mom}")

# ═══════════════════════════════════════════════════════════════════
# 4. SELECTION + SMA200 EXPOSURE SCALING
# ═══════════════════════════════════════════════════════════════════
print("\nComputing momentum selections + SMA200 filter...")

selections = {block: [] for block in UNIVERSES}
exposure_scale = {block: [] for block in UNIVERSES}
current_sel = {}

for block, candidates in UNIVERSES.items():
    ns = min(N_SELECT, len(candidates))
    defaults = [t for t in candidates if t not in LATE_JOINERS][:ns]
    if len(defaults) < ns:
        defaults = candidates[:ns]
    current_sel[block] = defaults

selection_log = []

for i in range(N_ret):
    d = dates_ret[i]
    is_rebal = (i == 0) or (d.month != dates_ret[i - 1].month)

    if is_rebal:
        log_entry = {"date": d}
        for block, candidates in UNIVERSES.items():
            ns = min(N_SELECT, len(candidates))
            scores = {t: mom[t][i] for t in candidates}
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

# Print summary
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
# 5. STRATEGY CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════
print("\nConstructing strategies...")

shy_ret = ret["SHY"]

def dynamic_block_returns_sma200(block_name, use_sma_filter=True, use_exit_signal=False):
    """Equal-weight average of top-N selected, with SMA200 exposure scaling.
    If use_exit_signal=True, assets below SMA_EXIT exit to SHY with TX_COST_BPS per switch.
    Execution: MOC / ~5min before close (delay=0). Reset on monthly rebalance."""
    r = np.zeros(N_ret)
    exit_count = 0
    switch_count = 0
    # Track per-asset exit state (reset each rebalance)
    prev_exited = {}

    for i in range(N_ret):
        picks = selections[block_name][i]
        shy_r = shy_ret[i] if not np.isnan(shy_ret[i]) else 0.0
        is_rebal = (i == 0) or (dates_ret[i].month != dates_ret[i - 1].month)

        if is_rebal:
            prev_exited = {}  # fresh start on rebalance

        if use_exit_signal:
            valid = []
            for t in picks:
                t_ret = ret[t][i]
                if np.isnan(t_ret):
                    continue
                was_exited = prev_exited.get(t, False)
                is_below = (t in sma_exit_above and not sma_exit_above[t][i])

                if is_below:
                    # Asset below SMA → exit to SHY
                    asset_ret = shy_r
                    if not was_exited:
                        switch_count += 1
                        asset_ret -= TX_COST_BPS / 10000  # exit cost
                    prev_exited[t] = True
                    exit_count += 1
                else:
                    # Asset above SMA → stay/re-enter
                    asset_ret = t_ret
                    if was_exited:
                        switch_count += 1
                        asset_ret -= TX_COST_BPS / 10000  # re-entry cost
                    prev_exited[t] = False
                valid.append(asset_ret)
            risk_ret = np.mean(valid) if valid else 0.0
        else:
            valid = [ret[t][i] for t in picks if not np.isnan(ret[t][i])]
            risk_ret = np.mean(valid) if valid else 0.0

        if use_sma_filter:
            sc = exposure_scale[block_name][i]
            r[i] = sc * risk_ret + (1 - sc) * shy_r
        else:
            r[i] = risk_ret
    return r, exit_count, switch_count

# SMA200-filtered + SMA50 exit on Equity & Hard Assets (not Bonds)
ret_equity_sma, eq_exits, eq_sw = dynamic_block_returns_sma200("Equity", True, "Equity" in EXIT_BLOCKS)
eq_label = f"SMA200 + SMA{SMA_EXIT} exit @{TX_COST_BPS}bp" if "Equity" in EXIT_BLOCKS else "SMA200"
print(f"  + Equity ({eq_label}, top-{N_SELECT} from {len(UNIVERSES['Equity'])}): {N_ret} days, {eq_exits} exits, {eq_sw} switches")

ret_bonds, bo_exits, bo_sw = dynamic_block_returns_sma200("Bonds", True, "Bonds" in EXIT_BLOCKS)
bo_label = f"SMA200 + SMA{SMA_EXIT} exit @{TX_COST_BPS}bp" if "Bonds" in EXIT_BLOCKS else "SMA200"
print(f"  + Bonds ({bo_label}, top-{N_SELECT} from {len(UNIVERSES['Bonds'])}): {N_ret} days, {bo_exits} exits, {bo_sw} switches")

ret_hard_sma, ha_exits, ha_sw = dynamic_block_returns_sma200("HardAssets", True, "HardAssets" in EXIT_BLOCKS)
ha_label = f"SMA200 + SMA{SMA_EXIT} exit @{TX_COST_BPS}bp" if "HardAssets" in EXIT_BLOCKS else "SMA200"
print(f"  + Hard Assets ({ha_label}, top-{N_SELECT} from {len(UNIVERSES['HardAssets'])}): {N_ret} days, {ha_exits} exits, {ha_sw} switches")

total_switches = eq_sw + bo_sw + ha_sw
print(f"  → Total switches: {total_switches} ({total_switches/max(N_ret/252,1):.0f}/year) @ {TX_COST_BPS}bp each")

# Long Vol: BTAL only
ret_longvol = ret["BTAL"] * BTAL_LEVERAGE
print(f"  + Long Volatility (BTAL): {N_ret} days")

# Commodity Trend with SMA200 gate
dbc_prices = price_data["DBC"]
ret_cmdty_trend = np.zeros(N_ret)
for i in range(N_ret):
    day_idx = i + 1
    # SMA200 gate: DBC must be above SMA200
    if day_idx >= SMA_LONG:
        sma200_dbc = np.mean(dbc_prices[day_idx - SMA_LONG:day_idx])
        if dbc_prices[day_idx] < sma200_dbc:
            ret_cmdty_trend[i] = 0.0
            continue
    if day_idx >= SMA_CMDTY:
        sma50 = np.mean(dbc_prices[day_idx - SMA_CMDTY:day_idx])
        deviation = (dbc_prices[day_idx] / sma50) - 1
        if deviation > 0:
            weight = min(deviation / 0.05, 1.0)
            ret_cmdty_trend[i] = ret["DBC"][i] * weight
        else:
            ret_cmdty_trend[i] = 0.0
    else:
        ret_cmdty_trend[i] = ret["DBC"][i] * 0.5
print(f"  + Commodity Trend (SMA-50 + SMA200 gate on DBC): {N_ret} days")

# Base Dragon (no SMA filter) for comparison
ret_equity_base, _, _ = dynamic_block_returns_sma200("Equity", False)
ret_hard_base, _, _ = dynamic_block_returns_sma200("HardAssets", False)

dbc_prices2 = price_data["DBC"]
ret_cmdty_base = np.zeros(N_ret)
for i in range(N_ret):
    day_idx = i + 1
    if day_idx >= SMA_CMDTY:
        sma50 = np.mean(dbc_prices2[day_idx - SMA_CMDTY:day_idx])
        deviation = (dbc_prices2[day_idx] / sma50) - 1
        if deviation > 0:
            weight = min(deviation / 0.05, 1.0)
            ret_cmdty_base[i] = ret["DBC"][i] * weight
        else:
            ret_cmdty_base[i] = 0.0
    else:
        ret_cmdty_base[i] = ret["DBC"][i] * 0.5

# ═══════════════════════════════════════════════════════════════════
# 6. PORTFOLIO CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════
print("\nBuilding portfolios (monthly rebalancing)...")

comp_ret_sma = {
    "Equity":     ret_equity_sma,
    "Bonds":      ret_bonds,
    "HardAssets": ret_hard_sma,
    "LongVol":    ret_longvol,
    "CmdtyTrend": ret_cmdty_trend,
}

comp_ret_base = {
    "Equity":     ret_equity_base,
    "Bonds":      ret_bonds,
    "HardAssets": ret_hard_base,
    "LongVol":    ret_longvol,
    "CmdtyTrend": ret_cmdty_base,
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
base_ret = monthly_rebal_portfolio(W_DRAGON, comp_ret_base, dates_ret)

comp_ret_6040 = {"Equity": ret["SPY"], "Bonds": ret["TLT"]}
port_6040_ret = monthly_rebal_portfolio(W_6040, comp_ret_6040, dates_ret)
spy_ret = ret["SPY"]

# Serpent / Hawk decomposition
W_SERPENT = {"Equity": 0.24 / 0.42, "Bonds": 0.18 / 0.42}
comp_ret_serpent = {"Equity": comp_ret_sma["Equity"], "Bonds": comp_ret_sma["Bonds"]}
serpent_ret = monthly_rebal_portfolio(W_SERPENT, comp_ret_serpent, dates_ret)

W_HAWK = {"HardAssets": 0.19 / 0.58, "LongVol": 0.21 / 0.58, "CmdtyTrend": 0.18 / 0.58}
comp_ret_hawk = {"HardAssets": comp_ret_sma["HardAssets"], "LongVol": comp_ret_sma["LongVol"], "CmdtyTrend": comp_ret_sma["CmdtyTrend"]}
hawk_ret = monthly_rebal_portfolio(W_HAWK, comp_ret_hawk, dates_ret)

def cum_nav(returns):
    nav = np.ones(len(returns) + 1)
    for i, r in enumerate(returns):
        nav[i + 1] = nav[i] * (1 + r)
    return nav

nav_dragon = cum_nav(dragon_ret)
nav_base = cum_nav(base_ret)
nav_6040 = cum_nav(port_6040_ret)
nav_spy = cum_nav(spy_ret)
nav_serpent = cum_nav(serpent_ret)
nav_hawk = cum_nav(hawk_ret)

nav_comp = {}
for comp in comp_ret_sma:
    nav_comp[comp] = cum_nav(comp_ret_sma[comp])

rebal_count = sum(1 for i in range(1, N_ret) if dates_ret[i].month != dates_ret[i-1].month) + 1
print(f"  + Dragon Doble SMA:    ${nav_dragon[-1]:.2f} (from $1) [{rebal_count} rebalances]")
print(f"  + Dragon Base:      ${nav_base[-1]:.2f}")
print(f"  + 60/40 Portfolio:  ${nav_6040[-1]:.2f}")
print(f"  + S&P 500:          ${nav_spy[-1]:.2f}")

# ═══════════════════════════════════════════════════════════════════
# 7. PERFORMANCE METRICS
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

m_dragon = calc_metrics(dragon_ret, "Dragon Doble SMA")
m_base = calc_metrics(base_ret, "Dragon Base")
m_6040 = calc_metrics(port_6040_ret, "60/40 Portfolio")
m_spy = calc_metrics(spy_ret, "S&P 500")
m_serpent = calc_metrics(serpent_ret, "Serpiente")
m_hawk = calc_metrics(hawk_ret, "Halcon")

m_comp = {}
for comp in comp_ret_sma:
    m_comp[comp] = calc_metrics(comp_ret_sma[comp], comp)

comp_names = ["Equity", "Bonds", "HardAssets", "LongVol", "CmdtyTrend"]
corr_data = np.column_stack([comp_ret_sma[c] for c in comp_names])
corr_matrix = np.corrcoef(corr_data, rowvar=False)

print(f"  Dragon Doble SMA: CAGR {m_dragon['cagr']*100:+.1f}%  Sharpe {m_dragon['sharpe']:.2f}  MDD {m_dragon['mdd']:.1f}%")
print(f"  Dragon Base:   CAGR {m_base['cagr']*100:+.1f}%  Sharpe {m_base['sharpe']:.2f}  MDD {m_base['mdd']:.1f}%")
print(f"  60/40:         CAGR {m_6040['cagr']*100:+.1f}%  Sharpe {m_6040['sharpe']:.2f}  MDD {m_6040['mdd']:.1f}%")
print(f"  S&P 500:       CAGR {m_spy['cagr']*100:+.1f}%  Sharpe {m_spy['sharpe']:.2f}  MDD {m_spy['mdd']:.1f}%")

# ═══════════════════════════════════════════════════════════════════
# 8. REGIME ANALYSIS
# ═══════════════════════════════════════════════════════════════════
REGIME_LOOKBACK = 252
regime_labels = []
for i in range(N_ret):
    day_idx = i + 1
    if day_idx >= REGIME_LOOKBACK:
        r1y = price_data["SPY"][day_idx] / price_data["SPY"][day_idx - REGIME_LOOKBACK] - 1
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
        "Dragon": calc_metrics(dragon_ret[mask], f"Dragon ({regime})"),
        "Base": calc_metrics(base_ret[mask], f"Base ({regime})"),
        "6040": calc_metrics(port_6040_ret[mask], f"60/40 ({regime})"),
        "Serpent": calc_metrics(serpent_ret[mask], f"Serpent ({regime})"),
        "Hawk": calc_metrics(hawk_ret[mask], f"Hawk ({regime})"),
    }

# ═══════════════════════════════════════════════════════════════════
# 9. ANNUAL RETURNS
# ═══════════════════════════════════════════════════════════════════
annual_returns = {}
for i, d in enumerate(dates_ret):
    yr = d.year
    if yr not in annual_returns:
        annual_returns[yr] = {"Dragon": [], "Base": [], "6040": [], "SPY": [], "Serpent": [], "Hawk": []}
    annual_returns[yr]["Dragon"].append(dragon_ret[i])
    annual_returns[yr]["Base"].append(base_ret[i])
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
# 10. STRESS PERIODS
# ═══════════════════════════════════════════════════════════════════
from datetime import datetime as _dt

STRESS_PERIODS = [
    ("Taper Tantrum",       "2013-05-22", "2013-06-24"),
    ("China / Oil Crash",   "2015-08-10", "2016-02-11"),
    ("Volmageddon",         "2018-01-26", "2018-02-08"),
    ("Q4 2018 Selloff",     "2018-10-01", "2018-12-24"),
    ("COVID Crash",         "2020-02-19", "2020-03-23"),
    ("2022 Bear / Rates",   "2022-01-03", "2022-10-12"),
    ("Trump Tariffs",       "2025-02-19", "2025-04-08"),
]

def period_return(returns, dates_list, start_str, end_str):
    start_d = _dt.strptime(start_str, "%Y-%m-%d").date()
    end_d = _dt.strptime(end_str, "%Y-%m-%d").date()
    mask = np.array([(d >= start_d and d <= end_d) for d in dates_list])
    if mask.sum() == 0: return None
    return (np.prod(1 + returns[mask]) - 1) * 100

stress_results = []
for sp_name, sp_start, sp_end in STRESS_PERIODS:
    r_d = period_return(dragon_ret, dates_ret, sp_start, sp_end)
    if r_d is None: continue
    stress_results.append({
        "name": sp_name, "start": sp_start, "end": sp_end,
        "Dragon": r_d,
        "Base": period_return(base_ret, dates_ret, sp_start, sp_end),
        "6040": period_return(port_6040_ret, dates_ret, sp_start, sp_end),
        "SPY": period_return(spy_ret, dates_ret, sp_start, sp_end),
        "Serpent": period_return(serpent_ret, dates_ret, sp_start, sp_end),
        "Hawk": period_return(hawk_ret, dates_ret, sp_start, sp_end),
    })

# Selection frequencies
block_freq = {}
for block in ["Equity", "Bonds", "HardAssets"]:
    freq = {}
    for entry in selection_log:
        for t in entry[block]["picks"]:
            freq[t] = freq.get(t, 0) + 1
    block_freq[block] = freq

# ═══════════════════════════════════════════════════════════════════
# 11. SVG CHART GENERATORS
# ═══════════════════════════════════════════════════════════════════
print("\nGenerating charts...")

def svg_line(nav_arr, dates_arr, color, width=2.0, dashed=False, vw=720, vh=300,
             log_scale=True, y_min=None, y_max=None):
    n = len(nav_arr)
    if n == 0: return ""
    vals = np.log(np.clip(nav_arr, 1e-6, None)) if log_scale else np.array(nav_arr, dtype=float)
    if y_min is None: y_min = np.nanmin(vals)
    if y_max is None: y_max = np.nanmax(vals)
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
        v = np.log(np.clip(nav, 1e-6, None)) if log_scale else np.array(nav, dtype=float)
        all_vals.extend(v[~np.isnan(v)])
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
    nd = {"Dragon": nav_dragon, "Base": nav_base, "6040": nav_6040, "SPY": nav_spy}
    g, ymn, ymx = svg_grid_and_labels(nd, dates, vw, vh)
    l = svg_line(nav_6040, dates, COLORS["6040"], 1.2, True, vw, vh, True, ymn, ymx)
    l += svg_line(nav_spy, dates, COLORS["SPY"], 1.2, True, vw, vh, True, ymn, ymx)
    l += svg_line(nav_base, dates, COLORS["DragonBase"], 1.8, True, vw, vh, True, ymn, ymx)
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
    dd3 = np.insert(dd_s(base_ret), 0, 0)
    ymn = min(np.min(dd1), np.min(dd2), np.min(dd3)); ymx = 0
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
    svg += dp(dd2, COLORS["6040"], 1.0, 0.06)
    svg += dp(dd3, COLORS["DragonBase"], 1.5, 0.08)
    svg += dp(dd1, COLORS["Dragon"], 1.8, 0.12)
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

def build_donut():
    cx, cy, r, ri = 100, 100, 80, 50
    comps = [("Equity",0.24,COLORS["Equity"]),("Bonds",0.18,COLORS["Bonds"]),
             ("HardAssets",0.19,COLORS["HardAssets"]),("LongVol",0.21,COLORS["LongVol"]),
             ("CmdtyTrend",0.18,COLORS["CmdtyTrend"])]
    svg = ""; angle = -90
    for name, w, color in comps:
        sweep = w * 360
        a1, a2 = math.radians(angle), math.radians(angle + sweep)
        x1o,y1o = cx+r*math.cos(a1), cy+r*math.sin(a1)
        x2o,y2o = cx+r*math.cos(a2), cy+r*math.sin(a2)
        x1i,y1i = cx+ri*math.cos(a2), cy+ri*math.sin(a2)
        x2i,y2i = cx+ri*math.cos(a1), cy+ri*math.sin(a1)
        lg = 1 if sweep > 180 else 0
        svg += f'<path d="M {x1o:.1f},{y1o:.1f} A {r},{r} 0 {lg},1 {x2o:.1f},{y2o:.1f} L {x1i:.1f},{y1i:.1f} A {ri},{ri} 0 {lg},0 {x2i:.1f},{y2i:.1f} Z" fill="{color}" stroke="#0f172a" stroke-width="1.5"/>'
        ma = math.radians(angle + sweep/2)
        lx, ly = cx+(r+18)*math.cos(ma), cy+(r+18)*math.sin(ma)
        svg += f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle" fill="{color}" font-size="8" font-weight="600">{int(w*100)}%</text>'
        angle += sweep
    svg += f'<text x="{cx}" y="{cy-5}" text-anchor="middle" fill="#e2e8f0" font-size="9" font-weight="700">DRAGON v3</text>'
    svg += f'<text x="{cx}" y="{cy+9}" text-anchor="middle" fill="#06b6d4" font-size="7" font-weight="600">SMA200</text>'
    return f'<svg viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

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

# Exposure timeline chart
def build_exposure_chart():
    """Show SMA200 exposure over time for Equity and HardAssets blocks."""
    vw, vh = 720, 160
    ml, mr, mt, mb = 50, 15, 10, 25
    pw, ph = vw - ml - mr, vh - mt - mb
    svg = ""
    # Grid
    for pct in [0.3, 0.5, 0.67, 1.0]:
        yp = mt + ph - ((pct - 0.2) / 0.9) * ph
        svg += f'<line x1="{ml}" y1="{yp:.0f}" x2="{vw-mr}" y2="{yp:.0f}" stroke="rgba(148,163,184,0.1)" stroke-width="0.5"/>'
        svg += f'<text x="{ml-5}" y="{yp:.0f}" text-anchor="end" fill="#64748b" font-size="8" dominant-baseline="middle">{pct:.0%}</text>'
    # Lines
    for block, color in [("Equity", COLORS["Equity"]), ("Bonds", COLORS["Bonds"]), ("HardAssets", COLORS["HardAssets"])]:
        exp = exposure_scale[block]
        pts = []
        for i in range(N_ret):
            x = ml + (i / max(N_ret - 1, 1)) * pw
            y = mt + ph - ((exp[i] - 0.2) / 0.9) * ph
            pts.append(f"{x:.1f},{y:.1f}")
        svg += f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.5" stroke-linejoin="round" opacity="0.7"/>'
    # Year labels
    seen = set()
    for i, d in enumerate(dates_ret):
        if d.year not in seen and (i == 0 or d.year != dates_ret[i-1].year):
            seen.add(d.year)
            x = ml + (i / max(N_ret - 1, 1)) * pw
            svg += f'<text x="{x:.0f}" y="{vh-5}" text-anchor="middle" fill="#64748b" font-size="8">{d.year}</text>'
    return f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">{svg}</svg>'

# ═══════════════════════════════════════════════════════════════════
# 12. HELPER FORMATTERS
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
        for p in ["Dragon","Base","6040","Serpent","Hawk"]:
            rows += f'<td class="num {pct_cls(rs[p]["cagr"])}">{rs[p]["cagr"]*100:+.1f}%</td>'
        rows += '</tr>'
    return rows

def annual_rows():
    rows = ""
    for yr in sorted(annual_table.keys()):
        a = annual_table[yr]
        rows += f'<tr><td>{yr}</td>'
        for p in ["Dragon","Base","6040","SPY","Serpent","Hawk"]:
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
    header += '</tr>'
    rows = ""
    for entry in selection_log:
        d = entry["date"]
        data = entry[block]
        picks = data["picks"]
        scores = data["scores"]
        rows += f'<tr><td style="color:#64748b;white-space:nowrap;font-size:9px">{d.strftime("%Y-%m")}</td>'
        for t in tickers:
            s = scores.get(t, np.nan)
            picked = t in picks
            if np.isnan(s):
                rows += '<td class="num" style="color:#334155;font-size:9px">--</td>'
            else:
                bg = "rgba(6,182,212,0.10)" if picked else "transparent"
                clr = ("#06b6d4" if s>=0 else "#f87171") if picked else ("#10b981" if s>0 else "#ef4444" if s<-0.05 else "#64748b")
                fw = "700" if picked else "400"
                bd = "border:1px solid rgba(6,182,212,0.3);" if picked else ""
                rows += f'<td class="num" style="background:{bg};color:{clr};font-weight:{fw};font-size:9px;{bd}">{s*100:+.1f}%</td>'
        rows += '</tr>'
    return f'{header}{rows}'

def selection_freq_html(freq_dict):
    total = len(selection_log)
    items = sorted(freq_dict.items(), key=lambda x: -x[1])
    html = ""
    for ticker, count in items:
        pct = count / total * 100
        color = TICKER_COLORS.get(ticker, "#94a3b8")
        label = TICKER_LABELS.get(ticker, ticker)
        html += f'''<div style="display:flex;align-items:center;gap:6px;margin:3px 0">
            <span style="width:55px;font-size:9px;font-weight:700;color:{color}">{ticker}</span>
            <span style="width:65px;font-size:8px;color:#64748b">{label}</span>
            <div style="flex:1;height:14px;background:rgba(148,163,184,0.06);border-radius:3px;overflow:hidden">
                <div style="width:{pct:.0f}%;height:100%;background:{color};opacity:0.5;border-radius:3px"></div>
            </div>
            <span style="width:60px;text-align:right;font-size:9px;color:#94a3b8">{count}m ({pct:.0f}%)</span>
        </div>'''
    return html

def universe_tags(block):
    tags = ""
    for t in UNIVERSES[block]:
        c = TICKER_COLORS.get(t, "#94a3b8")
        tags += f'<span class="comp-tag" style="background:rgba(148,163,184,0.08);color:{c}">{t}</span> '
    return tags

print("\n  --- Last 3 rebalances (HardAssets) ---")
for entry in selection_log[-3:]:
    d = entry["date"]
    scores = entry["HardAssets"]["scores"]
    picks = entry["HardAssets"]["picks"]
    ranked = sorted(scores.items(), key=lambda x: x[1] if not np.isnan(x[1]) else -np.inf, reverse=True)
    print(f"  {d.strftime('%Y-%m')}:")
    for rank, (t, s) in enumerate(ranked, 1):
        sel = " <-- TOP-3" if t in picks else ""
        sstr = f"{s*100:+.1f}%" if not np.isnan(s) else "n/a"
        print(f"    #{rank} {t:8s} ({TICKER_LABELS.get(t,''):10s}) Mom6M: {sstr:>8s}{sel}")

def build_stress_rows():
    rows = ""
    for sr in stress_results:
        rows += '<tr>'
        rows += f'<td style="font-weight:700;white-space:nowrap">{sr["name"]}</td>'
        rows += f'<td style="color:#64748b;font-size:8px;white-space:nowrap">{sr["start"]} &rarr; {sr["end"]}</td>'
        for key in ["Dragon", "Base", "6040", "SPY", "Serpent", "Hawk"]:
            v = sr[key]
            if v is None:
                rows += '<td class="num">--</td>'
            else:
                clr = "#10b981" if v >= 0 else "#ef4444"
                fw = "font-weight:700;" if key == "Dragon" else ""
                rows += f'<td class="num" style="{fw}color:{clr}">{v:+.1f}%</td>'
        rows += '</tr>'
    return rows

# Sharpe improvement
sharpe_delta = (m_dragon['sharpe'] / m_base['sharpe'] - 1) * 100 if m_base['sharpe'] > 0 else 0

# ═══════════════════════════════════════════════════════════════════
# 13. HTML REPORT
# ═══════════════════════════════════════════════════════════════════
print("\nGenerating HTML report...")

html = f'''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dragon Portfolio v3 — Doble SMA | SFinance</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Inter', -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; }}
.container {{ max-width:1200px; margin:0 auto; padding:24px 20px; }}
.header {{ display:flex; justify-content:space-between; align-items:center; padding:16px 0; border-bottom:1px solid rgba(148,163,184,0.12); margin-bottom:24px; }}
.header-title {{ font-size:22px; font-weight:800; letter-spacing:-0.5px; }}
.header-title span {{ color:#06b6d4; }}
.header-sub {{ font-size:11px; color:#64748b; text-align:right; }}
.header-sub strong {{ color:#94a3b8; }}
.alloc-section {{ display:grid; grid-template-columns: 200px 1fr; gap:24px; align-items:center; margin-bottom:28px; padding:20px; background:rgba(30,41,59,0.5); border:1px solid rgba(148,163,184,0.08); border-radius:8px; }}
.alloc-desc {{ font-size:10px; color:#94a3b8; line-height:1.6; }}
.alloc-desc .type-label {{ display:inline-block; padding:2px 8px; border-radius:3px; font-weight:700; font-size:9px; margin-right:6px; }}
.serpent-label {{ background:rgba(16,185,129,0.15); color:#10b981; }}
.hawk-label {{ background:rgba(245,158,11,0.15); color:#f59e0b; }}
.sma-label {{ background:rgba(6,182,212,0.15); color:#06b6d4; }}
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
.footer {{ display:flex; justify-content:space-between; font-size:8px; color:#475569; padding:16px 0; border-top:1px solid rgba(148,163,184,0.08); margin-top:20px; }}
@media (max-width: 900px) {{ .kpi-strip {{ grid-template-columns:repeat(4, 1fr); }} .grid-2,.grid-3 {{ grid-template-columns:1fr; }} .alloc-section {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="container">

  <div class="header">
    <div>
      <div class="header-title"><span>Dragon Portfolio</span> v3 — Doble SMA</div>
      <div style="font-size:10px;color:#64748b;margin-top:2px">Artemis Capital (2020) + SMA200 Filter + SMA{SMA_EXIT} Exit Signal | Top-{N_SELECT} momentum {MOM_LOOKBACK}d | {len(ALL_TICKERS)} activos</div>
    </div>
    <div class="header-sub"><strong>SFinance-alicIA</strong><br>{TODAY.strftime("%d %b %Y")} | {dates[0]} -> {dates[-1]}</div>
  </div>

  <div class="card" style="margin-bottom:24px;border-left:3px solid #06b6d4;padding:16px 20px">
    <div style="font-size:12px;font-weight:800;color:#06b6d4;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px">Hipotesis de Inversion</div>
    <div style="font-size:10px;color:#cbd5e1;line-height:1.8">
      <p>El <strong style="color:#e2e8f0">Dragon Portfolio v3</strong> parte de la tesis de <strong style="color:#e2e8f0">Artemis Capital</strong> y agrega un <strong style="color:#06b6d4">filtro de tendencia SMA200</strong>: antes de invertir en un activo, pregunta <em>"esta por encima de su media movil de 200 dias?"</em></p>
      <p style="margin-top:6px">Si un activo esta en <strong style="color:#10b981">tendencia alcista</strong> (sobre SMA200), recibe exposicion completa. Si esta en <strong style="color:#ef4444">tendencia bajista</strong> (bajo SMA200), se reduce su peso y el capital se redirige a <strong style="color:#e2e8f0">SHY</strong> (cash proxy). El filtro opera a nivel de bloque: la exposicion es proporcional a cuantos de los {N_SELECT} seleccionados estan sobre su SMA200 (minimo {MIN_EXPOSURE:.0%}).</p>
      <p style="margin-top:6px">Ademas, un <strong style="color:#f59e0b">exit signal SMA{SMA_EXIT}</strong> opera intra-mes en Equity y Hard Assets: si un activo seleccionado rompe por debajo de su SMA{SMA_EXIT}, se sale a SHY (~5min antes del cierre, MOC order) hasta el proximo rebalanceo. Coste estimado: <strong style="color:#e2e8f0">{TX_COST_BPS}bp por switch</strong>.</p>
      <p style="margin-top:6px">El resultado es un portafolio que <strong style="color:#e2e8f0">participa en las subidas pero se protege parcialmente en las bajadas</strong> — mejorando el ratio retorno/riesgo (Sharpe <span style="color:#06b6d4">+{sharpe_delta:.0f}%</span> vs Dragon base) sin cambiar la estructura de diversificacion secular.</p>
    </div>
  </div>

  <div class="alloc-section">
    <div style="text-align:center">{build_donut()}</div>
    <div class="alloc-desc">
      <div style="margin-bottom:8px;font-size:12px;font-weight:700;color:#e2e8f0">Diversificacion secular + Momentum top-{N_SELECT} + <span style="color:#06b6d4">Doble SMA</span></div>
      <p style="margin-bottom:8px">Cada mes se eligen los <strong style="color:#06b6d4">{N_SELECT} mejores</strong> por retorno trailing {MOM_LOOKBACK}d. Luego se aplica el filtro SMA200: la exposicion al bloque se escala segun cuantos picks estan sobre su SMA200.</p>
      <div><span class="type-label serpent-label">SERPIENTE 42%</span> Equity (24%): {len(UNIVERSES["Equity"])} cand. <span class="type-label sma-label">SMA200</span> | Bonds (18%): {len(UNIVERSES["Bonds"])} cand. <span class="type-label sma-label">SMA200</span></div>
      <div style="margin-top:4px"><span class="type-label hawk-label">HALCON 58%</span> Hard Assets (19%): {len(UNIVERSES["HardAssets"])} cand. <span class="type-label sma-label">SMA200</span> | Long Vol (21%): BTAL | Cmdty Trend (18%) <span class="type-label sma-label">SMA200 GATE</span></div>
    </div>
  </div>

  <div class="kpi-strip">
    <div class="kpi"><div class="kpi-label">CAGR</div><div class="kpi-value {pct_cls(m_dragon['cagr'])}">{m_dragon['cagr']*100:+.1f}%</div><div class="kpi-sub">Base: {m_base['cagr']*100:+.1f}% | 60/40: {m_6040['cagr']*100:+.1f}%</div></div>
    <div class="kpi"><div class="kpi-label">Sharpe</div><div class="kpi-value" style="color:#06b6d4">{m_dragon['sharpe']:.2f}</div><div class="kpi-sub">Base: {m_base['sharpe']:.2f} | 60/40: {m_6040['sharpe']:.2f}</div></div>
    <div class="kpi"><div class="kpi-label">Sortino</div><div class="kpi-value" style="color:#06b6d4">{m_dragon['sortino']:.2f}</div><div class="kpi-sub">Base: {m_base['sortino']:.2f} | 60/40: {m_6040['sortino']:.2f}</div></div>
    <div class="kpi"><div class="kpi-label">Max Drawdown</div><div class="kpi-value neg">{m_dragon['mdd']:.1f}%</div><div class="kpi-sub">Base: {m_base['mdd']:.1f}% | 60/40: {m_6040['mdd']:.1f}%</div></div>
    <div class="kpi"><div class="kpi-label">Volatilidad</div><div class="kpi-value">{m_dragon['vol']*100:.1f}%</div><div class="kpi-sub">Base: {m_base['vol']*100:.1f}% | 60/40: {m_6040['vol']*100:.1f}%</div></div>
    <div class="kpi"><div class="kpi-label">Calmar</div><div class="kpi-value">{m_dragon['calmar']:.2f}</div><div class="kpi-sub">Base: {m_base['calmar']:.2f} | 60/40: {m_6040['calmar']:.2f}</div></div>
    <div class="kpi"><div class="kpi-label">Ret / Risk</div><div class="kpi-value">{m_dragon['ret_to_risk']:.2f}x</div><div class="kpi-sub">Base: {m_base['ret_to_risk']:.2f}x | 60/40: {m_6040['ret_to_risk']:.2f}x</div></div>
    <div class="kpi"><div class="kpi-label">Periodo</div><div class="kpi-value" style="font-size:14px;color:#94a3b8">{m_dragon['years']:.1f}y</div><div class="kpi-sub">{N_ret} trading days</div></div>
  </div>

  <div class="section">
    <div class="section-title">Crecimiento de $1 — Escala Logaritmica</div>
    <div class="chart-container">{build_main_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:#06b6d4"></div><strong style="color:#06b6d4">Dragon v3 Doble SMA</strong> ${nav_dragon[-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#94a3b8"></div>Dragon Base ${nav_base[-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#475569"></div>60/40 ${nav_6040[-1]:.2f}</div>
      <div class="legend-item"><div class="legend-dot" style="background:#3b82f6"></div>S&P 500 ${nav_spy[-1]:.2f}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Exposicion SMA200 — Equity, Bonds & Hard Assets (proporcion del bloque invertida en riesgo vs cash)</div>
    <div class="chart-container">{build_exposure_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['Equity']}"></div><span style="color:{COLORS['Equity']}">Equity</span></div>
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['Bonds']}"></div><span style="color:{COLORS['Bonds']}">Bonds</span></div>
      <div class="legend-item"><div class="legend-dot" style="background:{COLORS['HardAssets']}"></div><span style="color:{COLORS['HardAssets']}">Hard Assets</span></div>
      <div class="legend-item" style="color:#475569">100% = todos sobre SMA200 | {MIN_EXPOSURE:.0%} = minimo (todos bajo SMA200)</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="type-card serpent">
      <div class="type-header" style="color:#10b981">Serpiente <span style="font-size:10px;font-weight:400;color:#64748b">— Crecimiento Secular (42%) | Top-{N_SELECT} Momentum + SMA200</span></div>
      <div class="type-kpis">
        <div class="type-kpi"><div class="tk-label">CAGR</div><div class="tk-value {pct_cls(m_serpent['cagr'])}">{m_serpent['cagr']*100:+.1f}%</div></div>
        <div class="type-kpi"><div class="tk-label">Sharpe</div><div class="tk-value" style="color:#10b981">{m_serpent['sharpe']:.2f}</div></div>
        <div class="type-kpi"><div class="tk-label">Max DD</div><div class="tk-value neg">{m_serpent['mdd']:.1f}%</div></div>
      </div>
      <div class="type-components">
        <div style="margin-bottom:4px"><span class="comp-tag" style="background:rgba(16,185,129,0.15);color:#10b981">Equity 24%</span> Top-{N_SELECT} de: {universe_tags("Equity")} <span class="comp-tag" style="background:rgba(6,182,212,0.12);color:#06b6d4">SMA200</span></div>
        <div><span class="comp-tag" style="background:rgba(6,182,212,0.15);color:#06b6d4">Bonds 18%</span> Top-{N_SELECT} de: {universe_tags("Bonds")} <span class="type-label sma-label">SMA200</span></div>
      </div>
    </div>
    <div class="type-card hawk">
      <div class="type-header" style="color:#f59e0b">Halcon <span style="font-size:10px;font-weight:400;color:#64748b">— Cambio Secular (58%) + SMA200</span></div>
      <div class="type-kpis">
        <div class="type-kpi"><div class="tk-label">CAGR</div><div class="tk-value {pct_cls(m_hawk['cagr'])}">{m_hawk['cagr']*100:+.1f}%</div></div>
        <div class="type-kpi"><div class="tk-label">Sharpe</div><div class="tk-value" style="color:#f59e0b">{m_hawk['sharpe']:.2f}</div></div>
        <div class="type-kpi"><div class="tk-label">Max DD</div><div class="tk-value neg">{m_hawk['mdd']:.1f}%</div></div>
      </div>
      <div class="type-components">
        <div style="margin-bottom:4px"><span class="comp-tag" style="background:rgba(245,158,11,0.15);color:#f59e0b">Hard Assets 19%</span> Top-{N_SELECT} de: {universe_tags("HardAssets")} <span class="comp-tag" style="background:rgba(6,182,212,0.12);color:#06b6d4">SMA200</span><br><span style="font-size:8px;color:#475569">BTC-USD compite desde ~2015 (late-joiner)</span></div>
        <span class="comp-tag" style="background:rgba(239,68,68,0.15);color:#ef4444">Long Vol 21%</span> BTAL (Anti-Beta) <span style="color:#475569;font-size:8px">(sin filtro SMA)</span><br>
        <span class="comp-tag" style="background:rgba(168,85,247,0.15);color:#a855f7">Cmdty Trend 18%</span> DBC + SMA-50 <span class="comp-tag" style="background:rgba(6,182,212,0.12);color:#06b6d4">SMA200 GATE</span>
      </div>
    </div>
  </div>

  <div class="grid-3">
    <div class="card"><div class="section-title">Frecuencia — Equity</div>{selection_freq_html(block_freq["Equity"])}</div>
    <div class="card"><div class="section-title">Frecuencia — Bonds</div>{selection_freq_html(block_freq["Bonds"])}</div>
    <div class="card"><div class="section-title">Frecuencia — Hard Assets</div>{selection_freq_html(block_freq["HardAssets"])}</div>
  </div>

  <div class="section">
    <div class="section-title">Momentum Scorecard — Hard Assets | Celdas cyan = seleccionado</div>
    <div class="table-scroll" style="max-height:350px"><table class="data-table">{build_momentum_scorecard("HardAssets", UNIVERSES["HardAssets"])}</table></div>
    <div style="margin-top:6px;font-size:8px;color:#475569">BTC-USD muestra "--" antes de tener {MOM_LOOKBACK} dias de historia. Exposicion del bloque ajustada por SMA200.</div>
  </div>

  <div class="section">
    <div class="section-title">Momentum Scorecard — Equity | Celdas cyan = seleccionado</div>
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
      <div class="legend-item"><svg width="20" height="8"><line x1="0" y1="4" x2="20" y2="4" stroke="#06b6d4" stroke-width="2" stroke-dasharray="4,3"/></svg><span style="color:#06b6d4">Dragon v3</span> ${nav_dragon[-1]:.2f}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Componentes Individuales (con filtro SMA200)</div>
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
    <div class="section-title">Drawdown — Dragon v3 vs Base vs 60/40</div>
    <div class="chart-container">{build_drawdown_chart()}</div>
    <div class="legend-row">
      <div class="legend-item"><div class="legend-dot" style="background:#06b6d4"></div><span style="color:#06b6d4">Dragon v3 Doble SMA</span> MDD: {m_dragon['mdd']:.1f}%</div>
      <div class="legend-item"><div class="legend-dot" style="background:#94a3b8"></div><span style="color:#94a3b8">Dragon Base</span> MDD: {m_base['mdd']:.1f}%</div>
      <div class="legend-item"><div class="legend-dot" style="background:#475569"></div><span style="color:#475569">60/40</span> MDD: {m_6040['mdd']:.1f}%</div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <div class="section-title">Performance por Regimen (CAGR)</div>
      <table class="data-table">
        <tr><th>Regimen</th><th class="num">Dias</th><th class="num" style="color:#06b6d4">v3 Doble SMA</th><th class="num">Base</th><th class="num">60/40</th><th class="num" style="color:#10b981">Serpiente</th><th class="num" style="color:#f59e0b">Halcon</th></tr>
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
      <tr><th>Ano</th><th class="num" style="color:#06b6d4">v3 Doble SMA</th><th class="num">Base</th><th class="num">60/40</th><th class="num" style="color:#3b82f6">S&P 500</th><th class="num" style="color:#10b981">Serpiente</th><th class="num" style="color:#f59e0b">Halcon</th></tr>
      {annual_rows()}
    </table>
  </div>

  <div class="section">
    <div class="section-title">Periodos de Stress</div>
    <table class="data-table">
      <tr>
        <th>Evento</th><th>Periodo</th>
        <th class="num" style="color:#06b6d4">v3 Doble SMA</th>
        <th class="num">Base</th>
        <th class="num">60/40</th>
        <th class="num" style="color:#3b82f6">S&P 500</th>
        <th class="num" style="color:#10b981">Serpiente</th>
        <th class="num" style="color:#f59e0b">Halcon</th>
      </tr>
      {build_stress_rows()}
    </table>
    <div style="margin-top:6px;font-size:8px;color:#475569">Retorno total peak-to-trough en cada periodo. <span class="pos">positivo</span> / <span class="neg">negativo</span>.</div>
  </div>

  <div class="section">
    <div class="section-title">Estadisticas Completas</div>
    <div class="table-scroll">
    <table class="data-table">
      <tr><th>Metrica</th><th class="num" style="color:#06b6d4">v3 Doble SMA</th><th class="num">Base</th><th class="num">60/40</th><th class="num" style="color:#3b82f6">S&P 500</th><th class="num" style="color:{COLORS['Equity']}">Equity</th><th class="num" style="color:{COLORS['Bonds']}">Bonds</th><th class="num" style="color:{COLORS['HardAssets']}">Hard Assets</th><th class="num" style="color:{COLORS['LongVol']}">Long Vol</th><th class="num" style="color:{COLORS['CmdtyTrend']}">Cmdty Trend</th></tr>
      <tr><td>CAGR</td><td class="num" style="font-weight:700;color:#06b6d4">{m_dragon['cagr']*100:+.1f}%</td><td class="num">{m_base['cagr']*100:+.1f}%</td><td class="num">{m_6040['cagr']*100:+.1f}%</td><td class="num">{m_spy['cagr']*100:+.1f}%</td><td class="num">{m_comp['Equity']['cagr']*100:+.1f}%</td><td class="num">{m_comp['Bonds']['cagr']*100:+.1f}%</td><td class="num">{m_comp['HardAssets']['cagr']*100:+.1f}%</td><td class="num">{m_comp['LongVol']['cagr']*100:+.1f}%</td><td class="num">{m_comp['CmdtyTrend']['cagr']*100:+.1f}%</td></tr>
      <tr><td>Volatilidad</td><td class="num" style="font-weight:700">{m_dragon['vol']*100:.1f}%</td><td class="num">{m_base['vol']*100:.1f}%</td><td class="num">{m_6040['vol']*100:.1f}%</td><td class="num">{m_spy['vol']*100:.1f}%</td><td class="num">{m_comp['Equity']['vol']*100:.1f}%</td><td class="num">{m_comp['Bonds']['vol']*100:.1f}%</td><td class="num">{m_comp['HardAssets']['vol']*100:.1f}%</td><td class="num">{m_comp['LongVol']['vol']*100:.1f}%</td><td class="num">{m_comp['CmdtyTrend']['vol']*100:.1f}%</td></tr>
      <tr><td>Sharpe</td><td class="num" style="font-weight:700;color:#06b6d4">{m_dragon['sharpe']:.2f}</td><td class="num">{m_base['sharpe']:.2f}</td><td class="num">{m_6040['sharpe']:.2f}</td><td class="num">{m_spy['sharpe']:.2f}</td><td class="num">{m_comp['Equity']['sharpe']:.2f}</td><td class="num">{m_comp['Bonds']['sharpe']:.2f}</td><td class="num">{m_comp['HardAssets']['sharpe']:.2f}</td><td class="num">{m_comp['LongVol']['sharpe']:.2f}</td><td class="num">{m_comp['CmdtyTrend']['sharpe']:.2f}</td></tr>
      <tr><td>Sortino</td><td class="num" style="font-weight:700">{m_dragon['sortino']:.2f}</td><td class="num">{m_base['sortino']:.2f}</td><td class="num">{m_6040['sortino']:.2f}</td><td class="num">{m_spy['sortino']:.2f}</td><td class="num">{m_comp['Equity']['sortino']:.2f}</td><td class="num">{m_comp['Bonds']['sortino']:.2f}</td><td class="num">{m_comp['HardAssets']['sortino']:.2f}</td><td class="num">{m_comp['LongVol']['sortino']:.2f}</td><td class="num">{m_comp['CmdtyTrend']['sortino']:.2f}</td></tr>
      <tr><td>Max DD</td><td class="num neg" style="font-weight:700">{m_dragon['mdd']:.1f}%</td><td class="num neg">{m_base['mdd']:.1f}%</td><td class="num neg">{m_6040['mdd']:.1f}%</td><td class="num neg">{m_spy['mdd']:.1f}%</td><td class="num neg">{m_comp['Equity']['mdd']:.1f}%</td><td class="num neg">{m_comp['Bonds']['mdd']:.1f}%</td><td class="num neg">{m_comp['HardAssets']['mdd']:.1f}%</td><td class="num neg">{m_comp['LongVol']['mdd']:.1f}%</td><td class="num neg">{m_comp['CmdtyTrend']['mdd']:.1f}%</td></tr>
      <tr><td>Calmar</td><td class="num" style="font-weight:700">{m_dragon['calmar']:.2f}</td><td class="num">{m_base['calmar']:.2f}</td><td class="num">{m_6040['calmar']:.2f}</td><td class="num">{m_spy['calmar']:.2f}</td><td class="num">{m_comp['Equity']['calmar']:.2f}</td><td class="num">{m_comp['Bonds']['calmar']:.2f}</td><td class="num">{m_comp['HardAssets']['calmar']:.2f}</td><td class="num">{m_comp['LongVol']['calmar']:.2f}</td><td class="num">{m_comp['CmdtyTrend']['calmar']:.2f}</td></tr>
      <tr><td>Total Return</td><td class="num" style="font-weight:700;color:#06b6d4">{m_dragon['total']:+.0f}%</td><td class="num">{m_base['total']:+.0f}%</td><td class="num">{m_6040['total']:+.0f}%</td><td class="num">{m_spy['total']:+.0f}%</td><td class="num">{m_comp['Equity']['total']:+.0f}%</td><td class="num">{m_comp['Bonds']['total']:+.0f}%</td><td class="num">{m_comp['HardAssets']['total']:+.0f}%</td><td class="num">{m_comp['LongVol']['total']:+.0f}%</td><td class="num">{m_comp['CmdtyTrend']['total']:+.0f}%</td></tr>
    </table>
    </div>
  </div>

  <div class="card" style="margin-bottom:20px">
    <div class="section-title">Metodologia</div>
    <div style="font-size:9px;color:#94a3b8;line-height:1.7;columns:2;column-gap:24px">
      <p><strong style="color:#e2e8f0">Momentum {MOM_LOOKBACK}d</strong> — price[t] / price[t-{MOM_LOOKBACK}] - 1. Cierre previo al rebalanceo (sin look-ahead). Top-{N_SELECT} por bloque, equal-weight.</p>
      <p style="margin-top:6px"><strong style="color:#06b6d4">SMA200 Filtro de Exposicion</strong> — Para Equity, Bonds y Hard Assets: la exposicion al bloque = (# picks sobre SMA200) / {N_SELECT}. Minimo {MIN_EXPOSURE:.0%}. Capital no-expuesto se invierte en SHY (cash proxy). Para Commodity Trend: DBC debe estar sobre SMA200 para activar la senal SMA50.</p>
      <p style="margin-top:6px"><strong style="color:#f59e0b">SMA{SMA_EXIT} Exit Signal</strong> — Para Equity y Hard Assets: si un activo seleccionado rompe por debajo de su SMA{SMA_EXIT}, se sale a SHY (orden MOC, ~5min antes del cierre). Re-entra cuando recupera SMA{SMA_EXIT}. Coste: {TX_COST_BPS}bp por switch. ~{total_switches/max(N_ret/252,1):.0f} switches/ano.</p>
      <p style="margin-top:6px"><strong style="color:#f59e0b">SMA{SMA_EXIT} Exit Signal</strong> — Intra-mes en Equity y Hard Assets: si un activo rompe bajo su SMA{SMA_EXIT}, se sale a SHY (MOC order, ~5min before close). Re-entrada cuando vuelve sobre SMA{SMA_EXIT}. Reset en rebalanceo mensual. Coste: {TX_COST_BPS}bp/switch.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Equity (24%)</strong> — {len(UNIVERSES["Equity"])} candidatos: {", ".join(UNIVERSES["Equity"])}. <span style="color:#06b6d4">SMA200</span> + <span style="color:#f59e0b">SMA{SMA_EXIT} exit</span>.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Bonds (18%)</strong> — {len(UNIVERSES["Bonds"])} candidatos: {", ".join(UNIVERSES["Bonds"])}. <span style="color:#06b6d4">SMA200 filtered.</span></p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Hard Assets (19%)</strong> — {len(UNIVERSES["HardAssets"])} candidatos: {", ".join(UNIVERSES["HardAssets"])}. BTC-USD late-joiner (~2015). <span style="color:#06b6d4">SMA200</span> + <span style="color:#f59e0b">SMA{SMA_EXIT} exit</span>.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Long Vol (21%)</strong> — BTAL (Anti-Beta). Sin filtro SMA.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Cmdty Trend (18%)</strong> — DBC + SMA-50. <span style="color:#06b6d4">SMA200 gate</span>: DBC &lt; SMA200 → exposicion 0%.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Benchmarks</strong> — Dragon Base (sin SMA200), 60/40 (SPY+TLT fijo), S&P 500.</p>
      <p style="margin-top:6px"><strong style="color:#e2e8f0">Rf</strong> — {RF_ANNUAL*100:.1f}%.</p>
    </div>
  </div>

  <div class="footer">
    <span>SFinance-alicIA | Dragon Portfolio v3 Doble SMA | Solo fines informativos</span>
    <span>{TODAY.strftime("%Y-%m-%d")} | {len(ALL_TICKERS)} activos ({len(LATE_JOINERS)} late-joiner) | SMA200 min exposure {MIN_EXPOSURE:.0%}</span>
  </div>

</div>
</body>
</html>'''

# ═══════════════════════════════════════════════════════════════════
# 14. OUTPUT
# ═══════════════════════════════════════════════════════════════════
outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Dragon_SMA200_Backtest.html")
with open(outpath, "w", encoding="utf-8") as f:
    f.write(html)
print(f"\n  Report saved: {outpath}")

pub_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public", "reportes")
if os.path.isdir(pub_dir):
    pub_path = os.path.join(pub_dir, "Dragon_SMA200_Backtest.html")
    with open(pub_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Public copy: {pub_path}")

if not os.environ.get("CI"):
    os.system(f'open "{outpath}"')
print("\n=== Done ===")
