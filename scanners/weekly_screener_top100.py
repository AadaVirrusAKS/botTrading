import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import warnings
from config.master_stock_list import get_master_stock_list
warnings.filterwarnings('ignore')

# -----------------------------
# TOP 100 STOCKS WEEKLY SCREENER
# Price Range: $50 - $200
# -----------------------------

class WeeklyStockScreener:
    def __init__(self, initial_capital=10000):
        self.initial_capital = initial_capital
        self.risk_reward_ratio = 3
        self.risk_per_trade = 0.02
        self.min_price = 50  # Minimum stock price
        self.max_price = 200  # Maximum stock price
        
        self.stock_universe = get_master_stock_list(include_etfs=False)
    
    def calculate_indicators(self, data):
        """Calculate technical indicators for weekly data"""
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
        
        # Volume analysis
        data['Avg_Volume'] = data['Volume'].rolling(window=20).mean()
        data['Volume_Ratio'] = data['Volume'] / data['Avg_Volume']
        
        return data
    
    def score_stock(self, row, ticker_info):
        """Score a stock based on multiple criteria"""
        score = 0
        reasons = []
        
        # Price above EMAs (Strong trend)
        if row['Close'] > row['50EMA']:
            score += 2
            reasons.append("Above 50 EMA")
        elif row['Close'] > row['50EMA'] * 0.98:  # Within 2%
            score += 1
            reasons.append("Near 50 EMA")
            
        if row['Close'] > row['200EMA']:
            score += 2
            reasons.append("Above 200 EMA")
            
        if row['50EMA'] > row['200EMA']:
            score += 3
            reasons.append("Golden Cross")
        elif row['50EMA'] > row['200EMA'] * 0.98:  # Nearly golden cross
            score += 1
            reasons.append("Approaching Golden Cross")
        
        # RSI in optimal range (more flexible)
        if 40 <= row['RSI'] <= 70:
            score += 3
            reasons.append(f"RSI optimal ({row['RSI']:.1f})")
        elif 30 <= row['RSI'] < 40:
            score += 2
            reasons.append(f"RSI recovering ({row['RSI']:.1f})")
        elif 70 < row['RSI'] <= 75:
            score += 1
            reasons.append(f"RSI strong ({row['RSI']:.1f})")
        
        # Volume confirmation
        if row['Volume_Ratio'] > 1.2:
            score += 2
            reasons.append("High volume")
        
        # Momentum (recent price action)
        price_change_5d = ((row['Close'] - row['Close_5d_ago']) / row['Close_5d_ago'] * 100) if 'Close_5d_ago' in row else 0
        if price_change_5d > 0:
            score += 1
            reasons.append(f"5-day gain ({price_change_5d:.1f}%)")
        
        # Risk/Reward ratio
        stop_loss = row['Close'] - row['ATR'] * 1.5
        take_profit = row['Close'] + (row['Close'] - stop_loss) * self.risk_reward_ratio
        risk_reward = (take_profit - row['Close']) / (row['Close'] - stop_loss)
        if risk_reward >= 3:
            score += 1
            reasons.append(f"R/R: 1:{risk_reward:.1f}")
        
        return score, reasons
    
    def analyze_stock(self, ticker):
        """Analyze a single stock"""
        try:
            # Download daily data for better analysis
            stock = yf.Ticker(ticker)
            data = stock.history(period='1y', interval='1d')  # Changed to daily
            
            if len(data) < 200:
                return None
            
            # Calculate indicators
            data = self.calculate_indicators(data)
            if data is None:
                return None
            
            # Get latest data
            latest = data.iloc[-1]
            
            # PRICE FILTER: Only stocks between $50-$200
            if latest['Close'] < 50 or latest['Close'] > 200:
                return None
            
            # Add 5-day ago close for momentum
            if len(data) >= 5:
                data['Close_5d_ago'] = data['Close'].shift(5)
                latest = data.iloc[-1]
            
            # Check if all indicators are valid
            if pd.isna(latest['50EMA']) or pd.isna(latest['200EMA']) or pd.isna(latest['RSI']):
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
            
            # Score the stock
            score, reasons = self.score_stock(latest, info)
            
            # Calculate trade parameters
            stop_loss = latest['Close'] - latest['ATR'] * 1.5
            take_profit = latest['Close'] + (latest['Close'] - stop_loss) * self.risk_reward_ratio
            
            # Calculate position size
            risk_amount = self.initial_capital * self.risk_per_trade
            risk_per_share = latest['Close'] - stop_loss
            shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
            position_value = shares * latest['Close']
            
            # Calculate potential profit
            potential_profit = (take_profit - latest['Close']) * shares
            
            return {
                'Ticker': ticker,
                'Company': company_name,
                'Sector': sector,
                'Score': score,
                'Current Price': latest['Close'],
                '50 EMA': latest['50EMA'],
                '200 EMA': latest['200EMA'],
                'RSI': latest['RSI'],
                'Volume': latest['Volume'],
                'Avg Volume': latest['Avg_Volume'],
                'Volume Ratio': latest['Volume_Ratio'],
                'Stop Loss': stop_loss,
                'Take Profit': take_profit,
                'Shares': shares,
                'Position Value': position_value,
                'Potential Profit': potential_profit,
                'Risk Amount': risk_amount,
                'Reasons': ', '.join(reasons),
                'Market Cap': market_cap
            }
            
        except Exception as e:
            print(f"Error analyzing {ticker}: {str(e)}")
            return None
    
    def screen_all_stocks(self):
        """Screen all stocks in the universe"""
        print("=" * 80)
        print("WEEKLY STOCK SCREENER - TOP 100 US COMPANIES")
        print(f"Price Filter: ${self.min_price} - ${self.max_price}")
        print(f"Screening Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        print(f"\n📊 Analyzing {len(self.stock_universe)} stocks (Price Range: ${self.min_price}-${self.max_price})...")
        print("This may take a few minutes...\n")
        
        results = []
        for i, ticker in enumerate(self.stock_universe, 1):
            print(f"[{i}/{len(self.stock_universe)}] Analyzing {ticker}...", end='\r')
            result = self.analyze_stock(ticker)
            if result and result['Score'] >= 3:  # Keep stocks with score of 3 or higher
                results.append(result)
        
        print("\n")
        
        if not results:
            print("⚠️ No stocks met the strict screening criteria (score >= 3)")
            print("Lowering threshold to show best available options...\n")
            
            # Try again with lower threshold
            results = []
            for ticker in self.stock_universe:
                result = self.analyze_stock(ticker)
                if result and result['Score'] > 0:
                    results.append(result)
            
            if not results:
                print("❌ No stocks have any positive scores")
                return pd.DataFrame(), pd.DataFrame()
        
        # Convert to DataFrame and sort by score
        df = pd.DataFrame(results)
        df = df.sort_values('Score', ascending=False)
        
        # Get top 5
        top_5 = df.head(5)
        
        return df, top_5
    
    def scan_market(self, min_score=3, max_workers=None):
        """
        Scan market with filtering - compatible with app/web_app.py
        
        Parameters:
        - min_score: Minimum score threshold (default 3)
        - max_workers: Not used (for API compatibility)
        
        Returns:
        - DataFrame of results filtered by min_score
        """
        print(f"\n🔄 Weekly Scanner - Filtering stocks with score >= {min_score}")
        
        results = []
        for i, ticker in enumerate(self.stock_universe, 1):
            print(f"[{i}/{len(self.stock_universe)}] Analyzing {ticker}...", end='\r')
            result = self.analyze_stock(ticker)
            if result and result['Score'] >= min_score:
                results.append(result)
        
        print("\n")
        
        if not results:
            print(f"⚠️ No stocks met criteria (score >= {min_score})")
            # Return empty DataFrame with proper structure
            return pd.DataFrame(columns=['Ticker', 'Score', 'Current Price', 'Entry', 'Stop Loss', 
                                        'Take Profit', 'Shares', 'Position Value', 'Potential Profit', 
                                        'Risk Amount', 'Reasons', 'Market Cap'])
        
        # Convert to DataFrame and sort by score
        df = pd.DataFrame(results)
        df = df.sort_values('Score', ascending=False)
        
        # Normalize column names for web API compatibility
        df = df.rename(columns={
            'Ticker': 'symbol',
            'Company': 'company_name',
            'Sector': 'sector',
            'Score': 'score',
            'Current Price': 'price',
            'Stop Loss': 'stop_loss',
            'Take Profit': 'target',
            'Shares': 'shares',
            'Position Value': 'position_value',
            'Potential Profit': 'potential_profit',
            'Risk Amount': 'risk_amount',
            'Reasons': 'reasons',
            'Market Cap': 'market_cap',
            'RSI': 'rsi',
            'Volume': 'volume',
            'Avg Volume': 'avg_volume',
            'Volume Ratio': 'volume_ratio',
            '50 EMA': 'ema_50',
            '200 EMA': 'ema_200'
        })
        
        # Add missing fields for table display
        df['current_price'] = df['price']
        df['entry_price'] = df['price']
        
        # Determine direction based on trend indicators
        # BULLISH: Price above 50 EMA AND 50 EMA > 200 EMA (golden cross)
        # BEARISH: Price below both EMAs or death cross
        df['direction'] = df.apply(lambda row: 
            'BULLISH' if (row['price'] > row['ema_50'] and row['ema_50'] > row['ema_200']) 
            else 'BEARISH' if (row['price'] < row['ema_50'] and row['ema_50'] < row['ema_200'])
            else 'NEUTRAL', axis=1)
        
        print(f"✅ Found {len(df)} stocks with score >= {min_score}")
        return df
    
    def display_results(self, all_stocks, top_5):
        """Display screening results"""
        print(f"\n{'=' * 80}")
        print(f"SCREENING COMPLETE - {len(all_stocks)} stocks qualified")
        print(f"{'=' * 80}")
        
        print(f"\n🏆 TOP 5 WEEKLY TRADE OPPORTUNITIES:\n")
        
        for idx, row in top_5.iterrows():
            print(f"\n{'-' * 80}")
            print(f"#{top_5.index.get_loc(idx) + 1}. {row['Ticker']} - {row['Company']}")
            print(f"{'-' * 80}")
            print(f"Score: {row['Score']}/13 ⭐")
            print(f"Sector: {row['Sector']}")
            print(f"Current Price: ${row['Current Price']:.2f}")
            print(f"50 EMA: ${row['50 EMA']:.2f} | 200 EMA: ${row['200 EMA']:.2f}")
            print(f"RSI: {row['RSI']:.2f}")
            print(f"\n📈 TRADE SETUP:")
            print(f"  Entry: ${row['Current Price']:.2f}")
            print(f"  Stop Loss: ${row['Stop Loss']:.2f} ({((row['Stop Loss']/row['Current Price']-1)*100):.1f}%)")
            print(f"  Take Profit: ${row['Take Profit']:.2f} ({((row['Take Profit']/row['Current Price']-1)*100):.1f}%)")
            print(f"  Shares: {row['Shares']:.0f}")
            print(f"  Position Value: ${row['Position Value']:.2f}")
            print(f"  Risk: ${row['Risk Amount']:.2f} (2% of capital)")
            print(f"  Potential Profit: ${row['Potential Profit']:.2f}")
            print(f"\n✅ Why this trade: {row['Reasons']}")
        
        print(f"\n{'=' * 80}")
        print("HONORABLE MENTIONS (Ranked 6-10):")
        print(f"{'=' * 80}\n")
        
        if len(all_stocks) > 5:
            honorable = all_stocks.iloc[5:10]
            for idx, row in honorable.iterrows():
                print(f"{honorable.index.get_loc(idx) + 6}. {row['Ticker']} ({row['Company'][:30]}) - Score: {row['Score']}/13 - ${row['Current Price']:.2f}")
        
        print(f"\n{'=' * 80}")
        print("SUMMARY STATISTICS:")
        print(f"{'=' * 80}")
        print(f"Total Stocks Analyzed: {len(self.stock_universe)}")
        print(f"Stocks Meeting Criteria: {len(all_stocks)}")
        print(f"Average Score (Top 5): {top_5['Score'].mean():.1f}/13")
        print(f"Total Capital Required (Top 5): ${top_5['Position Value'].sum():.2f}")
        print(f"Total Potential Profit (Top 5): ${top_5['Potential Profit'].sum():.2f}")
        print(f"\nSector Breakdown (Top 5):")
        sector_counts = top_5['Sector'].value_counts()
        for sector, count in sector_counts.items():
            print(f"  - {sector}: {count}")
        
        print(f"\n{'=' * 80}")
    
    def save_to_excel(self, all_stocks, top_5):
        """Save results to Excel"""
        filename = f"weekly_stock_screener_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # Top 5 trades
            top_5_export = top_5[[
                'Ticker', 'Company', 'Sector', 'Score', 'Current Price',
                '50 EMA', '200 EMA', 'RSI', 'Stop Loss', 'Take Profit',
                'Shares', 'Position Value', 'Potential Profit', 'Reasons'
            ]].copy()
            top_5_export.to_excel(writer, sheet_name='Top 5 Trades', index=False)
            
            # All qualified stocks
            all_export = all_stocks[[
                'Ticker', 'Company', 'Sector', 'Score', 'Current Price',
                'RSI', 'Stop Loss', 'Take Profit', 'Potential Profit'
            ]].copy()
            all_export.to_excel(writer, sheet_name='All Qualified Stocks', index=False)
            
            # Summary stats
            summary_data = {
                'Metric': [
                    'Screening Date',
                    'Stocks Analyzed',
                    'Stocks Qualified',
                    'Top 5 Average Score',
                    'Total Capital Required (Top 5)',
                    'Total Potential Profit (Top 5)',
                    'Portfolio Risk (Top 5)',
                ],
                'Value': [
                    datetime.now().strftime('%Y-%m-%d'),
                    len(self.stock_universe),
                    len(all_stocks),
                    f"{top_5['Score'].mean():.1f}/13",
                    f"${top_5['Position Value'].sum():.2f}",
                    f"${top_5['Potential Profit'].sum():.2f}",
                    f"${top_5['Risk Amount'].sum():.2f} ({len(top_5) * 2}%)",
                ]
            }
            summary_df = pd.DataFrame(summary_data)
            summary_df.to_excel(writer, sheet_name='Summary', index=False)
        
        print(f"\n✅ Results saved to: {filename}")
        return filename

# Run the screener
if __name__ == "__main__":
    screener = WeeklyStockScreener(initial_capital=10000)
    all_stocks, top_5 = screener.screen_all_stocks()
    
    if not top_5.empty:
        screener.display_results(all_stocks, top_5)
        screener.save_to_excel(all_stocks, top_5)
    else:
        print("\n⚠️ Unable to find any qualifying trades this week.")
        print("Market conditions may not be favorable for the strategy.")
        print("Try again next week or adjust criteria.")
