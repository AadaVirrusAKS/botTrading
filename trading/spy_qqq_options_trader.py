import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# Use centralized caching layer to avoid Yahoo rate limiting
try:
    from services.market_data import (
        cached_get_history, cached_get_price, cached_get_ticker_info,
        _is_globally_rate_limited
    )
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False

# -----------------------------
# SPY & QQQ DAILY OPTIONS TRADING BOT
# Target: 1:10 Risk/Reward Ratio on Daily Expiry Options
# -----------------------------

class DailyOptionsTrader:
    def __init__(self, capital=10000, risk_per_trade=0.02, target_rr=10.0):
        """
        Initialize Options Trading Bot
        
        Parameters:
        - capital: Starting capital ($10,000 default)
        - risk_per_trade: Risk percentage per trade (2% default)
        - target_rr: Target risk/reward ratio (10:1 default)
        """
        self.initial_capital = capital
        self.available_capital = capital
        self.risk_per_trade = risk_per_trade
        self.target_rr = target_rr
        
        # Trading state
        self.open_positions = {}
        self.closed_trades = []
        self.trade_log = []
        
        print("=" * 100)
        print("📈 SPY & QQQ DAILY OPTIONS TRADING BOT")
        print("=" * 100)
        print(f"Initial Capital: ${self.initial_capital:,.2f}")
        print(f"Risk per Trade: {self.risk_per_trade * 100}%")
        print(f"Target Risk/Reward: 1:{self.target_rr}")
        print(f"Max Risk per Trade: ${self.initial_capital * self.risk_per_trade:,.2f}")
        print(f"Strategy: Daily Expiry Options (0DTE)")
        print("=" * 100)
    
    def calculate_indicators(self, data):
        """Calculate technical indicators for options signals"""
        if len(data) < 20:  # Lowered requirement
            return None
        
        # Short-term EMAs for intraday trading
        data['9EMA'] = data['Close'].ewm(span=9, adjust=False).mean()
        data['21EMA'] = data['Close'].ewm(span=21, adjust=False).mean()
        
        # Use shorter EMA if not enough data for 50EMA
        if len(data) >= 50:
            data['50EMA'] = data['Close'].ewm(span=50, adjust=False).mean()
        else:
            data['50EMA'] = data['Close'].ewm(span=min(len(data)-1, 30), adjust=False).mean()
        
        # RSI (14 period)
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        data['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        data['MACD'] = data['Close'].ewm(span=12, adjust=False).mean() - data['Close'].ewm(span=26, adjust=False).mean()
        data['Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()
        
        # ATR for volatility
        data['High-Low'] = data['High'] - data['Low']
        data['High-Close'] = abs(data['High'] - data['Close'].shift())
        data['Low-Close'] = abs(data['Low'] - data['Close'].shift())
        data['TR'] = data[['High-Low', 'High-Close', 'Low-Close']].max(axis=1)
        data['ATR'] = data['TR'].rolling(window=14).mean()
        
        # Volume
        data['Volume_MA'] = data['Volume'].rolling(window=20).mean()
        
        return data
    
    def get_live_price(self, ticker):
        """Get real-time live price from Yahoo Finance"""
        try:
            if _USE_CACHED:
                price, _ = cached_get_price(ticker)
                if price:
                    return price
                # Fallback to ticker info
                info = cached_get_ticker_info(ticker) or {}
                return info.get('currentPrice') or info.get('regularMarketPrice') or info.get('previousClose')

            stock = yf.Ticker(ticker)
            # Try to get the most recent price
            info = stock.info
            
            # Try multiple price sources for live data
            live_price = None
            if 'currentPrice' in info and info['currentPrice']:
                live_price = info['currentPrice']
            elif 'regularMarketPrice' in info and info['regularMarketPrice']:
                live_price = info['regularMarketPrice']
            elif 'previousClose' in info and info['previousClose']:
                # Fallback to previous close if market is closed
                live_price = info['previousClose']
            
            # Verify with recent intraday data
            intraday = stock.history(period='1d', interval='1m')
            if len(intraday) > 0:
                latest_intraday = intraday['Close'].iloc[-1]
                # Use intraday if available (more current)
                if latest_intraday > 0:
                    live_price = latest_intraday
            
            return live_price
        except:
            return None
    
    def analyze_for_options(self, ticker):
        """
        Analyze stock for options trading signals
        Returns: Call or Put signal with strike and premium estimates
        """
        try:
            # Get LIVE current price first
            live_price = self.get_live_price(ticker)
            if live_price is None or live_price <= 0:
                print(f"  ❌ Could not get live price for {ticker}")
                return None
            
            print(f"  💹 LIVE Price: ${live_price:.2f}")

            if _USE_CACHED:
                if _is_globally_rate_limited():
                    return None
                data_daily = cached_get_history(ticker, period='1mo', interval='1d')
                data_intraday = cached_get_history(ticker, period='1d', interval='5m')
            else:
                stock = yf.Ticker(ticker)
                data_daily = stock.history(period='1mo', interval='1d')
                data_intraday = stock.history(period='1d', interval='5m')
            
            print(f"  📊 Data points: {len(data_daily)} daily candles, {len(data_intraday)} intraday candles")
            
            if len(data_daily) < 20:  # Lowered threshold
                print(f"  ❌ Insufficient data: {len(data_daily)} days")
                return None
            
            # Calculate indicators
            data_daily = self.calculate_indicators(data_daily)
            if data_daily is None:
                return None
            
            # Use LIVE price for analysis
            current_price = live_price
            latest_daily = data_daily.iloc[-1]
            prev_daily = data_daily.iloc[-2] if len(data_daily) >= 2 else latest_daily
            
            # Determine trend and momentum
            bullish_trend = (latest_daily['9EMA'] > latest_daily['21EMA'] and 
                           latest_daily['21EMA'] > latest_daily['50EMA'])
            bearish_trend = (latest_daily['9EMA'] < latest_daily['21EMA'] and 
                           latest_daily['21EMA'] < latest_daily['50EMA'])
            
            # MACD crossover
            macd_bullish = latest_daily['MACD'] > latest_daily['Signal'] and prev_daily['MACD'] <= prev_daily['Signal']
            macd_bearish = latest_daily['MACD'] < latest_daily['Signal'] and prev_daily['MACD'] >= prev_daily['Signal']
            
            # RSI conditions
            rsi = latest_daily['RSI']
            rsi_oversold = rsi < 35
            rsi_overbought = rsi > 65
            rsi_neutral = 40 <= rsi <= 60
            
            # Volume confirmation
            volume_surge = latest_daily['Volume'] > latest_daily['Volume_MA'] * 1.2
            
            # ATR for volatility
            atr = latest_daily['ATR']
            atr_percent = (atr / current_price) * 100
            
            # Intraday momentum (last 30 minutes or available data)
            if len(data_intraday) >= 6:
                recent_candles = data_intraday.tail(6)  # Last 30 min (6 x 5-min candles)
                intraday_momentum = (recent_candles['Close'].iloc[-1] - recent_candles['Close'].iloc[0]) / recent_candles['Close'].iloc[0] * 100
            elif len(data_intraday) > 0:
                intraday_momentum = (data_intraday['Close'].iloc[-1] - data_intraday['Close'].iloc[0]) / data_intraday['Close'].iloc[0] * 100
            else:
                # Use daily change if no intraday data
                intraday_momentum = (current_price - latest_daily['Close']) / latest_daily['Close'] * 100
            
            # Decision logic for CALL or PUT (with live price analysis)
            signal_type = None
            confidence_call = 0
            confidence_put = 0
            
            # CALL signals (bullish)
            if bullish_trend:
                confidence_call += 3
            if macd_bullish:
                confidence_call += 2
            if rsi_oversold:
                confidence_call += 2
            elif rsi < 50 and not rsi_overbought:  # Neutral to slightly bullish RSI
                confidence_call += 1
            if intraday_momentum > 0.2:  # Positive momentum (lowered threshold)
                confidence_call += 2
            if volume_surge:
                confidence_call += 1
            
            # PUT signals (bearish)
            if bearish_trend:
                confidence_put += 3
            elif not bullish_trend:
                confidence_put += 1
            if macd_bearish:
                confidence_put += 2
            if rsi_overbought:
                confidence_put += 2
            elif rsi > 50 and not rsi_oversold:  # Neutral to slightly bearish RSI
                confidence_put += 1
            if intraday_momentum < -0.2:  # Negative momentum (lowered threshold)
                confidence_put += 2
            if volume_surge:
                confidence_put += 1
            
            # Choose strongest signal (lowered threshold for faster entries)
            if confidence_call >= 5 and confidence_call > confidence_put:
                signal_type = 'CALL'
                confidence = confidence_call
            elif confidence_put >= 5 and confidence_put > confidence_call:
                signal_type = 'PUT'
                confidence = confidence_put
            elif confidence_call >= 4:  # Even lower threshold if clearly bullish
                signal_type = 'CALL'
                confidence = confidence_call
            elif confidence_put >= 4:  # Even lower threshold if clearly bearish
                signal_type = 'PUT'
                confidence = confidence_put
            
            # Print diagnostic info
            print(f"  📊 Analysis: CALL confidence={confidence_call}/11, PUT confidence={confidence_put}/11")
            print(f"  📈 Trend: Bullish={bullish_trend}, Bearish={bearish_trend}, Neutral={not bullish_trend and not bearish_trend}")
            print(f"  📉 RSI: {rsi:.2f} (Oversold={rsi_oversold}, Overbought={rsi_overbought}, Neutral={rsi_neutral})")
            print(f"  🚀 Intraday Momentum: {intraday_momentum:+.2f}%")
            print(f"  📊 MACD: Bullish Cross={macd_bullish}, Bearish Cross={macd_bearish}")
            print(f"  📈 EMAs: 9={latest_daily['9EMA']:.2f}, 21={latest_daily['21EMA']:.2f}, 50={latest_daily['50EMA']:.2f}")
            print(f"  💹 Price vs 50EMA: {((current_price/latest_daily['50EMA']-1)*100):+.2f}%")
            
            # FORCE a signal suggestion based on current conditions
            if signal_type is None:
                print(f"\n  ⚠️  No strong signal meeting strict criteria")
                print(f"  💡 SUGGESTED TRADE (lower confidence):")
                
                # Suggest based on which has higher confidence
                if confidence_call >= confidence_put and confidence_call >= 2:
                    signal_type = 'CALL'
                    confidence = confidence_call
                    print(f"     → CALL option (Confidence: {confidence_call}/11)")
                    print(f"     → Reason: Relatively more bullish indicators")
                elif confidence_put >= 2:
                    signal_type = 'PUT'
                    confidence = confidence_put
                    print(f"     → PUT option (Confidence: {confidence_put}/11)")
                    print(f"     → Reason: Relatively more bearish indicators")
                else:
                    # Default to momentum direction
                    if intraday_momentum > 0:
                        signal_type = 'CALL'
                        confidence = 2
                        print(f"     → CALL option (Confidence: 2/11)")
                        print(f"     → Reason: Positive intraday momentum ({intraday_momentum:+.2f}%)")
                    else:
                        signal_type = 'PUT'
                        confidence = 2
                        print(f"     → PUT option (Confidence: 2/11)")
                        print(f"     → Reason: Negative intraday momentum ({intraday_momentum:+.2f}%)")
                
                print(f"     ⚠️  WARNING: This is a speculative trade with lower confidence!")
            else:
                print(f"\n  ✅ STRONG SIGNAL: {signal_type} (Confidence: {confidence}/11)")
            
            # Calculate option parameters for 0DTE based on LIVE price
            # Use ATM or slightly ITM strikes for better premium and probability
            if signal_type == 'CALL':
                # Strike at or slightly below current price (ATM/ITM call)
                strike_offset = atr * 0.1  # 10% of ATR below for better entry
                strike_price = round(current_price - strike_offset, 0)
                
                # Calculate premium based on LIVE intrinsic + time value
                intrinsic_value = max(0, current_price - strike_price)
                # For 0DTE, time value should be realistic
                time_value = atr * 0.4  # Higher time value for ATM options
                estimated_premium = intrinsic_value + time_value
                
                print(f"  🎯 CALL Setup: Strike ${strike_price:.0f} (Current: ${current_price:.2f})")
                print(f"     Intrinsic: ${intrinsic_value:.2f}, Time Value: ${time_value:.2f}")
                print(f"     Total Premium: ${estimated_premium:.2f}")
                
            else:  # PUT
                # Strike at or slightly above current price (ATM/ITM put)
                strike_offset = atr * 0.1
                strike_price = round(current_price + strike_offset, 0)
                
                intrinsic_value = max(0, strike_price - current_price)
                time_value = atr * 0.4  # Higher time value for ATM options
                estimated_premium = intrinsic_value + time_value
                
                print(f"  🎯 PUT Setup: Strike ${strike_price:.0f} (Current: ${current_price:.2f})")
                print(f"     Intrinsic: ${intrinsic_value:.2f}, Time Value: ${time_value:.2f}")
                print(f"     Total Premium: ${estimated_premium:.2f}")
            
            # Risk management for 1:10 ratio
            max_risk = self.available_capital * self.risk_per_trade
            
            # For options: Risk = Premium paid per contract * number of contracts
            # Each option contract = 100 shares
            if estimated_premium <= 0:
                estimated_premium = atr * 0.1  # Minimum premium estimate
            
            cost_per_contract = estimated_premium * 100
            max_contracts = int(max_risk / cost_per_contract)
            
            if max_contracts < 1:
                max_contracts = 1
            
            total_risk = cost_per_contract * max_contracts
            
            # Calculate profit target (1:10)
            profit_target = total_risk * self.target_rr
            target_premium = estimated_premium + (profit_target / (100 * max_contracts))
            
            # Stop loss: 50% of premium (aggressive for 0DTE)
            stop_loss_premium = estimated_premium * 0.5
            
            return {
                'ticker': ticker,
                'signal_type': signal_type,
                'confidence': confidence,
                'current_price': current_price,
                'strike_price': strike_price,
                'estimated_premium': estimated_premium,
                'contracts': max_contracts,
                'cost_per_contract': cost_per_contract,
                'total_cost': total_risk,
                'target_premium': target_premium,
                'stop_loss_premium': stop_loss_premium,
                'profit_target': profit_target,
                'risk_reward': self.target_rr,
                'atr': atr,
                'rsi': rsi,
                'intraday_momentum': intraday_momentum,
                'expiry': 'Daily (0DTE)',
                'bullish_trend': bullish_trend,
                'bearish_trend': bearish_trend
            }
            
        except Exception as e:
            print(f"  ❌ Error analyzing {ticker}: {str(e)}")
            return None
    
    def execute_options_trade(self, analysis):
        """Execute options trade based on analysis"""
        if analysis is None:
            return False
        
        ticker = analysis['ticker']
        
        if analysis['total_cost'] > self.available_capital:
            print(f"  ⚠️  {ticker}: Insufficient capital")
            return False
        
        # Execute trade
        entry_time = datetime.now()
        
        self.open_positions[f"{ticker}_{entry_time.strftime('%H%M%S')}"] = {
            'ticker': ticker,
            'signal_type': analysis['signal_type'],
            'entry_time': entry_time,
            'strike_price': analysis['strike_price'],
            'entry_premium': analysis['estimated_premium'],
            'contracts': analysis['contracts'],
            'total_cost': analysis['total_cost'],
            'target_premium': analysis['target_premium'],
            'stop_loss_premium': analysis['stop_loss_premium'],
            'profit_target': analysis['profit_target'],
            'current_price': analysis['current_price'],
            'expiry': 'Daily (0DTE)',
            'confidence': analysis['confidence']
        }
        
        # Update capital
        self.available_capital -= analysis['total_cost']
        
        # Log trade
        self.trade_log.append({
            'action': 'BUY',
            'time': entry_time,
            'ticker': ticker,
            'option_type': analysis['signal_type'],
            'strike': analysis['strike_price'],
            'contracts': analysis['contracts'],
            'premium': analysis['estimated_premium'],
            'total_cost': analysis['total_cost']
        })
        
        print(f"\n  ✅ OPTIONS TRADE EXECUTED: {ticker}")
        print(f"     Type: {analysis['signal_type']}")
        print(f"     Strike: ${analysis['strike_price']:.2f}")
        print(f"     Contracts: {analysis['contracts']}")
        print(f"     Premium: ${analysis['estimated_premium']:.2f} per share")
        print(f"     Cost per Contract: ${analysis['cost_per_contract']:.2f}")
        print(f"     Total Cost: ${analysis['total_cost']:.2f}")
        print(f"     Target Premium: ${analysis['target_premium']:.2f} ({((analysis['target_premium']/analysis['estimated_premium']-1)*100):+.1f}%)")
        print(f"     Stop Loss: ${analysis['stop_loss_premium']:.2f} (-50%)")
        print(f"     Target Profit: ${analysis['profit_target']:.2f}")
        print(f"     Risk/Reward: 1:{analysis['risk_reward']}")
        print(f"     Expiry: {analysis['expiry']}")
        print(f"     Confidence: {analysis['confidence']}/10")
        print(f"     Remaining Capital: ${self.available_capital:,.2f}")
        
        return True
    
    def monitor_options_positions(self):
        """Monitor open options positions"""
        if not self.open_positions:
            print("\n  📊 No open positions to monitor")
            return
        
        print(f"\n{'=' * 100}")
        print(f"📊 MONITORING {len(self.open_positions)} OPEN OPTIONS POSITIONS")
        print(f"{'=' * 100}")
        
        positions_to_close = []
        
        for position_id, position in self.open_positions.items():
            try:
                ticker = position['ticker']
                
                # Get LIVE current stock price
                current_stock_price = self.get_live_price(ticker)
                if current_stock_price is None:
                    print(f"  ⚠️  Could not get live price for {ticker}")
                    continue
                
                # Calculate ACTUAL intrinsic value based on live price
                if position['signal_type'] == 'CALL':
                    intrinsic_value = max(0, current_stock_price - position['strike_price'])
                else:  # PUT
                    intrinsic_value = max(0, position['strike_price'] - current_stock_price)
                
                # Time decay for 0DTE (rapid decay)
                hours_held = (datetime.now() - position['entry_time']).seconds / 3600
                
                # Don't monitor if held less than 5 minutes (avoid instant false triggers)
                if hours_held < 0.08:  # 5 minutes = 0.08 hours
                    print(f"\n{position_id} ({ticker} {position['signal_type']} ${position['strike_price']:.0f})")
                    print(f"  ⏳ Just entered - waiting 5 minutes before monitoring")
                    continue
                
                time_decay_factor = max(0, 1 - (hours_held / 6.5))  # Market hours: 6.5 hours
                
                # Current premium based on live intrinsic value + remaining time value
                # Keep significant time value for realistic pricing
                time_value = position['entry_premium'] * 0.5 * time_decay_factor
                current_premium = max(intrinsic_value + time_value, intrinsic_value * 1.1)  # Minimum 10% above intrinsic
                
                # P&L calculation
                current_value = current_premium * 100 * position['contracts']
                unrealized_pl = current_value - position['total_cost']
                unrealized_pl_pct = (unrealized_pl / position['total_cost']) * 100
                
                # Show price movement and option status
                price_change = current_stock_price - position['current_price']
                price_change_pct = (price_change / position['current_price']) * 100
                
                print(f"\n{position_id} ({ticker} {position['signal_type']} ${position['strike_price']:.0f})")
                print(f"  Stock Price: ${position['current_price']:.2f} → ${current_stock_price:.2f} ({price_change_pct:+.2f}%)")
                print(f"  Premium: ${position['entry_premium']:.2f} → ${current_premium:.2f}")
                print(f"  Intrinsic Value: ${intrinsic_value:.2f} | Time Value: ${time_value:.2f}")
                print(f"  Contracts: {position['contracts']}")
                print(f"  P&L: ${unrealized_pl:,.2f} ({unrealized_pl_pct:+.1f}%)")
                print(f"  Target: ${position['target_premium']:.2f} | Stop: ${position['stop_loss_premium']:.2f}")
                print(f"  Time Held: {hours_held:.1f} hours")
                
                # Check exit conditions
                # Check market close time (4:00 PM ET = 16:00)
                import pytz
                et_timezone = pytz.timezone('US/Eastern')
                current_time = datetime.now(et_timezone)
                market_close_hour = 16  # 4:00 PM ET
                market_close_warning = 15  # 3:00 PM ET - start closing positions
                
                # CRITICAL: Force close all positions after 3:45 PM (15.75 hours from 9:30 AM open)
                # Market opens at 9:30 AM ET, so 3:45 PM = 6.25 hours of trading
                if current_time.hour >= 15 and current_time.minute >= 45:
                    print(f"  ⏰ MARKET CLOSING - FORCE EXIT ALL POSITIONS!")
                    print(f"     Time: {current_time.strftime('%I:%M %p')} - Market closes at 4:00 PM")
                    positions_to_close.append((position_id, current_premium, 'MARKET_CLOSE'))
                
                # Take profit at target (1:10 achieved)
                elif current_premium >= position['target_premium']:
                    print(f"  🎯 TAKE PROFIT TARGET HIT! (+{((current_premium/position['entry_premium']-1)*100):.0f}%)")
                    positions_to_close.append((position_id, current_premium, 'TAKE_PROFIT'))
                
                # Stop loss at 50% loss
                elif current_premium <= position['stop_loss_premium']:
                    print(f"  🛑 STOP LOSS TRIGGERED! ({((current_premium/position['entry_premium']-1)*100):.0f}%)")
                    positions_to_close.append((position_id, current_premium, 'STOP_LOSS'))
                
                # Warning after 3:00 PM
                elif current_time.hour >= 15:
                    print(f"  ⚠️  Market closing soon ({current_time.strftime('%I:%M %p')}) - Consider closing position")
                    print(f"  ⏳ Position active - monitoring...")
                
                else:
                    print(f"  ⏳ Position active - monitoring...")
                    
            except Exception as e:
                print(f"  ❌ Error monitoring {position_id}: {str(e)}")
        
        # Execute closes
        for position_id, exit_premium, exit_reason in positions_to_close:
            self.close_options_position(position_id, exit_premium, exit_reason)
    
    def close_options_position(self, position_id, exit_premium, exit_reason):
        """Close options position"""
        if position_id not in self.open_positions:
            return
        
        position = self.open_positions[position_id]
        
        # Calculate actual P&L
        exit_value = exit_premium * 100 * position['contracts']
        realized_pl = exit_value - position['total_cost']
        realized_pl_pct = (realized_pl / position['total_cost']) * 100
        
        # Update capital
        self.available_capital += exit_value
        
        # Record closed trade
        closed_trade = {
            'ticker': position['ticker'],
            'option_type': position['signal_type'],
            'strike': position['strike_price'],
            'entry_time': position['entry_time'],
            'exit_time': datetime.now(),
            'entry_premium': position['entry_premium'],
            'exit_premium': exit_premium,
            'contracts': position['contracts'],
            'total_cost': position['total_cost'],
            'exit_value': exit_value,
            'realized_pl': realized_pl,
            'realized_pl_pct': realized_pl_pct,
            'exit_reason': exit_reason,
            'hold_duration_hours': (datetime.now() - position['entry_time']).seconds / 3600
        }
        
        self.closed_trades.append(closed_trade)
        
        # Log trade
        self.trade_log.append({
            'action': 'SELL',
            'time': datetime.now(),
            'ticker': position['ticker'],
            'option_type': position['signal_type'],
            'strike': position['strike_price'],
            'contracts': position['contracts'],
            'premium': exit_premium,
            'proceeds': exit_value,
            'pl': realized_pl,
            'reason': exit_reason
        })
        
        # Remove from open positions
        del self.open_positions[position_id]
        
        print(f"\n  {'🟢' if realized_pl > 0 else '🔴'} POSITION CLOSED: {position['ticker']} {position['signal_type']}")
        print(f"     Exit Reason: {exit_reason}")
        print(f"     Exit Premium: ${exit_premium:.2f}")
        print(f"     P&L: ${realized_pl:,.2f} ({realized_pl_pct:+.1f}%)")
        print(f"     Hold Duration: {closed_trade['hold_duration_hours']:.1f} hours")
        print(f"     New Capital: ${self.available_capital:,.2f}")
    
    def get_summary(self):
        """Get trading summary"""
        total_pl = sum(trade['realized_pl'] for trade in self.closed_trades)
        winning_trades = len([t for t in self.closed_trades if t['realized_pl'] > 0])
        total_trades = len(self.closed_trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'initial_capital': self.initial_capital,
            'current_capital': self.available_capital,
            'total_pl': total_pl,
            'total_pl_pct': (total_pl / self.initial_capital * 100),
            'open_positions': len(self.open_positions),
            'closed_trades': total_trades,
            'win_rate': win_rate,
            'winning_trades': winning_trades,
            'losing_trades': total_trades - winning_trades
        }
    
    def display_summary(self):
        """Display trading summary"""
        summary = self.get_summary()
        
        print(f"\n{'=' * 100}")
        print(f"📊 OPTIONS TRADING SUMMARY")
        print(f"{'=' * 100}")
        print(f"Initial Capital:        ${summary['initial_capital']:>12,.2f}")
        print(f"Current Capital:        ${summary['current_capital']:>12,.2f}")
        print(f"Total P&L:              ${summary['total_pl']:>12,.2f} ({summary['total_pl_pct']:+.2f}%)")
        print(f"\nOpen Positions:         {summary['open_positions']:>12}")
        print(f"Closed Trades:          {summary['closed_trades']:>12}")
        print(f"Win Rate:               {summary['win_rate']:>11.1f}%")
        print(f"  Winning Trades:       {summary['winning_trades']:>12}")
        print(f"  Losing Trades:        {summary['losing_trades']:>12}")
        print(f"{'=' * 100}")
    
    def save_report(self):
        """Save trading report to Excel"""
        filename = f"options_trading_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # Summary
            summary = self.get_summary()
            summary_df = pd.DataFrame([summary])
            summary_df.to_excel(writer, sheet_name='Summary', index=False)
            
            # Open positions
            if self.open_positions:
                positions_df = pd.DataFrame(self.open_positions.values())
                positions_df.to_excel(writer, sheet_name='Open Positions', index=False)
            
            # Closed trades
            if self.closed_trades:
                trades_df = pd.DataFrame(self.closed_trades)
                trades_df.to_excel(writer, sheet_name='Closed Trades', index=False)
            
            # Trade log
            if self.trade_log:
                log_df = pd.DataFrame(self.trade_log)
                log_df.to_excel(writer, sheet_name='Trade Log', index=False)
        
        print(f"\n✅ Report saved to: {filename}")
        return filename


# -----------------------------
# MAIN EXECUTION
# -----------------------------

if __name__ == "__main__":
    # Check market hours before trading (MUST USE EASTERN TIME)
    import pytz
    et_timezone = pytz.timezone('US/Eastern')
    current_time = datetime.now(et_timezone)
    current_hour = current_time.hour
    current_minute = current_time.minute
    
    print("\n" + "=" * 100)
    print(f"🕐 MARKET TIME CHECK - Current ET: {current_time.strftime('%I:%M %p %Z')}")
    print("=" * 100)
    
    # Prevent trading outside market hours or too late
    if current_hour < 9 or (current_hour == 9 and current_minute < 30):
        print("⚠️  Market is CLOSED - Opens at 9:30 AM ET")
        print("   Wait until market opens to trade 0DTE options")
        exit(0)
    elif current_hour >= 16:
        print("🔴 Market is CLOSED - Closed at 4:00 PM ET")
        print("   0DTE options trading is finished for today")
        exit(0)
    elif current_hour >= 15:
        print("🔴 TOO LATE TO OPEN NEW 0DTE POSITIONS!")
        print("   Market closes at 4:00 PM - Not enough time for trade to develop")
        print("   Come back tomorrow for new opportunities")
        exit(0)
    elif current_hour >= 14:
        print("⚠️  CAUTION: Only 2 hours left until market close")
        print("   Risk is HIGHER for new 0DTE positions at this time")
        print("   Proceeding anyway...")
    else:
        print(f"✅ Market is OPEN - Good time to trade (Best: 10:00 AM - 3:00 PM ET)")
    
    # Initialize options trader
    trader = DailyOptionsTrader(capital=10000, risk_per_trade=0.02, target_rr=10.0)
    
    print("\n" + "=" * 100)
    print("PHASE 1: ANALYZING SPY & QQQ FOR OPTIONS SIGNALS")
    print("=" * 100)
    
    # Analyze SPY
    print("\n📊 Analyzing SPY...")
    spy_signal = trader.analyze_for_options('SPY')
    if spy_signal:
        print(f"\n✅ SPY Signal Found: {spy_signal['signal_type']}")
        print(f"   Strike: ${spy_signal['strike_price']:.2f}")
        print(f"   Confidence: {spy_signal['confidence']}/10")
        print(f"   Current Price: ${spy_signal['current_price']:.2f}")
        print(f"   RSI: {spy_signal['rsi']:.2f}")
        print(f"   Intraday Momentum: {spy_signal['intraday_momentum']:.2f}%")
        
        # Ask if user wants to execute
        print(f"\n   💰 Trade Details:")
        print(f"      Option Type: {spy_signal['signal_type']}")
        print(f"      Strike Price: ${spy_signal['strike_price']:.2f}")
        print(f"      Estimated Premium: ${spy_signal['estimated_premium']:.2f}")
        print(f"      Contracts: {spy_signal['contracts']}")
        print(f"      Total Cost: ${spy_signal['total_cost']:.2f}")
        print(f"      Target Profit: ${spy_signal['profit_target']:.2f} (1:10 ratio)")
        
        trader.execute_options_trade(spy_signal)
    else:
        print("  ⚠️  No strong signal for SPY at this time")
    
    # Analyze QQQ
    print("\n📊 Analyzing QQQ...")
    qqq_signal = trader.analyze_for_options('QQQ')
    if qqq_signal:
        print(f"\n✅ QQQ Signal Found: {qqq_signal['signal_type']}")
        print(f"   Strike: ${qqq_signal['strike_price']:.2f}")
        print(f"   Confidence: {qqq_signal['confidence']}/10")
        print(f"   Current Price: ${qqq_signal['current_price']:.2f}")
        print(f"   RSI: {qqq_signal['rsi']:.2f}")
        print(f"   Intraday Momentum: {qqq_signal['intraday_momentum']:.2f}%")
        
        print(f"\n   💰 Trade Details:")
        print(f"      Option Type: {qqq_signal['signal_type']}")
        print(f"      Strike Price: ${qqq_signal['strike_price']:.2f}")
        print(f"      Estimated Premium: ${qqq_signal['estimated_premium']:.2f}")
        print(f"      Contracts: {qqq_signal['contracts']}")
        print(f"      Total Cost: ${qqq_signal['total_cost']:.2f}")
        print(f"      Target Profit: ${qqq_signal['profit_target']:.2f} (1:10 ratio)")
        
        trader.execute_options_trade(qqq_signal)
    else:
        print("  ⚠️  No strong signal for QQQ at this time")
    
    # Monitor positions - Skip immediate monitoring to avoid false triggers
    print("\n" + "=" * 100)
    print("PHASE 2: POSITION STATUS")
    print("=" * 100)
    if trader.open_positions:
        print(f"✅ {len(trader.open_positions)} positions opened successfully")
        print("⏳ Positions will be monitored after 5 minutes to avoid instant false triggers")
        print("   (In production, run this script every 5-10 minutes for live monitoring)")
    else:
        print("📊 No positions opened")
    # trader.monitor_options_positions()  # Skip immediate monitoring
    
    # Display summary
    trader.display_summary()
    
    # Save report
    trader.save_report()
    
    print("\n" + "=" * 100)
    print("📈 OPTIONS TRADING SESSION COMPLETE")
    print("=" * 100)
    print("\n⚠️  NOTE: This is a simulated options trading system.")
    print("For real options trading, you would need:")
    print("  • Real-time options chain data")
    print("  • Broker API integration (e.g., TD Ameritrade, Interactive Brokers)")
    print("  • Greeks calculation (Delta, Gamma, Theta, Vega)")
    print("  • Bid-Ask spread consideration")
    print("  • Options approval level from broker")
    print("=" * 100)
