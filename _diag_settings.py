"""Check all settings for None values"""
import json, os

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ai_bot_state.json')
d = json.load(open(STATE_FILE))
settings = d.get('settings', {})
print('All settings:')
for k, v in sorted(settings.items()):
    truthy = bool(v) if v is not None else 'None(falsy!)'
    flag = ' *** NONE (will break boolean default!)' if v is None else ''
    print(f'  {k}: {v!r}  (truthy={truthy}){flag}')
