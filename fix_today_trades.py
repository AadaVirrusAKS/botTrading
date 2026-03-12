#!/usr/bin/env python3
"""
Retroactive fix: Sync today's AI Bot trade prices with actual Alpaca fill prices.
Fixes all BUY entries and SELL/CLOSE exits, recalculates P&L.
"""
import json
import shutil
from datetime import datetime
from services.alpaca_service import get_order_by_id, get_orders

STATE_FILE = 'ai_bot_state.json'
TODAY = '2026-03-12'

def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2) 

def get_alpaca_fill(order_id):
    """Fetch the Alpaca fill price for an order."""
    try:
        order = get_order_by_id(order_id)
        return order.get('filled_avg_price')
    except Exception as e:
        print(f"  ⚠️ Error fetching order {order_id}: {e}")
        return None

def find_matching_alpaca_order(alpaca_orders, symbol, side, qty, bot_timestamp):
    """Find an Alpaca order matching a local-only bot trade by symbol, side, qty, and time."""
    # Convert bot local time to approximate UTC (EST = UTC-5)
    from datetime import timedelta
    bot_dt = datetime.fromisoformat(bot_timestamp)
    bot_utc = bot_dt + timedelta(hours=5)
    
    best_match = None
    best_time_diff = float('inf')
    
    for o in alpaca_orders:
        if o['symbol'] != symbol:
            continue
        if o['side'] != side:
            continue
        if o['filled_qty'] != qty:
            continue
        # Already used?
        if o.get('_matched'):
            continue
        
        filled_at = o.get('filled_at', '')
        if filled_at:
            alpaca_dt = datetime.fromisoformat(filled_at.replace('+00:00', '').replace('Z', ''))
            time_diff = abs((alpaca_dt - bot_utc).total_seconds())
            if time_diff < best_time_diff and time_diff < 60:  # Within 60 seconds
                best_time_diff = time_diff
                best_match = o
    
    return best_match

def main():
    # Backup state
    shutil.copy2(STATE_FILE, f'{STATE_FILE}.bak_before_sync')
    print(f"✅ Backed up state to {STATE_FILE}.bak_before_sync")
    
    state = load_state()
    account = state.get('demo_account', {})
    trades = account.get('trades', [])
    
    # Get today's trades
    today_trades = [(i, t) for i, t in enumerate(trades) if TODAY in t.get('timestamp', '')]
    print(f"\n📊 Today's trades: {len(today_trades)}")
    
    # Get all Alpaca closed orders for matching local exits
    all_alpaca_orders = get_orders(status='closed', limit=500)
    today_alpaca = [o for o in all_alpaca_orders if o.get('created_at', '').startswith(TODAY)]
    print(f"📊 Alpaca orders today: {len(today_alpaca)}")
    
    # STEP 1: Build map of alpaca_order_id -> fill_price for all trades with alpaca_order_id
    alpaca_fills = {}  # order_id -> fill_price
    for idx, t in today_trades:
        order_id = t.get('alpaca_order_id')
        if order_id and order_id not in alpaca_fills:
            fill = get_alpaca_fill(order_id)
            if fill:
                alpaca_fills[order_id] = fill
    
    print(f"\n✅ Fetched {len(alpaca_fills)} Alpaca fill prices")
    
    # STEP 2: Try to match local-only exits to Alpaca orders
    local_matched = {}  # trade_index -> alpaca_order
    for idx, t in today_trades:
        if t.get('alpaca_order_id'):
            continue  # Already has Alpaca order
        action = t.get('action', '')
        if action not in ('SELL', 'CLOSE'):
            continue
        
        # Map the option contract to OCC symbol
        contract = t.get('contract', '')
        option_ticker = t.get('option_ticker', '')
        if not option_ticker:
            # Try to find from earlier BUY trade
            for _, bt in today_trades:
                if bt.get('contract') == contract and bt.get('option_ticker'):
                    option_ticker = bt['option_ticker']
                    break
        
        if not option_ticker:
            print(f"  ⚠️ Trade {idx} ({t['symbol']} {action}): No option_ticker, skipping")
            continue
        
        match = find_matching_alpaca_order(
            today_alpaca, option_ticker, 'sell', float(t.get('quantity', 0)),
            t.get('timestamp', '')
        )
        
        if match:
            match['_matched'] = True
            local_matched[idx] = match
            print(f"  🔗 Matched local trade {idx} ({t['symbol']} {action}) to Alpaca order {match['id'][:8]} fill=${match['filled_avg_price']}")
        else:
            print(f"  ❌ No Alpaca match for trade {idx} ({t['symbol']} {action} @ {t.get('timestamp','')[11:19]})")
    
    # STEP 3: Group BUY trades by contract for matching with SELL/CLOSE
    # Key: (contract, bot_price) ensures a SELL is matched to the BUY with the same
    # original bot entry price, avoiding cross-match with previous-day positions.
    buy_queues = {}  # contract -> list of buy info dicts
    for idx, t in today_trades:
        if t.get('action') != 'BUY':
            continue
        contract = t.get('contract', '')
        order_id = t.get('alpaca_order_id')
        alpaca_fill = alpaca_fills.get(order_id)
        if contract not in buy_queues:
            buy_queues[contract] = []
        buy_queues[contract].append({
            'index': idx,
            'bot_price': t.get('price'),
            'alpaca_fill': alpaca_fill,
            'quantity': t.get('quantity'),
            'used': False,
        })
    
    # STEP 4: Fix BUY trades
    print("\n--- Fixing BUY trades ---")
    fixed_buys = 0
    for idx, t in today_trades:
        if t.get('action') != 'BUY':
            continue
        order_id = t.get('alpaca_order_id')
        alpaca_fill = alpaca_fills.get(order_id)
        if alpaca_fill and alpaca_fill != t.get('price'):
            old_price = t['price']
            t['price'] = alpaca_fill
            t['cost'] = alpaca_fill * t.get('quantity', 1) * 100
            print(f"  ✅ Trade {idx} {t['symbol']:6} BUY: ${old_price:.2f} → ${alpaca_fill:.2f} (diff: ${alpaca_fill - old_price:+.2f})")
            fixed_buys += 1
        elif not alpaca_fill:
            print(f"  ⚠️ Trade {idx} {t['symbol']:6} BUY: No Alpaca fill available")
    
    # STEP 5: Fix SELL/CLOSE trades
    print("\n--- Fixing SELL/CLOSE trades ---")
    fixed_exits = 0
    
    for idx, t in today_trades:
        action = t.get('action', '')
        if action not in ('SELL', 'CLOSE'):
            continue
        
        contract = t.get('contract', '')
        order_id = t.get('alpaca_order_id')
        
        # Get the exit fill price
        exit_fill = None
        if order_id:
            exit_fill = alpaca_fills.get(order_id)
        elif idx in local_matched:
            exit_fill = local_matched[idx].get('filled_avg_price')
        
        # Get the entry fill price by matching this SELL's entry_price to a BUY's bot_price
        # This correctly handles previous-day positions (their entry_price won't match any today BUY)
        entry_fill = None
        original_entry = t.get('entry_price', 0)
        if contract in buy_queues:
            for buy_info in buy_queues[contract]:
                if buy_info['used']:
                    continue
                # Match by bot price ≈ SELL's recorded entry_price (within tolerance for float issues)
                if abs(buy_info['bot_price'] - original_entry) < 0.01:
                    entry_fill = buy_info.get('alpaca_fill')
                    buy_info['used'] = True
                    break
        
        old_entry = t.get('entry_price', 0)
        old_exit = t.get('exit_price') or t.get('price', 0)
        old_pnl = t.get('pnl', 0)
        
        changed = False
        
        # Update entry price from matched BUY's Alpaca fill
        if entry_fill and entry_fill != old_entry:
            t['entry_price'] = entry_fill
            changed = True
        
        # Update exit price from Alpaca fill
        if exit_fill:
            if t.get('exit_price') is not None:
                t['exit_price'] = exit_fill
            t['price'] = exit_fill
            changed = True
        
        # Recalculate P&L
        if changed:
            actual_entry = t.get('entry_price', old_entry)
            actual_exit = exit_fill if exit_fill else (t.get('exit_price') or t.get('price'))
            qty = t.get('quantity', 1)
            multiplier = 100  # Options
            
            new_pnl = (actual_exit - actual_entry) * qty * multiplier
            new_pnl_pct = ((actual_exit - actual_entry) / actual_entry * 100) if actual_entry else 0
            
            t['pnl'] = new_pnl
            t['pnl_pct'] = new_pnl_pct
            
            source = "ALP" if order_id else ("MATCH" if idx in local_matched else "LOCAL")
            print(f"  ✅ Trade {idx} {t['symbol']:6} {action:5} [{source}]: entry ${old_entry:.2f}→${t['entry_price']:.2f}, exit ${old_exit:.2f}→${actual_exit:.2f}, pnl ${old_pnl:+.2f}→${new_pnl:+.2f}")
            fixed_exits += 1
        else:
            print(f"  ⚠️ Trade {idx} {t['symbol']:6} {action:5}: No changes needed or no fill data")
    
    # STEP 6: Recalculate account balance
    account['balance'] = _recalculate_balance(account)
    
    # Save
    save_state(state)
    print(f"\n✅ Fixed {fixed_buys} BUY trades and {fixed_exits} SELL/CLOSE trades")
    print(f"✅ Updated balance: ${account['balance']:.2f}")
    print(f"✅ State saved to {STATE_FILE}")

def _recalculate_balance(account):
    """Recalculate balance from trade history (mirrors bot_engine.recalculate_balance)."""
    initial_balance = account.get('initial_balance', 100000)
    realized_pnl = 0
    open_cost = 0
    
    for trade in account.get('trades', []):
        action = trade.get('action', '')
        if action in ('SELL', 'CLOSE'):
            pnl = trade.get('pnl', 0)
            if pnl:
                realized_pnl += pnl
        elif action == 'BUY':
            # Check if this buy has a matching sell (position closed)
            # If not, it's an open position cost
            pass
    
    # Open positions reduce available balance
    for pos in account.get('positions', []):
        entry = pos.get('entry_price', 0)
        qty = pos.get('quantity', 0)
        if pos.get('instrument_type') == 'option':
            open_cost += entry * qty * 100
        else:
            open_cost += entry * qty
    
    return initial_balance + realized_pnl - open_cost

if __name__ == '__main__':
    main()
