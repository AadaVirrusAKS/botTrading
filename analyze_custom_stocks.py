import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# List of stocks to analyze (default list when running standalone)
tickers = ['SPCE', 'WKHS', 'DECK', 'AMC', 'NAMX', 'ANTA', 'PEW', 'GEMI', 'DJT',
           'BRBI', 'HIMS', 'LAZR', 'BITO', 'FCHL', 'KALA', 'CNEY', 'UNH', 'RAND', 'CLX', 'LRN','ETH','FIS','PTON']

def analyze_stock(ticker):
    """Analyze a single stock and return results"""
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period='1y')
        
        if len(data) < 50:
            return None
        
        # Get company info
        try:
            info = stock.info
            company_name = info.get('shortName', ticker)
            sector = info.get('sector', 'N/A')
            market_cap = info.get('marketCap', 0)
        except:
            company_name = ticker
            sector = 'N/A'
            market_cap = 0
        
        # Calculate indicators
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
        
        # Get latest data
        latest = data.iloc[-1]
        prev_5d = data.iloc[-6] if len(data) >= 6 else data.iloc[0]
        
        # Calculate metrics
        above_50 = latest['Close'] > latest['50EMA']
        above_200 = latest['Close'] > latest['200EMA']
        golden_cross = latest['50EMA'] > latest['200EMA']
        price_change_5d = ((latest['Close'] - prev_5d['Close']) / prev_5d['Close']) * 100
        
        # Score the trade
        score = 0
        if above_50:
            score += 2
        elif latest['Close'] > latest['50EMA'] * 0.98:
            score += 1
        
        if above_200:
            score += 2
            
        if golden_cross:
            score += 3
        elif latest['50EMA'] > latest['200EMA'] * 0.98:
            score += 1
        
        if 40 <= latest['RSI'] <= 70:
            score += 3
        elif 30 <= latest['RSI'] < 40:
            score += 2
        elif 70 < latest['RSI'] <= 75:
            score += 1
            
        if price_change_5d > 0:
            score += 1
        
        # Calculate trade setup
        stop_loss = latest['Close'] - latest['ATR'] * 1.5
        take_profit = latest['Close'] + (latest['Close'] - stop_loss) * 3
        
        # Position sizing
        initial_capital = 10000
        risk_amount = initial_capital * 0.02
        risk_per_share = latest['Close'] - stop_loss
        shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
        position_value = shares * latest['Close']
        potential_profit = (take_profit - latest['Close']) * shares
        
        # Determine signal
        if score >= 8:
            signal = "STRONG BUY"
            prediction = "Bullish"
        elif score >= 5:
            signal = "MODERATE BUY"
            prediction = "Cautious"
        elif score >= 3:
            signal = "NEUTRAL"
            prediction = "Neutral"
        else:
            signal = "NO TRADE"
            prediction = "Bearish"
        
        return {
            'Ticker': ticker,
            'Company': company_name,
            'Sector': sector,
            'Price': latest['Close'],
            '50 EMA': latest['50EMA'],
            '200 EMA': latest['200EMA'],
            'RSI': latest['RSI'],
            '5D Change %': price_change_5d,
            'Score': score,
            'Signal': signal,
            'Prediction': prediction,
            'Entry': latest['Close'],
            'Stop Loss': stop_loss,
            'Take Profit': take_profit,
            'Shares': shares,
            'Position Value': position_value,
            'Potential Profit': potential_profit,
            'Above 50 EMA': above_50,
            'Above 200 EMA': above_200,
            'Golden Cross': golden_cross,
            'Market Cap': market_cap
        }
        
    except Exception as e:
        print(f"Error analyzing {ticker}: {str(e)}")
        return None


# Only run the analysis automatically if this file is executed directly
if __name__ == "__main__":
    print("=" * 100)
    print("MULTI-STOCK ANALYSIS & PREDICTIONS")
    print(f"Analyzing {len(tickers)} stocks: {', '.join(tickers)}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Analyze all stocks
    print("\nAnalyzing stocks...\n")
    results = []

    for i, ticker in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] Analyzing {ticker}...", end='\r')
        result = analyze_stock(ticker)
        if result:
            results.append(result)

    print("\n" + "=" * 100)

    if not results:
        print("\n❌ No stocks could be analyzed")
    else:
        # Convert to DataFrame
        df = pd.DataFrame(results)
        df = df.sort_values('Score', ascending=False)
        
        # Display summary table
        print(f"\nANALYSIS COMPLETE - {len(df)} stocks analyzed\n")
        print("=" * 100)
        print("RANKING BY SCORE")
        print("=" * 100)
        
        summary_df = df[['Ticker', 'Company', 'Score', 'Signal', 'Price', 'RSI', '5D Change %']].copy()
        print(summary_df.to_string(index=False))
        
        # Show top recommendations
        print("\n" + "=" * 100)
        print("TOP RECOMMENDATIONS")
        print("=" * 100)
        
        top_stocks = df[df['Score'] >= 5].head(5)
        
        if len(top_stocks) == 0:
            print("\n⚠️ No stocks scored above 5 - All are risky or unfavorable setups")
            print("\nBest of the bunch (highest scores):")
            top_stocks = df.head(3)
        
        for idx, row in top_stocks.iterrows():
            print(f"\n{'-' * 100}")
            print(f"#{top_stocks.index.get_loc(idx) + 1}. {row['Ticker']} - {row['Company']}")
            print(f"{'-' * 100}")
            print(f"Score: {row['Score']}/13 - {row['Signal']}")
            print(f"Sector: {row['Sector']}")
            print(f"Current Price: ${row['Price']:.2f}")
            print(f"RSI: {row['RSI']:.1f}")
            print(f"5-Day Change: {row['5D Change %']:+.1f}%")
            
            print(f"\n📊 Technical Status:")
            print(f"  • Above 50 EMA: {'✅ Yes' if row['Above 50 EMA'] else '❌ No'}")
            print(f"  • Above 200 EMA: {'✅ Yes' if row['Above 200 EMA'] else '❌ No'}")
            print(f"  • Golden Cross: {'✅ Yes' if row['Golden Cross'] else '❌ No'}")
            
            print(f"\n📈 Trade Setup:")
            print(f"  Entry: ${row['Entry']:.2f}")
            print(f"  Stop Loss: ${row['Stop Loss']:.2f} ({((row['Stop Loss']/row['Entry']-1)*100):.1f}%)")
            print(f"  Take Profit: ${row['Take Profit']:.2f} ({((row['Take Profit']/row['Entry']-1)*100):.1f}%)")
            print(f"  Shares: {row['Shares']:.0f}")
            print(f"  Position Value: ${row['Position Value']:,.2f}")
            print(f"  Potential Profit: ${row['Potential Profit']:,.2f}")
            
            if row['Score'] >= 8:
                print(f"\n💡 Recommendation: STRONG BUY - Execute trade")
            elif row['Score'] >= 5:
                print(f"\n💡 Recommendation: MODERATE BUY - Proceed with caution")
            else:
                print(f"\n⚠️ Recommendation: WEAK SETUP - Consider waiting")
        
        # Show bottom performers
        print("\n" + "=" * 100)
        print("STOCKS TO AVOID (Lowest Scores)")
        print("=" * 100)
        
        bottom_stocks = df.tail(5)
        print("\nThese stocks have unfavorable technical setups:\n")
        
        for idx, row in bottom_stocks.iterrows():
            issues = []
            if not row['Above 50 EMA']:
                issues.append("Below 50 EMA")
            if not row['Above 200 EMA']:
                issues.append("Below 200 EMA")
            if not row['Golden Cross']:
                issues.append("Death Cross")
            if row['RSI'] < 30:
                issues.append("Oversold RSI")
            elif row['RSI'] > 70:
                issues.append("Overbought RSI")
            
            print(f"{row['Ticker']:6s} - Score: {row['Score']:2.0f}/13 - ${row['Price']:8.2f} - Issues: {', '.join(issues)}")
        
        # Summary statistics
        print("\n" + "=" * 100)
        print("PORTFOLIO SUMMARY")
        print("=" * 100)
        
        strong_buy = len(df[df['Score'] >= 8])
        moderate_buy = len(df[(df['Score'] >= 5) & (df['Score'] < 8)])
        neutral = len(df[(df['Score'] >= 3) & (df['Score'] < 5)])
        avoid = len(df[df['Score'] < 3])
        
        print(f"\nTotal Stocks Analyzed: {len(df)}")
        print(f"  • Strong Buy (Score ≥ 8): {strong_buy}")
        print(f"  • Moderate Buy (Score 5-7): {moderate_buy}")
        print(f"  • Neutral (Score 3-4): {neutral}")
        print(f"  • Avoid (Score < 3): {avoid}")
        
        tradeable = df[df['Score'] >= 5]
        if len(tradeable) > 0:
            print(f"\nTop {min(5, len(tradeable))} Tradeable Stocks:")
            print(f"  Total Capital Required: ${tradeable.head(5)['Position Value'].sum():,.2f}")
            print(f"  Total Potential Profit: ${tradeable.head(5)['Potential Profit'].sum():,.2f}")
            print(f"  Average Score: {tradeable.head(5)['Score'].mean():.1f}/13")
        
        # Save to Excel
        filename = f'custom_stock_analysis_{datetime.now().strftime("%Y%m%d")}.xlsx'
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # All stocks ranked
            export_df = df[['Ticker', 'Company', 'Sector', 'Score', 'Signal', 'Price', 
                            '50 EMA', '200 EMA', 'RSI', '5D Change %', 'Entry', 
                            'Stop Loss', 'Take Profit', 'Shares', 'Potential Profit']].copy()
            export_df.to_excel(writer, sheet_name='All Stocks', index=False)
            
            # Top recommendations
            if len(tradeable) > 0:
                tradeable[['Ticker', 'Company', 'Score', 'Signal', 'Price', 'Entry', 
                          'Stop Loss', 'Take Profit', 'Potential Profit']].to_excel(
                    writer, sheet_name='Top Picks', index=False)
            
            # Summary
            summary_data = {
                'Metric': [
                    'Analysis Date',
                    'Total Stocks',
                    'Strong Buy',
                    'Moderate Buy',
                    'Neutral',
                    'Avoid',
                    'Best Stock',
                    'Worst Stock',
                ],
                'Value': [
                    datetime.now().strftime('%Y-%m-%d'),
                    len(df),
                    strong_buy,
                    moderate_buy,
                    neutral,
                    avoid,
                    f"{df.iloc[0]['Ticker']} (Score: {df.iloc[0]['Score']})",
                    f"{df.iloc[-1]['Ticker']} (Score: {df.iloc[-1]['Score']})",
                ]
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name='Summary', index=False)
        
        print(f"\n✅ Full analysis saved to: {filename}")
        print("=" * 100)

