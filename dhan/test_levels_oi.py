import levels_oi as L

def c(o, cl, h=None, l=None, t=0):
    return {"o": o, "c": cl, "h": h or max(o, cl), "l": l or min(o, cl), "t": t}

def test_midpoints_and_neighbours():
    lv = [22185, 24985, 24814, 24700]
    s = L.merge_levels(lv)
    assert s == [22185, 24700, 24814, 24985]
    assert L.midpoints(s, 100) == [((22185+24700)/2, 22185, 24700), ((24700+24814)/2, 24700, 24814), ((24814+24985)/2, 24814, 24985)]
    assert L.below(s, 24814) == 24700 and L.above(s, 24814) == 24985

def test_swings():
    h = [1,2,3,2,1,2,5,2,1]; l = [x-1 for x in h]
    assert 3 in L.swing_levels(h, l, 2) and 5 not in L.swing_levels(h, l, 3)

def test_short_signal_cascade():
    lv = [24700, 24814, 24985]
    k = [c(24990, 24990), c(24990, 24900),            # close below 24985 -> armed
         c(24900, 24920, l=24890),                    # green candle, low 24890
         c(24920, 24880, h=24920, l=24870)]           # close < 24890 -> SELL
    s = L.find_signals(k, lv)
    assert s and s[0][0] == "SELL" and s[0][3] == 24985 and s[0][4] == 24814 and s[0][5] == 24700

def test_failed_breakdown():
    k = [c(24990, 24990), c(24990, 24900), c(24900, 25000)]
    assert L.find_signals(k, [24985, 24814]) == []

def test_long_signal():
    k = [c(100, 100), c(100, 112), c(113, 111, h=113), c(108, 115, l=107)]
    s = L.find_signals(k, [110, 120, 130])
    assert s and s[0][0] == "BUY" and s[0][4] == 120

def test_oi_bias():
    oc = {"100": {"ce": {"oi": 90, "previous_oi": 100}, "pe": {"oi": 150, "previous_oi": 100}}}
    assert L.oi_bias(oc, 100)[0] == "BULLISH"
    oc = {"100": {"ce": {"oi": 150, "previous_oi": 100}, "pe": {"oi": 90, "previous_oi": 100}}}
    assert L.oi_bias(oc, 100)[0] == "BEARISH"
