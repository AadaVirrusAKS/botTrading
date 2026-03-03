#!/usr/bin/env python3
from web_app import app
import json

client = app.test_client()

payload = {'position_key': 'AMD_CALL_210'}
resp = client.post('/api/positions/restore', json=payload)
print('STATUS:', resp.status_code)
try:
    print('RESPONSE:', resp.get_json())
except Exception:
    print('RESPONSE TEXT:', resp.data.decode())

# Show restored entry from active_positions.json
with open('active_positions.json','r') as f:
    positions = json.load(f)

print('\nACTIVE ENTRY:')
print(json.dumps(positions.get('AMD_CALL_210', {}), indent=2))
