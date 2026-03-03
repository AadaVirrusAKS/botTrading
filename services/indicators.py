"""
Technical Indicators - Comprehensive indicator calculations, pattern detection,
and trading signal generation.
"""
import numpy as np
import pandas as pd
from datetime import datetime

def calculate_comprehensive_indicators(hist, symbol):
    """Calculate comprehensive technical indicators like TradingView"""
    
    # Create a copy to avoid modifying original
    df = hist.copy()
    current_price = df['Close'].iloc[-1]
    
    # ========== TREND INDICATORS ==========
    
    # Moving Averages
    df['SMA_10'] = df['Close'].rolling(window=10).mean()
    df['SMA_20'] = df['Close'].rolling(window=20).mean()
    df['SMA_50'] = df['Close'].rolling(window=50).mean()
    df['SMA_100'] = df['Close'].rolling(window=100).mean()
    df['SMA_200'] = df['Close'].rolling(window=200).mean()
    
    df['EMA_9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
    
    # MACD
    df['MACD'] = df['EMA_12'] - df['EMA_26']
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Histogram'] = df['MACD'] - df['MACD_Signal']
    
    # ========== MOMENTUM INDICATORS ==========
    
    # RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Stochastic Oscillator
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    df['Stoch_%K'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))
    df['Stoch_%D'] = df['Stoch_%K'].rolling(window=3).mean()
    
    # CCI (Commodity Channel Index)
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    df['CCI'] = (tp - tp.rolling(window=20).mean()) / (0.015 * tp.rolling(window=20).std())
    
    # Williams %R
    highest_high = df['High'].rolling(window=14).max()
    lowest_low = df['Low'].rolling(window=14).min()
    df['Williams_%R'] = -100 * ((highest_high - df['Close']) / (highest_high - lowest_low))
    
    # ========== VOLATILITY INDICATORS ==========
    
    # ATR (Average True Range)
    df['TR'] = pd.concat([
        df['High'] - df['Low'],
        abs(df['High'] - df['Close'].shift()),
        abs(df['Low'] - df['Close'].shift())
    ], axis=1).max(axis=1)
    df['ATR'] = df['TR'].rolling(window=14).mean()
    
    # Bollinger Bands
    df['BB_Middle'] = df['Close'].rolling(window=20).mean()
    bb_std = df['Close'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
    df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
    df['BB_Width'] = ((df['BB_Upper'] - df['BB_Lower']) / df['BB_Middle']) * 100
    
    # Keltner Channels
    df['KC_Middle'] = df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['KC_Upper'] = df['KC_Middle'] + (df['ATR'] * 2)
    df['KC_Lower'] = df['KC_Middle'] - (df['ATR'] * 2)
    
    # ========== CHANDELIER EXIT ==========
    # Volatility-based trailing stop indicator (Charles Le Beau / Alexander Elder)
    # Default parameters: period=22, multiplier=3
    chandelier_period = 22
    chandelier_multiplier = 3.0
    
    # Highest High and Lowest Low over lookback period
    df['CE_Highest_High'] = df['High'].rolling(window=chandelier_period).max()
    df['CE_Lowest_Low'] = df['Low'].rolling(window=chandelier_period).min()
    
    # ATR for Chandelier Exit (using same period as lookback)
    df['CE_ATR'] = df['TR'].rolling(window=chandelier_period).mean()
    
    # Chandelier Exit Long: Highest High - (ATR × Multiplier)
    # This is the trailing stop for long positions
    df['CE_Long'] = df['CE_Highest_High'] - (df['CE_ATR'] * chandelier_multiplier)
    
    # Chandelier Exit Short: Lowest Low + (ATR × Multiplier)
    # This is the trailing stop for short positions
    df['CE_Short'] = df['CE_Lowest_Low'] + (df['CE_ATR'] * chandelier_multiplier)
    
    # Determine trend direction using state machine logic (same as TradingView)
    # Direction persists until a reversal signal occurs
    direction = 0  # 1 = bullish, -1 = bearish, 0 = neutral
    directions = []
    
    for i in range(len(df)):
        row = df.iloc[i]
        close = row['Close']
        ce_long = row['CE_Long']
        ce_short = row['CE_Short']
        
        if pd.isna(ce_long) or pd.isna(ce_short):
            directions.append('neutral')
            continue
        
        if direction >= 0:  # Was bullish or neutral
            if close < ce_long:
                direction = -1  # Switch to bearish
            else:
                direction = 1   # Stay/become bullish
        else:  # Was bearish
            if close > ce_short:
                direction = 1   # Switch to bullish
            else:
                direction = -1  # Stay bearish
        
        if direction == 1:
            directions.append('bullish')
        elif direction == -1:
            directions.append('bearish')
        else:
            directions.append('neutral')
    
    df['CE_Trend'] = directions
    
    # ========== SUPERTREND INDICATOR ==========
    # SuperTrend: Trend-following indicator based on ATR
    # Default parameters: period=10, multiplier=3
    st_period = 10
    st_multiplier = 3.0
    
    # Calculate ATR for SuperTrend
    df['ST_ATR'] = df['TR'].rolling(window=st_period).mean()
    
    # Calculate basic upper and lower bands
    df['ST_HL2'] = (df['High'] + df['Low']) / 2
    df['ST_BasicUpper'] = df['ST_HL2'] + (st_multiplier * df['ST_ATR'])
    df['ST_BasicLower'] = df['ST_HL2'] - (st_multiplier * df['ST_ATR'])
    
    # Calculate SuperTrend with state machine logic
    st_line = []
    st_trend_list = []
    prev_upper = None
    prev_lower = None
    prev_trend = 1
    prev_close = None
    
    for i in range(len(df)):
        row = df.iloc[i]
        close = row['Close']
        basic_upper = row['ST_BasicUpper']
        basic_lower = row['ST_BasicLower']
        
        if pd.isna(basic_upper) or pd.isna(basic_lower):
            st_line.append(np.nan)
            st_trend_list.append('neutral')
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
        if prev_trend == 1:
            if close < final_lower:
                current_trend = -1
            else:
                current_trend = 1
        else:
            if close > final_upper:
                current_trend = 1
            else:
                current_trend = -1
        
        # SuperTrend line follows the appropriate band
        if current_trend == 1:
            supertrend_value = final_lower
            st_trend_list.append('bullish')
        else:
            supertrend_value = final_upper
            st_trend_list.append('bearish')
        
        st_line.append(supertrend_value)
        prev_upper = final_upper
        prev_lower = final_lower
        prev_trend = current_trend
        prev_close = close
    
    df['ST_Line'] = st_line
    df['ST_Trend'] = st_trend_list
    
    # ========== VOLUME INDICATORS ==========
    
    # OBV (On-Balance Volume)
    df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
    
    # Volume Moving Average
    df['Volume_SMA_20'] = df['Volume'].rolling(window=20).mean()
    
    # Money Flow Index (MFI)
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3
    money_flow = typical_price * df['Volume']
    positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0).rolling(window=14).sum()
    negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0).rolling(window=14).sum()
    mfi_ratio = positive_flow / negative_flow
    df['MFI'] = 100 - (100 / (1 + mfi_ratio))
    
    # ========== TREND STRENGTH ==========
    
    # ADX (Average Directional Index)
    high_diff = df['High'].diff()
    low_diff = -df['Low'].diff()
    
    plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    
    atr_14 = df['ATR']
    plus_di = 100 * (plus_dm.rolling(window=14).mean() / atr_14)
    minus_di = 100 * (minus_dm.rolling(window=14).mean() / atr_14)
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    df['ADX'] = dx.rolling(window=14).mean()
    df['+DI'] = plus_di
    df['-DI'] = minus_di
    
    # ========== PIVOT POINTS ==========
    
    # Standard Pivot Points
    pivot = (df['High'].iloc[-2] + df['Low'].iloc[-2] + df['Close'].iloc[-2]) / 3
    r1 = (2 * pivot) - df['Low'].iloc[-2]
    s1 = (2 * pivot) - df['High'].iloc[-2]
    r2 = pivot + (df['High'].iloc[-2] - df['Low'].iloc[-2])
    s2 = pivot - (df['High'].iloc[-2] - df['Low'].iloc[-2])
    r3 = pivot + 2 * (df['High'].iloc[-2] - df['Low'].iloc[-2])
    s3 = pivot - 2 * (df['High'].iloc[-2] - df['Low'].iloc[-2])
    
    # ========== PATTERN RECOGNITION ==========
    
    patterns = detect_chart_patterns(df)
    
    # ========== SIGNALS & SUMMARY ==========
    
    signals = generate_trading_signals(df)
    
    # Get latest values
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    
    return {
        # Current Values
        'price': round(float(latest['Close']), 2),
        'volume': int(latest['Volume']),
        'volume_avg': int(latest['Volume_SMA_20']) if not pd.isna(latest['Volume_SMA_20']) else 0,
        
        # Moving Averages
        'moving_averages': {
            'sma_10': round(float(latest['SMA_10']), 2) if not pd.isna(latest['SMA_10']) else None,
            'sma_20': round(float(latest['SMA_20']), 2) if not pd.isna(latest['SMA_20']) else None,
            'sma_50': round(float(latest['SMA_50']), 2) if not pd.isna(latest['SMA_50']) else None,
            'sma_100': round(float(latest['SMA_100']), 2) if not pd.isna(latest['SMA_100']) else None,
            'sma_200': round(float(latest['SMA_200']), 2) if not pd.isna(latest['SMA_200']) else None,
            'ema_9': round(float(latest['EMA_9']), 2) if not pd.isna(latest['EMA_9']) else None,
            'ema_21': round(float(latest['EMA_21']), 2) if not pd.isna(latest['EMA_21']) else None,
            'ema_50': round(float(latest['EMA_50']), 2) if not pd.isna(latest['EMA_50']) else None,
            'ema_200': round(float(latest['EMA_200']), 2) if not pd.isna(latest['EMA_200']) else None,
        },
        
        # Momentum Indicators
        'momentum': {
            'rsi': round(float(latest['RSI']), 2) if not pd.isna(latest['RSI']) else None,
            'macd': round(float(latest['MACD']), 4) if not pd.isna(latest['MACD']) else None,
            'macd_signal': round(float(latest['MACD_Signal']), 4) if not pd.isna(latest['MACD_Signal']) else None,
            'macd_histogram': round(float(latest['MACD_Histogram']), 4) if not pd.isna(latest['MACD_Histogram']) else None,
            'stoch_k': round(float(latest['Stoch_%K']), 2) if not pd.isna(latest['Stoch_%K']) else None,
            'stoch_d': round(float(latest['Stoch_%D']), 2) if not pd.isna(latest['Stoch_%D']) else None,
            'cci': round(float(latest['CCI']), 2) if not pd.isna(latest['CCI']) else None,
            'williams_r': round(float(latest['Williams_%R']), 2) if not pd.isna(latest['Williams_%R']) else None,
            'mfi': round(float(latest['MFI']), 2) if not pd.isna(latest['MFI']) else None,
        },
        
        # Volatility Indicators
        'volatility': {
            'atr': round(float(latest['ATR']), 2) if not pd.isna(latest['ATR']) else None,
            'bb_upper': round(float(latest['BB_Upper']), 2) if not pd.isna(latest['BB_Upper']) else None,
            'bb_middle': round(float(latest['BB_Middle']), 2) if not pd.isna(latest['BB_Middle']) else None,
            'bb_lower': round(float(latest['BB_Lower']), 2) if not pd.isna(latest['BB_Lower']) else None,
            'bb_width': round(float(latest['BB_Width']), 2) if not pd.isna(latest['BB_Width']) else None,
        },
        
        # Chandelier Exit (Trailing Stops)
        'chandelier_exit': {
            'long_stop': round(float(latest['CE_Long']), 2) if not pd.isna(latest['CE_Long']) else None,
            'short_stop': round(float(latest['CE_Short']), 2) if not pd.isna(latest['CE_Short']) else None,
            'highest_high': round(float(latest['CE_Highest_High']), 2) if not pd.isna(latest['CE_Highest_High']) else None,
            'lowest_low': round(float(latest['CE_Lowest_Low']), 2) if not pd.isna(latest['CE_Lowest_Low']) else None,
            'atr_22': round(float(latest['CE_ATR']), 2) if not pd.isna(latest['CE_ATR']) else None,
            'trend': latest['CE_Trend'] if 'CE_Trend' in latest else 'neutral',
            'period': 22,
            'multiplier': 3.0,
        },
        
        # SuperTrend
        'supertrend': {
            'value': round(float(latest['ST_Line']), 2) if not pd.isna(latest['ST_Line']) else None,
            'trend': latest['ST_Trend'] if 'ST_Trend' in latest else 'neutral',
            'atr_10': round(float(latest['ST_ATR']), 2) if not pd.isna(latest['ST_ATR']) else None,
            'period': 10,
            'multiplier': 3.0,
        },
        
        # Trend Strength
        'trend': {
            'adx': round(float(latest['ADX']), 2) if not pd.isna(latest['ADX']) else None,
            'plus_di': round(float(latest['+DI']), 2) if not pd.isna(latest['+DI']) else None,
            'minus_di': round(float(latest['-DI']), 2) if not pd.isna(latest['-DI']) else None,
        },
        
        # Pivot Points
        'pivots': {
            'pivot': round(float(pivot), 2),
            'r1': round(float(r1), 2),
            'r2': round(float(r2), 2),
            'r3': round(float(r3), 2),
            's1': round(float(s1), 2),
            's2': round(float(s2), 2),
            's3': round(float(s3), 2),
        },
        
        # Volume
        'volume_data': {
            'current': int(latest['Volume']),
            'avg_20': int(latest['Volume_SMA_20']) if not pd.isna(latest['Volume_SMA_20']) else 0,
            'obv': int(latest['OBV']) if not pd.isna(latest['OBV']) else 0,
        },
        
        # Patterns & Signals
        'patterns': patterns,
        'signals': signals,
    }

def detect_chart_patterns(df):
    """Detect common chart patterns"""
    patterns = []
    
    if len(df) < 20:
        return patterns
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Bullish/Bearish Engulfing
    if prev['Close'] < prev['Open'] and latest['Close'] > latest['Open']:
        if latest['Open'] < prev['Close'] and latest['Close'] > prev['Open']:
            patterns.append({'name': 'Bullish Engulfing', 'type': 'bullish', 'strength': 'strong'})
    
    if prev['Close'] > prev['Open'] and latest['Close'] < latest['Open']:
        if latest['Open'] > prev['Close'] and latest['Close'] < prev['Open']:
            patterns.append({'name': 'Bearish Engulfing', 'type': 'bearish', 'strength': 'strong'})
    
    # Doji
    body_size = abs(latest['Close'] - latest['Open'])
    candle_range = latest['High'] - latest['Low']
    if candle_range > 0 and body_size / candle_range < 0.1:
        patterns.append({'name': 'Doji', 'type': 'neutral', 'strength': 'medium'})
    
    # Hammer / Hanging Man
    lower_shadow = latest['Open'] - latest['Low'] if latest['Close'] > latest['Open'] else latest['Close'] - latest['Low']
    upper_shadow = latest['High'] - latest['Close'] if latest['Close'] > latest['Open'] else latest['High'] - latest['Open']
    
    if candle_range > 0:
        if lower_shadow > 2 * body_size and upper_shadow < body_size:
            if latest['Close'] > latest['Open']:
                patterns.append({'name': 'Hammer', 'type': 'bullish', 'strength': 'medium'})
            else:
                patterns.append({'name': 'Hanging Man', 'type': 'bearish', 'strength': 'medium'})
    
    # Support/Resistance Break
    sma_50 = latest['SMA_50']
    if not pd.isna(sma_50):
        if prev['Close'] < sma_50 and latest['Close'] > sma_50:
            patterns.append({'name': 'SMA50 Breakout', 'type': 'bullish', 'strength': 'medium'})
        elif prev['Close'] > sma_50 and latest['Close'] < sma_50:
            patterns.append({'name': 'SMA50 Breakdown', 'type': 'bearish', 'strength': 'medium'})
    
    return patterns

def generate_trading_signals(df):
    """Generate buy/sell/hold signals based on multiple indicators"""
    
    if len(df) < 50:
        return {'overall': 'HOLD', 'strength': 0, 'details': []}
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    bullish_signals = 0
    bearish_signals = 0
    details = []
    
    # RSI
    if not pd.isna(latest['RSI']):
        if latest['RSI'] < 30:
            bullish_signals += 2
            details.append('RSI Oversold (<30)')
        elif latest['RSI'] > 70:
            bearish_signals += 2
            details.append('RSI Overbought (>70)')
        elif 40 <= latest['RSI'] <= 60:
            bullish_signals += 1
            details.append('RSI Neutral Zone')
    
    # MACD
    if not pd.isna(latest['MACD']) and not pd.isna(prev['MACD']):
        if prev['MACD'] < prev['MACD_Signal'] and latest['MACD'] > latest['MACD_Signal']:
            bullish_signals += 2
            details.append('MACD Bullish Crossover')
        elif prev['MACD'] > prev['MACD_Signal'] and latest['MACD'] < latest['MACD_Signal']:
            bearish_signals += 2
            details.append('MACD Bearish Crossover')
        elif latest['MACD'] > latest['MACD_Signal']:
            bullish_signals += 1
        else:
            bearish_signals += 1
    
    # Moving Average Alignment
    price = latest['Close']
    if not pd.isna(latest['EMA_9']) and not pd.isna(latest['EMA_21']):
        if latest['EMA_9'] > latest['EMA_21'] and price > latest['EMA_9']:
            bullish_signals += 2
            details.append('Price above EMA9 > EMA21')
        elif latest['EMA_9'] < latest['EMA_21'] and price < latest['EMA_9']:
            bearish_signals += 2
            details.append('Price below EMA9 < EMA21')
    
    # Bollinger Bands
    if not pd.isna(latest['BB_Upper']) and not pd.isna(latest['BB_Lower']):
        if price < latest['BB_Lower']:
            bullish_signals += 1
            details.append('Price below BB Lower (oversold)')
        elif price > latest['BB_Upper']:
            bearish_signals += 1
            details.append('Price above BB Upper (overbought)')
    
    # ADX Trend Strength
    if not pd.isna(latest['ADX']):
        if latest['ADX'] > 25:
            if not pd.isna(latest['+DI']) and not pd.isna(latest['-DI']):
                if latest['+DI'] > latest['-DI']:
                    bullish_signals += 1
                    details.append(f'Strong Uptrend (ADX: {latest["ADX"]:.1f})')
                else:
                    bearish_signals += 1
                    details.append(f'Strong Downtrend (ADX: {latest["ADX"]:.1f})')
    
    # Volume Confirmation
    if not pd.isna(latest['Volume_SMA_20']):
        if latest['Volume'] > latest['Volume_SMA_20'] * 1.5:
            if price > prev['Close']:
                bullish_signals += 1
                details.append('High volume on up move')
            else:
                bearish_signals += 1
                details.append('High volume on down move')
    
    # Calculate overall signal
    total_signals = bullish_signals + bearish_signals
    if total_signals == 0:
        return {'overall': 'HOLD', 'strength': 0, 'details': details, 'bullish': 0, 'bearish': 0}
    
    signal_strength = abs(bullish_signals - bearish_signals)
    
    if bullish_signals > bearish_signals + 2:
        overall = 'STRONG BUY'
    elif bullish_signals > bearish_signals:
        overall = 'BUY'
    elif bearish_signals > bullish_signals + 2:
        overall = 'STRONG SELL'
    elif bearish_signals > bullish_signals:
        overall = 'SELL'
    else:
        overall = 'HOLD'
    
    return {
        'overall': overall,
        'strength': signal_strength,
        'bullish': bullish_signals,
        'bearish': bearish_signals,
        'details': details
    }

