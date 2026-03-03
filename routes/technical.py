"""
Technical Analysis Routes - Chart data, technical analysis.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import time
from datetime import datetime
import yfinance as yf
import numpy as np
import pandas as pd

from services.utils import clean_nan_values
from services.market_data import (
    cached_get_history, cached_get_ticker_info, _log_fetch_event,
    _is_rate_limited, _is_rate_limit_error, _mark_rate_limited,
    _mark_global_rate_limit, _is_expected_no_data_error
)
from services.indicators import (
    calculate_comprehensive_indicators, detect_chart_patterns, generate_trading_signals
)

technical_bp = Blueprint("technical", __name__)

# ============================================================================
# TECHNICAL ANALYSIS ENDPOINTS
# ============================================================================

@technical_bp.route('/api/technical/chart-data', methods=['POST'])
def get_chart_data():
    """Get OHLCV data for charting"""
    try:
        data = request.get_json()
        symbol = data.get('symbol', '').upper()
        timeframe = data.get('timeframe', '1d')
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400
        
        # Map timeframes
        interval_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '4h': '1h', '1d': '1d', '1wk': '1wk'
        }
        interval = interval_map.get(timeframe, '1d')
        
        # Determine period
        if timeframe in ['1m', '5m']:
            period = '5d'
        elif timeframe in ['15m', '30m']:
            period = '1mo'
        elif timeframe == '1h':
            period = '3mo'
        elif timeframe == '4h':
            period = '6mo'
        else:
            period = '5y'  # 5 years of data for daily/weekly charts
        
        # Fetch data (with prepost=True for intraday to include extended hours)
        use_prepost = timeframe in ['1m', '5m', '15m', '30m', '1h']
        hist = cached_get_history(symbol, period=period, interval=interval, prepost=use_prepost)
        
        if hist is None or hist.empty:
            return jsonify({'success': False, 'error': 'No data available'}), 404
        
        # Format for charting
        candles = []
        volume_data = []
        
        for idx, row in hist.iterrows():
            # Skip rows with NaN values or invalid data
            if pd.isna(row['Open']) or pd.isna(row['High']) or pd.isna(row['Low']) or pd.isna(row['Close']):
                continue
            if not np.isfinite(row['Open']) or not np.isfinite(row['High']) or not np.isfinite(row['Low']) or not np.isfinite(row['Close']):
                continue
            
            # Validate OHLC relationships
            open_val = float(row['Open'])
            high_val = float(row['High'])
            low_val = float(row['Low'])
            close_val = float(row['Close'])
            
            # Skip invalid candles where high < low or prices are negative
            if high_val < low_val or high_val <= 0 or low_val <= 0:
                continue
                
            timestamp = int(idx.timestamp())
            candles.append({
                'time': timestamp,
                'open': round(open_val, 2),
                'high': round(high_val, 2),
                'low': round(low_val, 2),
                'close': round(close_val, 2),
            })
            
            # Only add volume if not NaN and positive
            if not pd.isna(row['Volume']) and row['Volume'] >= 0:
                volume_data.append({
                    'time': timestamp,
                    'value': int(row['Volume']),
                    'color': '#26a69a' if close_val >= open_val else '#ef5350'
                })
        
        # Calculate indicators for overlay
        hist_copy = hist.copy()
        hist_copy['SMA_20'] = hist_copy['Close'].rolling(window=20).mean()
        hist_copy['SMA_50'] = hist_copy['Close'].rolling(window=50).mean()
        hist_copy['EMA_9'] = hist_copy['Close'].ewm(span=9, adjust=False).mean()
        hist_copy['EMA_21'] = hist_copy['Close'].ewm(span=21, adjust=False).mean()
        
        # Bollinger Bands
        hist_copy['BB_Middle'] = hist_copy['Close'].rolling(window=20).mean()
        bb_std = hist_copy['Close'].rolling(window=20).std()
        hist_copy['BB_Upper'] = hist_copy['BB_Middle'] + (bb_std * 2)
        hist_copy['BB_Lower'] = hist_copy['BB_Middle'] - (bb_std * 2)
        
        # Chandelier Exit (ATR-based trailing stops)
        # Default parameters: period=22, multiplier=3
        chandelier_period = 22
        chandelier_multiplier = 3.0
        
        # Calculate ATR for Chandelier Exit
        hist_copy['CE_TR'] = pd.concat([
            hist_copy['High'] - hist_copy['Low'],
            abs(hist_copy['High'] - hist_copy['Close'].shift()),
            abs(hist_copy['Low'] - hist_copy['Close'].shift())
        ], axis=1).max(axis=1)
        hist_copy['CE_ATR'] = hist_copy['CE_TR'].rolling(window=chandelier_period).mean()
        
        # Highest High and Lowest Low over lookback period
        hist_copy['CE_Highest_High'] = hist_copy['High'].rolling(window=chandelier_period).max()
        hist_copy['CE_Lowest_Low'] = hist_copy['Low'].rolling(window=chandelier_period).min()
        
        # Chandelier Exit Long: Highest High - (ATR × Multiplier)
        hist_copy['CE_Long'] = hist_copy['CE_Highest_High'] - (hist_copy['CE_ATR'] * chandelier_multiplier)
        
        # Chandelier Exit Short: Lowest Low + (ATR × Multiplier)
        hist_copy['CE_Short'] = hist_copy['CE_Lowest_Low'] + (hist_copy['CE_ATR'] * chandelier_multiplier)
        
        # Chandelier Exit Buy/Sell Signals - Using TradingView Logic
        # The indicator tracks a "direction" state:
        # - BULLISH (uptrend): Price is above CE_Long (trailing stop for longs)
        # - BEARISH (downtrend): Price is below CE_Short (trailing stop for shorts)
        #
        # BUY signal: Direction changes from bearish to bullish
        #   - When price was in downtrend (below CE_Short) and closes ABOVE CE_Short
        # SELL signal: Direction changes from bullish to bearish  
        #   - When price was in uptrend (above CE_Long) and closes BELOW CE_Long
        
        # Initialize direction column
        hist_copy['CE_Direction'] = 0  # 1 = bullish, -1 = bearish, 0 = neutral
        
        # Calculate direction for each bar based on state machine logic
        direction = 0  # Start neutral
        directions = []
        buy_signals = []
        sell_signals = []
        
        for i in range(len(hist_copy)):
            row = hist_copy.iloc[i]
            close = row['Close']
            ce_long = row['CE_Long']
            ce_short = row['CE_Short']
            
            prev_direction = direction
            
            # Skip if CE values are NaN
            if pd.isna(ce_long) or pd.isna(ce_short):
                directions.append(0)
                buy_signals.append(False)
                sell_signals.append(False)
                continue
            
            # Determine current direction based on previous direction and price
            if prev_direction >= 0:  # Was bullish or neutral
                # Check if we should switch to bearish (price breaks below CE_Long)
                if close < ce_long:
                    direction = -1  # Switch to bearish
                else:
                    direction = 1   # Stay/become bullish
            else:  # Was bearish
                # Check if we should switch to bullish (price breaks above CE_Short)
                if close > ce_short:
                    direction = 1   # Switch to bullish
                else:
                    direction = -1  # Stay bearish
            
            # Detect direction changes for signals
            # BUY: Direction changed from bearish (-1) to bullish (1)
            buy_signal = (prev_direction == -1) and (direction == 1)
            # SELL: Direction changed from bullish (1) to bearish (-1)
            sell_signal = (prev_direction == 1) and (direction == -1)
            
            directions.append(direction)
            buy_signals.append(buy_signal)
            sell_signals.append(sell_signal)
        
        hist_copy['CE_Direction'] = directions
        hist_copy['CE_Buy'] = buy_signals
        hist_copy['CE_Sell'] = sell_signals
        
        # ========== SUPERTREND INDICATOR ==========
        # SuperTrend: Trend-following indicator based on ATR
        # Default parameters: period=10, multiplier=3
        st_period = 10
        st_multiplier = 3.0
        
        # Calculate ATR for SuperTrend
        hist_copy['ST_TR'] = pd.concat([
            hist_copy['High'] - hist_copy['Low'],
            abs(hist_copy['High'] - hist_copy['Close'].shift()),
            abs(hist_copy['Low'] - hist_copy['Close'].shift())
        ], axis=1).max(axis=1)
        hist_copy['ST_ATR'] = hist_copy['ST_TR'].rolling(window=st_period).mean()
        
        # Calculate basic upper and lower bands
        hist_copy['ST_HL2'] = (hist_copy['High'] + hist_copy['Low']) / 2
        hist_copy['ST_BasicUpper'] = hist_copy['ST_HL2'] + (st_multiplier * hist_copy['ST_ATR'])
        hist_copy['ST_BasicLower'] = hist_copy['ST_HL2'] - (st_multiplier * hist_copy['ST_ATR'])
        
        # Calculate SuperTrend with state machine logic
        st_upper = []
        st_lower = []
        st_trend = []  # 1 = bullish (green), -1 = bearish (red)
        st_line = []   # The actual SuperTrend line values
        st_buy_signals = []
        st_sell_signals = []
        
        prev_upper = None
        prev_lower = None
        prev_trend = 1  # Start bullish
        prev_close = None
        
        for i in range(len(hist_copy)):
            row = hist_copy.iloc[i]
            close = row['Close']
            basic_upper = row['ST_BasicUpper']
            basic_lower = row['ST_BasicLower']
            
            if pd.isna(basic_upper) or pd.isna(basic_lower):
                st_upper.append(np.nan)
                st_lower.append(np.nan)
                st_trend.append(0)
                st_line.append(np.nan)
                st_buy_signals.append(False)
                st_sell_signals.append(False)
                prev_close = close
                continue
            
            # Calculate final upper band
            if prev_upper is not None and not pd.isna(prev_upper):
                if basic_upper < prev_upper or (prev_close is not None and prev_close > prev_upper):
                    final_upper = basic_upper
                else:
                    final_upper = prev_upper
            else:
                final_upper = basic_upper
            
            # Calculate final lower band
            if prev_lower is not None and not pd.isna(prev_lower):
                if basic_lower > prev_lower or (prev_close is not None and prev_close < prev_lower):
                    final_lower = basic_lower
                else:
                    final_lower = prev_lower
            else:
                final_lower = basic_lower
            
            # Determine trend direction
            if prev_trend == 1:  # Was bullish
                if close < final_lower:
                    current_trend = -1  # Switch to bearish
                else:
                    current_trend = 1   # Stay bullish
            else:  # Was bearish
                if close > final_upper:
                    current_trend = 1   # Switch to bullish
                else:
                    current_trend = -1  # Stay bearish
            
            # SuperTrend line follows the appropriate band
            if current_trend == 1:
                supertrend_value = final_lower  # Bullish: line is below price
            else:
                supertrend_value = final_upper  # Bearish: line is above price
            
            # Detect buy/sell signals (trend changes)
            buy_sig = (prev_trend == -1) and (current_trend == 1)
            sell_sig = (prev_trend == 1) and (current_trend == -1)
            
            st_upper.append(final_upper)
            st_lower.append(final_lower)
            st_trend.append(current_trend)
            st_line.append(supertrend_value)
            st_buy_signals.append(buy_sig)
            st_sell_signals.append(sell_sig)
            
            prev_upper = final_upper
            prev_lower = final_lower
            prev_trend = current_trend
            prev_close = close
        
        hist_copy['ST_Upper'] = st_upper
        hist_copy['ST_Lower'] = st_lower
        hist_copy['ST_Trend'] = st_trend
        hist_copy['ST_Line'] = st_line
        hist_copy['ST_Buy'] = st_buy_signals
        hist_copy['ST_Sell'] = st_sell_signals
        
        # Format indicator lines
        sma_20 = []
        sma_50 = []
        ema_9 = []
        ema_21 = []
        bb_upper = []
        bb_middle = []
        bb_lower = []
        chandelier_long = []
        chandelier_short = []
        chandelier_buy_signals = []
        chandelier_sell_signals = []
        supertrend_line = []
        supertrend_buy_signals = []
        supertrend_sell_signals = []
        
        for idx, row in hist_copy.iterrows():
            timestamp = int(idx.timestamp())
            
            # Only add indicators if value is valid (not NaN and not inf)
            if not pd.isna(row['SMA_20']) and np.isfinite(row['SMA_20']):
                sma_20.append({'time': timestamp, 'value': round(float(row['SMA_20']), 2)})
            if not pd.isna(row['SMA_50']) and np.isfinite(row['SMA_50']):
                sma_50.append({'time': timestamp, 'value': round(float(row['SMA_50']), 2)})
            if not pd.isna(row['EMA_9']) and np.isfinite(row['EMA_9']):
                ema_9.append({'time': timestamp, 'value': round(float(row['EMA_9']), 2)})
            if not pd.isna(row['EMA_21']) and np.isfinite(row['EMA_21']):
                ema_21.append({'time': timestamp, 'value': round(float(row['EMA_21']), 2)})
            if not pd.isna(row['BB_Upper']) and np.isfinite(row['BB_Upper']) and not pd.isna(row['BB_Middle']) and not pd.isna(row['BB_Lower']):
                bb_upper.append({'time': timestamp, 'value': round(float(row['BB_Upper']), 2)})
                bb_middle.append({'time': timestamp, 'value': round(float(row['BB_Middle']), 2)})
                bb_lower.append({'time': timestamp, 'value': round(float(row['BB_Lower']), 2)})
            
            # Chandelier Exit
            if not pd.isna(row['CE_Long']) and np.isfinite(row['CE_Long']):
                chandelier_long.append({'time': timestamp, 'value': round(float(row['CE_Long']), 2)})
            if not pd.isna(row['CE_Short']) and np.isfinite(row['CE_Short']):
                chandelier_short.append({'time': timestamp, 'value': round(float(row['CE_Short']), 2)})
            
            # Chandelier Exit Buy/Sell Signals (markers for chart)
            if row.get('CE_Buy', False) == True:
                chandelier_buy_signals.append({
                    'time': timestamp,
                    'position': 'belowBar',
                    'color': '#26a69a',
                    'shape': 'arrowUp',
                    'text': 'BUY',
                    'size': 2
                })
            if row.get('CE_Sell', False) == True:
                chandelier_sell_signals.append({
                    'time': timestamp,
                    'position': 'aboveBar',
                    'color': '#ef5350',
                    'shape': 'arrowDown',
                    'text': 'SELL',
                    'size': 2
                })
            
            # SuperTrend line and signals
            st_line_val = row.get('ST_Line')
            st_trend_val = row.get('ST_Trend', 0)
            if st_line_val is not None and not pd.isna(st_line_val) and np.isfinite(st_line_val):
                # Color the line based on trend direction
                supertrend_line.append({
                    'time': timestamp,
                    'value': round(float(st_line_val), 2),
                    'color': '#26a69a' if st_trend_val == 1 else '#ef5350'  # Green if bullish, red if bearish
                })
            
            if row.get('ST_Buy', False) == True:
                supertrend_buy_signals.append({
                    'time': timestamp,
                    'position': 'belowBar',
                    'color': '#26a69a',
                    'shape': 'arrowUp',
                    'text': 'BUY',
                    'size': 2
                })
            if row.get('ST_Sell', False) == True:
                supertrend_sell_signals.append({
                    'time': timestamp,
                    'position': 'aboveBar',
                    'color': '#ef5350',
                    'shape': 'arrowDown',
                    'text': 'SELL',
                    'size': 2
                })
        
        # Sort all data by timestamp (required by Lightweight Charts)
        candles = sorted(candles, key=lambda x: x['time'])
        volume_data = sorted(volume_data, key=lambda x: x['time'])
        sma_20 = sorted(sma_20, key=lambda x: x['time'])
        sma_50 = sorted(sma_50, key=lambda x: x['time'])
        ema_9 = sorted(ema_9, key=lambda x: x['time'])
        ema_21 = sorted(ema_21, key=lambda x: x['time'])
        bb_upper = sorted(bb_upper, key=lambda x: x['time'])
        bb_middle = sorted(bb_middle, key=lambda x: x['time'])
        bb_lower = sorted(bb_lower, key=lambda x: x['time'])
        chandelier_long = sorted(chandelier_long, key=lambda x: x['time'])
        chandelier_short = sorted(chandelier_short, key=lambda x: x['time'])
        chandelier_buy_signals = sorted(chandelier_buy_signals, key=lambda x: x['time'])
        chandelier_sell_signals = sorted(chandelier_sell_signals, key=lambda x: x['time'])
        supertrend_line = sorted(supertrend_line, key=lambda x: x['time'])
        supertrend_buy_signals = sorted(supertrend_buy_signals, key=lambda x: x['time'])
        supertrend_sell_signals = sorted(supertrend_sell_signals, key=lambda x: x['time'])
        
        return jsonify({
            'success': True,
            'symbol': symbol,
            'timeframe': timeframe,
            'candles': candles,
            'volume': volume_data,
            'indicators': {
                'sma_20': sma_20,
                'sma_50': sma_50,
                'ema_9': ema_9,
                'ema_21': ema_21,
                'bb_upper': bb_upper,
                'bb_middle': bb_middle,
                'bb_lower': bb_lower,
                'chandelier_long': chandelier_long,
                'chandelier_short': chandelier_short,
                'chandelier_buy_signals': chandelier_buy_signals,
                'chandelier_sell_signals': chandelier_sell_signals,
                'supertrend_line': supertrend_line,
                'supertrend_buy_signals': supertrend_buy_signals,
                'supertrend_sell_signals': supertrend_sell_signals,
            }
        })
        
    except Exception as e:
        print(f"Chart data error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@technical_bp.route('/api/technical/analyze', methods=['POST'])
def technical_analyze():
    """Comprehensive technical analysis with TradingView-like indicators"""
    try:
        data = request.get_json()
        symbol = data.get('symbol', '').upper()
        timeframe = data.get('timeframe', '1d')  # 1d, 1h, 15m, 5m
        indicators = data.get('indicators', ['all'])  # List of indicators to calculate
        
        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400
        
        # Map timeframes to yfinance intervals
        interval_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '4h': '1h', '1d': '1d', '1wk': '1wk'
        }
        interval = interval_map.get(timeframe, '1d')
        
        # Fetch historical data
        # Determine period based on timeframe
        if timeframe in ['1m', '5m']:
            period = '1d'
        elif timeframe in ['15m', '30m']:
            period = '5d'
        elif timeframe == '1h':
            period = '1mo'
        elif timeframe == '4h':
            period = '3mo'
        else:
            period = '1y'
        
        # Use prepost=True for intraday timeframes to include extended hours
        use_prepost = timeframe in ['1m', '5m', '15m', '30m', '1h']
        hist = cached_get_history(symbol, period=period, interval=interval, prepost=use_prepost)
        
        if hist is None or hist.empty:
            return jsonify({
                'success': False, 
                'error': f'⚠️ {symbol} appears to be delisted or has no available market data',
                'message': f'Unable to load chart data for {symbol}. This stock may be delisted, suspended, or the ticker symbol may be incorrect. Please verify the symbol and try again.'
            }), 404
        
        # Calculate all technical indicators
        result = calculate_comprehensive_indicators(hist, symbol)
        
        # Add basic info
        current_price = hist['Close'].iloc[-1]
        result['symbol'] = symbol
        result['timeframe'] = timeframe
        result['current_price'] = round(float(current_price), 2)
        result['timestamp'] = datetime.now().isoformat()
        
        return jsonify({
            'success': True,
            'data': result
        })
        
    except Exception as e:
        print(f"Technical analysis error: {e}")
        error_msg = str(e).lower()
        if 'delisted' in error_msg or 'no data found' in error_msg or 'symbol may be delisted' in error_msg:
            return jsonify({
                'success': False,
                'error': f'⚠️ {symbol} appears to be delisted or unavailable',
                'message': f'Unable to load technical analysis for {symbol}. This stock may be delisted, suspended, or the ticker symbol may be incorrect. Please verify the symbol and try again.'
            }), 404
        return jsonify({'success': False, 'error': f'Technical analysis failed: {str(e)}'}), 500

