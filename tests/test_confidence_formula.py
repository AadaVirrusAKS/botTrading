"""Quick test to verify confidence formula produces proper spread."""

def calc_confidence(score, vol_ratio, oi, rsi, direction):
    base = int(30 + score * 4)
    if vol_ratio >= 1.5:
        base += 5
    elif vol_ratio < 0.5:
        base -= 10
    if oi >= 5000:
        base += 3
    elif oi < 500:
        base -= 5
    if direction == 'BULLISH' and rsi > 70:
        base -= 8
    elif direction == 'BEARISH' and rsi < 30:
        base -= 8
    return min(95, max(40, base))

def passes_filter(vol_ratio, oi):
    """Options filter: skip only if BOTH vol_ratio dead AND OI dead."""
    return not (vol_ratio < 0.3 and oi < 100)

def test_after_hours_options():
    """After hours: vol_ratio=0 but high OI should still pass with varied confidence."""
    cases = [
        (14, 0.0, 87670, 46.9, 'BEARISH'),  # NVDA
        (13, 0.0, 22547, 38.7, 'BEARISH'),  # AMD
        (13, 0.0, 5668, 41.8, 'BEARISH'),   # TSLA
        (12, 0.0, 3227, 38.2, 'BEARISH'),   # SLV
        (11, 0.0, 52689, 42.8, 'BEARISH'),  # SPY
    ]
    confidences = []
    for score, vr, oi, rsi, d in cases:
        assert passes_filter(vr, oi), f"OI={oi} should pass filter"
        conf = calc_confidence(score, vr, oi, rsi, d)
        confidences.append(conf)
        assert 40 <= conf <= 95, f"conf={conf} out of range"
    
    # Ensure spread: not all the same
    assert len(set(confidences)) > 1, f"All confidences identical: {confidences}"
    # NVDA (score 14, high OI) should be highest
    assert confidences[0] > confidences[-1], f"Higher score should have higher conf: {confidences}"
    print(f"After-hours confidences: {confidences}")

def test_market_hours_options():
    """Market hours: vol_ratio > 0 should produce varied confidence."""
    cases = [
        (15, 2.0, 10000, 55.0, 'BULLISH'),
        (12, 1.0, 2000, 65.0, 'BULLISH'),
        (9, 0.5, 300, 72.0, 'BULLISH'),
        (7, 0.3, 50, 80.0, 'BULLISH'),
    ]
    for score, vr, oi, rsi, d in cases:
        conf = calc_confidence(score, vr, oi, rsi, d)
        print(f"  score={score} vol={vr} oi={oi} rsi={rsi} -> conf={conf}%")
        assert 40 <= conf <= 95

def test_filter_blocks_illiquid():
    """Should skip when BOTH vol_ratio dead AND OI tiny."""
    assert not passes_filter(0.0, 50), "Should block vol=0, oi=50"
    assert not passes_filter(0.1, 0), "Should block vol=0.1, oi=0"
    
def test_filter_passes_liquid():
    """Should pass when OI is decent even with 0 volume."""
    assert passes_filter(0.0, 500), "OI=500 should pass"
    assert passes_filter(0.0, 5000), "OI=5000 should pass"
    assert passes_filter(1.5, 50), "High vol should pass"

def test_no_identical_98():
    """The old bug: all options got 98%. Verify this can't happen."""
    scores = [11, 12, 13, 14]
    confs = [calc_confidence(s, 0.0, 5000, 45.0, 'BEARISH') for s in scores]
    assert 98 not in confs, f"98% should never appear: {confs}"
    assert len(set(confs)) > 1, f"All identical: {confs}"
    print(f"Confidences for scores {scores}: {confs}")

if __name__ == '__main__':
    test_after_hours_options()
    test_market_hours_options()
    test_filter_blocks_illiquid()
    test_filter_passes_liquid()
    test_no_identical_98()
    print("\n✅ All confidence formula tests passed!")
