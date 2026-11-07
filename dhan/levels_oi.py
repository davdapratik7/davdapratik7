#!/usr/bin/env python3
"""Level + 50% + OI planner (v2.1 logic) on the DhanHQ v2 API.

Port of the TradingView "Level + 50% + OI v2.1" indicator. OI bias is read
automatically from Dhan's option chain instead of a manual toggle.

Levels : manual (kept EXACT) + daily swings + 15m swings, merged within --merge-tol
50% lines: global (top/bottom), dynamic (nearest pair with gap >= --min-gap), pain zone
Signals: breakdown/breakout -> opposite candle -> its low/high broken (15m candles)
         T1/T2 = next levels. OI gate applies to live candles only (history = "n/a").

Setup:  pip install requests
        export DHAN_CLIENT_ID=...  DHAN_ACCESS_TOKEN=...
Run:    python levels_oi.py --symbol NIFTY --levels 22185,24985
        python levels_oi.py --symbol SENSEX --watch 60
Informational only; it places no orders. Not financial advice.
"""
import argparse, os, sys, time, datetime as dt

INDEX = {"NIFTY": 13, "BANKNIFTY": 25, "SENSEX": 51}   # IDX_I security ids
BASE = "https://api.dhan.co/v2"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


# ---------------- pure logic (no network) ----------------
def swing_levels(highs, lows, n):
    """Pivot highs/lows: the bar is the extreme of n bars on each side."""
    out = []
    for i in range(n, len(highs) - n):
        if highs[i] == max(highs[i - n:i + n + 1]):
            out.append(highs[i])
        if lows[i] == min(lows[i - n:i + n + 1]):
            out.append(lows[i])
    return out


def build_levels(manual, auto_by_src, tol):
    """Manual levels stay exact. Auto levels within tol of a manual level are dropped;
    the rest are clustered (averaged) within tol. Returns sorted [(price, src)]."""
    man = sorted(set(manual))
    items = sorted((v, s) for s, vs in auto_by_src.items() for v in vs
                   if all(abs(v - m) > tol for m in man))
    out = [(m, "M") for m in man]
    cur = cnt = tag = None
    for v, s in items:
        if cur is not None and v - cur <= tol:
            cur = (cur * cnt + v) / (cnt + 1)
            cnt += 1
        else:
            if cur is not None:
                out.append((round(cur, 2), tag))
            cur, cnt, tag = v, 1, s
    if cur is not None:
        out.append((round(cur, 2), tag))
    return sorted(out)


def below(levels, x):
    c = [v for v in levels if v < x]
    return max(c) if c else None


def above(levels, x):
    c = [v for v in levels if v > x]
    return min(c) if c else None


def dynamic_mid(levels, spot, min_gap):
    """50% of the nearest pair around spot whose gap >= min_gap (widens outward)."""
    lo_c = sorted((v for v in levels if v < spot), reverse=True)
    hi_c = sorted(v for v in levels if v > spot)
    if not lo_c or not hi_c:
        return None
    i = j = 0
    while hi_c[j] - lo_c[i] < min_gap:
        can_lo, can_hi = i + 1 < len(lo_c), j + 1 < len(hi_c)
        if can_lo and (not can_hi or spot - lo_c[i + 1] <= hi_c[j + 1] - spot):
            i += 1
        elif can_hi:
            j += 1
        else:
            return None
    return (lo_c[i] + hi_c[j]) / 2, lo_c[i], hi_c[j]


def pain_zone(levels, spot, frac=0.02):
    for a, b in zip(levels, levels[1:]):
        d = b - a
        if d > 0 and abs(spot - (a + b) / 2) <= d * frac:
            return True
    return False


def find_signals(candles, levels, wait=10):
    """State machine (candles: dicts o,h,l,c,t).
    SELL: close below a level -> green candle -> a later low breaks that green low.
    BUY : close above a level -> red candle   -> a later high breaks that red high.
    Either setup is cancelled by price closing back through the level or by timeout."""
    sigs = []
    bs = us = 0                     # state: 0 idle, 1 wait candle, 2 wait break
    b_start = u_start = 0
    b_lvl = u_lvl = g_low = r_high = None
    for i in range(1, len(candles)):
        p, k = candles[i - 1], candles[i]
        sup, res = below(levels, p["c"]), above(levels, p["c"])
        if sup is not None and k["c"] < sup:
            bs, b_start, b_lvl, g_low = 1, i, sup, None
        if res is not None and k["c"] > res:
            us, u_start, u_lvl, r_high = 1, i, res, None

        if bs == 1 and i > b_start:
            if k["c"] > b_lvl:
                bs = 0
            elif k["c"] > k["o"]:
                bs, g_low = 2, k["l"]
            elif i - b_start > wait:
                bs = 0
        if bs == 2:
            if k["l"] < g_low:
                t1 = below(levels, b_lvl)
                sigs.append({"side": "SELL", "idx": i, "t": k["t"], "px": k["c"], "level": b_lvl,
                             "t1": t1, "t2": below(levels, t1) if t1 is not None else None})
                bs = 0
            elif k["c"] > b_lvl or i - b_start > wait * 2:
                bs = 0

        if us == 1 and i > u_start:
            if k["c"] < u_lvl:
                us = 0
            elif k["c"] < k["o"]:
                us, r_high = 2, k["h"]
            elif i - u_start > wait:
                us = 0
        if us == 2:
            if k["h"] > r_high:
                t1 = above(levels, u_lvl)
                sigs.append({"side": "BUY", "idx": i, "t": k["t"], "px": k["c"], "level": u_lvl,
                             "t1": t1, "t2": above(levels, t1) if t1 is not None else None})
                us = 0
            elif k["c"] < u_lvl or i - u_start > wait * 2:
                us = 0
    return sigs


def oi_bias(oc, spot, strikes_each_side=5):
    """oc = Dhan option-chain 'oc' dict {strike: {ce:{oi,previous_oi}, pe:{...}}}."""
    strikes = sorted(oc, key=float)
    atm = min(range(len(strikes)), key=lambda i: abs(float(strikes[i]) - spot))
    near = strikes[max(0, atm - strikes_each_side): atm + strikes_each_side + 1]
    chg = lambda s, side: oc[s].get(side, {}).get("oi", 0) - oc[s].get(side, {}).get("previous_oi", 0)
    ce, pe = sum(chg(s, "ce") for s in near), sum(chg(s, "pe") for s in near)
    if pe > 0 and ce <= 0:
        bias = "BULLISH"        # put writers adding, call writers exiting
    elif ce > 0 and pe <= 0:
        bias = "BEARISH"        # call writers adding, put writers exiting
    elif pe > ce:
        bias = "LEAN BULLISH"
    elif ce > pe:
        bias = "LEAN BEARISH"
    else:
        bias = "NEUTRAL"
    return bias, ce, pe


def oi_walls(oc, spot):
    """Call wall = strike above spot with the most CE OI (resistance);
    put wall = strike below spot with the most PE OI (support)."""
    up = [(float(k), v.get("ce", {}).get("oi", 0)) for k, v in oc.items() if float(k) > spot]
    dn = [(float(k), v.get("pe", {}).get("oi", 0)) for k, v in oc.items() if float(k) < spot]
    cw = max(up, key=lambda x: x[1]) if up else None
    pw = max(dn, key=lambda x: x[1]) if dn else None
    return cw, pw


# ---------------- Dhan API ----------------
def _post(path, body):
    import requests
    cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        sys.exit("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN")
    r = requests.post(BASE + path, json=body, timeout=20,
                      headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json"})
    if not r.ok:
        sys.exit(f"Dhan API {path} -> HTTP {r.status_code}: {r.text[:300]}")
    return r.json()


def _to_candles(d):
    return [{"o": o, "h": h, "l": l, "c": c, "t": t}
            for o, h, l, c, t in zip(d["open"], d["high"], d["low"], d["close"], d["timestamp"])]


def fetch_daily(sec_id, days=400):
    to = dt.date.today()
    return _to_candles(_post("/charts/historical", {
        "securityId": str(sec_id), "exchangeSegment": "IDX_I", "instrument": "INDEX",
        "expCode": 0, "oi": False,
        "fromDate": str(to - dt.timedelta(days=days)), "toDate": str(to)}))


def fetch_15m(sec_id, days=10):
    to = dt.date.today() + dt.timedelta(days=1)
    return _to_candles(_post("/charts/intraday", {
        "securityId": str(sec_id), "exchangeSegment": "IDX_I", "instrument": "INDEX",
        "interval": "15", "fromDate": str(to - dt.timedelta(days=days)), "toDate": str(to)}))


def fetch_option_chain(sec_id):
    exp = _post("/optionchain/expirylist", {"UnderlyingScrip": sec_id, "UnderlyingSeg": "IDX_I"})["data"][0]
    d = _post("/optionchain", {"UnderlyingScrip": sec_id, "UnderlyingSeg": "IDX_I", "Expiry": exp})["data"]
    return d["last_price"], d["oc"], exp


# ---------------- report ----------------
def fmt_t(t):
    try:
        return dt.datetime.fromtimestamp(t, IST).strftime("%d-%b %H:%M")
    except Exception:
        return str(t)


def run(a):
    sec = INDEX[a.symbol]
    manual = [float(x) for x in a.levels.split(",") if x.strip()]
    auto = {}
    if not a.no_auto:
        daily = fetch_daily(sec)
        auto["D"] = swing_levels([c["h"] for c in daily], [c["l"] for c in daily], a.dpiv)[-a.max_daily:]
    m15 = fetch_15m(sec)
    if not a.no_auto:
        auto["15"] = swing_levels([c["h"] for c in m15], [c["l"] for c in m15], a.p15)[-20:]
    spot, oc, exp = fetch_option_chain(sec)
    bias, ce, pe = oi_bias(oc, spot)

    lv = [(p, s) for p, s in build_levels(manual, auto, a.merge_tol)
          if abs(p - spot) / spot * 100 <= a.range_pct]
    prices = [p for p, _ in lv]
    print(f"\n{a.symbol}  spot {spot:.2f}  expiry {exp}")
    if not prices:
        print("  no levels in range")
        return
    top, bot = prices[-1], prices[0]
    for p, s in sorted(lv, reverse=True):
        tag = "BULL zone" if p == top else "BEAR zone" if p == bot else s
        print(f"  {p:>10.2f}  {tag:<10}{'  <-- spot' if abs(p - spot) < 30 else ''}")
    print(f"  Global 50%  : {(top + bot) / 2:.2f}")
    dm = dynamic_mid(prices, spot, a.min_gap)
    print(f"  Dynamic 50% : {dm[0]:.2f}  (between {dm[1]:.2f} and {dm[2]:.2f})" if dm else "  Dynamic 50% : n/a")
    print(f"  Pain zone   : {'ACTIVE' if pain_zone(prices, spot) else '-'}")
    print(f"  OI bias     : {bias}  (CE chg {ce:+,.0f}, PE chg {pe:+,.0f})")
    cwall, pwall = oi_walls(oc, spot)
    if cwall:
        print(f"  Call wall   : {cwall[0]:.0f}  (CE OI {cwall[1]:,.0f})  <- OI resistance")
    if pwall:
        print(f"  Put wall    : {pwall[0]:.0f}  (PE OI {pwall[1]:,.0f})  <- OI support")
    if a.chartscript:
        print(f"  ChartScript inputs -> Call OI change: {ce:.0f}   Put OI change: {pe:.0f}")

    for s in find_signals(m15, prices, a.wait)[-3:]:
        live = s["idx"] >= len(m15) - a.live_bars
        want = "BULL" if s["side"] == "BUY" else "BEAR"
        gate = ("OI OK" if want in bias else "OI NOT confirmed") if live else "history (OI n/a)"
        print(f"  15m {s['side']:<4} @ {s['px']:.2f} lvl {s['level']:.2f}  "
              f"T1 {s['t1']} T2 {s['t2']}  [{gate}]  {fmt_t(s['t'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=INDEX, default="NIFTY")
    ap.add_argument("--levels", default="", help="manual levels, comma separated (kept exact)")
    ap.add_argument("--no-auto", action="store_true", help="manual levels only")
    ap.add_argument("--dpiv", type=int, default=5, help="daily swing length")
    ap.add_argument("--p15", type=int, default=5, help="15m swing length")
    ap.add_argument("--max-daily", type=int, default=20)
    ap.add_argument("--merge-tol", type=float, default=15)
    ap.add_argument("--min-gap", type=float, default=100)
    ap.add_argument("--range-pct", type=float, default=6)
    ap.add_argument("--wait", type=int, default=10, help="max candles to wait for the setup")
    ap.add_argument("--live-bars", type=int, default=2, help="latest N candles count as live for OI gate")
    ap.add_argument("--watch", type=int, default=0, help="repeat every N seconds (min 3: option chain rate limit)")
    ap.add_argument("--chartscript", action="store_true", help="print callChg/putChg to type into the ChartScript inputs")
    a = ap.parse_args()
    while True:
        run(a)
        if not a.watch:
            break
        time.sleep(max(a.watch, 3))


if __name__ == "__main__":
    main()
