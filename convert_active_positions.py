#!/usr/bin/env python3
"""Migration utility: normalize entries in active_positions.json
- Ensure closed/active status is lowercase
- Ensure options have `entry_premium` and `exit_premium`
- Ensure legacy `entry`/`exit` fields exist
- Write back updated file and print a summary
"""
import json
import os
from datetime import datetime

FILE = 'active_positions.json'

if not os.path.exists(FILE):
    print('No active_positions.json found')
    raise SystemExit(1)

with open(FILE, 'r') as f:
    data = json.load(f)

changed = 0
for k, pos in data.items():
    orig = pos.copy()
    # Normalize status
    if 'status' in pos and isinstance(pos['status'], str):
        s = pos['status'].lower()
        if pos['status'] != s:
            pos['status'] = s
            changed += 1
    # Ensure entry and entry_premium
    if pos.get('type') == 'option':
        if 'entry_premium' not in pos and 'entry' in pos:
            pos['entry_premium'] = pos['entry']
            changed += 1
        if 'entry' not in pos and 'entry_premium' in pos:
            pos['entry'] = pos['entry_premium']
            changed += 1
        # Closed option: ensure exit_premium
        if pos.get('status') == 'closed':
            if 'exit_premium' not in pos and 'exit' in pos:
                pos['exit_premium'] = pos['exit']
                changed += 1
            if 'exit' not in pos and 'exit_premium' in pos:
                pos['exit'] = pos['exit_premium']
                changed += 1
    else:
        # For stocks, ensure entry/exit names exist
        if 'entry' not in pos and 'entry_price' in pos:
            pos['entry'] = pos['entry_price']
            changed += 1
        if pos.get('status') == 'closed' and 'exit' not in pos and 'exit_price' in pos:
            pos['exit'] = pos['exit_price']
            changed += 1

    # Ensure pnl fields exist for closed
    if pos.get('status') == 'closed':
        if 'pnl' not in pos:
            try:
                entry_val = pos.get('entry_premium', pos.get('entry', 0))
                exit_val = pos.get('exit_premium', pos.get('exit', 0))
                qty = pos.get('contracts', pos.get('quantity', 1))
                multiplier = 100 if pos.get('type') == 'option' else 1
                pos['pnl'] = (exit_val - entry_val) * qty * multiplier
                pos['pnl_pct'] = ((exit_val - entry_val) / entry_val) * 100 if entry_val else 0
                changed += 1
            except Exception:
                pass

with open(FILE + '.bak.' + datetime.now().strftime('%Y%m%d%H%M%S'), 'w') as f:
    json.dump(data, f, indent=2)

with open(FILE, 'w') as f:
    json.dump(data, f, indent=2)

print(f'Migration complete. Fields updated: {changed}')
print('Wrote backup and updated', FILE)
