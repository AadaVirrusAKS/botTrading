"""
Short Squeeze Scanner
Identifies stocks with high probability of short squeeze (20-30%+ potential moves)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import random
import warnings
from config.master_stock_list import get_master_stock_list
from config import PROJECT_ROOT, DATA_DIR
warnings.filterwarnings('ignore')

class ShortSqueezeScanner:
    def __init__(self):
        # Get comprehensive stock universe
        self.symbols = self.get_stock_universe()
        self.request_delay = 0.5  # 500ms delay between requests (increased from 300ms)
        self.batch_delay = 10  # 10 second delay after each batch
        self.batch_size = 5  # Process 5 stocks, then wait 10 seconds
    
    def retry_with_backoff(self, func, max_retries=3, initial_delay=3):
        """Retry function with exponential backoff for rate limiting"""
        for attempt in range(max_retries):
            try:
                time.sleep(self.request_delay + random.uniform(0, 0.3))  # Add jitter
                return func()
            except Exception as e:
                error_msg = str(e).lower()
                if 'rate limit' in error_msg or 'too many requests' in error_msg or '429' in error_msg:
                    if attempt < max_retries - 1:
                        delay = initial_delay * (2 ** attempt) + random.uniform(0, 3)
                        print(f"⏳ Rate limited on attempt {attempt+1}, waiting {delay:.1f}s...")
                        time.sleep(delay)
                    else:
                        # Max retries reached - fail silently
                        return None
                else:
                    # For non-rate-limit errors, fail silently and continue
                    return None
        return None
    
    def get_stock_universe(self):
        """Get comprehensive list of US stocks and ETFs for short squeeze analysis"""
        return get_master_stock_list(include_etfs=True)
    
    def get_short_interest_data(self, symbol):
        """Get short interest metrics"""
        try:
            stock = yf.Ticker(symbol)
            info = stock.info
            
            # Key short interest metrics
            short_data = {
                'short_percent_float': info.get('shortPercentOfFloat', 0) * 100 if info.get('shortPercentOfFloat') else 0,
                'short_ratio': info.get('shortRatio', 0),  # Days to cover
                'shares_short': info.get('sharesShort', 0),
                'shares_outstanding': info.get('sharesOutstanding', 0),
                'float_shares': info.get('floatShares', 0),
                'shares_short_prior_month': info.get('sharesShortPriorMonth', 0)
            }
            
            # Calculate short interest change
            if short_data['shares_short_prior_month'] > 0:
                short_data['short_interest_change'] = (
                    (short_data['shares_short'] - short_data['shares_short_prior_month']) / 
                    short_data['shares_short_prior_month'] * 100
                )
            else:
                short_data['short_interest_change'] = 0
            
            return short_data
        except Exception as e:
            print(f"Error getting short data for {symbol}: {e}")
            return None
    
    def get_price_momentum(self, symbol):
        """Analyze recent price momentum and volume"""
        try:
            def fetch_history():
                stock = yf.Ticker(symbol)
                return stock.history(period='3mo')
            
            # Get 3 months of data with retry
            hist = self.retry_with_backoff(fetch_history)
            if hist is None or hist.empty or len(hist) < 20:
                return None
            
            current_price = hist['Close'].iloc[-1]
            
            # Calculate various momentum indicators
            sma_20 = hist['Close'].tail(20).mean()
            sma_50 = hist['Close'].tail(50).mean() if len(hist) >= 50 else sma_20
            
            # Price changes
            change_1d = ((current_price - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2]) * 100 if len(hist) >= 2 else 0
            change_5d = ((current_price - hist['Close'].iloc[-6]) / hist['Close'].iloc[-6]) * 100 if len(hist) >= 6 else 0
            change_1m = ((current_price - hist['Close'].iloc[-21]) / hist['Close'].iloc[-21]) * 100 if len(hist) >= 21 else 0
            
            # Volume analysis
            avg_volume_20d = hist['Volume'].tail(20).mean()
            recent_volume_5d = hist['Volume'].tail(5).mean()
            volume_spike = (recent_volume_5d / avg_volume_20d) if avg_volume_20d > 0 else 1
            
            # Price vs moving averages
            price_vs_sma20 = ((current_price - sma_20) / sma_20) * 100
            price_vs_sma50 = ((current_price - sma_50) / sma_50) * 100
            
            # RSI calculation
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1] if not rsi.empty else 50
            
            # Volatility
            returns = hist['Close'].pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100
            
            # 52-week range
            high_52w = hist['Close'].max()
            low_52w = hist['Close'].min()
            range_position = ((current_price - low_52w) / (high_52w - low_52w)) * 100 if high_52w > low_52w else 50
            
            return {
                'current_price': current_price,
                'change_1d': change_1d,
                'change_5d': change_5d,
                'change_1m': change_1m,
                'volume_spike': volume_spike,
                'price_vs_sma20': price_vs_sma20,
                'price_vs_sma50': price_vs_sma50,
                'rsi': current_rsi,
                'volatility': volatility,
                'range_position': range_position,
                'avg_volume': avg_volume_20d
            }
        except Exception as e:
            print(f"Error getting momentum for {symbol}: {e}")
            return None
    
    def get_fundamental_data(self, symbol):
        """Get basic fundamental data"""
        try:
            def fetch_info():
                stock = yf.Ticker(symbol)
                return stock.info
            
            info = self.retry_with_backoff(fetch_info)
            if info is None:
                return None
            
            return {
                'company_name': info.get('longName', symbol),
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'market_cap': info.get('marketCap', 0),
                'beta': info.get('beta', 1.0),
                'pe_ratio': info.get('trailingPE', None)
            }
        except Exception as e:
            print(f"Error getting fundamentals for {symbol}: {e}")
            return None
    
    def calculate_squeeze_score(self, short_data, momentum_data):
        """Calculate short squeeze probability score (0-100)"""
        if not short_data or not momentum_data:
            return 0
        
        score = 0
        
        # 1. Short Interest (30 points max)
        short_pct = short_data['short_percent_float']
        if short_pct > 30:
            score += 30
        elif short_pct > 20:
            score += 25
        elif short_pct > 15:
            score += 20
        elif short_pct > 10:
            score += 15
        elif short_pct > 5:
            score += 10
        
        # 2. Days to Cover / Short Ratio (20 points max)
        days_to_cover = short_data['short_ratio']
        if days_to_cover > 10:
            score += 20
        elif days_to_cover > 7:
            score += 17
        elif days_to_cover > 5:
            score += 14
        elif days_to_cover > 3:
            score += 10
        elif days_to_cover > 1:
            score += 5
        
        # 3. Price Momentum (25 points max)
        # Starting to move up is key
        if momentum_data['change_5d'] > 10:
            score += 15
        elif momentum_data['change_5d'] > 5:
            score += 12
        elif momentum_data['change_5d'] > 2:
            score += 8
        elif momentum_data['change_5d'] > 0:
            score += 5
        
        # Above moving averages
        if momentum_data['price_vs_sma20'] > 5:
            score += 10
        elif momentum_data['price_vs_sma20'] > 0:
            score += 5
        
        # 4. Volume Spike (15 points max)
        if momentum_data['volume_spike'] > 3:
            score += 15
        elif momentum_data['volume_spike'] > 2:
            score += 12
        elif momentum_data['volume_spike'] > 1.5:
            score += 8
        elif momentum_data['volume_spike'] > 1.2:
            score += 5
        
        # 5. RSI - Breaking out (10 points max)
        rsi = momentum_data['rsi']
        if 60 < rsi < 75:  # Bullish but not overbought
            score += 10
        elif 55 < rsi < 80:
            score += 7
        elif 50 < rsi < 85:
            score += 4
        
        return min(score, 100)
    
    def identify_squeeze_triggers(self, short_data, momentum_data):
        """Identify specific squeeze triggers"""
        triggers = []
        
        if not short_data or not momentum_data:
            return triggers
        
        # High short interest
        if short_data['short_percent_float'] > 30:
            triggers.append('🔥 EXTREME Short Interest (>30%)')
        elif short_data['short_percent_float'] > 20:
            triggers.append('⚠️ High Short Interest (>20%)')
        
        # High days to cover
        if short_data['short_ratio'] > 7:
            triggers.append(f"📅 High Days to Cover ({short_data['short_ratio']:.1f} days)")
        
        # Short interest increasing
        if short_data['short_interest_change'] > 10:
            triggers.append('📈 Short Interest Rising')
        
        # Price breaking out
        if momentum_data['change_5d'] > 10:
            triggers.append('🚀 Strong 5-Day Rally')
        
        # Volume explosion
        if momentum_data['volume_spike'] > 2.5:
            triggers.append('📊 Volume Explosion')
        
        # Above key moving averages
        if momentum_data['price_vs_sma20'] > 0 and momentum_data['price_vs_sma50'] > 0:
            triggers.append('✅ Above SMA20 & SMA50')
        
        # RSI momentum
        if 60 < momentum_data['rsi'] < 75:
            triggers.append('💪 Strong RSI Momentum')
        
        # Low float advantage
        if short_data['float_shares'] > 0 and short_data['float_shares'] < 50000000:  # <50M
            triggers.append('🎯 Low Float (<50M)')
        
        return triggers
    
    def analyze_stock(self, symbol):
        """Complete short squeeze analysis for a stock"""
        # Silent analysis - no print spam
        
        # Get all data
        short_data = self.get_short_interest_data(symbol)
        momentum_data = self.get_price_momentum(symbol)
        fundamental_data = self.get_fundamental_data(symbol)
        
        if not short_data or not momentum_data or not fundamental_data:
            return None
        
        # Must have minimum short interest to be considered
        if short_data['short_percent_float'] < 5:
            return None
        
        # Calculate squeeze score
        squeeze_score = self.calculate_squeeze_score(short_data, momentum_data)
        
        # Identify triggers
        triggers = self.identify_squeeze_triggers(short_data, momentum_data)
        
        # Combine all data
        result = {
            'symbol': symbol,
            'company_name': fundamental_data['company_name'],
            'sector': fundamental_data['sector'],
            'market_cap': fundamental_data['market_cap'],
            'current_price': momentum_data['current_price'],
            'squeeze_score': squeeze_score,
            'short_percent_float': short_data['short_percent_float'],
            'days_to_cover': short_data['short_ratio'],
            'short_interest_change': short_data['short_interest_change'],
            'change_1d': momentum_data['change_1d'],
            'change_5d': momentum_data['change_5d'],
            'change_1m': momentum_data['change_1m'],
            'volume_spike': momentum_data['volume_spike'],
            'avg_volume': momentum_data['avg_volume'],
            'rsi': momentum_data['rsi'],
            'price_vs_sma20': momentum_data['price_vs_sma20'],
            'volatility': momentum_data['volatility'],
            'triggers': triggers,
            'num_triggers': len(triggers)
        }
        
        return result
    
    def scan_market(self, min_squeeze_score=50, max_workers=1):
        """Scan market for short squeeze candidates - with batch rate limiting"""
        results = []
        
        print(f"\n🔍 Scanning {len(self.symbols)} stocks/ETFs for short squeeze setups...")
        print(f"📊 Filter: Squeeze Score >= {min_squeeze_score}")
        print(f"⚡ Batch processing: {self.batch_size} stocks, then {self.batch_delay}s wait")
        print(f"⏱️  Estimated time: {len(self.symbols) * 0.5 / 60:.1f} minutes")
        print("-" * 100)
        
        # Process in batches to avoid rate limiting
        total = len(self.symbols)
        for batch_start in range(0, total, self.batch_size):
            batch_end = min(batch_start + self.batch_size, total)
            batch_symbols = self.symbols[batch_start:batch_end]
            
            # Process batch with limited parallelism
            if max_workers > 1:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(self.analyze_stock, symbol): symbol 
                              for symbol in batch_symbols}
                    
                    for future in as_completed(futures):
                        result = future.result()
                        if result and result['squeeze_score'] >= min_squeeze_score:
                            results.append(result)
            else:
                # Sequential processing (most reliable for rate limits)
                for symbol in batch_symbols:
                    result = self.analyze_stock(symbol)
                    if result and result['squeeze_score'] >= min_squeeze_score:
                        results.append(result)
            
            # Progress update
            completed = batch_end
            print(f"Progress: {completed}/{total} stocks analyzed ({completed/total*100:.1f}%) - Found {len(results)} candidates")
            
            # Wait between batches (except for last batch)
            if batch_end < total:
                print(f"⏸️  Waiting {self.batch_delay}s to avoid rate limits...")
                time.sleep(self.batch_delay)
        
        print(f"\n✅ Scan complete! Found {len(results)} candidates with score >= {min_squeeze_score}")
        
        # Convert to DataFrame and sort by squeeze score
        df = pd.DataFrame(results)
        
        if not df.empty:
            df = df.sort_values('squeeze_score', ascending=False)
        
        return df
    
    def display_results(self, df, top_n=10):
        """Display top short squeeze candidates"""
        if df.empty:
            print("\nNo short squeeze candidates found matching criteria.")
            return
        
        print(f"\n{'='*100}")
        print(f"🏆 TOP {min(top_n, len(df))} SHORT SQUEEZE CANDIDATES (from {len(df)} qualified)")
        print(f"💰 Stocks with potential for 20-30%+ explosive moves")
        print(f"{'='*100}\n")
        
        for idx, row in df.head(top_n).iterrows():
            # Risk/Opportunity indicator
            if row['squeeze_score'] > 80:
                level_icon = "🔥🔥🔥"
                level = "EXTREME"
            elif row['squeeze_score'] > 70:
                level_icon = "🔥🔥"
                level = "HIGH"
            elif row['squeeze_score'] > 60:
                level_icon = "🔥"
                level = "MODERATE"
            else:
                level_icon = "⚠️"
                level = "DEVELOPING"
            
            print(f"\n{level_icon} {row['symbol']} - {row['company_name']}")
            print(f"Squeeze Level: {level} (Score: {row['squeeze_score']:.0f}/100)")
            print(f"{'─'*100}")
            
            # Price and momentum
            momentum_icon = "🚀" if row['change_5d'] > 5 else "📈" if row['change_5d'] > 0 else "📉"
            print(f"{momentum_icon} Price: ${row['current_price']:.2f}")
            print(f"   1D/5D/1M: {row['change_1d']:+.1f}% / {row['change_5d']:+.1f}% / {row['change_1m']:+.1f}%")
            
            # Short interest metrics
            print(f"\n📊 SHORT METRICS:")
            print(f"   Short Interest: {row['short_percent_float']:.1f}% of float")
            print(f"   Days to Cover: {row['days_to_cover']:.1f} days")
            if row['short_interest_change'] != 0:
                si_change_icon = "📈" if row['short_interest_change'] > 0 else "📉"
                print(f"   {si_change_icon} SI Change: {row['short_interest_change']:+.1f}%")
            
            # Technical indicators
            print(f"\n📈 TECHNICAL:")
            vol_icon = "🔊" if row['volume_spike'] > 2 else "🔉" if row['volume_spike'] > 1.5 else ""
            print(f"   {vol_icon} Volume Spike: {row['volume_spike']:.1f}x average")
            print(f"   RSI: {row['rsi']:.1f}")
            print(f"   vs SMA20: {row['price_vs_sma20']:+.1f}%")
            print(f"   Volatility: {row['volatility']:.1f}%")
            
            # Market cap
            print(f"\n💰 Market Cap: ${row['market_cap']/1e9:.2f}B")
            print(f"📍 Sector: {row['sector']}")
            
            # Squeeze triggers
            if row['triggers']:
                print(f"\n⚡ SQUEEZE TRIGGERS ({row['num_triggers']}):")
                for trigger in row['triggers']:
                    print(f"   • {trigger}")
            
            # Trading strategy hint
            print(f"\n💡 STRATEGY:")
            if row['squeeze_score'] > 70 and row['change_5d'] > 5:
                print(f"   → ALREADY SQUEEZING - Consider momentum plays or wait for pullback")
            elif row['squeeze_score'] > 70:
                print(f"   → HIGH SETUP - Watch for catalyst/volume breakout")
            elif row['change_5d'] > 0 and row['volume_spike'] > 1.5:
                print(f"   → BUILDING - Early stage, accumulation phase")
            else:
                print(f"   → COILED - Waiting for trigger event")
        
        # Summary statistics
        print(f"\n{'='*100}")
        print("SUMMARY STATISTICS")
        print(f"{'='*100}")
        print(f"Total candidates: {len(df)}")
        print(f"Average Squeeze Score: {df['squeeze_score'].mean():.1f}")
        print(f"Average Short Interest: {df['short_percent_float'].mean():.1f}%")
        print(f"Average 5-Day Change: {df['change_5d'].mean():+.1f}%")
        
        # Categorize by squeeze stage
        print(f"\nSTAGE BREAKDOWN:")
        extreme = len(df[df['squeeze_score'] > 80])
        high = len(df[(df['squeeze_score'] > 70) & (df['squeeze_score'] <= 80)])
        moderate = len(df[(df['squeeze_score'] > 60) & (df['squeeze_score'] <= 70)])
        developing = len(df[df['squeeze_score'] <= 60])
        
        print(f"  🔥🔥🔥 EXTREME (>80): {extreme}")
        print(f"  🔥🔥 HIGH (70-80): {high}")
        print(f"  🔥 MODERATE (60-70): {moderate}")
        print(f"  ⚠️ DEVELOPING (<60): {developing}")
    
    def save_results(self, df, filename=None):
        """Save results to CSV"""
        if filename is None:
            filename = os.path.join(DATA_DIR, 'short_squeeze_candidates.csv')
        if not df.empty:
            # Convert triggers list to string
            df_copy = df.copy()
            df_copy['triggers'] = df_copy['triggers'].apply(lambda x: ' | '.join(x) if x else '')
            
            df_copy.to_csv(filename, index=False)
            print(f"\n💾 Results saved to {filename}")


def main():
    """Main execution function"""
    print("="*100)
    print("SHORT SQUEEZE SCANNER")
    print("Identifying stocks with 20-30%+ explosive move potential")
    print("="*100)
    
    scanner = ShortSqueezeScanner()
    
    # Scan market
    # Adjust min_squeeze_score based on how aggressive you want to be:
    # 80+ = Extreme setups (very high probability)
    # 70+ = Strong setups
    # 60+ = Good setups
    # 50+ = Developing setups
    
    results_df = scanner.scan_market(
        min_squeeze_score=50,  # Minimum squeeze score
        max_workers=15
    )
    
    # Display results
    scanner.display_results(results_df, top_n=25)
    
    # Save to CSV
    scanner.save_results(results_df)
    
    print("\n" + "="*100)
    print("⚠️  RISK WARNING:")
    print("Short squeezes are HIGHLY VOLATILE and RISKY!")
    print("• Use proper position sizing (1-2% of portfolio max)")
    print("• Set stop losses (15-20% below entry)")
    print("• Take profits in stages (scale out)")
    print("• Don't chase - wait for entry signals")
    print("• These can reverse violently")
    print("="*100)


if __name__ == "__main__":
    main()
