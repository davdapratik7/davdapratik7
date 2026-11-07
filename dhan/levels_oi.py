#!/usr/bin/env python3
"""Levels + 50% midpoint + OI-confirmed level-to-level plan, using the DhanHQ v2 API.

Same framework as tradingview/levels_50pct_oi.pine, but OI bias is read
automatically from Dhan's option chain.

Setup:  pip install requests
        export DHAN_CLIENT_ID=...  DHAN_ACCESS_TOKEN=...
Run:    python levels_oi.py --symbol NIFTY --levels 22185,24985
        python levels_oi.py --symbol SENSEX --watch 60
Not financial advice. Verify against your own charts before trading.
"""
import argparse, os, sys, time, datetime as dt

# IDX_I security ids on Dhan: NIFTY=13, BANKNIFTY=25, SENSEX=51
INDEX = {"NIFTY": 13, "BANKNIFTY": 25, "SENSEX": 51}
BASE = "https://api.dhan.co/v2"


# ---------------- pure logic (no network) ----------------
def swing_levels(highs, lows, n):
    """Pivot highs/lows: bar is the extreme of n bars on each side."""
    out = []
    for i in range(n, len(highs) - n):
        if highs[i] == max(highs[i - n:i + n + 1]):
            out.append(highs[i])
        if lows[i] == min(lows[i - n:i + n + 1]):
            out.append(lows[i])
    return out


def merge_levels(levels, tol=0.0):
    out = []
    for v in sorted(set(round(x, 2) for x in levels)):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def midpoints(levels, min_gap):
    return [((a + b) / 2, a, b) for a, b in zip(levels, levels[1:]) if b - a >= min_gap]


def below(levels, x):
    c = [v for v in levels if v < x]
    return max(c) if c else None


def above(levels, x):
    c = [v for v in levels if v > x]
    return min(c) if c else None


def find_signals(candles, levels):
    """candles: list of dicts o,h,l,c,t. Mirrors the Pine state machine.
    Short: close below level -> green candle forms -> its low breaks.
    Long : close above level -> red candle forms -> its high breaks."""
    sigs = []
    short = long_ = False
    brk_s = brk_l = green_low = red_high = None
    for i in range(1, len(candles)):
        p, k = candles[i - 1], candles[i]
        for L in levels:
            if p["c"] >= L > k["c"]:
                short, long_, brk_s, green_low = True, False, L, None
            if p["c"] <= L < k["c"]:
                long_, short, brk_l, red_high = True, False, L, None
        if short:
            if k["c"] > brk_s:
                short = False
            elif green_low is not None and k["c"] < green_low:
                t1 = below(levels, brk_s)
                t2 = below(levels, t1) if t1 else None
                sigs.append(("SELL", k["t"], k["c"], brk_s, t1, t2))
                short = False
            elif k["c"] > k["o"]:
                green_low = k["l"]
        if long_:
            if k["c"] < brk_l:
                long_ = False
            elif red_high is not None and k["c"] > red_high:
                t1 = above(levels, brk_l)
                t2 = above(levels, t1) if t1 else None
                sigs.append(("BUY", k["t"], k["c"], brk_l, t1, t2))
                long_ = False
            elif k["c"] < k["o"]:
                red_high = k["h"]
    return sigs


def oi_bias(oc, spot, strikes_each_side=5):
    """oc: Dhan option-chain 'oc' dict {strike: {ce:{oi,previous_oi}, pe:{...}}}.
    Sums OI change over strikes around spot."""
    strikes = sorted(oc, key=float)
    atm = min(range(len(strikes)), key=lambda i: abs(float(strikes[i]) - spot))
    near = strikes[max(0, atm - strikes_each_side): atm + strikes_each_side + 1]
    ce = sum(oc[s].get("ce", {}).get("oi", 0) - oc[s].get("ce", {}).get("previous_oi", 0) for s in near)
    pe = sum(oc[s].get("pe", {}).get("oi", 0) - oc[s].get("pe", {}).get("previous_oi", 0) for s in near)
    if pe > 0 and ce <= 0:
        bias = "BULLISH"   # put writers adding, call writers exiting
    elif ce > 0 and pe <= 0:
        bias = "BEARISH"   # call writers adding, put writers exiting
    elif pe > ce:
        bias = "LEAN BULLISH"
    elif ce > pe:
        bias = "LEAN BEARISH"
    else:
        bias = "NEUTRAL"
    return bias, ce, pe


# ---------------- Dhan API ----------------
def _post(path, body):
    import requests
    cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        sys.exit("Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN")
    r = requests.post(BASE + path, json=body, timeout=20,
                      headers={"access-token": tok, "client-id": cid, "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


def _to_candles(d):
    return [{"o": o, "h": h, "l": l, "c": c, "t": t}
            for o, h, l, c, t in zip(d["open"], d["high"], d["low"], d["close"], d["timestamp"])]


def fetch_daily(sec_id, days=400):
    to = dt.date.today()
    d = _post("/charts/historical", {"securityId": str(sec_id), "exchangeSegment": "IDX_I",
              "instrument": "INDEX", "expCode": 0, "oi": False,
              "fromDate": str(to - dt.timedelta(days=days)), "toDate": str(to)})
    return _to_candles(d)


def fetch_15m(sec_id, days=5):
    to = dt.date.today() + dt.timedelta(days=1)
    d = _post("/charts/intraday", {"securityId": str(sec_id), "exchangeSegment": "IDX_I",
              "instrument": "INDEX", "interval": "15",
              "fromDate": str(to - dt.timedelta(days=days)), "toDate": str(to)})
    return _to_candles(d)


def fetch_option_chain(sec_id):
    exp = _post("/optionchain/expirylist", {"UnderlyingScrip": sec_id, "UnderlyingSeg": "IDX_I"})["data"][0]
    d = _post("/optionchain", {"UnderlyingScrip": sec_id, "UnderlyingSeg": "IDX_I", "Expiry": exp})["data"]
    return d["last_price"], d["oc"], exp


# ---------------- report ----------------
def run(args):
    sec = INDEX[args.symbol]
    daily = fetch_daily(sec)
    levels = [float(x) for x in args.levels.split(",") if x.strip()]
    if not args.no_auto:
        levels += swing_levels([c["h"] for c in daily], [c["l"] for c in daily], args.swing)
    spot, oc, exp = fetch_option_chain(sec)
    levels = [v for v in merge_levels(levels) if abs(v - spot) / spot * 100 <= args.range_pct]
    bias, ce, pe = oi_bias(oc, spot)

    print(f"\n{args.symbol}  spot {spot:.2f}  expiry {exp}")
    mids = {round(m, 2): (a, b) for m, a, b in midpoints(levels, args.min_gap)}
    top, bot = (levels[-1], levels[0]) if levels else (None, None)
    for v in sorted(set(levels) | set(mids), reverse=True):
        tag = "50%" if v in mids else ("BULL (green)" if v == top else "BEAR (red)" if v == bot else "level")
        print(f"  {v:>10.2f}  {tag}{'   <-- spot nearby' if abs(v - spot) < 30 else ''}")
    print(f"OI bias: {bias}  (CE chg {ce:+,.0f}, PE chg {pe:+,.0f})")

    for s in find_signals(fetch_15m(sec), levels)[-3:]:
        side, t, px, brk, t1, t2 = s
        ok = (side == "BUY" and "BULL" in bias) or (side == "SELL" and "BEAR" in bias)
        print(f"  15m {side} @ {px:.2f} (level {brk:.2f}) T1 {t1} T2 {t2}  OI {'OK' if ok else 'NOT confirmed'}  t={t}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=INDEX, default="NIFTY")
    ap.add_argument("--levels", default="", help="manual levels, comma separated")
    ap.add_argument("--no-auto", action="store_true", help="skip auto daily swing levels")
    ap.add_argument("--swing", type=int, default=10)
    ap.add_argument("--min-gap", type=float, default=100)
    ap.add_argument("--range-pct", type=float, default=6)
    ap.add_argument("--watch", type=int, default=0, help="repeat every N seconds")
    a = ap.parse_args()
    while True:
        run(a)
        if not a.watch:
            break
        time.sleep(a.watch)


if __name__ == "__main__":
    main()
