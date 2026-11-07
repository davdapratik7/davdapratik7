import levels_oi as L

def c(o, cl, h=None, l=None, t=0):
    return {"o": o, "c": cl, "h": h if h is not None else max(o, cl),
            "l": l if l is not None else min(o, cl), "t": t}

def test_manual_stays_exact_and_auto_merges():
    r = L.build_levels([24985], {"D": [24990, 24700, 24708], "15": [22000]}, 15)
    assert (24985, "M") in r and all(p != 24990 for p, _ in r)
    assert (24704.0, "D") in r and (22000, "15") in r
    assert [p for p, _ in r] == sorted(p for p, _ in r)

def test_dynamic_mid_respects_min_gap():
    lv = [24700, 24814, 24820, 24985]
    # spot 24816: nearest pair 24814/24820 is only 6 apart -> widen to reach gap >= 100
    m, lo, hi = L.dynamic_mid(lv, 24816, 100)
    assert hi - lo >= 100 and lo < 24816 < hi and m == (lo + hi) / 2
    assert L.dynamic_mid([100], 90, 10) is None

def test_pain_zone():
    assert L.pain_zone([24700, 24900], 24800)
    assert not L.pain_zone([24700, 24900], 24750)

def test_swings():
    h = [1, 2, 3, 2, 1, 2, 5, 2, 1]; l = [x - 1 for x in h]
    assert 3 in L.swing_levels(h, l, 2) and 5 not in L.swing_levels(h, l, 3)

LV = [24700, 24814, 24985]

def test_sell_signal_with_cascade_targets():
    k = [c(24990, 24990), c(24990, 24900),            # close below 24985 -> armed
         c(24900, 24920, l=24890),                    # green candle, low 24890
         c(24920, 24880, h=24920, l=24870)]           # low breaks 24890 -> SELL
    s = L.find_signals(k, LV)
    assert len(s) == 1 and s[0]["side"] == "SELL" and s[0]["level"] == 24985
    assert s[0]["t1"] == 24814 and s[0]["t2"] == 24700

def test_sell_cancelled_when_price_closes_back_above_level():
    k = [c(24990, 24990), c(24990, 24900), c(24900, 24920, l=24890),
         c(24920, 25000, l=24915),                    # back above 24985 -> cancel
         c(25000, 24880, h=25000, l=24870)]           # fresh breakdown, no signal yet
    assert L.find_signals(k, LV) == []

def test_timeout_cancels_setup():
    k = [c(105, 105), c(105, 95), c(95, 94), c(94, 93), c(93, 92),
         c(92, 93, l=91), c(93, 90, h=93, l=89)]
    assert L.find_signals(k, [100], wait=2) == []

def test_buy_signal():
    k = [c(100, 100), c(100, 112), c(113, 111, h=113), c(111, 115, l=111, h=114)]
    s = L.find_signals(k, [110, 120, 130])
    assert len(s) == 1 and s[0]["side"] == "BUY" and s[0]["t1"] == 120 and s[0]["t2"] == 130

def test_oi_bias():
    oc = {"100": {"ce": {"oi": 90, "previous_oi": 100}, "pe": {"oi": 150, "previous_oi": 100}}}
    assert L.oi_bias(oc, 100)[0] == "BULLISH"
    oc = {"100": {"ce": {"oi": 150, "previous_oi": 100}, "pe": {"oi": 90, "previous_oi": 100}}}
    assert L.oi_bias(oc, 100)[0] == "BEARISH"

def test_oi_walls():
    oc = {"24900": {"ce": {"oi": 10}, "pe": {"oi": 500}}, "24800": {"ce": {"oi": 5}, "pe": {"oi": 900}},
          "25000": {"ce": {"oi": 700}, "pe": {"oi": 1}}, "25100": {"ce": {"oi": 300}, "pe": {"oi": 1}}}
    cw, pw = L.oi_walls(oc, 24950)
    assert cw == (25000.0, 700) and pw == (24800.0, 900)
