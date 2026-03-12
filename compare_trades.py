#!/usr/bin/env python3
"""Compare today's bot trades with Alpaca fill prices to find mismatches."""
import json
import sys
sys.path.insert(0, '.')

from services.alpaca_service import get_order_by_id

with open('ai_bot_state.json') as f:
    state = json.load(f)

trades = state['demo_account']['trades']
today_trades = [t for t in trades if '2026-03-12' in t.get('timestamp', '')]
print(f"Total today's trades: {len(today_trades)}\n")

mismatches = []
no_alpaca = []

for i, t in enumerate(today_trades):
    alp_id = t.get('alpaca_order_id')
    if not alp_id or alp_id == 'None':
        no_alpaca.append((i, t))
        continue
    
    try:
        order = get_order_by_id(alp_id)
        fill_price = order.get('filled_avg_price')
        if fill_price is None:
            print(f"#{i:02d} {t['action']:6s} {t.get('contract',''):30s} status={order.get('status')} NO FILL")
            continue
        fill_price = float(fill_price)
        bot_price = float(t.get('price', 0))
        diff = abs(fill_price - bot_price)
        
        if diff > 0.001:
            mismatches.append((i, t, fill_price, order))
            print(f"#{i:02d} MISMATCH {t['action']:6s} {t.get('contract',''):30s} bot=${bot_price:.2f} alpaca=${fill_price:.2f} diff=${diff:.2f}")
        else:
            print(f"#{i:02d} OK       {t['action']:6s} {t.get('contract',''):30s} bot=${bot_price:.2f} alpaca=${fill_price:.2f}")
    except Exception as e:
        print(f"#{i:02d} ERROR    {t['action']:6s} {t.get('contract',''):30s} {e}")

print(f"\n--- SUMMARY ---")
print(f"Total today: {len(today_trades)}")
print(f"Mismatches: {len(mismatches)}")
print(f"No Alpaca ID: {len(no_alpaca)}")
for i, t in no_alpaca:
    print(f"  #{i:02d} {t['action']:6s} {t.get('contract',''):30s} price=${t.get('price',0):.2f} reason={t.get('reason','')}")
