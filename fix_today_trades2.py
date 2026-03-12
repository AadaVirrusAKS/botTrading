#!/usr/bin/env python3
"""
Fix today's trades:
1. Link alpaca_order_ids to trades #15 and #17 that have matching Alpaca fills
2. Try to close 4 orphaned Alpaca positions (COIN, ORCL, CRM, ADBE)
3. If closes succeed, update bot trades with actual Alpaca fill prices
"""
import json
import copy
import sys
import time
sys.path.insert(0, '.')

from services.alpaca_service import get_order_by_id, close_position, get_positions

STATE_FILE = 'ai_bot_state.json'

# Load state
with open(STATE_FILE) as f:
    state = json.load(f)

# Backup
with open(STATE_FILE + '.bak_before_sync2', 'w') as f:
    json.dump(state, f, indent=2)
print("Backup saved to ai_bot_state.json.bak_before_sync2")

trades = state['demo_account']['trades']
today_trades = [(i, t) for i, t in enumerate(trades) if '2026-03-12' in t.get('timestamp', '')]

changes = 0

# -------------------------------------------------------
# STEP 1: Link alpaca_order_ids for #15 (ORCL) and #17 (ADBE)
# -------------------------------------------------------
# These are today_trades indices 15 and 17 within today's list
# Map: (symbol OCC, qty, approx UTC time) -> alpaca order ID
alpaca_sell_matches = {
    # ORCL SELL qty=6 at ~16:46 UTC = 11:46 ET
    ('ORCL', 6, 'TRAILING_STOP'): '500f0b4d-412a-4a30-b66a-586f2abca5d0',
    # ADBE SELL qty=3 at ~16:53 UTC = 11:53 ET  
    ('ADBE', 3, 'TRAILING_STOP'): '27acde8b-b3d1-4b7d-9a7c-d1c5d8439d75',
}

for local_idx, (global_idx, t) in enumerate(today_trades):
    if t.get('alpaca_order_id') or t.get('alpaca_order_id') == 'None':
        continue
    key = (t.get('symbol', ''), t.get('quantity', 0), t.get('reason', ''))
    if key in alpaca_sell_matches:
        order_id = alpaca_sell_matches[key]
        # Verify the order
        order = get_order_by_id(order_id)
        fill = order.get('filled_avg_price')
        if fill:
            fill = float(fill)
            old_price = t.get('price', 0)
            trades[global_idx]['alpaca_order_id'] = order_id
            if abs(fill - old_price) > 0.001:
                # Update price and recalc P&L
                trades[global_idx]['price'] = fill
                entry = t.get('entry_price', 0)
                qty = t.get('quantity', 1)
                mult = 100 if t.get('instrument_type') == 'option' else 1
                side = t.get('side', 'LONG')
                if side == 'LONG':
                    pnl = (fill - entry) * qty * mult
                else:
                    pnl = (entry - fill) * qty * mult
                trades[global_idx]['pnl'] = pnl
                trades[global_idx]['pnl_pct'] = ((fill - entry) / entry * 100) if entry > 0 else 0
                print(f"FIXED #{local_idx}: {t['action']} {t['symbol']} price ${old_price:.2f} -> ${fill:.2f}, P&L: ${pnl:.2f}")
            else:
                print(f"LINKED #{local_idx}: {t['action']} {t['symbol']} alpaca_id={order_id} (price already correct: ${fill:.2f})")
            changes += 1

# -------------------------------------------------------
# STEP 2: Try to close 4 orphaned Alpaca positions
# -------------------------------------------------------
orphaned = [
    # (today_list_idx, symbol, occ_symbol)
    (25, 'COIN', 'COIN260320P00195000'),
    (26, 'ORCL', 'ORCL260320P00160000'),
    (27, 'CRM', 'CRM260320P00200000'),
    (28, 'ADBE', 'ADBE260320P00270000'),
]

# Check what's actually open
open_positions = get_positions()
open_syms = {p['symbol']: p for p in open_positions}
print(f"\nOpen Alpaca positions: {list(open_syms.keys())}")

for local_idx, sym, occ in orphaned:
    global_idx = today_trades[local_idx][0]
    t = trades[global_idx]
    
    if occ not in open_syms:
        print(f"#{local_idx} {sym} ({occ}) - NOT open on Alpaca, skipping")
        continue
    
    pos = open_syms[occ]
    print(f"\n#{local_idx} {sym} ({occ}) - Open on Alpaca, qty={pos['qty']}, trying to close...")
    
    try:
        result = close_position(occ)
        print(f"  Close result: {result}")
        
        # If we got an order back, poll for fill
        order_id = result.get('id')
        if order_id:
            fill = None
            for attempt in range(20):
                order = get_order_by_id(order_id)
                fill = order.get('filled_avg_price')
                if fill:
                    fill = float(fill)
                    break
                status = order.get('status', '')
                if status in ('canceled', 'expired', 'rejected'):
                    print(f"  Order {status}, no fill")
                    break
                time.sleep(1)
            
            if fill:
                old_price = t.get('price', 0)
                trades[global_idx]['alpaca_order_id'] = order_id
                trades[global_idx]['price'] = fill
                entry = t.get('entry_price', 0)
                qty = t.get('quantity', 1)
                mult = 100 if t.get('instrument_type') == 'option' else 1
                side = t.get('side', 'LONG')
                if side == 'LONG':
                    pnl = (fill - entry) * qty * mult
                else:
                    pnl = (entry - fill) * qty * mult
                trades[global_idx]['pnl'] = pnl
                trades[global_idx]['pnl_pct'] = ((fill - entry) / entry * 100) if entry > 0 else 0
                print(f"  FIXED: price ${old_price:.2f} -> ${fill:.2f}, P&L: ${pnl:.2f}")
                changes += 1
            else:
                print(f"  WARNING: Close submitted but no fill price received")
        else:
            # close_position with no qty returns {'symbol': ..., 'status': 'closed'}, no order id
            print(f"  Closed via close_position (no order ID returned)")
            # Can't get fill price from bulk close - leave bot price as-is
    except Exception as e:
        print(f"  ERROR closing: {e}")
        if 'market hours' in str(e).lower():
            print(f"  -> Market closed, will need to close tomorrow")

# -------------------------------------------------------
# STEP 3: Recalculate balance
# -------------------------------------------------------
if changes > 0:
    account = state['demo_account']
    initial = 50000.0
    total_pnl = sum((t.get('pnl') or 0) for t in account['trades'] if t.get('action') in ('SELL', 'BUY_TO_COVER', 'CLOSE'))
    new_balance = initial + total_pnl
    old_balance = account.get('balance', 0)
    account['balance'] = new_balance
    print(f"\nBalance: ${old_balance:.2f} -> ${new_balance:.2f} (total P&L: ${total_pnl:.2f})")
    
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    print(f"\nSaved {changes} changes to {STATE_FILE}")
else:
    print("\nNo changes needed")
