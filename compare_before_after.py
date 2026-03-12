#!/usr/bin/env python3
"""Compare today's trades: original (before sync) vs current (after sync)"""
import json
from datetime import datetime

today = datetime.now().strftime('%Y-%m-%d')

# Load original (before any Alpaca sync)
with open('ai_bot_state.json.bak_before_sync') as f:
    orig = json.load(f)

# Load current (after Alpaca syncs)
with open('ai_bot_state.json') as f:
    curr = json.load(f)

orig_trades = [t for t in orig['demo_account']['trades'] if t.get('timestamp','').startswith(today)]
curr_trades = [t for t in curr['demo_account']['trades'] if t.get('timestamp','').startswith(today)]

print(f"Original trades today: {len(orig_trades)}")
print(f"Current trades today: {len(curr_trades)}")
print()

# Count P&L
orig_losses = sum(1 for t in orig_trades if (t.get('pnl') or 0) < 0)
orig_wins = sum(1 for t in orig_trades if (t.get('pnl') or 0) > 0)
curr_losses = sum(1 for t in curr_trades if (t.get('pnl') or 0) < 0)
curr_wins = sum(1 for t in curr_trades if (t.get('pnl') or 0) > 0)
print(f"Original: {orig_wins} wins, {orig_losses} losses")
print(f"Current:  {curr_wins} wins, {curr_losses} losses")
print()

# Compare each trade
print(f"{'#':>3} {'Action':6} {'Sym':5} {'Orig$':>8} {'Curr$':>8} {'OrgPnL':>8} {'CurPnL':>8} {'Changed':>8}")
print("-" * 65)

max_trades = max(len(orig_trades), len(curr_trades))
for i in range(max_trades):
    ot = orig_trades[i] if i < len(orig_trades) else None
    ct = curr_trades[i] if i < len(curr_trades) else None
    
    if ot and ct:
        sym = ot.get('symbol', '?')
        act = ot.get('action', '?')
        op = ot.get('price', 0) or 0
        cp = ct.get('price', 0) or 0
        opnl = ot.get('pnl')
        cpnl = ct.get('pnl')
        opnl_s = f"${opnl:.2f}" if opnl is not None else "None"
        cpnl_s = f"${cpnl:.2f}" if cpnl is not None else "None"
        changed = "YES" if abs(op - cp) > 0.001 or (opnl != cpnl and opnl is not None and cpnl is not None) else ""
        print(f"{i+1:3d} {act:6} {sym:5} ${op:7.2f} ${cp:7.2f} {opnl_s:>8} {cpnl_s:>8} {changed:>8}")
    elif ot:
        print(f"{i+1:3d} ORIG ONLY: {ot.get('action')} {ot.get('symbol')} ${ot.get('price',0):.2f}")
    elif ct:
        print(f"{i+1:3d} CURR ONLY: {ct.get('action')} {ct.get('symbol')} ${ct.get('price',0):.2f}")

# Total P&L comparison
orig_total = sum((t.get('pnl') or 0) for t in orig_trades)
curr_total = sum((t.get('pnl') or 0) for t in curr_trades)
print(f"\nOriginal total P&L: ${orig_total:.2f}")
print(f"Current total P&L:  ${curr_total:.2f}")
print(f"Difference:         ${curr_total - orig_total:.2f}")

# Check original balance
print(f"\nOriginal balance: ${orig['demo_account']['balance']:.2f}")
print(f"Current balance:  ${curr['demo_account']['balance']:.2f}")
