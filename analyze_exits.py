#!/usr/bin/env python3
"""Analyze why all closed trades are red."""
import json
from datetime import datetime

with open('ai_bot_state.json', 'r') as f:
    s = json.load(f)

settings = s.get('settings', {})
print("=== RELEVANT SETTINGS ===")
print(f"stop_loss: {settings.get('stop_loss')}")
print(f"take_profit: {settings.get('take_profit')}")
print(f"trailing_stop: {settings.get('trailing_stop')}")
print(f"max_option_loss_pct: {settings.get('max_option_loss_pct', 'NOT SET (default 40)')}")
print(f"max_loss_per_trade: {settings.get('max_loss_per_trade', 'NOT SET (default 500)')}")
print(f"position_size: {settings.get('position_size')}")
print(f"instrument_type: {settings.get('instrument_type')}")

today_str = datetime.now().strftime('%Y-%m-%d')
trades = s.get('demo_account', {}).get('trades', [])
closed = [t for t in trades if t.get('timestamp','').startswith(today_str) and t.get('pnl') is not None]

print(f"\n=== CLOSED TRADES TODAY ({len(closed)}) ===")
for t in closed:
    sym = t.get('symbol')
    contract = t.get('contract', '')
    ep = t.get('entry_price', 0)
    xp = t.get('exit_price', 0)
    qty = t.get('quantity', 0)
    pnl = t.get('pnl', 0)
    reason = t.get('reason', '')
    loss_pct = (ep - xp) / ep * 100 if ep > 0 else 0
    dollar_loss = (ep - xp) * qty * 100
    
    print(f"\n{sym} {contract}")
    print(f"  entry=${ep:.2f}  exit=${xp:.2f}  qty={qty}  dollar_loss=${abs(dollar_loss):.2f}")
    print(f"  premium_drop={loss_pct:.1f}%  recorded_pnl=${pnl:.2f}  reason={reason}")
    print(f"  Scanner SL (50% premium) = ${ep * 0.50:.2f}")
    print(f"  max_option_loss_pct (40%) triggers when premium drops to ${ep * 0.60:.2f}")
    print(f"  max_loss_per_trade ($500) triggers after ${500 / (qty * 100):.4f}/share drop = ${ep - 500/(qty*100):.2f}")
    
    if reason == 'MAX_LOSS_GUARD':
        if abs(dollar_loss) >= 500:
            print(f"  >>> TRIGGERED BY: max_loss_per_trade ($500 cap)")
        elif loss_pct >= 40:
            print(f"  >>> TRIGGERED BY: max_option_loss_pct (40%)")
        else:
            print(f"  >>> CHECK: loss%={loss_pct:.1f}, dollar={abs(dollar_loss):.2f}")
    elif reason == 'STOP_LOSS':
        print(f"  >>> TRIGGERED BY: stop_loss hit (SL=${ep * 0.50:.2f} but exited at ${xp:.2f})")
        if xp > ep * 0.50:
            print(f"  >>> PREMATURE: Exited ABOVE the 50% stop! Price {xp:.2f} > SL {ep*0.50:.2f}")

# Check settings-level stop_loss
sl_setting = settings.get('stop_loss', 2)
print(f"\n=== STOP LOSS SETTING ANALYSIS ===")
print(f"settings.stop_loss = {sl_setting}")
print(f"If interpreted as %: stop triggers at {100-sl_setting}% of entry = {sl_setting}% drop")
print(f"For a $10 premium, that means SL at ${10 * (1 - sl_setting/100):.2f}")
print(f"For a $10 premium, scanner SL (50% premium) = $5.00")
