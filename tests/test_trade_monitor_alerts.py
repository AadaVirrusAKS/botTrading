import json
import os
from trade_monitor_alerts import TradeAlertMonitor


def test_auto_close_on_spike(tmp_path):
    # Use isolated positions file and log
    positions_file = tmp_path / "positions.json"
    alerts_log = tmp_path / "alerts.log"

    monitor = TradeAlertMonitor(alert_interval=1)
    monitor.positions_file = str(positions_file)
    monitor.alerts_log = str(alerts_log)
    monitor.active_positions = {}

    # Add a position (entry premium $1.00, targets 2/3/4)
    pos_id = monitor.add_position('AMD', 'CALL', 110, entry_premium=1.00, entry_time='TEST',
                                  target_1=2.00, target_2=3.00, target_3=4.00, stop_loss=0.50, contracts=1)

    # Seed previous underlying price
    monitor.active_positions[pos_id]['last_underlying'] = 100.0
    monitor.save_positions()

    # Simulate a spike and premium jump
    monitor.get_live_price = lambda t: 104.5  # ~4.5% spike
    monitor.estimate_option_premium = lambda ticker, direction, strike, entry: 4.30

    # Trigger update / alert
    monitor.generate_alert(pos_id)

    # Reload positions from file and assert closed and correct exit
    with open(monitor.positions_file, 'r') as f:
        data = json.load(f)

    assert pos_id in data
    pos = data[pos_id]
    assert pos['status'] == 'CLOSED'
    assert abs(pos['exit_premium'] - 4.3) < 1e-6
    assert pos['targets']['1:4']['hit'] is True

    # Check alerts log created
    assert os.path.exists(monitor.alerts_log)


if __name__ == '__main__':
    # Run the test directly for environments without pytest
    test_auto_close_on_spike(__import__('tempfile').gettempdir())
