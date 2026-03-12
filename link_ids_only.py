#!/usr/bin/env python3
"""
Revert fix: Restore original bot prices but keep alpaca_order_ids linked.
- Read alpaca_order_ids from the synced version (bak_before_revert)
- Apply ONLY the alpaca_order_id field to the reverted state (bak_before_sync)
- Do NOT change any prices, P&L, or other fields
"""
import json
from datetime import datetime

STATE_FILE = 'ai_bot_state.json'
SYNCED_FILE = 'ai_bot_state.json.bak_before_revert'

today = datetime.now().strftime('%Y-%m-%d')

# Load reverted state (original bot prices - the CORRECT one)
with open(STATE_FILE) as f:
    state = json.load(f)

# Load synced state (has alpaca_order_ids we want)
with open(SYNCED_FILE) as f:
    synced = json.load(f)

# Get today's trades from both
orig_trades = state['demo_account']['trades']
sync_trades = synced['demo_account']['trades']

orig_today = [(i, t) for i, t in enumerate(orig_trades) if t.get('timestamp','').startswith(today)]
sync_today = [(i, t) for i, t in enumerate(sync_trades) if t.get('timestamp','').startswith(today)]

print(f"Original today: {len(orig_today)} trades")
print(f"Synced today: {len(sync_today)} trades")
print()

changes = 0
for j, (oi, ot) in enumerate(orig_today):
    if j < len(sync_today):
        si, st = sync_today[j]
        # Verify same trade by matching action + symbol
        if ot.get('action') == st.get('action') and ot.get('symbol') == st.get('symbol'):
            synced_aid = st.get('alpaca_order_id')
            orig_aid = ot.get('alpaca_order_id')
            
            if synced_aid and not orig_aid:
                orig_trades[oi]['alpaca_order_id'] = synced_aid
                changes += 1
                print(f"  #{j+1} {ot['action']:6} {ot['symbol']:5} -> linked alpaca_id={synced_aid[:12]}...")
            elif synced_aid and orig_aid:
                # Already has one - keep whichever
                pass
            else:
                print(f"  #{j+1} {ot['action']:6} {ot['symbol']:5} -> no alpaca_id in synced version either")
        else:
            print(f"  #{j+1} MISMATCH: orig={ot.get('action')} {ot.get('symbol')} vs sync={st.get('action')} {st.get('symbol')}")

# Save
with open(STATE_FILE, 'w') as f:
    json.dump(state, f, indent=2)

print(f"\nLinked {changes} alpaca_order_ids (prices UNCHANGED)")

# Verify
acct = state['demo_account']
today_trades = [t for t in acct['trades'] if t.get('timestamp','').startswith(today)]
with_id = sum(1 for t in today_trades if t.get('alpaca_order_id'))
wins = sum(1 for t in today_trades if (t.get('pnl') or 0) > 0)
losses = sum(1 for t in today_trades if (t.get('pnl') or 0) < 0)
total_pnl = sum((t.get('pnl') or 0) for t in today_trades)
print(f"\nWith alpaca_order_id: {with_id}/{len(today_trades)}")
print(f"Wins: {wins}, Losses: {losses}")
print(f"Total P&L: ${total_pnl:.2f}")
print(f"Balance: ${acct['balance']:.2f}")
