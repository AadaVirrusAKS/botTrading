import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import threading
import warnings
from config.master_stock_list import MASTER_ETF_UNIVERSE
warnings.filterwarnings('ignore')

# -----------------------------
# NEXT DAY OPTIONS PREDICTION SYSTEM
# Predicts trades for next day expiry at 3:00 PM CT (4:00 PM ET)
# Target: 1:3 or 1:5 Risk/Reward Ratio
# -----------------------------

class NextDayOptionsPredictor:
    def __init__(self, real_time_mode=False):
        """Initialize predictor for next day options"""
        self.prediction_time = "3:00 PM CT (4:00 PM ET)"
        self.min_rr = 3.0  # Minimum 1:3 ratio
        self.target_rr = 5.0  # Target 1:5 ratio
        self.real_time_mode = real_time_mode
        self.monitoring = False
        self.active_positions = {}
        
        print("=" * 100)
        print("🔮 NEXT DAY OPTIONS PREDICTION & TRADE SYSTEM")
        print("=" * 100)
        print(f"Mode: {'REAL-TIME MONITORING' if real_time_mode else 'PREDICTION ONLY'}")
        print(f"Prediction Time: {self.prediction_time}")
        print(f"Target Risk/Reward: 1:{self.min_rr} to 1:{self.target_rr}")
        print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
        print("=" * 100)
    
    def is_market_open(self):
        """Check if market is currently open (9:30 AM - 4:00 PM ET)"""
        now = datetime.now()
        # Convert to ET (assuming server time is local)
        current_time = now.time()
        market_open = current_time >= datetime.strptime('09:30', '%H:%M').time()
        market_close = current_time <= datetime.strptime('16:00', '%H:%M').time()
        is_weekday = now.weekday() < 5  # Monday = 0, Friday = 4
        return market_open and market_close and is_weekday
    
    def get_live_price(self, ticker):
        """Get current live price - uses cached service layer when available for rate-limit resilience"""
        # Try cached service layer first (has v8 API fallback, handles rate limits)
        try:
            from services.market_data import cached_batch_prices
            prices = cached_batch_prices([ticker], period='5d', interval='5m', prepost=True)
            if prices and ticker in prices and prices[ticker] is not None:
                return float(prices[ticker])
        except ImportError:
            pass  # Standalone mode - no service layer available
        except Exception:
            pass  # Service layer failed, try raw yfinance below

        # Fallback: raw yfinance (for standalone mode or if service layer fails)
        try:
            stock = yf.Ticker(ticker)
            
            # Try historical data first (most reliable)
            hist = stock.history(period='5d')
            if not hist.empty and len(hist) > 0:
                return float(hist['Close'].iloc[-1])
            
            # Fallback to info
            info = stock.info
            if 'currentPrice' in info and info['currentPrice']:
                return info['currentPrice']
            elif 'regularMarketPrice' in info and info['regularMarketPrice']:
                return info['regularMarketPrice']
            
            return None
        except Exception as e:
            # Silently fail - error already logged in analyze_stock
            return None
    
    def get_real_option_premium(self, ticker, strike_price, option_type='call', expiry_type='daily'):
        """
        Fetch REAL option premium from yfinance option chain.
        Uses expiry_type to find the correct expiration date.
        Returns live market data instead of estimates.
        """
        try:
            # Try cached service layer first
            try:
                from services.market_data import cached_get_option_dates, cached_get_option_chain
                expirations = cached_get_option_dates(ticker)
            except ImportError:
                stock = yf.Ticker(ticker)
                expirations = list(stock.options) if stock.options else []
            
            if not expirations:
                return None
            
            from datetime import datetime, timedelta
            import calendar
            today = datetime.now()
            
            # Calculate target date based on expiry_type
            if expiry_type == '0dte':
                target_date = today
                if today.hour >= 15 or today.weekday() >= 5:
                    target_date = today + timedelta(days=1)
                    while target_date.weekday() >= 5:
                        target_date += timedelta(days=1)
            elif expiry_type == 'weekly':
                # Find next Friday
                days_until_friday = (4 - today.weekday()) % 7
                if days_until_friday == 0 and today.hour >= 15:
                    days_until_friday = 7
                target_date = today + timedelta(days=max(days_until_friday, 1))
            elif expiry_type == 'monthly':
                # Find 3rd Friday of current/next month
                year, month = today.year, today.month
                c = calendar.monthcalendar(year, month)
                fridays = [week[4] for week in c if week[4] != 0]
                if len(fridays) >= 3:
                    third_friday = fridays[2]
                    target_date = datetime(year, month, third_friday)
                    if target_date.date() <= today.date():
                        month += 1
                        if month > 12:
                            month, year = 1, year + 1
                        c = calendar.monthcalendar(year, month)
                        fridays = [week[4] for week in c if week[4] != 0]
                        third_friday = fridays[2]
                        target_date = datetime(year, month, third_friday)
                else:
                    target_date = today + timedelta(days=30)
            else:  # 'daily'
                target_date = today + timedelta(days=1)
                while target_date.weekday() >= 5:
                    target_date += timedelta(days=1)
            
            # Find closest expiration to target_date
            closest_exp = min(expirations, 
                            key=lambda x: abs((datetime.strptime(x, '%Y-%m-%d') - target_date).days))
            
            # Get option chain (try cached service layer first)
            try:
                from services.market_data import cached_get_option_chain
                opt_chain = cached_get_option_chain(ticker, closest_exp)
            except ImportError:
                stock_obj = yf.Ticker(ticker)
                opt_chain = stock_obj.option_chain(closest_exp)
            chain = opt_chain.calls if option_type.lower() == 'call' else opt_chain.puts
            
            # Find strike closest to requested
            chain = chain.copy()
            chain['strike_diff'] = abs(chain['strike'] - strike_price)
            best_match = chain.loc[chain['strike_diff'].idxmin()]
            
            # Use mid price (average of bid/ask) for most accurate entry
            mid_price = (best_match['bid'] + best_match['ask']) / 2
            
            return {
                'strike': best_match['strike'],
                'lastPrice': best_match['lastPrice'],
                'bid': best_match['bid'],
                'ask': best_match['ask'],
                'mid': mid_price,  # Best estimate for entry
                'volume': best_match['volume'],
                'openInterest': best_match['openInterest'],
                'impliedVolatility': best_match['impliedVolatility'],
                'contractSymbol': best_match['contractSymbol'],
                'expiration': closest_exp
            }
        except Exception as e:
            # Silently return None - will fall back to estimate
            return None

    def calculate_advanced_indicators(self, data):
        """Calculate comprehensive technical indicators"""
        if len(data) < 50:
            return None
        
        # Multiple EMAs for trend analysis
        data['9EMA'] = data['Close'].ewm(span=9, adjust=False).mean()
        data['21EMA'] = data['Close'].ewm(span=21, adjust=False).mean()
        data['50EMA'] = data['Close'].ewm(span=50, adjust=False).mean()
        data['200EMA'] = data['Close'].ewm(span=200, adjust=False).mean()
        
        # RSI
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        data['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        data['MACD'] = data['Close'].ewm(span=12, adjust=False).mean() - data['Close'].ewm(span=26, adjust=False).mean()
        data['Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()
        data['MACD_Hist'] = data['MACD'] - data['Signal']
        
        # Bollinger Bands
        data['BB_Middle'] = data['Close'].rolling(window=20).mean()
        bb_std = data['Close'].rolling(window=20).std()
        data['BB_Upper'] = data['BB_Middle'] + (bb_std * 2)
        data['BB_Lower'] = data['BB_Middle'] - (bb_std * 2)
        
        # ATR for volatility
        data['High-Low'] = data['High'] - data['Low']
        data['High-Close'] = abs(data['High'] - data['Close'].shift())
        data['Low-Close'] = abs(data['Low'] - data['Close'].shift())
        data['TR'] = data[['High-Low', 'High-Close', 'Low-Close']].max(axis=1)
        data['ATR'] = data['TR'].rolling(window=14).mean()
        
        # Volume analysis
        data['Volume_SMA'] = data['Volume'].rolling(window=20).mean()
        data['Volume_Ratio'] = data['Volume'] / data['Volume_SMA']
        
        # Price momentum
        data['ROC'] = ((data['Close'] - data['Close'].shift(10)) / data['Close'].shift(10)) * 100
        
        return data
    
    def predict_next_day_move(self, ticker, verbose=True, expiry_type='daily'):
        """Predict price movement and generate trade setup
        expiry_type: '0dte', 'daily', 'weekly', 'monthly'
        """
        try:
            # Get live current price (uses cached service layer with fallbacks)
            current_price = self.get_live_price(ticker)
            if current_price is None:
                if verbose:
                    print(f"  ❌ Could not get live price for {ticker}")
                return None
            
            if verbose:
                print(f"\n{'=' * 100}")
                print(f"📊 ANALYZING {ticker}")
                print(f"{'=' * 100}")
                print(f"  💹 Current Price: ${current_price:.2f}")
            
            # Get historical data for analysis (use cached service layer when available)
            data_daily = None
            try:
                from services.market_data import cached_get_history
                data_daily = cached_get_history(ticker, period='3mo', interval='1d')
            except ImportError:
                pass  # Standalone mode
            except Exception:
                pass  # Service layer failed
            
            if data_daily is None or (hasattr(data_daily, 'empty') and data_daily.empty):
                # Fallback to raw yfinance
                try:
                    stock = yf.Ticker(ticker)
                    data_daily = stock.history(period='3mo', interval='1d')
                except Exception as e:
                    if verbose:
                        print(f"  ❌ Could not get history for {ticker}: {e}")
                    return None
            
            if data_daily is None or len(data_daily) < 50:
                if verbose:
                    print(f"  ❌ Insufficient data for {ticker}")
                return None
            
            # Calculate indicators
            data_daily = self.calculate_advanced_indicators(data_daily)
            if data_daily is None:
                return None
            
            latest = data_daily.iloc[-1]
            prev = data_daily.iloc[-2]
            week_ago = data_daily.iloc[-5] if len(data_daily) >= 5 else prev
            
            # Analyze trend and momentum
            if verbose:
                print(f"\n  📈 TECHNICAL ANALYSIS:")
                print(f"     EMAs: 9={latest['9EMA']:.2f}, 21={latest['21EMA']:.2f}, 50={latest['50EMA']:.2f}, 200={latest['200EMA']:.2f}")
                print(f"     RSI: {latest['RSI']:.2f}")
                print(f"     MACD: {latest['MACD']:.2f}, Signal: {latest['Signal']:.2f}")
                print(f"     Bollinger: Lower={latest['BB_Lower']:.2f}, Upper={latest['BB_Upper']:.2f}")
                print(f"     ATR: ${latest['ATR']:.2f}")
                print(f"     Volume Ratio: {latest['Volume_Ratio']:.2f}x")
                print(f"     ROC (10-day): {latest['ROC']:.2f}%")
            
            # Determine bias (CALL or PUT)
            bullish_score = 0
            bearish_score = 0
            
            # Trend analysis
            if latest['9EMA'] > latest['21EMA'] > latest['50EMA']:
                bullish_score += 3
                if verbose: print(f"     ✅ Strong bullish EMA alignment")
            elif latest['9EMA'] < latest['21EMA'] < latest['50EMA']:
                bearish_score += 3
                if verbose: print(f"     ✅ Strong bearish EMA alignment")
            
            if current_price > latest['200EMA']:
                bullish_score += 2
                if verbose: print(f"     ✅ Price above 200 EMA (bullish long-term)")
            else:
                bearish_score += 2
                if verbose: print(f"     ✅ Price below 200 EMA (bearish long-term)")
            
            # MACD
            if latest['MACD'] > latest['Signal']:
                bullish_score += 2
                if prev['MACD'] <= prev['Signal']:
                    bullish_score += 1
                    if verbose: print(f"     ✅ Fresh MACD bullish crossover")
            else:
                bearish_score += 2
                if prev['MACD'] >= prev['Signal']:
                    bearish_score += 1
                    if verbose: print(f"     ✅ Fresh MACD bearish crossover")
            
            # RSI
            if latest['RSI'] < 40:
                bullish_score += 2
                if verbose: print(f"     ✅ RSI oversold - bounce likely")
            elif latest['RSI'] > 60:
                bearish_score += 2
                if verbose: print(f"     ✅ RSI overbought - pullback likely")
            
            # Bollinger Bands
            if current_price <= latest['BB_Lower']:
                bullish_score += 2
                if verbose: print(f"     ✅ At lower Bollinger Band - mean reversion up")
            elif current_price >= latest['BB_Upper']:
                bearish_score += 2
                if verbose: print(f"     ✅ At upper Bollinger Band - mean reversion down")
            
            # Volume
            if latest['Volume_Ratio'] > 1.5:
                if verbose: print(f"     ✅ High volume - strong conviction")
                if bullish_score > bearish_score:
                    bullish_score += 1
                else:
                    bearish_score += 1
            
            # Momentum
            if latest['ROC'] > 2:
                bullish_score += 1
                if verbose: print(f"     ✅ Strong positive momentum")
            elif latest['ROC'] < -2:
                bearish_score += 1
                if verbose: print(f"     ✅ Strong negative momentum")
            
            if verbose:
                print(f"\n  📊 PREDICTION SCORE:")
                print(f"     Bullish Score: {bullish_score}/15")
                print(f"     Bearish Score: {bearish_score}/15")
            
            # Determine direction
            is_weak_signal = False
            if bullish_score > bearish_score and bullish_score >= 7:
                direction = 'CALL'
                confidence = bullish_score
                if verbose: print(f"\n  🎯 PREDICTION: CALL (Bullish)")
            elif bearish_score > bullish_score and bearish_score >= 7:
                direction = 'PUT'
                confidence = bearish_score
                if verbose: print(f"\n  🎯 PREDICTION: PUT (Bearish)")
            else:
                # Weak signal - still return data but mark as not recommended
                is_weak_signal = True
                if bullish_score > bearish_score:
                    direction = 'CALL'
                    confidence = bullish_score
                elif bearish_score > bullish_score:
                    direction = 'PUT'
                    confidence = bearish_score
                else:
                    # Equal scores - default to bearish for safety
                    direction = 'PUT'
                    confidence = bearish_score
                if verbose:
                    print(f"\n  ⚠️  WEAK SIGNAL - Not recommended (scores too close or too low)")
                    print(f"     Direction: {direction} (Low confidence: {confidence}/15)")
            
            # Calculate expected price range for tomorrow
            atr = latest['ATR']
            daily_volatility = atr / current_price * 100
            
            # Expected move (1 ATR for next day)
            expected_high = current_price + atr
            expected_low = current_price - atr
            
            if verbose:
                print(f"\n  📈 EXPECTED TOMORROW'S RANGE:")
                print(f"     Low: ${expected_low:.2f}")
                print(f"     High: ${expected_high:.2f}")
                print(f"     Daily Volatility: {daily_volatility:.2f}%")
            
            # Calculate option strike and pricing based on expiry type
            # Different expiry types need different strike offsets and time values
            if expiry_type == '0dte':
                # 0DTE: Very close to ATM, minimal time value
                call_strike_mult = 0.05   # Barely OTM
                put_strike_mult = 0.05
                time_value_mult = 0.15    # Very low time value (expires today)
                target_atr_mult = 0.4     # Smaller expected move
                premium_cap = 0.01        # Cap at 1% of stock price
            elif expiry_type == 'daily':
                # 1DTE: Slightly OTM
                call_strike_mult = 0.1
                put_strike_mult = 0.2
                time_value_mult = 0.3     # Low time value
                target_atr_mult = 0.6
                premium_cap = 0.015
            elif expiry_type == 'weekly':
                # Weekly: More OTM for better risk/reward
                call_strike_mult = 0.3
                put_strike_mult = 0.4
                time_value_mult = 0.6     # Moderate time value
                target_atr_mult = 1.2     # Larger expected move
                premium_cap = 0.025
            else:  # monthly
                # Monthly: Further OTM, higher time value
                call_strike_mult = 0.6
                put_strike_mult = 0.7
                time_value_mult = 1.2     # Significant time value
                target_atr_mult = 2.0     # Large expected move
                premium_cap = 0.04
            
            if direction == 'CALL':
                strike_offset = atr * call_strike_mult
                strike_price = round(current_price + strike_offset, 0)
                
                intrinsic = max(0, current_price - strike_price)
                time_value = atr * time_value_mult
                estimated_premium = max(intrinsic + time_value, atr * (time_value_mult * 0.5))
                estimated_premium = min(estimated_premium, current_price * premium_cap)
                
                target_price = current_price + (atr * target_atr_mult)
                
            else:  # PUT
                strike_offset = atr * put_strike_mult
                strike_price = round(current_price + strike_offset, 0)
                
                intrinsic = max(0, strike_price - current_price)
                time_value = atr * time_value_mult
                estimated_premium = intrinsic + time_value
                estimated_premium = min(estimated_premium, current_price * premium_cap)
                
                target_price = current_price - (atr * target_atr_mult)
            
            # Calculate risk/reward
            stop_loss_premium = estimated_premium * 0.5  # 50% stop loss
            risk = estimated_premium - stop_loss_premium
            
            # For 1:5 ratio
            target_premium_5x = estimated_premium + (risk * self.target_rr)
            # For 1:3 ratio
            target_premium_3x = estimated_premium + (risk * self.min_rr)
            
            # Try to get REAL option premium from market (pass expiry_type)
            real_option = self.get_real_option_premium(ticker, strike_price, option_type=direction.lower(), expiry_type=expiry_type)
            
            # Track bid/ask/last for transparency
            option_bid = 0
            option_ask = 0
            option_last = 0
            
            if real_option:
                option_bid = real_option.get('bid', 0) or 0
                option_ask = real_option.get('ask', 0) or 0
                option_last = real_option.get('lastPrice', 0) or 0
                
                # Use real market data - but only if premium is meaningful
                actual_premium = real_option['mid']  # Mid price (avg of bid/ask)
                
                # Fallback: if mid is 0 (illiquid), try lastPrice, then keep estimate
                if actual_premium <= 0:
                    actual_premium = real_option.get('lastPrice', 0)
                
                if actual_premium > 0:
                    estimated_premium = actual_premium
                    
                    # Recalculate targets with real premium
                    stop_loss_premium = actual_premium * 0.5
                    risk = actual_premium - stop_loss_premium
                    target_premium_5x = actual_premium + (risk * self.target_rr)
                    target_premium_3x = actual_premium + (risk * self.min_rr)
                    
                    premium_source = 'LIVE'
                    strike_price = real_option['strike']  # Use actual strike from market
                else:
                    # Real option found but no valid premium - keep estimate
                    premium_source = 'ESTIMATED'
                    # Still use the matched strike from market for accuracy
                    strike_price = real_option['strike']
            else:
                premium_source = 'ESTIMATED'
            
            # Calculate expiry date based on expiry_type
            from datetime import datetime, timedelta
            today = datetime.now()
            
            # Determine target expiry based on type
            if expiry_type == '0dte':
                # Same day expiry
                next_day = today
                # If market is closed or after 3:30 PM, use next trading day
                if today.hour >= 15 or today.weekday() >= 5:
                    next_day = today + timedelta(days=1)
                    while next_day.weekday() >= 5:
                        next_day += timedelta(days=1)
                expiry_label = '0DTE (Same Day)'
                exit_time = '3:00 PM CT / 4:00 PM ET'
            elif expiry_type == 'weekly':
                # Find next Friday
                days_until_friday = (4 - today.weekday()) % 7
                if days_until_friday == 0 and today.hour >= 15:
                    days_until_friday = 7  # Next Friday if current Friday after 3 PM
                next_day = today + timedelta(days=max(days_until_friday, 1))
                expiry_label = 'Weekly (Friday)'
                exit_time = '3:00 PM CT / 4:00 PM ET Friday'
            elif expiry_type == 'monthly':
                # Find 3rd Friday of current/next month
                import calendar
                year = today.year
                month = today.month
                c = calendar.monthcalendar(year, month)
                # Find third Friday
                third_friday = None
                fridays = [week[4] for week in c if week[4] != 0]
                if len(fridays) >= 3:
                    third_friday = fridays[2]
                    expiry_date_obj = datetime(year, month, third_friday)
                    if expiry_date_obj <= today:
                        # Move to next month
                        month += 1
                        if month > 12:
                            month = 1
                            year += 1
                        c = calendar.monthcalendar(year, month)
                        fridays = [week[4] for week in c if week[4] != 0]
                        third_friday = fridays[2]
                    next_day = datetime(year, month, third_friday)
                else:
                    # Fallback to next month
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                    c = calendar.monthcalendar(year, month)
                    fridays = [week[4] for week in c if week[4] != 0]
                    third_friday = fridays[2]
                    next_day = datetime(year, month, third_friday)
                expiry_label = 'Monthly (3rd Friday)'
                exit_time = '3:00 PM CT / 4:00 PM ET on Expiry'
            else:  # 'daily' or default
                next_day = today + timedelta(days=1)
                # Skip weekends
                while next_day.weekday() >= 5:
                    next_day += timedelta(days=1)
                expiry_label = 'Next Day (1DTE)'
                exit_time = '3:00 PM CT / 4:00 PM ET'
            
            expiry_date = next_day.strftime('%m/%d/%Y')
            expiry_day = next_day.strftime('%a')
            
            # Determine actual expiry type based on ticker for display
            etf_tickers = MASTER_ETF_UNIVERSE
            if ticker in etf_tickers:
                display_expiry_type = expiry_type.upper()
            else:
                display_expiry_type = expiry_type.upper()
            
            return {
                'ticker': ticker,
                'current_price': current_price,
                'direction': direction,
                'confidence': confidence,
                'signal_strength': confidence,
                'signal': direction,
                'strike_price': strike_price,
                'estimated_premium': estimated_premium,
                'entry_premium': estimated_premium,
                'premium_source': premium_source,  # 'LIVE' or 'ESTIMATED'
                'option_bid': round(option_bid, 2),
                'option_ask': round(option_ask, 2),
                'option_last': round(option_last, 2),
                'entry_price': current_price,
                'target_premium_3x': target_premium_3x,
                'target_premium_5x': target_premium_5x,
                'stop_loss_premium': stop_loss_premium,
                'target_price': target_price,
                'expected_high': expected_high,
                'expected_low': expected_low,
                'atr': atr,
                'rsi': latest['RSI'],
                'bullish_score': bullish_score,
                'bearish_score': bearish_score,
                'expiry': expiry_label,
                'expiry_date': expiry_date,
                'expiry_day': expiry_day,
                'expiry_type': display_expiry_type,
                'exit_time': exit_time,
                'is_weak_signal': is_weak_signal,
                'weak_signal_reason': f'Low confidence score ({confidence}/15). Scores too close: Bullish {bullish_score}/15 vs Bearish {bearish_score}/15' if is_weak_signal else None
            }
            
        except Exception as e:
            print(f"  ❌ Error: {str(e)}")
            return None
    
    def display_trade_setup(self, prediction):
        """Display formatted trade setup"""
        if prediction is None:
            return
        
        print(f"\n{'=' * 100}")
        print(f"🎯 RECOMMENDED TRADE SETUP FOR TOMORROW")
        print(f"{'=' * 100}")
        print(f"\n📋 TRADE DETAILS:")
        print(f"   Ticker: {prediction['ticker']}")
        print(f"   Direction: {prediction['direction']} Option")
        print(f"   Confidence: {prediction['confidence']}/15")
        print(f"   Expiry: {prediction['expiry']}")
        print(f"   Exit Time: {prediction['exit_time']}")
        
        print(f"\n💰 OPTION SPECIFICATIONS:")
        print(f"   Current Stock Price: ${prediction['current_price']:.2f}")
        print(f"   Strike Price: ${prediction['strike_price']:.0f}")
        print(f"   Estimated Premium: ${prediction['estimated_premium']:.2f} per share")
        print(f"   Cost per Contract: ${prediction['estimated_premium'] * 100:.2f}")
        
        print(f"\n🎯 PROFIT TARGETS:")
        print(f"   1:3 Target Premium: ${prediction['target_premium_3x']:.2f} (+{((prediction['target_premium_3x']/prediction['estimated_premium']-1)*100):.0f}%)")
        print(f"   1:5 Target Premium: ${prediction['target_premium_5x']:.2f} (+{((prediction['target_premium_5x']/prediction['estimated_premium']-1)*100):.0f}%)")
        print(f"   Stop Loss Premium: ${prediction['stop_loss_premium']:.2f} (-50%)")
        
        print(f"\n📈 EXPECTED STOCK MOVEMENT:")
        print(f"   Target Price: ${prediction['target_price']:.2f}")
        print(f"   Expected Range: ${prediction['expected_low']:.2f} - ${prediction['expected_high']:.2f}")
        print(f"   ATR (Volatility): ${prediction['atr']:.2f}")
        
        print(f"\n💡 ENTRY STRATEGY:")
        print(f"   ⏰ Best Entry Time: 10:00 AM - 11:30 AM CT (11:00 AM - 12:30 PM ET)")
        print(f"   📊 Enter when RSI confirms direction")
        print(f"   🎯 Use limit orders at estimated premium or better")
        
        print(f"\n⚠️  EXIT STRATEGY:")
        print(f"   ✅ Take 1:3 profit: Close 50% of position")
        print(f"   ✅ Take 1:5 profit: Close remaining 50%")
        print(f"   🛑 Stop Loss: -50% from entry")
        print(f"   ⏰ MANDATORY EXIT: 3:00 PM CT (4:00 PM ET) - No exceptions!")
        
        # Risk calculations
        cost_1_contract = prediction['estimated_premium'] * 100
        cost_2_contracts = cost_1_contract * 2
        
        profit_3x_1c = (prediction['target_premium_3x'] - prediction['estimated_premium']) * 100
        profit_5x_1c = (prediction['target_premium_5x'] - prediction['estimated_premium']) * 100
        
        print(f"\n💵 POSITION SIZING (Examples):")
        print(f"   1 Contract:")
        print(f"      Cost: ${cost_1_contract:.2f}")
        print(f"      1:3 Profit: ${profit_3x_1c:.2f}")
        print(f"      1:5 Profit: ${profit_5x_1c:.2f}")
        print(f"      Max Loss: ${cost_1_contract * 0.5:.2f}")
        
        print(f"\n   2 Contracts:")
        print(f"      Cost: ${cost_2_contracts:.2f}")
        print(f"      1:3 Profit: ${profit_3x_1c * 2:.2f}")
        print(f"      1:5 Profit: ${profit_5x_1c * 2:.2f}")
        print(f"      Max Loss: ${cost_2_contracts * 0.5:.2f}")
        
        print(f"\n{'=' * 100}")
    
    def setup_trade_alerts(self, prediction):
        """Set up trade alerts and entry/exit levels"""
        if prediction is None:
            return None
        
        ticker = prediction['ticker']
        direction = prediction['direction']
        
        # Create trade setup
        trade_setup = {
            'ticker': ticker,
            'direction': direction,
            'strike': prediction['strike_price'],
            'entry_premium': prediction['estimated_premium'],
            'current_premium': None,
            'target_3x': prediction['target_premium_3x'],
            'target_5x': prediction['target_premium_5x'],
            'stop_loss': prediction['stop_loss_premium'],
            'status': 'SETUP',
            'position_size': 0,
            'entry_time': None,
            'pnl': 0,
            'alerts': {
                '1:3_hit': False,
                '1:5_hit': False,
                'stop_loss_hit': False
            }
        }
        
        return trade_setup
    
    def monitor_live_price(self, ticker):
        """Monitor live price with 1-minute updates"""
        print(f"\n🔴 LIVE: Monitoring {ticker}...")
        price = self.get_live_price(ticker)
        if price:
            print(f"   Current Price: ${price:.2f} | Time: {datetime.now().strftime('%I:%M:%S %p')}")
        return price
    
    def update_trade_status(self, trade_setup, current_price):
        """Update trade status based on current price movement"""
        if trade_setup['status'] == 'SETUP':
            return trade_setup
        
        ticker = trade_setup['ticker']
        direction = trade_setup['direction']
        entry = trade_setup['entry_premium']
        
        # Simulate premium change based on price movement
        # This is simplified - in reality, you'd get actual options data
        price_change_pct = ((current_price - entry) / entry) * 100
        
        # Options amplify movement (rough estimate)
        if direction == 'CALL':
            premium_multiplier = 1 + (price_change_pct / 100 * 5)  # 5x leverage estimate
        else:  # PUT
            premium_multiplier = 1 + (-price_change_pct / 100 * 5)
        
        estimated_current_premium = entry * premium_multiplier
        trade_setup['current_premium'] = estimated_current_premium
        
        # Calculate P&L
        if trade_setup['position_size'] > 0:
            trade_setup['pnl'] = (estimated_current_premium - entry) * 100 * trade_setup['position_size']
        
        # Check targets and stops
        if estimated_current_premium >= trade_setup['target_5x'] and not trade_setup['alerts']['1:5_hit']:
            print(f"   🎯 {ticker} 1:5 TARGET HIT! Premium: ${estimated_current_premium:.2f}")
            trade_setup['alerts']['1:5_hit'] = True
        elif estimated_current_premium >= trade_setup['target_3x'] and not trade_setup['alerts']['1:3_hit']:
            print(f"   ✅ {ticker} 1:3 TARGET HIT! Premium: ${estimated_current_premium:.2f}")
            trade_setup['alerts']['1:3_hit'] = True
        elif estimated_current_premium <= trade_setup['stop_loss'] and not trade_setup['alerts']['stop_loss_hit']:
            print(f"   🛑 {ticker} STOP LOSS HIT! Premium: ${estimated_current_premium:.2f}")
            trade_setup['alerts']['stop_loss_hit'] = True
        
        return trade_setup
    
    def real_time_monitor(self, predictions, duration_minutes=60):
        """Monitor trades in real-time with live updates"""
        if not predictions:
            print("No predictions to monitor")
            return
        
        print(f"\n{'=' * 100}")
        print(f"🔴 STARTING REAL-TIME MONITORING")
        print(f"{'=' * 100}")
        print(f"Duration: {duration_minutes} minutes")
        print(f"Update Interval: 60 seconds")
        print(f"Monitoring: {', '.join([p['ticker'] for p in predictions])}")
        print(f"{'=' * 100}")
        
        # Set up trade monitoring for each prediction
        trade_setups = {}
        for pred in predictions:
            setup = self.setup_trade_alerts(pred)
            if setup:
                trade_setups[pred['ticker']] = setup
                print(f"\n📋 {pred['ticker']} Trade Setup Ready:")
                print(f"   Direction: {pred['direction']}")
                print(f"   Strike: ${pred['strike_price']:.0f}")
                print(f"   Entry Premium: ${pred['estimated_premium']:.2f}")
                print(f"   Target 1:3: ${pred['target_premium_3x']:.2f}")
                print(f"   Target 1:5: ${pred['target_premium_5x']:.2f}")
                print(f"   Stop Loss: ${pred['stop_loss_premium']:.2f}")
        
        self.monitoring = True
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)
        update_count = 0
        
        try:
            while self.monitoring and datetime.now() < end_time:
                update_count += 1
                print(f"\n{'─' * 100}")
                print(f"📊 UPDATE #{update_count} | {datetime.now().strftime('%I:%M:%S %p')} | Market: {'🟢 OPEN' if self.is_market_open() else '🔴 CLOSED'}")
                print(f"{'─' * 100}")
                
                # Update each position
                for ticker, setup in trade_setups.items():
                    current_price = self.monitor_live_price(ticker)
                    if current_price:
                        setup = self.update_trade_status(setup, current_price)
                        trade_setups[ticker] = setup
                        
                        # Display current status
                        if setup['current_premium']:
                            pnl_pct = ((setup['current_premium'] - setup['entry_premium']) / setup['entry_premium']) * 100
                            print(f"   Premium: ${setup['current_premium']:.2f} ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.1f}%)")
                            if setup['position_size'] > 0:
                                print(f"   P&L: ${setup['pnl']:.2f}")
                
                # Check if we should exit (3:00 PM CT = 4:00 PM ET)
                current_time = datetime.now().time()
                exit_time = datetime.strptime('15:00', '%H:%M').time()  # 3:00 PM CT
                if current_time >= exit_time:
                    print(f"\n⚠️  EXIT TIME REACHED - CLOSE ALL POSITIONS!")
                    self.monitoring = False
                    break
                
                # Wait before next update
                time.sleep(60)  # Update every 60 seconds
                
        except KeyboardInterrupt:
            print(f"\n\n⚠️  Monitoring stopped by user")
            self.monitoring = False
        
        # Final summary
        print(f"\n{'=' * 100}")
        print(f"📊 MONITORING SESSION COMPLETE")
        print(f"{'=' * 100}")
        for ticker, setup in trade_setups.items():
            print(f"\n{ticker}:")
            print(f"  1:3 Target: {'✅ HIT' if setup['alerts']['1:3_hit'] else '❌ MISSED'}")
            print(f"  1:5 Target: {'✅ HIT' if setup['alerts']['1:5_hit'] else '❌ MISSED'}")
            print(f"  Stop Loss: {'🛑 HIT' if setup['alerts']['stop_loss_hit'] else '✅ SAFE'}")
        print(f"{'=' * 100}")
    
    def generate_predictions(self):
        """Generate predictions for SPY and QQQ"""
        tickers = [symbol for symbol in MASTER_ETF_UNIVERSE if symbol in {'SPY', 'QQQ'}]
        predictions = []
        
        for ticker in tickers:
            prediction = self.predict_next_day_move(ticker)
            if prediction:
                predictions.append(prediction)
                self.display_trade_setup(prediction)
        
        # Summary comparison
        if len(predictions) == 2:
            print(f"\n{'=' * 100}")
            print(f"📊 SUMMARY COMPARISON")
            print(f"{'=' * 100}")
            
            for pred in predictions:
                print(f"\n{pred['ticker']}: {pred['direction']} ${pred['strike_price']:.0f}")
                print(f"  Confidence: {pred['confidence']}/15")
                print(f"  Premium: ${pred['estimated_premium']:.2f}")
                print(f"  1:5 Target: ${pred['target_premium_5x']:.2f}")
            
            # Recommend best trade
            best = max(predictions, key=lambda x: x['confidence'])
            print(f"\n🏆 HIGHEST CONFIDENCE: {best['ticker']} {best['direction']} (Score: {best['confidence']}/15)")
        
        return predictions
    
    def analyze_stock(self, ticker, verbose=False, expiry_type='daily'):
        """Analyze a single stock for web API - returns simplified dict
        expiry_type: '0dte', 'daily', 'weekly', 'monthly'
        """
        try:
            prediction = self.predict_next_day_move(ticker, verbose=verbose, expiry_type=expiry_type)
            if prediction is None:
                return None
            
            # Return simplified format for web display with PREMIUM targets (not stock price)
            return {
                'ticker': prediction['ticker'],
                'signal': prediction['direction'],
                'entry_price': prediction['current_price'],
                'strike_price': prediction['strike_price'],
                'entry_premium': prediction['estimated_premium'],
                'premium': prediction['estimated_premium'],  # Kept for backward compatibility
                'target_premium_3x': prediction['target_premium_3x'],  # 1:3 premium target
                'target_premium_5x': prediction['target_premium_5x'],  # 1:5 premium target
                'stop_loss_premium': prediction['stop_loss_premium'],
                'stop_loss': prediction['stop_loss_premium'],  # Kept for backward compatibility
                'stock_target_price': prediction['target_price'],  # Stock price expectation (for reference)
                'risk_reward_min': self.min_rr,  # 1:3
                'risk_reward_max': self.target_rr,  # 1:5
                'signal_strength': prediction['confidence'],
                'rsi': prediction['rsi'],
                'atr': prediction['atr'],
                'expected_range': f"${prediction['expected_low']:.2f} - ${prediction['expected_high']:.2f}",
                'expiry': prediction['expiry'],
                'expiry_date': prediction['expiry_date'],
                'expiry_day': prediction['expiry_day'],
                'expiry_type': prediction['expiry_type'],
                'exit_time': prediction['exit_time'],
                'is_weak_signal': prediction.get('is_weak_signal', False),
                'weak_signal_reason': prediction.get('weak_signal_reason'),
                'bullish_score': prediction.get('bullish_score', 0),
                'bearish_score': prediction.get('bearish_score', 0)
            }
        except Exception as e:
            # Silent fail for web API
            return None


# -----------------------------
# MAIN EXECUTION
# -----------------------------

if __name__ == "__main__":
    import sys
    
    # Check if real-time mode is requested
    real_time = '--live' in sys.argv or '--realtime' in sys.argv
    monitor_duration = 60  # Default 60 minutes
    
    # Check for custom duration
    for arg in sys.argv:
        if arg.startswith('--duration='):
            try:
                monitor_duration = int(arg.split('=')[1])
            except:
                pass
    
    # Initialize predictor
    predictor = NextDayOptionsPredictor(real_time_mode=real_time)
    
    # Generate predictions
    predictions = predictor.generate_predictions()
    
    if real_time and predictions:
        print(f"\n{'=' * 100}")
        print(f"🔴 REAL-TIME MODE ACTIVATED")
        print(f"{'=' * 100}")
        
        # Ask user if they want to start monitoring
        response = input("\n⚠️  Start real-time monitoring? (y/n): ").strip().lower()
        
        if response == 'y':
            predictor.real_time_monitor(predictions, duration_minutes=monitor_duration)
        else:
            print("\nReal-time monitoring cancelled.")
    
    print(f"\n{'=' * 100}")
    print(f"⚠️  IMPORTANT REMINDERS")
    print(f"{'=' * 100}")
    print(f"1. These are PREDICTIONS based on technical analysis")
    print(f"2. Options can move quickly - monitor every 15-30 minutes")
    print(f"3. MUST exit by 3:00 PM CT (4:00 PM ET) tomorrow")
    print(f"4. Use stop loss discipline - protect your capital")
    print(f"5. Take partial profits at 1:3, let rest run to 1:5")
    print(f"6. Market conditions can change overnight - reassess in morning")
    print(f"\n💡 TIP: Run with --live flag for real-time monitoring")
    print(f"   Example: python next_day_options_predictor.py --live --duration=120")
    print(f"=" * 100)
