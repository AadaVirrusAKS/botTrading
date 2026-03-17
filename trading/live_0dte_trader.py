import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import pytz
import warnings
warnings.filterwarnings('ignore')

# -----------------------------
# LIVE 0DTE OPTIONS TRADER WITH MONITORING
# Executes trades and monitors until 2:50 PM CT (3:50 PM ET)
# Target: 1:3 Risk/Reward Ratio
# -----------------------------

class Live0DTETrader:
    def __init__(self, spy_trade, qqq_trade):
        """Initialize with trade setups"""
        self.spy_trade = spy_trade
        self.qqq_trade = qqq_trade
        self.positions = []
        self.closed_positions = []
        self.exit_time_ct = "2:50 PM"
        self.exit_time_et = "3:50 PM"
        
    def execute_trade(self, trade_setup):
        """Execute a trade based on setup"""
        ticker = trade_setup['ticker']
        direction = trade_setup['direction']
        strike = trade_setup['strike']
        entry_premium = trade_setup['entry_premium']
        target_premium = trade_setup['target_premium']
        stop_premium = trade_setup['stop_premium']
        
        position = {
            'ticker': ticker,
            'direction': direction,
            'strike': strike,
            'entry_premium': entry_premium,
            'target_premium': target_premium,
            'stop_premium': stop_premium,
            'entry_price': trade_setup['current_price'],
            'entry_time': datetime.now(pytz.timezone('US/Eastern')),
            'contracts': 1,
            'status': 'OPEN',
            'cost': entry_premium * 100
        }
        
        self.positions.append(position)
        
        print(f"\n✅ TRADE EXECUTED: {ticker} {direction} ${strike}")
        print(f"   Entry Premium: ${entry_premium:.2f} (Cost: ${entry_premium * 100:.2f})")
        print(f"   Target: ${target_premium:.2f}")
        print(f"   Stop Loss: ${stop_premium:.2f}")
        
        return position
    
    def get_current_premium(self, position):
        """Estimate current premium based on live price"""
        ticker = position['ticker']
        direction = position['direction']
        strike = position['strike']
        entry_time = position['entry_time']
        
        # Get current price
        stock = yf.Ticker(ticker)
        current_price = stock.info.get('currentPrice') or stock.history(period='1d', interval='1m')['Close'].iloc[-1]
        
        # Calculate intrinsic value
        if direction == 'CALL':
            intrinsic = max(0, current_price - strike)
        else:  # PUT
            intrinsic = max(0, strike - current_price)
        
        # Calculate time decay (0DTE decays fast)
        now = datetime.now(pytz.timezone('US/Eastern'))
        hours_held = (now - entry_time).seconds / 3600
        market_hours_left = 16 - now.hour - (now.minute / 60)
        time_decay_factor = max(0, market_hours_left / 6.5)  # 6.5 market hours
        
        # Estimate time value (decreases as expiry approaches)
        entry_time_value = position['entry_premium'] - max(0, position['entry_price'] - strike if direction == 'CALL' else strike - position['entry_price'])
        current_time_value = entry_time_value * time_decay_factor
        
        # Current premium = intrinsic + remaining time value
        current_premium = intrinsic + current_time_value
        current_premium = max(current_premium, intrinsic * 1.05)  # At least 5% above intrinsic
        
        return current_premium, current_price
    
    def monitor_positions(self):
        """Monitor all open positions"""
        et_tz = pytz.timezone('US/Eastern')
        now = datetime.now(et_tz)
        
        print(f"\n{'='*90}")
        print(f"📊 POSITION MONITOR - {now.strftime('%I:%M:%S %p ET')}")
        print(f"{'='*90}")
        
        for position in self.positions:
            if position['status'] != 'OPEN':
                continue
            
            current_premium, current_price = self.get_current_premium(position)
            
            # Calculate P&L
            pl = (current_premium - position['entry_premium']) * 100
            pl_pct = (pl / position['cost']) * 100
            
            # Time held
            time_held = (now - position['entry_time']).seconds / 60  # minutes
            
            print(f"\n{position['ticker']} {position['direction']} ${position['strike']}")
            print(f"  Stock Price: ${position['entry_price']:.2f} → ${current_price:.2f} ({((current_price/position['entry_price']-1)*100):+.2f}%)")
            print(f"  Premium: ${position['entry_premium']:.2f} → ${current_premium:.2f}")
            print(f"  P&L: ${pl:+.2f} ({pl_pct:+.1f}%)")
            print(f"  Target: ${position['target_premium']:.2f} | Stop: ${position['stop_premium']:.2f}")
            print(f"  Time Held: {time_held:.0f} minutes")
            
            # Check exit conditions
            if current_premium >= position['target_premium']:
                print(f"  🎯 TARGET HIT! Closing position...")
                self.close_position(position, current_premium, 'TARGET')
            elif current_premium <= position['stop_premium']:
                print(f"  🛑 STOP LOSS HIT! Closing position...")
                self.close_position(position, current_premium, 'STOP')
            else:
                to_target = ((position['target_premium'] / current_premium - 1) * 100)
                to_stop = ((current_premium / position['stop_premium'] - 1) * 100)
                print(f"  📊 To Target: {to_target:+.0f}% | To Stop: {to_stop:+.0f}%")
    
    def close_position(self, position, exit_premium, reason):
        """Close a position"""
        position['status'] = 'CLOSED'
        position['exit_premium'] = exit_premium
        position['exit_time'] = datetime.now(pytz.timezone('US/Eastern'))
        position['exit_reason'] = reason
        
        pl = (exit_premium - position['entry_premium']) * 100
        pl_pct = (pl / position['cost']) * 100
        
        position['pl'] = pl
        position['pl_pct'] = pl_pct
        
        self.closed_positions.append(position)
        
        print(f"\n  ✅ CLOSED - {reason}")
        print(f"  Final P&L: ${pl:+.2f} ({pl_pct:+.1f}%)")
    
    def force_close_all(self):
        """Force close all open positions at 2:50 PM CT"""
        print(f"\n{'='*90}")
        print(f"⏰ 2:50 PM CT REACHED - FORCE CLOSING ALL POSITIONS")
        print(f"{'='*90}")
        
        for position in self.positions:
            if position['status'] == 'OPEN':
                current_premium, _ = self.get_current_premium(position)
                self.close_position(position, current_premium, 'TIME_EXIT')
    
    def display_summary(self):
        """Display trading summary"""
        et_tz = pytz.timezone('US/Eastern')
        now = datetime.now(et_tz)
        
        print(f"\n{'='*90}")
        print(f"📊 TRADING SESSION SUMMARY - {now.strftime('%I:%M %p ET')}")
        print(f"{'='*90}")
        
        total_pl = sum([p.get('pl', 0) for p in self.closed_positions])
        total_cost = sum([p['cost'] for p in self.positions])
        
        print(f"\nTotal Positions: {len(self.positions)}")
        print(f"Closed: {len(self.closed_positions)} | Open: {len([p for p in self.positions if p['status'] == 'OPEN'])}")
        print(f"Total Investment: ${total_cost:.2f}")
        print(f"Total P&L: ${total_pl:+.2f} ({(total_pl/total_cost*100):+.1f}%)")
        
        if self.closed_positions:
            print(f"\n{'='*90}")
            print("CLOSED POSITIONS:")
            for p in self.closed_positions:
                print(f"\n{p['ticker']} {p['direction']} ${p['strike']}")
                print(f"  Entry: ${p['entry_premium']:.2f} → Exit: ${p['exit_premium']:.2f}")
                print(f"  P&L: ${p['pl']:+.2f} ({p['pl_pct']:+.1f}%)")
                print(f"  Reason: {p['exit_reason']}")
        
        print(f"\n{'='*90}")
    
    def should_exit(self):
        """Check if we should exit (2:50 PM CT = 3:50 PM ET)"""
        et_tz = pytz.timezone('US/Eastern')
        now = datetime.now(et_tz)
        exit_hour = 15  # 3 PM
        exit_minute = 50
        
        if now.hour > exit_hour or (now.hour == exit_hour and now.minute >= exit_minute):
            return True
        return False
    
    def run(self):
        """Main trading loop"""
        et_tz = pytz.timezone('US/Eastern')
        
        print(f"\n{'='*90}")
        print(f"🚀 LIVE 0DTE OPTIONS TRADING SESSION")
        print(f"{'='*90}")
        print(f"Start Time: {datetime.now(et_tz).strftime('%I:%M %p ET')}")
        print(f"Exit Time: {self.exit_time_et} ({self.exit_time_ct} CT)")
        print(f"Risk/Reward: 1:3")
        print(f"{'='*90}")
        
        # Execute initial trades
        print(f"\n📈 EXECUTING TRADES...")
        self.execute_trade(self.spy_trade)
        self.execute_trade(self.qqq_trade)
        
        print(f"\n✅ All trades executed! Starting monitoring...")
        print(f"⏰ Will monitor every 2 minutes until {self.exit_time_et}")
        
        # Monitor loop
        monitor_interval = 120  # 2 minutes
        
        while True:
            # Check if we should exit
            if self.should_exit():
                self.force_close_all()
                break
            
            # Monitor positions
            self.monitor_positions()
            
            # Check if all positions closed
            open_count = len([p for p in self.positions if p['status'] == 'OPEN'])
            if open_count == 0:
                print(f"\n✅ All positions closed!")
                break
            
            # Wait before next check
            et_now = datetime.now(et_tz)
            minutes_to_exit = (15 * 60 + 50) - (et_now.hour * 60 + et_now.minute)
            
            if minutes_to_exit <= 5:
                print(f"\n⚠️  Only {minutes_to_exit} minutes until forced exit!")
            
            print(f"\n⏳ Next check in {monitor_interval//60} minutes...")
            time.sleep(monitor_interval)
        
        # Final summary
        self.display_summary()
        
        print(f"\n{'='*90}")
        print(f"🏁 TRADING SESSION COMPLETE")
        print(f"{'='*90}")


# -----------------------------
# MAIN EXECUTION
# -----------------------------

if __name__ == "__main__":
    # Get current market data
    spy = yf.Ticker('SPY')
    qqq = yf.Ticker('QQQ')
    
    spy_price = spy.info.get('currentPrice') or spy.history(period='1d', interval='1m')['Close'].iloc[-1]
    qqq_price = qqq.info.get('currentPrice') or qqq.history(period='1d', interval='1m')['Close'].iloc[-1]
    
    # Get ATR for premium calculation
    spy_daily = spy.history(period='1mo', interval='1d')
    qqq_daily = qqq.history(period='1mo', interval='1d')
    
    spy_daily['TR'] = spy_daily[['High', 'Low']].apply(lambda x: x['High'] - x['Low'], axis=1)
    qqq_daily['TR'] = qqq_daily[['High', 'Low']].apply(lambda x: x['High'] - x['Low'], axis=1)
    
    spy_atr = spy_daily['TR'].rolling(14).mean().iloc[-1]
    qqq_atr = qqq_daily['TR'].rolling(14).mean().iloc[-1]
    
    # SPY PUT Setup (based on bearish momentum)
    spy_strike = round(spy_price + (spy_atr * 0.1), 0)
    spy_intrinsic = max(0, spy_strike - spy_price)
    spy_time_value = spy_atr * 0.25
    spy_premium = spy_intrinsic + spy_time_value
    spy_stop = spy_premium * 0.5
    spy_target = spy_premium + ((spy_premium - spy_stop) * 3)
    
    spy_trade = {
        'ticker': 'SPY',
        'direction': 'PUT',
        'strike': spy_strike,
        'current_price': spy_price,
        'entry_premium': spy_premium,
        'target_premium': spy_target,
        'stop_premium': spy_stop
    }
    
    # QQQ PUT Setup
    qqq_strike = round(qqq_price + (qqq_atr * 0.1), 0)
    qqq_intrinsic = max(0, qqq_strike - qqq_price)
    qqq_time_value = qqq_atr * 0.25
    qqq_premium = qqq_intrinsic + qqq_time_value
    qqq_stop = qqq_premium * 0.5
    qqq_target = qqq_premium + ((qqq_premium - qqq_stop) * 3)
    
    qqq_trade = {
        'ticker': 'QQQ',
        'direction': 'PUT',
        'strike': qqq_strike,
        'current_price': qqq_price,
        'entry_premium': qqq_premium,
        'target_premium': qqq_target,
        'stop_premium': qqq_stop
    }
    
    # Display trade plan
    print(f"\n{'='*90}")
    print(f"📋 TRADE PLAN CONFIRMATION")
    print(f"{'='*90}")
    
    print(f"\n1️⃣  SPY PUT ${spy_strike}")
    print(f"    Current: ${spy_price:.2f}")
    print(f"    Entry: ${spy_premium:.2f} (${spy_premium*100:.2f} per contract)")
    print(f"    Target: ${spy_target:.2f} (+150%)")
    print(f"    Stop: ${spy_stop:.2f} (-50%)")
    
    print(f"\n2️⃣  QQQ PUT ${qqq_strike}")
    print(f"    Current: ${qqq_price:.2f}")
    print(f"    Entry: ${qqq_premium:.2f} (${qqq_premium*100:.2f} per contract)")
    print(f"    Target: ${qqq_target:.2f} (+150%)")
    print(f"    Stop: ${qqq_stop:.2f} (-50%)")
    
    total_cost = (spy_premium + qqq_premium) * 100
    total_profit_potential = ((spy_target - spy_premium) + (qqq_target - qqq_premium)) * 100
    total_loss_risk = ((spy_premium - spy_stop) + (qqq_premium - qqq_stop)) * 100
    
    print(f"\n💼 Total Investment: ${total_cost:.2f}")
    print(f"💰 Profit Potential: +${total_profit_potential:.2f}")
    print(f"⚠️  Loss Risk: -${total_loss_risk:.2f}")
    print(f"⚖️  Risk/Reward: 1:{total_profit_potential/total_loss_risk:.1f}")
    
    print(f"\n{'='*90}")
    
    # Confirm execution
    response = input("\n🚀 Ready to execute trades and start monitoring? (yes/no): ").strip().lower()
    
    if response == 'yes' or response == 'y':
        trader = Live0DTETrader(spy_trade, qqq_trade)
        trader.run()
    else:
        print("\n❌ Trading cancelled by user")
