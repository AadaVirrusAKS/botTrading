#!/usr/bin/env python3
"""Find Alpaca orders matching the 6 local-only exit trades and check for orphaned positions."""
import json
import sys
sys.path.insert(0, '.')

from services.alpaca_service import get_orders, get_positions

# Get all Alpaca orders from today
orders = get_orders(limit=500)
print(f"Total Alpaca orders: {len(orders)}")

# Filter to today's filled orders
today_orders = [o for o in orders if o.get('filled_at') and '2026-03-12' in o.get('filled_at', '')]
print(f"Today's filled orders: {len(today_orders)}\n")

# The 6 local-only exit trades and their symbols
local_exits = {
    '#15 ORCL SELL': 'ORCL260320P00160000',
    '#17 ADBE SELL': 'ADBE260320P00270000',
    '#25 COIN SELL': 'COIN260320P00195000',
    '#26 ORCL CLOSE': 'ORCL260320P00160000',
    '#27 CRM CLOSE': 'CRM260320P00200000',
    '#28 ADBE CLOSE': 'ADBE260320P00270000',
}

# Print all today's SELL orders
print("=== Today's SELL orders on Alpaca ===")
for o in today_orders:
    if o.get('side') == 'sell':
        sym = o.get('symbol', '')
        print(f"  {sym:30s} qty={o.get('filled_qty')} fill=${o.get('filled_avg_price')} status={o.get('status')} id={o.get('id')} filled_at={o.get('filled_at','')[:19]}")

# Check current open positions
print("\n=== Current Alpaca Open Positions ===")
positions = get_positions()
for p in positions:
    print(f"  {p['symbol']:30s} qty={p['qty']} avg_entry=${p['avg_entry_price']} unrealized_pl=${p['unrealized_pl']}")

# Now let's look at ALL orders for the specific option symbols
print("\n=== All orders for local-exit symbols ===")
target_syms = set(local_exits.values())
for o in orders:
    sym = o.get('symbol', '')
    if sym in target_syms:
        print(f"  {o.get('side'):4s} {sym:30s} qty={o.get('filled_qty')} fill=${o.get('filled_avg_price')} status={o.get('status')} filled_at={o.get('filled_at','')[:19] if o.get('filled_at') else 'N/A'} id={o.get('id')}")
