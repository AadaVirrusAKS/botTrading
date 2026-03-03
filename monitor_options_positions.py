import yfinance as yf
import pandas as pd
import pickle
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Load the trader instance from the main script
# This script is meant to be run separately to monitor existing positions

print("=" * 100)
print("📊 OPTIONS POSITION MONITOR")
print("=" * 100)

# For demonstration, let's create a simple monitoring function
def monitor_spy_qqq_positions():
    """Monitor SPY and QQQ options positions"""
    
    # Check market hours - US Market: 9:30 AM - 4:00 PM ET
    current_time = datetime.now()
    current_hour = current_time.hour
    current_minute = current_time.minute
    
    # Market status
    if current_hour < 9 or (current_hour == 9 and current_minute < 30):
        print("⚠️  Market hasn't opened yet (Opens at 9:30 AM ET)")
    elif current_hour >= 16:
        print("🔴 Market is CLOSED (Closed at 4:00 PM ET)")
        print("⚠️  All 0DTE options have EXPIRED worthless if not closed!")
        return
    elif current_hour == 15 and current_minute >= 45:
        print("🔴 URGENT: Market closing in 15 minutes - CLOSE ALL POSITIONS NOW!")
    elif current_hour >= 15:
        print(f"⚠️  Market closing soon - Current time: {current_time.strftime('%I:%M %p')} ET")
    else:
        print(f"✅ Market is OPEN - Current time: {current_time.strftime('%I:%M %p')} ET")
    
    tickers = ['SPY', 'QQQ']
    
    for ticker in tickers:
        print(f"\n📈 Analyzing {ticker}...")
        
        try:
            stock = yf.Ticker(ticker)
            
            # Get live price
            info = stock.info
            live_price = None
            if 'currentPrice' in info and info['currentPrice']:
                live_price = info['currentPrice']
            elif 'regularMarketPrice' in info and info['regularMarketPrice']:
                live_price = info['regularMarketPrice']
            
            if live_price is None:
                intraday = stock.history(period='1d', interval='1m')
                if len(intraday) > 0:
                    live_price = intraday['Close'].iloc[-1]
            
            if live_price:
                print(f"   💹 Current Price: ${live_price:.2f}")
                
                # Get recent data for context
                data = stock.history(period='5d', interval='1h')
                if len(data) > 0:
                    recent_high = data['High'].tail(20).max()
                    recent_low = data['Low'].tail(20).min()
                    current_momentum = ((live_price - data['Close'].iloc[-5]) / data['Close'].iloc[-5] * 100) if len(data) >= 5 else 0
                    
                    print(f"   📊 5-Hour Range: ${recent_low:.2f} - ${recent_high:.2f}")
                    print(f"   🚀 5-Period Momentum: {current_momentum:+.2f}%")
                    
                    # Suggest actions
                    if ticker == 'SPY':
                        # For SPY PUT at $686
                        put_strike = 686
                        if live_price <= put_strike:
                            itm_amount = put_strike - live_price
                            print(f"   ✅ PUT ${put_strike} is ITM by ${itm_amount:.2f} - Consider taking profit!")
                        elif live_price > put_strike + 3:
                            print(f"   ⚠️  PUT ${put_strike} is OTM by ${live_price - put_strike:.2f} - Monitor for stop loss")
                        else:
                            print(f"   ⏳ PUT ${put_strike} near ATM - Hold and monitor")
                    
                    elif ticker == 'QQQ':
                        # For QQQ PUT at $622
                        put_strike = 622
                        if live_price <= put_strike:
                            itm_amount = put_strike - live_price
                            print(f"   ✅ PUT ${put_strike} is ITM by ${itm_amount:.2f} - Consider taking profit!")
                        elif live_price > put_strike + 3:
                            print(f"   ⚠️  PUT ${put_strike} is OTM by ${live_price - put_strike:.2f} - Monitor for stop loss")
                        else:
                            print(f"   ⏳ PUT ${put_strike} near ATM - Hold and monitor")
            
        except Exception as e:
            print(f"   ❌ Error: {str(e)}")

# Run monitoring
monitor_spy_qqq_positions()

print("\n" + "=" * 100)
print("💡 TRADING TIPS FOR 0DTE OPTIONS:")
print("=" * 100)
print("1. Monitor positions every 5-10 minutes")
print("2. Take profits quickly when target is hit (even partial targets like 5:1)")
print("3. Cut losses fast - 0DTE options can go to zero quickly")
print("4. Best times to trade: 10:00-11:30 AM and 2:00-3:30 PM ET")
print("5. Avoid holding through lunch (11:30 AM - 1:00 PM) due to low volume")
print("6. ⚠️  CRITICAL: Close ALL positions by 3:45 PM - Options expire at 4:00 PM!")
print("7. ⚠️  After 4:00 PM: All 0DTE options expire worthless if OTM")
print("=" * 100)
print("\n🕐 INTRADAY TRADING SCHEDULE:")
print("   • Market Open: 9:30 AM ET")
print("   • Best Entry: 10:00 AM - 11:30 AM")
print("   • Lunch Lull: 11:30 AM - 1:00 PM (low volume)")
print("   • Afternoon Session: 1:00 PM - 3:00 PM")
print("   • Final Warning: 3:00 PM (start closing positions)")
print("   • FORCE CLOSE: 3:45 PM (MANDATORY for 0DTE)")
print("   • Market Close: 4:00 PM ET (options expire)")
print("=" * 100)
