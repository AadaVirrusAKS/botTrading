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

def _nuke_yf_cache():
    """Delete corrupted yfinance SQLite cache files and recreate the directory."""
    import glob
    for db_file in glob.glob(os.path.join(YF_CACHE_DIR, "*.db*")):
        try:
            os.remove(db_file)
        except OSError:
            pass
    print("🔄 Cleared corrupted yfinance cache")

def _validate_yf_cache():
    """Check that yfinance SQLite databases have required tables; nuke if corrupt."""
    import sqlite3
    for db_name in ("tkr-tz.db", "cookies.db"):
        db_path = os.path.join(YF_CACHE_DIR, db_name)
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            conn.close()
            if not tables:
                _nuke_yf_cache()
                return
        except Exception:
            _nuke_yf_cache()
            return

_validate_yf_cache()

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
_history_cache_ttl = 300   # seconds — history data cached for 5 min (scanners don't need fresher)
_history_cache_lock = threading.Lock()
_history_rate_limit_block = {}  # {key: unix_timestamp_until} — shared by price + history
_history_rate_limit_ttl = 120   # seconds — cooldown after per-symbol 429
_history_rate_limit_lock = threading.Lock()
_rate_limit_log = {'last_log_ts': 0.0, 'suppressed': 0}
_rate_limit_log_lock = threading.Lock()

# GLOBAL rate limit — when Yahoo blocks the IP entirely, stop ALL requests
_global_rate_limit_until = 0.0  # unix timestamp; 0 = not blocked
_global_rate_limit_lock = threading.Lock()
_global_rate_limit_cooldown = 60   # seconds — initial backoff on global 429
_global_rate_limit_consecutive = 0  # consecutive 429s — drives progressive backoff
_global_rate_limit_max_cooldown = 300  # max backoff cap (5 min)

_chain_cache = {}          # {(symbol, expiry): {'chain': OptionChain, 'ts': datetime}}
_chain_cache_ttl = 45      # seconds — option chains cached for 45s (was 180s; shorter for live premium monitoring)
_chain_cache_lock = threading.Lock()

_options_dates_cache = {}  # {symbol: {'dates': list, 'ts': datetime}}
_options_dates_ttl = 3600  # seconds — expiry dates rarely change intraday (1 hour)
_options_dates_lock = threading.Lock()

_ticker_info_cache = {}    # {symbol: {'info': dict, 'ts': datetime}}
_ticker_info_ttl = 300     # seconds — fundamentals cached for 5 min
_ticker_info_lock = threading.Lock()

_fetch_log_cache = {}      # {(kind, key): last_log_unix_ts}
_fetch_log_lock = threading.Lock()

# =============================
# GLOBAL TOKEN-BUCKET THROTTLE
# =============================
# Limits total Yahoo API requests across ALL callers to avoid 429s.
_throttle_tokens = 8.0           # current tokens (starts full)
_throttle_max_tokens = 8.0       # max burst
_throttle_refill_rate = 3.0      # tokens per second (3 req/s sustained)
_throttle_last_refill = time.time()
_throttle_lock = threading.Lock()


def _throttle_acquire(timeout=10):
    """Acquire a token from the global rate limiter. Blocks until available or timeout.
    Returns True if acquired, False if timed out."""
    global _throttle_tokens, _throttle_last_refill
    deadline = time.time() + timeout
    while True:
        now = time.time()
        if now > deadline:
            return False
        with _throttle_lock:
            # Refill tokens
            elapsed = now - _throttle_last_refill
            _throttle_tokens = min(_throttle_max_tokens, _throttle_tokens + elapsed * _throttle_refill_rate)
            _throttle_last_refill = now
            if _throttle_tokens >= 1.0:
                _throttle_tokens -= 1.0
                return True
        # Wait a short time before retrying
        time.sleep(0.1)


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
    """Set the global rate limit flag with progressive backoff.
    First 429 → 60s, second consecutive → 120s, third → 180s, max 300s.
    Resets to 0 after a successful request."""
    now_ts = time.time()
    with _global_rate_limit_lock:
        global _global_rate_limit_until, _global_rate_limit_consecutive
        _global_rate_limit_consecutive += 1
        cooldown = min(
            _global_rate_limit_cooldown * _global_rate_limit_consecutive,
            _global_rate_limit_max_cooldown
        )
        _global_rate_limit_until = now_ts + cooldown
    print(f"[RateLimit] ⛔ GLOBAL rate limit triggered (#{_global_rate_limit_consecutive}). "
          f"All Yahoo requests paused for {cooldown}s")


def _mark_global_rate_limit_success():
    """Reset consecutive 429 counter AND lift global block after a successful Yahoo API call."""
    global _global_rate_limit_consecutive, _global_rate_limit_until
    with _global_rate_limit_lock:
        if _global_rate_limit_consecutive > 0:
            _global_rate_limit_consecutive = 0
        if _global_rate_limit_until > 0:
            _global_rate_limit_until = 0.0


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


# Minimum cache TTL even when force_live is requested — prevents
# aggressive polling (e.g. 10-second interval) from hammering Yahoo.
_FORCE_LIVE_MIN_TTL = 15   # seconds — fresh-enough for real-time monitoring

def cached_get_price(symbol, period='1d', interval='1m', prepost=True, use_cache=True):
    """Get current price for a symbol with caching. Returns (price, hist_df) or (None, None)."""
    cache_key = symbol
    now = datetime.now()
    
    with _price_cache_lock:
        if cache_key in _price_cache:
            entry = _price_cache[cache_key]
            age = (now - entry['ts']).total_seconds()
            if use_cache and age < _price_cache_ttl:
                return entry['price'], entry.get('hist')
            # Even for force-live, honour a short minimum TTL
            if (not use_cache) and age < _FORCE_LIVE_MIN_TTL:
                return entry['price'], entry.get('hist')
    
    # Skip if rate-limited (per-symbol or global)
    if _is_rate_limited(symbol):
        # Return stale cache entry if available (better than nothing)
        with _price_cache_lock:
            if cache_key in _price_cache:
                entry = _price_cache[cache_key]
                return entry['price'], entry.get('hist')
        return None, None

    # Throttle: wait for a token before making the API call
    if not _throttle_acquire(timeout=10):
        with _price_cache_lock:
            if cache_key in _price_cache:
                return _price_cache[cache_key]['price'], _price_cache[cache_key].get('hist')
        return None, None

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval, prepost=prepost)
        if not hist.empty:
            price = float(hist['Close'].iloc[-1])
            _mark_global_rate_limit_success()
            with _price_cache_lock:
                _price_cache[cache_key] = {'price': price, 'hist': hist, 'ts': now}
            return price, hist
        # Fallback: try 5d with 5m interval (works pre-market when 1m is empty)
        hist = ticker.history(period='5d', interval='5m', prepost=prepost)
        if not hist.empty:
            price = float(hist['Close'].iloc[-1])
            with _price_cache_lock:
                _price_cache[cache_key] = {'price': price, 'hist': hist, 'ts': now}
            return price, hist
        # Last resort: 5d daily (always available)
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

    # Always check cache first.
    # Even with use_cache=False (force-live), honour a short minimum TTL
    # so that 10-second polling doesn't fire redundant API calls.
    with _price_cache_lock:
        for sym in symbols:
            if sym in _price_cache:
                entry = _price_cache[sym]
                age = (now - entry['ts']).total_seconds()
                if use_cache and age < _price_cache_ttl:
                    prices[sym] = entry['price']
                elif (not use_cache) and age < _FORCE_LIVE_MIN_TTL:
                    # force-live but data is very recent — reuse it
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
        # Throttle: single token for a batch download (it's one HTTP call)
        if not _throttle_acquire(timeout=15):
            # Couldn't get token in time, serve stale cache
            with _price_cache_lock:
                for sym in uncached:
                    if sym in _price_cache:
                        prices[sym] = _price_cache[sym]['price']
            return prices
        try:
            # yf.download can fetch multiple symbols at once — single API call
            data = yf.download(uncached, period=period, interval=interval, 
                             prepost=prepost, group_by='ticker', threads=True,
                             progress=False)
            now = datetime.now()
            _mark_global_rate_limit_success()
            
            if data is None or data.empty:
                _log_fetch_event('batch-empty', 'prices',
                    f"[BatchPrices] yf.download returned empty for {uncached} ({period}/{interval})",
                    cooldown=60)
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
            else:
                if "'Close'" not in str(e):
                    _log_fetch_event('batch-error', 'prices', f"[Cache] Batch download error: {e}", cooldown=120)

        # ── Retry with longer period / coarser interval if 1m batch was empty ──
        # Before market open (or early pre-market), 1-minute data for the new
        # trading day doesn't exist yet, so the initial batch returns empty.
        # A second batch with 5d/5m (or 5d/1d) is far more reliable.
        missing_after_batch = [s for s in uncached if s not in prices]
        if missing_after_batch and interval in ('1m', '2m'):
            _log_fetch_event('batch-retry', 'prices',
                f"[Cache] {len(missing_after_batch)} symbols missing after {interval} batch — retrying with 5d/5m",
                cooldown=300)
            try:
                data2 = yf.download(missing_after_batch, period='5d', interval='5m',
                                    prepost=prepost, group_by='ticker', threads=True,
                                    progress=False)
                now2 = datetime.now()
                if data2 is not None and not data2.empty:
                    if len(missing_after_batch) == 1:
                        sym = missing_after_batch[0]
                        if 'Close' in data2.columns:
                            close_col = data2['Close'].dropna()
                            if not close_col.empty:
                                price = float(close_col.iloc[-1])
                                prices[sym] = price
                                with _price_cache_lock:
                                    _price_cache[sym] = {'price': price, 'hist': data2, 'ts': now2}
                    else:
                        if hasattr(data2.columns, 'get_level_values'):
                            try:
                                avail2 = list(data2.columns.get_level_values(0).unique())
                            except Exception:
                                avail2 = []
                            for sym in missing_after_batch:
                                try:
                                    if sym in avail2:
                                        sd = data2[sym]
                                        if not sd.empty and 'Close' in sd.columns:
                                            cl = sd['Close'].dropna()
                                            if not cl.empty:
                                                price = float(cl.iloc[-1])
                                                prices[sym] = price
                                                with _price_cache_lock:
                                                    _price_cache[sym] = {'price': price, 'hist': sd, 'ts': now2}
                                except Exception:
                                    pass
            except Exception as e2:
                if _is_rate_limit_error(e2):
                    _mark_global_rate_limit()
                    with _price_cache_lock:
                        for sym in missing_after_batch:
                            if sym not in prices and sym in _price_cache:
                                prices[sym] = _price_cache[sym]['price']
                    return prices

        # Fallback: individually fetch any symbols still missing after batch
        # (batch can return empty for some symbols, e.g. outside market hours
        # with 1m interval, or partial data from yf.download)
        still_missing = [s for s in uncached if s not in prices]
        if still_missing:
            # Try individual yfinance calls first
            for sym in still_missing:
                if sym not in prices:
                    p, _ = cached_get_price(sym, period='5d', interval='5m', prepost=prepost, use_cache=use_cache)
                    if p is not None:
                        prices[sym] = p
            
            # If yfinance is completely blocked, use non-yfinance API fallback
            final_missing = [s for s in uncached if s not in prices]
            if final_missing:
                print(f"[BatchPrices] {len(final_missing)} symbols still missing after all yfinance attempts, trying API fallback")
                api_prices = fetch_quote_api_batch(final_missing)
                now_fb = datetime.now()
                for sym, price in api_prices.items():
                    if sym not in prices:
                        prices[sym] = price
                        with _price_cache_lock:
                            _price_cache[sym] = {'price': price, 'ts': now_fb}
                        print(f"[BatchPrices] ✅ Got {sym} from API fallback: ${price:.2f}")
                
                # Last resort: ticker info
                for sym in uncached:
                    if sym not in prices:
                        try:
                            info = cached_get_ticker_info(sym)
                            p = info.get('regularMarketPrice') or info.get('currentPrice') or info.get('previousClose')
                            if p and float(p) > 0:
                                prices[sym] = float(p)
                                with _price_cache_lock:
                                    _price_cache[sym] = {'price': float(p), 'ts': datetime.now()}
                                print(f"[BatchPrices] ✅ Got {sym} from ticker info: ${float(p):.2f}")
                        except Exception:
                            pass
    
    return prices


def fetch_quote_api_batch(symbols, timeout=8):
    """Fetch best-effort live prices using multiple Yahoo Finance API endpoints."""
    unique_symbols = sorted(set((s or '').strip().upper() for s in symbols if s))
    if not unique_symbols:
        return {}

    out = {}
    
    # Strategy 1: Yahoo v8 finance/chart endpoint (one per symbol, but very reliable)
    try:
        import requests
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        for sym in unique_symbols:
            if sym in out:
                continue
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                resp = session.get(url, params={'range': '1d', 'interval': '1m'}, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    meta = (data.get('chart', {}).get('result') or [{}])[0].get('meta', {})
                    price = meta.get('regularMarketPrice') or meta.get('previousClose')
                    if price:
                        out[sym] = float(price)
            except Exception:
                pass
        if out:
            return out
    except ImportError:
        pass
    
    # Strategy 2: yf.Ticker().fast_info (per-symbol, uses yfinance internals)
    for sym in unique_symbols:
        if sym in out:
            continue
        try:
            t = yf.Ticker(sym)
            fi = t.fast_info
            price = getattr(fi, 'last_price', None) or getattr(fi, 'previous_close', None)
            if price and float(price) > 0:
                out[sym] = float(price)
        except Exception:
            pass

    return out


# --- Yahoo v7 Options API fallback (bypasses yfinance rate limits) ---
# Uses a fresh session with cookie+crumb to access options data when yfinance is 429'd.
_v7_session = None
_v7_crumb = None
_v7_session_ts = 0.0
_v7_session_lock = threading.Lock()
_V7_SESSION_TTL = 900  # Re-authenticate every 15 min


def _get_v7_session():
    """Get or create an authenticated Yahoo Finance session for v7 API calls."""
    global _v7_session, _v7_crumb, _v7_session_ts
    now = time.time()
    with _v7_session_lock:
        if _v7_session and _v7_crumb and (now - _v7_session_ts) < _V7_SESSION_TTL:
            return _v7_session, _v7_crumb
    # Create fresh session outside the lock (network calls)
    import requests as _req
    s = _req.Session()
    s.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        s.get('https://fc.yahoo.com', timeout=8)
        r = s.get('https://query2.finance.yahoo.com/v1/test/getcrumb', timeout=8)
        crumb = r.text.strip() if r.status_code == 200 else None
        if crumb and 'Too Many' not in crumb:
            with _v7_session_lock:
                _v7_session = s
                _v7_crumb = crumb
                _v7_session_ts = time.time()
            return s, crumb
    except Exception:
        pass
    return None, None


def _fetch_option_dates_v7(symbol):
    """Fetch option expiration dates via v7 API with cookie+crumb. Returns list or None."""
    s, crumb = _get_v7_session()
    if not s or not crumb:
        return None
    try:
        url = f'https://query2.finance.yahoo.com/v7/finance/options/{symbol}?crumb={crumb}'
        r = s.get(url, timeout=10)
        if r.status_code != 200:
            if r.status_code == 429:
                # Session might be rate-limited too; invalidate it
                with _v7_session_lock:
                    global _v7_session_ts
                    _v7_session_ts = 0
            return None
        data = r.json()
        result = data.get('optionChain', {}).get('result', [])
        if not result:
            return None
        epochs = result[0].get('expirationDates', [])
        if not epochs:
            return None
        import calendar as _cal
        dates = [datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d') for ts in epochs]
        # Store epoch->date mapping so chain requests use exact Yahoo epochs
        _v7_epoch_map = getattr(_fetch_option_dates_v7, '_epoch_map', {})
        for ts, d in zip(epochs, dates):
            _v7_epoch_map[(symbol, d)] = ts
        _fetch_option_dates_v7._epoch_map = _v7_epoch_map
        _log_fetch_event('option-dates-v7', symbol,
            f"[Options] Got {len(dates)} dates for {symbol} via v7 API", cooldown=60)
        return dates
    except Exception:
        return None


def _fetch_option_chain_v7(symbol, expiry):
    """Fetch option chain via v7 API with cookie+crumb. Returns (calls_df, puts_df) or None."""
    s, crumb = _get_v7_session()
    if not s or not crumb:
        return None
    try:
        # Use exact epoch from Yahoo if we have it, else compute UTC midnight
        epoch_map = getattr(_fetch_option_dates_v7, '_epoch_map', {})
        exp_ts = epoch_map.get((symbol, expiry))
        if exp_ts is None:
            import calendar as _cal
            exp_ts = int(_cal.timegm(datetime.strptime(expiry, '%Y-%m-%d').timetuple()))
        url = f'https://query2.finance.yahoo.com/v7/finance/options/{symbol}?crumb={crumb}&date={exp_ts}'
        r = s.get(url, timeout=10)
        if r.status_code != 200:
            if r.status_code == 429:
                with _v7_session_lock:
                    global _v7_session_ts
                    _v7_session_ts = 0
            return None
        data = r.json()
        result = data.get('optionChain', {}).get('result', [])
        if not result:
            return None
        options = result[0].get('options', [])
        if not options:
            return None

        def _parse(contracts):
            rows = []
            for c in contracts:
                rows.append({
                    'contractSymbol': c.get('contractSymbol', ''),
                    'strike': c.get('strike', 0),
                    'lastPrice': c.get('lastPrice', 0),
                    'bid': c.get('bid', 0),
                    'ask': c.get('ask', 0),
                    'volume': c.get('volume', 0),
                    'openInterest': c.get('openInterest', 0),
                    'impliedVolatility': c.get('impliedVolatility', 0),
                    'inTheMoney': c.get('inTheMoney', False),
                })
            return pd.DataFrame(rows) if rows else pd.DataFrame()

        calls_df = _parse(options[0].get('calls', []))
        puts_df = _parse(options[0].get('puts', []))
        _log_fetch_event('option-chain-v7', f"{symbol}:{expiry}",
            f"[Options] Got chain for {symbol} {expiry} via v7 API (calls={len(calls_df)}, puts={len(puts_df)})", cooldown=60)
        return (calls_df, puts_df)
    except Exception:
        return None


def _fetch_history_v8_api(symbol, period='3mo', interval='1d'):
    """Fallback: fetch historical OHLCV data via Yahoo v8 chart API when yfinance is rate-limited.
    Returns a DataFrame compatible with yfinance output, or None."""
    try:
        import requests
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {'range': period, 'interval': interval, 'includePrePost': 'false'}
        resp = requests.get(url, params=params, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = (data.get('chart', {}).get('result') or [None])[0]
        if not result:
            return None
        timestamps = result.get('timestamp', [])
        quote = (result.get('indicators', {}).get('quote') or [{}])[0]
        if not timestamps or not quote:
            return None
        df = pd.DataFrame({
            'Open': quote.get('open', []),
            'High': quote.get('high', []),
            'Low': quote.get('low', []),
            'Close': quote.get('close', []),
            'Volume': quote.get('volume', []),
        }, index=pd.to_datetime(timestamps, unit='s'))
        # Match yfinance convention: intraday index named 'Datetime', daily named 'Date'
        is_intraday = interval in ('1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h')
        df.index.name = 'Datetime' if is_intraday else 'Date'
        df = df.dropna(subset=['Close'])
        if df.empty:
            return None
        _log_fetch_event('history-v8', symbol, f"[History] Got {len(df)} rows for {symbol} via v8 API ({period}/{interval})", cooldown=60)
        return df
    except Exception:
        return None


def prewarm_history_cache(symbols, period='5d', interval='5m'):
    """Pre-warm the history cache for multiple symbols using a single yf.download() call.
    This dramatically reduces API calls: 1 batch request vs N individual requests.
    Scanner functions that later call cached_get_history() will get cache hits.
    """
    if not symbols:
        return 0

    now = datetime.now()
    uncached = []
    
    # Only fetch symbols not already in cache
    with _history_cache_lock:
        for sym in symbols:
            cache_key = (sym, period, interval)
            if cache_key in _history_cache:
                entry = _history_cache[cache_key]
                if (now - entry['ts']).total_seconds() < _history_cache_ttl:
                    continue
            uncached.append(sym)
    
    if not uncached:
        return 0  # Everything already cached
    
    if _is_globally_rate_limited():
        _log_fetch_event('prewarm-skip', 'global',
            f"[PreWarm] Skipping pre-warm ({len(uncached)} symbols) — globally rate-limited", cooldown=60)
        return 0

    # Throttle: one token for the batch download
    if not _throttle_acquire(timeout=15):
        return 0

    warmed = 0
    # Download in sub-batches of 30 to avoid huge single requests
    batch_size = 30
    for i in range(0, len(uncached), batch_size):
        chunk = uncached[i:i + batch_size]
        try:
            data = yf.download(
                chunk, period=period, interval=interval,
                group_by='ticker', threads=True, progress=False
            )
            fetch_time = datetime.now()
            
            if data is None or data.empty:
                continue
            
            _mark_global_rate_limit_success()
            
            if len(chunk) == 1:
                sym = chunk[0]
                # Single symbol: flat columns
                if not data.empty and 'Close' in data.columns:
                    cache_key = (sym, period, interval)
                    with _history_cache_lock:
                        _history_cache[cache_key] = {'data': data.copy(), 'ts': fetch_time}
                    warmed += 1
            else:
                # Multiple symbols: MultiIndex columns
                if hasattr(data.columns, 'get_level_values'):
                    try:
                        avail = list(data.columns.get_level_values(0).unique())
                    except Exception:
                        avail = []
                    for sym in chunk:
                        try:
                            if sym in avail:
                                sym_data = data[sym]
                                if sym_data is not None and not sym_data.empty:
                                    cache_key = (sym, period, interval)
                                    with _history_cache_lock:
                                        _history_cache[cache_key] = {'data': sym_data.copy(), 'ts': fetch_time}
                                    warmed += 1
                        except Exception:
                            pass
        except Exception as e:
            if _is_rate_limit_error(e):
                _mark_global_rate_limit()
                break  # Stop pre-warming on rate limit

        # Brief pause between sub-batches
        if i + batch_size < len(uncached):
            if not _throttle_acquire(timeout=10):
                break
            time.sleep(0.5)

    # Phase 2: backfill any symbols that yf.download missed using v8 API
    # This ensures ALL symbols have cached history before the scanner runs,
    # so scan_intraday_option() never makes yfinance history calls (which compete
    # with option API calls for rate-limit budget).
    still_uncached = []
    with _history_cache_lock:
        for sym in uncached:
            cache_key = (sym, period, interval)
            if cache_key not in _history_cache or (now - _history_cache[cache_key]['ts']).total_seconds() >= _history_cache_ttl:
                still_uncached.append(sym)

    if still_uncached:
        v8_filled = 0
        for sym in still_uncached:
            df = _fetch_history_v8_api(sym, period=period, interval=interval)
            if df is not None and not df.empty:
                with _history_cache_lock:
                    _history_cache[(sym, period, interval)] = {'data': df, 'ts': datetime.now()}
                v8_filled += 1
                warmed += 1
            time.sleep(0.15)  # Brief pause between v8 calls
        if v8_filled > 0:
            print(f"[PreWarm] ✅ Backfilled {v8_filled}/{len(still_uncached)} symbols via v8 API")

    if warmed > 0:
        print(f"[PreWarm] ✅ Pre-warmed {warmed}/{len(uncached)} symbols ({period}/{interval}) total")
    return warmed


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
        # Try v8 API fallback (different rate-limit pool)
        df = _fetch_history_v8_api(symbol, period=period, interval=interval)
        if df is not None and not df.empty:
            _mark_global_rate_limit_success()  # v8 success = Yahoo partially working
            with _history_cache_lock:
                _history_cache[cache_key] = {'data': df, 'ts': now}
            return df
        return None
    
    # Throttle: wait for a token
    if not _throttle_acquire(timeout=10):
        with _history_cache_lock:
            if cache_key in _history_cache:
                return _history_cache[cache_key]['data']
        return None

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, prepost=prepost)
        if not df.empty:
            _mark_global_rate_limit_success()
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
    
    # Final fallback: v8 API
    df = _fetch_history_v8_api(symbol, period=period, interval=interval)
    if df is not None and not df.empty:
        _mark_global_rate_limit_success()  # v8 success = Yahoo partially working; unblock option chain calls
        with _history_cache_lock:
            _history_cache[cache_key] = {'data': df, 'ts': now}
        return df
    return None


def cached_get_option_dates(symbol):
    """Get available option expiration dates with caching. Returns list or []."""
    now = datetime.now()
    
    with _options_dates_lock:
        if symbol in _options_dates_cache:
            entry = _options_dates_cache[symbol]
            if (now - entry['ts']).total_seconds() < _options_dates_ttl:
                return entry['dates']
    
    # When globally rate-limited, serve stale cache but still attempt the call
    # (option endpoints are separate from history; prewarm v8 handles history so
    # option calls are the only yfinance traffic during scans)
    if _is_globally_rate_limited():
        with _options_dates_lock:
            if symbol in _options_dates_cache:
                return _options_dates_cache[symbol]['dates']
        # Fall through to try yfinance anyway — option calls are lightweight
    
    # Throttle: wait for a token
    if not _throttle_acquire(timeout=10):
        with _options_dates_lock:
            if symbol in _options_dates_cache:
                return _options_dates_cache[symbol]['dates']
        return []

    try:
        ticker = yf.Ticker(symbol)
        dates = list(ticker.options)
        _mark_global_rate_limit_success()
        with _options_dates_lock:
            _options_dates_cache[symbol] = {'dates': dates, 'ts': now}
        return dates
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('option-dates-error', symbol, f"[Cache] Error fetching option dates for {symbol}: {e}", cooldown=180)

    # --- v7 API fallback when yfinance fails ---
    v7_dates = _fetch_option_dates_v7(symbol)
    if v7_dates:
        with _options_dates_lock:
            _options_dates_cache[symbol] = {'dates': v7_dates, 'ts': now}
        return v7_dates

    # Serve stale cache if we have any
    with _options_dates_lock:
        if symbol in _options_dates_cache:
            return _options_dates_cache[symbol]['dates']
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
    else:
        # force_live: reuse very recent data to avoid redundant fetches
        with _chain_cache_lock:
            if cache_key in _chain_cache:
                entry = _chain_cache[cache_key]
                if (now - entry['ts']).total_seconds() < _FORCE_LIVE_MIN_TTL:
                    return entry['chain']

    # When globally rate-limited, serve stale cache but still attempt the call
    if _is_globally_rate_limited():
        with _chain_cache_lock:
            if cache_key in _chain_cache:
                return _chain_cache[cache_key]['chain']
        # Fall through to try yfinance anyway
    
    # Throttle: wait for a token
    if not _throttle_acquire(timeout=10):
        with _chain_cache_lock:
            if cache_key in _chain_cache:
                return _chain_cache[cache_key]['chain']
        return None

    try:
        ticker = yf.Ticker(symbol)
        chain = ticker.option_chain(expiry)
        _mark_global_rate_limit_success()
        with _chain_cache_lock:
            _chain_cache[cache_key] = {'chain': chain, 'ts': now}
        return chain
    except Exception as e:
        if _is_rate_limit_error(e):
            _mark_rate_limited(symbol)
        else:
            if not _is_expected_no_data_error(e):
                _log_fetch_event('option-chain-error', f"{symbol}:{expiry}", f"[Cache] Error fetching option chain for {symbol} {expiry}: {e}", cooldown=180)

    # --- v7 API fallback when yfinance fails ---
    v7_result = _fetch_option_chain_v7(symbol, expiry)
    if v7_result:
        from collections import namedtuple
        Chain = namedtuple('Chain', ['calls', 'puts'])
        chain = Chain(calls=v7_result[0], puts=v7_result[1])
        with _chain_cache_lock:
            _chain_cache[cache_key] = {'chain': chain, 'ts': now}
        return chain

    # Serve stale cache if we have it
    with _chain_cache_lock:
        if cache_key in _chain_cache:
            return _chain_cache[cache_key]['chain']
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
    
    # Throttle: wait for a token
    if not _throttle_acquire(timeout=10):
        with _ticker_info_lock:
            if symbol in _ticker_info_cache:
                return _ticker_info_cache[symbol]['info']
        return {}

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        _mark_global_rate_limit_success()
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
    global _global_rate_limit_until, _global_rate_limit_consecutive
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
        _global_rate_limit_consecutive = 0
    # Also reset scanner results cache
    for key in scanner_cache:
        scanner_cache[key] = {'data': None, 'timestamp': None, 'running': False}
    print("[Cache] All caches, scanner cache, and rate-limit blocks cleared")


def clear_rate_limit_blocks():
    """Clear only rate-limit guards without dropping data caches."""
    global _global_rate_limit_until, _global_rate_limit_consecutive
    with _history_rate_limit_lock:
        _history_rate_limit_block.clear()
    with _global_rate_limit_lock:
        _global_rate_limit_until = 0.0
        _global_rate_limit_consecutive = 0

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
        print(f"[BatchQuotes] ⚠️ Globally rate-limited — serving stale cache for {len(uncached)} symbols")
        for sym in uncached:
            if sym in quote_cache:
                cached_data, _ = quote_cache[sym]
                results[sym] = cached_data
        return results
    
    # 3. Throttle: one token for the batch download
    if not _throttle_acquire(timeout=30):
        print(f"[BatchQuotes] ⚠️ Throttle timeout (30s) — serving stale cache for {len(uncached)} symbols")
        for sym in uncached:
            if sym in quote_cache:
                results[sym] = quote_cache[sym][0]
        return results

    # 4. Single yf.download() batch call
    try:
        data = yf.download(
            uncached,
            period='5d',
            interval='1d',
            group_by='ticker',
            auto_adjust=True,
            threads=True,
            progress=False,
            timeout=60
        )
        
        if data is None or data.empty:
            print(f"[BatchQuotes] ⚠️ yf.download returned empty for {len(uncached)} symbols")
            # Serve stale cache as fallback
            for sym in uncached:
                if sym in quote_cache:
                    results[sym] = quote_cache[sym][0]
            # Ensure data is a safe empty DataFrame so processing blocks skip cleanly
            data = pd.DataFrame()
        
        fetch_time = datetime.now()
        
        if len(uncached) == 1:
            # Single symbol -> flat columns (Close, Open, etc.)
            sym = uncached[0]
            try:
                # Handle both MultiIndex and flat columns
                if isinstance(data.columns, pd.MultiIndex):
                    # Single ticker with MultiIndex: try data[sym] or data['Close'][sym]
                    try:
                        sym_close = data[sym]['Close'].dropna() if sym in data.columns.get_level_values(0) else data['Close'].dropna()
                    except (KeyError, TypeError):
                        sym_close = data['Close'].dropna() if 'Close' in data.columns.get_level_values(0) else pd.Series(dtype=float)
                    if len(sym_close) >= 2:
                        current_price = float(sym_close.iloc[-1])
                        prev_close = float(sym_close.iloc[-2])
                        change = current_price - prev_close
                        change_pct = (change / prev_close * 100) if prev_close > 0 else 0
                        vol_col = data[sym]['Volume'] if sym in data.columns.get_level_values(0) else data.get('Volume', pd.Series([0]))
                        quote = {
                            'symbol': sym,
                            'price': round(current_price, 2),
                            'change': round(change, 2),
                            'changePct': round(change_pct, 2),
                            'volume': int(vol_col.iloc[-1]) if len(vol_col) > 0 else 0,
                        }
                        results[sym] = quote
                        quote_cache[sym] = (quote, fetch_time)
                elif 'Close' in data.columns and len(data) >= 2:
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
            # Multiple symbols -> MultiIndex columns
            # yfinance may return (Ticker, Price) or (Price, Ticker) depending on group_by
            avail_symbols = []
            field_first = False  # True if columns are (Price, Ticker) instead of (Ticker, Price)
            if hasattr(data.columns, 'get_level_values'):
                try:
                    level0 = list(data.columns.get_level_values(0).unique())
                    level1 = list(data.columns.get_level_values(1).unique())
                    # Detect layout: if level 0 has field names like 'Close', it's (Price, Ticker)
                    price_fields = {'Close', 'Open', 'High', 'Low', 'Volume'}
                    if set(level0) & price_fields:
                        # Format: (Price, Ticker) — field-first
                        field_first = True
                        avail_symbols = level1
                    else:
                        # Format: (Ticker, Price) — ticker-first (group_by='ticker')
                        avail_symbols = level0
                except Exception:
                    avail_symbols = []
            
            for sym in uncached:
                try:
                    if sym not in avail_symbols:
                        continue
                    
                    if field_first:
                        # Access as data['Close'][sym], etc.
                        close_vals = data['Close'][sym].dropna() if 'Close' in data.columns.get_level_values(0) else pd.Series(dtype=float)
                        vol_vals = data['Volume'][sym] if 'Volume' in data.columns.get_level_values(0) else pd.Series([0])
                        high_vals = data['High'][sym] if 'High' in data.columns.get_level_values(0) else pd.Series([0])
                        low_vals = data['Low'][sym] if 'Low' in data.columns.get_level_values(0) else pd.Series([0])
                        open_vals = data['Open'][sym] if 'Open' in data.columns.get_level_values(0) else pd.Series([0])
                    else:
                        # Access as data[sym]['Close'], etc.
                        sym_data = data[sym]
                        if sym_data is None or sym_data.empty:
                            continue
                        close_vals = sym_data['Close'].dropna() if 'Close' in sym_data.columns else pd.Series(dtype=float)
                        vol_vals = sym_data['Volume'] if 'Volume' in sym_data.columns else pd.Series([0])
                        high_vals = sym_data['High'] if 'High' in sym_data.columns else pd.Series([0])
                        low_vals = sym_data['Low'] if 'Low' in sym_data.columns else pd.Series([0])
                        open_vals = sym_data['Open'] if 'Open' in sym_data.columns else pd.Series([0])
                    
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
                        'volume': int(vol_vals.iloc[-1]) if len(vol_vals) > 0 else 0,
                        'high': round(float(high_vals.iloc[-1]), 2) if len(high_vals) > 0 else 0,
                        'low': round(float(low_vals.iloc[-1]), 2) if len(low_vals) > 0 else 0,
                        'open': round(float(open_vals.iloc[-1]), 2) if len(open_vals) > 0 else 0,
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
    
    # 5. API fallback for symbols still missing (bypasses yfinance rate limiter)
    still_missing = [s for s in unique_symbols if s not in results]
    if still_missing:
        _log_fetch_event('batch-api-fallback', ','.join(still_missing[:5]),
                         f"Trying API fallback for {len(still_missing)} missing symbols", cooldown=60)
        api_prices = fetch_quote_api_batch(still_missing)
        api_time = datetime.now()
        for sym, price in api_prices.items():
            if price and price > 0:
                quote = {
                    'symbol': sym,
                    'price': round(price, 2),
                    'change': 0,
                    'changePct': 0,
                    'volume': 0,
                    'high': 0,
                    'low': 0,
                    'open': 0,
                }
                # Try to compute change from stale cache
                if sym in quote_cache:
                    old_quote, _ = quote_cache[sym]
                    old_price = old_quote.get('price', 0)
                    if old_price and old_price > 0:
                        quote['change'] = round(price - old_price, 2)
                        quote['changePct'] = round((price - old_price) / old_price * 100, 2)
                results[sym] = quote
                quote_cache[sym] = (quote, api_time)
        if api_prices:
            print(f"[BatchQuotes] ✅ API fallback recovered {len(api_prices)}/{len(still_missing)} symbols")

    return results

