#!/usr/bin/env python3
"""Recalculate bot demo_account balance from trade history after fixing AMD trade P&L."""
import json

with open('ai_bot_state.json', 'r') as f:
    state = json.load(f)

acct = state['demo_account']
initial = acct.get('initial_balance', 10000.0)
trades = acct.get('trades', [])
positions = acct.get('positions', [])

# Sum realized P&L
realized_pnl = 0.0
for t in trades:
    pnl = t.get('pnl')
    if pnl is not None:
        realized_pnl += pnl
print(f"Realized P&L from trades: ${realized_pnl:.2f}")

# Net position cost
net_cost = 0.0
for p in positions:
    is_opt = p.get('instrument_type') == 'option'
    mult = 100 if is_opt else 1
    qty = p.get('quantity', 0)
    entry = p.get('entry_price', 0)
    cost = entry * qty * mult
    side = p.get('side', 'LONG')
    if side == 'SHORT':
        net_cost -= cost
    else:
        net_cost += cost
    print(f"  {p['symbol']} {p.get('contract','')} {side} qty={qty} entry={entry} mult={mult} cost=${cost:.2f}")

print(f"Net position cost: ${net_cost:.2f}")

correct = initial + realized_pnl - net_cost
print(f"\nOld balance: ${acct['balance']:.2f}")
print(f"Correct balance: ${correct:.2f}")
print(f"Difference: ${correct - acct['balance']:.2f}")

acct['balance'] = round(correct, 2)
with open('ai_bot_state.json', 'w') as f:
    json.dump(state, f, indent=2, default=str)
print(f"\nBalance updated to ${acct['balance']:.2f}")
