#!/usr/bin/env python3
"""
TRIPLE CONFIRMATION INTRADAY SCANNER
Identifies high-probability INTRADAY setups using SuperTrend + VWAP + MACD on 5-15 minute timeframes
Designed for same-day trades with quick entries and exits
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from config.master_stock_list import get_intraday_core_list
from config import PROJECT_ROOT, DATA_DIR

# Use centralized caching layer to avoid Yahoo rate limiting
try:
    from services.market_data import (
        cached_get_history, prewarm_history_cache, _is_globally_rate_limited
    )
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False


class TripleConfirmationIntraday:
    """Intraday scanner using 5-15 minute timeframes for all indicators"""
    
    def __init__(self):
        self.universe = get_intraday_core_list()
        
        self.results = []
        self.output_file = os.path.join(DATA_DIR, 'triple_confirmation_intraday.json')
    
    def calculate_supertrend(self, df, period=7, multiplier=2):
        """Calculate SuperTrend indicator - optimized for intraday"""
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        df['ATR'] = df['TR'].rolling(window=period).mean()
        
        df['UpperBand'] = ((df['High'] + df['Low']) / 2) + (multiplier * df['ATR'])
        df['LowerBand'] = ((df['High'] + df['Low']) / 2) - (multiplier * df['ATR'])
        
        df['SuperTrend'] = 0.0
        df['Direction'] = 1
        
        for i in range(period, len(df)):
            close = df['Close'].iloc[i]
            prev_supertrend = df['SuperTrend'].iloc[i-1] if i > period else df['LowerBand'].iloc[i]
            
            if close > prev_supertrend:
                df.loc[df.index[i], 'Direction'] = 1
                df.loc[df.index[i], 'SuperTrend'] = df['LowerBand'].iloc[i]
            else:
                df.loc[df.index[i], 'Direction'] = -1
                df.loc[df.index[i], 'SuperTrend'] = df['UpperBand'].iloc[i]
        
        return df
    
    def calculate_vwap(self, df):
        """Calculate VWAP from start of day"""
        # Reset cumsum at start of each day
        df['Date'] = df.index.date
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['TP_Volume'] = df['Typical_Price'] * df['Volume']
        
        # Group by date and calculate cumulative sums
        df['Cum_TP_Volume'] = df.groupby('Date')['TP_Volume'].cumsum()
        df['Cum_Volume'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TP_Volume'] / df['Cum_Volume']
        
        return df
    
    def calculate_macd(self, df, fast=9, slow=21, signal=9):
        """Calculate MACD - faster settings for intraday"""
        df['EMA_Fast'] = df['Close'].ewm(span=fast, adjust=False).mean()
        df['EMA_Slow'] = df['Close'].ewm(span=slow, adjust=False).mean()
        df['MACD'] = df['EMA_Fast'] - df['EMA_Slow']
        df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['MACD_Signal']
        return df
    
    def analyze_stock(self, ticker):
        """Analyze stock for intraday triple confirmation"""
        try:
            if _USE_CACHED:
                # Skip if globally rate-limited
                if _is_globally_rate_limited():
                    return None
                # Use cached history to avoid rate limiting
                data_5m = cached_get_history(ticker, period='5d', interval='5m')
            else:
                stock = yf.Ticker(ticker)
                data_5m = stock.history(period='5d', interval='5m')
            
            if data_5m is None or data_5m.empty or len(data_5m) < 50:
                return None
            
            # Calculate all indicators on 5-minute timeframe
            data_5m = self.calculate_supertrend(data_5m.copy(), period=7, multiplier=2)
            data_5m = self.calculate_vwap(data_5m.copy())
            data_5m = self.calculate_macd(data_5m.copy(), fast=9, slow=21, signal=9)
            
            # Filter to today's data only
            today = datetime.now().date()
            today_data = data_5m[data_5m.index.date == today]
            
            if today_data.empty:
                # Use most recent session
                latest_date = data_5m.index.date[-1]
                today_data = data_5m[data_5m.index.date == latest_date]
            
            if today_data.empty or len(today_data) < 10:
                return None
            
            # Get current values (most recent bar)
            current_price = today_data['Close'].iloc[-1]
            supertrend = today_data['SuperTrend'].iloc[-1]
            supertrend_direction = today_data['Direction'].iloc[-1]
            
            macd = today_data['MACD'].iloc[-1]
            macd_signal = today_data['MACD_Signal'].iloc[-1]
            macd_hist = today_data['MACD_Histogram'].iloc[-1]
            
            vwap = today_data['VWAP'].iloc[-1]
            
            # Determine signals
            st_bullish = supertrend_direction == 1
            st_bearish = supertrend_direction == -1
            
            vwap_bullish = current_price > vwap
            vwap_bearish = current_price < vwap
            
            macd_bullish = macd > macd_signal and macd_hist > 0
            macd_bearish = macd < macd_signal and macd_hist < 0
            
            # Check for triple confirmation
            bullish_confirmation = st_bullish and vwap_bullish and macd_bullish
            bearish_confirmation = st_bearish and vwap_bearish and macd_bearish
            
            if not (bullish_confirmation or bearish_confirmation):
                return None
            
            # Calculate metrics
            distance_from_vwap = ((current_price - vwap) / vwap) * 100
            distance_from_st = ((current_price - supertrend) / supertrend) * 100
            
            # Volume analysis (current 5-min bar vs average)
            avg_volume_5m = today_data['Volume'].mean()
            current_volume = today_data['Volume'].iloc[-1]
            volume_ratio = current_volume / avg_volume_5m if avg_volume_5m > 0 else 1
            
            # Score the setup (0-15 scale)
            score = 0
            signals = []
            
            # Base confirmation (6 points)
            score += 6
            signals.append("✅ Intraday Triple Confirmation")
            
            # MACD strength (3 points) - adjusted for intraday
            macd_strength = abs(macd_hist)
            if macd_strength > 0.3:
                score += 3
                signals.append("🔥 Strong Intraday Momentum")
            elif macd_strength > 0.1:
                score += 2
                signals.append("📊 Good Momentum")
            else:
                score += 1
            
            # Distance from VWAP (3 points) - tighter for intraday
            abs_vwap_dist = abs(distance_from_vwap)
            if abs_vwap_dist < 0.3:
                score += 3
                signals.append("🎯 Very Tight to VWAP")
            elif abs_vwap_dist < 0.8:
                score += 2
                signals.append("✓ Near VWAP")
            else:
                score += 1
            
            # Volume (3 points) - critical for intraday
            if volume_ratio > 2.0:
                score += 3
                signals.append(f"📈 Surge Volume ({volume_ratio:.1f}x)")
            elif volume_ratio > 1.5:
                score += 2
                signals.append(f"📊 High Volume ({volume_ratio:.1f}x)")
            else:
                score += 1
            
            direction = "BULLISH" if bullish_confirmation else "BEARISH"
            
            # Calculate ATR for targets (intraday ATR)
            atr = today_data['ATR'].iloc[-1]
            
            # Intraday targets - tighter than swing trading
            if bullish_confirmation:
                stop_loss = supertrend
                target_1 = current_price + (atr * 1.0)  # 1:1 ATR
                target_2 = current_price + (atr * 1.5)  # 1:1.5 ATR
                target_3 = current_price + (atr * 2.0)  # 1:2 ATR
            else:
                stop_loss = supertrend
                target_1 = current_price - (atr * 1.0)
                target_2 = current_price - (atr * 1.5)
                target_3 = current_price - (atr * 2.0)
            
            # Get current time
            latest_time = today_data.index[-1].strftime('%H:%M')
            
            return {
                'ticker': ticker,
                'score': score,
                'direction': direction,
                'current_price': current_price,
                'vwap': vwap,
                'distance_from_vwap': distance_from_vwap,
                'supertrend': supertrend,
                'distance_from_st': distance_from_st,
                'macd': macd,
                'macd_signal': macd_signal,
                'macd_histogram': macd_hist,
                'volume_ratio': volume_ratio,
                'atr': atr,
                'stop_loss': stop_loss,
                'target_1': target_1,
                'target_2': target_2,
                'target_3': target_3,
                'signals': signals,
                'latest_time': latest_time,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"Error analyzing {ticker}: {e}")
            return None
    
    def scan_market(self):
        """Scan all stocks for intraday triple confirmation"""
        print("\n" + "=" * 100)
        print("⚡ INTRADAY TRIPLE CONFIRMATION SCANNER - 5-Minute Timeframe")
        print("=" * 100)
        print(f"📊 Scanning {len(self.universe)} highly liquid stocks...")
        print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⚙️ Settings: SuperTrend(7,2) + VWAP + MACD(9,21,9)\n")
        
        self.results = []
        
        # Pre-warm cache with batch download to avoid per-ticker rate limiting
        if _USE_CACHED:
            print(f"  📦 Pre-warming cache for {len(self.universe)} symbols...")
            prewarm_history_cache(self.universe, period='5d', interval='5m')

        # Throttled parallel processing (3 workers to respect rate limits)
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_ticker = {executor.submit(self.analyze_stock, ticker): ticker 
                               for ticker in self.universe}
            
            for future in as_completed(future_to_ticker):
                result = future.result()
                if result:
                    self.results.append(result)
                    print(f"✅ {result['ticker']}: {result['direction']} @ {result['latest_time']} - Score: {result['score']}/15")
        
        self.results.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\n{'=' * 100}")
        print(f"✨ Found {len(self.results)} intraday setups with triple confirmation")
        print(f"{'=' * 100}\n")
        
        self.display_results()
        self.save_results()
        
        return self.results
    
    def display_results(self):
        """Display formatted intraday results"""
        if not self.results:
            print("⚠️ No intraday triple confirmation setups found.\n")
            print("💡 TIP: Best scanning times are 10:00-11:30 AM and 2:00-3:30 PM ET\n")
            return
        
        bullish = [r for r in self.results if r['direction'] == 'BULLISH']
        bearish = [r for r in self.results if r['direction'] == 'BEARISH']
        
        if bullish:
            print("\n" + "🟢" * 50)
            print(f"📈 BULLISH INTRADAY SETUPS ({len(bullish)} found)")
            print("🟢" * 50 + "\n")
            
            for i, r in enumerate(bullish[:10], 1):
                print(f"#{i} {r['ticker']} @ {r['latest_time']} - Score: {r['score']}/15")
                print(f"   💰 Entry: ${r['current_price']:.2f}")
                print(f"   📊 VWAP: ${r['vwap']:.2f} ({r['distance_from_vwap']:+.2f}%)")
                print(f"   🔄 SuperTrend: ${r['supertrend']:.2f}")
                print(f"   📈 MACD Hist: {r['macd_histogram']:+.3f}")
                print(f"   📊 Volume: {r['volume_ratio']:.1f}x average")
                print(f"   🎯 Intraday Targets: ${r['target_1']:.2f} → ${r['target_2']:.2f} → ${r['target_3']:.2f}")
                print(f"   🛑 Stop: ${r['stop_loss']:.2f} (Risk: ${abs(r['current_price']-r['stop_loss']):.2f})")
                print(f"   ✨ {', '.join(r['signals'])}")
                print()
        
        if bearish:
            print("\n" + "🔴" * 50)
            print(f"📉 BEARISH INTRADAY SETUPS ({len(bearish)} found)")
            print("🔴" * 50 + "\n")
            
            for i, r in enumerate(bearish[:10], 1):
                print(f"#{i} {r['ticker']} @ {r['latest_time']} - Score: {r['score']}/15")
                print(f"   💰 Entry: ${r['current_price']:.2f}")
                print(f"   📊 VWAP: ${r['vwap']:.2f} ({r['distance_from_vwap']:+.2f}%)")
                print(f"   🔄 SuperTrend: ${r['supertrend']:.2f}")
                print(f"   📉 MACD Hist: {r['macd_histogram']:+.3f}")
                print(f"   📊 Volume: {r['volume_ratio']:.1f}x average")
                print(f"   🎯 Intraday Targets: ${r['target_1']:.2f} → ${r['target_2']:.2f} → ${r['target_3']:.2f}")
                print(f"   🛑 Stop: ${r['stop_loss']:.2f} (Risk: ${abs(r['current_price']-r['stop_loss']):.2f})")
                print(f"   ✨ {', '.join(r['signals'])}")
                print()
        
        print("\n⏰ INTRADAY TRADING RULES:")
        print("   • Enter within 5-10 minutes of scan")
        print("   • Close ALL positions by 4:00 PM ET")
        print("   • Use tight stops - SuperTrend level")
        print("   • Take profits quickly at T1/T2")
        print("   • Re-scan every 1-2 hours\n")
    
    def save_results(self):
        """Save intraday results to JSON"""
        try:
            with open(self.output_file, 'w') as f:
                json.dump({
                    'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'timeframe': '5-minute',
                    'type': 'INTRADAY',
                    'total_scanned': len(self.universe),
                    'setups_found': len(self.results),
                    'results': self.results
                }, f, indent=2)
            
            print(f"💾 Intraday results saved to {self.output_file}\n")
        except Exception as e:
            print(f"⚠️ Error saving results: {e}\n")


def main():
    """Main execution"""
    scanner = TripleConfirmationIntraday()
    scanner.scan_market()


if __name__ == "__main__":
    main()
