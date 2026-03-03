"""
Market Data Service - Centralized caching layer for yfinance API calls.
Contains all cache variables, rate limiting, and cached fetch functions.
"""
import os
import threading
import time
import functools
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

# Suppress yfinance noisy warnings
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# Use project-local yfinance cache
YF_CACHE_DIR = os.path.join(os.getcwd(), ".yfinance_cache")
os.makedirs(YF_CACHE_DIR, exist_ok=True)
try:
    import yfinance.cache as _yf_cache_mod
    _yf_cache_mod.set_cache_location(YF_CACHE_DIR)
    yf.set_tz_cache_location(YF_CACHE_DIR)
except Exception as _yf_cache_err:
    print(f"⚠️ yfinance cache configuration warning: {_yf_cache_err}")

quote_cache = {}
cache_timeout = 60  # seconds (increased from 15 to reduce Yahoo API hits)
active_subscriptions = {}  # Track active subscription threads

# =============================
# GLOBAL MARKET DATA CACHE
# =============================
# Centralized caching layer to minimize yfinance API calls.
# All functions should use these cache helpers instead of calling yf directly.

import functools

_price_cache = {}          # {symbol: {'price': float, 'hist': DataFrame, 'ts': datetime}}
_price_cache_ttl = 60      # seconds — stock prices cached for 60s
_price_cache_lock = threading.Lock()

_history_cache = {}        # {(symbol, period, interval): {'data': DataFrame, 'ts': datetime}}
_history_cache_ttl = 120   # seconds — history data cached for 2 min
_history_cache_lock = threading.Lock()
_history_rate_limit_block = {}  # {key: unix_timestamp_until} — shared by price + history
_history_rate_limit_ttl = 120   # seconds — cooldown after per-symbol 429
_history_rate_limit_lock = threading.Lock()
_rate_limit_log = {'last_log_ts': 0.0, 'suppressed': 0}
_rate_limit_log_lock = threading.Lock()

# GLOBAL rate limit — when Yahoo blocks the IP entirely, stop ALL requests
_global_rate_limit_until = 0.0  # unix timestamp; 0 = not blocked
_global_rate_limit_lock = threading.Lock()
_global_rate_limit_cooldown = 300  # seconds — back off for 5 min on global 429

_chain_cache = {}          # {(symbol, expiry): {'chain': OptionChain, 'ts': datetime}}
_chain_cache_ttl = 60      # seconds — option chains cached for 60s
_chain_cache_lock = threading.Lock()

_options_dates_cache = {}  # {symbol: {'dates': list, 'ts': datetime}}
_options_dates_ttl = 300   # seconds — expiry dates rarely change (5 min cache)
_options_dates_lock = threading.Lock()

_ticker_info_cache = {}    # {symbol: {'info': dict, 'ts': datetime}}
_ticker_info_ttl = 300     # seconds — fundamentals cached for 5 min
_ticker_info_lock = threading.Lock()

_fetch_log_cache = {}      # {(kind, key): last_log_unix_ts}
_fetch_log_lock = threading.Lock()


def _log_rate_limit(symbol):
    """Aggregate repeated 429 logs to avoid terminal spam."""
    now_ts = time.time()
    with _rate_limit_log_lock:
        _rate_limit_log['suppressed'] += 1
        elapsed = now_ts - _rate_limit_log['last_log_ts']
        if elapsed < 30:
            return
        suppressed = _rate_limit_log['suppressed']
        _rate_limit_log['suppressed'] = 0
        _rate_limit_log['last_log_ts'] = now_ts
    print(f"[Cache] Yahoo rate-limited {suppressed} request(s). Last symbol={symbol}; cooldown={_history_rate_limit_ttl}s")


def _is_globally_rate_limited():
    """Check if we're under a global IP-level rate limit from Yahoo."""
    now_ts = time.time()
    with _global_rate_limit_lock:
        return now_ts < _global_rate_limit_until


def _mark_global_rate_limit():
    """Set the global rate limit flag — blocks ALL Yahoo requests for the cooldown period."""
    now_ts = time.time()
    with _global_rate_limit_lock:
        global _global_rate_limit_until
        _global_rate_limit_until = now_ts + _global_rate_limit_cooldown
    print(f"[RateLimit] ⛔ GLOBAL rate limit triggered. All Yahoo requests paused for {_global_rate_limit_cooldown}s")


def _is_rate_limited(symbol):
    """Check if a symbol (or global IP) is currently under rate-limit cooldown."""
    if _is_globally_rate_limited():
        return True
    now_ts = time.time()
    with _history_rate_limit_lock:
        blocked_until = _history_rate_limit_block.get(symbol, 0)
    return now_ts < blocked_until


def _mark_rate_limited(symbol):
    """Mark a symbol as rate-limited for the cooldown period."""
    now_ts = time.time()
    with _history_rate_limit_lock:
        _history_rate_limit_block[symbol] = now_ts + _history_rate_limit_ttl
    _log_rate_limit(symbol)


def _is_rate_limit_error(e):
    """Check if an exception is a Yahoo rate-limit (429) error."""
    error_text = str(e).lower()
    return 'too many requests' in error_text or 'rate limit' in error_text or '429' in error_text


def _is_expected_no_data_error(e):
    """Best-effort filter for frequent, non-actionable Yahoo/yfinance data-miss errors."""
    text = str(e).lower()
    noisy_markers = (
        'possibly delisted',
        'no price data found',
        'no data found',
        'failed to get ticker',
        'could not get exchange timezone',
        'no timezone found',
        'unable to open database file',
        'no such table: _kv',
        'no such table: _cookieschema',
        'no such table'
    )
    return any(marker in text for marker in noisy_markers)


def _log_fetch_event(kind, key, message, cooldown=120):
    """Log fetch-related messages with per-key cooldown to reduce terminal noise."""
    now_ts = time.time()
    cache_key = (str(kind), str(key))
    with _fetch_log_lock:
        last_ts = _fetch_log_cache.get(cache_key, 0)
        if now_ts - last_ts < cooldown:
            return
        _fetch_log_cache[cache_key] = now_ts
    print(message)


def cached_get_price(symbol, period='1d', interval='1m', prepost=True, use_cache=True):
    """Get current price for a symbol with caching. Returns (price, hist_df) or (None, None)."""
    cache_key = symbol
    now = datetime.now()
    
    if use_cache:
        with _price_cache_lock:
            if cache_key in _price_cache:
                entry = _price_cache[cache_key]
                if (now - entry['ts']).total_seconds() < _price_cache_ttl:
                    return entry['price'], entry.get('hist')
    
    # Skip if rate-limited (per-symbol or global)
    if _is_rate_limited(symbol):
        # Return stale cache entry if available (better than nothing)
        with _price_cache_lock:
            if cache_key in _price_cache:
                entry = _price_cache[cache_key]
                return entry['price'], entry.get('hist')
        return None, None

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval, prepost=prepost)
        if not hist.empty:
            price = float(hist['Close'].iloc[-1])
            with _price_cache_lock:
                _price_cache[cache_key] = {'price': price, 'hist': hist, 'ts': now}
            return price, hist
        # Fallback to 5d
        hist = ticker.history(period='5d', prepost=prepost)
        if not hist.empty:
            price = float(hist['Close'].iloc[-1])
            with _price_cache_lock:
                _price_cache[cache_key] = {'price': price, 'hist': hist, 'ts': now}
            return price, hist
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
            _mark_global_rate_limit()  # If one symbol 429s, Yahoo has blocked the whole IP
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('price-error', symbol, f"[Cache] Error fetching price for {symbol}: {e}", cooldown=180)
    return None, None


def cached_batch_prices(symbols, period='1d', interval='1m', prepost=True, use_cache=True):
    """Batch fetch prices for multiple symbols. Returns {symbol: price}."""
    now = datetime.now()
    prices = {}
    uncached = []

    # Always check cache first (even with use_cache=False, serve stale data when globally blocked)
    with _price_cache_lock:
        for sym in symbols:
            if sym in _price_cache:
                entry = _price_cache[sym]
                age = (now - entry['ts']).total_seconds()
                if use_cache and age < _price_cache_ttl:
                    prices[sym] = entry['price']
                elif _is_globally_rate_limited():
                    # Serve stale cache when globally rate-limited (better than nothing)
                    prices[sym] = entry['price']
                else:
                    uncached.append(sym)
            else:
                uncached.append(sym)

    # If globally rate-limited, don't make any API calls
    if _is_globally_rate_limited():
        return prices
    
    if uncached:
        try:
            # yf.download can fetch multiple symbols at once — single API call
            data = yf.download(uncached, period=period, interval=interval, 
                             prepost=prepost, group_by='ticker', threads=True,
                             progress=False)
            now = datetime.now()
            
            if data.empty:
                pass  # No data returned, fall through to fallback
            elif len(uncached) == 1:
                sym = uncached[0]
                # Single symbol: yfinance returns flat columns like 'Close', 'Open', etc.
                if 'Close' in data.columns:
                    close_col = data['Close'].dropna()
                    if not close_col.empty:
                        price = float(close_col.iloc[-1])
                        prices[sym] = price
                        with _price_cache_lock:
                            _price_cache[sym] = {'price': price, 'hist': data, 'ts': now}
            else:
                # Multiple symbols: yfinance returns MultiIndex columns (symbol, field)
                # Check if we have MultiIndex columns
                if hasattr(data.columns, 'get_level_values'):
                    try:
                        available_symbols = list(data.columns.get_level_values(0).unique())
                    except Exception:
                        available_symbols = []
                    
                    for sym in uncached:
                        try:
                            if sym in available_symbols:
                                sym_data = data[sym]
                                if not sym_data.empty and 'Close' in sym_data.columns:
                                    close_col = sym_data['Close'].dropna()
                                    if not close_col.empty:
                                        price = float(close_col.iloc[-1])
                                        prices[sym] = price
                                        with _price_cache_lock:
                                            _price_cache[sym] = {'price': price, 'hist': sym_data, 'ts': now}
                        except Exception:
                            pass
                else:
                    # Flat columns - try to extract 'Close' directly
                    if 'Close' in data.columns:
                        close_col = data['Close'].dropna()
                        if not close_col.empty:
                            # For single result, apply to first uncached symbol
                            price = float(close_col.iloc[-1])
                            prices[uncached[0]] = price
        except Exception as e:
            # Suppress rate-limit and trivial errors
            if _is_rate_limit_error(e):
                _log_rate_limit('batch')
                _mark_global_rate_limit()  # Batch 429 = entire IP is blocked
                # Serve any stale cache for uncached symbols
                with _price_cache_lock:
                    for sym in uncached:
                        if sym not in prices and sym in _price_cache:
                            prices[sym] = _price_cache[sym]['price']
                return prices
            elif "'Close'" not in str(e):
                _log_fetch_event('batch-error', 'prices', f"[Cache] Batch download error: {e}", cooldown=120)

        # Fallback: individually fetch any symbols still missing after batch
        # (batch can return empty for some symbols, e.g. outside market hours
        # with 1m interval, or partial data from yf.download)
        for sym in uncached:
            if sym not in prices:
                p, _ = cached_get_price(sym, period, interval, prepost, use_cache=use_cache)
                if p is not None:
                    prices[sym] = p
    
    return prices


def fetch_quote_api_batch(symbols, timeout=6):
    """Fetch best-effort live prices from Yahoo quote API (non-yfinance fallback)."""
    unique_symbols = sorted(set((s or '').strip().upper() for s in symbols if s))
    if not unique_symbols:
        return {}

    try:
        import requests
        url = "https://query1.finance.yahoo.com/v7/finance/quote"
        resp = requests.get(url, params={'symbols': ','.join(unique_symbols)}, timeout=timeout)
        if resp.status_code != 200:
            return {}
        payload = resp.json() or {}
        rows = (payload.get('quoteResponse') or {}).get('result') or []
        out = {}
        for item in rows:
            symbol = (item.get('symbol') or '').upper()
            if not symbol:
                continue
            price = (
                item.get('regularMarketPrice')
                or item.get('postMarketPrice')
                or item.get('preMarketPrice')
                or item.get('currentPrice')
            )
            if price is not None:
                try:
                    out[symbol] = float(price)
                except Exception:
                    pass
        return out
    except Exception:
        return {}


def cached_get_history(symbol, period='3mo', interval='1d', prepost=False):
    """Get historical data with caching. Returns DataFrame or None."""
    cache_key = (symbol, period, interval)
    now = datetime.now()
    now_ts = time.time()
    
    with _history_cache_lock:
        if cache_key in _history_cache:
            entry = _history_cache[cache_key]
            if (now - entry['ts']).total_seconds() < _history_cache_ttl:
                return entry['data']

    # Skip if rate-limited (per-symbol or global)
    if _is_rate_limited(symbol):
        # Return stale cache if available
        with _history_cache_lock:
            if cache_key in _history_cache:
                return _history_cache[cache_key]['data']
        return None
    
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, prepost=prepost)
        if not df.empty:
            with _history_cache_lock:
                _history_cache[cache_key] = {'data': df, 'ts': now}
            return df
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
            _mark_global_rate_limit()
            # Return stale cache
            with _history_cache_lock:
                if cache_key in _history_cache:
                    return _history_cache[cache_key]['data']
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('history-error', symbol, f"[Cache] Error fetching history for {symbol}: {e}", cooldown=180)
    return None


def cached_get_option_dates(symbol):
    """Get available option expiration dates with caching. Returns list or []."""
    now = datetime.now()
    
    with _options_dates_lock:
        if symbol in _options_dates_cache:
            entry = _options_dates_cache[symbol]
            if (now - entry['ts']).total_seconds() < _options_dates_ttl:
                return entry['dates']
    
    try:
        ticker = yf.Ticker(symbol)
        dates = list(ticker.options)
        with _options_dates_lock:
            _options_dates_cache[symbol] = {'dates': dates, 'ts': now}
        return dates
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
            _mark_global_rate_limit()
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('option-dates-error', symbol, f"[Cache] Error fetching option dates for {symbol}: {e}", cooldown=180)
    return []


def cached_get_option_chain(symbol, expiry, use_cache=True):
    """Get option chain for a symbol+expiry with caching. Returns chain or None."""
    cache_key = (symbol, expiry)
    now = datetime.now()

    if use_cache:
        with _chain_cache_lock:
            if cache_key in _chain_cache:
                entry = _chain_cache[cache_key]
                if (now - entry['ts']).total_seconds() < _chain_cache_ttl:
                    return entry['chain']

    # Respect global rate limit — serve stale cache if available
    if _is_globally_rate_limited():
        with _chain_cache_lock:
            if cache_key in _chain_cache:
                return _chain_cache[cache_key]['chain']
        return None
    
    try:
        ticker = yf.Ticker(symbol)
        chain = ticker.option_chain(expiry)
        with _chain_cache_lock:
            _chain_cache[cache_key] = {'chain': chain, 'ts': now}
        return chain
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
            _mark_global_rate_limit()
            with _chain_cache_lock:
                if cache_key in _chain_cache:
                    return _chain_cache[cache_key]['chain']
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('option-chain-error', f"{symbol}:{expiry}", f"[Cache] Error fetching option chain for {symbol} {expiry}: {e}", cooldown=180)
    return None


def cached_get_ticker_info(symbol):
    """Get ticker.info with caching. Returns dict or {}."""
    now = datetime.now()
    
    with _ticker_info_lock:
        if symbol in _ticker_info_cache:
            entry = _ticker_info_cache[symbol]
            if (now - entry['ts']).total_seconds() < _ticker_info_ttl:
                return entry['info']

    # Skip upstream call while rate-limited (per-symbol or global)
    if _is_rate_limited(symbol):
        # Return stale cache if available
        with _ticker_info_lock:
            if symbol in _ticker_info_cache:
                return _ticker_info_cache[symbol]['info']
        return {}
    
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        with _ticker_info_lock:
            _ticker_info_cache[symbol] = {'info': info, 'ts': now}
        return info
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
            _mark_global_rate_limit()
            with _ticker_info_lock:
                if symbol in _ticker_info_cache:
                    return _ticker_info_cache[symbol]['info']
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('info-error', symbol, f"[Cache] Error fetching info for {symbol}: {e}", cooldown=180)
    return {}


def clear_all_caches():
    """Clear all caches — useful for forced refresh."""
    global _global_rate_limit_until
    with _price_cache_lock:
        _price_cache.clear()
    with _history_cache_lock:
        _history_cache.clear()
    with _history_rate_limit_lock:
        _history_rate_limit_block.clear()
    with _chain_cache_lock:
        _chain_cache.clear()
    with _options_dates_lock:
        _options_dates_cache.clear()
    with _ticker_info_lock:
        _ticker_info_cache.clear()
    with _global_rate_limit_lock:
        _global_rate_limit_until = 0.0
    print("[Cache] All caches and rate-limit blocks cleared")


def clear_rate_limit_blocks():
    """Clear only rate-limit guards without dropping data caches."""
    global _global_rate_limit_until
    with _history_rate_limit_lock:
        _history_rate_limit_block.clear()
    with _global_rate_limit_lock:
        _global_rate_limit_until = 0.0

# Scanner results cache (long-lived)
scanner_cache = {
    'unified': {'data': None, 'timestamp': None, 'running': False},
    'short-squeeze': {'data': None, 'timestamp': None, 'running': False},
    'quality-stocks': {'data': None, 'timestamp': None, 'running': False},
    'weekly-screener': {'data': None, 'timestamp': None, 'running': False},
    'golden-cross': {'data': None, 'timestamp': None, 'running': False},
    'triple-confirmation': {'data': None, 'timestamp': None, 'running': False},
    'triple-intraday': {'data': None, 'timestamp': None, 'running': False},
    'triple-positional': {'data': None, 'timestamp': None, 'running': False},
    'triple-confirmation-all': {'data': None, 'timestamp': None, 'running': False},
    'options-analysis': {'data': None, 'timestamp': None, 'running': False},
    'volume-spike': {'data': None, 'timestamp': None, 'running': False},
    'etf-scanner': {'data': None, 'timestamp': None, 'running': False},
    'intraday-stocks': {'data': None, 'timestamp': None, 'running': False},
    'intraday-options': {'data': None, 'timestamp': None, 'running': False}
}
scanner_cache_timeout = 600  # 10 minutes - increased to reduce API calls

# Autonomous trading state
autonomous_trader_state = {
    'running': False,
    'trader': None,
    'thread': None,
    'positions': {},
    'trade_log': [],
    'daily_pnl': 0.0,
    'last_scan': None,
    'settings': {
        'api_key_set': False,
        'broker_connected': False,
        'paper_trading': True,
        'scan_interval': 5,
        'min_confidence': 75,
        'max_positions': 5,
        'risk_per_trade': 0.02
    }
}


def _fetch_all_quotes_batch(symbols):
    """Fetch quotes for all symbols using a SINGLE yf.download() call.
    
    This replaces the old per-symbol ThreadPoolExecutor approach which generated
    ~200 individual API calls. Now uses one batch download request.
    """
    unique_symbols = list(set(s.strip().upper() for s in symbols if s))
    if not unique_symbols:
        return {}
    
    results = {}
    now = datetime.now()
    uncached = []
    
    # 1. Serve from quote_cache where still fresh
    for sym in unique_symbols:
        if sym in quote_cache:
            cached_data, ts = quote_cache[sym]
            if (now - ts).total_seconds() < cache_timeout:
                results[sym] = cached_data
            else:
                uncached.append(sym)
        else:
            uncached.append(sym)
    
    if not uncached:
        return results   # everything served from cache
    
    # 2. If globally rate-limited, serve stale cache entries
    if _is_globally_rate_limited():
        for sym in uncached:
            if sym in quote_cache:
                cached_data, _ = quote_cache[sym]
                results[sym] = cached_data
        return results
    
    # 3. Single yf.download() batch call
    try:
        data = yf.download(
            uncached,
            period='5d',
            interval='1d',
            prepost=True,
            group_by='ticker',
            threads=True,
            progress=False,
            timeout=60
        )
        
        if data is None or data.empty:
            # Serve stale cache as fallback
            for sym in uncached:
                if sym in quote_cache:
                    results[sym] = quote_cache[sym][0]
            return results
        
        fetch_time = datetime.now()
        
        if len(uncached) == 1:
            # Single symbol -> flat columns (Close, Open, etc.)
            sym = uncached[0]
            try:
                if 'Close' in data.columns and len(data) >= 2:
                    close_vals = data['Close'].dropna()
                    if len(close_vals) >= 2:
                        current_price = float(close_vals.iloc[-1])
                        prev_close = float(close_vals.iloc[-2])
                        change = current_price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                        quote = {
                            'symbol': sym,
                            'price': round(current_price, 2),
                            'change': round(change, 2),
                            'changePct': round(change_pct, 2),
                            'volume': int(data['Volume'].iloc[-1]) if 'Volume' in data.columns else 0,
                            'high': round(float(data['High'].iloc[-1]), 2) if 'High' in data.columns else 0,
                            'low': round(float(data['Low'].iloc[-1]), 2) if 'Low' in data.columns else 0,
                            'open': round(float(data['Open'].iloc[-1]), 2) if 'Open' in data.columns else 0,
                        }
                        results[sym] = quote
                        quote_cache[sym] = (quote, fetch_time)
            except Exception:
                pass
        else:
            # Multiple symbols -> MultiIndex columns (symbol, field)
            avail_symbols = []
            if hasattr(data.columns, 'get_level_values'):
                try:
                    avail_symbols = list(data.columns.get_level_values(0).unique())
                except Exception:
                    avail_symbols = []
            
            for sym in uncached:
                try:
                    if sym not in avail_symbols:
                        continue
                    sym_data = data[sym]
                    if sym_data is None or sym_data.empty:
                        continue
                    close_vals = sym_data['Close'].dropna() if 'Close' in sym_data.columns else pd.Series(dtype=float)
                    if len(close_vals) < 2:
                        continue
                    current_price = float(close_vals.iloc[-1])
                    prev_close = float(close_vals.iloc[-2])
                    if current_price <= 0:
                        continue
                    change = current_price - prev_close
                    change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                    quote = {
                        'symbol': sym,
                        'price': round(current_price, 2),
                        'change': round(change, 2),
                        'changePct': round(change_pct, 2),
                        'volume': int(sym_data['Volume'].iloc[-1]) if 'Volume' in sym_data.columns else 0,
                        'high': round(float(sym_data['High'].iloc[-1]), 2) if 'High' in sym_data.columns else 0,
                        'low': round(float(sym_data['Low'].iloc[-1]), 2) if 'Low' in sym_data.columns else 0,
                        'open': round(float(sym_data['Open'].iloc[-1]), 2) if 'Open' in sym_data.columns else 0,
                    }
                    results[sym] = quote
                    quote_cache[sym] = (quote, fetch_time)
                except Exception:
                    continue
        
        print(f"📊 Batch quotes: fetched {len(results)}/{len(unique_symbols)} symbols in 1 API call")
    
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_global_rate_limit()
        print(f"Batch quote download error: {e}")
        # Serve stale cache as fallback
        for sym in uncached:
            if sym in quote_cache:
                results[sym] = quote_cache[sym][0]
    
    return results

