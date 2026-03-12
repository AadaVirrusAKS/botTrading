#!/usr/bin/env python3
"""Fix balance using bot_engine's proper recalculate_balance"""
import json
import sys
sys.path.insert(0, '/Users/akum221/Documents/TradingCode')
from services.bot_engine import recalculate_balance

with open('ai_bot_state.json') as f:
    state = json.load(f)

acct = state['demo_account']
old_balance = acct['balance']
initial = acct.get('initial_balance', 10000.0)

correct_balance = recalculate_balance(acct)
print(f"Initial balance: ${initial:.2f}")
print(f"Old balance: ${old_balance:.2f}")
print(f"Correct balance: ${correct_balance:.2f}")
print(f"Difference: ${correct_balance - old_balance:.2f}")

if abs(correct_balance - old_balance) > 0.01:
    acct['balance'] = correct_balance
    with open('ai_bot_state.json', 'w') as f:
        json.dump(state, f, indent=2)
    print(f"\nBalance FIXED: ${old_balance:.2f} -> ${correct_balance:.2f}")
else:
    print("\nBalance already correct")
