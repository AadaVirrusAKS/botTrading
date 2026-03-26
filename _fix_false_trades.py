#!/usr/bin/env python3
"""One-time script to identify and remove false exit trades from bot state."""
import json
import sys
import shutil
from datetime import datetime

STATE_FILE = 'data/bot_state_user_1.json'

with open(STATE_FILE) as f:
    state = json.load(f)

account = state.get('demo_account', {})
trades = account.get('trades', [])

# Identify the two false exit trades
false_exits = []
for i, t in enumerate(trades):
    # IWM STOP_LOSS exit today (stale Alpaca lastPrice $2.78)
    if (t.get('symbol') == 'IWM'
        and t.get('reason') == 'STOP_LOSS'
        and '2026-03-26' in t.get('timestamp', '')
        and abs(t.get('exit_price', t.get('price', 0)) - 2.78) < 0.01):
        false_exits.append((i, t))
        print(f"[{i}] IWM FALSE EXIT: SELL @ ${t.get('price')}, pnl=${t.get('pnl'):.2f}, alpaca_id={t.get('alpaca_order_id')}")
    
    # PLTR exit yesterday (incorrect price)
    if (t.get('symbol') == 'PLTR'
        and t.get('action') in ('SELL', 'CLOSE')
        and '2026-03-25T14:57' in t.get('timestamp', '')
        and abs(t.get('price', 0) - 3.61) < 0.01):
        false_exits.append((i, t))
        print(f"[{i}] PLTR FALSE EXIT: SELL @ ${t.get('price')}, pnl=${t.get('pnl'):.2f}, alpaca_id={t.get('alpaca_order_id')}")

# Find original BUY trades
print("\n=== ORIGINAL BUY TRADES ===")
iwm_buy = None
pltr_buy = None
for t in trades:
    if t.get('symbol') == 'IWM' and t.get('action') == 'BUY' and '2026-03-26' in t.get('timestamp', ''):
        iwm_buy = t
        print(f"IWM BUY: qty={t.get('quantity')}, price=${t.get('price')}, alpaca_id={t.get('alpaca_order_id')}")
    if (t.get('symbol') == 'PLTR' and t.get('action') == 'BUY'
        and t.get('contract', '') == 'PLTR $152P 2026-04-02'
        and '2026-03-25' in t.get('timestamp', '')):
        pltr_buy = t
        print(f"PLTR BUY: qty={t.get('quantity')}, price=${t.get('price')}, ticker={t.get('option_ticker')}, alpaca_id={t.get('alpaca_order_id')}")

print(f"\nFound {len(false_exits)} false exits")
print(f"Current balance: ${account.get('balance', 0):.2f}")
print(f"Current positions: {len(account.get('positions', []))}")

if '--apply' not in sys.argv:
    print("\nDRY RUN — pass --apply to execute changes")
    print("\n⚠️  NOTE: Both exits were FILLED on Alpaca:")
    print("  IWM sell filled @ $3.26 (order b50a4352)")
    print("  PLTR sell filled @ $3.50 (order 62830b13)")
    print("  To re-open on Alpaca, you must re-buy manually.")
    sys.exit(0)

# Backup first
backup_path = STATE_FILE + '.pre_fix_' + datetime.now().strftime('%H%M%S')
shutil.copy2(STATE_FILE, backup_path)
print(f"\nBackup saved to {backup_path}")

# Remove false exit trades (by index, reverse order to preserve indices)
false_indices = sorted([i for i, _ in false_exits], reverse=True)
for idx in false_indices:
    removed = trades.pop(idx)
    print(f"Removed trade [{idx}]: {removed['symbol']} {removed.get('action')} @ ${removed.get('price')}")

# Restore positions that were incorrectly closed
positions = account.get('positions', [])

if iwm_buy:
    iwm_pos = {
        'symbol': 'IWM',
        'contract': iwm_buy.get('contract', 'IWM $249P 2026-03-30'),
        'option_ticker': iwm_buy.get('option_ticker', 'IWM260330P00249000'),
        'instrument_type': 'option',
        'option_type': 'put',
        'strike': iwm_buy.get('strike', 249.0),
        'expiry': iwm_buy.get('expiry', '2026-03-30'),
        'dte': iwm_buy.get('dte', 0),
        'side': 'LONG',
        'quantity': iwm_buy.get('quantity', 11),
        'entry_price': iwm_buy.get('price', 3.46),
        'current_price': iwm_buy.get('price', 3.46),
        'stop_loss': round(iwm_buy.get('price', 3.46) * 0.5, 2),
        'target': round(iwm_buy.get('price', 3.46) * 1.37, 2),
        'target_2': round(iwm_buy.get('price', 3.46) * 2.0, 2),
        'timestamp': iwm_buy.get('timestamp'),
        'auto_trade': True,
        'source': 'bot',
        'trade_type': 'swing',
        'alpaca_order_id': iwm_buy.get('alpaca_order_id'),
        '_cached_atr': iwm_buy.get('price', 3.46) * 0.15,
    }
    positions.append(iwm_pos)
    print(f"Restored IWM position: {iwm_pos['quantity']}x @ ${iwm_pos['entry_price']}")

if pltr_buy:
    pltr_pos = {
        'symbol': 'PLTR',
        'contract': pltr_buy.get('contract', 'PLTR $152P 2026-04-02'),
        'option_ticker': pltr_buy.get('option_ticker', 'PLTR260402P00152500'),
        'instrument_type': 'option',
        'option_type': 'put',
        'strike': pltr_buy.get('strike', 152.5),
        'expiry': pltr_buy.get('expiry', '2026-04-02'),
        'dte': pltr_buy.get('dte', 0),
        'side': 'LONG',
        'quantity': pltr_buy.get('quantity', 11),
        'entry_price': pltr_buy.get('price', 3.80),
        'current_price': pltr_buy.get('price', 3.80),
        'stop_loss': round(pltr_buy.get('price', 3.80) * 0.5, 2),
        'target': round(pltr_buy.get('price', 3.80) * 1.37, 2),
        'target_2': round(pltr_buy.get('price', 3.80) * 2.0, 2),
        'timestamp': pltr_buy.get('timestamp'),
        'auto_trade': True,
        'source': 'bot',
        'trade_type': 'swing',
        'alpaca_order_id': pltr_buy.get('alpaca_order_id'),
        '_cached_atr': pltr_buy.get('price', 3.80) * 0.15,
    }
    positions.append(pltr_pos)
    print(f"Restored PLTR position: {pltr_pos['quantity']}x @ ${pltr_pos['entry_price']}")

account['positions'] = positions

# Recalculate balance from trade history
# Use the recalculate_balance function from the bot engine
import sys as _sys
_sys.path.insert(0, '.')
from services.bot_engine import recalculate_balance
old_balance = account.get('balance', 0)
new_balance = recalculate_balance(account)
account['balance'] = new_balance
print(f"\nBalance: ${old_balance:.2f} → ${new_balance:.2f} (delta: ${new_balance - old_balance:.2f})")

# The false exits credited back the cost of positions + pnl
# IWM: 11 contracts * $2.78 * 100 = $3058 returned + pnl -$748 = net $2310 added
# PLTR: 11 contracts * $3.61 * 100 = $3971 returned + pnl -$209 = net $3762 added  
# Removing these exits means those amounts need to be subtracted (positions are still open)
iwm_false_pnl = 0
pltr_false_pnl = 0
for _, t in false_exits:
    if t.get('symbol') == 'IWM':
        iwm_false_pnl = t.get('pnl', 0)
    elif t.get('symbol') == 'PLTR':
        pltr_false_pnl = t.get('pnl', 0)

print(f"IWM false P&L removed: ${iwm_false_pnl:.2f}")
print(f"PLTR false P&L removed: ${pltr_false_pnl:.2f}")

with open(STATE_FILE, 'w') as f:
    json.dump(state, f, indent=2, default=str)

print(f"\nState saved. Positions now: {len(positions)}")
print("⚠️  Alpaca: Both sells were FILLED. To re-open on Alpaca, re-buy manually.")
