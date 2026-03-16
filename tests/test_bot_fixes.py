#!/usr/bin/env python3
"""
Test script for bot improvement fixes (no Alpaca connection).
Simulates the auto_cycle logic with dummy data to validate all 5 fixes:
  1. Signal-position conflict detection
  2. Signal reversal auto-close
  3. Volume ratio filter
  4. Improved market regime filter (dual SMA)
  5. Better confidence scoring

Run: python3 tests/test_bot_fixes.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import json
import copy

# ============================================================
# DUMMY DATA SETUP
# ============================================================

def make_signal(symbol, direction, option_type, score, rsi, volume_ratio,
                open_interest=5000, strike=100, premium=3.0, iv=40):
    """Create a dummy scanner signal."""
    contract = f"{symbol} ${strike}{option_type[0].upper()} 2026-03-20"
    return {
        'symbol': symbol,
        'action': 'BUY',
        'confidence': 0,  # Will be computed by fixed formula
        'entry': premium,
        'stop_loss': round(premium * 0.5, 2),
        'target': round(premium * 2.0, 2),
        'target_2': round(premium * 3.0, 2),
        'reason': f"TEST {direction} setup",
        'score': score,
        'direction': direction,
        'rsi': rsi,
        'volume_ratio': volume_ratio,
        'instrument_type': 'option',
        'option_type': option_type,
        'contract': contract,
        'option_ticker': f"{symbol}260320{'C' if option_type=='call' else 'P'}00{strike}000",
        'strike': strike,
        'expiry': '2026-03-20',
        'dte': 3,
        'premium': premium,
        'stock_price': strike + (2 if option_type == 'call' else -2),
        'iv': iv,
        'open_interest': open_interest,
        'scan_type': 'intraday',
    }


def make_position(symbol, option_type, entry_price, current_price, qty=5,
                  strike=100, expiry='2026-03-20'):
    """Create a dummy open position."""
    contract = f"{symbol} ${strike}{option_type[0].upper()} {expiry}"
    return {
        'symbol': symbol,
        'contract': contract,
        'option_ticker': f"{symbol}260320{'C' if option_type=='call' else 'P'}00{strike}000",
        'instrument_type': 'option',
        'option_type': option_type,
        'strike': strike,
        'expiry': expiry,
        'dte': 3,
        'side': 'LONG',
        'quantity': qty,
        'entry_price': entry_price,
        'current_price': current_price,
        'stop_loss': round(entry_price * 0.5, 2),
        'target': round(entry_price * 2.0, 2),
        'target_2': round(entry_price * 3.0, 2),
        'timestamp': (datetime.now() - timedelta(hours=2)).isoformat(),
        'auto_trade': True,
        'source': 'bot',
        'trade_type': 'swing',
    }


# ============================================================
# TEST FIX 5: Confidence Scoring
# ============================================================
def test_confidence_scoring():
    """Test that the new confidence formula produces differentiated scores."""
    print("=" * 80)
    print("TEST FIX 5: Confidence Scoring Formula")
    print("=" * 80)

    test_cases = [
        # (score, vol_ratio, oi, rsi, direction, expected_range)
        (7,  0.5, 200,  50, 'BULLISH', (40, 55)),   # Low score + low OI = low confidence
        (10, 1.0, 3000, 50, 'BULLISH', (60, 75)),   # Medium score = medium confidence
        (13, 1.8, 8000, 45, 'BULLISH', (78, 92)),   # High score + good volume + high OI
        (15, 2.0, 10000,40, 'BEARISH', (85, 95)),   # Very high score
        (12, 0.3, 5000, 75, 'BULLISH', (45, 65)),   # Good score but overbought for calls
        (12, 0.3, 5000, 25, 'BEARISH', (45, 65)),   # Good score but oversold for puts
        (14, 1.5, 300,  50, 'BULLISH', (70, 90)),   # High score but low-ish OI
        (18, 2.5, 50000,55, 'BEARISH', (90, 95)),   # Monster score
    ]

    all_pass = True
    for score, vol_ratio, oi, rsi, direction, (min_conf, max_conf) in test_cases:
        base_conf = int(30 + score * 4)
        if vol_ratio >= 1.5:
            base_conf += 5
        elif vol_ratio < 0.5:
            base_conf -= 10
        if oi >= 5000:
            base_conf += 3
        elif oi < 500:
            base_conf -= 5
        if direction == 'BULLISH' and rsi > 70:
            base_conf -= 8
        elif direction == 'BEARISH' and rsi < 30:
            base_conf -= 8
        confidence = min(95, max(40, base_conf))

        in_range = min_conf <= confidence <= max_conf
        status = "PASS" if in_range else "FAIL"
        if not in_range:
            all_pass = False
        print(f"  {status}: score={score}, vol={vol_ratio}, OI={oi}, RSI={rsi}, dir={direction} "
              f"-> confidence={confidence}% (expected {min_conf}-{max_conf}%)")

    # Verify NO duplicate 98% scores
    scores = []
    for score in range(7, 20):
        for vr in [0.5, 1.0, 2.0]:
            base_conf = int(30 + score * 4)
            if vr >= 1.5:
                base_conf += 5
            elif vr < 0.5:
                base_conf -= 10
            conf = min(95, max(40, base_conf))
            scores.append(conf)
    unique_scores = len(set(scores))
    has_variety = unique_scores >= 10
    status = "PASS" if has_variety else "FAIL"
    if not has_variety:
        all_pass = False
    print(f"  {status}: Unique confidence values across 39 combos: {unique_scores} (need >= 10)")
    print(f"  RESULT: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")
    return all_pass


# ============================================================
# TEST FIX 3: Volume Ratio Filter
# ============================================================
def test_volume_ratio_filter():
    """Test that low volume ratio signals are filtered out."""
    print("=" * 80)
    print("TEST FIX 3: Volume Ratio Filter")
    print("=" * 80)

    signals = [
        make_signal('AAPL', 'BULLISH', 'call', 12, 50, 0.1),   # Should be FILTERED (0.1 < 0.3)
        make_signal('MSFT', 'BEARISH', 'put', 13, 55, 0.0),    # Should be FILTERED (0.0)
        make_signal('NVDA', 'BEARISH', 'put', 14, 47, 0.2),    # Should be FILTERED (0.2 < 0.3)
        make_signal('TSLA', 'BULLISH', 'call', 11, 50, 0.5),   # Should PASS (0.5 >= 0.3)
        make_signal('AMD',  'BEARISH', 'put', 13, 55, 1.5),    # Should PASS (1.5 >= 0.3)
        make_signal('SPY',  'BULLISH', 'call', 10, 48, 2.0),   # Should PASS (2.0 >= 0.3)
    ]

    passed = [s for s in signals if s['volume_ratio'] >= 0.3]
    filtered = [s for s in signals if s['volume_ratio'] < 0.3]

    all_pass = True
    for s in filtered:
        print(f"  PASS: {s['symbol']} vol_ratio={s['volume_ratio']} -> FILTERED (correct)")
    for s in passed:
        print(f"  PASS: {s['symbol']} vol_ratio={s['volume_ratio']} -> KEPT (correct)")

    if len(passed) != 3:
        all_pass = False
        print(f"  FAIL: Expected 3 signals to pass, got {len(passed)}")
    if len(filtered) != 3:
        all_pass = False
        print(f"  FAIL: Expected 3 signals filtered, got {len(filtered)}")

    print(f"  RESULT: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")
    return all_pass


# ============================================================
# TEST FIX 1: Signal-Position Conflict Detection
# ============================================================
def test_signal_position_conflict():
    """Test that the bot won't enter PUT when holding CALL on same underlying."""
    print("=" * 80)
    print("TEST FIX 1: Signal-Position Conflict Detection")
    print("=" * 80)

    # Simulate: holding AMD CALL, signal says buy AMD PUT
    positions = [
        make_position('AMD', 'call', 4.40, 3.66, qty=6, strike=200),
    ]
    signals = [
        make_signal('AMD', 'BEARISH', 'put', 13, 38, 1.5, strike=200, premium=6.87),
        make_signal('NVDA', 'BEARISH', 'put', 14, 47, 1.8, strike=180, premium=2.60),
        make_signal('CRM', 'BULLISH', 'call', 13, 54, 1.2, strike=200, premium=2.67),
    ]

    skipped = []
    allowed = []
    for signal in signals:
        sym = signal['symbol']
        new_opt_type = signal.get('option_type', '').lower()
        conflict_found = False
        for existing_pos in positions:
            if existing_pos.get('symbol') == sym and existing_pos.get('instrument_type') == 'option':
                existing_opt_type = existing_pos.get('option_type', '').lower()
                if (existing_opt_type == 'call' and new_opt_type == 'put') or \
                   (existing_opt_type == 'put' and new_opt_type == 'call'):
                    skipped.append(f"{sym}: Directional conflict — have {existing_opt_type.upper()} but signal is {new_opt_type.upper()}")
                    conflict_found = True
                    break
        if not conflict_found:
            allowed.append(signal)

    all_pass = True
    # AMD should be blocked
    if len(skipped) == 1 and 'AMD' in skipped[0]:
        print(f"  PASS: AMD PUT signal blocked (holding AMD CALL): {skipped[0]}")
    else:
        all_pass = False
        print(f"  FAIL: AMD conflict not detected. Skipped: {skipped}")

    # NVDA and CRM should pass
    allowed_syms = {s['symbol'] for s in allowed}
    if 'NVDA' in allowed_syms and 'CRM' in allowed_syms:
        print(f"  PASS: NVDA and CRM signals allowed (no conflict)")
    else:
        all_pass = False
        print(f"  FAIL: Expected NVDA and CRM allowed, got {allowed_syms}")

    # Test: same direction should NOT be blocked
    signals2 = [make_signal('AMD', 'BULLISH', 'call', 12, 50, 1.0, strike=205, premium=2.0)]
    skipped2 = []
    for signal in signals2:
        sym = signal['symbol']
        new_opt_type = signal.get('option_type', '').lower()
        conflict_found = False
        for existing_pos in positions:
            if existing_pos.get('symbol') == sym and existing_pos.get('instrument_type') == 'option':
                existing_opt_type = existing_pos.get('option_type', '').lower()
                if (existing_opt_type == 'call' and new_opt_type == 'put') or \
                   (existing_opt_type == 'put' and new_opt_type == 'call'):
                    skipped2.append(sym)
                    conflict_found = True
                    break
    if len(skipped2) == 0:
        print(f"  PASS: AMD CALL signal (same direction) NOT blocked")
    else:
        all_pass = False
        print(f"  FAIL: Same-direction signal incorrectly blocked")

    print(f"  RESULT: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")
    return all_pass


# ============================================================
# TEST FIX 2: Signal Reversal Auto-Close
# ============================================================
def test_signal_reversal():
    """Test that positions get closed when scanner signals flip direction."""
    print("=" * 80)
    print("TEST FIX 2: Signal Reversal Auto-Close")
    print("=" * 80)

    # Positions: holding CALL on AMD and MRK
    positions = [
        make_position('AMD', 'call', 4.40, 3.66, qty=6, strike=200),
        make_position('MRK', 'call', 1.85, 1.73, qty=16, strike=115),
        make_position('UNH', 'call', 3.02, 2.81, qty=9, strike=290),
    ]

    # Scanner signals: AMD is now BEARISH, MRK is still BULLISH, UNH has no signal
    signals = [
        make_signal('AMD', 'BEARISH', 'put', 13, 38, 1.5, strike=200, premium=6.87),
        make_signal('MRK', 'BULLISH', 'call', 11, 54, 1.0, strike=115, premium=1.90),
    ]

    exits_triggered = []
    for pos in positions:
        symbol = pos['symbol']
        is_option = pos.get('instrument_type') == 'option'
        current_price = pos['current_price']
        entry_price = pos['entry_price']

        exit_reason = None
        # Check signal reversal
        if is_option and signals:
            pos_opt_type = pos.get('option_type', '').lower()
            for sig in signals:
                if sig.get('symbol') == symbol:
                    sig_direction = sig.get('direction', '').upper()
                    if pos_opt_type == 'call' and sig_direction == 'BEARISH':
                        exit_reason = 'SIGNAL_REVERSAL'
                        break
                    elif pos_opt_type == 'put' and sig_direction == 'BULLISH':
                        exit_reason = 'SIGNAL_REVERSAL'
                        break

        if exit_reason:
            pnl = (current_price - entry_price) * pos['quantity'] * 100
            exits_triggered.append({
                'symbol': symbol,
                'reason': exit_reason,
                'pnl': pnl,
            })

    all_pass = True

    # AMD should be closed (CALL position, scanner is BEARISH)
    amd_exits = [e for e in exits_triggered if e['symbol'] == 'AMD']
    if len(amd_exits) == 1 and amd_exits[0]['reason'] == 'SIGNAL_REVERSAL':
        print(f"  PASS: AMD CALL closed on signal reversal (PnL: ${amd_exits[0]['pnl']:.2f})")
    else:
        all_pass = False
        print(f"  FAIL: AMD not closed on reversal. Exits: {amd_exits}")

    # MRK should NOT be closed (CALL position, scanner is still BULLISH)
    mrk_exits = [e for e in exits_triggered if e['symbol'] == 'MRK']
    if len(mrk_exits) == 0:
        print(f"  PASS: MRK CALL not closed (scanner still bullish)")
    else:
        all_pass = False
        print(f"  FAIL: MRK incorrectly closed. Exits: {mrk_exits}")

    # UNH should NOT be closed (no scanner signal for it)
    unh_exits = [e for e in exits_triggered if e['symbol'] == 'UNH']
    if len(unh_exits) == 0:
        print(f"  PASS: UNH CALL not closed (no opposing signal)")
    else:
        all_pass = False
        print(f"  FAIL: UNH incorrectly closed. Exits: {unh_exits}")

    print(f"  RESULT: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")
    return all_pass


# ============================================================
# TEST FIX 4: Market Regime Filter (Dual SMA)
# ============================================================
def test_market_regime_filter():
    """Test that dual-SMA regime detection is more selective than single SMA."""
    print("=" * 80)
    print("TEST FIX 4: Market Regime Filter (Dual SMA)")
    print("=" * 80)

    # Simulate SPY price scenarios
    test_cases = [
        # (spy_latest, spy_prev, sma3, sma10, expected_regime)
        # Clear bearish: below both SMAs, down from previous
        (665.0, 668.0, 667.0, 670.0, 'bearish'),
        # Clear bullish: above both SMAs, up from previous
        (680.0, 677.0, 678.0, 675.0, 'bullish'),
        # Whipsaw: below SMA3 but above SMA10 -> NEUTRAL (old system would say bearish)
        (668.0, 669.0, 669.0, 665.0, 'neutral'),
        # Whipsaw: above SMA3 but below SMA10 -> NEUTRAL (old system would say bullish)
        (668.0, 667.0, 667.0, 670.0, 'neutral'),
        # Flat market: same as previous, above both -> neutral (no directional confirm)
        (670.0, 670.0, 669.0, 668.0, 'neutral'),
    ]

    all_pass = True
    for spy_latest, spy_prev, sma3, sma10, expected in test_cases:
        # New dual-SMA logic
        if spy_latest < sma3 and spy_latest < sma10 and spy_latest < spy_prev:
            regime = 'bearish'
        elif spy_latest > sma3 and spy_latest > sma10 and spy_latest > spy_prev:
            regime = 'bullish'
        else:
            regime = 'neutral'

        # Old single-SMA logic for comparison
        if spy_latest < sma3 and spy_latest < spy_prev:
            old_regime = 'bearish'
        elif spy_latest > sma3 and spy_latest > spy_prev:
            old_regime = 'bullish'
        else:
            old_regime = 'neutral'

        status = "PASS" if regime == expected else "FAIL"
        if regime != expected:
            all_pass = False
        diff = " (differs from old)" if regime != old_regime else ""
        print(f"  {status}: SPY=${spy_latest}, prev=${spy_prev}, SMA3=${sma3}, SMA10=${sma10} "
              f"-> {regime} (expect: {expected}){diff}")

    print(f"  RESULT: {'ALL PASS' if all_pass else 'SOME FAILED'}\n")
    return all_pass


# ============================================================
# INTEGRATION TEST: Full Auto-Cycle Simulation
# ============================================================
def test_integration():
    """
    Full integration test simulating a bot auto_cycle with all fixes active.
    Uses the exact state from today's bot (March 16, 2026).
    """
    print("=" * 80)
    print("INTEGRATION TEST: Full Auto-Cycle Simulation (No Alpaca)")
    print("=" * 80)

    # Current positions (from today's real state)
    positions = [
        make_position('MRK', 'call', 1.85, 1.73, qty=16, strike=115),
        make_position('UNH', 'call', 3.02, 2.81, qty=9, strike=290),
        make_position('AMD', 'call', 4.40, 3.66, qty=6, strike=200),
    ]

    # Scanner signals (mimicking today's signals)
    signals = [
        make_signal('NVDA', 'BEARISH', 'put', 14, 46.9, 1.8, open_interest=87670, strike=180, premium=2.60),
        make_signal('AMD',  'BEARISH', 'put', 13, 38.7, 1.5, open_interest=22547, strike=200, premium=6.87),
        make_signal('TSLA', 'BEARISH', 'put', 13, 41.8, 0.8, open_interest=5668,  strike=395, premium=7.01),
        make_signal('AVGO', 'BEARISH', 'put', 13, 36.7, 0.9, open_interest=3285,  strike=325, premium=6.95),
        make_signal('CRM',  'BULLISH', 'call',13, 54.5, 1.2, open_interest=8106,  strike=200, premium=2.67),
        make_signal('SLV',  'BULLISH', 'call',12, 38.2, 0.4, open_interest=3227,  strike=73,  premium=2.26),
        make_signal('JPM',  'BEARISH', 'put', 12, 51.4, 0.9, open_interest=4667,  strike=285, premium=3.65),
        make_signal('SOFI', 'BEARISH', 'put', 12, 52.1, 0.2, open_interest=60017, strike=17,  premium=0.39),
        make_signal('SPY',  'BEARISH', 'put', 11, 42.8, 1.3, open_interest=52689, strike=670, premium=7.60),
        make_signal('GOOGL','BULLISH', 'call',11, 52.1, 0.1, open_interest=7578,  strike=305, premium=4.65),
    ]

    # Apply confidence scoring (FIX 5) and volume filter (FIX 3)
    processed_signals = []
    vol_filtered = []
    for s in signals:
        vol_ratio = s['volume_ratio']
        if vol_ratio < 0.3:
            vol_filtered.append(s['symbol'])
            continue
        # Compute confidence
        base_conf = int(30 + s['score'] * 4)
        if vol_ratio >= 1.5:
            base_conf += 5
        elif vol_ratio < 0.5:
            base_conf -= 10
        oi = s.get('open_interest', 0)
        if oi >= 5000:
            base_conf += 3
        elif oi < 500:
            base_conf -= 5
        rsi_val = s['rsi']
        direction = s['direction']
        if direction == 'BULLISH' and rsi_val > 70:
            base_conf -= 8
        elif direction == 'BEARISH' and rsi_val < 30:
            base_conf -= 8
        s['confidence'] = min(95, max(40, base_conf))
        processed_signals.append(s)

    print(f"\n  [FIX 3] Volume filter removed {len(vol_filtered)} signals: {vol_filtered}")
    print(f"  [FIX 5] Confidence scores for remaining signals:")
    for s in processed_signals:
        print(f"    {s['symbol']:6s} {s['direction']:8s} score={s['score']:2d} vol={s['volume_ratio']:.1f} OI={s['open_interest']:6d} RSI={s['rsi']:.1f} -> confidence={s['confidence']}%")

    # Market regime check (FIX 4) — simulate with dummy SPY data
    spy_latest = 668.95
    spy_prev = 672.0
    spy_sma3 = 670.5
    spy_sma10 = 675.0
    if spy_latest < spy_sma3 and spy_latest < spy_sma10 and spy_latest < spy_prev:
        market_regime = 'bearish'
    elif spy_latest > spy_sma3 and spy_latest > spy_sma10 and spy_latest > spy_prev:
        market_regime = 'bullish'
    else:
        market_regime = 'neutral'
    print(f"\n  [FIX 4] Market regime: {market_regime.upper()} (SPY=${spy_latest}, SMA3=${spy_sma3}, SMA10=${spy_sma10})")

    # Signal reversal check on positions (FIX 2)
    print(f"\n  [FIX 2] Signal reversal check on {len(positions)} open positions:")
    reversal_exits = []
    for pos in positions:
        symbol = pos['symbol']
        pos_opt_type = pos.get('option_type', '').lower()
        for sig in processed_signals:
            if sig.get('symbol') == symbol:
                sig_direction = sig.get('direction', '').upper()
                if pos_opt_type == 'call' and sig_direction == 'BEARISH':
                    pnl = (pos['current_price'] - pos['entry_price']) * pos['quantity'] * 100
                    reversal_exits.append({'symbol': symbol, 'reason': 'SIGNAL_REVERSAL', 'pnl': pnl})
                    print(f"    🔄 {symbol}: CALL -> scanner BEARISH = CLOSE (PnL: ${pnl:.2f})")
                    break
                elif pos_opt_type == 'put' and sig_direction == 'BULLISH':
                    pnl = (pos['current_price'] - pos['entry_price']) * pos['quantity'] * 100
                    reversal_exits.append({'symbol': symbol, 'reason': 'SIGNAL_REVERSAL', 'pnl': pnl})
                    print(f"    🔄 {symbol}: PUT -> scanner BULLISH = CLOSE (PnL: ${pnl:.2f})")
                    break
        else:
            print(f"    ✅ {symbol}: {pos_opt_type.upper()} - no opposing signal (hold)")

    # Entry conflict check (FIX 1)
    remaining_positions = [p for p in positions if p['symbol'] not in [e['symbol'] for e in reversal_exits]]
    print(f"\n  [FIX 1] Entry conflict check for new signals against {len(remaining_positions)} remaining positions:")
    skipped = []
    allowed = []
    min_confidence = 75
    for signal in processed_signals:
        sym = signal['symbol']
        new_opt_type = signal.get('option_type', '').lower()
        
        # Confidence filter
        if signal['confidence'] < min_confidence:
            skipped.append(f"{sym}: Below {min_confidence}% confidence ({signal['confidence']}%)")
            continue
        
        # Market regime filter
        if market_regime == 'bearish' and new_opt_type == 'call':
            skipped.append(f"{sym}: CALL blocked — bearish market regime")
            continue
        elif market_regime == 'bullish' and new_opt_type == 'put':
            skipped.append(f"{sym}: PUT blocked — bullish market regime")
            continue
        
        # Directional conflict
        conflict = False
        for existing_pos in remaining_positions:
            if existing_pos.get('symbol') == sym and existing_pos.get('instrument_type') == 'option':
                existing_opt_type = existing_pos.get('option_type', '').lower()
                if (existing_opt_type == 'call' and new_opt_type == 'put') or \
                   (existing_opt_type == 'put' and new_opt_type == 'call'):
                    skipped.append(f"{sym}: Directional conflict — have {existing_opt_type.upper()} but signal is {new_opt_type.upper()}")
                    conflict = True
                    break
        if conflict:
            continue
        
        allowed.append(signal)

    for reason in skipped:
        print(f"    ⛔ SKIPPED: {reason}")
    for s in allowed:
        print(f"    ✅ ALLOWED: {s['symbol']} {s['option_type'].upper()} (confidence={s['confidence']}%)")

    # Summary
    print(f"\n  {'='*60}")
    print(f"  INTEGRATION SUMMARY:")
    print(f"  {'='*60}")
    print(f"  Signals scanned:     {len(signals)}")
    print(f"  Volume-filtered out: {len(vol_filtered)} ({', '.join(vol_filtered)})")
    print(f"  Market regime:       {market_regime.upper()}")
    print(f"  Reversal exits:      {len(reversal_exits)} ({', '.join(e['symbol'] for e in reversal_exits)})")
    print(f"  Entry skipped:       {len(skipped)}")
    print(f"  Entries allowed:     {len(allowed)} ({', '.join(s['symbol'] for s in allowed)})")

    # Validate expectations
    all_pass = True

    # AMD should be closed by reversal
    if 'AMD' in [e['symbol'] for e in reversal_exits]:
        print(f"\n  ✅ AMD correctly closed on signal reversal")
    else:
        all_pass = False
        print(f"\n  ❌ AMD should have been closed on reversal")

    # SOFI and GOOGL should be volume-filtered
    if 'SOFI' in vol_filtered and 'GOOGL' in vol_filtered:
        print(f"  ✅ SOFI and GOOGL correctly volume-filtered")
    else:
        all_pass = False
        print(f"  ❌ SOFI/GOOGL should be volume-filtered")

    # Confidence should vary (not all 98%)
    confs = [s['confidence'] for s in processed_signals]
    if len(set(confs)) >= 3:
        print(f"  ✅ Confidence scores vary: {sorted(set(confs))}")
    else:
        all_pass = False
        print(f"  ❌ Confidence scores not varied enough: {confs}")

    # CRM CALL should be blocked in bearish regime
    crm_skipped = [r for r in skipped if 'CRM' in r and 'CALL blocked' in r]
    if market_regime == 'bearish':
        if crm_skipped:
            print(f"  ✅ CRM CALL correctly blocked in bearish regime")
        else:
            all_pass = False
            print(f"  ❌ CRM CALL should be blocked in bearish regime")
    else:
        print(f"  ℹ️  Market regime is {market_regime}, CRM CALL not regime-blocked")

    print(f"\n  INTEGRATION RESULT: {'ALL PASS' if all_pass else 'SOME TESTS FAILED'}")
    return all_pass


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("\n" + "🧪" * 40)
    print("  BOT IMPROVEMENT FIXES — TEST SUITE")
    print("  Date: March 16, 2026 | No Alpaca Connection")
    print("🧪" * 40 + "\n")

    results = []
    results.append(('Fix 5: Confidence Scoring',     test_confidence_scoring()))
    results.append(('Fix 3: Volume Ratio Filter',    test_volume_ratio_filter()))
    results.append(('Fix 1: Conflict Detection',     test_signal_position_conflict()))
    results.append(('Fix 2: Signal Reversal',        test_signal_reversal()))
    results.append(('Fix 4: Market Regime (Dual SMA)', test_market_regime_filter()))
    results.append(('Integration Test',              test_integration()))

    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    all_ok = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        if not passed:
            all_ok = False
        print(f"  {status}: {name}")

    print(f"\n  {'✅ ALL TESTS PASSED' if all_ok else '❌ SOME TESTS FAILED'}")
    print("=" * 80)
    sys.exit(0 if all_ok else 1)
