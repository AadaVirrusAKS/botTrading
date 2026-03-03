# 🔔 Trade Monitoring & Alert System Guide

## Overview
Automated system that checks for initiated trades and sends alerts every 5-10 minutes with real-time updates on targets, stop losses, and mandatory exit times.

---

## 🎯 Features

### Real-Time Monitoring
✅ **Position Tracking**: Tracks all active positions automatically  
✅ **Live P&L Updates**: Real-time profit/loss calculations  
✅ **Target Alerts**: Notifications when 1:2, 1:3, 1:4 targets hit  
✅ **Stop Loss Alerts**: Immediate warning if stop loss triggered  
✅ **Exit Time Warnings**: Mandatory 2:50 PM CT exit reminders  
✅ **Persistent Storage**: Saves positions to file (survives restarts)  

### Alert System
🔔 **Frequency**: Every 5-10 minutes (configurable)  
📊 **Content**: Current premium, P&L, targets status, recommendations  
📝 **Logging**: All alerts saved to file for review  
⏰ **Time Management**: Countdown to mandatory exit  

---

## 🚀 Quick Start

### Method 1: Interactive Setup (Easiest)
```bash
python3 trade_monitor_alerts.py --setup
```
This will:
1. Ask if you've entered a trade
2. Collect position details (ticker, strike, premium, etc.)
3. Calculate targets and stop loss automatically
4. Start monitoring with 5-minute alerts

### Method 2: Automated Workflow (Complete)
```bash
python3 analyze_and_monitor.py
```
This will:
1. Generate predictions for tomorrow
2. Create detailed trade setups
3. Check for initiated trades
4. Start monitoring automatically

### Method 3: Demo Mode (Test First)
```bash
python3 demo_monitoring.py
```
Shows how the system works with a sample position (1-minute alerts for demo).

---

## 📊 Step-by-Step Workflow

### Morning: Generate Predictions
```bash
# Step 1: Get today's predictions
python3 next_day_options_predictor.py

# Step 2: Get detailed trade setups
python3 execute_trade_setup.py
```

### Enter Your Trade
Based on the predictions:
1. Open your broker platform
2. Find the recommended option (e.g., SPY PUT $676)
3. Enter the trade with LIMIT order
4. Note your:
   - Entry time
   - Entry premium
   - Number of contracts

### Setup Monitoring
```bash
python3 trade_monitor_alerts.py --setup
```

Enter your position details when prompted:
```
Ticker (SPY/QQQ): SPY
Direction (CALL/PUT): PUT
Strike Price: $676
Entry Premium: $5.18
Number of Contracts: 2
```

The system automatically calculates:
- Target 1 (1:2): $10.37
- Target 2 (1:3): $15.55
- Target 3 (1:4): $20.74
- Stop Loss: $2.59

### Monitor Throughout Day
The system will send alerts every 5 minutes like this:

```
====================================================================================================
🔔 ALERT #1 - 11:05:00 AM
====================================================================================================

📊 POSITION: SPY PUT $676
   Entry Premium: $5.18
   Current Premium: $6.45
   P&L: $254.00 (+24.5%)
   Contracts: 2
   Status: ACTIVE

🎯 TARGETS:
   1:2: $10.37 - ⏳ PENDING
   1:3: $15.55 - ⏳ PENDING
   1:4: $20.74 - ⏳ PENDING

🛑 STOP LOSS: $2.59 - ✅ SAFE

💰 CURRENT STOCK PRICE: $672.30

💡 RECOMMENDED ACTIONS:
   📊 Hold position - monitor for target hits

⏰ TIME REMAINING: 3h 45m until mandatory exit
====================================================================================================
```

When targets hit:
```
🎯 TARGET HIT! SPY 1:2 - Premium: $10.50

💡 RECOMMENDED ACTIONS:
   ✅ Close 33% of position (1:2 target hit)
   📊 Let 67% run to 1:3 target
```

---

## 💾 Position Storage

Positions are saved to `active_positions.json`:

```json
{
  "SPY_PUT_676_11:00 AM": {
    "ticker": "SPY",
    "direction": "PUT",
    "strike": 676,
    "entry_premium": 5.18,
    "entry_time": "11:00 AM",
    "contracts": 2,
    "targets": {
      "1:2": {"value": 10.37, "hit": false},
      "1:3": {"value": 15.55, "hit": false},
      "1:4": {"value": 20.74, "hit": false}
    },
    "stop_loss": 2.59,
    "status": "ACTIVE"
  }
}
```

Benefits:
- Survives script restarts
- Track multiple positions
- Historical record
- Resume monitoring anytime

---

## 🎯 Alert Types

### 1. Regular Status Update (Every 5-10 min)
```
🔔 ALERT #5 - 11:25:00 AM
📊 Current Premium: $7.20
💰 P&L: $404.00 (+38.9%)
⏰ TIME REMAINING: 3h 25m
```

### 2. Target Hit Alert
```
🎯 TARGET HIT! SPY 1:3 - Premium: $15.60

💡 RECOMMENDED ACTION:
✅ Close another 33% (1:3 target hit)
📊 Let 34% run to 1:4 target
```

### 3. Stop Loss Alert
```
🛑 STOP LOSS HIT! SPY - Premium: $2.50

💡 RECOMMENDED ACTION:
🛑 CLOSE POSITION IMMEDIATELY - Stop loss triggered
```

### 4. Exit Time Warning
```
⏰ MANDATORY EXIT TIME! Close SPY immediately!

💡 RECOMMENDED ACTION:
⏰ MANDATORY EXIT - Close all positions NOW!
```

---

## ⚙️ Configuration

### Change Alert Interval
```python
# 5-minute alerts
monitor = TradeAlertMonitor(alert_interval=5)

# 10-minute alerts
monitor = TradeAlertMonitor(alert_interval=10)

# 2-minute alerts (aggressive)
monitor = TradeAlertMonitor(alert_interval=2)
```

### Programmatic Usage
```python
from trade_monitor_alerts import TradeAlertMonitor

# Create monitor
monitor = TradeAlertMonitor(alert_interval=5)

# Add position
monitor.add_position(
    ticker='SPY',
    direction='PUT',
    strike=676,
    entry_premium=5.18,
    entry_time='11:00 AM',
    target_1=10.37,
    target_2=15.55,
    target_3=20.74,
    stop_loss=2.59,
    contracts=2
)

# Start monitoring
monitor.monitor_positions(duration_minutes=360)  # 6 hours

# Or check summary anytime
monitor.get_summary()

# Close position
monitor.close_position(position_id, exit_premium=12.50)
```

---

## 📋 Alert Log

All alerts are logged to `trade_alerts.log`:

```
[2025-12-17 11:00:00 AM] ✅ POSITION ADDED: SPY PUT $676 - Entry: $5.18
[2025-12-17 11:05:00 AM] 🔔 ALERT #1 - P&L: +24.5%
[2025-12-17 11:10:00 AM] 🔔 ALERT #2 - P&L: +32.1%
[2025-12-17 11:35:00 AM] 🎯 TARGET HIT! SPY 1:2 - Premium: $10.50
[2025-12-17 12:15:00 PM] 🎯 TARGET HIT! SPY 1:3 - Premium: $15.75
[2025-12-17 02:50:00 PM] ⏰ MANDATORY EXIT TIME! Close SPY immediately!
[2025-12-17 02:52:00 PM] ✅ CLOSED: SPY - P&L: $1,280.00 (+123.6%)
```

---

## 🎓 Example Session

### Complete Walkthrough

**11:00 AM - Enter Trade**
```bash
python3 execute_trade_setup.py
# Review: SPY PUT $676 at $5.18
# Enter 2 contracts in broker
```

**11:05 AM - Setup Monitoring**
```bash
python3 trade_monitor_alerts.py --setup

# Enter details:
Ticker: SPY
Direction: PUT
Strike: 676
Entry Premium: 5.18
Contracts: 2
```

**11:10 AM - First Alert**
```
🔔 ALERT #1
P&L: +$180 (+17.4%)
Status: ACTIVE - Hold position
```

**11:15 AM - Second Alert**
```
🔔 ALERT #2
P&L: +$296 (+28.6%)
Status: ACTIVE - Hold position
```

**11:35 AM - Target 1 Hit!**
```
🎯 TARGET HIT! 1:2 at $10.50
ACTION: Close 33% (sell 0.66 contracts)
Let rest run to 1:3
```

**12:15 PM - Target 2 Hit!**
```
🎯 TARGET HIT! 1:3 at $15.75
ACTION: Close 33% (sell 0.66 contracts)
Let rest run to 1:4
```

**2:50 PM - Exit Time**
```
⏰ MANDATORY EXIT!
Close remaining 0.68 contracts
Final P&L: +$1,280 (+123.6%)
```

---

## 🔧 Troubleshooting

### "No active positions found"
- Make sure you ran `--setup` and entered position details
- Check `active_positions.json` file exists
- Try running setup again

### Alerts not updating
- Check internet connection (needs to fetch live prices)
- Verify ticker symbols are correct
- Try restarting monitoring

### Wrong price estimates
- System uses simplified premium estimation
- For exact prices, check your broker platform
- Use estimates as guidance, not exact values

### Position not closing
- Manually close via broker platform
- Then mark as closed in system:
```python
monitor.close_position(position_id, exit_premium=actual_exit)
```

---

## 📞 Common Commands

```bash
# Setup new monitoring session
python3 trade_monitor_alerts.py --setup

# Run complete workflow (prediction + setup + monitor)
python3 analyze_and_monitor.py

# See demo (test without real positions)
python3 demo_monitoring.py

# Generate predictions only
python3 execute_trade_setup.py

# View real-time dashboard
python3 realtime_trade_monitor.py
```

---

## ⚠️ Important Notes

### Mandatory Exit Time
- System warns at 2:50 PM CT (3:50 PM ET)
- **MUST close all positions by then**
- Options lose value rapidly after 3:50 PM
- Never hold 1DTE options to expiration

### Premium Estimates
- System estimates premium based on price movement
- **Not exact** - check broker for real values
- Use as guidance for monitoring
- Trust your broker's prices for actual trades

### Internet Required
- Needs connection to fetch live prices
- Alerts may delay if connection drops
- Save positions file regularly

### Data Persistence
- Positions saved to `active_positions.json`
- Alerts logged to `trade_alerts.log`
- Back up files if needed
- Can resume monitoring after restart

---

## 🎯 Best Practices

1. **Start Monitoring Early**: Setup monitoring right after entering trade
2. **Check Alerts Regularly**: Don't ignore notifications
3. **Take Partial Profits**: Close 33% at each target as recommended
4. **Respect Stop Loss**: Exit immediately if triggered
5. **Exit on Time**: Never ignore 2:50 PM warning
6. **Keep Log**: Review `trade_alerts.log` to learn
7. **Test First**: Run demo before using with real trades

---

## 📊 System Status

| Component | Status | Description |
|-----------|--------|-------------|
| Position Tracking | ✅ | Monitors all active trades |
| Alert System | ✅ | 5-10 minute notifications |
| Target Detection | ✅ | Auto-detects target hits |
| Stop Loss Monitoring | ✅ | Warns if stop triggered |
| Exit Time Alerts | ✅ | Mandatory 2:50 PM warning |
| P&L Calculation | ✅ | Real-time profit/loss |
| File Persistence | ✅ | Saves between sessions |
| Log History | ✅ | Complete audit trail |

---

## 🚀 Next Steps

1. **Test the System**
   ```bash
   python3 demo_monitoring.py
   ```

2. **Generate Today's Predictions**
   ```bash
   python3 execute_trade_setup.py
   ```

3. **Enter Your Trade** (via broker)

4. **Setup Monitoring**
   ```bash
   python3 trade_monitor_alerts.py --setup
   ```

5. **Let It Run**
   - System sends alerts every 5 minutes
   - Follow recommended actions
   - Close positions as targets hit

---

**✅ SYSTEM READY FOR USE!**

The monitoring system will track your trades, send regular alerts, and help you manage positions throughout the trading day. Never miss a target or stop loss again! 🎯

---

*Last Updated: December 17, 2025*
*Version: 1.0 - Automated Alert System*
