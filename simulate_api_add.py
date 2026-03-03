#!/usr/bin/env python3
"""Simulate bot and manual adds via web_app Flask test client.
Verifies that manual trades are preserved when a bot tries to add a position with the same key.
"""
import json
from web_app import app

client = app.test_client()

# 1) Add manual position
manual = {
    'symbol': 'AMD',
    'type': 'option',
    'direction': 'CALL',
    'strike': 210,
    'expiration': '2026-02-27',
    'entry_price': 5.65,
    'quantity': 4,
    'source': 'manual'
}
resp = client.post('/api/positions/add', json=manual)
print('Manual add status:', resp.status_code, resp.json.get('message'))

# 2) Simulate bot adding same position (should create a new key unless allow_merge true)
bot = manual.copy()
bot['source'] = 'bot'
bot['entry_price'] = 8.07
# Mark this as a bot-originated request and explicitly allow merge when intended
bot['allow_merge'] = True
resp2 = client.post('/api/positions/add', json=bot)
print('Bot add status:', resp2.status_code, resp2.json.get('message'))

# 3) Show positions containing AMD
with open('active_positions.json','r') as f:
    positions = json.load(f)

amd_positions = {k: v for k, v in positions.items() if 'AMD' in k}
print('\nAMD positions in active_positions.json:')
for k, v in amd_positions.items():
    print('-', k, json.dumps({
        'entry': v.get('entry'),
        'entry_premium': v.get('entry_premium'),
        'quantity': v.get('quantity'),
        'source': v.get('source', 'manual'),
        'status': v.get('status')
    }))
