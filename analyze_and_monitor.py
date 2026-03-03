#!/usr/bin/env python3
"""
AUTOMATED TRADE ANALYZER & MONITOR
1. Generates predictions for tomorrow's trades
2. Creates trade setups
3. Monitors initiated trades with 5-10 min alerts
"""

import subprocess
import sys
import os
from datetime import datetime
import json

def run_predictions():
    """Run prediction analysis"""
    print("\n" + "=" * 100)
    print("🔮 STEP 1: GENERATING PREDICTIONS")
    print("=" * 100)
    
    result = subprocess.run(
        ['python3', 'next_day_options_predictor.py'],
        capture_output=False
    )
    
    return result.returncode == 0

def run_trade_setup():
    """Generate trade setups"""
    print("\n" + "=" * 100)
    print("⚡ STEP 2: GENERATING TRADE SETUPS")
    print("=" * 100)
    
    result = subprocess.run(
        ['python3', 'execute_trade_setup.py'],
        capture_output=False
    )
    
    return result.returncode == 0

def check_for_initiated_trades():
    """Check if user has initiated any trades"""
    print("\n" + "=" * 100)
    print("📊 STEP 3: CHECK INITIATED TRADES")
    print("=" * 100)
    
    # Check for active positions file
    if os.path.exists('active_positions.json'):
        try:
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
                active = [p for p in positions.values() if p['status'] == 'ACTIVE']
                
                if active:
                    print(f"\n✅ Found {len(active)} active position(s)!")
                    for pos in active:
                        print(f"\n   📊 {pos['ticker']} {pos['direction']} ${pos['strike']}")
                        print(f"      Entry: ${pos['entry_premium']:.2f}")
                        print(f"      Contracts: {pos['contracts']}")
                    return True
        except:
            pass
    
    print("\n⚠️  No active positions found.")
    print("\n💡 Would you like to enter a position now?")
    return False

def setup_monitoring():
    """Setup monitoring for trades"""
    print("\n" + "=" * 100)
    print("🔴 STEP 4: SETUP MONITORING")
    print("=" * 100)
    
    from trade_monitor_alerts import TradeAlertMonitor, setup_from_predictions
    
    # Try to load existing positions
    monitor = TradeAlertMonitor(alert_interval=5)
    
    if monitor.active_positions:
        active_count = sum(1 for p in monitor.active_positions.values() if p['status'] == 'ACTIVE')
        
        if active_count > 0:
            print(f"\n✅ Loaded {active_count} active position(s)")
            monitor.get_summary()
            
            print("\n🔴 Start monitoring with 5-minute alerts?")
            response = input("Enter 'y' to start, 'n' to skip: ").strip().lower()
            
            if response == 'y':
                duration = input("\nMonitor duration in minutes (default: 360 = 6 hours): ").strip()
                try:
                    duration = int(duration) if duration else 360
                except:
                    duration = 360
                
                print(f"\n🔴 Starting monitoring for {duration} minutes...")
                print("Alerts will be sent every 5 minutes")
                print("Press Ctrl+C to stop\n")
                
                monitor.monitor_positions(duration_minutes=duration)
                return True
    
    # No active positions - offer to add one
    print("\n📝 No active positions to monitor.")
    print("\n💡 Would you like to add a position now?")
    response = input("Enter 'y' to add position, 'n' to exit: ").strip().lower()
    
    if response == 'y':
        monitor = setup_from_predictions()
        if monitor:
            print("\n🔴 Start monitoring?")
            response = input("Enter 'y' to start: ").strip().lower()
            if response == 'y':
                monitor.monitor_positions()
        return True
    
    return False

def main():
    """Main workflow"""
    print("=" * 100)
    print("🤖 AUTOMATED TRADE ANALYZER & MONITOR")
    print("=" * 100)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
    print("=" * 100)
    
    print("\n📋 This script will:")
    print("   1. Generate predictions for tomorrow's trades")
    print("   2. Create detailed trade setups")
    print("   3. Check if you've initiated any trades")
    print("   4. Monitor positions with alerts every 5 minutes")
    
    print("\n" + "=" * 100)
    response = input("\nReady to begin? (y/n): ").strip().lower()
    
    if response != 'y':
        print("\n👋 Cancelled by user")
        return
    
    # Step 1: Generate predictions
    if not run_predictions():
        print("\n❌ Prediction generation failed")
        return
    
    print("\n✅ Predictions generated!")
    input("\nPress Enter to continue to trade setups...")
    
    # Step 2: Generate trade setups
    if not run_trade_setup():
        print("\n❌ Trade setup generation failed")
        return
    
    print("\n✅ Trade setups generated!")
    input("\nPress Enter to continue to monitoring setup...")
    
    # Step 3 & 4: Setup and start monitoring
    setup_monitoring()
    
    print("\n" + "=" * 100)
    print("✅ WORKFLOW COMPLETE")
    print("=" * 100)
    print(f"Ended: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
    print("=" * 100)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        print("\n✅ Goodbye! 👋\n")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
