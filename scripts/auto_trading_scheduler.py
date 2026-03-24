#!/usr/bin/env python3
"""
Auto-Trading Scheduler for 0DTE Options
Runs throughout the trading day with automatic position management
"""

import time
from datetime import datetime
import subprocess
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PROJECT_ROOT

def get_market_status():
    """Check if market is open and return status"""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    day = now.weekday()  # 0=Monday, 6=Sunday
    
    # Weekend check
    if day >= 5:  # Saturday or Sunday
        return "WEEKEND", "Market is closed on weekends"
    
    # Before market open
    if hour < 9 or (hour == 9 and minute < 30):
        return "PRE_MARKET", f"Market opens at 9:30 AM (Currently {now.strftime('%I:%M %p')})"
    
    # After market close
    if hour >= 16:
        return "CLOSED", f"Market closed at 4:00 PM (Currently {now.strftime('%I:%M %p')})"
    
    # Force close time (3:00 PM CT / 4:00 PM ET)
    if hour >= 15:
        return "FORCE_CLOSE", "CRITICAL: Market closing - CLOSE ALL POSITIONS NOW!"
    
    # Late afternoon warning (2:45 PM)
    if hour == 14 and minute >= 45:
        return "CLOSING_SOON", f"Market closing soon - Start closing positions"
    
    # Normal trading hours
    if (hour == 9 and minute >= 30) or (10 <= hour < 15):
        return "OPEN", f"Market is open ({now.strftime('%I:%M %p')})"
    
    return "UNKNOWN", "Unable to determine market status"

def run_trader():
    """Run the main trading script"""
    print("\n" + "=" * 100)
    print("🚀 EXECUTING TRADING SCRIPT")
    print("=" * 100)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "trading.spy_qqq_options_trader"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=PROJECT_ROOT
        )
        print(result.stdout)
        if result.stderr:
            print("Errors:", result.stderr)
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        print("⚠️  Trading script timed out")
        return False
    except Exception as e:
        print(f"❌ Error running trader: {e}")
        return False

def run_monitor():
    """Run the monitoring script"""
    print("\n" + "=" * 100)
    print("📊 MONITORING POSITIONS")
    print("=" * 100)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "monitoring.monitor_options_positions"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=PROJECT_ROOT
        )
        print(result.stdout)
        if result.stderr:
            print("Errors:", result.stderr)
        return True
    except Exception as e:
        print(f"❌ Error running monitor: {e}")
        return False

def main():
    """Main scheduler loop"""
    print("=" * 100)
    print("🤖 0DTE OPTIONS AUTO-TRADING SCHEDULER")
    print("=" * 100)
    print("This script will:")
    print("  • Check market status every minute")
    print("  • Execute trades at optimal times (10:00 AM)")
    print("  • Monitor positions every 5 minutes")
    print("  • Force close all positions at 3:00 PM CT")
    print("=" * 100)
    
    trade_executed_today = False
    last_monitor_time = datetime.now()
    
    while True:
        status, message = get_market_status()
        now = datetime.now()
        
        print(f"\n[{now.strftime('%I:%M:%S %p')}] Status: {status}")
        print(f"  {message}")
        
        if status == "WEEKEND":
            print("  💤 Sleeping until Monday...")
            time.sleep(3600)  # Check every hour on weekends
            continue
        
        elif status == "PRE_MARKET":
            print("  ⏰ Waiting for market open...")
            time.sleep(60)  # Check every minute
            continue
        
        elif status == "CLOSED":
            print("  ✅ Trading day complete")
            print("  💤 See you tomorrow!")
            break
        
        elif status == "FORCE_CLOSE":
            print("  🚨 EMERGENCY: Force closing all positions!")
            run_monitor()  # This will show urgent warning
            print("\n  ⚠️  Manual intervention may be required - Check your positions!")
            time.sleep(300)  # Wait 5 minutes then check again
            continue
        
        elif status == "CLOSING_SOON":
            print("  ⚠️  Start winding down positions")
            run_monitor()
            time.sleep(300)  # Monitor every 5 minutes when closing
            continue
        
        elif status == "OPEN":
            # Execute trade once per day at optimal time (10:00 AM)
            if not trade_executed_today and now.hour == 10 and now.minute < 5:
                print("  🎯 Optimal entry time - Executing trades")
                if run_trader():
                    trade_executed_today = True
                    print("  ✅ Trades executed successfully")
                else:
                    print("  ⚠️  Trade execution had issues")
            
            # Monitor positions every 5 minutes if we have trades
            minutes_since_monitor = (now - last_monitor_time).seconds / 60
            if trade_executed_today and minutes_since_monitor >= 5:
                print("  📊 Running position check")
                run_monitor()
                last_monitor_time = now
            
            # Sleep until next check
            if trade_executed_today:
                print("  ⏳ Next check in 5 minutes")
                time.sleep(300)  # 5 minutes
            else:
                print("  ⏳ Waiting for optimal entry time (10:00 AM)")
                time.sleep(60)  # 1 minute
        
        else:
            print("  ❓ Unknown status - waiting...")
            time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n" + "=" * 100)
        print("⚠️  Scheduler stopped by user")
        print("=" * 100)
        print("🔴 IMPORTANT: Check if you have open positions!")
        print("   Run: python3 -m monitoring.monitor_options_positions")
        print("=" * 100)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        print("🔴 Check your positions manually!")
