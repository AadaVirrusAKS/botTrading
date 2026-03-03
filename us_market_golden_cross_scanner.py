import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
from master_stock_list import get_master_stock_list
warnings.filterwarnings('ignore')

# -----------------------------
# COMPREHENSIVE US MARKET SCANNER
# -----------------------------

class USMarketScanner:
    def __init__(self):
        self.risk_reward_ratio = 5  # 1:5 profit margin
        self.hold_period = 7  # 1-2 weeks (7-14 days)
        
    def get_all_us_stocks(self):
        """Get comprehensive list of US stocks by sector"""
        print("📊 Loading US market stock universe...")
        return {'US Market': get_master_stock_list(include_etfs=True)}
    
    def calculate_indicators(self, data):
        """Calculate technical indicators"""
        if len(data) < 200:
            return None
            
        # EMAs
        data['50EMA'] = data['Close'].ewm(span=50, adjust=False).mean()
        data['200EMA'] = data['Close'].ewm(span=200, adjust=False).mean()
        
        # RSI
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        data['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR
        data['ATR'] = data['Close'].rolling(window=14).std()
        
        return data
    
    def check_golden_cross_and_breakeven(self, data, ticker):
        """Check for Golden Cross and breakeven conditions"""
        latest = data.iloc[-1]
        prev_50 = data['50EMA'].iloc[-2] if len(data) >= 2 else latest['50EMA']
        prev_200 = data['200EMA'].iloc[-2] if len(data) >= 2 else latest['200EMA']
        
        # Check Golden Cross (50 EMA crossed above 200 EMA recently)
        golden_cross = latest['50EMA'] > latest['200EMA']
        recent_golden_cross = golden_cross and (prev_50 <= prev_200 or latest['50EMA'] < latest['200EMA'] * 1.02)
        
        # Check if price is near breakeven (close to both EMAs)
        near_50ema = abs(latest['Close'] - latest['50EMA']) / latest['50EMA'] < 0.05  # Within 5%
        near_200ema = abs(latest['Close'] - latest['200EMA']) / latest['200EMA'] < 0.10  # Within 10%
        
        # Price above both EMAs
        above_both = latest['Close'] > latest['50EMA'] and latest['Close'] > latest['200EMA']
        
        # Check RSI (not overbought)
        rsi_good = 40 <= latest['RSI'] <= 75
        
        return golden_cross, above_both, rsi_good, near_50ema or near_200ema
    
    def calculate_profit_target(self, entry_price, atr):
        """Calculate 1:5 profit margin target"""
        stop_loss = entry_price - (atr * 1.5)
        risk = entry_price - stop_loss
        take_profit = entry_price + (risk * self.risk_reward_ratio)
        
        return stop_loss, take_profit, risk
    
    def analyze_stock(self, ticker):
        """Analyze individual stock"""
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period='1y', interval='1d')
            
            if len(data) < 200:
                return None
            
            # Calculate indicators
            data = self.calculate_indicators(data)
            if data is None:
                return None
            
            latest = data.iloc[-1]
            
            # Check conditions
            golden_cross, above_both, rsi_good, near_breakeven = self.check_golden_cross_and_breakeven(data, ticker)
            
            # Must have golden cross and be above both EMAs
            if not (golden_cross and above_both):
                return None
            
            # Calculate trade setup
            stop_loss, take_profit, risk = self.calculate_profit_target(latest['Close'], latest['ATR'])
            
            # Verify 1:5 ratio
            actual_reward = take_profit - latest['Close']
            actual_ratio = actual_reward / risk if risk > 0 else 0
            
            if actual_ratio < 4.8:  # Allow small variance
                return None
            
            # Get company info
            try:
                info = stock.info
                company_name = info.get('shortName', ticker)
                sector = info.get('sector', 'Unknown')
                market_cap = info.get('marketCap', 0)
            except:
                company_name = ticker
                sector = 'Unknown'
                market_cap = 0
            
            # Calculate score
            score = 0
            if golden_cross:
                score += 5
            if above_both:
                score += 3
            if rsi_good:
                score += 3
            if near_breakeven:
                score += 2
            if latest['Volume'] > data['Volume'].rolling(20).mean().iloc[-1]:
                score += 2
            
            return {
                'Ticker': ticker,
                'Company': company_name,
                'Sector': sector,
                'Score': score,
                'Price': latest['Close'],
                '50 EMA': latest['50EMA'],
                '200 EMA': latest['200EMA'],
                'RSI': latest['RSI'],
                'Volume': latest['Volume'],
                'Entry': latest['Close'],
                'Stop Loss': stop_loss,
                'Take Profit': take_profit,
                'Risk': risk,
                'Reward': actual_reward,
                'Risk/Reward': actual_ratio,
                'Market Cap': market_cap,
                'Golden Cross': golden_cross,
                'Near Breakeven': near_breakeven
            }
            
        except Exception as e:
            return None
    
    def scan_market(self):
        """Scan entire US market by sector"""
        print("=" * 100)
        print("US MARKET SCANNER - GOLDEN CROSS & BREAKEVEN OPPORTUNITIES")
        print(f"Target: Top 5 stocks per sector with 1:5 profit margin")
        print(f"Hold Period: {self.hold_period}-14 days")
        print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 100)
        
        sectors_dict = self.get_all_us_stocks()
        all_results = []
        sector_results = {}
        
        total_stocks = sum(len(tickers) for tickers in sectors_dict.values())
        current = 0
        
        for sector, tickers in sectors_dict.items():
            print(f"\n📊 Scanning {sector} sector ({len(tickers)} stocks)...")
            sector_stocks = []
            
            for ticker in tickers:
                current += 1
                print(f"  [{current}/{total_stocks}] {ticker}...", end='\r')
                
                result = self.analyze_stock(ticker)
                if result:
                    sector_stocks.append(result)
                    all_results.append(result)
            
            # Get top 5 from this sector
            if sector_stocks:
                df = pd.DataFrame(sector_stocks)
                df = df.sort_values('Score', ascending=False)
                top_5 = df.head(5)
                sector_results[sector] = top_5
                print(f"\n  ✅ Found {len(sector_stocks)} qualified stocks in {sector}")
        
        print("\n\n" + "=" * 100)
        print("SCAN COMPLETE")
        print("=" * 100)
        
        return sector_results, all_results

    def display_results(self, sector_results):
        """Display results by sector"""
        print("\n" + "=" * 100)
        print("TOP 5 STOCKS PER SECTOR - GOLDEN CROSS & 1:5 PROFIT OPPORTUNITIES")
        print("=" * 100)
        
        total_opportunities = 0
        
        for sector, stocks_df in sector_results.items():
            if len(stocks_df) == 0:
                continue
                
            print(f"\n{'=' * 100}")
            print(f"🏢 {sector.upper()} - Top {len(stocks_df)} Picks")
            print(f"{'=' * 100}")
            
            for idx, row in stocks_df.iterrows():
                rank = stocks_df.index.get_loc(idx) + 1
                total_opportunities += 1
                
                print(f"\n#{rank}. {row['Ticker']} - {row['Company']}")
                print(f"{'─' * 90}")
                print(f"  Score: {row['Score']}/15")
                print(f"  Current Price: ${row['Price']:.2f}")
                print(f"  50 EMA: ${row['50 EMA']:.2f} | 200 EMA: ${row['200 EMA']:.2f}")
                print(f"  RSI: {row['RSI']:.2f}")
                
                print(f"\n  📈 TRADE SETUP (1-2 Week Hold):")
                print(f"     Entry: ${row['Entry']:.2f}")
                print(f"     Stop Loss: ${row['Stop Loss']:.2f} ({((row['Stop Loss']/row['Entry']-1)*100):.1f}%)")
                print(f"     Take Profit: ${row['Take Profit']:.2f} ({((row['Take Profit']/row['Entry']-1)*100):.1f}%)")
                print(f"     Risk/Reward: 1:{row['Risk/Reward']:.1f}")
                
                print(f"\n  ✅ Qualifications:")
                if row['Golden Cross']:
                    print(f"     • Golden Cross confirmed (50 EMA > 200 EMA)")
                print(f"     • Price above both EMAs")
                if row['Near Breakeven']:
                    print(f"     • Near breakeven levels")
        
        print(f"\n{'=' * 100}")
        print(f"TOTAL OPPORTUNITIES FOUND: {total_opportunities} stocks across {len(sector_results)} sectors")
        print(f"{'=' * 100}")
    
    def save_results(self, sector_results, all_results):
        """Save results to Excel"""
        filename = f"us_market_scan_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # Summary sheet
            summary_data = []
            for sector, stocks_df in sector_results.items():
                if len(stocks_df) > 0:
                    summary_data.append({
                        'Sector': sector,
                        'Stocks Found': len(stocks_df),
                        'Top Pick': stocks_df.iloc[0]['Ticker'],
                        'Top Score': stocks_df.iloc[0]['Score']
                    })
            
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)
            
            # Individual sector sheets
            for sector, stocks_df in sector_results.items():
                if len(stocks_df) > 0:
                    export_df = stocks_df[[
                        'Ticker', 'Company', 'Score', 'Price', '50 EMA', '200 EMA', 
                        'RSI', 'Entry', 'Stop Loss', 'Take Profit', 'Risk/Reward'
                    ]].copy()
                    sheet_name = sector[:31]  # Excel sheet name limit
                    export_df.to_excel(writer, sheet_name=sheet_name, index=False)
            
            # All opportunities
            if all_results:
                all_df = pd.DataFrame(all_results)
                all_df = all_df.sort_values('Score', ascending=False)
                all_df[[
                    'Ticker', 'Company', 'Sector', 'Score', 'Price', 
                    'Entry', 'Stop Loss', 'Take Profit', 'Risk/Reward'
                ]].to_excel(writer, sheet_name='All Opportunities', index=False)
        
        print(f"\n✅ Results saved to: {filename}")
        return filename


# Run the scanner
if __name__ == "__main__":
    scanner = USMarketScanner()
    sector_results, all_results = scanner.scan_market()
    scanner.display_results(sector_results)
    scanner.save_results(sector_results, all_results)
