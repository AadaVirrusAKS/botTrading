#!/usr/bin/env python3
"""Check today's trades and diagnose PnL issues."""
import json
from datetime import datetime

with open('ai_bot_state.json', 'r') as f:
    state = json.load(f)

today_str = datetime.now().strftime('%Y-%m-%d')
trades = state.get('demo_account', {}).get('trades', [])
today_trades = [t for t in trades if t.get('timestamp', '').startswith(today_str)]

print(f"=== ALL TODAY TRADES ({len(today_trades)}) ===")
for i, t in enumerate(today_trades):
    pnl = t.get('pnl')
    pnl_str = f"${pnl:.2f}" if pnl is not None else "N/A"
    pnl_pct = t.get('pnl_pct')
    pnl_pct_str = f"{pnl_pct:.1f}%" if pnl_pct is not None else ""
    action = t.get('action', '')
    reason = t.get('reason', '')
    symbol = t.get('symbol', '')
    contract = t.get('contract', '')
    entry = t.get('entry_price', t.get('price', 0))
    exit_p = t.get('exit_price', t.get('price', 0))
    qty = t.get('quantity', 0)
    auto_exit = t.get('auto_exit', False)
    ts = t.get('timestamp', '')
    inst = t.get('instrument_type', '')
    side = t.get('side', '')
    
    if auto_exit or action in ('SELL', 'CLOSE'):
        marker = "EXIT"
    else:
        marker = "ENTRY"
    
    print(f"{i+1}. [{marker}] {symbol} {contract} | {action} | entry=${entry:.2f} exit=${exit_p:.2f} qty={qty} | PnL={pnl_str} ({pnl_pct_str}) | reason: {reason} | {ts}")
    if pnl is not None and inst == 'option':
        calc_pnl = (exit_p - entry) * qty * 100
        print(f"   Calculated PnL: ${calc_pnl:.2f} (recorded: ${pnl:.2f}) | inst={inst} side={side}")

positions = state.get('demo_account', {}).get('positions', [])
print(f"\n=== OPEN POSITIONS ({len(positions)}) ===")
for p in positions:
    sym = p.get('symbol', '')
    con = p.get('contract', '')
    ep = p.get('entry_price', 0)
    cp = p.get('current_price', 0)
    sl = p.get('stop_loss', 0)
    tp = p.get('target', 0)
    q = p.get('quantity', 0)
    print(f"  {sym} {con} | entry=${ep:.2f} | cur=${cp:.2f} | qty={q} | SL=${sl:.2f} TP=${tp:.2f}")
