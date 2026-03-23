#!/usr/bin/env python3
"""Analyze all bot trades - losses, today's trades, patterns."""
import json
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
TODAY = '2026-03-23'

all_closed_trades = []
all_open_positions = []

# 1. Parse active_positions.json
with open(os.path.join(DATA_DIR, 'active_positions.json')) as f:
    positions = json.load(f)
for key, pos in positions.items():
    if pos.get('status') == 'closed':
        all_closed_trades.append({
            'source': 'active_positions',
            'symbol': pos.get('ticker', ''),
            'contract': '',
            'type': pos.get('type', ''),
            'direction': pos.get('direction', 'LONG'),
            'entry': pos.get('entry', 0),
            'exit': pos.get('exit', 0),
            'pnl': pos.get('pnl', 0),
            'pnl_pct': pos.get('pnl_pct', 0),
            'date_added': pos.get('date_added', ''),
            'date_closed': pos.get('date_closed', ''),
            'close_reason': pos.get('close_reason', ''),
            'quantity': pos.get('quantity', 0),
        })

# 2. Parse bot_state_user_1.json
with open(os.path.join(DATA_DIR, 'bot_state_user_1.json')) as f:
    bot1 = json.load(f)

trades = bot1.get('demo_account', {}).get('trades', [])
for t in trades:
    action = t.get('action', '')
    if action in ['SELL', 'BUY_TO_COVER', 'CLOSE'] and t.get('pnl') is not None:
        all_closed_trades.append({
            'source': 'bot_user_1',
            'symbol': t.get('symbol', ''),
            'contract': t.get('contract', ''),
            'type': t.get('instrument_type', ''),
            'option_type': t.get('option_type', ''),
            'direction': t.get('side', 'LONG'),
            'entry': t.get('entry_price', 0),
            'exit': t.get('exit_price', t.get('price', 0)),
            'pnl': t.get('pnl', 0),
            'pnl_pct': t.get('pnl_pct', 0),
            'date_closed': t.get('timestamp', ''),
            'close_reason': t.get('reason', ''),
            'quantity': t.get('quantity', 0),
        })

# Open positions from bot_state_user_1
for pos in bot1.get('demo_account', {}).get('positions', []):
    mult = 100 if pos.get('instrument_type') == 'option' else 1
    upnl = (pos.get('current_price', 0) - pos.get('entry_price', 0)) * pos.get('quantity', 0) * mult
    all_open_positions.append({
        'symbol': pos.get('symbol', ''),
        'contract': pos.get('contract', ''),
        'type': pos.get('instrument_type', ''),
        'entry_price': pos.get('entry_price', 0),
        'current_price': pos.get('current_price', 0),
        'quantity': pos.get('quantity', 0),
        'timestamp': pos.get('timestamp', ''),
        'unrealized_pnl': upnl,
        'stop_loss': pos.get('stop_loss', 0),
        'target': pos.get('target', 0),
    })

# 3. Parse ai_bot_state.json
with open(os.path.join(DATA_DIR, 'ai_bot_state.json')) as f:
    ai_bot = json.load(f)
for t in ai_bot.get('demo_account', {}).get('trades', []):
    action = t.get('action', '')
    if action in ['SELL', 'BUY_TO_COVER', 'CLOSE'] and t.get('pnl') is not None:
        all_closed_trades.append({
            'source': 'ai_bot',
            'symbol': t.get('symbol', ''),
            'contract': t.get('contract', ''),
            'type': t.get('instrument_type', ''),
            'option_type': t.get('option_type', ''),
            'direction': t.get('side', 'LONG'),
            'entry': t.get('entry_price', 0),
            'exit': t.get('exit_price', t.get('price', 0)),
            'pnl': t.get('pnl', 0),
            'pnl_pct': t.get('pnl_pct', 0),
            'date_closed': t.get('timestamp', ''),
            'close_reason': t.get('reason', ''),
            'quantity': t.get('quantity', 0),
        })

# AI bot open positions
for pos in ai_bot.get('demo_account', {}).get('positions', []):
    mult = 100 if pos.get('instrument_type') == 'option' else 1
    upnl = (pos.get('current_price', 0) - pos.get('entry_price', 0)) * pos.get('quantity', 0) * mult
    all_open_positions.append({
        'symbol': pos.get('symbol', ''),
        'contract': pos.get('contract', ''),
        'type': pos.get('instrument_type', ''),
        'entry_price': pos.get('entry_price', 0),
        'current_price': pos.get('current_price', 0),
        'quantity': pos.get('quantity', 0),
        'timestamp': pos.get('timestamp', ''),
        'unrealized_pnl': upnl,
        'stop_loss': pos.get('stop_loss', 0),
        'target': pos.get('target', 0),
    })

# ============================== OUTPUT ==============================
SEP = "=" * 100

total_pnl = sum(t['pnl'] for t in all_closed_trades if t['pnl'])
wins = [t for t in all_closed_trades if t['pnl'] and t['pnl'] > 0]
losses = [t for t in all_closed_trades if t['pnl'] and t['pnl'] < 0]

print(SEP)
print("TRADING BOT FULL ANALYSIS")
print(SEP)
print(f"Total closed trades: {len(all_closed_trades)}")
print(f"Open positions: {len(all_open_positions)}")
print(f"Total P&L: ${total_pnl:,.2f}")
print(f"Wins: {len(wins)} | Losses: {len(losses)}")
if wins or losses:
    print(f"Win Rate: {len(wins)/(len(wins)+len(losses))*100:.1f}%")

# TOP 15 WORST LOSSES (ALL-TIME)
print(f"\n{SEP}")
print("TOP 15 WORST LOSSES (ALL-TIME)")
print(SEP)
sorted_losses = sorted(all_closed_trades, key=lambda x: x.get('pnl', 0))
for i, t in enumerate(sorted_losses[:15], 1):
    dt = str(t['date_closed'])[:10] if t['date_closed'] else 'N/A'
    sym = t['symbol']
    contract = t.get('contract', '')
    label = contract if contract else f"{sym} ({t['type']})"
    print(f"  {i:2}. {label:<42} PnL: ${t['pnl']:>10,.2f} ({t['pnl_pct']:>7.2f}%)  Qty: {t['quantity']:>5}  Exit: {t['close_reason']:<15}  Date: {dt}  Src: {t['source']}")

# TODAY'S TRADES
print(f"\n{SEP}")
print(f"TODAY'S TRADES ({TODAY})")
print(SEP)
today_trades = [t for t in all_closed_trades if t['date_closed'] and TODAY in str(t['date_closed'])]
today_pnl = sum(t['pnl'] for t in today_trades if t['pnl'])
today_wins = [t for t in today_trades if t['pnl'] and t['pnl'] > 0]
today_losses = [t for t in today_trades if t['pnl'] and t['pnl'] < 0]
print(f"Closed trades today: {len(today_trades)}")
print(f"Today Net P&L: ${today_pnl:,.2f}")
print(f"Today Wins: {len(today_wins)} | Losses: {len(today_losses)}")
if today_trades:
    print(f"Win Rate: {len(today_wins)/len(today_trades)*100:.1f}%")

print("\nToday's trade details (sorted by PnL):")
for i, t in enumerate(sorted(today_trades, key=lambda x: x.get('pnl', 0)), 1):
    contract = t.get('contract', '')
    label = contract if contract else f"{t['symbol']} ({t['type']})"
    icon = "LOSS" if t['pnl'] < 0 else "WIN "
    print(f"  [{icon}] {label:<42} PnL: ${t['pnl']:>10,.2f} ({t['pnl_pct']:>7.2f}%)  Qty: {t['quantity']:>5}  Exit: {t['close_reason']}")

# OPEN POSITIONS
print(f"\n{SEP}")
print("CURRENTLY OPEN POSITIONS (Unrealized P&L)")
print(SEP)
total_unrealized = 0
for p in all_open_positions:
    contract = p.get('contract', '')
    label = contract if contract else f"{p['symbol']} ({p['type']})"
    upnl = p['unrealized_pnl']
    total_unrealized += upnl
    icon = "LOSS" if upnl < 0 else "WIN "
    print(f"  [{icon}] {label:<45} Entry: ${p['entry_price']:.2f}  Curr: ${p['current_price']:.2f}  Unrealized: ${upnl:>10,.2f}  Qty: {p['quantity']}  SL: ${p['stop_loss']:.2f}  Target: ${p['target']:.2f}")
print(f"  Total Unrealized: ${total_unrealized:,.2f}")

# LOSS PATTERNS
print(f"\n{SEP}")
print("LOSS PATTERN ANALYSIS")
print(SEP)

by_reason = defaultdict(lambda: {'count': 0, 'total_pnl': 0})
for t in losses:
    r = t['close_reason']
    by_reason[r]['count'] += 1
    by_reason[r]['total_pnl'] += t['pnl']
print("\nLosses by exit reason:")
for reason, stats in sorted(by_reason.items(), key=lambda x: x[1]['total_pnl']):
    print(f"  {reason:<25}: {stats['count']:>4} trades  Total: ${stats['total_pnl']:>10,.2f}")

# By instrument type
by_type = defaultdict(lambda: {'count': 0, 'total_pnl': 0, 'wins': 0, 'losses': 0})
for t in all_closed_trades:
    tp = t.get('type', 'unknown')
    if t.get('option_type'):
        tp = f"option_{t['option_type']}"
    by_type[tp]['count'] += 1
    by_type[tp]['total_pnl'] += t.get('pnl', 0) or 0
    if t.get('pnl', 0) and t['pnl'] > 0:
        by_type[tp]['wins'] += 1
    elif t.get('pnl', 0) and t['pnl'] < 0:
        by_type[tp]['losses'] += 1

print("\nP&L by instrument type:")
for tp, stats in sorted(by_type.items(), key=lambda x: x[1]['total_pnl']):
    wr = stats['wins'] / (stats['wins'] + stats['losses']) * 100 if (stats['wins'] + stats['losses']) > 0 else 0
    print(f"  {tp:<15}: {stats['count']:>4} trades  P&L: ${stats['total_pnl']:>10,.2f}  W/L: {stats['wins']}/{stats['losses']}  WR: {wr:.0f}%")

# worst symbols
by_symbol = defaultdict(lambda: {'count': 0, 'total_pnl': 0, 'wins': 0, 'losses': 0})
for t in all_closed_trades:
    s = t['symbol']
    by_symbol[s]['count'] += 1
    by_symbol[s]['total_pnl'] += t.get('pnl', 0) or 0
    if t.get('pnl', 0) and t['pnl'] > 0:
        by_symbol[s]['wins'] += 1
    elif t.get('pnl', 0) and t['pnl'] < 0:
        by_symbol[s]['losses'] += 1

print("\nWorst performing symbols:")
for sym, stats in sorted(by_symbol.items(), key=lambda x: x[1]['total_pnl'])[:10]:
    wr = stats['wins'] / (stats['wins'] + stats['losses']) * 100 if (stats['wins'] + stats['losses']) > 0 else 0
    print(f"  {sym:<8}: {stats['count']:>4} trades  P&L: ${stats['total_pnl']:>10,.2f}  W/L: {stats['wins']}/{stats['losses']}  WR: {wr:.0f}%")

print("\nBest performing symbols:")
for sym, stats in sorted(by_symbol.items(), key=lambda x: x[1]['total_pnl'], reverse=True)[:10]:
    wr = stats['wins'] / (stats['wins'] + stats['losses']) * 100 if (stats['wins'] + stats['losses']) > 0 else 0
    print(f"  {sym:<8}: {stats['count']:>4} trades  P&L: ${stats['total_pnl']:>10,.2f}  W/L: {stats['wins']}/{stats['losses']}  WR: {wr:.0f}%")

# Daily P&L breakdown
by_date = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0, 'losses': 0})
for t in all_closed_trades:
    dt = str(t.get('date_closed', ''))[:10]
    if dt and len(dt) == 10:
        by_date[dt]['count'] += 1
        by_date[dt]['pnl'] += t.get('pnl', 0) or 0
        if t.get('pnl', 0) and t['pnl'] > 0:
            by_date[dt]['wins'] += 1
        elif t.get('pnl', 0) and t['pnl'] < 0:
            by_date[dt]['losses'] += 1

print(f"\n{SEP}")
print("DAILY P&L BREAKDOWN (Worst Days)")
print(SEP)
for dt, stats in sorted(by_date.items(), key=lambda x: x[1]['pnl'])[:10]:
    print(f"  {dt}: {stats['count']:>4} trades  P&L: ${stats['pnl']:>10,.2f}  W/L: {stats['wins']}/{stats['losses']}")

# ACCOUNT
print(f"\n{SEP}")
print("ACCOUNT STATUS")
print(SEP)
print(f"Bot User 1 Balance: ${bot1['demo_account']['balance']:,.2f} (Started: ${bot1['demo_account']['initial_balance']:,.2f})")
print(f"AI Bot Balance:     ${ai_bot['demo_account']['balance']:,.2f} (Started: ${ai_bot['demo_account']['initial_balance']:,.2f})")
ret1 = ((bot1['demo_account']['balance'] - bot1['demo_account']['initial_balance']) / bot1['demo_account']['initial_balance']) * 100
print(f"Bot User 1 Total Return: {ret1:.1f}%")
