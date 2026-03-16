#!/usr/bin/env python3
"""Detailed trade-by-trade P&L comparison: Bot vs Alpaca."""
import json
from collections import defaultdict

TODAY = '2026-03-13'

with open('ai_bot_state.json') as f:
    state = json.load(f)

trades = state.get('demo_account', {}).get('trades', [])
today_trades = [t for t in trades if t.get('timestamp', '').startswith(TODAY)]

# Fetch Alpaca orders
from services.alpaca_service import get_orders, get_account

orders = get_orders()
today_orders = [o for o in orders if o.get('created_at', '').startswith(TODAY)]

# Build Alpaca order map by ID
alpaca_by_id = {}
for o in today_orders:
    alpaca_by_id[o['id']] = o

# Also build by symbol+side for matching
alpaca_by_sym_side = defaultdict(list)
for o in today_orders:
    key = (o['symbol'], o['side'])
    alpaca_by_sym_side[key].append(o)

print("=" * 130)
print("TRADE-BY-TRADE: BOT vs ALPACA FILL PRICE COMPARISON")
print("=" * 130)
print(f"{'#':>3s} {'Time':8s} {'Action':5s} {'Symbol':30s} {'BotQty':>7s} {'BotPrice':>10s} {'AlpQty':>7s} {'AlpFill':>10s} {'PriceDiff':>10s} {'$Impact':>10s} {'Match':15s}")
print("-" * 130)

total_bot_pnl = 0
total_alp_pnl = 0
total_price_gap_impact = 0
unmatched_bot = []
round_trips_bot = []
round_trips_alp = []

# Track round trips per symbol
bot_buys = defaultdict(list)
bot_sells = defaultdict(list)
alp_buys = defaultdict(list)
alp_sells = defaultdict(list)

for i, t in enumerate(today_trades):
    symbol = t.get('symbol', '?')
    action = t.get('action', '?')
    contract = t.get('contract', '')
    qty = t.get('quantity', 0)
    price = t.get('price', 0)
    pnl = t.get('pnl', None)
    ts = t.get('timestamp', '')
    alpaca_id = t.get('alpaca_order_id', '')
    instr = t.get('instrument_type', 'stock')
    multiplier = 100 if instr == 'option' else 1
    
    time_part = ts.split('T')[1][:8] if 'T' in ts else ''
    
    # Match to Alpaca order
    alp_order = alpaca_by_id.get(alpaca_id)
    alp_qty = float(alp_order['filled_qty']) if alp_order and alp_order.get('filled_qty') else 0
    alp_fill = float(alp_order['filled_avg_price']) if alp_order and alp_order.get('filled_avg_price') else 0
    
    price_diff = price - alp_fill if alp_fill > 0 else 0
    cost_impact = price_diff * qty * multiplier
    total_price_gap_impact += abs(cost_impact)
    
    match_status = "✅ Matched" if alp_order else "❌ NO MATCH"
    if alp_order and abs(price_diff) > 0.05:
        match_status = "⚠️ Price Gap"
    
    display_sym = f"{symbol} {contract}" if contract else symbol
    
    print(f"{i+1:3d} {time_part:8s} {action:5s} {display_sym:30s} {qty:7d} ${price:9.2f} {alp_qty:7.0f} ${alp_fill:9.2f} ${price_diff:+9.4f} ${cost_impact:+9.2f} {match_status}")
    
    if action in ('BUY',):
        bot_buys[contract or symbol].append({'qty': qty, 'price': price, 'ts': ts})
        if alp_order:
            alp_buys[contract or symbol].append({'qty': alp_qty, 'price': alp_fill, 'ts': ts})
    elif action in ('SELL', 'CLOSE'):
        bot_sells[contract or symbol].append({'qty': qty, 'price': price, 'pnl': pnl, 'ts': ts})
        if alp_order:
            alp_sells[contract or symbol].append({'qty': alp_qty, 'price': alp_fill, 'ts': ts})
    
    if not alp_order:
        unmatched_bot.append(t)

print("-" * 130)

# Now compute round-trip P&L for each symbol in BOTH systems
print("\n" + "=" * 130)
print("ROUND-TRIP P&L PER SYMBOL: BOT vs ALPACA")
print("=" * 130)
print(f"{'Symbol/Contract':35s} | {'Bot Buy':>10s} {'Bot Sell':>10s} {'Bot P&L':>10s} | {'Alp Buy':>10s} {'Alp Sell':>10s} {'Alp P&L':>10s} | {'GAP':>10s} {'Notes':20s}")
print("-" * 130)

all_symbols = set(list(bot_buys.keys()) + list(bot_sells.keys()))
grand_bot_pnl = 0
grand_alp_pnl = 0

for sym in sorted(all_symbols):
    bb = bot_buys.get(sym, [])
    bs = bot_sells.get(sym, [])
    ab = alp_buys.get(sym, [])
    als = alp_sells.get(sym, [])
    
    # Weighted avg buy price (bot)
    bot_buy_total = sum(b['qty'] * b['price'] for b in bb)
    bot_buy_qty = sum(b['qty'] for b in bb)
    bot_avg_buy = bot_buy_total / bot_buy_qty if bot_buy_qty else 0
    
    # Weighted avg sell price (bot)  
    bot_sell_total = sum(s['qty'] * s['price'] for s in bs)
    bot_sell_qty = sum(s['qty'] for s in bs)
    bot_avg_sell = bot_sell_total / bot_sell_qty if bot_sell_qty else 0
    
    # Bot P&L (options *= 100)
    multiplier = 100  # all today's trades are options
    bot_pnl_calc = (bot_avg_sell - bot_avg_buy) * min(bot_buy_qty, bot_sell_qty) * multiplier if bot_sell_qty else 0
    
    # Alpaca side
    alp_buy_total = sum(b['qty'] * b['price'] for b in ab)
    alp_buy_qty = sum(b['qty'] for b in ab)
    alp_avg_buy = alp_buy_total / alp_buy_qty if alp_buy_qty else 0
    
    alp_sell_total = sum(s['qty'] * s['price'] for s in als)
    alp_sell_qty = sum(s['qty'] for s in als)
    alp_avg_sell = alp_sell_total / alp_sell_qty if alp_sell_qty else 0
    
    alp_pnl_calc = (alp_avg_sell - alp_avg_buy) * min(alp_buy_qty, alp_sell_qty) * multiplier if alp_sell_qty and alp_buy_qty else 0
    
    gap = bot_pnl_calc - alp_pnl_calc
    grand_bot_pnl += bot_pnl_calc
    grand_alp_pnl += alp_pnl_calc
    
    notes = ""
    if not ab and bb:
        notes = "❌ No Alpaca buy"
    elif not als and bs:
        notes = "❌ No Alpaca sell"
    elif abs(gap) > 50:
        notes = "⚠️ LARGE GAP"
    elif abs(gap) > 10:
        notes = "⚠️ gap"
    else:
        notes = "✅"
    
    print(f"{sym:35s} | ${bot_avg_buy:>9.2f} ${bot_avg_sell:>9.2f} ${bot_pnl_calc:>+9.2f} | ${alp_avg_buy:>9.2f} ${alp_avg_sell:>9.2f} ${alp_pnl_calc:>+9.2f} | ${gap:>+9.2f} {notes}")

print("-" * 130)
print(f"{'TOTALS':35s} | {'':10s} {'':10s} ${grand_bot_pnl:>+9.2f} | {'':10s} {'':10s} ${grand_alp_pnl:>+9.2f} | ${grand_bot_pnl - grand_alp_pnl:>+9.2f}")

print(f"\n{'='*130}")
print("SUMMARY OF GAPS")
print(f"{'='*130}")
print(f"  Bot Total Realized P&L:    ${grand_bot_pnl:>+10.2f}")
print(f"  Alpaca Total Realized P&L: ${grand_alp_pnl:>+10.2f}")
print(f"  P&L GAP (Bot - Alpaca):    ${grand_bot_pnl - grand_alp_pnl:>+10.2f}")
print()

# Unmatched trades
print(f"  TRADES WITH NO ALPACA ORDER ID ({len(unmatched_bot)}):")
for t in unmatched_bot:
    sym = t.get('symbol', '?')
    action = t.get('action', '?')
    contract = t.get('contract', '')
    pnl = t.get('pnl', None)
    ts = t.get('timestamp', '').split('T')[1][:8] if 'T' in t.get('timestamp','') else ''
    pnl_str = f"P&L=${pnl:.2f}" if pnl is not None else ""
    print(f"    {ts} {action} {sym} {contract} {pnl_str}")

# Additional Alpaca orders not in bot
bot_alpaca_ids = set()
for t in today_trades:
    aid = t.get('alpaca_order_id', '')
    if aid:
        bot_alpaca_ids.add(aid)

extra_alpaca = [o for o in today_orders if o['id'] not in bot_alpaca_ids]
print(f"\n  ALPACA ORDERS NOT IN BOT ({len(extra_alpaca)}):")
for o in extra_alpaca:
    sym = o.get('symbol', '?')
    side = o.get('side', '?')
    qty = o.get('qty', 0)
    fill = float(o.get('filled_avg_price', 0)) if o.get('filled_avg_price') else 0
    status = o.get('status', '?')
    created = o.get('created_at', '')[:19]
    print(f"    {created} {side:4s} {sym:25s} qty={qty} fill=${fill:.2f} status={status} id={o['id'][:25]}")

# Get Alpaca account P&L
account = get_account()
alp_equity = float(account.get('equity', 0))
alp_last_equity = float(account.get('last_equity', 0))
alp_daily_pnl = alp_equity - alp_last_equity

bot_balance = state.get('demo_account', {}).get('balance', 0)
bot_daily_pnl_rec = state.get('demo_account', {}).get('daily_pnl', {}).get(TODAY, 'N/A')

print(f"\n  ACCOUNT-LEVEL COMPARISON:")
print(f"    Alpaca Equity:        ${alp_equity:>12,.2f}")
print(f"    Alpaca Last Equity:   ${alp_last_equity:>12,.2f}")
print(f"    Alpaca Daily P&L:     ${alp_daily_pnl:>+12,.2f}")
print(f"    Bot Balance:          ${bot_balance:>12,.2f}")
print(f"    Bot Daily P&L (rec):  {bot_daily_pnl_rec}")
print(f"    Bot Calc Realized:    ${grand_bot_pnl:>+12,.2f}")
