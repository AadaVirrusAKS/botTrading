#!/usr/bin/env python3
"""
REAL-TIME OPTIONS TRADE MONITOR
Monitors live positions and generates trade alerts
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import os

class RealtimeTradeMonitor:
    def __init__(self):
        """Initialize real-time trade monitor"""
        self.tickers = ['SPY', 'QQQ']
        self.monitoring = True
        self.positions = {}
        
        print("=" * 100)
        print("🔴 REAL-TIME OPTIONS TRADE MONITOR")
        print("=" * 100)
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
        print(f"Monitoring: {', '.join(self.tickers)}")
        print("=" * 100)
    
    def get_live_data(self, ticker):
        """Get live market data"""
        try:
            stock = yf.Ticker(ticker)
            
            # Get current price
            info = stock.info
            current_price = info.get('currentPrice') or info.get('regularMarketPrice')
            
            if not current_price:
                hist = stock.history(period='1d', interval='1m')
                if len(hist) > 0:
                    current_price = hist['Close'].iloc[-1]
            
            # Get intraday data for momentum
            intraday = stock.history(period='1d', interval='5m')
            
            if len(intraday) < 2:
                return None
            
            # Calculate intraday indicators
            last_price = intraday['Close'].iloc[-1]
            prev_price = intraday['Close'].iloc[-2]
            open_price = intraday['Open'].iloc[0]
            high_today = intraday['High'].max()
            low_today = intraday['Low'].min()
            
            # Volume
            current_volume = intraday['Volume'].iloc[-1]
            avg_volume = intraday['Volume'].mean()
            
            # Price changes
            change_from_open = ((last_price - open_price) / open_price) * 100
            change_5min = ((last_price - prev_price) / prev_price) * 100
            
            # Calculate quick RSI
            closes = intraday['Close'].tail(14)
            if len(closes) >= 14:
                delta = closes.diff()
                gain = (delta.where(delta > 0, 0)).mean()
                loss = (-delta.where(delta < 0, 0)).mean()
                rs = gain / loss if loss != 0 else 0
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 50
            
            return {
                'ticker': ticker,
                'price': last_price,
                'open': open_price,
                'high': high_today,
                'low': low_today,
                'change_from_open': change_from_open,
                'change_5min': change_5min,
                'volume_ratio': current_volume / avg_volume if avg_volume > 0 else 1,
                'rsi': rsi,
                'timestamp': datetime.now()
            }
        except Exception as e:
            print(f"Error getting data for {ticker}: {e}")
            return None
    
    def analyze_momentum(self, data):
        """Analyze current momentum and generate signals"""
        if not data:
            return None
        
        signals = []
        
        # RSI signals
        if data['rsi'] < 30:
            signals.append("🟢 RSI OVERSOLD - Bullish bounce potential")
        elif data['rsi'] > 70:
            signals.append("🔴 RSI OVERBOUGHT - Bearish pullback potential")
        elif 40 < data['rsi'] < 50:
            signals.append("🟡 RSI NEUTRAL/BULLISH - Watching for momentum")
        elif 50 < data['rsi'] < 60:
            signals.append("🟡 RSI NEUTRAL/BEARISH - Watching for momentum")
        
        # Price momentum
        if data['change_5min'] > 0.2:
            signals.append("⬆️ STRONG 5-MIN UPWARD MOMENTUM")
        elif data['change_5min'] < -0.2:
            signals.append("⬇️ STRONG 5-MIN DOWNWARD MOMENTUM")
        
        # Daily position
        if data['change_from_open'] > 1.0:
            signals.append("🚀 STRONG DAILY GAINS")
        elif data['change_from_open'] < -1.0:
            signals.append("📉 SIGNIFICANT DAILY LOSSES")
        
        # Volume
        if data['volume_ratio'] > 1.5:
            signals.append("📊 HIGH VOLUME - Strong conviction")
        
        # Price position
        range_size = data['high'] - data['low']
        if range_size > 0:
            position_in_range = (data['price'] - data['low']) / range_size
            if position_in_range > 0.8:
                signals.append("🔝 Near daily HIGH - resistance")
            elif position_in_range < 0.2:
                signals.append("🔻 Near daily LOW - support")
        
        return signals
    
    def generate_trade_setup(self, data):
        """Generate current trade setup recommendation"""
        if not data:
            return None
        
        ticker = data['ticker']
        price = data['price']
        rsi = data['rsi']
        change_5min = data['change_5min']
        change_day = data['change_from_open']
        
        # Determine bias based on multiple factors
        bullish_score = 0
        bearish_score = 0
        
        # RSI scoring
        if rsi < 40:
            bullish_score += 2
        elif rsi > 60:
            bearish_score += 2
        
        # Momentum scoring
        if change_5min > 0.1:
            bullish_score += 1
        elif change_5min < -0.1:
            bearish_score += 1
        
        if change_day > 0.5:
            bullish_score += 1
        elif change_day < -0.5:
            bearish_score += 1
        
        # Volume confirmation
        if data['volume_ratio'] > 1.2:
            if bullish_score > bearish_score:
                bullish_score += 1
            else:
                bearish_score += 1
        
        # Determine direction
        if bullish_score > bearish_score and bullish_score >= 2:
            direction = 'CALL'
            confidence = bullish_score
        elif bearish_score > bullish_score and bearish_score >= 2:
            direction = 'PUT'
            confidence = bearish_score
        else:
            direction = 'NEUTRAL'
            confidence = 0
        
        if direction == 'NEUTRAL':
            return None
        
        # Calculate strike and targets
        volatility = (data['high'] - data['low']) / 2
        
        if direction == 'CALL':
            strike = round(price - volatility * 0.3, 0)
            target_price = price + volatility * 0.8
        else:
            strike = round(price + volatility * 0.3, 0)
            target_price = price - volatility * 0.8
        
        return {
            'ticker': ticker,
            'direction': direction,
            'confidence': confidence,
            'current_price': price,
            'strike': strike,
            'target_price': target_price,
            'rsi': rsi,
            'change_5min': change_5min,
            'change_day': change_day
        }
    
    def display_dashboard(self, ticker_data):
        """Display real-time trading dashboard"""
        os.system('clear')  # Clear screen for fresh display
        
        print("=" * 100)
        print(f"🔴 LIVE TRADING DASHBOARD | {datetime.now().strftime('%I:%M:%S %p')}")
        print("=" * 100)
        
        for ticker, data in ticker_data.items():
            if not data:
                continue
            
            print(f"\n{'─' * 100}")
            print(f"📊 {ticker}")
            print(f"{'─' * 100}")
            print(f"  💰 Price: ${data['price']:.2f} | Open: ${data['open']:.2f} | High: ${data['high']:.2f} | Low: ${data['low']:.2f}")
            print(f"  📈 Change Today: {'+' if data['change_from_open'] >= 0 else ''}{data['change_from_open']:.2f}%")
            print(f"  ⚡ 5-Min Change: {'+' if data['change_5min'] >= 0 else ''}{data['change_5min']:.3f}%")
            print(f"  📊 RSI: {data['rsi']:.1f} | Volume Ratio: {data['volume_ratio']:.2f}x")
            
            # Display signals
            signals = self.analyze_momentum(data)
            if signals:
                print(f"\n  🎯 SIGNALS:")
                for signal in signals:
                    print(f"     {signal}")
            
            # Display trade setup
            setup = self.generate_trade_setup(data)
            if setup:
                print(f"\n  💡 TRADE SETUP:")
                print(f"     Direction: {setup['direction']} Option")
                print(f"     Strike: ${setup['strike']:.0f}")
                print(f"     Target Price: ${setup['target_price']:.2f}")
                print(f"     Confidence: {setup['confidence']}/5")
                print(f"     Entry: Market entry when RSI confirms")
        
        print(f"\n{'=' * 100}")
        print(f"⏰ Next update in 30 seconds... (Ctrl+C to stop)")
        print(f"{'=' * 100}")
    
    def run(self, update_interval=30, duration_minutes=None):
        """Run real-time monitoring"""
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes) if duration_minutes else None
        
        try:
            while self.monitoring:
                # Check time limit
                if end_time and datetime.now() >= end_time:
                    print("\n⏰ Time limit reached")
                    break
                
                # Check market hours
                now = datetime.now()
                is_weekday = now.weekday() < 5
                current_time = now.time()
                market_open = datetime.strptime('09:30', '%H:%M').time()
                market_close = datetime.strptime('16:00', '%H:%M').time()
                
                if not is_weekday or not (market_open <= current_time <= market_close):
                    print(f"\n🔴 MARKET CLOSED - Waiting... ({now.strftime('%I:%M:%S %p')})")
                    time.sleep(60)
                    continue
                
                # Fetch live data for all tickers
                ticker_data = {}
                for ticker in self.tickers:
                    data = self.get_live_data(ticker)
                    if data:
                        ticker_data[ticker] = data
                
                # Display dashboard
                self.display_dashboard(ticker_data)
                
                # Wait for next update
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            print("\n\n⚠️  Monitoring stopped by user")
        finally:
            print(f"\n{'=' * 100}")
            print(f"📊 SESSION SUMMARY")
            print(f"{'=' * 100}")
            print(f"Duration: {(datetime.now() - start_time).total_seconds() / 60:.1f} minutes")
            print(f"Ended: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
            print(f"{'=' * 100}")


if __name__ == "__main__":
    import sys
    
    # Parse command line arguments
    duration = None
    interval = 30
    
    for arg in sys.argv[1:]:
        if arg.startswith('--duration='):
            duration = int(arg.split('=')[1])
        elif arg.startswith('--interval='):
            interval = int(arg.split('=')[1])
    
    monitor = RealtimeTradeMonitor()
    
    print("\n🔴 Starting real-time monitoring...")
    print(f"Update interval: {interval} seconds")
    if duration:
        print(f"Duration: {duration} minutes")
    else:
        print("Duration: Unlimited (Ctrl+C to stop)")
    print("\nPress Ctrl+C to stop monitoring\n")
    
    time.sleep(2)
    
    monitor.run(update_interval=interval, duration_minutes=duration)
