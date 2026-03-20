"""
Beaten Down Quality Stock Scanner
Finds stocks and ETFs with strong fundamentals that have been beaten down by market crashes
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import warnings
from config.master_stock_list import get_master_stock_list
from config import PROJECT_ROOT, DATA_DIR
warnings.filterwarnings('ignore')

# Use centralized caching layer to avoid Yahoo rate limiting
try:
    from services.market_data import (
        cached_get_history, cached_get_ticker_info,
        prewarm_history_cache, _is_globally_rate_limited
    )
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False

class BeatenDownQualityScanner:
    def __init__(self):
        # S&P 500 components and popular ETFs
        self.symbols = self.get_stock_universe()
        
    def get_stock_universe(self):
        """Get a comprehensive list of stocks and ETFs to scan"""
        return get_master_stock_list(include_etfs=True)
    
    def get_price_metrics(self, symbol):
        """Calculate price decline metrics"""
        try:
            if _USE_CACHED:
                if _is_globally_rate_limited():
                    return None
                hist = cached_get_history(symbol, period='1y', interval='1d')
            else:
                stock = yf.Ticker(symbol)
                hist = stock.history(period='1y')

            if hist is None or hist.empty or len(hist) < 50:
                return None
            
            current_price = hist['Close'].iloc[-1]
            
            # Calculate various metrics
            high_52w = hist['Close'].max()
            low_52w = hist['Close'].min()
            avg_volume_3m = hist['Volume'].tail(63).mean()
            
            # Drawdown from 52-week high
            drawdown_from_high = ((current_price - high_52w) / high_52w) * 100
            
            # Recent decline (1 month, 3 months, 6 months)
            decline_1m = ((current_price - hist['Close'].iloc[-21]) / hist['Close'].iloc[-21]) * 100 if len(hist) >= 21 else 0
            decline_3m = ((current_price - hist['Close'].iloc[-63]) / hist['Close'].iloc[-63]) * 100 if len(hist) >= 63 else 0
            decline_6m = ((current_price - hist['Close'].iloc[-126]) / hist['Close'].iloc[-126]) * 100 if len(hist) >= 126 else 0
            
            # Volatility (standard deviation of returns)
            returns = hist['Close'].pct_change().dropna()
            volatility = returns.std() * np.sqrt(252) * 100  # Annualized
            
            # RSI calculation
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1] if not rsi.empty and len(rsi) > 0 else 50
            
            # Current volume
            current_volume = hist['Volume'].iloc[-1]
            
            return {
                'current_price': current_price,
                'high_52w': high_52w,
                'low_52w': low_52w,
                'drawdown_from_high': drawdown_from_high,
                'decline_1m': decline_1m,
                'decline_3m': decline_3m,
                'decline_6m': decline_6m,
                'avg_volume': avg_volume_3m,
                'volume': current_volume,
                'rsi': current_rsi,
                'volatility': volatility
            }
        except Exception as e:
            print(f"Error getting price metrics for {symbol}: {e}")
            return None
    
    def get_fundamental_metrics(self, symbol):
        """Get fundamental analysis metrics"""
        try:
            if _USE_CACHED:
                if _is_globally_rate_limited():
                    return None
                info = cached_get_ticker_info(symbol) or {}
            else:
                stock = yf.Ticker(symbol)
                info = stock.info
            
            # Extract key fundamental metrics
            fundamentals = {
                'market_cap': info.get('marketCap', 0),
                'pe_ratio': info.get('trailingPE', None),
                'forward_pe': info.get('forwardPE', None),
                'peg_ratio': info.get('pegRatio', None),
                'price_to_book': info.get('priceToBook', None),
                'debt_to_equity': info.get('debtToEquity', None),
                'current_ratio': info.get('currentRatio', None),
                'roe': info.get('returnOnEquity', None),
                'roa': info.get('returnOnAssets', None),
                'profit_margin': info.get('profitMargins', None),
                'operating_margin': info.get('operatingMargins', None),
                'revenue_growth': info.get('revenueGrowth', None),
                'earnings_growth': info.get('earningsGrowth', None),
                'free_cash_flow': info.get('freeCashflow', 0),
                'operating_cash_flow': info.get('operatingCashflow', 0),
                'quick_ratio': info.get('quickRatio', None),
                'sector': info.get('sector', 'N/A'),
                'industry': info.get('industry', 'N/A'),
                'company_name': info.get('longName', symbol)
            }
            
            return fundamentals
        except Exception as e:
            print(f"Error getting fundamentals for {symbol}: {e}")
            return None
    
    def calculate_quality_score(self, fundamentals):
        """Calculate a quality score based on fundamentals (0-100)"""
        if not fundamentals:
            return 0
        
        score = 0
        max_score = 0
        
        # Profitability metrics (40 points)
        if fundamentals['roe'] is not None:
            max_score += 10
            if fundamentals['roe'] > 0.15:  # ROE > 15%
                score += 10
            elif fundamentals['roe'] > 0.10:
                score += 7
            elif fundamentals['roe'] > 0.05:
                score += 4
        
        if fundamentals['profit_margin'] is not None:
            max_score += 10
            if fundamentals['profit_margin'] > 0.15:
                score += 10
            elif fundamentals['profit_margin'] > 0.10:
                score += 7
            elif fundamentals['profit_margin'] > 0.05:
                score += 4
        
        if fundamentals['operating_margin'] is not None:
            max_score += 10
            if fundamentals['operating_margin'] > 0.15:
                score += 10
            elif fundamentals['operating_margin'] > 0.10:
                score += 7
            elif fundamentals['operating_margin'] > 0.05:
                score += 4
        
        if fundamentals['roa'] is not None:
            max_score += 10
            if fundamentals['roa'] > 0.10:
                score += 10
            elif fundamentals['roa'] > 0.05:
                score += 6
            elif fundamentals['roa'] > 0.02:
                score += 3
        
        # Financial health (30 points)
        if fundamentals['current_ratio'] is not None:
            max_score += 10
            if fundamentals['current_ratio'] > 2.0:
                score += 10
            elif fundamentals['current_ratio'] > 1.5:
                score += 7
            elif fundamentals['current_ratio'] > 1.0:
                score += 4
        
        if fundamentals['debt_to_equity'] is not None:
            max_score += 10
            if fundamentals['debt_to_equity'] < 0.5:
                score += 10
            elif fundamentals['debt_to_equity'] < 1.0:
                score += 7
            elif fundamentals['debt_to_equity'] < 2.0:
                score += 4
        
        if fundamentals['free_cash_flow'] > 0:
            max_score += 10
            score += 10
        
        # Growth metrics (20 points)
        if fundamentals['revenue_growth'] is not None:
            max_score += 10
            if fundamentals['revenue_growth'] > 0.15:
                score += 10
            elif fundamentals['revenue_growth'] > 0.10:
                score += 7
            elif fundamentals['revenue_growth'] > 0.05:
                score += 4
        
        if fundamentals['earnings_growth'] is not None:
            max_score += 10
            if fundamentals['earnings_growth'] > 0.15:
                score += 10
            elif fundamentals['earnings_growth'] > 0.10:
                score += 7
            elif fundamentals['earnings_growth'] > 0.05:
                score += 4
        
        # Valuation (10 points)
        if fundamentals['pe_ratio'] is not None and fundamentals['pe_ratio'] > 0:
            max_score += 10
            if fundamentals['pe_ratio'] < 15:
                score += 10
            elif fundamentals['pe_ratio'] < 20:
                score += 7
            elif fundamentals['pe_ratio'] < 30:
                score += 4
        
        # Normalize to 0-100 scale
        if max_score > 0:
            return (score / max_score) * 100
        return 0
    
    def analyze_stock(self, symbol):
        """Complete analysis for a single stock"""
        print(f"Analyzing {symbol}...")
        
        # Get price metrics
        price_metrics = self.get_price_metrics(symbol)
        if not price_metrics:
            return None
        
        # Get fundamentals
        fundamentals = self.get_fundamental_metrics(symbol)
        if not fundamentals:
            return None
        
        # Calculate quality score
        quality_score = self.calculate_quality_score(fundamentals)
        
        # Combine all data
        result = {
            'symbol': symbol,
            'company_name': fundamentals['company_name'],
            'sector': fundamentals['sector'],
            'current_price': price_metrics['current_price'],
            'drawdown_from_high': price_metrics['drawdown_from_high'],
            'decline_1m': price_metrics['decline_1m'],
            'decline_3m': price_metrics['decline_3m'],
            'decline_6m': price_metrics['decline_6m'],
            'quality_score': quality_score,
            'market_cap': fundamentals['market_cap'],
            'pe_ratio': fundamentals['pe_ratio'],
            'forward_pe': fundamentals['forward_pe'],
            'price_to_book': fundamentals['price_to_book'],
            'roe': fundamentals['roe'],
            'profit_margin': fundamentals['profit_margin'],
            'debt_to_equity': fundamentals['debt_to_equity'],
            'current_ratio': fundamentals['current_ratio'],
            'revenue_growth': fundamentals['revenue_growth'],
            'free_cash_flow': fundamentals['free_cash_flow'],
            'avg_volume': price_metrics['avg_volume'],
            'volume': price_metrics['volume'],
            'rsi': price_metrics['rsi'],
            'volatility': price_metrics['volatility']
        }
        
        return result
    
    def scan_market(self, min_drawdown=-5, min_quality_score=30, max_workers=10):
        """
        Scan the market for beaten down quality stocks
        
        Parameters:
        - min_drawdown: Minimum drawdown from 52-week high (negative number, e.g., -5 for 5% down)
        - min_quality_score: Minimum quality score (0-100)
        - max_workers: Number of parallel threads
        """
        results = []
        filtered_out = {'not_down_enough': 0, 'quality_too_low': 0, 'errors': 0}
        
        print(f"\nScanning {len(self.symbols)} stocks and ETFs...")
        print(f"Filters: Drawdown <= {min_drawdown}%, Quality Score >= {min_quality_score}")
        print("-" * 80)
        
        # Use ThreadPoolExecutor for parallel processing
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {executor.submit(self.analyze_stock, symbol): symbol 
                               for symbol in self.symbols}
            
            for future in as_completed(future_to_symbol):
                try:
                    result = future.result()
                    if result:
                        # Apply filters
                        if (result['drawdown_from_high'] <= min_drawdown and 
                            result['quality_score'] >= min_quality_score):
                            results.append(result)
                        else:
                            # Track why filtered out
                            if result['drawdown_from_high'] > min_drawdown:
                                filtered_out['not_down_enough'] += 1
                            if result['quality_score'] < min_quality_score:
                                filtered_out['quality_too_low'] += 1
                except Exception as e:
                    filtered_out['errors'] += 1
        
        print(f"✅ Found {len(results)} stocks matching criteria")
        print(f"📊 Filtered out: {filtered_out['not_down_enough']} (not down enough), {filtered_out['quality_too_low']} (quality too low), {filtered_out['errors']} (errors)")
        
        # Convert to DataFrame and sort
        df = pd.DataFrame(results)
        
        if not df.empty:
            # Calculate growth potential score (2x-10x potential like VERO)
            # Prioritize: small-cap (<$2B), high volatility (>50%), beaten down (>20% from high)
            df['growth_potential'] = 0.0
            
            # Small-cap bonus (higher potential for explosive growth)
            df.loc[df['market_cap'] < 500_000_000, 'growth_potential'] += 30  # <$500M = +30
            df.loc[(df['market_cap'] >= 500_000_000) & (df['market_cap'] < 2_000_000_000), 'growth_potential'] += 20  # $500M-$2B = +20
            df.loc[(df['market_cap'] >= 2_000_000_000) & (df['market_cap'] < 10_000_000_000), 'growth_potential'] += 10  # $2B-$10B = +10
            
            # High volatility bonus (more explosive moves possible)
            df.loc[df['volatility'] > 100, 'growth_potential'] += 20  # >100% volatility = +20
            df.loc[(df['volatility'] >= 60) & (df['volatility'] <= 100), 'growth_potential'] += 15  # 60-100% = +15
            df.loc[(df['volatility'] >= 40) & (df['volatility'] < 60), 'growth_potential'] += 10  # 40-60% = +10
            
            # Beaten down bonus (recovery potential)
            df.loc[df['drawdown_from_high'] <= -40, 'growth_potential'] += 20  # Down >40% = +20
            df.loc[(df['drawdown_from_high'] > -40) & (df['drawdown_from_high'] <= -25), 'growth_potential'] += 15  # Down 25-40% = +15
            df.loc[(df['drawdown_from_high'] > -25) & (df['drawdown_from_high'] <= -15), 'growth_potential'] += 10  # Down 15-25% = +10
            
            # Revenue growth bonus (high growth companies)
            df.loc[df['revenue_growth'] > 50, 'growth_potential'] += 15  # >50% growth = +15
            df.loc[(df['revenue_growth'] >= 25) & (df['revenue_growth'] <= 50), 'growth_potential'] += 10  # 25-50% = +10
            
            # Calculate opportunity score (combination of quality and discount)
            df['opportunity_score'] = (
                df['quality_score'] * 0.4 + 
                (abs(df['drawdown_from_high']) * 2) * 0.3 +
                df['growth_potential'] * 0.3
            )
            
            df = df.sort_values('opportunity_score', ascending=False)
        
        return df
    
    def display_results(self, df, top_n=30):
        """Display the results in a formatted way"""
        if df.empty:
            print("\nNo stocks found matching the criteria.")
            return
        
        print(f"\n{'='*100}")
        print(f"TOP {min(top_n, len(df))} BEATEN DOWN QUALITY STOCKS/ETFs")
        print(f"{'='*100}\n")
        
        for idx, row in df.head(top_n).iterrows():
            print(f"\n{row['symbol']} - {row['company_name']}")
            print(f"Sector: {row['sector']}")
            print(f"{'─'*100}")
            
            # Price metrics
            print(f"Current Price: ${row['current_price']:.2f}")
            print(f"Drawdown from 52W High: {row['drawdown_from_high']:.1f}%")
            print(f"1M/3M/6M Decline: {row['decline_1m']:.1f}% / {row['decline_3m']:.1f}% / {row['decline_6m']:.1f}%")
            
            # Quality metrics
            print(f"\nQuality Score: {row['quality_score']:.1f}/100")
            print(f"Growth Potential: {row['growth_potential']:.1f}/100 🚀")
            print(f"Opportunity Score: {row['opportunity_score']:.1f}/100")
            
            # Fundamentals
            print(f"\nMarket Cap: ${row['market_cap']/1e9:.2f}B")
            if row['pe_ratio']:
                print(f"P/E Ratio: {row['pe_ratio']:.2f}", end="")
                if row['forward_pe']:
                    print(f" (Fwd: {row['forward_pe']:.2f})")
                else:
                    print()
            
            if row['price_to_book']:
                print(f"Price/Book: {row['price_to_book']:.2f}")
            
            if row['roe']:
                print(f"ROE: {row['roe']*100:.1f}%")
            
            if row['profit_margin']:
                print(f"Profit Margin: {row['profit_margin']*100:.1f}%")
            
            if row['debt_to_equity'] is not None:
                print(f"Debt/Equity: {row['debt_to_equity']:.2f}")
            
            if row['current_ratio']:
                print(f"Current Ratio: {row['current_ratio']:.2f}")
            
            if row['revenue_growth']:
                print(f"Revenue Growth: {row['revenue_growth']*100:.1f}%")
            
            if row['free_cash_flow']:
                print(f"Free Cash Flow: ${row['free_cash_flow']/1e9:.2f}B")
            
            print(f"Volatility: {row['volatility']:.1f}%")
        
        # Summary statistics
        print(f"\n{'='*100}")
        print("SUMMARY STATISTICS")
        print(f"{'='*100}")
        print(f"Total stocks found: {len(df)}")
        if len(df) > 0:
            print(f"Average Quality Score: {df['quality_score'].mean():.1f}")
            print(f"Average Drawdown: {df['drawdown_from_high'].mean():.1f}%")
            print(f"Average Opportunity Score: {df['opportunity_score'].mean():.1f}")
            print(f"Average Growth Potential: {df['growth_potential'].mean():.1f}")
        
        # Sector breakdown
        print(f"\nSECTOR BREAKDOWN:")
        sector_counts = df['sector'].value_counts()
        for sector, count in sector_counts.items():
            print(f"  {sector}: {count}")
    
    def save_results(self, df, filename=None):
        """Save results to CSV"""
        if filename is None:
            filename = os.path.join(DATA_DIR, 'beaten_down_quality_stocks.csv')
        if not df.empty:
            df.to_csv(filename, index=False)
            print(f"\nResults saved to {filename}")


def main():
    """Main execution function"""
    print("="*100)
    print("BEATEN DOWN QUALITY STOCK SCANNER")
    print("Finding fundamentally strong stocks that have been beaten down by the market")
    print("="*100)
    
    scanner = BeatenDownQualityScanner()
    
    # Scan with relaxed parameters to find more opportunities
    # Adjust these parameters based on your criteria:
    # - min_drawdown: -5 means stock is down at least 5% from 52-week high
    # - min_quality_score: 30 means decent fundamentals (60+ for very high quality)
    
    results_df = scanner.scan_market(
        min_drawdown=-5,       # At least 5% down from 52-week high
        min_quality_score=30,  # Quality score of at least 30/100
        max_workers=10         # Parallel processing threads
    )
    
    # Display results
    scanner.display_results(results_df, top_n=30)
    
    # Save to CSV
    scanner.save_results(results_df)
    
    print("\n" + "="*100)
    print("Analysis complete!")
    print("="*100)


if __name__ == "__main__":
    main()
