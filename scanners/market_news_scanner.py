"""
Pre-Market and Post-Market News Scanner
Analyzes market-moving news for day trading and next-day trading opportunities
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import warnings
from config.master_stock_list import get_intraday_core_list
warnings.filterwarnings('ignore')

class MarketNewsScanner:
    def __init__(self):
        self.major_symbols = self.get_watchlist()
        self.market_indices = ['SPY', 'QQQ', 'DIA', 'IWM', 'VIX']
        
        # Keywords for different types of market-moving news
        self.bullish_keywords = [
            'beat', 'beats', 'exceeded', 'upgrade', 'raised guidance', 'strong',
            'positive', 'growth', 'profit', 'revenue beat', 'partnership',
            'acquisition', 'approved', 'breakthrough', 'record', 'soars',
            'rally', 'gains', 'surge', 'jump', 'climbs', 'advancement'
        ]
        
        self.bearish_keywords = [
            'miss', 'misses', 'missed', 'downgrade', 'lowered guidance', 'weak',
            'negative', 'loss', 'decline', 'falls', 'drops', 'plunge',
            'concern', 'warning', 'investigation', 'lawsuit', 'recall',
            'slump', 'tumbles', 'crash', 'plummets', 'disappointing'
        ]
        
        self.high_impact_keywords = [
            'earnings', 'fed', 'interest rate', 'inflation', 'gdp',
            'employment', 'jobs report', 'cpi', 'ppi', 'merger',
            'acquisition', 'fda approval', 'clinical trial', 'guidance',
            'dividend', 'split', 'buyback', 'bankruptcy', 'delisting'
        ]
    
    def get_watchlist(self):
        """Get major stocks for news monitoring"""
        return get_intraday_core_list()
    
    def get_stock_news(self, symbol):
        """Get recent news for a specific symbol"""
        try:
            stock = yf.Ticker(symbol)
            news = stock.news
            
            if not news:
                return []
            
            news_items = []
            for item in news[:10]:  # Get latest 10 news items
                news_items.append({
                    'symbol': symbol,
                    'title': item.get('title', ''),
                    'publisher': item.get('publisher', 'Unknown'),
                    'link': item.get('link', ''),
                    'publish_time': datetime.fromtimestamp(item.get('providerPublishTime', 0)),
                    'type': item.get('type', 'STORY'),
                    'thumbnail': item.get('thumbnail', {}).get('resolutions', [{}])[0].get('url', '') if item.get('thumbnail') else ''
                })
            
            return news_items
        except Exception as e:
            print(f"Error fetching news for {symbol}: {e}")
            return []
    
    def analyze_sentiment(self, text):
        """Analyze sentiment of news text"""
        text_lower = text.lower()
        
        bullish_count = sum(1 for keyword in self.bullish_keywords if keyword in text_lower)
        bearish_count = sum(1 for keyword in self.bearish_keywords if keyword in text_lower)
        high_impact = any(keyword in text_lower for keyword in self.high_impact_keywords)
        
        # Calculate sentiment score
        if bullish_count > bearish_count:
            sentiment = 'BULLISH'
            score = min(bullish_count * 10, 100)
        elif bearish_count > bullish_count:
            sentiment = 'BEARISH'
            score = min(bearish_count * 10, 100)
        else:
            sentiment = 'NEUTRAL'
            score = 50
        
        return {
            'sentiment': sentiment,
            'score': score,
            'high_impact': high_impact,
            'bullish_signals': bullish_count,
            'bearish_signals': bearish_count
        }
    
    def get_price_action(self, symbol, hours=24):
        """Get recent price action for context"""
        try:
            stock = yf.Ticker(symbol)
            
            # Get intraday data if available
            hist = stock.history(period='5d', interval='1h')
            if hist.empty:
                hist = stock.history(period='5d')
            
            if hist.empty or len(hist) < 2:
                return None
            
            current_price = hist['Close'].iloc[-1]
            prev_close = hist['Close'].iloc[-2]
            
            # Calculate metrics
            change = current_price - prev_close
            change_pct = (change / prev_close) * 100
            
            # Volume analysis
            avg_volume = hist['Volume'].mean()
            recent_volume = hist['Volume'].iloc[-1]
            volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1
            
            # Volatility
            returns = hist['Close'].pct_change().dropna()
            volatility = returns.std() * 100
            
            return {
                'current_price': current_price,
                'change': change,
                'change_pct': change_pct,
                'volume_ratio': volume_ratio,
                'volatility': volatility,
                'in_play': volume_ratio > 1.5 or abs(change_pct) > 3
            }
        except Exception as e:
            print(f"Error getting price action for {symbol}: {e}")
            return None
    
    def categorize_news_timing(self, publish_time):
        """Categorize news by market timing"""
        now = datetime.now()
        hour = publish_time.hour
        
        # Market hours: 9:30 AM - 4:00 PM ET (approximate)
        if 0 <= hour < 9:
            return 'PRE-MARKET'
        elif 9 <= hour < 16:
            return 'MARKET-HOURS'
        else:
            return 'POST-MARKET'
    
    def get_trading_impact(self, news_item, price_action, sentiment):
        """Determine potential trading impact"""
        impact_level = 'LOW'
        trading_signals = []
        
        # High impact news
        if sentiment['high_impact']:
            impact_level = 'HIGH'
            
            if sentiment['sentiment'] == 'BULLISH':
                trading_signals.extend(['Gap Up Potential', 'Breakout Watch', 'Call Options'])
            elif sentiment['sentiment'] == 'BEARISH':
                trading_signals.extend(['Gap Down Risk', 'Short Setup', 'Put Options'])
        
        # Price action confirmation
        if price_action:
            if price_action['in_play']:
                impact_level = 'MEDIUM' if impact_level == 'LOW' else impact_level
                trading_signals.append('High Volume')
            
            if abs(price_action['change_pct']) > 5:
                impact_level = 'HIGH'
                trading_signals.append('Volatile Move')
        
        # Timing-based signals
        timing = self.categorize_news_timing(news_item['publish_time'])
        if timing == 'PRE-MARKET':
            trading_signals.append('Pre-Market Mover')
        elif timing == 'POST-MARKET':
            trading_signals.append('After-Hours Action')
        
        return {
            'impact_level': impact_level,
            'trading_signals': trading_signals,
            'timing': timing
        }
    
    def scan_all_news(self, max_workers=20):
        """Scan news for all watchlist symbols"""
        print(f"\nScanning news for {len(self.major_symbols)} symbols...")
        print("-" * 100)
        
        all_news = []
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {executor.submit(self.get_stock_news, symbol): symbol 
                               for symbol in self.major_symbols}
            
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                news_items = future.result()
                
                if news_items:
                    print(f"Found {len(news_items)} news items for {symbol}")
                    all_news.extend(news_items)
        
        return all_news
    
    def analyze_all_news(self, news_items):
        """Analyze all news items for trading opportunities"""
        analyzed_news = []
        
        print(f"\nAnalyzing {len(news_items)} news items...")
        print("-" * 100)
        
        for item in news_items:
            # Skip very old news (older than 48 hours)
            if (datetime.now() - item['publish_time']).total_seconds() > 48 * 3600:
                continue
            
            # Analyze sentiment
            sentiment = self.analyze_sentiment(item['title'])
            
            # Get price action
            price_action = self.get_price_action(item['symbol'])
            
            # Determine trading impact
            impact = self.get_trading_impact(item, price_action, sentiment)
            
            # Combine all data
            analyzed_item = {
                **item,
                **sentiment,
                'price_action': price_action,
                'impact_level': impact['impact_level'],
                'trading_signals': impact['trading_signals'],
                'timing': impact['timing']
            }
            
            analyzed_news.append(analyzed_item)
        
        return analyzed_news
    
    def display_market_overview(self):
        """Display overall market condition"""
        print("\n" + "="*100)
        print("MARKET OVERVIEW")
        print("="*100)
        
        for index in self.market_indices:
            try:
                ticker = yf.Ticker(index)
                hist = ticker.history(period='2d')
                
                if not hist.empty and len(hist) >= 2:
                    current = hist['Close'].iloc[-1]
                    prev = hist['Close'].iloc[-2]
                    change_pct = ((current - prev) / prev) * 100
                    
                    direction = "🟢" if change_pct > 0 else "🔴" if change_pct < 0 else "⚪"
                    print(f"{direction} {index}: ${current:.2f} ({change_pct:+.2f}%)")
            except:
                pass
        
        print()
    
    def display_news_by_category(self, news_items):
        """Display news organized by timing and impact"""
        if not news_items:
            print("\nNo recent market-moving news found.")
            return
        
        # Sort by impact and time
        df = pd.DataFrame(news_items)
        
        # Priority: HIGH impact first, then by recency
        impact_priority = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
        df['impact_priority'] = df['impact_level'].map(impact_priority)
        df = df.sort_values(['impact_priority', 'publish_time'], ascending=[False, False])
        
        # Separate by timing
        pre_market = df[df['timing'] == 'PRE-MARKET']
        post_market = df[df['timing'] == 'POST-MARKET']
        market_hours = df[df['timing'] == 'MARKET-HOURS']
        
        # Display Pre-Market News
        print("\n" + "="*100)
        print("📰 PRE-MARKET NEWS (Impacts Today's Trading)")
        print("="*100)
        self._display_news_section(pre_market)
        
        # Display Post-Market News
        print("\n" + "="*100)
        print("📰 POST-MARKET NEWS (Impacts Next Day Trading)")
        print("="*100)
        self._display_news_section(post_market)
        
        # Display Market Hours News (if recent)
        if not market_hours.empty:
            print("\n" + "="*100)
            print("📰 MARKET HOURS NEWS (Current Day Impact)")
            print("="*100)
            self._display_news_section(market_hours.head(10))
    
    def _display_news_section(self, df):
        """Display a section of news"""
        if df.empty:
            print("  No significant news in this period.\n")
            return
        
        for idx, row in df.head(20).iterrows():
            # Impact indicator
            if row['impact_level'] == 'HIGH':
                impact_icon = "🔥"
            elif row['impact_level'] == 'MEDIUM':
                impact_icon = "⚠️"
            else:
                impact_icon = "ℹ️"
            
            # Sentiment indicator
            if row['sentiment'] == 'BULLISH':
                sentiment_icon = "🟢"
            elif row['sentiment'] == 'BEARISH':
                sentiment_icon = "🔴"
            else:
                sentiment_icon = "⚪"
            
            print(f"\n{impact_icon} {sentiment_icon} {row['symbol']} - {row['impact_level']} IMPACT")
            print(f"{'─'*100}")
            print(f"Title: {row['title']}")
            print(f"Time: {row['publish_time'].strftime('%Y-%m-%d %I:%M %p')}")
            print(f"Source: {row['publisher']}")
            print(f"Sentiment: {row['sentiment']} (Score: {row['score']}/100)")
            
            # Price action
            if row['price_action']:
                pa = row['price_action']
                change_icon = "📈" if pa['change_pct'] > 0 else "📉"
                print(f"{change_icon} Price: ${pa['current_price']:.2f} ({pa['change_pct']:+.2f}%)")
                if pa['volume_ratio'] > 1.5:
                    print(f"📊 Volume: {pa['volume_ratio']:.1f}x average (HIGH VOLUME)")
            
            # Trading signals
            if row['trading_signals']:
                print(f"💡 Trading Signals: {', '.join(row['trading_signals'])}")
            
            print(f"🔗 Link: {row['link']}")
    
    def get_top_movers(self):
        """Get top pre-market and after-hours movers"""
        print("\n" + "="*100)
        print("📊 TOP MOVERS (Stocks in Play)")
        print("="*100)
        
        movers = []
        
        for symbol in self.major_symbols[:50]:  # Check top 50 for speed
            try:
                stock = yf.Ticker(symbol)
                
                # Get pre/post market data if available
                info = stock.info
                regular_price = info.get('regularMarketPrice', 0)
                prev_close = info.get('previousClose', 0)
                
                if regular_price and prev_close:
                    change_pct = ((regular_price - prev_close) / prev_close) * 100
                    
                    if abs(change_pct) > 2:  # Significant move
                        movers.append({
                            'symbol': symbol,
                            'price': regular_price,
                            'change_pct': change_pct,
                            'volume': info.get('volume', 0),
                            'avg_volume': info.get('averageVolume', 1)
                        })
            except:
                continue
        
        if movers:
            movers_df = pd.DataFrame(movers)
            movers_df = movers_df.sort_values('change_pct', ascending=False)
            
            # Get top gainers (positive changes)
            gainers = movers_df[movers_df['change_pct'] > 0].head(5)
            # Get top losers (negative changes)
            losers = movers_df[movers_df['change_pct'] < 0].tail(5).sort_values('change_pct')
            
            print("\n🟢 TOP GAINERS:")
            if not gainers.empty:
                for _, row in gainers.iterrows():
                    vol_indicator = "🔊" if row['volume'] > row['avg_volume'] * 1.5 else ""
                    print(f"  {row['symbol']}: ${row['price']:.2f} ({row['change_pct']:+.2f}%) {vol_indicator}")
            else:
                print("  No significant gainers found.")
            
            print("\n🔴 TOP LOSERS:")
            if not losers.empty:
                for _, row in losers.iterrows():
                    vol_indicator = "🔊" if row['volume'] > row['avg_volume'] * 1.5 else ""
                    print(f"  {row['symbol']}: ${row['price']:.2f} ({row['change_pct']:+.2f}%) {vol_indicator}")
            else:
                print("  No significant losers found.")
    
    def generate_trading_watchlist(self, news_items):
        """Generate a watchlist based on news analysis"""
        if not news_items:
            return
        
        df = pd.DataFrame(news_items)
        
        # Filter high impact news
        high_impact = df[df['impact_level'].isin(['HIGH', 'MEDIUM'])]
        
        if high_impact.empty:
            return
        
        print("\n" + "="*100)
        print("📋 TODAY'S TRADING WATCHLIST (Based on News)")
        print("="*100)
        
        # Group by symbol and sentiment
        watchlist = high_impact.groupby(['symbol', 'sentiment']).size().reset_index(name='news_count')
        watchlist = watchlist.sort_values('news_count', ascending=False)
        
        print("\n🎯 BULLISH SETUPS:")
        bullish = watchlist[watchlist['sentiment'] == 'BULLISH'].head(10)
        for _, row in bullish.iterrows():
            print(f"  {row['symbol']} - {row['news_count']} bullish catalyst(s)")
        
        print("\n🎯 BEARISH SETUPS:")
        bearish = watchlist[watchlist['sentiment'] == 'BEARISH'].head(10)
        for _, row in bearish.iterrows():
            print(f"  {row['symbol']} - {row['news_count']} bearish catalyst(s)")
    
    def save_report(self, news_items):
        """Save news analysis to CSV"""
        if not news_items:
            return
        
        df = pd.DataFrame(news_items)
        
        # Flatten price_action dictionary
        if 'price_action' in df.columns:
            df['current_price'] = df['price_action'].apply(lambda x: x['current_price'] if x else None)
            df['price_change_pct'] = df['price_action'].apply(lambda x: x['change_pct'] if x else None)
            df['volume_ratio'] = df['price_action'].apply(lambda x: x['volume_ratio'] if x else None)
            df = df.drop('price_action', axis=1)
        
        # Convert lists to strings
        df['trading_signals'] = df['trading_signals'].apply(lambda x: ', '.join(x) if x else '')
        
        filename = f"market_news_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        df.to_csv(filename, index=False)
        print(f"\n💾 Report saved to: {filename}")


def main():
    """Main execution function"""
    print("="*100)
    print("MARKET NEWS SCANNER - PRE & POST MARKET ANALYSIS")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    print("="*100)
    
    scanner = MarketNewsScanner()
    
    # Display market overview
    scanner.display_market_overview()
    
    # Get top movers
    scanner.get_top_movers()
    
    # Scan all news
    all_news = scanner.scan_all_news(max_workers=20)
    
    # Analyze news
    analyzed_news = scanner.analyze_all_news(all_news)
    
    # Display news by category
    scanner.display_news_by_category(analyzed_news)
    
    # Generate trading watchlist
    scanner.generate_trading_watchlist(analyzed_news)
    
    # Save report
    scanner.save_report(analyzed_news)
    
    print("\n" + "="*100)
    print("✅ News scan complete!")
    print("="*100)


if __name__ == "__main__":
    main()
