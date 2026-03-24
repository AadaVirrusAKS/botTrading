import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import time
import pytz
import warnings
warnings.filterwarnings('ignore')

# Use centralized caching layer to avoid Yahoo rate limiting
try:
    from services.market_data import (
        cached_get_history, cached_get_price, cached_get_ticker_info,
        _is_globally_rate_limited
    )
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False

# -----------------------------
# AUTOMATED 0DTE TRADER & MONITOR
# Executes SPY & QQQ PUT trades and monitors until 3:00 PM CT
# Target: 1:3 Risk/Reward Ratio
# -----------------------------

class Auto0DTEMonitor:
    def __init__(self):
        """Initialize trader"""
        self.et_tz = pytz.timezone('US/Eastern')
        self.ct_tz = pytz.timezone('US/Central')
        self.positions = []
        self.exit_hour_et = 16  # 4 PM ET
        self.exit_minute_et = 0  # 4:00 PM ET
        self.monitor_interval = 300  # 5 minutes
        
    def get_live_price(self, ticker):
        """Get current live price"""
        try:
            if _USE_CACHED:
                price, _ = cached_get_price(ticker)
                if price:
                    return price
                info = cached_get_ticker_info(ticker) or {}
                return info.get('currentPrice')
            stock = yf.Ticker(ticker)
            price = stock.info.get('currentPrice')
            if not price:
                hist = stock.history(period='1d', interval='1m')
                if len(hist) > 0:
                    price = hist['Close'].iloc[-1]
            return price
        except:
            return None
    
    def get_atr(self, ticker):
        """Calculate ATR"""
        if _USE_CACHED:
            daily = cached_get_history(ticker, period='1mo', interval='1d')
        else:
            stock = yf.Ticker(ticker)
            daily = stock.history(period='1mo', interval='1d')
        if daily is None or daily.empty:
            return None
        daily['TR'] = daily['High'] - daily['Low']
        return daily['TR'].rolling(14).mean().iloc[-1]
    
    def execute_trades(self):
        """Execute both SPY and QQQ PUT trades"""
        print("="*100)
        print("🚀 EXECUTING 0DTE OPTIONS TRADES")
        print("="*100)
        
        now_et = datetime.now(self.et_tz)
        now_ct = datetime.now(self.ct_tz)
        
        print(f"\n⏰ Execution Time: {now_et.strftime('%I:%M %p ET')} / {now_ct.strftime('%I:%M %p CT')}")
        print()
        
        # Execute SPY PUT
        spy_price = self.get_live_price('SPY')
        spy_atr = self.get_atr('SPY')
        
        spy_strike = round(spy_price + (spy_atr * 0.1), 0)
        spy_intrinsic = max(0, spy_strike - spy_price)
        spy_premium = spy_intrinsic + (spy_atr * 0.25)
        spy_stop = spy_premium * 0.5
        spy_target = spy_premium + ((spy_premium - spy_stop) * 3)
        
        spy_position = {
            'ticker': 'SPY',
            'direction': 'PUT',
            'strike': spy_strike,
            'entry_price': spy_price,
            'entry_premium': spy_premium,
            'current_premium': spy_premium,
            'target_premium': spy_target,
            'stop_premium': spy_stop,
            'entry_time': now_et,
            'contracts': 1,
            'status': 'OPEN'
        }
        self.positions.append(spy_position)
        
        print(f"✅ SPY PUT ${spy_strike:.0f}")
        print(f"   Entry: ${spy_price:.2f}")
        print(f"   Premium: ${spy_premium:.2f} (${spy_premium*100:.2f} per contract)")
        print(f"   Target: ${spy_target:.2f} | Stop: ${spy_stop:.2f}")
        print()
        
        # Execute QQQ PUT
        qqq_price = self.get_live_price('QQQ')
        qqq_atr = self.get_atr('QQQ')
        
        qqq_strike = round(qqq_price + (qqq_atr * 0.1), 0)
        qqq_intrinsic = max(0, qqq_strike - qqq_price)
        qqq_premium = qqq_intrinsic + (qqq_atr * 0.25)
        qqq_stop = qqq_premium * 0.5
        qqq_target = qqq_premium + ((qqq_premium - qqq_stop) * 3)
        
        qqq_position = {
            'ticker': 'QQQ',
            'direction': 'PUT',
            'strike': qqq_strike,
            'entry_price': qqq_price,
            'entry_premium': qqq_premium,
            'current_premium': qqq_premium,
            'target_premium': qqq_target,
            'stop_premium': qqq_stop,
            'entry_time': now_et,
            'contracts': 1,
            'status': 'OPEN'
        }
        self.positions.append(qqq_position)
        
        print(f"✅ QQQ PUT ${qqq_strike:.0f}")
        print(f"   Entry: ${qqq_price:.2f}")
        print(f"   Premium: ${qqq_premium:.2f} (${qqq_premium*100:.2f} per contract)")
        print(f"   Target: ${qqq_target:.2f} | Stop: ${qqq_stop:.2f}")
        print()
        
        total_cost = (spy_premium + qqq_premium) * 100
        total_profit = ((spy_target - spy_premium) + (qqq_target - qqq_premium)) * 100
        total_risk = ((spy_premium - spy_stop) + (qqq_premium - qqq_stop)) * 100
        
        print("="*100)
        print(f"💼 POSITION SUMMARY")
        print("="*100)
        print(f"Total Investment: ${total_cost:.2f}")
        print(f"Profit Target: +${total_profit:.2f} (+150%)")
        print(f"Stop Loss: -${total_risk:.2f} (-50%)")
        print(f"Risk/Reward: 1:{total_profit/total_risk:.1f}")
        print("="*100)
        print()
    
    def calculate_current_premium(self, position):
        """Calculate current premium based on live price"""
        ticker = position['ticker']
        strike = position['strike']
        entry_time = position['entry_time']
        
        # Get current price
        current_price = self.get_live_price(ticker)
        
        # Intrinsic value
        intrinsic = max(0, strike - current_price)  # PUT option
        
        # Time decay
        now_et = datetime.now(self.et_tz)
        hours_held = (now_et - entry_time).seconds / 3600
        market_hours_left = (16 - now_et.hour) - (now_et.minute / 60)
        time_decay_factor = max(0, market_hours_left / 6.5)
        
        # Get ATR for time value
        atr = self.get_atr(ticker)
        time_value = (atr * 0.25) * time_decay_factor
        
        current_premium = max(intrinsic + time_value, intrinsic * 1.05)
        
        return current_premium, current_price, intrinsic, time_value
    
    def monitor_positions(self):
        """Monitor open positions"""
        now_et = datetime.now(self.et_tz)
        now_ct = datetime.now(self.ct_tz)
        
        print(f"\n{'='*100}")
        print(f"📊 POSITION UPDATE - {now_et.strftime('%I:%M %p ET')} / {now_ct.strftime('%I:%M %p CT')}")
        print(f"{'='*100}")
        
        open_positions = [p for p in self.positions if p['status'] == 'OPEN']
        
        if not open_positions:
            print("\n✅ All positions closed!")
            return False
        
        for position in open_positions:
            current_premium, current_price, intrinsic, time_value = self.calculate_current_premium(position)
            
            # Calculate P&L
            premium_change = current_premium - position['entry_premium']
            premium_change_pct = (premium_change / position['entry_premium']) * 100
            pl_amount = premium_change * 100
            
            print(f"\n{position['ticker']} {position['direction']} ${position['strike']:.0f}")
            print(f"  Stock: ${position['entry_price']:.2f} → ${current_price:.2f}")
            print(f"  Premium: ${position['entry_premium']:.2f} → ${current_premium:.2f} ({premium_change_pct:+.1f}%)")
            print(f"  Intrinsic: ${intrinsic:.2f} | Time Value: ${time_value:.2f}")
            print(f"  P&L: ${pl_amount:+.2f}")
            print(f"  Target: ${position['target_premium']:.2f} | Stop: ${position['stop_premium']:.2f}")
            
            # Check exit conditions
            if current_premium >= position['target_premium']:
                print(f"  🎯 TARGET HIT! Closing position...")
                position['status'] = 'CLOSED'
                position['exit_premium'] = current_premium
                position['exit_reason'] = 'TARGET'
                position['profit'] = pl_amount
            elif current_premium <= position['stop_premium']:
                print(f"  🛑 STOP LOSS HIT! Closing position...")
                position['status'] = 'CLOSED'
                position['exit_premium'] = current_premium
                position['exit_reason'] = 'STOP'
                position['profit'] = pl_amount
            else:
                status = "✅ RUNNING"
                to_target = ((position['target_premium'] - current_premium) / position['entry_premium']) * 100
                to_stop = ((current_premium - position['stop_premium']) / position['entry_premium']) * 100
                print(f"  {status} | To Target: +{to_target:.0f}% | To Stop: {to_stop:.0f}%")
        
        print(f"\n{'='*100}")
        
        return True
    
    def should_continue(self):
        """Check if we should continue monitoring"""
        now_et = datetime.now(self.et_tz)
        
        # Check if market closed or exit time reached
        if now_et.hour >= 16:
            print("\n🔴 Market closed - Force closing all positions")
            return False
        
        if now_et.hour >= self.exit_hour_et and now_et.minute >= self.exit_minute_et:
            print(f"\n⏰ Exit time reached ({self.exit_hour_et}:{self.exit_minute_et:02d} PM ET)")
            print("🔴 Force closing all positions")
            return False
        
        # Check if all positions closed
        open_positions = [p for p in self.positions if p['status'] == 'OPEN']
        if not open_positions:
            return False
        
        return True
    
    def display_final_summary(self):
        """Display final trading summary"""
        print(f"\n{'='*100}")
        print("🏁 FINAL TRADING SUMMARY")
        print(f"{'='*100}")
        
        total_pl = 0
        wins = 0
        losses = 0
        
        for position in self.positions:
            if position['status'] == 'CLOSED':
                pl = position.get('profit', 0)
                total_pl += pl
                
                if pl > 0:
                    wins += 1
                    result = "✅ WIN"
                else:
                    losses += 1
                    result = "❌ LOSS"
                
                print(f"\n{position['ticker']} {position['direction']} ${position['strike']:.0f}")
                print(f"  Entry: ${position['entry_premium']:.2f}")
                print(f"  Exit: ${position.get('exit_premium', 0):.2f}")
                print(f"  Reason: {position.get('exit_reason', 'UNKNOWN')}")
                print(f"  P&L: ${pl:+.2f} ({result})")
        
        print(f"\n{'='*100}")
        print(f"Total P&L: ${total_pl:+.2f}")
        print(f"Win Rate: {wins}/{wins+losses} ({wins/(wins+losses)*100:.0f}%)" if (wins+losses) > 0 else "Win Rate: N/A")
        print(f"{'='*100}")
    
    def run(self):
        """Main trading loop"""
        print("\n" + "="*100)
        print("🤖 AUTOMATED 0DTE TRADER - SPY & QQQ PUTS")
        print("="*100)
        print(f"⏰ Exit Time: 3:00 PM CT (4:00 PM ET)")
        print(f"🔄 Monitor Interval: {self.monitor_interval//60} minutes")
        print("="*100)
        
        # Execute trades
        self.execute_trades()
        
        print("\n⏳ Waiting 5 minutes before first monitoring check...")
        print("   (Avoiding instant false triggers)")
        time.sleep(300)  # Wait 5 minutes
        
        # Monitor loop
        while self.should_continue():
            continue_monitoring = self.monitor_positions()
            
            if not continue_monitoring:
                break
            
            print(f"\n⏳ Next check in {self.monitor_interval//60} minutes...")
            time.sleep(self.monitor_interval)
        
        # Force close any remaining open positions
        now_et = datetime.now(self.et_tz)
        for position in self.positions:
            if position['status'] == 'OPEN':
                current_premium, current_price, _, _ = self.calculate_current_premium(position)
                position['status'] = 'CLOSED'
                position['exit_premium'] = current_premium
                position['exit_reason'] = 'TIME_EXIT'
                position['profit'] = (current_premium - position['entry_premium']) * 100
                
                print(f"\n🔴 Force closing {position['ticker']} at ${current_premium:.2f}")
        
        # Final summary
        self.display_final_summary()
        
        print(f"\n{'='*100}")
        print("✅ TRADING SESSION COMPLETE")
        print(f"{'='*100}\n")


# -----------------------------
# MAIN EXECUTION
# -----------------------------

if __name__ == "__main__":
    trader = Auto0DTEMonitor()
    trader.run()
