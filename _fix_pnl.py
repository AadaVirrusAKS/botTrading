#!/usr/bin/env python3
"""Fix P&L for trades with incorrect exit prices from manual closes on 2026-04-07"""
import json
from datetime import datetime

# Load bot state
with open('data/bot_state_user_1.json', 'r') as f:
    state = json.load(f)

# Backup first
backup_name = f"data/bot_state_user_1.json.pre_pnl_fix_{datetime.now().strftime('%H%M%S')}"
with open(backup_name, 'w') as f:
    json.dump(state, f, indent=2)
print(f"Backup saved to {backup_name}")

trades = state['demo_account']['trades']
fixed_count = 0

for trade in trades:
    ts = trade.get('timestamp', '')
    if not ts.startswith('2026-04-07'):
        continue
    
    contract = trade.get('contract', '')
    action = trade.get('action', '')
    
    # Fix COIN $170C 14:56:51 - Alpaca fill was $11.25
    if contract == 'COIN $170C 2026-04-17' and 'T14:56:51' in ts and action == 'CLOSE':
        old_price = trade.get('price', 0)
        old_pnl = trade.get('pnl', 0)
        new_price = 11.25
        qty = trade.get('quantity', 3)
        entry = trade.get('entry_price', 10.65)
        new_pnl = (new_price - entry) * qty * 100
        new_pnl_pct = ((new_price - entry) / entry) * 100 if entry > 0 else 0
        
        trade['price'] = new_price
        trade['pnl'] = new_pnl
        trade['pnl_pct'] = new_pnl_pct
        print(f"FIXED: {contract}")
        print(f"  Price: ${old_price:.2f} -> ${new_price:.2f}")
        print(f"  P&L: ${old_pnl:.2f} -> ${new_pnl:.2f}")
        fixed_count += 1
    
    # Fix QQQ $585C 14:57:02 - Alpaca fill was $10.72
    if contract == 'QQQ $585C 2026-04-13' and 'T14:57:02' in ts and action == 'CLOSE':
        old_price = trade.get('price', 0)
        old_pnl = trade.get('pnl', 0)
        new_price = 10.72
        qty = trade.get('quantity', 2)
        entry = trade.get('entry_price', 8.88)
        new_pnl = (new_price - entry) * qty * 100
        new_pnl_pct = ((new_price - entry) / entry) * 100 if entry > 0 else 0
        
        trade['price'] = new_price
        trade['pnl'] = new_pnl
        trade['pnl_pct'] = new_pnl_pct
        print(f"FIXED: {contract}")
        print(f"  Price: ${old_price:.2f} -> ${new_price:.2f}")
        print(f"  P&L: ${old_pnl:.2f} -> ${new_pnl:.2f}")
        fixed_count += 1
    
    # Fix AMD $220C 14:57:19 - Alpaca fill was $8.80
    if contract == 'AMD $220C 2026-04-17' and 'T14:57:19' in ts and action == 'CLOSE':
        old_price = trade.get('price', 0)
        old_pnl = trade.get('pnl', 0)
        new_price = 8.80
        qty = trade.get('quantity', 4)
        entry = trade.get('entry_price', 8.75)
        new_pnl = (new_price - entry) * qty * 100
        new_pnl_pct = ((new_price - entry) / entry) * 100 if entry > 0 else 0
        
        trade['price'] = new_price
        trade['pnl'] = new_pnl
        trade['pnl_pct'] = new_pnl_pct
        print(f"FIXED: {contract}")
        print(f"  Price: ${old_price:.2f} -> ${new_price:.2f}")
        print(f"  P&L: ${old_pnl:.2f} -> ${new_pnl:.2f}")
        fixed_count += 1

print(f"\nFixed {fixed_count} trades")

# Save updated state
with open('data/bot_state_user_1.json', 'w') as f:
    json.dump(state, f, indent=2)
print("Saved updated bot_state_user_1.json")
