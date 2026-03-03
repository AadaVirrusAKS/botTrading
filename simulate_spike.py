#!/usr/bin/env python3
from trade_monitor_alerts import TradeAlertMonitor
import time

m = TradeAlertMonitor(alert_interval=1)
# Add a test position with minimum-ish premium and 1 contract
pos_id = m.add_position('AMD', 'CALL', 110, entry_premium=1.00, entry_time='SIM',
                        target_1=2.00, target_2=3.00, target_3=4.00, stop_loss=0.50, contracts=1)

# Seed last underlying to simulate previous price
m.active_positions[pos_id]['last_underlying'] = 100.0
m.save_positions()

# Monkeypatch live price and estimated premium to simulate a sudden spike
m.get_live_price = lambda t: 104.5  # ~4.5% spike
m.estimate_option_premium = lambda ticker, direction, strike, entry: 4.30  # premium above top target

print('\n=== BEFORE UPDATE ===')
print(m.active_positions[pos_id])

# Trigger an alert/update which should detect spike, adjust targets, and auto-close
m.generate_alert(pos_id)

print('\n=== AFTER UPDATE ===')
print(m.active_positions.get(pos_id))

# Print saved active_positions.json for verification
with open('active_positions.json','r') as f:
    print('\n=== active_positions.json ===')
    print(f.read())
