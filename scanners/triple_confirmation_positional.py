#!/usr/bin/env python3
"""
TRIPLE CONFIRMATION POSITIONAL SCANNER
Identifies high-probability 1-2 WEEK positions using SuperTrend + VWAP + MACD on WEEKLY timeframes
Designed for swing positions with multi-day/week holding periods
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

# Use centralized caching layer to avoid Yahoo rate limiting
try:
    from services.market_data import (
        cached_get_history, prewarm_history_cache, _is_globally_rate_limited
    )
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False


class TripleConfirmationPositional:
    """Positional scanner using weekly timeframes for 1-2 week holds"""
    
    def __init__(self):
        self.universe = get_master_stock_list(include_etfs=True)
        
        self.results = []
        self.output_file = os.path.join(DATA_DIR, 'triple_confirmation_positional.json')
    
    def calculate_supertrend(self, df, period=14, multiplier=3):
        """Calculate SuperTrend - optimized for weekly timeframe"""
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
        """Calculate VWAP - weekly rolling"""
        df['Typical_Price'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['TP_Volume'] = df['Typical_Price'] * df['Volume']
        
        # Use 5-week rolling VWAP for positional
        window = 5
        df['VWAP'] = (df['TP_Volume'].rolling(window=window).sum() / 
                      df['Volume'].rolling(window=window).sum())
        
        return df
    
    def calculate_macd(self, df, fast=12, slow=26, signal=9):
        """Calculate MACD - standard settings for weekly"""
        df['EMA_Fast'] = df['Close'].ewm(span=fast, adjust=False).mean()
        df['EMA_Slow'] = df['Close'].ewm(span=slow, adjust=False).mean()
        df['MACD'] = df['EMA_Fast'] - df['EMA_Slow']
        df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['MACD_Signal']
        return df
    
    def calculate_trend_strength(self, df):
        """Calculate trend strength indicators"""
        # ADX for trend strength
        df['Plus_DM'] = df['High'].diff()
        df['Minus_DM'] = -df['Low'].diff()
        
        df['Plus_DM'] = df['Plus_DM'].where((df['Plus_DM'] > df['Minus_DM']) & (df['Plus_DM'] > 0), 0)
        df['Minus_DM'] = df['Minus_DM'].where((df['Minus_DM'] > df['Plus_DM']) & (df['Minus_DM'] > 0), 0)
        
        period = 14
        df['Plus_DI'] = 100 * (df['Plus_DM'].rolling(window=period).mean() / df['ATR'])
        df['Minus_DI'] = 100 * (df['Minus_DM'].rolling(window=period).mean() / df['ATR'])
        
        df['DX'] = 100 * abs(df['Plus_DI'] - df['Minus_DI']) / (df['Plus_DI'] + df['Minus_DI'])
        df['ADX'] = df['DX'].rolling(window=period).mean()
        
        return df
    
    def analyze_stock(self, ticker):
        """Analyze stock for positional triple confirmation"""
        try:
            if _USE_CACHED:
                if _is_globally_rate_limited():
                    return None
                weekly = cached_get_history(ticker, period='2y', interval='1wk')
            else:
                stock = yf.Ticker(ticker)
                weekly = stock.history(period='2y', interval='1wk')
            
            if weekly is None or weekly.empty or len(weekly) < 52:
                return None
            
            # Calculate all indicators on weekly timeframe
            weekly = self.calculate_supertrend(weekly.copy(), period=14, multiplier=3)
            weekly = self.calculate_vwap(weekly.copy())
            weekly = self.calculate_macd(weekly.copy())
            weekly = self.calculate_trend_strength(weekly.copy())
            
            # Get current values
            current_price = weekly['Close'].iloc[-1]
            supertrend = weekly['SuperTrend'].iloc[-1]
            supertrend_direction = weekly['Direction'].iloc[-1]
            
            macd = weekly['MACD'].iloc[-1]
            macd_signal = weekly['MACD_Signal'].iloc[-1]
            macd_hist = weekly['MACD_Histogram'].iloc[-1]
            
            vwap = weekly['VWAP'].iloc[-1]
            adx = weekly['ADX'].iloc[-1] if not pd.isna(weekly['ADX'].iloc[-1]) else 0
            
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
            
            # Weekly volume analysis
            avg_volume = weekly['Volume'].tail(12).mean()  # 12-week average
            current_volume = weekly['Volume'].iloc[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
            
            # Score the setup (0-15 scale)
            score = 0
            signals = []
            
            # Base confirmation (6 points)
            score += 6
            signals.append("✅ Weekly Triple Confirmation")
            
            # MACD strength (3 points)
            macd_strength = abs(macd_hist)
            if macd_strength > 2.0:
                score += 3
                signals.append("🔥 Very Strong Weekly Momentum")
            elif macd_strength > 0.8:
                score += 2
                signals.append("📊 Strong Momentum")
            else:
                score += 1
            
            # Trend strength via ADX (3 points)
            if adx > 30:
                score += 3
                signals.append(f"💪 Strong Trend (ADX: {adx:.0f})")
            elif adx > 20:
                score += 2
                signals.append(f"📈 Moderate Trend (ADX: {adx:.0f})")
            else:
                score += 1
            
            # Distance from VWAP (3 points)
            abs_vwap_dist = abs(distance_from_vwap)
            if abs_vwap_dist < 2.0:
                score += 3
                signals.append("🎯 Near Weekly VWAP")
            elif abs_vwap_dist < 5.0:
                score += 2
                signals.append("✓ Reasonable VWAP Distance")
            else:
                score += 1
            
            direction = "BULLISH" if bullish_confirmation else "BEARISH"
            
            # Calculate ATR and targets (weekly ATR)
            atr = weekly['ATR'].iloc[-1]
            
            # Positional targets - wider than swing/intraday
            if bullish_confirmation:
                stop_loss = supertrend
                target_1 = current_price + (atr * 2.0)  # 1:2 weekly ATR
                target_2 = current_price + (atr * 3.5)  # 1:3.5 weekly ATR
                target_3 = current_price + (atr * 5.0)  # 1:5 weekly ATR
            else:
                stop_loss = supertrend
                target_1 = current_price - (atr * 2.0)
                target_2 = current_price - (atr * 3.5)
                target_3 = current_price - (atr * 5.0)
            
            # Risk/Reward calculation
            risk = abs(current_price - stop_loss)
            reward_t1 = abs(target_1 - current_price)
            rr_ratio = reward_t1 / risk if risk > 0 else 0
            
            # Get additional context
            week_high = weekly['High'].iloc[-1]
            week_low = weekly['Low'].iloc[-1]
            week_change_pct = ((current_price - weekly['Close'].iloc[-2]) / weekly['Close'].iloc[-2]) * 100
            
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
                'adx': adx,
                'volume_ratio': volume_ratio,
                'atr': atr,
                'stop_loss': stop_loss,
                'target_1': target_1,
                'target_2': target_2,
                'target_3': target_3,
                'risk_amount': risk,
                'rr_ratio': rr_ratio,
                'week_high': week_high,
                'week_low': week_low,
                'week_change_pct': week_change_pct,
                'signals': signals,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
        except Exception as e:
            print(f"Error analyzing {ticker}: {e}")
            return None
    
    def scan_market(self):
        """Scan all stocks for positional triple confirmation"""
        print("\n" + "=" * 100)
        print("📅 POSITIONAL TRIPLE CONFIRMATION SCANNER - Weekly Timeframe (1-2 Week Holds)")
        print("=" * 100)
        print(f"📊 Scanning {len(self.universe)} stocks for weekly trend alignment...")
        print(f"⏰ Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"⚙️ Settings: SuperTrend(14,3) + Weekly VWAP(5) + MACD(12,26,9) + ADX(14)\n")
        
        self.results = []
        
        # Pre-warm cache with batch download
        if _USE_CACHED:
            print(f"  📦 Pre-warming cache for {len(self.universe)} symbols...")
            prewarm_history_cache(self.universe, period='2y', interval='1wk')

        # Throttled parallel processing (3 workers to respect rate limits)
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_ticker = {executor.submit(self.analyze_stock, ticker): ticker 
                               for ticker in self.universe}
            
            for future in as_completed(future_to_ticker):
                result = future.result()
                if result:
                    self.results.append(result)
                    print(f"✅ {result['ticker']}: {result['direction']} - Score: {result['score']}/15 (R:R 1:{result['rr_ratio']:.1f})")
        
        self.results.sort(key=lambda x: (x['score'], x['rr_ratio']), reverse=True)
        
        print(f"\n{'=' * 100}")
        print(f"✨ Found {len(self.results)} positional setups with weekly confirmation")
        print(f"{'=' * 100}\n")
        
        self.display_results()
        self.save_results()
        
        return self.results
    
    def display_results(self):
        """Display formatted positional results"""
        if not self.results:
            print("⚠️ No weekly triple confirmation setups found.\n")
            return
        
        bullish = [r for r in self.results if r['direction'] == 'BULLISH']
        bearish = [r for r in self.results if r['direction'] == 'BEARISH']
        
        if bullish:
            print("\n" + "🟢" * 50)
            print(f"📈 BULLISH POSITIONAL SETUPS - 1-2 WEEK HOLDS ({len(bullish)} found)")
            print("🟢" * 50 + "\n")
            
            for i, r in enumerate(bullish[:15], 1):
                print(f"#{i} {r['ticker']} - Score: {r['score']}/15")
                print(f"   💰 Entry: ${r['current_price']:.2f} | Week Range: ${r['week_low']:.2f}-${r['week_high']:.2f}")
                print(f"   📊 Weekly VWAP: ${r['vwap']:.2f} ({r['distance_from_vwap']:+.2f}%)")
                print(f"   🔄 SuperTrend: ${r['supertrend']:.2f} ({r['distance_from_st']:+.2f}%)")
                print(f"   📈 MACD: {r['macd']:.3f} | Histogram: {r['macd_histogram']:+.3f}")
                print(f"   💪 ADX: {r['adx']:.0f} | Week Change: {r['week_change_pct']:+.1f}%")
                print(f"   🎯 Targets (1-2 weeks): ${r['target_1']:.2f} → ${r['target_2']:.2f} → ${r['target_3']:.2f}")
                print(f"   🛑 Stop Loss: ${r['stop_loss']:.2f} | Risk: ${r['risk_amount']:.2f} | R:R = 1:{r['rr_ratio']:.1f}")
                print(f"   ✨ {', '.join(r['signals'])}")
                print()
        
        if bearish:
            print("\n" + "🔴" * 50)
            print(f"📉 BEARISH POSITIONAL SETUPS - 1-2 WEEK HOLDS ({len(bearish)} found)")
            print("🔴" * 50 + "\n")
            
            for i, r in enumerate(bearish[:15], 1):
                print(f"#{i} {r['ticker']} - Score: {r['score']}/15")
                print(f"   💰 Entry: ${r['current_price']:.2f} | Week Range: ${r['week_low']:.2f}-${r['week_high']:.2f}")
                print(f"   📊 Weekly VWAP: ${r['vwap']:.2f} ({r['distance_from_vwap']:+.2f}%)")
                print(f"   🔄 SuperTrend: ${r['supertrend']:.2f} ({r['distance_from_st']:+.2f}%)")
                print(f"   📉 MACD: {r['macd']:.3f} | Histogram: {r['macd_histogram']:+.3f}")
                print(f"   💪 ADX: {r['adx']:.0f} | Week Change: {r['week_change_pct']:+.1f}%")
                print(f"   🎯 Targets (1-2 weeks): ${r['target_1']:.2f} → ${r['target_2']:.2f} → ${r['target_3']:.2f}")
                print(f"   🛑 Stop Loss: ${r['stop_loss']:.2f} | Risk: ${r['risk_amount']:.2f} | R:R = 1:{r['rr_ratio']:.1f}")
                print(f"   ✨ {', '.join(r['signals'])}")
                print()
        
        print("\n📅 POSITIONAL TRADING RULES:")
        print("   • Hold for 1-2 weeks minimum")
        print("   • Use weekly SuperTrend as trailing stop")
        print("   • Take partial profits at T1 (50%), T2 (30%), T3 (20%)")
        print("   • Re-scan weekly on Sunday evening")
        print("   • Exit if weekly confirmation breaks\n")
    
    def save_results(self):
        """Save positional results to JSON"""
        try:
            with open(self.output_file, 'w') as f:
                json.dump({
                    'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'timeframe': 'weekly',
                    'type': 'POSITIONAL (1-2 weeks)',
                    'total_scanned': len(self.universe),
                    'setups_found': len(self.results),
                    'results': self.results
                }, f, indent=2)
            
            print(f"💾 Positional results saved to {self.output_file}\n")
        except Exception as e:
            print(f"⚠️ Error saving results: {e}\n")


def main():
    """Main execution"""
    scanner = TripleConfirmationPositional()
    scanner.scan_market()


if __name__ == "__main__":
    main()
