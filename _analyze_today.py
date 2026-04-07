#!/usr/bin/env python3
"""Analyze today's trades"""
import json
from datetime import datetime

with open('data/bot_state_user_1.json', 'r') as f:
    state = json.load(f)

trades = state['demo_account']['trades']
today = '2026-04-07'

# Filter today's trades
today_trades = [t for t in trades if t.get('timestamp', '').startswith(today)]

print("=" * 100)
print(f"TRADE ANALYSIS - {today}")
print("=" * 100)

# Separate entries and exits
entries = [t for t in today_trades if t.get('action') == 'BUY']
exits = [t for t in today_trades if t.get('action') in ('SELL', 'CLOSE')]

print(f"\n📊 SUMMARY")
print("-" * 50)
print(f"Total Entries: {len(entries)}")
print(f"Total Exits: {len(exits)}")

# Calculate P&L
total_pnl = sum(t.get('pnl', 0) or 0 for t in exits)
winners = [t for t in exits if (t.get('pnl', 0) or 0) > 0]
losers = [t for t in exits if (t.get('pnl', 0) or 0) < 0]
breakeven = [t for t in exits if (t.get('pnl', 0) or 0) == 0]

print(f"\n💰 P&L BREAKDOWN")
print("-" * 50)
print(f"Winners: {len(winners)}")
print(f"Losers: {len(losers)}")
print(f"Breakeven: {len(breakeven)}")
win_rate = (len(winners) / len(exits) * 100) if exits else 0
print(f"Win Rate: {win_rate:.1f}%")
print(f"\n🎯 TOTAL P&L: ${total_pnl:,.2f}")

# Best and worst trades
if winners:
    best = max(winners, key=lambda t: t.get('pnl', 0))
    print(f"\n✅ BEST TRADE: {best.get('contract', best.get('symbol'))} +${best.get('pnl', 0):,.2f}")
if losers:
    worst = min(losers, key=lambda t: t.get('pnl', 0))
    print(f"🔴 WORST TRADE: {worst.get('contract', worst.get('symbol'))} ${worst.get('pnl', 0):,.2f}")

# Average win/loss
if winners:
    avg_win = sum(t.get('pnl', 0) for t in winners) / len(winners)
    print(f"📈 Average Win: ${avg_win:,.2f}")
if losers:
    avg_loss = sum(t.get('pnl', 0) for t in losers) / len(losers)
    print(f"📉 Average Loss: ${avg_loss:,.2f}")

# P&L by exit reason
print(f"\n📈 P&L BY EXIT REASON")
print("-" * 50)
reasons = {}
for t in exits:
    reason = t.get('reason', 'MANUAL')
    if reason not in reasons:
        reasons[reason] = {'count': 0, 'pnl': 0}
    reasons[reason]['count'] += 1
    reasons[reason]['pnl'] += t.get('pnl', 0) or 0

for reason, data in sorted(reasons.items(), key=lambda x: -x[1]['pnl']):
    print(f"  {reason}: {data['count']} trades, ${data['pnl']:,.2f}")

# P&L by direction (CALL vs PUT)
print(f"\n📊 P&L BY DIRECTION")
print("-" * 50)
calls = [t for t in exits if t.get('option_type') == 'call']
puts = [t for t in exits if t.get('option_type') == 'put']
call_pnl = sum(t.get('pnl', 0) or 0 for t in calls)
put_pnl = sum(t.get('pnl', 0) or 0 for t in puts)
print(f"  CALLs: {len(calls)} trades, ${call_pnl:,.2f}")
print(f"  PUTs: {len(puts)} trades, ${put_pnl:,.2f}")

# Detailed trade list
print(f"\n📋 DETAILED EXITS (Chronological)")
print("-" * 110)
print(f"{'Time':<10} {'Contract':<32} {'Entry':>8} {'Exit':>8} {'Qty':>4} {'P&L':>12} {'Reason':<20}")
print("-" * 110)

for t in sorted(exits, key=lambda x: x.get('timestamp', '')):
    ts = t.get('timestamp', '')[:19].split('T')[1][:8] if 'T' in t.get('timestamp', '') else ''
    contract = t.get('contract', t.get('symbol', ''))[:30]
    entry = t.get('entry_price', 0) or 0
    exit_p = t.get('price', 0) or 0
    qty = t.get('quantity', 0)
    pnl = t.get('pnl', 0) or 0
    reason = t.get('reason', 'MANUAL')[:18]
    pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"${pnl:,.2f}"
    print(f"{ts:<10} {contract:<32} ${entry:>6.2f} ${exit_p:>6.2f} {qty:>4} {pnl_str:>12} {reason:<20}")

print("-" * 110)
print(f"{'TOTAL':>69} ${total_pnl:>10,.2f}")

# Issues analysis
print(f"\n⚠️ ISSUES IDENTIFIED")
print("-" * 50)

# Trades closed at breakeven or small loss after being in profit
quick_stops = [t for t in exits if t.get('reason') == 'STOP_LOSS' and abs(t.get('pnl', 0) or 0) < 100]
if quick_stops:
    print(f"  🔸 {len(quick_stops)} trades hit stop loss with minimal P&L (tight stops?)")

# Signal reversals
reversals = [t for t in exits if t.get('reason') == 'SIGNAL_REVERSAL']
if reversals:
    total_rev_loss = sum(t.get('pnl', 0) or 0 for t in reversals)
    print(f"  🔸 {len(reversals)} signal reversals: ${total_rev_loss:,.2f}")

# Entries
print(f"\n📥 ENTRIES TODAY ({len(entries)} total)")
print("-" * 90)
for t in sorted(entries, key=lambda x: x.get('timestamp', '')):
    ts = t.get('timestamp', '')[:19].split('T')[1][:8] if 'T' in t.get('timestamp', '') else ''
    contract = t.get('contract', t.get('symbol', ''))[:30]
    price = t.get('price', 0)
    qty = t.get('quantity', 0)
    cost = qty * price * 100 if t.get('instrument_type') == 'option' else qty * price
    conf = t.get('confidence', '')
    opt_type = t.get('option_type', '').upper()
    print(f"{ts} {contract:<32} {opt_type:>4} {qty:>2}x @ ${price:.2f} = ${cost:,.0f} (conf: {conf})")
