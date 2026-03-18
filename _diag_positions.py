#!/usr/bin/env python3
"""Find bot positions with active options."""
import json, glob, os

for f in sorted(glob.glob('data/bot_state*.json')):
    if '.bak' in f:
        continue
    with open(f) as fh:
        data = json.load(fh)
    positions = data.get('positions', {})
    active = {k: v for k, v in positions.items() if v.get('status') == 'active'}
    if active:
        print(f'=== {f}: {len(active)} active ===')
        for k, v in active.items():
            ticker = v.get('ticker', '?')
            ptype = v.get('type', '?')
            strike = v.get('strike', 'N/A')
            exp = v.get('expiration', 'N/A')
            entry = v.get('entry_premium', v.get('entry', '?'))
            current = v.get('current_premium', v.get('current_price', '?'))
            direction = v.get('direction', '?')
            print(f'  {k}: {ticker} {direction} ${strike} exp={exp} entry={entry} current={current} type={ptype}')

# Also check ai_bot_state
for f in ['data/ai_bot_state.json']:
    if not os.path.exists(f):
        continue
    with open(f) as fh:
        data = json.load(fh)
    positions = data.get('positions', {})
    active = {k: v for k, v in positions.items() if v.get('status') == 'active'}
    if active:
        print(f'\n=== {f}: {len(active)} active ===')
        for k, v in active.items():
            ticker = v.get('ticker', '?')
            ptype = v.get('type', '?')
            strike = v.get('strike', 'N/A')
            exp = v.get('expiration', 'N/A')
            entry = v.get('entry_premium', v.get('entry', '?'))
            current = v.get('current_premium', v.get('current_price', '?'))
            direction = v.get('direction', '?')
            print(f'  {k}: {ticker} {direction} ${strike} exp={exp} entry={entry} current={current} type={ptype}')
