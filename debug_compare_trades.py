#!/usr/bin/env python3
"""Compare Alpaca vs AI Bot trades side-by-side for today."""
import json, urllib.request
from datetime import datetime
from collections import defaultdict

# Fetch data
alpaca_orders = json.load(urllib.request.urlopen('http://localhost:5000/api/alpaca/orders?status=closed&limit=500'))['data']
bot_status = json.load(urllib.request.urlopen('http://localhost:5000/api/bot/status'))

today = datetime.now().strftime('2026-03-12')  # Use the known date

print("=" * 120)
print("ALPACA ORDERS TODAY (filled)")
print("=" * 120)

# Filter today's filled Alpaca orders
alpaca_today = [o for o in alpaca_orders if o['status'] == 'filled' and (o.get('filled_at') or '').startswith(today)]
alpaca_today.sort(key=lambda o: o.get('filled_at') or '')

for o in alpaca_today:
    t = o['filled_at'][:19] if o.get('filled_at') else '?'
    print(f"  {t}  {o['side'].upper():4s}  {o['symbol']:30s}  qty={o['filled_qty']}  @ ${o['filled_avg_price']:.2f}")

# FIFO P&L for Alpaca
symbol_lots = {}
alpaca_pnl_list = []
for o in sorted(alpaca_today, key=lambda x: x.get('filled_at') or ''):
    sym = o['symbol']
    if sym not in symbol_lots:
        symbol_lots[sym] = []
    lots = symbol_lots[sym]
    remaining = o['filled_qty'] or o['qty']
    price = o['filled_avg_price']
    side = o['side']
    pnl = 0
    is_closing = False
    mult = 100 if len(sym) > 10 else 1
    while remaining > 0 and lots and lots[0]['side'] != side:
        is_closing = True
        lot = lots[0]
        match_qty = min(remaining, lot['qty'])
        if lot['side'] == 'buy':
            pnl += (price - lot['price']) * match_qty * mult
        else:
            pnl += (lot['price'] - price) * match_qty * mult
        lot['qty'] -= match_qty
        remaining -= match_qty
        if lot['qty'] <= 0:
            lots.pop(0)
    if remaining > 0:
        lots.append({'qty': remaining, 'price': price, 'side': side})
    if is_closing:
        alpaca_pnl_list.append({
            'symbol': sym, 'side': side, 'qty': o['filled_qty'],
            'price': price, 'pnl': round(pnl, 2), 'time': o.get('filled_at', '')[:19]
        })

print(f"\nAlpaca closing trades today: {len(alpaca_pnl_list)}")
print("-" * 100)
alpaca_total = 0
alpaca_by_symbol = defaultdict(float)
for t in alpaca_pnl_list:
    color = '+' if t['pnl'] >= 0 else ''
    print(f"  {t['time']}  CLOSE {t['symbol']:30s}  qty={t['qty']}  @ ${t['price']:.2f}  P&L: {color}${t['pnl']:.2f}")
    alpaca_total += t['pnl']
    alpaca_by_symbol[t['symbol']] += t['pnl']
print(f"\n  ALPACA TOTAL REALIZED TODAY: ${alpaca_total:.2f}")

print("\n" + "=" * 120)
print("AI BOT TRADES TODAY")
print("=" * 120)

bot_trades = bot_status.get('trades', [])
bot_today = [t for t in bot_trades if t.get('timestamp', '').startswith(today)]
bot_today.sort(key=lambda t: t.get('timestamp', ''))

bot_by_symbol = defaultdict(float)
bot_sells = []
for t in bot_today:
    action = t.get('action', '?')
    sym = t.get('symbol', '?')
    qty = t.get('quantity', '?')
    price = t.get('price', 0)
    pnl = t.get('pnl')
    pnl_str = f"P&L: ${pnl:.2f}" if pnl is not None else ""
    print(f"  {t.get('timestamp', '?')[:19]}  {action:4s}  {sym:30s}  qty={qty}  @ ${price:.2f}  {pnl_str}")
    if action == 'SELL' and pnl is not None:
        bot_sells.append(t)
        bot_by_symbol[sym] += pnl

bot_total = sum(t['pnl'] for t in bot_sells)
print(f"\nAI Bot closing trades today: {len(bot_sells)}")
print(f"  AI BOT TOTAL REALIZED TODAY: ${bot_total:.2f}")

print("\n" + "=" * 120)
print("SYMBOL-BY-SYMBOL COMPARISON")
print("=" * 120)
all_symbols = sorted(set(list(alpaca_by_symbol.keys()) + list(bot_by_symbol.keys())))

# Map OCC option symbols to base symbols for comparison
def base_symbol(sym):
    """Extract base ticker from OCC option symbol like AMD260320P00200000 -> AMD"""
    if len(sym) > 10:
        # OCC format: SYMBOL + YYMMDD + C/P + strike
        for i, c in enumerate(sym):
            if c.isdigit():
                return sym[:i]
    return sym

alpaca_by_base = defaultdict(float)
for sym, pnl in alpaca_by_symbol.items():
    alpaca_by_base[base_symbol(sym)] += pnl

bot_by_base = defaultdict(float)
for sym, pnl in bot_by_symbol.items():
    bot_by_base[base_symbol(sym)] += pnl

all_bases = sorted(set(list(alpaca_by_base.keys()) + list(bot_by_base.keys())))
print(f"\n{'Symbol':<12} {'Alpaca P&L':>15} {'AI Bot P&L':>15} {'Difference':>15} {'Match?':>10}")
print("-" * 70)
for sym in all_bases:
    a_pnl = alpaca_by_base.get(sym, 0)
    b_pnl = bot_by_base.get(sym, 0)
    diff = a_pnl - b_pnl
    match = "✅" if abs(diff) < 1 else "❌"
    print(f"  {sym:<10} {a_pnl:>+13.2f}   {b_pnl:>+13.2f}   {diff:>+13.2f}   {match:>6}")

print("-" * 70)
print(f"  {'TOTAL':<10} {alpaca_total:>+13.2f}   {bot_total:>+13.2f}   {alpaca_total - bot_total:>+13.2f}")

print("\n" + "=" * 120)
print("DETAILED TRADE PAIRING")  
print("=" * 120)

# Show alpaca trades per base symbol
for sym in all_bases:
    print(f"\n--- {sym} ---")
    # Alpaca trades for this base
    alp_trades = [o for o in alpaca_today if base_symbol(o['symbol']) == sym]
    bot_sym_trades = [t for t in bot_today if t.get('symbol') == sym]
    
    print(f"  Alpaca ({len(alp_trades)} orders):")
    for o in alp_trades:
        print(f"    {o['side'].upper():4s} {o['symbol']} qty={o['filled_qty']} @ ${o['filled_avg_price']:.2f}")
    
    print(f"  AI Bot ({len(bot_sym_trades)} trades):")
    for t in bot_sym_trades:
        pnl_str = f" P&L=${t['pnl']:.2f}" if t.get('pnl') is not None else ""
        print(f"    {t['action']:4s} {t['symbol']} qty={t.get('quantity')} @ ${t.get('price', 0):.2f}{pnl_str}")

# Check for key differences
print("\n" + "=" * 120)
print("KEY FINDINGS")
print("=" * 120)

# 1. Are Alpaca trades options vs bot trades stocks?
alpaca_options = [o for o in alpaca_today if len(o['symbol']) > 10]
alpaca_stocks = [o for o in alpaca_today if len(o['symbol']) <= 10]
bot_options = [t for t in bot_today if len(t.get('symbol', '')) > 10]
bot_stocks = [t for t in bot_today if len(t.get('symbol', '')) <= 10]

print(f"  Alpaca: {len(alpaca_options)} option orders, {len(alpaca_stocks)} stock orders")
print(f"  AI Bot: {len(bot_options)} option trades, {len(bot_stocks)} stock trades")

# 2. Price differences
print(f"\n  Alpaca total realized: ${alpaca_total:+.2f}")
print(f"  AI Bot total realized: ${bot_total:+.2f}")
print(f"  Gap:                   ${alpaca_total - bot_total:+.2f}")

if alpaca_options and not bot_options:
    print("\n  ⚠️  MAJOR FINDING: Alpaca trades OPTIONS but AI Bot trades STOCKS!")
    print("     Options have 100x multiplier, different pricing, and different P&L.")
if alpaca_stocks and bot_stocks:
    print("\n  Checking stock price differences:")
    for sym in all_bases:
        alp_buys = [o for o in alpaca_today if base_symbol(o['symbol']) == sym and o['side'] == 'buy']
        bot_buys = [t for t in bot_today if t.get('symbol') == sym and t.get('action') == 'BUY']
        if alp_buys and bot_buys:
            a_price = alp_buys[0]['filled_avg_price']
            b_price = bot_buys[0].get('price', 0)
            if abs(a_price - b_price) > 0.01:
                print(f"    {sym}: Alpaca entry=${a_price:.2f} vs Bot entry=${b_price:.2f} (diff=${abs(a_price-b_price):.2f})")
