#!/usr/bin/env python3
"""
TRADE MONITORING & ALERT SYSTEM
Checks for initiated trades and sends alerts every 5-10 minutes
Tracks entry, targets, stop loss, and exit times
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time as dt_time
import time
import json
from config import PROJECT_ROOT, DATA_DIR

class TradeAlertMonitor:
    def __init__(self, alert_interval=5):
        """Initialize trade alert monitor
        
        Args:
            alert_interval: Minutes between alerts (default: 5)
        """
        self.alert_interval = alert_interval
        self.positions_file = os.path.join(DATA_DIR, 'active_positions.json')
        self.alerts_log = 'trade_alerts.log'
        self.active_positions = {}
        self.load_positions()
        
        print("=" * 100)
        print("🔔 TRADE MONITORING & ALERT SYSTEM")
        print("=" * 100)
        print(f"Alert Interval: {alert_interval} minutes")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
        print("=" * 100)
    
    def load_positions(self):
        """Load active positions from file"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    self.active_positions = json.load(f)
                print(f"✅ Loaded {len(self.active_positions)} active position(s)")
            except:
                self.active_positions = {}
        else:
            print("📝 No active positions file found - will create on first trade")
    
    def save_positions(self):
        """Save active positions to file"""
        with open(self.positions_file, 'w') as f:
            json.dump(self.active_positions, f, indent=2)
    
    def log_alert(self, message):
        """Log alert to file and console"""
        timestamp = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
        log_entry = f"[{timestamp}] {message}\n"
        
        with open(self.alerts_log, 'a') as f:
            f.write(log_entry)
        
        print(message)
    
    def add_position(self, ticker, direction, strike, entry_premium, entry_time, 
                     target_1, target_2, target_3, stop_loss, contracts=1):
        """Add a new position to monitor"""
        position_id = f"{ticker}_{direction}_{strike}_{entry_time}"
        
        self.active_positions[position_id] = {
            'ticker': ticker,
            'direction': direction,
            'strike': strike,
            'entry_premium': entry_premium,
            'entry_time': entry_time,
            'entry_date': datetime.now().strftime('%Y-%m-%d'),
            'contracts': contracts,
            'targets': {
                '1:2': {'value': target_1, 'hit': False, 'time': None},
                '1:3': {'value': target_2, 'hit': False, 'time': None},
                '1:4': {'value': target_3, 'hit': False, 'time': None}
            },
            'stop_loss': stop_loss,
            'stop_hit': False,
            'last_underlying': None,
            'auto_close_on_spike': True,
            'status': 'ACTIVE',
            'current_premium': entry_premium,
            'pnl': 0,
            'pnl_pct': 0,
            'alerts_sent': 0,
            'last_alert': None
        }
        
        self.save_positions()
        self.log_alert(f"✅ POSITION ADDED: {ticker} {direction} ${strike} - Entry: ${entry_premium:.2f}")
        return position_id
    
    def get_live_price(self, ticker):
        """Get current live price"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            
            if not price:
                hist = stock.history(period='1d', interval='1m')
                if len(hist) > 0:
                    price = hist['Close'].iloc[-1]
            
            return price
        except Exception as e:
            return None
    
    def estimate_option_premium(self, ticker, direction, strike, entry_premium):
        """Estimate current option premium based on price movement"""
        current_price = self.get_live_price(ticker)
        
        if not current_price:
            return entry_premium
        
        # Get historical data for ATR
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='5d', interval='1d')
            
            if len(hist) < 2:
                return entry_premium
            
            # Calculate simple volatility
            hist['TR'] = hist['High'] - hist['Low']
            atr = hist['TR'].mean()
            
            # Estimate premium change based on price movement
            # This is simplified - real options pricing is more complex
            price_change_pct = ((current_price - strike) / strike) * 100
            
            # Options leverage approximation
            if direction == 'CALL':
                if current_price > strike:  # ITM
                    intrinsic = current_price - strike
                    time_value = max(atr * 0.2, 0.5)  # Decay over time
                    estimated_premium = intrinsic + time_value
                else:  # OTM
                    estimated_premium = max(atr * 0.15, 0.3)
            else:  # PUT
                if current_price < strike:  # ITM
                    intrinsic = strike - current_price
                    time_value = max(atr * 0.2, 0.5)
                    estimated_premium = intrinsic + time_value
                else:  # OTM
                    estimated_premium = max(atr * 0.15, 0.3)
            
            return estimated_premium
            
        except:
            return entry_premium
    
    def check_mandatory_exit_time(self):
        """Check if it's time for mandatory exit (3:00 PM CT = 4:00 PM ET)"""
        now = datetime.now()
        exit_time = dt_time(15, 0)  # 3:00 PM CT
        current_time = now.time()
        
        return current_time >= exit_time
    
    def update_position(self, position_id):
        """Update position with current data"""
        position = self.active_positions[position_id]
        
        if position['status'] != 'ACTIVE':
            return position
        
        ticker = position['ticker']
        direction = position['direction']
        strike = position['strike']
        entry_premium = position['entry_premium']
        
        # Get current premium estimate
        current_premium = self.estimate_option_premium(ticker, direction, strike, entry_premium)
        position['current_premium'] = current_premium
        
        # Get current underlying price and detect sudden spikes
        current_underlying = self.get_live_price(ticker)
        prev_underlying = position.get('last_underlying')
        if current_underlying is not None:
            position['last_underlying'] = current_underlying

        # If underlying jumped significantly (e.g. >3%) adjust targets upward
        try:
            if prev_underlying and current_underlying:
                spike_pct = abs(current_underlying - prev_underlying) / prev_underlying
            else:
                spike_pct = 0
        except Exception:
            spike_pct = 0

        # Recalculate conservative targets when a fast spike is detected
        if spike_pct >= 0.03:
            # Keep existing targets but ensure they are at least the standard 1:2/1:3/1:4 multiples
            t1 = max(position['targets']['1:2']['value'], position['entry_premium'] * 2)
            t2 = max(position['targets']['1:3']['value'], position['entry_premium'] * 3)
            t3 = max(position['targets']['1:4']['value'], position['entry_premium'] * 4)
            position['targets']['1:2']['value'] = float(t1)
            position['targets']['1:3']['value'] = float(t2)
            position['targets']['1:4']['value'] = float(t3)
            self.log_alert(f"⚙️ Targets adjusted for spike ({spike_pct:.2%}) on {ticker}: {t1:.2f},{t2:.2f},{t3:.2f}")
        
        # Calculate P&L
        premium_change = current_premium - entry_premium
        position['pnl'] = premium_change * 100 * position['contracts']
        position['pnl_pct'] = (premium_change / entry_premium) * 100
        
        # Check targets
        for target_name, target_data in position['targets'].items():
            if not target_data['hit'] and current_premium >= target_data['value']:
                target_data['hit'] = True
                target_data['time'] = datetime.now().strftime('%I:%M:%S %p')
                self.log_alert(f"🎯 TARGET HIT! {ticker} {target_name} - Premium: ${current_premium:.2f}")

        # If premium is already well above the highest target, optionally auto-close
        try:
            highest = position['targets']['1:4']['value']
        except Exception:
            highest = None

        if highest and current_premium >= highest * 1.02 and position.get('auto_close_on_spike', True):
            # Auto-close to capture higher current premium
            self.log_alert(f"🚀 Premium spike: current ${current_premium:.2f} >= top target ${highest:.2f}. Auto-closing {ticker}.")
            # call close_position which will set status and save
            try:
                self.close_position(position_id, exit_premium=current_premium)
            except Exception as e:
                self.log_alert(f"❌ Error auto-closing {position_id}: {e}")
            return position
        
        # Check stop loss
        if not position['stop_hit'] and current_premium <= position['stop_loss']:
            position['stop_hit'] = True
            self.log_alert(f"🛑 STOP LOSS HIT! {ticker} - Premium: ${current_premium:.2f}")
        
        # Check mandatory exit time
        if self.check_mandatory_exit_time():
            if position['status'] == 'ACTIVE':
                position['status'] = 'EXIT_TIME'
                self.log_alert(f"⏰ MANDATORY EXIT TIME! Close {ticker} immediately!")
        
        self.save_positions()
        return position
    
    def generate_alert(self, position_id):
        """Generate detailed alert for position"""
        position = self.update_position(position_id)
        
        ticker = position['ticker']
        direction = position['direction']
        strike = position['strike']
        
        print("\n" + "=" * 100)
        print(f"🔔 ALERT #{position['alerts_sent'] + 1} - {datetime.now().strftime('%I:%M:%S %p')}")
        print("=" * 100)
        
        print(f"\n📊 POSITION: {ticker} {direction} ${strike}")
        print(f"   Entry Premium: ${position['entry_premium']:.2f}")
        print(f"   Current Premium: ${position['current_premium']:.2f}")
        print(f"   P&L: ${position['pnl']:.2f} ({'+' if position['pnl_pct'] >= 0 else ''}{position['pnl_pct']:.1f}%)")
        print(f"   Contracts: {position['contracts']}")
        print(f"   Status: {position['status']}")
        
        print(f"\n🎯 TARGETS:")
        for target_name, target_data in position['targets'].items():
            status = "✅ HIT" if target_data['hit'] else "⏳ PENDING"
            hit_time = f" at {target_data['time']}" if target_data['hit'] else ""
            print(f"   {target_name}: ${target_data['value']:.2f} - {status}{hit_time}")
        
        stop_status = "🛑 HIT" if position['stop_hit'] else "✅ SAFE"
        print(f"\n🛑 STOP LOSS: ${position['stop_loss']:.2f} - {stop_status}")
        
        # Current stock price
        current_price = self.get_live_price(ticker)
        if current_price:
            print(f"\n💰 CURRENT STOCK PRICE: ${current_price:.2f}")
        
        # Action recommendations
        print(f"\n💡 RECOMMENDED ACTIONS:")
        if position['targets']['1:2']['hit'] and not position['targets']['1:3']['hit']:
            print(f"   ✅ Close 33% of position (1:2 target hit)")
            print(f"   📊 Let 67% run to 1:3 target")
        elif position['targets']['1:3']['hit'] and not position['targets']['1:4']['hit']:
            print(f"   ✅ Close another 33% (1:3 target hit)")
            print(f"   📊 Let 34% run to 1:4 target")
        elif position['targets']['1:4']['hit']:
            print(f"   ✅ Close remaining position (1:4 target hit!)")
        elif position['stop_hit']:
            print(f"   🛑 CLOSE POSITION IMMEDIATELY - Stop loss triggered")
        elif position['status'] == 'EXIT_TIME':
            print(f"   ⏰ MANDATORY EXIT - Close all positions NOW!")
        else:
            print(f"   📊 Hold position - monitor for target hits")
        
        # Time remaining
        now = datetime.now()
        exit_time = now.replace(hour=15, minute=0, second=0)
        if now < exit_time:
            time_remaining = exit_time - now
            hours = time_remaining.seconds // 3600
            minutes = (time_remaining.seconds % 3600) // 60
            print(f"\n⏰ TIME REMAINING: {hours}h {minutes}m until mandatory exit")
        else:
            print(f"\n⏰ PAST EXIT TIME - CLOSE POSITIONS IMMEDIATELY!")
        
        print("=" * 100)
        
        position['alerts_sent'] += 1
        position['last_alert'] = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
        self.save_positions()
    
    def close_position(self, position_id, exit_premium=None):
        """Close a position"""
        if position_id not in self.active_positions:
            print(f"❌ Position {position_id} not found")
            return
        
        position = self.active_positions[position_id]
        
        if exit_premium is None:
            exit_premium = position['current_premium']
        
        # Normalize fields for compatibility with other scripts / web UI
        position['status'] = 'closed'
        position['exit_premium'] = exit_premium
        position['exit_time'] = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')

        # Ensure legacy fields exist for web app ('entry' / 'exit')
        try:
            position['entry'] = position.get('entry', position.get('entry_premium', position.get('entry_price')))
        except Exception:
            position['entry'] = position.get('entry_premium')
        position['exit'] = exit_premium

        # Calculate final P&L and percent
        entry_val = position.get('entry_premium', position.get('entry', 0))
        final_pnl = (exit_premium - entry_val) * 100 * position.get('contracts', 1)
        final_pnl_pct = ((exit_premium - entry_val) / entry_val) * 100 if entry_val > 0 else 0

        # Save P&L fields in both naming conventions
        position['pnl'] = final_pnl
        position['pnl_pct'] = final_pnl_pct

        self.save_positions()
        
        print(f"\n✅ POSITION CLOSED")
        print(f"   {position['ticker']} {position['direction']} ${position['strike']}")
        print(f"   Entry: ${position['entry_premium']:.2f}")
        print(f"   Exit: ${exit_premium:.2f}")
        print(f"   Final P&L: ${final_pnl:.2f} ({'+' if final_pnl_pct >= 0 else ''}{final_pnl_pct:.1f}%)")
        
        self.log_alert(f"✅ CLOSED: {position['ticker']} - P&L: ${final_pnl:.2f} ({final_pnl_pct:.1f}%)")
    
    def monitor_positions(self, duration_minutes=None):
        """Monitor all active positions with regular alerts"""
        print(f"\n🔴 Starting position monitoring...")
        print(f"Alert frequency: Every {self.alert_interval} minutes")
        
        if not self.active_positions:
            print("\n⚠️  No active positions to monitor!")
            print("\nTo add a position, use:")
            print("  monitor.add_position(ticker, direction, strike, entry_premium, ...)")
            return
        
        active_count = sum(1 for p in self.active_positions.values() if p['status'] == 'ACTIVE')
        print(f"Monitoring {active_count} active position(s)\n")
        
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes) if duration_minutes else None
        
        try:
            while True:
                # Check all active positions
                for position_id in list(self.active_positions.keys()):
                    position = self.active_positions[position_id]
                    
                    if position['status'] == 'ACTIVE':
                        self.generate_alert(position_id)
                
                # Check if monitoring should end
                if end_time and datetime.now() >= end_time:
                    print("\n⏰ Monitoring duration complete")
                    break
                
                # Check mandatory exit time
                if self.check_mandatory_exit_time():
                    print("\n⚠️  MANDATORY EXIT TIME REACHED!")
                    print("All positions should be closed by now.")
                    break
                
                # Wait for next alert
                print(f"\n💤 Next alert in {self.alert_interval} minutes...")
                time.sleep(self.alert_interval * 60)
                
        except KeyboardInterrupt:
            print("\n\n⚠️  Monitoring stopped by user")
        
        print("\n" + "=" * 100)
        print("📊 MONITORING SESSION COMPLETE")
        print("=" * 100)
    
    def get_summary(self):
        """Get summary of all positions"""
        print("\n" + "=" * 100)
        print("📊 POSITIONS SUMMARY")
        print("=" * 100)
        
        if not self.active_positions:
            print("\n⚠️  No positions found")
            return
        
        active = [p for p in self.active_positions.values() if p['status'] == 'ACTIVE']
        closed = [p for p in self.active_positions.values() if p['status'] == 'CLOSED']
        
        print(f"\n✅ ACTIVE POSITIONS: {len(active)}")
        for pos in active:
            print(f"\n   {pos['ticker']} {pos['direction']} ${pos['strike']}")
            print(f"   Entry: ${pos['entry_premium']:.2f} | Current: ${pos['current_premium']:.2f}")
            print(f"   P&L: ${pos['pnl']:.2f} ({'+' if pos['pnl_pct'] >= 0 else ''}{pos['pnl_pct']:.1f}%)")
            print(f"   Alerts Sent: {pos['alerts_sent']}")
        
        if closed:
            print(f"\n📁 CLOSED POSITIONS: {len(closed)}")
            total_pnl = 0
            for pos in closed:
                pnl = (pos['exit_premium'] - pos['entry_premium']) * 100 * pos['contracts']
                total_pnl += pnl
                print(f"\n   {pos['ticker']} {pos['direction']} ${pos['strike']}")
                print(f"   P&L: ${pnl:.2f}")
            
            print(f"\n💰 TOTAL P&L (Closed): ${total_pnl:.2f}")
        
        print("\n" + "=" * 100)


def setup_from_predictions():
    """Setup positions from today's predictions"""
    print("\n" + "=" * 100)
    print("🎯 SETUP POSITIONS FROM PREDICTIONS")
    print("=" * 100)
    
    monitor = TradeAlertMonitor(alert_interval=5)
    
    # Check if user has entered positions
    print("\n💡 Have you entered any trades based on today's analysis?")
    response = input("Enter 'y' if yes, 'n' if no: ").strip().lower()
    
    if response != 'y':
        print("\n📝 No trades initiated yet.")
        print("\nRun: python3 execute_trade_setup.py")
        print("Then come back here to set up monitoring.")
        return None
    
    # Get position details
    print("\n" + "=" * 100)
    print("📝 ENTER POSITION DETAILS")
    print("=" * 100)
    
    ticker = input("\nTicker (SPY/QQQ): ").strip().upper()
    direction = input("Direction (CALL/PUT): ").strip().upper()
    strike = float(input("Strike Price: $"))
    entry_premium = float(input("Entry Premium: $"))
    contracts = int(input("Number of Contracts: "))
    entry_time = datetime.now().strftime('%I:%M %p')
    
    # Calculate targets (1:2, 1:3, 1:4 ratios)
    target_1 = entry_premium * 2  # 100% gain
    target_2 = entry_premium * 3  # 200% gain
    target_3 = entry_premium * 4  # 300% gain
    stop_loss = entry_premium * 0.5  # 50% loss
    
    print(f"\n✅ Calculated targets:")
    print(f"   Target 1 (1:2): ${target_1:.2f}")
    print(f"   Target 2 (1:3): ${target_2:.2f}")
    print(f"   Target 3 (1:4): ${target_3:.2f}")
    print(f"   Stop Loss: ${stop_loss:.2f}")
    
    # Add position
    position_id = monitor.add_position(
        ticker=ticker,
        direction=direction,
        strike=strike,
        entry_premium=entry_premium,
        entry_time=entry_time,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
        stop_loss=stop_loss,
        contracts=contracts
    )
    
    print(f"\n✅ Position added successfully!")
    print(f"   Position ID: {position_id}")
    
    return monitor


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == '--setup':
        # Interactive setup mode
        monitor = setup_from_predictions()
        if monitor:
            print("\n🔴 Start monitoring now?")
            response = input("Enter 'y' to start, 'n' to exit: ").strip().lower()
            if response == 'y':
                monitor.monitor_positions()
    else:
        # Quick demo/test mode
        print("\n💡 USAGE OPTIONS:")
        print("\n1. Interactive Setup (Recommended):")
        print("   python3 trade_monitor_alerts.py --setup")
        print("\n2. In Python Script:")
        print("   from trade_monitor_alerts import TradeAlertMonitor")
        print("   monitor = TradeAlertMonitor(alert_interval=5)")
        print("   monitor.add_position(...)")
        print("   monitor.monitor_positions()")
        print("\n" + "=" * 100)
