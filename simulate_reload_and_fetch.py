#!/usr/bin/env python3
from web_app import app
import json

client = app.test_client()

# Trigger reload
r = client.post('/api/positions/reload')
print('RELOAD STATUS:', r.status_code)
print('RELOAD RESPONSE:', r.get_json())

# Fetch positions active
r2 = client.get('/api/positions/active')
print('\nPOSITIONS API STATUS:', r2.status_code)
data = r2.get_json()
print('API success:', data.get('success'))
print('Active positions count:', len(data.get('positions', [])))

# List AMD positions
amd = [p for p in data.get('positions', []) if p.get('symbol') == 'AMD']
print('\nAMD positions returned by API:')
for p in amd:
    print('-', p.get('position_key'), '| entry:', p.get('entry_price'), '| source:', p.get('source'))

# Also show closed positions with AMD
amd_closed = [p for p in data.get('closed_positions', []) if p.get('symbol') == 'AMD']
print('\nAMD closed positions returned by API:')
for p in amd_closed:
    print('-', p.get('position_key'), '| entry:', p.get('entry_price'))
