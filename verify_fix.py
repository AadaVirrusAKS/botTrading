#!/usr/bin/env python3
"""Verify today's trades after fix"""
import json
from datetime import datetime

with open('ai_bot_state.json') as f:
    state = json.load(f)

acct = state['demo_account']
print(f"Balance: ${acct['balance']:.2f}")
print(f"Initial: ${acct['initial_balance']:.2f}")
print(f"P&L: ${acct['balance'] - acct['initial_balance']:.2f}")

trades = acct['trades']
print(f"Total trades: {len(trades)}")

today = datetime.now().strftime('%Y-%m-%d')
today_trades = [t for t in trades if t.get('timestamp','').startswith(today)]
print(f"\nToday trades: {len(today_trades)}")
with_id = sum(1 for t in today_trades if t.get('alpaca_order_id'))
print(f"With alpaca_order_id: {with_id}/{len(today_trades)}")
print()

for i, t in enumerate(today_trades):
    aid = 'YES' if t.get('alpaca_order_id') else 'NO '
    pnl = t.get('pnl')
    pnl_str = f"${pnl:.2f}" if pnl is not None else 'None'
    sym = t.get('symbol', '?')
    act = t.get('action', '?')
    price = t.get('price', 0) or 0
    qty = t.get('quantity', 0) or 0
    reason = t.get('reason', '')
    print(f"  #{i+1:2d} {act:6s} {sym:5s} ${price:7.2f} qty={qty} aid={aid} pnl={pnl_str}  {reason}")

# Count positions
positions = acct.get('positions', {})
print(f"\nOpen positions in bot: {len(positions)}")
for sym, pos in positions.items():
    print(f"  {sym}: qty={pos.get('quantity')} entry=${pos.get('entry_price',0):.2f}")
