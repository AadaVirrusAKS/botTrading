#!/usr/bin/env python3
"""
EXECUTE TRADE SETUP - Real-Time Options Trading
Generates actionable trade setups with specific entry/exit points
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# Use centralized caching layer to avoid Yahoo rate limiting
try:
    from services.market_data import (
        cached_get_history, cached_get_price, cached_get_ticker_info,
        cached_get_option_dates, cached_get_option_chain,
        _is_globally_rate_limited
    )
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False

class TradeExecutor:
    def __init__(self):
        """Initialize trade executor"""
        self.tickers = ['SPY', 'QQQ']
        
        print("=" * 100)
        print("⚡ REAL-TIME OPTIONS TRADE EXECUTOR")
        print("=" * 100)
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
        print(f"Analyzing: {', '.join(self.tickers)}")
        print("=" * 100)
    
    def get_option_expiration_date(self, ticker, stock_obj=None):
        """Get appropriate option expiration date based on ticker type"""
        # ETFs with daily expirations
        daily_etfs = ['SPY', 'QQQ', 'IWM', 'DIA', 'SPX', 'NDX']
        
        ticker_upper = ticker.upper()
        today = datetime.now()
        current_weekday = today.weekday()  # Monday=0, Friday=4
        
        # For daily ETFs, use next trading day
        if ticker_upper in daily_etfs:
            next_day = today + timedelta(days=1)
            while next_day.weekday() >= 5:  # Skip weekends
                next_day += timedelta(days=1)
            return next_day.strftime('%Y-%m-%d')
        
        # For stocks, find appropriate weekly/monthly expiration
        # Avoid current week expiry if Thu/Fri (too close to expiration)
        try:
            if _USE_CACHED:
                expirations = cached_get_option_dates(ticker, force_live=True)
            elif stock_obj is None:
                stock_obj = yf.Ticker(ticker)
                expirations = stock_obj.options
            else:
                expirations = stock_obj.options
            
            if expirations:
                # Convert to datetime objects
                exp_dates = [datetime.strptime(exp, '%Y-%m-%d') for exp in expirations]
                
                # Filter future expirations
                future_exps = [exp for exp in exp_dates if exp > today]
                
                if future_exps:
                    # If Thu/Fri (weekday 3 or 4), skip options expiring this week
                    if current_weekday >= 3:  # Thursday or Friday
                        # Find expirations at least 3 days away
                        safe_exps = [exp for exp in future_exps if (exp - today).days >= 3]
                        if safe_exps:
                            nearest_exp = min(safe_exps)
                            print(f"   📅 Using next week expiration (avoiding current week - too close)")
                            return nearest_exp.strftime('%Y-%m-%d')
                    
                    # Mon-Wed: can use current week or next available
                    nearest_exp = min(future_exps)
                    return nearest_exp.strftime('%Y-%m-%d')
            
            # Fallback: Calculate next appropriate Friday
            days_ahead = 4 - current_weekday  # Friday is 4
            if days_ahead <= 0 or current_weekday >= 3:  # Past Friday or Thu/Fri
                days_ahead += 7  # Next week's Friday
            next_friday = today + timedelta(days=days_ahead)
            return next_friday.strftime('%Y-%m-%d')
            
        except Exception as e:
            print(f"⚠️ Error fetching expirations: {e}")
            # Fallback to appropriate Friday
            days_ahead = 4 - current_weekday
            if days_ahead <= 0 or current_weekday >= 3:
                days_ahead += 7
            next_friday = today + timedelta(days=days_ahead)
            return next_friday.strftime('%Y-%m-%d')
    
    def get_live_price(self, ticker):
        """Get current live price"""
        try:
            if _USE_CACHED:
                price, _ = cached_get_price(ticker, use_cache=False)
                if price:
                    return price
                info = cached_get_ticker_info(ticker, force_live=True) or {}
                return info.get('currentPrice') or info.get('regularMarketPrice')

            stock = yf.Ticker(ticker)
            info = stock.info
            
            current_price = info.get('currentPrice') or info.get('regularMarketPrice')
            
            if not current_price:
                hist = stock.history(period='1d', interval='1m')
                if len(hist) > 0:
                    current_price = hist['Close'].iloc[-1]
            
            return current_price
        except Exception as e:
            print(f"Error getting price for {ticker}: {e}")
            return None
    
    def calculate_indicators(self, ticker):
        """Calculate technical indicators"""
        try:
            if _USE_CACHED:
                daily = cached_get_history(ticker, period='3mo', interval='1d', force_live=True)
                hourly = cached_get_history(ticker, period='5d', interval='1h', force_live=True)
            else:
                stock = yf.Ticker(ticker)
                daily = stock.history(period='3mo', interval='1d')
                hourly = stock.history(period='5d', interval='1h')
            
            if daily is None or hourly is None or len(daily) < 20 or len(hourly) < 20:
                return None
            
            # Calculate EMAs
            daily['9EMA'] = daily['Close'].ewm(span=9, adjust=False).mean()
            daily['21EMA'] = daily['Close'].ewm(span=21, adjust=False).mean()
            daily['50EMA'] = daily['Close'].ewm(span=50, adjust=False).mean()
            
            # RSI
            delta = daily['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            daily['RSI'] = 100 - (100 / (1 + rs))
            
            # ATR
            daily['High-Low'] = daily['High'] - daily['Low']
            daily['High-Close'] = abs(daily['High'] - daily['Close'].shift())
            daily['Low-Close'] = abs(daily['Low'] - daily['Close'].shift())
            daily['TR'] = daily[['High-Low', 'High-Close', 'Low-Close']].max(axis=1)
            daily['ATR'] = daily['TR'].rolling(window=14).mean()
            
            # Hourly momentum
            hourly['ROC'] = ((hourly['Close'] - hourly['Close'].shift(6)) / hourly['Close'].shift(6)) * 100
            
            latest_daily = daily.iloc[-1]
            latest_hourly = hourly.iloc[-1]
            
            return {
                'ema_9': latest_daily['9EMA'],
                'ema_21': latest_daily['21EMA'],
                'ema_50': latest_daily['50EMA'],
                'rsi': latest_daily['RSI'],
                'atr': latest_daily['ATR'],
                'hourly_roc': latest_hourly['ROC'],
                'daily_high': daily['High'].iloc[-1],
                'daily_low': daily['Low'].iloc[-1],
                'daily_open': daily['Open'].iloc[-1]
            }
        except Exception as e:
            print(f"Error calculating indicators for {ticker}: {e}")
            return None
    
    def generate_trade_setup(self, ticker):
        """Generate complete trade setup"""
        print(f"\n{'=' * 100}")
        print(f"📊 ANALYZING {ticker}")
        print(f"{'=' * 100}")
        
        # Get live price
        current_price = self.get_live_price(ticker)
        if not current_price:
            print(f"❌ Could not get price for {ticker}")
            return None
        
        print(f"💰 Current Price: ${current_price:.2f}")
        
        # Calculate indicators
        indicators = self.calculate_indicators(ticker)
        if not indicators:
            print(f"❌ Could not calculate indicators for {ticker}")
            return None
        
        print(f"\n📈 TECHNICAL INDICATORS:")
        print(f"   EMAs: 9=${indicators['ema_9']:.2f} | 21=${indicators['ema_21']:.2f} | 50=${indicators['ema_50']:.2f}")
        print(f"   RSI: {indicators['rsi']:.1f}")
        print(f"   ATR: ${indicators['atr']:.2f}")
        print(f"   Hourly Momentum: {indicators['hourly_roc']:.2f}%")
        print(f"   Daily Range: ${indicators['daily_low']:.2f} - ${indicators['daily_high']:.2f}")
        
        # Determine direction - Enhanced analysis for both CALL and PUT
        bullish_signals = []
        bearish_signals = []
        
        # Price vs EMAs (Strong weight - 2 signals)
        if current_price > indicators['ema_9']:
            bullish_signals.append("Price > 9 EMA")
        else:
            bearish_signals.append("Price < 9 EMA")
        
        if current_price > indicators['ema_21']:
            bullish_signals.append("Price > 21 EMA")
        else:
            bearish_signals.append("Price < 21 EMA")
        
        if current_price > indicators['ema_50']:
            bullish_signals.append("Price > 50 EMA")
        else:
            bearish_signals.append("Price < 50 EMA")
        
        # RSI - Enhanced for better PUT detection
        if indicators['rsi'] <= 30:
            bullish_signals.append(f"RSI oversold ({indicators['rsi']:.1f})")
            bullish_signals.append("Strong bounce potential")
        elif 30 < indicators['rsi'] <= 45:
            bullish_signals.append(f"RSI below neutral ({indicators['rsi']:.1f})")
        elif 45 < indicators['rsi'] < 55:
            # Neutral zone - add weak signal to whichever side price trend supports
            if current_price > indicators['ema_9']:
                bullish_signals.append(f"RSI neutral-bullish ({indicators['rsi']:.1f})")
            else:
                bearish_signals.append(f"RSI neutral-bearish ({indicators['rsi']:.1f})")
        elif 55 <= indicators['rsi'] < 70:
            bearish_signals.append(f"RSI above neutral ({indicators['rsi']:.1f})")
        elif 70 <= indicators['rsi'] < 80:
            bearish_signals.append(f"RSI overbought zone ({indicators['rsi']:.1f})")
            bearish_signals.append("Pullback risk increasing")
        elif indicators['rsi'] >= 80:
            bearish_signals.append(f"⚠️ RSI EXTREME OVERBOUGHT ({indicators['rsi']:.1f})")
            bearish_signals.append("High reversal probability")
        
        # Momentum (Strong indicator)
        if indicators['hourly_roc'] > 0.5:
            bullish_signals.append("Strong positive momentum")
        elif indicators['hourly_roc'] > 0.2:
            bullish_signals.append("Positive momentum")
        elif indicators['hourly_roc'] < -0.5:
            bearish_signals.append("Strong negative momentum")
        elif indicators['hourly_roc'] < -0.2:
            bearish_signals.append("Negative momentum")
        
        # EMA trend alignment (Critical signal)
        if indicators['ema_9'] > indicators['ema_21'] > indicators['ema_50']:
            bullish_signals.append("Bullish EMA alignment (9>21>50)")
            bullish_signals.append("Uptrend confirmed")
        elif indicators['ema_9'] < indicators['ema_21'] < indicators['ema_50']:
            bearish_signals.append("Bearish EMA alignment (9<21<50)")
            bearish_signals.append("Downtrend confirmed")
        elif indicators['ema_9'] > indicators['ema_21']:
            bullish_signals.append("Short-term uptrend (9>21)")
        elif indicators['ema_9'] < indicators['ema_21']:
            bearish_signals.append("Short-term downtrend (9<21)")
        
        # Price position relative to daily range
        daily_range = indicators['daily_high'] - indicators['daily_low']
        price_position = (current_price - indicators['daily_low']) / daily_range if daily_range > 0 else 0.5
        
        if price_position > 0.8:
            bearish_signals.append(f"Price near daily high ({price_position*100:.0f}%)")
        elif price_position < 0.2:
            bullish_signals.append(f"Price near daily low ({price_position*100:.0f}%)")
        
        print(f"\n🎯 MARKET ANALYSIS:")
        print(f"   BULLISH SIGNALS: {len(bullish_signals)}")
        for signal in bullish_signals:
            print(f"      ✅ {signal}")
        print(f"   BEARISH SIGNALS: {len(bearish_signals)}")
        for signal in bearish_signals:
            print(f"      🔴 {signal}")
        
        # Determine primary direction with clear logic
        bullish_score = len(bullish_signals)
        bearish_score = len(bearish_signals)
        
        print(f"\n   SCORE: Bullish={bullish_score} vs Bearish={bearish_score}")
        
        if bullish_score > bearish_score:
            direction = 'CALL'
            confidence = min(bullish_score, 5)
            reason = "Bullish signals dominate"
        elif bearish_score > bullish_score:
            direction = 'PUT'
            confidence = min(bearish_score, 5)
            reason = "Bearish signals dominate"
        else:
            # Equal signals - use RSI and momentum as tiebreakers
            if indicators['rsi'] < 45:
                direction = 'CALL'
                confidence = 3
                reason = "RSI oversold - bounce expected"
            elif indicators['rsi'] > 55:
                direction = 'PUT'
                confidence = 3
                reason = "RSI elevated - pullback expected"
            elif indicators['hourly_roc'] > 0:
                direction = 'CALL'
                confidence = 2
                reason = "Momentum positive"
            else:
                direction = 'PUT'
                confidence = 2
                reason = "Momentum negative"
        
        print(f"\n🎯 TRADE DIRECTION: {direction}")
        print(f"   Confidence: {'⭐' * confidence} ({confidence}/5)")
        print(f"   Reason: {reason}")
        
        # Calculate strike and premiums
        atr = indicators['atr']
        
        # Get actual option chain data for real premiums
        try:
            if _USE_CACHED:
                expirations = cached_get_option_dates(ticker, force_live=True)
            else:
                stock = yf.Ticker(ticker)
                expirations = list(stock.options) if stock.options else []
            if not expirations:
                print(f"⚠️ No options available for {ticker}")
                return None
            
            # Get the appropriate expiration date based on ticker type
            target_expiration = self.get_option_expiration_date(ticker, stock)
            
            # Verify the target expiration is in available expirations
            if target_expiration not in expirations:
                print(f"⚠️ Target expiration {target_expiration} not available")
                print(f"   Available: {expirations[:5]}")
                # Find the closest available expiration
                target_date = datetime.strptime(target_expiration, '%Y-%m-%d')
                exp_dates = [datetime.strptime(exp, '%Y-%m-%d') for exp in expirations]
                closest_exp = min(exp_dates, key=lambda x: abs((x - target_date).days))
                target_expiration = closest_exp.strftime('%Y-%m-%d')
                print(f"   Using closest: {target_expiration}")
            
            print(f"\n📅 Using expiration: {target_expiration}")
            
            # Fetch option chain (live - bypass cache for fresh premiums)
            if _USE_CACHED:
                opt_chain = cached_get_option_chain(ticker, target_expiration, use_cache=False)
            else:
                opt_chain = stock.option_chain(target_expiration)
            
        except Exception as e:
            print(f"⚠️ Error fetching option chain: {e}")
            return None
        
        if direction == 'CALL':
            # ATM or slightly OTM call
            target_strike = round(current_price + (atr * 0.1), 0)  # Slightly OTM
            options_df = opt_chain.calls
            
        else:  # PUT
            # Slightly OTM put
            target_strike = round(current_price - (atr * 0.1), 0)  # Slightly OTM
            options_df = opt_chain.puts
        
        # Find the closest strike to our target
        if options_df.empty:
            print(f"⚠️ No {direction} options available")
            return None
        
        # Find closest strike
        closest_idx = (options_df['strike'] - target_strike).abs().idxmin()
        selected_option = options_df.loc[closest_idx]
        
        strike = selected_option['strike']
        
        # Get the real premium from the option chain
        entry_premium = selected_option['lastPrice']
        
        # Validate premium - use bid/ask if lastPrice is invalid
        if entry_premium == 0 or pd.isna(entry_premium) or entry_premium < 0.01:
            bid = selected_option.get('bid', 0)
            ask = selected_option.get('ask', 0)
            
            if bid > 0 and ask > 0:
                entry_premium = (bid + ask) / 2
                print(f"💰 Using bid/ask midpoint: ${entry_premium:.2f} (bid: ${bid:.2f}, ask: ${ask:.2f})")
            elif bid > 0:
                entry_premium = bid
                print(f"💰 Using bid price: ${entry_premium:.2f}")
            elif ask > 0:
                entry_premium = ask
                print(f"💰 Using ask price: ${entry_premium:.2f}")
            else:
                # Fallback to theoretical calculation
                intrinsic = max(0, current_price - strike) if direction == 'CALL' else max(0, strike - current_price)
                time_value = atr * 0.35
                entry_premium = max(intrinsic + time_value, atr * 0.25)
                entry_premium = min(entry_premium, current_price * 0.015)
                print(f"⚠️ Using theoretical premium: ${entry_premium:.2f}")
        else:
            print(f"✅ Using market premium: ${entry_premium:.2f} (strike: ${strike:.0f})")
        
        target_price = strike + (atr * 0.8) if direction == 'CALL' else strike - (atr * 0.8)
        
        # Risk management
        stop_loss_premium = entry_premium * 0.5  # 50% stop
        target_1 = entry_premium * 2  # 100% gain (1:2 ratio)
        target_2 = entry_premium * 3  # 200% gain (1:3 ratio)
        target_3 = entry_premium * 4  # 300% gain (1:4 ratio)
        
        # Calculate contract costs and validate premium
        contract_cost = entry_premium * 100
        
        # Premium affordability check
        premium_pct = (entry_premium / current_price) * 100
        if entry_premium > current_price * 0.02:
            print(f"\n   ⚠️  WARNING: {direction} premium is {premium_pct:.2f}% of stock price")
            print(f"   💡 Consider: Wider strike or shorter expiry for lower cost")
        
        trade_setup = {
            'ticker': ticker,
            'direction': direction,
            'confidence': confidence,
            'current_price': current_price,
            'strike': strike,
            'entry_premium': entry_premium,
            'stop_loss_premium': stop_loss_premium,
            'target_1': target_1,
            'target_2': target_2,
            'target_3': target_3,
            'target_price': target_price,
            'contract_cost': contract_cost,
            'atr': atr,
            'rsi': indicators['rsi']
        }
        
        return trade_setup
    
    def display_trade_plan(self, setup):
        """Display actionable trade plan"""
        if not setup:
            return
        
        print(f"\n{'=' * 100}")
        print(f"⚡ ACTIONABLE TRADE SETUP - {setup['ticker']}")
        print(f"{'=' * 100}")
        
        print(f"\n📋 OPTION DETAILS:")
        print(f"   Type: {setup['direction']} Option")
        print(f"   Ticker: {setup['ticker']}")
        print(f"   Strike Price: ${setup['strike']:.0f}")
        print(f"   Expiry: Next Day (1DTE) or Tomorrow")
        print(f"   Current Stock Price: ${setup['current_price']:.2f}")
        
        premium_pct = (setup['entry_premium'] / setup['current_price']) * 100
        print(f"\n💰 ENTRY:")
        print(f"   Entry Premium: ${setup['entry_premium']:.2f} per share ({premium_pct:.2f}% of stock price)")
        print(f"   Cost per Contract: ${setup['contract_cost']:.2f}")
        print(f"   Entry Time: NOW or market open tomorrow")
        print(f"   Order Type: LIMIT ORDER at ${setup['entry_premium']:.2f} or better")
        if setup['direction'] == 'CALL' and premium_pct > 1.5:
            print(f"   ⚠️  Premium Alert: Consider lower-cost alternatives")
        
        print(f"\n🎯 PROFIT TARGETS:")
        print(f"   Target 1 (1:2): ${setup['target_1']:.2f} - Close 33% (+100%)")
        print(f"   Target 2 (1:3): ${setup['target_2']:.2f} - Close 33% (+200%)")
        print(f"   Target 3 (1:4): ${setup['target_3']:.2f} - Close 34% (+300%)")
        
        print(f"\n🛑 RISK MANAGEMENT:")
        print(f"   Stop Loss: ${setup['stop_loss_premium']:.2f} (-50%)")
        print(f"   Max Loss per Contract: ${setup['contract_cost'] * 0.5:.2f}")
        print(f"   Risk/Reward: 1:2 minimum")
        
        print(f"\n⏰ EXIT RULES:")
        print(f"   MANDATORY EXIT: 3:00 PM CT (4:00 PM ET)")
        print(f"   Monitor every 15-30 minutes")
        print(f"   Take partials at each target")
        print(f"   Trail stop after Target 1 hits")
        
        # Example position sizing
        print(f"\n💵 POSITION SIZING EXAMPLES:")
        
        for contracts in [1, 2, 3]:
            total_cost = setup['contract_cost'] * contracts
            max_loss = total_cost * 0.5
            profit_t1 = (setup['target_1'] - setup['entry_premium']) * 100 * contracts * 0.33
            profit_t2 = (setup['target_2'] - setup['entry_premium']) * 100 * contracts * 0.33
            profit_t3 = (setup['target_3'] - setup['entry_premium']) * 100 * contracts * 0.34
            total_profit = profit_t1 + profit_t2 + profit_t3
            
            print(f"\n   {contracts} Contract{'s' if contracts > 1 else ''}:")
            print(f"      Total Cost: ${total_cost:.2f}")
            print(f"      Max Loss: ${max_loss:.2f}")
            print(f"      Potential Profit: ${total_profit:.2f}")
            print(f"      Risk/Reward: 1:{(total_profit/max_loss):.1f}")
        
        print(f"\n{'=' * 100}")
        print(f"📝 EXECUTION CHECKLIST:")
        print(f"{'=' * 100}")
        print(f"  [ ] Verify market is open")
        print(f"  [ ] Check {setup['ticker']} current price matches ${setup['current_price']:.2f}")
        print(f"  [ ] Find {setup['direction']} option with strike ${setup['strike']:.0f}")
        print(f"  [ ] Place LIMIT order at ${setup['entry_premium']:.2f}")
        print(f"  [ ] Set alert at ${setup['target_1']:.2f} (Target 1)")
        print(f"  [ ] Set alert at ${setup['target_2']:.2f} (Target 2)")
        print(f"  [ ] Set alert at ${setup['target_3']:.2f} (Target 3)")
        print(f"  [ ] Set stop loss at ${setup['stop_loss_premium']:.2f}")
        print(f"  [ ] Set calendar reminder for 3:00 PM CT exit")
        print(f"{'=' * 100}")
    
    def run(self):
        """Execute trade setup generation"""
        setups = []
        
        for ticker in self.tickers:
            setup = self.generate_trade_setup(ticker)
            if setup:
                setups.append(setup)
                self.display_trade_plan(setup)
        
        # Comparison
        if len(setups) > 1:
            print(f"\n{'=' * 100}")
            print(f"📊 TRADE COMPARISON")
            print(f"{'=' * 100}")
            
            for setup in setups:
                print(f"\n{setup['ticker']}:")
                print(f"  Direction: {setup['direction']} ${setup['strike']:.0f}")
                print(f"  Cost: ${setup['contract_cost']:.2f}")
                print(f"  Confidence: {setup['confidence']}/5")
                print(f"  Potential: ${((setup['target_3']-setup['entry_premium'])*100):.2f}")
            
            best = max(setups, key=lambda x: x['confidence'])
            print(f"\n🏆 RECOMMENDED: {best['ticker']} {best['direction']} (Confidence: {best['confidence']}/5)")
        
        return setups


if __name__ == "__main__":
    executor = TradeExecutor()
    setups = executor.run()
    
    print(f"\n{'=' * 100}")
    print(f"⚠️  IMPORTANT REMINDERS")
    print(f"{'=' * 100}")
    print(f"1. ⏰ Check market hours before entering")
    print(f"2. 📊 Verify option liquidity and bid-ask spread")
    print(f"3. 🎯 Use LIMIT orders - never market orders")
    print(f"4. 🛑 Stick to stop loss - no exceptions")
    print(f"5. ⏰ MUST exit by 3:00 PM CT (4:00 PM ET)")
    print(f"6. 📈 Monitor position every 15-30 minutes")
    print(f"7. 💰 Take partial profits at each target")
    print(f"8. 🔄 Options premium can change rapidly")
    print(f"{'=' * 100}")
    print(f"\n✅ Trade setups generated at {datetime.now().strftime('%I:%M:%S %p')}")
    print(f"{'=' * 100}")
