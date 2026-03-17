#!/usr/bin/env python3
"""
TRIPLE CONFIRMATION SCANNER
Identifies high-probability setups using SuperTrend + VWAP + MACD alignment
Only shows stocks where all 3 indicators confirm the same direction
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
from config.master_stock_list import get_master_stock_list
from config import PROJECT_ROOT, DATA_DIR


class TripleConfirmationScanner:
    """Scanner for stocks with SuperTrend + VWAP + MACD alignment"""
    
    def __init__(self):
        self.universe = get_master_stock_list(include_etfs=True)
        
        self.results = []
        self.output_file = os.path.join(DATA_DIR, 'triple_confirmation_picks.json')
    
    def calculate_supertrend(self, df, period=10, multiplier=3):
        """Calculate SuperTrend indicator"""
        # Calculate ATR
        df['H-L'] = df['High'] - df['Low']
        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
        df['ATR'] = df['TR'].rolling(window=period).mean()
        
        # Calculate basic bands
        df['UpperBand'] = ((df['High'] + df['Low']) / 2) + (multiplier * df['ATR'])
        df['LowerBand'] = ((df['High'] + df['Low']) / 2) - (multiplier * df['ATR'])
        
        # Initialize SuperTrend
        df['SuperTrend'] = 0.0
        df['Direction'] = 1  # 1 = bullish, -1 = bearish
        
        for i in range(period, len(df)):
            # Current close
            close = df['Close'].iloc[i]
            
            # Previous SuperTrend
            prev_supertrend = df['SuperTrend'].iloc[i-1] if i > period else df['LowerBand'].iloc[i]
            prev_direction = df['Direction'].iloc[i-1] if i > period else 1
            
            # Determine direction
            if close > prev_supertrend:
                df.loc[df.index[i], 'Direction'] = 1
                df.loc[df.index[i], 'SuperTrend'] = df['LowerBand'].iloc[i]
            else:
                df.loc[df.index[i], 'Direction'] = -1
                df.loc[df.index[i], 'SuperTrend'] = df['UpperBand'].iloc[i]
        
        return df
    
    def calculate_vwap(self, df):
        """Calculate Volume Weighted Average Price"""
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['TP_Volume'] = df['Typical_Price'] * df['Volume']
        df['Cum_TP_Volume'] = df['TP_Volume'].cumsum()
        df['Cum_Volume'] = df['Volume'].cumsum()
        df['VWAP'] = df['Cum_TP_Volume'] / df['Cum_Volume']
        return df
    
    def calculate_macd(self, df, fast=12, slow=26, signal=9):
        """Calculate MACD indicator"""
        df['EMA_Fast'] = df['Close'].ewm(span=fast, adjust=False).mean()
        df['EMA_Slow'] = df['Close'].ewm(span=slow, adjust=False).mean()
        df['MACD'] = df['EMA_Fast'] - df['EMA_Slow']
        df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['MACD_Signal']
        return df
    
    def analyze_stock(self, ticker):
        """Analyze a single stock for triple confirmation"""
        try:
            # Fetch data - need intraday for VWAP
            stock = yf.Ticker(ticker)
            
            # Get 5 days of 5-minute data for intraday VWAP
            intraday = stock.history(period='5d', interval='5m')
            
            if intraday.empty or len(intraday) < 50:
                return None
            
            # Get daily data for longer-term indicators
            daily = stock.history(period='3mo')
            
            if daily.empty or len(daily) < 50:
                return None
            
            # Calculate indicators on daily timeframe
            daily = self.calculate_supertrend(daily.copy())
            daily = self.calculate_macd(daily.copy())
            
            # Calculate VWAP on intraday (today's session)
            today = datetime.now().date()
            today_data = intraday[intraday.index.date == today]
            
            if today_data.empty:
                # Use yesterday's data if today is not available
                yesterday = today - timedelta(days=1)
                today_data = intraday[intraday.index.date == yesterday]
            
            if today_data.empty:
                return None
            
            today_data = self.calculate_vwap(today_data.copy())
            
            # Get current values
            current_price = daily['Close'].iloc[-1]
            supertrend = daily['SuperTrend'].iloc[-1]
            supertrend_direction = daily['Direction'].iloc[-1]
            
            macd = daily['MACD'].iloc[-1]
            macd_signal = daily['MACD_Signal'].iloc[-1]
            macd_hist = daily['MACD_Histogram'].iloc[-1]
            
            vwap = today_data['VWAP'].iloc[-1]
            
            # Determine signals
            # SuperTrend: Price above = bullish, below = bearish
            st_bullish = supertrend_direction == 1
            st_bearish = supertrend_direction == -1
            
            # VWAP: Price above = bullish, below = bearish
            vwap_bullish = current_price > vwap
            vwap_bearish = current_price < vwap
            
            # MACD: Histogram positive and MACD > Signal = bullish
            macd_bullish = macd > macd_signal and macd_hist > 0
            macd_bearish = macd < macd_signal and macd_hist < 0
            
            # Check for triple confirmation
            bullish_confirmation = st_bullish and vwap_bullish and macd_bullish
            bearish_confirmation = st_bearish and vwap_bearish and macd_bearish
            
            if not (bullish_confirmation or bearish_confirmation):
                return None  # No triple confirmation
            
            # Calculate additional metrics
            distance_from_vwap = ((current_price - vwap) / vwap) * 100
            distance_from_st = ((current_price - supertrend) / supertrend) * 100
            
            # Calculate volume ratio
            avg_volume = daily['Volume'].tail(20).mean()
            current_volume = daily['Volume'].iloc[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            
            # Score the setup (0-15 scale)
            score = 0
            signals = []
            
            # Base confirmation (6 points - 2 per indicator)
            score += 6
            signals.append("✅ Triple Confirmation Aligned")
            
            # MACD strength (3 points)
            macd_strength = abs(macd_hist)
            if macd_strength > 0.5:
                score += 3
                signals.append("🔥 Strong MACD Momentum")
            elif macd_strength > 0.2:
                score += 2
                signals.append("📊 Good MACD Momentum")
            else:
                score += 1
            
            # Distance from VWAP (3 points)
            abs_vwap_dist = abs(distance_from_vwap)
            if abs_vwap_dist < 0.5:
                score += 3
                signals.append("🎯 Price Near VWAP (Tight)")
            elif abs_vwap_dist < 1.5:
                score += 2
                signals.append("✓ Price Close to VWAP")
            else:
                score += 1
            
            # Volume confirmation (3 points)
            if volume_ratio > 1.5:
                score += 3
                signals.append(f"📈 High Volume ({volume_ratio:.1f}x)")
            elif volume_ratio > 1.2:
                score += 2
                signals.append(f"📊 Above Avg Volume ({volume_ratio:.1f}x)")
            else:
                score += 1
            
            # Determine direction
            direction = "BULLISH" if bullish_confirmation else "BEARISH"
            
            # Calculate targets and stops
            atr = daily['ATR'].iloc[-1]
            
            if bullish_confirmation:
                stop_loss = supertrend  # Use SuperTrend as stop
                target_1 = current_price + (atr * 1.5)
                target_2 = current_price + (atr * 2.5)
                target_3 = current_price + (atr * 3.5)
            else:
                stop_loss = supertrend  # Use SuperTrend as stop
                target_1 = current_price - (atr * 1.5)
                target_2 = current_price - (atr * 2.5)
                target_3 = current_price - (atr * 3.5)
            
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
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"Error analyzing {ticker}: {e}")
            return None
    
    def scan_market(self):
        """Scan all stocks for triple confirmation setups"""
        print("\n" + "=" * 100)
        print("🔍 TRIPLE CONFIRMATION SCANNER - SuperTrend + VWAP + MACD")
        print("=" * 100)
        print(f"📊 Scanning {len(self.universe)} stocks for aligned signals...")
        print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        self.results = []
        
        # Parallel processing
        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_ticker = {executor.submit(self.analyze_stock, ticker): ticker 
                               for ticker in self.universe}
            
            for future in as_completed(future_to_ticker):
                result = future.result()
                if result:
                    self.results.append(result)
                    print(f"✅ {result['ticker']}: {result['direction']} - Score: {result['score']}/15")
        
        # Sort by score
        self.results.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\n{'=' * 100}")
        print(f"✨ Found {len(self.results)} stocks with triple confirmation")
        print(f"{'=' * 100}\n")
        
        # Display results
        self.display_results()
        
        # Save to JSON
        self.save_results()
        
        return self.results
    
    def display_results(self):
        """Display formatted results"""
        if not self.results:
            print("⚠️ No triple confirmation setups found in current market conditions.\n")
            return
        
        # Separate bullish and bearish
        bullish = [r for r in self.results if r['direction'] == 'BULLISH']
        bearish = [r for r in self.results if r['direction'] == 'BEARISH']
        
        # Display bullish setups
        if bullish:
            print("\n" + "🟢" * 50)
            print(f"📈 BULLISH SETUPS ({len(bullish)} found)")
            print("🟢" * 50 + "\n")
            
            for i, result in enumerate(bullish[:10], 1):  # Top 10
                print(f"#{i} {result['ticker']} - Score: {result['score']}/15")
                print(f"   💰 Price: ${result['current_price']:.2f}")
                print(f"   📊 VWAP: ${result['vwap']:.2f} ({result['distance_from_vwap']:+.2f}%)")
                print(f"   🔄 SuperTrend: ${result['supertrend']:.2f} ({result['distance_from_st']:+.2f}%)")
                print(f"   📈 MACD: {result['macd']:.3f} | Hist: {result['macd_histogram']:.3f}")
                print(f"   📊 Volume: {result['volume_ratio']:.1f}x average")
                print(f"   🎯 Targets: ${result['target_1']:.2f} → ${result['target_2']:.2f} → ${result['target_3']:.2f}")
                print(f"   🛑 Stop Loss: ${result['stop_loss']:.2f}")
                print(f"   ✨ Signals: {', '.join(result['signals'])}")
                print()
        
        # Display bearish setups
        if bearish:
            print("\n" + "🔴" * 50)
            print(f"📉 BEARISH SETUPS ({len(bearish)} found)")
            print("🔴" * 50 + "\n")
            
            for i, result in enumerate(bearish[:10], 1):  # Top 10
                print(f"#{i} {result['ticker']} - Score: {result['score']}/15")
                print(f"   💰 Price: ${result['current_price']:.2f}")
                print(f"   📊 VWAP: ${result['vwap']:.2f} ({result['distance_from_vwap']:+.2f}%)")
                print(f"   🔄 SuperTrend: ${result['supertrend']:.2f} ({result['distance_from_st']:+.2f}%)")
                print(f"   📉 MACD: {result['macd']:.3f} | Hist: {result['macd_histogram']:.3f}")
                print(f"   📊 Volume: {result['volume_ratio']:.1f}x average")
                print(f"   🎯 Targets: ${result['target_1']:.2f} → ${result['target_2']:.2f} → ${result['target_3']:.2f}")
                print(f"   🛑 Stop Loss: ${result['stop_loss']:.2f}")
                print(f"   ✨ Signals: {', '.join(result['signals'])}")
                print()
    
    def save_results(self):
        """Save results to JSON file"""
        try:
            with open(self.output_file, 'w') as f:
                json.dump({
                    'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'total_scanned': len(self.universe),
                    'setups_found': len(self.results),
                    'results': self.results
                }, f, indent=2)
            
            print(f"💾 Results saved to {self.output_file}\n")
        except Exception as e:
            print(f"⚠️ Error saving results: {e}\n")


def main():
    """Main execution"""
    scanner = TripleConfirmationScanner()
    scanner.scan_market()


if __name__ == "__main__":
    main()
