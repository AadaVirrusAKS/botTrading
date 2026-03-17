#!/usr/bin/env python3
"""Analyze today's trades in Bot vs Alpaca and find P&L gaps."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from datetime import datetime
from config import PROJECT_ROOT, DATA_DIR

TODAY = '2026-03-13'

def load_bot_state():
    with open(os.path.join(DATA_DIR, 'ai_bot_state.json')) as f:
        return json.load(f)

def analyze_bot_trades(state):
    trades = state.get('demo_account', {}).get('trades', [])
    today_trades = [t for t in trades if t.get('timestamp', '').startswith(TODAY)]
    
    buys = [t for t in today_trades if t.get('action') == 'BUY']
    sells = [t for t in today_trades if t.get('action') == 'SELL']
    
    print(f"{'='*100}")
    print(f"BOT TRADES TODAY ({TODAY}) - Total: {len(today_trades)} | Buys: {len(buys)} | Sells: {len(sells)}")
    print(f"{'='*100}")
    
    # Print all trades
    total_realized_pnl = 0
    for i, t in enumerate(today_trades):
        symbol = t.get('symbol', '?')
        action = t.get('action', '?')
        instr = t.get('instrument_type', '?')
        contract = t.get('contract', '')
        qty = t.get('quantity', 0)
        price = t.get('price', 0)
        entry_price = t.get('entry_price', 0)
        exit_price = t.get('exit_price', 0)
        pnl = t.get('pnl', None)
        pnl_pct = t.get('pnl_pct', None)
        reason = t.get('reason', '')
        ts = t.get('timestamp', '')
        alpaca_id = t.get('alpaca_order_id', '')
        option_type = t.get('option_type', '')
        
        time_part = ts.split('T')[1][:8] if 'T' in ts else ts
        
        if action == 'SELL' and pnl is not None:
            total_realized_pnl += pnl
        
        pnl_str = f"P&L=${pnl:.2f} ({pnl_pct:.1f}%)" if pnl is not None else ""
        alpaca_str = alpaca_id[:25] if alpaca_id else "NO_ALPACA_ID"
        
        print(f"  {i+1:2d}. {time_part} | {action:4s} {symbol:5s} {contract:30s} | {instr:6s} | qty={qty:3d} @ ${price:.2f} | {pnl_str:25s} | {reason[:50]:50s} | alpaca={alpaca_str}")
    
    print(f"\n  TOTAL REALIZED P&L (Bot closed trades): ${total_realized_pnl:.2f}")
    return today_trades, total_realized_pnl

def analyze_open_positions(state):
    positions = state.get('demo_account', {}).get('positions', [])
    
    print(f"\n{'='*100}")
    print(f"BOT OPEN POSITIONS ({len(positions)})")
    print(f"{'='*100}")
    
    total_unrealized = 0
    total_cost = 0
    for p in positions:
        symbol = p.get('symbol', '?')
        contract = p.get('contract', '')
        qty = p.get('quantity', 0)
        entry = p.get('entry_price', 0)
        current = p.get('current_price', 0)
        instr = p.get('instrument_type', 'stock')
        multiplier = 100 if instr == 'option' else 1
        unrealized = (current - entry) * qty * multiplier
        cost = entry * qty * multiplier
        total_unrealized += unrealized
        total_cost += cost
        alpaca_id = p.get('alpaca_order_id', '')
        ts = p.get('timestamp', '')
        time_part = ts.split('T')[1][:8] if 'T' in ts else ''
        
        print(f"  {symbol:5s} {contract:30s} | qty={qty:3d} entry=${entry:.2f} current=${current:.2f} | unrealized=${unrealized:>+9.2f} | opened={time_part} | alpaca={alpaca_id[:25] if alpaca_id else 'N/A'}")
    
    print(f"\n  TOTAL UNREALIZED P&L: ${total_unrealized:.2f}")
    print(f"  TOTAL POSITION COST:  ${total_cost:.2f}")
    return positions, total_unrealized

def analyze_bot_balance(state):
    demo = state.get('demo_account', {})
    balance = demo.get('balance', 0)
    initial = demo.get('initial_balance', 0)
    daily_pnl = demo.get('daily_pnl', {})
    today_pnl = daily_pnl.get(TODAY, 'NOT SET')
    
    print(f"\n{'='*100}")
    print(f"BOT ACCOUNT SUMMARY")
    print(f"{'='*100}")
    print(f"  Balance:         ${balance:.2f}")
    print(f"  Initial Balance: ${initial:.2f}")
    print(f"  Overall P&L:     ${balance - initial:.2f}")
    print(f"  Daily P&L entry: {today_pnl}")
    
    # Recent daily P&L entries
    print(f"\n  Recent Daily P&L entries:")
    for date in sorted(daily_pnl.keys())[-10:]:
        print(f"    {date}: ${daily_pnl[date]:.2f}" if isinstance(daily_pnl[date], (int, float)) else f"    {date}: {daily_pnl[date]}")
    
    return balance, initial

def analyze_alpaca():
    """Try to get Alpaca positions and orders via the service."""
    print(f"\n{'='*100}")
    print(f"ALPACA PAPER TRADING DATA")
    print(f"{'='*100}")
    
    try:
        from services.alpaca_service import get_account, get_positions, get_orders, is_configured, ALPACA_AVAILABLE
        
        if not ALPACA_AVAILABLE:
            print("  ⚠️ alpaca-py not installed")
            return None, None, None
        
        if not is_configured():
            print("  ⚠️ Alpaca not configured (no API keys)")
            return None, None, None
        
        # Account info
        account = get_account()
        print(f"\n  ALPACA ACCOUNT:")
        print(f"    Equity:         ${float(account.get('equity', 0)):,.2f}")
        print(f"    Cash:           ${float(account.get('cash', 0)):,.2f}")
        print(f"    Buying Power:   ${float(account.get('buying_power', 0)):,.2f}")
        print(f"    Portfolio Value: ${float(account.get('portfolio_value', 0)):,.2f}")
        print(f"    P&L Today:      ${float(account.get('pnl_today', account.get('equity', 0)) or 0):,.2f}")
        
        # Positions
        positions = get_positions()
        print(f"\n  ALPACA POSITIONS ({len(positions)}):")
        alpaca_total_unrealized = 0
        alpaca_total_cost = 0
        for p in positions:
            sym = p.get('symbol', '?')
            qty = p.get('qty', 0)
            entry = float(p.get('avg_entry_price', 0))
            current = float(p.get('current_price', 0))
            unrealized = float(p.get('unrealized_pl', 0))
            market_val = float(p.get('market_value', 0))
            cost_basis = float(p.get('cost_basis', 0))
            side = p.get('side', '?')
            alpaca_total_unrealized += unrealized
            alpaca_total_cost += cost_basis
            print(f"    {sym:25s} | side={side} qty={qty} entry=${entry:.2f} current=${current:.2f} | unrealized=${unrealized:>+10.2f} | cost=${cost_basis:.2f} mkt_val=${market_val:.2f}")
        
        print(f"\n    ALPACA TOTAL UNREALIZED: ${alpaca_total_unrealized:.2f}")
        print(f"    ALPACA TOTAL COST BASIS: ${alpaca_total_cost:.2f}")
        
        # Today's orders
        orders = get_orders()
        today_orders = [o for o in orders if o.get('submitted_at', '').startswith(TODAY) or o.get('created_at', '').startswith(TODAY)]
        print(f"\n  ALPACA ORDERS TODAY ({len(today_orders)} of {len(orders)} total):")
        for o in today_orders:
            sym = o.get('symbol', '?')
            side = o.get('side', '?')
            qty = o.get('qty', 0)
            filled_qty = o.get('filled_qty', 0)
            filled_avg = o.get('filled_avg_price', 0)
            status = o.get('status', '?')
            order_type = o.get('type', '?')
            order_id = o.get('id', '')
            created = o.get('created_at', '')
            print(f"    {created[:19]:19s} | {side:4s} {sym:25s} | qty={qty} filled={filled_qty} @ ${float(filled_avg) if filled_avg else 0:.2f} | status={status} | type={order_type} | id={order_id[:25]}")
        
        return account, positions, today_orders
    except Exception as e:
        print(f"  ❌ Error fetching Alpaca data: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None

def compare_pnl(state, bot_realized_pnl, bot_unrealized_pnl, alpaca_account, alpaca_positions, alpaca_orders):
    """Compare Bot P&L vs Alpaca P&L and find gaps."""
    print(f"\n{'='*100}")
    print(f"P&L GAP ANALYSIS")
    print(f"{'='*100}")
    
    demo = state.get('demo_account', {})
    bot_balance = demo.get('balance', 0)
    
    print(f"\n  BOT SIDE:")
    print(f"    Realized P&L today:   ${bot_realized_pnl:.2f}")
    print(f"    Unrealized P&L:       ${bot_unrealized_pnl:.2f}")
    print(f"    Total (R+U):          ${bot_realized_pnl + bot_unrealized_pnl:.2f}")
    print(f"    Cash Balance:         ${bot_balance:.2f}")
    
    if alpaca_account:
        alpaca_equity = float(alpaca_account.get('equity', 0))
        alpaca_cash = float(alpaca_account.get('cash', 0))
        alpaca_unrealized = sum(float(p.get('unrealized_pl', 0)) for p in (alpaca_positions or []))
        
        print(f"\n  ALPACA SIDE:")
        print(f"    Equity:               ${alpaca_equity:,.2f}")
        print(f"    Cash:                 ${alpaca_cash:,.2f}")
        print(f"    Unrealized P&L:       ${alpaca_unrealized:,.2f}")
        
        print(f"\n  GAPS:")
        print(f"    Bot Unrealized vs Alpaca Unrealized: ${bot_unrealized_pnl - alpaca_unrealized:+.2f}")
        
        # Position-level comparison
        bot_positions = demo.get('positions', [])
        alpaca_pos_map = {}
        if alpaca_positions:
            for p in alpaca_positions:
                sym = p.get('symbol', '')
                alpaca_pos_map[sym] = p
        
        print(f"\n  POSITION-BY-POSITION COMPARISON:")
        print(f"  {'Symbol':25s} | {'Bot Qty':>8s} | {'Alp Qty':>8s} | {'Bot Entry':>10s} | {'Alp Entry':>10s} | {'Bot Curr':>10s} | {'Alp Curr':>10s} | {'Bot UPnL':>10s} | {'Alp UPnL':>10s} | {'PnL Gap':>10s}")
        print(f"  {'-'*25}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")
        
        matched = set()
        for bp in bot_positions:
            alpaca_sym = bp.get('alpaca_symbol', '') or bp.get('option_ticker', '') or bp.get('symbol', '')
            bot_sym = bp.get('symbol', '')
            contract = bp.get('contract', '')
            instr = bp.get('instrument_type', 'stock')
            multiplier = 100 if instr == 'option' else 1
            
            bot_qty = bp.get('quantity', 0)
            bot_entry = bp.get('entry_price', 0)
            bot_current = bp.get('current_price', 0)
            bot_upnl = (bot_current - bot_entry) * bot_qty * multiplier
            
            ap = alpaca_pos_map.get(alpaca_sym, {})
            alp_qty = ap.get('qty', '-')
            alp_entry = float(ap.get('avg_entry_price', 0)) if ap else 0
            alp_current = float(ap.get('current_price', 0)) if ap else 0
            alp_upnl = float(ap.get('unrealized_pl', 0)) if ap else 0
            
            gap = bot_upnl - alp_upnl if ap else bot_upnl
            
            label = f"{bot_sym} {contract}" if contract else bot_sym
            matched.add(alpaca_sym)
            
            flag = " ⚠️" if abs(gap) > 10 else ""
            print(f"  {label:25s} | {bot_qty:>8d} | {str(alp_qty):>8s} | ${bot_entry:>9.2f} | ${alp_entry:>9.2f} | ${bot_current:>9.2f} | ${alp_current:>9.2f} | ${bot_upnl:>+9.2f} | ${alp_upnl:>+9.2f} | ${gap:>+9.2f}{flag}")
        
        # Alpaca positions not in bot
        for sym, ap in alpaca_pos_map.items():
            if sym not in matched:
                alp_qty = ap.get('qty', 0)
                alp_entry = float(ap.get('avg_entry_price', 0))
                alp_current = float(ap.get('current_price', 0))
                alp_upnl = float(ap.get('unrealized_pl', 0))
                print(f"  {sym:25s} | {'N/A':>8s} | {str(alp_qty):>8s} | {'N/A':>10s} | ${alp_entry:>9.2f} | {'N/A':>10s} | ${alp_current:>9.2f} | {'N/A':>10s} | ${alp_upnl:>+9.2f} | ⚠️ ONLY IN ALPACA")
    else:
        print(f"\n  ⚠️ Could not fetch Alpaca data for comparison")

    # Check for order ID mismatches
    bot_positions = demo.get('positions', [])
    print(f"\n  ORDER FILL PRICE ANALYSIS (Bot entry vs what Alpaca filled):")
    if alpaca_orders:
        order_map = {o.get('id', ''): o for o in alpaca_orders}
        for bp in bot_positions:
            alpaca_oid = bp.get('alpaca_order_id', '')
            if alpaca_oid and alpaca_oid in order_map:
                ao = order_map[alpaca_oid]
                bot_entry = bp.get('entry_price', 0)
                alp_filled = float(ao.get('filled_avg_price', 0)) if ao.get('filled_avg_price') else 0
                diff = bot_entry - alp_filled if alp_filled else 0
                instr = bp.get('instrument_type', 'stock')
                multiplier = 100 if instr == 'option' else 1
                qty = bp.get('quantity', 0)
                cost_diff = diff * qty * multiplier
                flag = " ⚠️ ENTRY PRICE GAP" if abs(diff) > 0.01 else " ✅"
                print(f"    {bp.get('symbol',''):5s} {bp.get('contract',''):25s} | Bot entry=${bot_entry:.2f} | Alpaca fill=${alp_filled:.2f} | diff=${diff:+.4f} | cost_impact=${cost_diff:+.2f}{flag}")

if __name__ == '__main__':
    state = load_bot_state()
    today_trades, realized_pnl = analyze_bot_trades(state)
    positions, unrealized_pnl = analyze_open_positions(state)
    alpaca_account, alpaca_positions, alpaca_orders = analyze_alpaca()
    compare_pnl(state, realized_pnl, unrealized_pnl, alpaca_account, alpaca_positions, alpaca_orders)
