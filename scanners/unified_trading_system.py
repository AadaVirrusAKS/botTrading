#!/usr/bin/env python3
"""
UNIFIED TRADING SYSTEM
Comprehensive platform for Options, Stocks, and ETFs
Generates Top 5 picks for each category with monitoring and alerts
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
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.master_stock_list import get_master_stock_list, MASTER_ETF_UNIVERSE
from config import PROJECT_ROOT, DATA_DIR

class UnifiedTradingSystem:
    """Main trading system for all asset types"""
    
    def __init__(self, alert_interval=5):
        """Initialize unified trading system"""
        self.alert_interval = alert_interval
        self.positions_file = os.path.join(DATA_DIR, 'active_positions.json')
        self.picks_file = os.path.join(DATA_DIR, 'top_picks.json')
        self.alerts_log = 'trade_alerts.log'
        
        # Asset universes (shared master source)
        self.options_universe = ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN', 'META']
        self.stock_universe = get_master_stock_list(include_etfs=False)
        self.etf_universe = MASTER_ETF_UNIVERSE
        
        self.active_positions = self.load_positions()
        
        print("=" * 100)
        print("🚀 UNIFIED TRADING SYSTEM")
        print("=" * 100)
        print(f"📅 {datetime.now().strftime('%A, %B %d, %Y %I:%M %p')}")
        print(f"🎯 Focus: OPTIONS • STOCKS • ETFs")
        print(f"⚡ Alert Interval: {alert_interval} minutes")
        print("=" * 100)
    
    def load_positions(self) -> Dict:
        """Load active positions"""
        if os.path.exists(self.positions_file):
            try:
                with open(self.positions_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def save_positions(self):
        """Save positions to file"""
        with open(self.positions_file, 'w') as f:
            json.dump(self.active_positions, f, indent=2)
    
    def save_picks(self, picks: Dict):
        """Save top picks to file"""
        picks['timestamp'] = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
        with open(self.picks_file, 'w') as f:
            json.dump(picks, f, indent=2)
    
    def log_alert(self, message: str):
        """Log alert message"""
        timestamp = datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')
        with open(self.alerts_log, 'a') as f:
            f.write(f"[{timestamp}] {message}\n")
        print(message)
    
    def get_live_data(self, ticker: str) -> Optional[Dict]:
        """Get comprehensive live data for any ticker"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            # Get current price
            current_price = info.get('currentPrice') or info.get('regularMarketPrice')
            if not current_price:
                hist = stock.history(period='1d', interval='1m')
                if len(hist) > 0:
                    current_price = hist['Close'].iloc[-1]
            
            if not current_price:
                return None
            
            # Get historical data
            daily = stock.history(period='3mo', interval='1d')
            if len(daily) < 20:
                return None
            
            # Calculate indicators
            daily['SMA20'] = daily['Close'].rolling(window=20).mean()
            daily['SMA50'] = daily['Close'].rolling(window=50).mean()
            daily['SMA200'] = daily['Close'].rolling(window=200).mean()
            daily['EMA9'] = daily['Close'].ewm(span=9, adjust=False).mean()
            daily['EMA21'] = daily['Close'].ewm(span=21, adjust=False).mean()
            
            # RSI
            delta = daily['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            daily['RSI'] = 100 - (100 / (1 + rs))
            
            # MACD
            daily['MACD'] = daily['Close'].ewm(span=12, adjust=False).mean() - daily['Close'].ewm(span=26, adjust=False).mean()
            daily['Signal'] = daily['MACD'].ewm(span=9, adjust=False).mean()
            
            # ATR
            daily['TR'] = daily[['High', 'Low']].apply(lambda x: x['High'] - x['Low'], axis=1)
            daily['ATR'] = daily['TR'].rolling(window=14).mean()
            
            # Volume
            daily['Volume_SMA'] = daily['Volume'].rolling(window=20).mean()
            daily['Volume_Ratio'] = daily['Volume'] / daily['Volume_SMA']
            
            latest = daily.iloc[-1]
            prev = daily.iloc[-2]
            
            return {
                'ticker': ticker,
                'price': current_price,
                'open': latest['Open'],
                'high': latest['High'],
                'low': latest['Low'],
                'volume': latest['Volume'],
                'sma20': latest['SMA20'],
                'sma50': latest['SMA50'],
                'sma200': latest.get('SMA200', None),
                'ema9': latest['EMA9'],
                'ema21': latest['EMA21'],
                'rsi': latest['RSI'],
                'macd': latest['MACD'],
                'signal': latest['Signal'],
                'atr': latest['ATR'],
                'volume_ratio': latest['Volume_Ratio'],
                'prev_close': prev['Close'],
                'change_pct': ((current_price - prev['Close']) / prev['Close']) * 100,
                'market_cap': info.get('marketCap', 0),
                'sector': info.get('sector', 'Unknown')
            }
        except Exception as e:
            print(f"Error fetching {ticker}: {e}")
            return None
    
    def score_asset(self, data: Dict, asset_type: str) -> Tuple[float, List[str]]:
        """Score an asset based on technical indicators"""
        score = 0
        signals = []
        
        if not data:
            return 0, []
        
        # Trend scoring (common for all)
        if data['ema9'] > data['ema21']:
            score += 2
            signals.append("Bullish EMAs")
        if data['price'] > data['sma50']:
            score += 1
            signals.append("Above SMA50")
        if data.get('sma200') and data['price'] > data['sma200']:
            score += 1
            signals.append("Above SMA200")
        
        # Momentum scoring - AVOID OVERBOUGHT (sharp drop risk)
        if 40 <= data['rsi'] <= 60:
            score += 3
            signals.append("RSI healthy (40-60)")
        elif 30 < data['rsi'] < 40:
            score += 2
            signals.append("RSI oversold (bounce potential)")
        elif 60 < data['rsi'] < 70:
            score += 1
            signals.append("RSI elevated (caution)")
        elif data['rsi'] >= 80:
            score -= 2
            signals.append("⚠️ RSI OVERBOUGHT - AVOID (drop risk)")
        elif data['rsi'] <= 30:
            score += 2
            signals.append("RSI oversold (bounce opportunity)")
        
        if data['macd'] > data['signal']:
            score += 2
            signals.append("MACD bullish")
        
        # Volume
        if data['volume_ratio'] > 1.2:
            score += 1
            signals.append("High volume")
        
        # Asset-specific scoring
        if asset_type == 'OPTIONS':
            # High volatility is good for options, but avoid overbought
            if data['atr'] / data['price'] > 0.02:
                score += 2
                signals.append("High volatility (options-friendly)")
            if abs(data['change_pct']) > 1 and data['rsi'] < 70:
                score += 1
                signals.append("Strong momentum")
            # Extra penalty for overbought options
            if data['rsi'] >= 75:
                score -= 1
                signals.append("⚠️ Extreme overbought")
        
        elif asset_type == 'STOCK':
            # Steady growth for stocks
            if 0 < data['change_pct'] < 3:
                score += 2
                signals.append("Steady positive trend")
            if data.get('market_cap', 0) > 10e9:
                score += 1
                signals.append("Large cap stability")
        
        elif asset_type == 'ETF':
            # Stability and trend for ETFs
            if abs(data['change_pct']) < 2:
                score += 1
                signals.append("Low volatility (stable)")
            if data['volume_ratio'] > 1.0:
                score += 1
                signals.append("Good liquidity")
        
        return score, signals
    
    def generate_option_setup(self, ticker: str, data: Dict) -> Optional[Dict]:
        """Generate option trade setup"""
        if not data:
            return None
        
        price = data['price']
        atr = data['atr']
        rsi = data['rsi']
        
        # Determine direction
        bullish_score = 0
        bearish_score = 0
        
        if data['ema9'] > data['ema21']:
            bullish_score += 1
        else:
            bearish_score += 1
        
        if rsi < 45:
            bullish_score += 2
        elif rsi > 55:
            bearish_score += 2
        
        if data['macd'] > data['signal']:
            bullish_score += 1
        else:
            bearish_score += 1
        
        if bullish_score > bearish_score:
            direction = 'CALL'
            strike = round(price + (atr * 0.1), 0)
        else:
            direction = 'PUT'
            strike = round(price + (atr * 0.25), 0)
        
        # Calculate premium
        intrinsic = max(0, price - strike) if direction == 'CALL' else max(0, strike - price)
        time_value = atr * 0.35
        premium = max(intrinsic + time_value, atr * 0.25)
        premium = min(premium, price * 0.015)
        
        return {
            'ticker': ticker,
            'type': direction,
            'strike': strike,
            'premium': premium,
            'contract_cost': premium * 100,
            'target_1': premium * 2,
            'target_2': premium * 3,
            'target_3': premium * 4,
            'stop_loss': premium * 0.5,
            'confidence': max(bullish_score, bearish_score),
            'expiry': '1DTE (Tomorrow)'
        }
    
    def get_top_5_picks(self) -> Dict:
        """Generate top 5 picks for each category"""
        print("\n" + "=" * 100)
        print("🔍 SCANNING MARKETS FOR TOP PICKS...")
        print("=" * 100)
        
        all_picks = {
            'options': [],
            'stocks': [],
            'etfs': []
        }
        
        # Helper function for parallel processing
        def analyze_ticker(ticker, asset_type):
            """Analyze single ticker - thread-safe"""
            try:
                data = self.get_live_data(ticker)
                if not data:
                    return None
                
                score, signals = self.score_asset(data, asset_type)
                
                result = {
                    'ticker': ticker,
                    'score': score,
                    'signals': signals,
                    'data': data
                }
                
                # Add option setup for OPTIONS
                if asset_type == 'OPTIONS':
                    option_setup = self.generate_option_setup(ticker, data)
                    if option_setup:
                        result['setup'] = option_setup
                    else:
                        return None  # Skip if no valid option setup
                
                return result
            except Exception as e:
                return None
        
        # Scan OPTIONS universe (parallel - 10x faster!)
        print("\n💰 Scanning Options Candidates...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(analyze_ticker, ticker, 'OPTIONS'): ticker 
                      for ticker in self.options_universe}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    all_picks['options'].append(result)
                    print(f"  ✅ {result['ticker']} - Score: {result['score']}/15")
        
        # Scan STOCKS universe (parallel)
        print("\n📈 Scanning Stock Candidates...")
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(analyze_ticker, ticker, 'STOCK'): ticker 
                      for ticker in self.stock_universe}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    all_picks['stocks'].append(result)
                    print(f"  ✅ {result['ticker']} - Score: {result['score']}/15")
        
        # Scan ETF universe (parallel)
        print("\n📊 Scanning ETF Candidates...")
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(analyze_ticker, ticker, 'ETF'): ticker 
                      for ticker in self.etf_universe}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    all_picks['etfs'].append(result)
                    print(f"  ✅ {result['ticker']} - Score: {result['score']}/15")
        
        # Get top 5 from each category
        top_picks = {
            'options': sorted(all_picks['options'], key=lambda x: x['score'], reverse=True)[:5],
            'stocks': sorted(all_picks['stocks'], key=lambda x: x['score'], reverse=True)[:5],
            'etfs': sorted(all_picks['etfs'], key=lambda x: x['score'], reverse=True)[:5]
        }
        
        self.save_picks(top_picks)
        return top_picks
    
    def display_top_picks(self, picks: Dict):
        """Display top 5 picks for each category"""
        print("\n" + "=" * 100)
        print("🏆 TOP 5 PICKS - ANALYSIS COMPLETE")
        print("=" * 100)
        print(f"Generated: {datetime.now().strftime('%I:%M:%S %p')}")
        
        # OPTIONS
        print("\n" + "─" * 100)
        print("💰 TOP 5 OPTIONS TRADES")
        print("─" * 100)
        
        for i, pick in enumerate(picks['options'], 1):
            data = pick['data']
            setup = pick['setup']
            print(f"\n#{i}. {pick['ticker']} - Score: {pick['score']}/15")
            print(f"   💹 Price: ${data['price']:.2f} | RSI: {data['rsi']:.1f}")
            print(f"   🎯 {setup['type']} ${setup['strike']:.0f} @ ${setup['premium']:.2f}")
            print(f"   💵 Cost: ${setup['contract_cost']:.2f} | Target: ${setup['target_3']:.2f} (1:4)")
            print(f"   ✅ Signals: {', '.join(pick['signals'][:3])}")
        
        # STOCKS
        print("\n" + "─" * 100)
        print("📈 TOP 5 STOCK PICKS")
        print("─" * 100)
        
        for i, pick in enumerate(picks['stocks'], 1):
            data = pick['data']
            print(f"\n#{i}. {pick['ticker']} - Score: {pick['score']}/15")
            print(f"   💹 Price: ${data['price']:.2f} | Change: {'+' if data['change_pct'] >= 0 else ''}{data['change_pct']:.2f}%")
            print(f"   📊 RSI: {data['rsi']:.1f} | Volume: {data['volume_ratio']:.2f}x avg")
            print(f"   🏢 Sector: {data['sector']}")
            print(f"   ✅ Signals: {', '.join(pick['signals'][:3])}")
        
        # ETFs
        print("\n" + "─" * 100)
        print("📊 TOP 5 ETF PICKS")
        print("─" * 100)
        
        for i, pick in enumerate(picks['etfs'], 1):
            data = pick['data']
            print(f"\n#{i}. {pick['ticker']} - Score: {pick['score']}/15")
            print(f"   💹 Price: ${data['price']:.2f} | Change: {'+' if data['change_pct'] >= 0 else ''}{data['change_pct']:.2f}%")
            print(f"   📊 RSI: {data['rsi']:.1f} | ATR: ${data['atr']:.2f}")
            print(f"   ✅ Signals: {', '.join(pick['signals'][:3])}")
        
        print("\n" + "=" * 100)
    
    def add_position(self, ticker: str, asset_type: str, direction: str = None,
                     strike: float = None, entry_price: float = None, 
                     contracts: int = 1, shares: int = None):
        """Add position to monitoring"""
        position_id = f"{ticker}_{asset_type}_{datetime.now().strftime('%H%M%S')}"
        
        position = {
            'ticker': ticker,
            'asset_type': asset_type,
            'entry_time': datetime.now().strftime('%I:%M %p'),
            'entry_date': datetime.now().strftime('%Y-%m-%d'),
            'status': 'ACTIVE',
            'alerts_sent': 0
        }
        
        if asset_type == 'OPTION':
            position.update({
                'direction': direction,
                'strike': strike,
                'entry_premium': entry_price,
                'contracts': contracts,
                'targets': {
                    '1:2': {'value': entry_price * 2, 'hit': False},
                    '1:3': {'value': entry_price * 3, 'hit': False},
                    '1:4': {'value': entry_price * 4, 'hit': False}
                },
                'stop_loss': entry_price * 0.5,
                'current_premium': entry_price,
                'pnl': 0
            })
        else:  # STOCK or ETF
            position.update({
                'entry_price': entry_price,
                'shares': shares or contracts,
                'target_price': entry_price * 1.10,  # 10% gain
                'stop_loss': entry_price * 0.95,  # 5% loss
                'current_price': entry_price,
                'pnl': 0
            })
        
        self.active_positions[position_id] = position
        self.save_positions()
        self.log_alert(f"✅ POSITION ADDED: {ticker} {asset_type}")
        
        return position_id
    
    def monitor_positions(self, duration_minutes: int = 360):
        """Monitor all active positions"""
        if not self.active_positions:
            print("\n⚠️  No active positions to monitor")
            return
        
        print(f"\n🔴 Starting monitoring ({self.alert_interval}-minute alerts)...")
        
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        try:
            while datetime.now() < end_time:
                active_count = sum(1 for p in self.active_positions.values() if p['status'] == 'ACTIVE')
                
                if active_count == 0:
                    print("\n✅ All positions closed")
                    break
                
                print(f"\n{'=' * 100}")
                print(f"🔔 ALERT - {datetime.now().strftime('%I:%M:%S %p')}")
                print(f"{'=' * 100}")
                
                for pos_id, position in list(self.active_positions.items()):
                    if position['status'] == 'ACTIVE':
                        self.check_position(pos_id)
                
                # Check exit time
                if datetime.now().time() >= dt_time(14, 50):
                    print("\n⏰ MANDATORY EXIT TIME!")
                    break
                
                print(f"\n💤 Next alert in {self.alert_interval} minutes...")
                time.sleep(self.alert_interval * 60)
                
        except KeyboardInterrupt:
            print("\n\n⚠️  Monitoring stopped")
    
    def check_position(self, position_id: str):
        """Check and update a position"""
        position = self.active_positions[position_id]
        ticker = position['ticker']
        
        data = self.get_live_data(ticker)
        if not data:
            return
        
        print(f"\n📊 {ticker} ({position['asset_type']})")
        
        if position['asset_type'] == 'OPTION':
            # Estimate current premium (simplified)
            position['current_premium'] = data['price'] * 0.02  # Rough estimate
            pnl = (position['current_premium'] - position['entry_premium']) * 100 * position['contracts']
            position['pnl'] = pnl
            
            print(f"   Entry: ${position['entry_premium']:.2f} | Current: ${position['current_premium']:.2f}")
            print(f"   P&L: ${pnl:.2f}")
            
            # Check targets
            for target_name, target in position['targets'].items():
                if not target['hit'] and position['current_premium'] >= target['value']:
                    target['hit'] = True
                    print(f"   🎯 {target_name} HIT!")
        
        else:  # STOCK or ETF
            position['current_price'] = data['price']
            pnl = (position['current_price'] - position['entry_price']) * position['shares']
            position['pnl'] = pnl
            
            print(f"   Entry: ${position['entry_price']:.2f} | Current: ${position['current_price']:.2f}")
            print(f"   P&L: ${pnl:.2f}")
            
            if position['current_price'] >= position['target_price']:
                print(f"   🎯 Target price hit!")
            elif position['current_price'] <= position['stop_loss']:
                print(f"   🛑 Stop loss hit!")
        
        position['alerts_sent'] += 1
        self.save_positions()


def main():
    """Main execution"""
    system = UnifiedTradingSystem(alert_interval=5)
    
    print("\n📋 SELECT ACTION:")
    print("\n1️⃣  Generate Top 5 Picks (Options, Stocks, ETFs)")
    print("2️⃣  Monitor Active Positions")
    print("3️⃣  Add New Position")
    print("4️⃣  View Saved Picks")
    print("5️⃣  Exit")
    
    choice = input("\n👉 Enter choice (1-5): ").strip()
    
    if choice == '1':
        picks = system.get_top_5_picks()
        system.display_top_picks(picks)
        
        print("\n💡 Picks saved to 'top_picks.json'")
        print("\nStart monitoring? (y/n): ", end='')
        if input().strip().lower() == 'y':
            system.monitor_positions()
    
    elif choice == '2':
        system.monitor_positions()
    
    elif choice == '3':
        print("\n📝 Add Position")
        ticker = input("Ticker: ").upper()
        asset_type = input("Type (OPTION/STOCK/ETF): ").upper()
        
        if asset_type == 'OPTION':
            direction = input("Direction (CALL/PUT): ").upper()
            strike = float(input("Strike: "))
            premium = float(input("Entry Premium: "))
            contracts = int(input("Contracts: "))
            system.add_position(ticker, asset_type, direction, strike, premium, contracts)
        else:
            price = float(input("Entry Price: "))
            shares = int(input("Shares: "))
            system.add_position(ticker, asset_type, entry_price=price, shares=shares)
        
        print("\n✅ Position added! Start monitoring? (y/n): ", end='')
        if input().strip().lower() == 'y':
            system.monitor_positions()
    
    elif choice == '4':
        if os.path.exists(system.picks_file):
            with open(system.picks_file, 'r') as f:
                picks = json.load(f)
            print(f"\n📅 Last generated: {picks.get('timestamp', 'Unknown')}")
            system.display_top_picks(picks)
        else:
            print("\n⚠️  No saved picks found. Run option 1 first.")
    
    print("\n" + "=" * 100)
    print("✅ Session Complete")
    print("=" * 100)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
