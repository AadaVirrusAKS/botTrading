"""
Market Helper Functions - Market status, live quotes, sector performance, movers.
"""
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
import numpy as np

from services.utils import MAJOR_INDICES, SECTOR_ETFS, SECTOR_STOCKS, US_MARKET_HOLIDAYS
from services.symbols import KNOWN_DELISTED
from services.market_data import (
    cached_get_price, cached_batch_prices, cached_get_history,
    cached_get_ticker_info, _is_rate_limited, _log_fetch_event,
    _price_cache, _price_cache_lock, quote_cache, cache_timeout,
    _fetch_all_quotes_batch
)

def get_market_status():
    """Check if market is currently open, with holiday detection"""
    now = datetime.now(ZoneInfo('America/New_York'))
    year = now.year
    month = now.month
    day = now.day
    hour = now.hour
    minute = now.minute
    day_of_week = now.weekday()
    
    # Check for holidays first
    holiday_key = (year, month, day)
    if holiday_key in US_MARKET_HOLIDAYS:
        holiday_name = US_MARKET_HOLIDAYS[holiday_key]
        return "CLOSED", f"Holiday: {holiday_name}"
    
    # Market hours: 9:30 AM - 4:00 PM ET (weekdays)
    # Pre-market: 4:00 AM - 9:30 AM
    # After-hours: 4:00 PM - 8:00 PM
    
    if day_of_week >= 5:  # Weekend
        return "CLOSED", "Weekend"
    
    time_minutes = hour * 60 + minute
    
    if 570 <= time_minutes < 960:  # 9:30 AM - 4:00 PM
        return "OPEN", "Regular Trading"
    elif 240 <= time_minutes < 570:  # 4:00 AM - 9:30 AM
        return "PRE_MARKET", "Pre-Market"
    elif 960 <= time_minutes < 1200:  # 4:00 PM - 8:00 PM
        return "AFTER_HOURS", "After-Hours"
    else:
        return "CLOSED", "Market Closed"

def get_live_quote(symbol, use_cache=True):
    """Fetch real-time quote data with caching"""
    # Check cache first
    if use_cache and symbol in quote_cache:
        cached_data, timestamp = quote_cache[symbol]
        if (datetime.now() - timestamp).total_seconds() < cache_timeout:
            return cached_data
    
    try:
        price, hist = cached_get_price(symbol, period='5d', interval='1d', prepost=True)
        
        # Skip symbols with no data (rate-limited, market closed, or truly delisted)
        if hist is None or hist.empty:
            if not _is_rate_limited(symbol):
                _log_fetch_event('quote-no-data', symbol, f"[Quote] {symbol}: no data returned (rate-limited or market closed)", cooldown=300)
            return None
        
        current_price = price
        if current_price is None or current_price <= 0:
            print(f"[Quote] {symbol}: invalid price returned")
            return None
        prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
        change = current_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
        
        result = {
            'symbol': symbol,
            'price': round(current_price, 2),
            'change': round(change, 2),
            'changePct': round(change_pct, 2),
            'volume': int(hist['Volume'].iloc[-1]),
            'high': round(hist['High'].iloc[-1], 2),
            'low': round(hist['Low'].iloc[-1], 2),
            'open': round(hist['Open'].iloc[-1], 2)
        }
        
        # Update cache
        quote_cache[symbol] = (result, datetime.now())
        return result
        
    except Exception as e:
        # Silently fail for rate limits, return cached data if available
        if symbol in quote_cache:
            cached_data, _ = quote_cache[symbol]
            return cached_data
        return None

def get_sector_performance():
    """Get current sector performance using ETFs — batch fetch"""
    sector_symbols = list(SECTOR_ETFS.keys())
    all_quotes = _fetch_all_quotes_batch(sector_symbols)
    
    results = []
    for symbol, sector in SECTOR_ETFS.items():
        if symbol in all_quotes:
            quote = all_quotes[symbol]
            results.append({
                'sector': sector,
                'changePct': quote.get('changePct', 0),
                'price': quote.get('price', 0),
                'symbol': quote.get('symbol', symbol)
            })
    
    return sorted(results, key=lambda x: x['changePct'], reverse=True)

# Extended watchlist for pre-market & after-hours analysis
EXTENDED_HOURS_WATCHLIST = [
    # Mega caps
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B',
    # Tech & semiconductors
    'AMD', 'INTC', 'QCOM', 'AVGO', 'TXN', 'MU', 'ASML', 'TSM',
    # Communication & streaming
    'NFLX', 'DIS', 'T', 'VZ', 'CMCSA', 'TMUS',
    # Financials
    'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'V', 'MA',
    # Consumer
    'WMT', 'HD', 'NKE', 'SBUX', 'MCD', 'PG', 'KO', 'PEP',
    # Healthcare & biotech
    'PFE', 'JNJ', 'UNH', 'ABBV', 'TMO', 'LLY', 'MRNA', 'BNTX',
    # Energy
    'XOM', 'CVX', 'COP', 'SLB', 'OXY',
    # E-commerce & fintech
    'SHOP', 'PYPL', 'COIN', 'SOFI',
    # Transportation & ride-sharing
    'UBER', 'LYFT', 'F', 'GM', 'RIVN', 'LCID',
    # Software & cloud
    'CRM', 'ORCL', 'ADBE', 'NOW', 'SNOW', 'DDOG', 'CRWD',
    # Social & communication
    'SNAP', 'PINS', 'SPOT', 'RBLX',
    # Cloud & remote work
    'ZM', 'DOCU', 'TWLO', 'NET', 'OKTA',
    # Growth & innovation
    'PLTR', 'ARKK', 'U', 'ABNB',
    # ETFs for sector view
    'SPY', 'QQQ', 'IWM', 'XLK', 'XLF', 'XLE'
]

def get_top_movers(direction='gainers', limit=20):
    """Get top gainers or losers from predefined watchlist — uses batch fetch"""
    watchlist = [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD',
        'NFLX', 'DIS', 'PYPL', 'INTC', 'BABA', 'PFE', 'WMT', 'JPM',
        'BAC', 'XOM', 'CVX', 'T', 'VZ', 'KO', 'PEP', 'NKE', 'BA',
        'UBER', 'LYFT', 'SNAP', 'SPOT', 'RIVN', 'ZM', 'DOCU', 'TWLO',
        'CRM', 'ORCL', 'CSCO', 'IBM', 'QCOM', 'TXN', 'AVGO', 'MU'
    ]
    
    # Use single batch call instead of per-symbol ThreadPoolExecutor
    all_quotes = _fetch_all_quotes_batch(watchlist)
    results = [q for q in all_quotes.values() if q and q.get('changePct') is not None]
    
    if direction == 'gainers':
        filtered = [r for r in results if r.get('changePct', 0) > 0]
        filtered = sorted(filtered, key=lambda x: x.get('changePct', 0), reverse=True)
    else:
        filtered = [r for r in results if r.get('changePct', 0) < 0]
        filtered = sorted(filtered, key=lambda x: x.get('changePct', 0), reverse=False)
    
    return filtered[:limit]

def get_extended_hours_data(symbol):
    """Fetch pre-market or after-hours data for a symbol"""
    try:
        info = cached_get_ticker_info(symbol)
        
        # Skip symbols with no price data
        if not info or (not info.get('currentPrice') and not info.get('regularMarketPrice')):
            if symbol not in KNOWN_DELISTED and not _is_rate_limited(symbol):
                print(f"[Extended] {symbol}: no price data (API issue or market closed)")
            return None
        
        current_price = info.get('currentPrice') or info.get('regularMarketPrice', 0)
        
        # Get previous close
        prev_close = info.get('previousClose', info.get('regularMarketPreviousClose', 0))
        
        # Get pre/post market price if available
        pre_market_price = info.get('preMarketPrice')
        post_market_price = info.get('postMarketPrice')
        
        # Determine which session we're in
        market_state, _ = get_market_status()
        
        if market_state == 'PRE_MARKET' and pre_market_price:
            extended_price = pre_market_price
            extended_change = extended_price - prev_close
            extended_change_pct = (extended_change / prev_close * 100) if prev_close > 0 else 0
            session_type = 'pre-market'
        elif market_state == 'AFTER_HOURS' and post_market_price:
            extended_price = post_market_price
            extended_change = extended_price - prev_close
            extended_change_pct = (extended_change / prev_close * 100) if prev_close > 0 else 0
            session_type = 'after-hours'
        else:
            # Use regular market data
            extended_price = current_price
            extended_change = current_price - prev_close
            extended_change_pct = (extended_change / prev_close * 100) if prev_close > 0 else 0
            session_type = 'regular'
        
        # Get volume data
        hist = cached_get_history(symbol, period='2d', interval='1d')
        volume = int(hist['Volume'].iloc[-1]) if hist is not None and not hist.empty else 0
        avg_volume = info.get('averageVolume', 0)
        
        return {
            'symbol': symbol,
            'price': round(extended_price, 2),
            'prevClose': round(prev_close, 2),
            'change': round(extended_change, 2),
            'changePct': round(extended_change_pct, 2),
            'volume': volume,
            'avgVolume': avg_volume,
            'sessionType': session_type,
            'marketState': market_state
        }
    except Exception as e:
        if not _is_expected_no_data_error(e):
            _log_fetch_event('extended-hours-error', symbol, f"Error fetching extended hours data for {symbol}: {e}", cooldown=180)
        return None

def get_premarket_movers(limit=20):
    """Get top pre-market gainers and losers using batch download (single API call)"""
    # Use _fetch_all_quotes_batch for single yf.download() call
    all_quotes = _fetch_all_quotes_batch(EXTENDED_HOURS_WATCHLIST)
    
    results = []
    market_state, _ = get_market_status()
    session_type = 'pre-market' if market_state == 'PRE_MARKET' else (
        'after-hours' if market_state == 'AFTER_HOURS' else 'regular'
    )
    
    for sym in EXTENDED_HOURS_WATCHLIST:
        if sym in all_quotes:
            q = all_quotes[sym]
            results.append({
                'symbol': sym,
                'price': q.get('price', 0),
                'prevClose': q.get('open', 0),  # approx
                'change': q.get('change', 0),
                'changePct': q.get('changePct', 0),
                'volume': q.get('volume', 0),
                'avgVolume': 0,
                'sessionType': session_type,
                'marketState': market_state
            })
    
    # Sort by absolute change percentage
    results = sorted(results, key=lambda x: abs(x.get('changePct', 0)), reverse=True)
    
    gainers = [r for r in results if r.get('changePct', 0) > 0][:limit]
    losers = [r for r in results if r.get('changePct', 0) < 0][:limit]
    
    return {
        'gainers': gainers,
        'losers': losers,
        'timestamp': datetime.now().isoformat(),
        'total_scanned': len(results)
    }

def get_afterhours_movers(limit=20):
    """Get top after-hours gainers and losers"""
    # Use same logic as pre-market
    return get_premarket_movers(limit=limit)

