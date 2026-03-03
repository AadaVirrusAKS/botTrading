import re
from zoneinfo import ZoneInfo
# =============================
# SYMBOL/NAME RESOLVER
# =============================
def resolve_symbol_or_name(query):
    """
    Accepts a string (symbol or company name), returns the ticker symbol if found, else returns the original string.
    Uses is_valid_symbol_cached() to avoid redundant API calls.
    """
    import yfinance as yf
    query = query.strip()
    # If it looks like a symbol (letters/numbers, short), try as symbol first
    candidate = query.upper() if re.match(r'^[A-Za-z0-9\.\-]{1,8}$', query) else None
    # Try as symbol using cached validator (avoids repeat API hits)
    if candidate and is_valid_symbol_cached(candidate):
        return candidate
    # Otherwise, try to resolve as company name
    try:
        import requests
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            quotes = data.get('quotes', [])
            # Try to find a quote with a matching name (case-insensitive, substring match)
            query_lower = query.lower()
            for item in quotes:
                name = item.get('shortname') or item.get('longname') or item.get('name') or ''
                symbol = item.get('symbol')
                if (
                    item.get('quoteType') == 'EQUITY' and symbol and name
                    and query_lower in name.lower() and is_valid_symbol_cached(symbol)
                ):
                    return symbol.upper()
            # Fallback: return first valid equity symbol
            for item in quotes:
                symbol = item.get('symbol')
                if item.get('quoteType') == 'EQUITY' and symbol and is_valid_symbol_cached(symbol):
                    return symbol.upper()
    except Exception as e:
        print(f"[resolve_symbol_or_name] Error resolving '{query}': {e}")
    return None
import threading

# =============================
# FLASK APP INITIALIZATION
# =============================
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import json
import os
from datetime import datetime
import threading
import time
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

# Use project-local yfinance cache to avoid global sqlite corruption/permission issues
YF_CACHE_DIR = os.path.join(os.getcwd(), '.yfinance_cache')
os.makedirs(YF_CACHE_DIR, exist_ok=True)
try:
    import yfinance.cache as _yf_cache_mod
    _yf_cache_mod.set_cache_location(YF_CACHE_DIR)
    yf.set_tz_cache_location(YF_CACHE_DIR)
except Exception as _yf_cache_err:
    print(f"⚠️ yfinance cache configuration warning: {_yf_cache_err}")

# =============================
# SYMBOL VALIDITY CACHE
# =============================
# Known delisted/invalid tickers - instantly rejected without API calls
KNOWN_DELISTED = {
    'BBBY',   # Bed Bath & Beyond - delisted 2023
    'CLOV',   # Clover Health - delisted
    'SPCE',   # Virgin Galactic - delisted/bankrupt
    'SKLZ',   # Skillz - delisted
    'WKHS',   # Workhorse - delisted
    'GOEV',   # Canoo - delisted/bankrupt
    'PSNY',   # Polestar - delisted
    'VFS',    # VinFast - delisted
    'BODY',   # Beachbody - delisted
    'GREE',   # Greenidge Generation - delisted
    'EXPR',   # Express - delisted/bankrupt
    'WISH',   # ContextLogic - delisted
    'SDC',    # SmileDirectClub - delisted/bankrupt
    'IRNT',   # IronNet - delisted/bankrupt
    'ATER',   # Aterian - delisted
    'BARK',   # BARK Inc - delisted
    'CLNE',   # Clean Energy Fuels - delisted
    'FCEL',   # FuelCell Energy - delisted
    'BLUE',   # Bluebird Bio - delisted
    'TLRY',   # Tilray - delisted
    'SNDL',   # SNDL Inc - delisted
    'ACB',    # Aurora Cannabis - delisted
    'OGI',    # Organigram - delisted
    'CRON',   # Cronos Group - delisted
    'CGC',    # Canopy Growth - delisted
    'PLUG',   # Plug Power - delisted
    'BLDP',   # Ballard Power - delisted
    'NVAX',   # Novavax - delisted
    'SAVA',   # Cassava Sciences - delisted
}

# Cache to avoid repeated API calls for invalid/delisted symbols
_INVALID_SYMBOLS_CACHE = set(KNOWN_DELISTED)  # Pre-populate with known delisted
_VALID_SYMBOLS_CACHE = set()
_SYMBOL_CACHE_LOCK = threading.Lock()

def is_valid_symbol_cached(symbol):
    """Fast symbol validation without network calls.

    Why: API-based validation can be rate-limited (Yahoo 429), which caused valid
    symbols (AAPL/MSFT/SPY) to be incorrectly cached as invalid and blocked scans.
    """
    symbol = str(symbol or '').upper().strip()
    if not symbol:
        return False

    with _SYMBOL_CACHE_LOCK:
        if symbol in _INVALID_SYMBOLS_CACHE:
            return False
        if symbol in _VALID_SYMBOLS_CACHE:
            return True

    # Basic format sanity check (allow letters, digits, dot and dash)
    if not re.fullmatch(r'[A-Z0-9.-]{1,10}', symbol):
        with _SYMBOL_CACHE_LOCK:
            _INVALID_SYMBOLS_CACHE.add(symbol)
        return False

    # Hard-block known delisted symbols only
    if symbol in KNOWN_DELISTED:
        with _SYMBOL_CACHE_LOCK:
            _INVALID_SYMBOLS_CACHE.add(symbol)
        return False

    with _SYMBOL_CACHE_LOCK:
        _VALID_SYMBOLS_CACHE.add(symbol)
    return True

def filter_valid_symbols(symbols):
    """Filter a list of symbols, removing known-delisted and invalid ones.
    Uses cache for instant rejection of known-bad symbols."""
    valid = []
    for s in symbols:
        s_upper = s.upper().strip()
        if s_upper not in KNOWN_DELISTED:
            valid.append(s)
    return valid

# ...existing code...

app = Flask(__name__)
app.config['SECRET_KEY'] = 'trading-dashboard-secret-2026'
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# =============================
# PAPER TRADING STATE HELPERS
# =============================
PAPER_TRADING_FILE = 'paper_trading_state.json'
PAPER_TRADING_LOCK = threading.Lock()
PAPER_TRADING_CAPITAL = 10000.0

def load_paper_state():
    if not os.path.exists(PAPER_TRADING_FILE):
        state = {"capital": PAPER_TRADING_CAPITAL, "balance": PAPER_TRADING_CAPITAL, "positions": [], "trade_log": []}
        with open(PAPER_TRADING_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        return state
    with open(PAPER_TRADING_FILE, 'r') as f:
        return json.load(f)

def save_paper_state(state):
    with open(PAPER_TRADING_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def reset_paper_state():
    state = {"capital": PAPER_TRADING_CAPITAL, "balance": PAPER_TRADING_CAPITAL, "positions": [], "trade_log": []}
    save_paper_state(state)
    return state

# =============================
# PAPER TRADING API ENDPOINTS
# =============================
@app.route('/api/paper/start', methods=['POST'])
def paper_start():
    with PAPER_TRADING_LOCK:
        state = reset_paper_state()
    return jsonify({'success': True, 'data': state})

@app.route('/api/paper/balance', methods=['GET'])
def paper_balance():
    with PAPER_TRADING_LOCK:
        state = load_paper_state()
    return jsonify({'success': True, 'data': {'balance': state['balance'], 'capital': state['capital']}})

@app.route('/api/paper/positions', methods=['GET'])
def paper_positions():
    with PAPER_TRADING_LOCK:
        state = load_paper_state()
    return jsonify({'success': True, 'data': state['positions']})

@app.route('/api/paper/trade', methods=['POST'])
def paper_trade():
    req = request.get_json(force=True)
    symbol = req.get('symbol')
    qty = float(req.get('qty', 0))
    price = float(req.get('price', 0))
    side = req.get('side', 'buy').lower()
    if not symbol or qty <= 0 or price <= 0 or side not in ['buy', 'sell']:
        return jsonify({'success': False, 'error': 'Invalid trade params'}), 400
    with PAPER_TRADING_LOCK:
        state = load_paper_state()
        if side == 'buy':
            cost = qty * price
            if cost > state['balance']:
                return jsonify({'success': False, 'error': 'Insufficient balance'}), 400
            # Add default stop loss and targets if missing
            # Use ATR if available, else fallback to entry price
            atr = req.get('atr')
            stop_loss = req.get('stop_loss')
            targets = req.get('targets')
            # Calculate default stop loss and targets
            if not stop_loss:
                if atr:
                    stop_loss = price - (float(atr) * 1.5)
                else:
                    stop_loss = price * 0.95  # 5% below entry
            if not targets:
                targets = [price * 1.1, price * 1.2, price * 1.3]  # 10%, 20%, 30% above entry
            pos = {
                'symbol': symbol,
                'qty': qty,
                'entry': price,
                'open_time': datetime.now().isoformat(),
                'stop_loss': float(stop_loss),
                'target_1': float(targets[0]),
                'target_2': float(targets[1]),
                'target_3': float(targets[2])
            }
            state['positions'].append(pos)
            state['balance'] -= cost
            state['trade_log'].append({'action': 'buy', 'symbol': symbol, 'qty': qty, 'price': price, 'time': datetime.now().isoformat()})
        elif side == 'sell':
            # Only allow closing existing position at a profit or breakeven
            for i, pos in enumerate(state['positions']):
                if pos['symbol'] == symbol and pos['qty'] == qty:
                    entry = pos['entry']
                    if price < entry:
                        return jsonify({'success': False, 'error': 'Cannot book a loss in paper trading'}), 400
                    profit = (price - entry) * qty
                    state['balance'] += profit
                    state['positions'].pop(i)
                    state['trade_log'].append({'action': 'sell', 'symbol': symbol, 'qty': qty, 'price': price, 'profit': profit, 'time': datetime.now().isoformat()})
                    break
            else:
                return jsonify({'success': False, 'error': 'No matching open position'}), 400
        save_paper_state(state)
    return jsonify({'success': True, 'data': state})

@app.route('/api/paper/close', methods=['POST'])
def paper_close():
    req = request.get_json(force=True)
    symbol = req.get('symbol')
    price = float(req.get('price', 0))
    if not symbol or price <= 0:
        return jsonify({'success': False, 'error': 'Invalid params'}), 400
    with PAPER_TRADING_LOCK:
        state = load_paper_state()
        for i, pos in enumerate(state['positions']):
            if pos['symbol'] == symbol:
                entry = pos['entry']
                qty = pos['qty']
                if price < entry:
                    return jsonify({'success': False, 'error': 'Cannot book a loss in paper trading'}), 400
                profit = (price - entry) * qty
                state['balance'] += profit
                state['positions'].pop(i)
                state['trade_log'].append({'action': 'close', 'symbol': symbol, 'qty': qty, 'price': price, 'profit': profit, 'time': datetime.now().isoformat()})
                save_paper_state(state)
                return jsonify({'success': True, 'data': state})
        return jsonify({'success': False, 'error': 'No matching open position'}), 400

@app.route('/api/paper/reset', methods=['POST'])
def paper_reset():
    with PAPER_TRADING_LOCK:
        state = reset_paper_state()
    return jsonify({'success': True, 'data': state})
#!/usr/bin/env python3
"""
US Market Trading Dashboard - Web Application
Similar to IntradayPulse.in but for USA markets

Provides real-time market data, scanners, sector analysis, and trade monitoring
via a modern web interface.
"""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import json
import os
from datetime import datetime
import threading
import time
import logging
import yfinance as yf
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed

# Suppress yfinance's noisy 'possibly delisted' warnings for valid symbols
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)

# Import existing trading modules
from unified_trading_system import UnifiedTradingSystem
from short_squeeze_scanner import ShortSqueezeScanner
from beaten_down_quality_scanner import BeatenDownQualityScanner
from next_day_options_predictor import NextDayOptionsPredictor

# Optional imports with graceful fallbacks
try:
    from weekly_screener_top100 import WeeklyStockScreener
except ImportError:
    WeeklyStockScreener = None
    print("⚠️  Weekly screener module not available")

try:
    from us_market_golden_cross_scanner import USMarketScanner
except ImportError:
    USMarketScanner = None
    print("⚠️  Golden cross scanner module not available")

try:
    from triple_confirmation_scanner import TripleConfirmationScanner
except ImportError:
    TripleConfirmationScanner = None
    print("⚠️  Triple confirmation scanner module not available")

try:
    from triple_confirmation_intraday import TripleConfirmationIntraday
except ImportError:
    TripleConfirmationIntraday = None
    print("⚠️  Triple confirmation intraday module not available")

try:
    from triple_confirmation_positional import TripleConfirmationPositional
except ImportError:
    TripleConfirmationPositional = None
    print("⚠️  Triple confirmation positional module not available")

# Import Autonomous Trading Agent
try:
    from autonomous_deepseek_trader import AutonomousTrader, DeepSeekAnalyzer, RiskManager
    AUTONOMOUS_AVAILABLE = True
except ImportError:
    AUTONOMOUS_AVAILABLE = False
    print("⚠️  Autonomous trading module not available")

# Note: custom_analyzer_methods.py contains UI methods, not a class
# Custom analysis is handled by UnifiedTradingSystem in /api/scanner/custom-analyzer endpoint

app = Flask(__name__)
app.config['SECRET_KEY'] = 'trading-dashboard-secret-2026'
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    if hasattr(e, 'code'):
        return jsonify({'success': False, 'error': str(e)}), e.code
    # Handle non-HTTP exceptions
    print(f"Unhandled exception: {e}")
    return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500

# =============================
# SYMBOL SUGGESTION ENDPOINT
# =============================
# Common stock name to symbol mapping for instant suggestions
COMMON_STOCKS = {
    'apple': {'symbol': 'AAPL', 'name': 'Apple Inc.'},
    'microsoft': {'symbol': 'MSFT', 'name': 'Microsoft Corporation'},
    'google': {'symbol': 'GOOGL', 'name': 'Alphabet Inc.'},
    'alphabet': {'symbol': 'GOOGL', 'name': 'Alphabet Inc.'},
    'amazon': {'symbol': 'AMZN', 'name': 'Amazon.com Inc.'},
    'meta': {'symbol': 'META', 'name': 'Meta Platforms Inc.'},
    'facebook': {'symbol': 'META', 'name': 'Meta Platforms Inc.'},
    'tesla': {'symbol': 'TSLA', 'name': 'Tesla Inc.'},
    'nvidia': {'symbol': 'NVDA', 'name': 'NVIDIA Corporation'},
    'netflix': {'symbol': 'NFLX', 'name': 'Netflix Inc.'},
    'amd': {'symbol': 'AMD', 'name': 'Advanced Micro Devices'},
    'intel': {'symbol': 'INTC', 'name': 'Intel Corporation'},
    'disney': {'symbol': 'DIS', 'name': 'Walt Disney Co.'},
    'nike': {'symbol': 'NKE', 'name': 'Nike Inc.'},
    'walmart': {'symbol': 'WMT', 'name': 'Walmart Inc.'},
    'costco': {'symbol': 'COST', 'name': 'Costco Wholesale'},
    'visa': {'symbol': 'V', 'name': 'Visa Inc.'},
    'mastercard': {'symbol': 'MA', 'name': 'Mastercard Inc.'},
    'paypal': {'symbol': 'PYPL', 'name': 'PayPal Holdings'},
    'jpmorgan': {'symbol': 'JPM', 'name': 'JPMorgan Chase & Co.'},
    'chase': {'symbol': 'JPM', 'name': 'JPMorgan Chase & Co.'},
    'goldman': {'symbol': 'GS', 'name': 'Goldman Sachs Group'},
    'boeing': {'symbol': 'BA', 'name': 'Boeing Co.'},
    'coca-cola': {'symbol': 'KO', 'name': 'Coca-Cola Co.'},
    'coke': {'symbol': 'KO', 'name': 'Coca-Cola Co.'},
    'pepsi': {'symbol': 'PEP', 'name': 'PepsiCo Inc.'},
    'starbucks': {'symbol': 'SBUX', 'name': 'Starbucks Corp.'},
    'mcdonalds': {'symbol': 'MCD', 'name': "McDonald's Corp."},
    'uber': {'symbol': 'UBER', 'name': 'Uber Technologies'},
    'airbnb': {'symbol': 'ABNB', 'name': 'Airbnb Inc.'},
    'spotify': {'symbol': 'SPOT', 'name': 'Spotify Technology'},
    'zoom': {'symbol': 'ZM', 'name': 'Zoom Video Communications'},
    'salesforce': {'symbol': 'CRM', 'name': 'Salesforce Inc.'},
    'oracle': {'symbol': 'ORCL', 'name': 'Oracle Corporation'},
    'ibm': {'symbol': 'IBM', 'name': 'IBM Corporation'},
    'cisco': {'symbol': 'CSCO', 'name': 'Cisco Systems'},
    'adobe': {'symbol': 'ADBE', 'name': 'Adobe Inc.'},
    'broadcom': {'symbol': 'AVGO', 'name': 'Broadcom Inc.'},
    'qualcomm': {'symbol': 'QCOM', 'name': 'Qualcomm Inc.'},
    'micron': {'symbol': 'MU', 'name': 'Micron Technology'},
    'palantir': {'symbol': 'PLTR', 'name': 'Palantir Technologies'},
    'snowflake': {'symbol': 'SNOW', 'name': 'Snowflake Inc.'},
    'shopify': {'symbol': 'SHOP', 'name': 'Shopify Inc.'},
    'square': {'symbol': 'XYZ', 'name': 'Block Inc.'},
    'block': {'symbol': 'XYZ', 'name': 'Block Inc.'},
    'coinbase': {'symbol': 'COIN', 'name': 'Coinbase Global'},
    'robinhood': {'symbol': 'HOOD', 'name': 'Robinhood Markets'},
    'gamestop': {'symbol': 'GME', 'name': 'GameStop Corp.'},
    'amc': {'symbol': 'AMC', 'name': 'AMC Entertainment'},
    'berkshire': {'symbol': 'BRK-B', 'name': 'Berkshire Hathaway'},
    'johnson': {'symbol': 'JNJ', 'name': 'Johnson & Johnson'},
    'pfizer': {'symbol': 'PFE', 'name': 'Pfizer Inc.'},
    'moderna': {'symbol': 'MRNA', 'name': 'Moderna Inc.'},
    'unitedhealth': {'symbol': 'UNH', 'name': 'UnitedHealth Group'},
    'exxon': {'symbol': 'XOM', 'name': 'Exxon Mobil Corp.'},
    'chevron': {'symbol': 'CVX', 'name': 'Chevron Corporation'},
    'att': {'symbol': 'T', 'name': 'AT&T Inc.'},
    'verizon': {'symbol': 'VZ', 'name': 'Verizon Communications'},
    'comcast': {'symbol': 'CMCSA', 'name': 'Comcast Corporation'},
    'target': {'symbol': 'TGT', 'name': 'Target Corporation'},
    'home depot': {'symbol': 'HD', 'name': 'Home Depot Inc.'},
    'lowes': {'symbol': 'LOW', 'name': "Lowe's Companies"},
    'ford': {'symbol': 'F', 'name': 'Ford Motor Co.'},
    'gm': {'symbol': 'GM', 'name': 'General Motors Co.'},
    'rivian': {'symbol': 'RIVN', 'name': 'Rivian Automotive'},
    'lucid': {'symbol': 'LCID', 'name': 'Lucid Group'},
    'nio': {'symbol': 'NIO', 'name': 'NIO Inc.'},
    'spy': {'symbol': 'SPY', 'name': 'SPDR S&P 500 ETF'},
    'qqq': {'symbol': 'QQQ', 'name': 'Invesco QQQ Trust'},
    'dia': {'symbol': 'DIA', 'name': 'SPDR Dow Jones ETF'},
    'iwm': {'symbol': 'IWM', 'name': 'iShares Russell 2000 ETF'},
    'voo': {'symbol': 'VOO', 'name': 'Vanguard S&P 500 ETF'},
}

@app.route('/api/symbol/suggest')
def symbol_suggest():
    """
    Suggest ticker symbols for a partial company name or symbol.
    Returns a list of {symbol, name, exchange, type} dicts.
    """
    import requests as req_lib
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify({'success': False, 'error': 'No query provided', 'suggestions': []}), 400
    
    suggestions = []
    seen_symbols = set()
    
    # First, check common stocks mapping for instant results
    for name_key, stock_info in COMMON_STOCKS.items():
        if query in name_key or query in stock_info['symbol'].lower():
            if stock_info['symbol'] not in seen_symbols:
                suggestions.append({
                    'symbol': stock_info['symbol'],
                    'name': stock_info['name'],
                    'exchange': 'NASDAQ/NYSE',
                    'type': 'EQUITY'
                })
                seen_symbols.add(stock_info['symbol'])
    
    # If we have enough suggestions from common stocks, return them
    if len(suggestions) >= 5:
        return jsonify({'success': True, 'suggestions': suggestions[:10]})
    
    # Try Yahoo Finance API as fallback
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=10&newsCount=0"
        resp = req_lib.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('quotes', []):
                if item.get('quoteType') == 'EQUITY' and 'symbol' in item:
                    symbol = item['symbol']
                    if symbol not in seen_symbols:
                        suggestions.append({
                            'symbol': symbol,
                            'name': item.get('shortname') or item.get('longname') or item.get('name') or '',
                            'exchange': item.get('exchange', ''),
                            'type': item.get('quoteType', '')
                        })
                        seen_symbols.add(symbol)
    except Exception as e:
        print(f"[symbol_suggest] Yahoo API error: {e}")
    
    # If still no suggestions, try to validate as direct symbol
    if not suggestions and len(query) <= 6:
        try:
            ticker = yf.Ticker(query.upper())
            info = ticker.info
            if info and info.get('regularMarketPrice'):
                suggestions.append({
                    'symbol': query.upper(),
                    'name': info.get('shortName', info.get('longName', query.upper())),
                    'exchange': info.get('exchange', ''),
                    'type': 'EQUITY'
                })
        except Exception:
            pass
    
    return jsonify({'success': True, 'suggestions': suggestions[:10]})

# Cache for reducing API calls
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

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def clean_nan_values(data):
    """Convert NaN/inf values to None and numpy scalars to native Python types for valid JSON serialization."""
    if isinstance(data, dict):
        return {k: clean_nan_values(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_nan_values(item) for item in data]
    elif isinstance(data, np.bool_):
        return bool(data)
    elif isinstance(data, np.integer):
        return int(data)
    elif isinstance(data, np.floating):
        if np.isnan(data) or np.isinf(data):
            return None
        return float(data)
    elif isinstance(data, np.ndarray):
        return [clean_nan_values(x) for x in data.tolist()]
    elif isinstance(data, float):
        if np.isnan(data) or np.isinf(data):
            return None
        return data
    return data

# ============================================================================
# MARKET DATA & UTILITIES
# ============================================================================

MAJOR_INDICES = {
    '^GSPC': 'S&P 500',
    '^DJI': 'Dow Jones',
    '^IXIC': 'NASDAQ',
    '^RUT': 'Russell 2000',
    '^VIX': 'VIX'
}

SECTOR_ETFS = {
    'XLK': 'Technology',
    'XLF': 'Financials',
    'XLV': 'Healthcare',
    'XLE': 'Energy',
    'XLI': 'Industrials',
    'XLP': 'Consumer Staples',
    'XLY': 'Consumer Discretionary',
    'XLB': 'Materials',
    'XLRE': 'Real Estate',
    'XLC': 'Communication',
    'XLU': 'Utilities'
}

# Top stocks by sector (major holdings from each sector ETF)
SECTOR_STOCKS = {
    'XLK': ['AAPL', 'MSFT', 'NVDA', 'AVGO', 'ORCL', 'CRM', 'CSCO', 'ACN', 'ADBE', 'AMD', 'INTC', 'TXN', 'QCOM', 'IBM', 'AMAT'],
    'XLF': ['BRK-B', 'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'SPGI', 'BLK', 'AXP', 'C', 'SCHW', 'PGR', 'MMC'],
    'XLV': ['LLY', 'UNH', 'JNJ', 'ABBV', 'MRK', 'TMO', 'ABT', 'PFE', 'AMGN', 'DHR', 'BMY', 'MDT', 'ISRG', 'CVS', 'GILD'],
    'XLE': ['XOM', 'CVX', 'COP', 'EOG', 'SLB', 'MPC', 'PSX', 'VLO', 'DINO', 'OXY', 'WMB', 'HAL', 'DVN', 'TRGP', 'KMI'],
    'XLI': ['GE', 'CAT', 'UNP', 'HON', 'RTX', 'BA', 'DE', 'UPS', 'LMT', 'MMM', 'GD', 'NOC', 'FDX', 'CSX', 'NSC'],
    'XLP': ['PG', 'COST', 'KO', 'PEP', 'WMT', 'PM', 'MDLZ', 'MO', 'CL', 'KMB', 'GIS', 'SYY', 'STZ', 'KHC', 'HSY'],
    'XLY': ['AMZN', 'TSLA', 'HD', 'MCD', 'NKE', 'LOW', 'BKNG', 'SBUX', 'TJX', 'CMG', 'MAR', 'GM', 'F', 'ORLY', 'ROST'],
    'XLB': ['LIN', 'SHW', 'APD', 'FCX', 'ECL', 'NEM', 'DOW', 'DD', 'NUE', 'VMC', 'MLM', 'CTVA', 'PPG', 'ALB', 'IFF'],
    'XLRE': ['PLD', 'AMT', 'EQIX', 'CCI', 'PSA', 'O', 'WELL', 'SPG', 'DLR', 'VICI', 'AVB', 'EQR', 'VTR', 'ARE', 'ESS'],
    'XLC': ['META', 'GOOGL', 'GOOG', 'NFLX', 'DIS', 'CMCSA', 'VZ', 'T', 'CHTR', 'TMUS', 'EA', 'TTWO', 'WBD', 'OMC', 'LYV'],
    'XLU': ['NEE', 'SO', 'DUK', 'CEG', 'SRE', 'AEP', 'D', 'PCG', 'EXC', 'XEL', 'ED', 'PEG', 'WEC', 'ES', 'AWK']
}

# US Stock Market Holidays for 2025-2027
US_MARKET_HOLIDAYS = {
    # 2025
    (2025, 1, 1): "New Year's Day",
    (2025, 1, 20): "Martin Luther King Jr. Day",
    (2025, 2, 17): "Presidents Day",
    (2025, 4, 18): "Good Friday",
    (2025, 5, 26): "Memorial Day",
    (2025, 6, 19): "Juneteenth",
    (2025, 7, 4): "Independence Day",
    (2025, 9, 1): "Labor Day",
    (2025, 11, 27): "Thanksgiving Day",
    (2025, 12, 25): "Christmas Day",
    # 2026
    (2026, 1, 1): "New Year's Day",
    (2026, 1, 19): "Martin Luther King Jr. Day",
    (2026, 2, 16): "Presidents Day",
    (2026, 4, 3): "Good Friday",
    (2026, 5, 25): "Memorial Day",
    (2026, 6, 19): "Juneteenth",
    (2026, 7, 3): "Independence Day (Observed)",
    (2026, 9, 7): "Labor Day",
    (2026, 11, 26): "Thanksgiving Day",
    (2026, 12, 25): "Christmas Day",
    # 2027
    (2027, 1, 1): "New Year's Day",
    (2027, 1, 18): "Martin Luther King Jr. Day",
    (2027, 2, 15): "Presidents Day",
    (2027, 3, 26): "Good Friday",
    (2027, 5, 31): "Memorial Day",
    (2027, 6, 18): "Juneteenth (Observed)",
    (2027, 7, 5): "Independence Day (Observed)",
    (2027, 9, 6): "Labor Day",
    (2027, 11, 25): "Thanksgiving Day",
    (2027, 12, 24): "Christmas Day (Observed)",
}

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

# ============================================================================
# FLASK ROUTES - PAGES
# ============================================================================

@app.route('/api/health')
def api_health():
    """Health check endpoint for frontend reconnection detection."""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'rate_limited': _is_globally_rate_limited()
    })


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')

@app.route('/test-debug')
def test_debug():
    """Debug test page"""
    return render_template('test_debug.html')

@app.route('/scanners')
def scanners_page():
    """Scanner results page"""
    return render_template('scanners.html')

@app.route('/options')
def options_page():
    """Options analysis page"""
    return render_template('options.html')

@app.route('/monitoring')
def monitoring_page():
    """Live position monitoring page"""
    return render_template('monitoring.html')

@app.route('/technical-analysis')
def technical_analysis_page():
    """Advanced technical analysis page with TradingView-like indicators"""
    return render_template('technical_analysis.html')

@app.route('/test-chart')
def test_chart():
    """Simple chart test page"""
    return render_template('test_chart_simple.html')

@app.route('/diagnostic')
def diagnostic():
    """Chart diagnostic page"""
    return render_template('chart_diagnostic.html')

@app.route('/autonomous')
def autonomous_page():
    """Autonomous AI Trading page"""
    return render_template('autonomous.html')

@app.route('/ai-trading')
def ai_trading_page():
    """AI Trading Bot page with Demo/Real account modes"""
    return render_template('ai_trading.html')

@app.route('/crypto')
def crypto_page():
    """Crypto Dashboard page"""
    return render_template('crypto.html')

@app.route('/ai-analysis')
def ai_analysis_page():
    """AI Stock Analysis - Candlestick pattern recognition & price prediction"""
    return render_template('ai_analysis.html')

# ============================================================================
# AI STOCK ANALYSIS API ENDPOINTS
# ============================================================================

@app.route('/api/ai/analyze', methods=['POST'])
def ai_analyze_stock():
    """Run full AI analysis on a stock symbol."""
    try:
        from ai_stock_analysis import run_ai_analysis
        data = request.get_json() or {}
        symbol = data.get('symbol', '').strip()
        period = data.get('period', '6mo')
        horizon = data.get('horizon', 5)
        allow_fallback = bool(data.get('allow_fallback', False))
        force_live = bool(data.get('force_live', True))

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        # Resolve symbol or name
        resolved = resolve_symbol_or_name(symbol)
        if not resolved:
            return jsonify({'success': False, 'error': f'Could not resolve symbol: {symbol}'}), 400

        # Live-first mode (default): let analysis engine fetch live data directly.
        # Cache-prefetch mode remains available when force_live=False.
        if force_live:
            df = None
            info = None
        else:
            df = cached_get_history(resolved, period=period, interval='1d')
            info = cached_get_ticker_info(resolved)

        result = run_ai_analysis(resolved, period=period, prediction_horizon=horizon, df=df, info=info)
        if not result or not isinstance(result, dict) or result.get('error'):
            error_message = (result or {}).get('error') if isinstance(result, dict) else 'No analysis output'
            if allow_fallback:
                fallback = build_ai_analysis_fallback(resolved, period=period, horizon=horizon, error_message=error_message)
                fallback['success'] = True
                fallback['data_source'] = 'fallback'
                return jsonify(clean_nan_values(fallback))
            return jsonify({
                'success': False,
                'error': f'Live analysis unavailable for {resolved}: {error_message}'
            }), 503

        result['success'] = True
        result['data_source'] = 'live'
        return jsonify(clean_nan_values(result))

    except Exception as e:
        try:
            data = request.get_json() or {}
            allow_fallback = bool(data.get('allow_fallback', False))
            symbol = (data.get('symbol') or '').strip().upper() or 'SPY'
            period = data.get('period', '6mo')
            horizon = int(data.get('horizon', 5) or 5)
            if allow_fallback:
                fallback = build_ai_analysis_fallback(symbol, period=period, horizon=horizon, error_message=str(e))
                fallback['success'] = True
                fallback['data_source'] = 'fallback'
                return jsonify(clean_nan_values(fallback))
        except Exception:
            pass
        print(f"Error in ai_analyze_stock: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def build_ai_analysis_fallback(symbol: str, period: str = '6mo', horizon: int = 5, error_message: str = ''):
    """Fallback AI analysis payload used when live provider is rate-limited.

    Keeps AI Analysis page operational with best-effort local data.
    """
    symbol = (symbol or 'SPY').upper()
    horizon = max(1, min(int(horizon or 5), 30))

    # Base price from local top picks when available
    base_price = 100.0
    company_name = symbol
    try:
        if os.path.exists('top_picks.json'):
            with open('top_picks.json', 'r') as f:
                tp = json.load(f) or {}
            for row in (tp.get('stocks', []) + tp.get('etfs', []) + tp.get('options', [])):
                if str(row.get('ticker', '')).upper() == symbol:
                    md = row.get('data') or {}
                    base_price = float(md.get('price') or base_price)
                    company_name = md.get('sector', symbol) or symbol
                    break
    except Exception:
        pass

    # Build deterministic lightweight forecast rails
    drift = 0.003  # +0.3%/day conservative drift
    ensemble = [round(base_price * (1 + drift * (i + 1)), 2) for i in range(horizon)]
    lin_reg = [round(base_price * (1 + (drift + 0.001) * (i + 1)), 2) for i in range(horizon)]
    exp_smooth = [round(base_price * (1 + (drift - 0.0005) * (i + 1)), 2) for i in range(horizon)]
    mean_rev = [round(base_price * (1 + (drift * 0.6) * (i + 1)), 2) for i in range(horizon)]
    momentum = [round(base_price * (1 + (drift + 0.0015) * (i + 1)), 2) for i in range(horizon)]
    upper_95 = [round(p * 1.02, 2) for p in ensemble]
    lower_95 = [round(p * 0.98, 2) for p in ensemble]
    predicted_change_pct = ((ensemble[-1] - base_price) / base_price * 100) if base_price > 0 else 0

    direction = 'BUY' if predicted_change_pct >= 0 else 'SELL'
    confidence = 58.0 if error_message else 62.0

    return {
        'symbol': symbol,
        'company_name': company_name,
        'current_price': base_price,
        'ai_signal': {
            'signal': direction,
            'confidence': confidence,
            'score': confidence,
            'components': {
                'pattern_score': 0.0,
                'prediction_score': 1.5 if direction == 'BUY' else -1.5,
                'trend_score': 0.5 if direction == 'BUY' else -0.5,
            },
            'reasons': [
                'Using cached/local fallback data (live feed rate-limited)',
                f'{horizon}-day projection available',
            ],
            'warnings': [error_message] if error_message else []
        },
        'patterns': {
            'summary': {'total': 0, 'bullish': 0, 'bearish': 0, 'neutral': 0, 'avg_reliability': 0, 'dominant': 'neutral'},
            'recent': [],
            'overlays': [],
            'next_candle': {
                'formation_color': 'neutral',
                'context': 'Fallback mode',
                'formation': 'No reliable live pattern',
                'formation_prob': 50,
                'expected_move_pct': round(predicted_change_pct / max(horizon, 1), 2),
                'expected_range_pct': 1.0,
                'rsi': 50,
                'vol_trend': 'neutral',
                'buyer_pct': 50,
                'seller_pct': 50,
                'bull_prob': 50,
                'bear_prob': 50,
                'signals': {'momentum_bull': 50, 'volume_buying': 50, 'rsi': 50, 'pattern_bias': 50},
                'shape': {'upper_wick': 25, 'body': 50, 'lower_wick': 25, 'is_bullish': predicted_change_pct >= 0},
            }
        },
        'predictions': {
            'horizon': horizon,
            'current_price': base_price,
            'predicted_change_pct': round(predicted_change_pct, 2),
            'predictions': {
                'ensemble': ensemble,
                'linear_regression': lin_reg,
                'exponential_smoothing': exp_smooth,
                'mean_reversion': mean_rev,
                'momentum': momentum,
            },
            'confidence': {'upper_95': upper_95, 'lower_95': lower_95},
            'model_weights': {
                'linear_regression': 0.25,
                'exponential_smoothing': 0.25,
                'mean_reversion': 0.25,
                'momentum': 0.25,
            },
            'targets': {
                'take_profit_1': round(base_price * 1.01, 2),
                'take_profit_2': round(base_price * 1.02, 2),
                'take_profit_3': round(base_price * 1.03, 2),
                'stop_loss': round(base_price * 0.98, 2),
                'resistance': round(base_price * 1.015, 2),
                'support': round(base_price * 0.985, 2),
                'bullish_target': round(base_price * 1.03, 2),
                'bearish_target': round(base_price * 0.97, 2),
            }
        },
        'trend_analysis': {
            'short_term': {'direction': 'bullish' if predicted_change_pct >= 0 else 'bearish', 'strength': 55, 'change_pct': round(predicted_change_pct / max(horizon, 1), 2)},
            'medium_term': {'direction': 'neutral', 'strength': 50, 'change_pct': round(predicted_change_pct / 2, 2)},
            'long_term': {'direction': 'neutral', 'strength': 48, 'change_pct': round(predicted_change_pct, 2)},
            'volume_trend': {'status': 'normal', 'volume_ratio': 1.0, 'avg_recent_volume': 0},
            'trend_strength': {'label': 'Moderate', 'score': 55, 'consistency': 52, 'direction': 'up' if predicted_change_pct >= 0 else 'down'},
            'support_resistance': {
                'supports': [round(base_price * 0.98, 2), round(base_price * 0.96, 2)],
                'resistances': [round(base_price * 1.02, 2), round(base_price * 1.04, 2)],
            },
            'volatility_regime': {'regime': 'normal', 'percentile': 50, 'current_vol': 0, 'avg_vol': 0},
        },
        'metadata': {
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_period': period,
            'bars_analyzed': 0,
            'prediction_horizon': horizon,
        }
    }


@app.route('/api/ai/heatmap', methods=['POST'])
def ai_heatmap():
    """Run heatmap analysis across multiple stocks."""
    try:
        from ai_stock_analysis import run_heatmap_analysis
        data = request.get_json() or {}
        symbols = data.get('symbols', None)

        result = run_heatmap_analysis(symbols)
        result['success'] = True
        return jsonify(clean_nan_values(result))

    except Exception as e:
        print(f"Error in ai_heatmap: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/patterns', methods=['POST'])
def ai_patterns_only():
    """Get just candlestick patterns for a symbol (lightweight endpoint)."""
    try:
        from ai_stock_analysis import CandlestickPatternEngine
        data = request.get_json()
        symbol = data.get('symbol', '').strip().upper()
        lookback = data.get('lookback', 50)

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        df = cached_get_history(symbol, period='6mo', interval='1d')

        if df is None or df.empty or len(df) < 10:
            return jsonify({'success': False, 'error': f'No data for {symbol}'}), 400

        engine = CandlestickPatternEngine()
        patterns = engine.detect_all_patterns(df, lookback=lookback)
        summary = engine.get_pattern_summary(patterns)
        recent = engine.get_recent_patterns(patterns)

        return jsonify(clean_nan_values({
            'success': True,
            'symbol': symbol,
            'patterns': recent,
            'summary': summary,
            'total_detected': len(patterns),
        }))

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/ai/predict', methods=['POST'])
def ai_predict_only():
    """Get just price predictions for a symbol (lightweight endpoint)."""
    try:
        from ai_stock_analysis import PricePredictionEngine
        data = request.get_json()
        symbol = data.get('symbol', '').strip().upper()
        horizon = data.get('horizon', 5)

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        df = cached_get_history(symbol, period='6mo', interval='1d')

        if df is None or df.empty or len(df) < 30:
            return jsonify({'success': False, 'error': f'Insufficient data for {symbol}'}), 400

        engine = PricePredictionEngine()
        predictions = engine.predict(df, horizon=horizon)

        return jsonify(clean_nan_values({
            'success': True,
            'symbol': symbol,
            'predictions': predictions,
        }))

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# CRYPTO API ENDPOINTS
# ============================================================================

# Top crypto symbols (Yahoo Finance format: add -USD suffix)
CRYPTO_SYMBOLS = [
    'BTC-USD', 'ETH-USD', 'USDT-USD', 'BNB-USD', 'SOL-USD',
    'XRP-USD', 'USDC-USD', 'STETH-USD', 'ADA-USD', 'DOGE-USD',
    'AVAX-USD', 'TRX-USD', 'LINK-USD', 'TON11419-USD', 'SHIB-USD',
    'DOT-USD', 'BCH-USD', 'NEAR-USD', 'MATIC-USD', 'LTC-USD',
    'ICP-USD', 'UNI7083-USD', 'LEO-USD', 'PEPE24478-USD', 'DAI-USD',
    'APT21794-USD', 'ETC-USD', 'RNDR-USD', 'HBAR-USD', 'MNT27075-USD',
    'ATOM-USD', 'FIL-USD', 'CRO-USD', 'IMX10603-USD', 'STX4847-USD',
    'OKB-USD', 'XLM-USD', 'VET-USD', 'OP-USD', 'ARB11841-USD',
    'MKR-USD', 'GRT6719-USD', 'INJ-USD', 'SUI20947-USD', 'THETA-USD',
    'RUNE-USD', 'FTM-USD', 'ALGO-USD', 'AAVE-USD', 'FLOW-USD'
]

# Crypto name mappings
CRYPTO_NAMES = {
    'BTC-USD': ('Bitcoin', '₿'),
    'ETH-USD': ('Ethereum', 'Ξ'),
    'USDT-USD': ('Tether', '💵'),
    'BNB-USD': ('BNB', '🔶'),
    'SOL-USD': ('Solana', '☀️'),
    'XRP-USD': ('XRP', '💧'),
    'USDC-USD': ('USD Coin', '💲'),
    'STETH-USD': ('Lido Staked ETH', '🔷'),
    'ADA-USD': ('Cardano', '🔵'),
    'DOGE-USD': ('Dogecoin', '🐕'),
    'AVAX-USD': ('Avalanche', '🔺'),
    'TRX-USD': ('TRON', '🔴'),
    'LINK-USD': ('Chainlink', '🔗'),
    'TON11419-USD': ('Toncoin', '💎'),
    'SHIB-USD': ('Shiba Inu', '🐕'),
    'DOT-USD': ('Polkadot', '⚫'),
    'BCH-USD': ('Bitcoin Cash', '💰'),
    'NEAR-USD': ('NEAR Protocol', '🌐'),
    'MATIC-USD': ('Polygon', '🟣'),
    'LTC-USD': ('Litecoin', '🥈'),
    'ICP-USD': ('Internet Computer', '♾️'),
    'UNI7083-USD': ('Uniswap', '🦄'),
    'LEO-USD': ('UNUS SED LEO', '🦁'),
    'PEPE24478-USD': ('Pepe', '🐸'),
    'DAI-USD': ('Dai', '🌞'),
    'APT21794-USD': ('Aptos', '🅰️'),
    'ETC-USD': ('Ethereum Classic', '💎'),
    'RNDR-USD': ('Render', '🎨'),
    'HBAR-USD': ('Hedera', '♓'),
    'MNT27075-USD': ('Mantle', '🏔️'),
    'ATOM-USD': ('Cosmos', '⚛️'),
    'FIL-USD': ('Filecoin', '📁'),
    'CRO-USD': ('Cronos', '🔵'),
    'IMX10603-USD': ('Immutable', '🎮'),
    'STX4847-USD': ('Stacks', '📚'),
    'OKB-USD': ('OKB', '🟢'),
    'XLM-USD': ('Stellar', '⭐'),
    'VET-USD': ('VeChain', '✅'),
    'OP-USD': ('Optimism', '🔴'),
    'ARB11841-USD': ('Arbitrum', '🔵'),
    'MKR-USD': ('Maker', '🏛️'),
    'GRT6719-USD': ('The Graph', '📊'),
    'INJ-USD': ('Injective', '💉'),
    'SUI20947-USD': ('Sui', '🌊'),
    'THETA-USD': ('Theta Network', '🎬'),
    'RUNE-USD': ('THORChain', '⚡'),
    'FTM-USD': ('Fantom', '👻'),
    'ALGO-USD': ('Algorand', '🔷'),
    'AAVE-USD': ('Aave', '👻'),
    'FLOW-USD': ('Flow', '🌀')
}


def _get_cached_info_only(symbol):
    """Return cached ticker info without making upstream network requests."""
    now = datetime.now()
    with _ticker_info_lock:
        entry = _ticker_info_cache.get(symbol)
        if not entry:
            return {}
        if (now - entry['ts']).total_seconds() < _ticker_info_ttl:
            return entry.get('info', {}) or {}
    return {}


def _get_crypto_data_batch(symbols):
    """Get crypto quote rows via single batch quote call plus cached-only metadata."""
    quotes_map = _fetch_all_quotes_batch(symbols)
    cryptos = []

    for symbol in symbols:
        quote = quotes_map.get(symbol)
        if not quote:
            continue

        info = _get_cached_info_only(symbol)
        name, icon = CRYPTO_NAMES.get(symbol, (symbol.replace('-USD', ''), '🪙'))

        cryptos.append({
            'symbol': symbol,
            'name': name,
            'icon': icon,
            'price': float(quote.get('price', 0) or 0),
            'change_pct': float(quote.get('changePct', 0) or 0),
            'market_cap': info.get('marketCap', 0) if isinstance(info, dict) else 0,
            'volume': int((quote.get('volume', 0) or (info.get('volume24Hr', 0) if isinstance(info, dict) else 0) or (info.get('volume', 0) if isinstance(info, dict) else 0) or 0)),
            'high_24h': float(quote.get('high', 0) or (info.get('dayHigh', 0) if isinstance(info, dict) else 0) or 0),
            'low_24h': float(quote.get('low', 0) or (info.get('dayLow', 0) if isinstance(info, dict) else 0) or 0),
        })

    return cryptos

def get_crypto_data(symbol):
    """Get crypto data from yfinance"""
    try:
        info = cached_get_ticker_info(symbol)
        hist = cached_get_history(symbol, period='2d', interval='1d')
        
        if hist is None or hist.empty:
            return None
        
        current_price = hist['Close'].iloc[-1] if not hist.empty else 0
        prev_close = hist['Close'].iloc[-2] if len(hist) >= 2 else current_price
        
        change_pct = ((current_price - prev_close) / prev_close * 100) if prev_close > 0 else 0
        
        name_tuple = CRYPTO_NAMES.get(symbol, (symbol.replace('-USD', ''), '🪙'))
        
        return {
            'symbol': symbol,
            'name': name_tuple[0],
            'icon': name_tuple[1],
            'price': float(current_price),
            'change_pct': float(change_pct),
            'market_cap': info.get('marketCap', 0),
            'volume': info.get('volume24Hr', 0) or info.get('volume', 0),
            'high_24h': info.get('dayHigh', 0) or float(hist['High'].iloc[-1]) if not hist.empty else 0,
            'low_24h': info.get('dayLow', 0) or float(hist['Low'].iloc[-1]) if not hist.empty else 0,
        }
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

@app.route('/api/crypto/all')
def get_all_cryptos():
    """Get all top cryptocurrencies"""
    try:
        cryptos = _get_crypto_data_batch(CRYPTO_SYMBOLS[:50])
        total_market_cap = 0
        total_volume = 0
        btc_market_cap = 0

        for data in cryptos:
            total_market_cap += data.get('market_cap', 0) or 0
            total_volume += data.get('volume', 0) or 0
            if data['symbol'] == 'BTC-USD':
                btc_market_cap = data.get('market_cap', 0) or 0
        
        # Sort by market cap
        cryptos.sort(key=lambda x: x.get('market_cap', 0) or 0, reverse=True)
        
        btc_dominance = (btc_market_cap / total_market_cap * 100) if total_market_cap > 0 else 0
        
        return jsonify({
            'success': True,
            'cryptos': cryptos,
            'market_stats': {
                'total_market_cap': total_market_cap,
                'total_volume': total_volume,
                'btc_dominance': btc_dominance
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crypto/movers/<direction>')
def get_crypto_movers(direction):
    """Get top gainers or losers"""
    try:
        cryptos = _get_crypto_data_batch(CRYPTO_SYMBOLS[:50])
        
        # Sort by change percentage
        if direction == 'gainers':
            cryptos.sort(key=lambda x: x.get('change_pct', 0), reverse=True)
            cryptos = [c for c in cryptos if c.get('change_pct', 0) > 0][:15]
        else:
            cryptos.sort(key=lambda x: x.get('change_pct', 0))
            cryptos = [c for c in cryptos if c.get('change_pct', 0) < 0][:15]
        
        return jsonify({'success': True, 'cryptos': cryptos})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crypto/search')
def search_cryptos():
    """Search cryptocurrencies by name or symbol"""
    try:
        query = request.args.get('q', '').strip().lower()
        if not query:
            return jsonify({'success': True, 'cryptos': []})
        
        results = []
        
        # First search in our known crypto list
        for symbol, (name, icon) in CRYPTO_NAMES.items():
            if query in name.lower() or query in symbol.lower().replace('-usd', ''):
                data = get_crypto_data(symbol)
                if data:
                    results.append(data)
        
        # If no results, try direct symbol lookup
        if not results:
            test_symbol = f"{query.upper()}-USD"
            data = get_crypto_data(test_symbol)
            if data:
                results.append(data)
        
        return jsonify({'success': True, 'cryptos': results[:10]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/crypto/quote/<symbol>')
def get_crypto_quote(symbol):
    """Get quote for a specific cryptocurrency"""
    try:
        # Ensure symbol has -USD suffix
        if not symbol.upper().endswith('-USD'):
            symbol = f"{symbol.upper()}-USD"
        
        data = get_crypto_data(symbol)
        if data:
            return jsonify({'success': True, 'crypto': data})
        return jsonify({'success': False, 'error': 'Crypto not found'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================================================
# API ENDPOINTS
# ============================================================================

# ============================================================================
# UNIFIED BATCH API - Reduces multiple API calls to single request
# ============================================================================

# ============================================================================
# COMPREHENSIVE MARKET BREADTH (1800+ US Stocks)
# ============================================================================

_market_breadth_cache = {
    'data': None,
    'timestamp': None,
    'cache_ttl': 600,       # Market breadth data cached for 10 min (1163+ symbols, expensive)
}
_market_breadth_lock = threading.Lock()

# S&P 500 constituents (as of early 2026) - Yahoo Finance format (dots → dashes)
_SP500 = [
    'AAPL','ABBV','ABT','ACN','ADBE','ADI','ADM','ADP','ADSK','AEE','AEP','AES',
    'AFL','AIG','AIZ','AJG','AKAM','ALB','ALGN','ALL','ALLE','AMAT','AMCR','AMD',
    'AME','AMGN','AMP','AMT','AMZN','ANET','AON','AOS','APA','APD','APH',
    'APTV','ARE','ATO','AVB','AVGO','AVY','AWK','AXP','AZO',
    'BA','BAC','BAX','BBWI','BBY','BDX','BEN','BF-B','BG','BIIB','BIO','BK',
    'BKNG','BKR','BLK','BMY','BR','BRK-B','BRO','BSX','BWA','BX','BXP',
    'C','CAG','CAH','CARR','CAT','CB','CBOE','CBRE','CCI','CCL','CDNS',
    'CDW','CE','CEG','CF','CFG','CHD','CHRW','CHTR','CI','CINF','CL','CLX',    'CMCSA','CME','CMG','CMI','CMS','CNC','CNP','COF','COO','COP','COR','COST',
    'CPAY','CPB','CPRT','CPT','CRL','CRM','CSCO','CSGP','CSX','CTAS','CTRA',
    'CTSH','CTVA','CVS','CVX',
    'D','DAL','DAY','DD','DE','DECK','DG','DGX','DHI','DHR','DIS','DLTR',
    'DOV','DOW','DPZ','DRI','DTE','DUK','DVA','DVN',
    'DXCM','EA','EBAY','ECL','ED','EFX','EIX','EL','EMN','EMR','ENPH','EOG',
    'EPAM','EQIX','EQR','EQT','ERIE','ES','ESS','ETN','ETR','EVRG','EW','EXC',
    'EXPD','EXPE','EXR',
    'F','FANG','FAST','FBIN','FCX','FDS','FDX','FE','FFIV','FICO','FIS',
    'FISV','FITB','FMC','FOX','FOXA','FRT','FSLR','FTNT','FTV',
    'GD','GDDY','GE','GEHC','GEN','GILD','GIS','GL','GLW','GM','GNRC','GOOG',
    'GOOGL','GPC','GPN','GRMN','GS','GWW',
    'HAL','HAS','HBAN','HCA','HD','HOLX','HON','HPE','HPQ','HRL','HSIC','HST',
    'HSY','HUBB','HUM','HWM','IBM','ICE','IDXX','IEX','IFF','ILMN','INCY','INTC',
    'INTU','INVH','IP','IQV','IR','IRM','ISRG','IT','ITW','IVZ',
    'J','JBHT','JBL','JCI','JKHY','JNJ','JPM',
    'K','KDP','KEY','KEYS','KHC','KIM','KLAC','KMB','KMI','KMX','KO','KR',
    'KVUE','L','LDOS','LEN','LH','LHX','LIN','LKQ','LLY','LMT','LNT','LOW',
    'LRCX','LULU','LUV','LVS','LW','LYB','LYV',
    'MA','MAA','MAR','MAS','MCD','MCHP','MCK','MCO','MDLZ','MDT','MET','META',
    'MGM','MHK','MKC','MKTX','MLM','MMC','MMM','MNST','MO','MOH','MOS','MPC',
    'MPWR','MRK','MRNA','MRVL','MS','MSCI','MSFT','MSI','MTB','MTCH','MTD','MU',
    'NCLH','NDAQ','NDSN','NEE','NEM','NFLX','NI','NKE','NOC','NOW','NRG',
    'NSC','NTAP','NTRS','NUE','NVDA','NVR','NWS','NWSA','NXPI',
    'O','ODFL','OKE','OMC','ON','ORCL','ORLY','OTIS','OXY',
    'PANW','LYV','PAYC','PAYX','PCAR','PCG','PEG','PEP',
    'PFE','PFG','PG','PGR','PH','PHM','PKG','PLD','PM','PNC','PNR','PNW','POOL',
    'PPG','PPL','PRU','PSA','PSX','PTC','PVH','PWR','DINO',
    'PYPL','QCOM','QRVO','RCL','REG','REGN','RF','RHI','RJF','RL','RMD','ROK',
    'ROL','ROP','ROST','RSG','RTX','RVTY',
    'SBAC','SBUX','SCHW','SEE','SHW','SJM','SLB','SMCI','TRGP',
    'SNA','SNPS','SO','SOLV','SPG','SPGI','SRE','STE','STT','STX','STZ',
    'SWK','SWKS','SYF','SYK','SYY',
    'T','TAP','TDG','TDY','TECH','TEL','TER','TFC','TFX','TGT','THC','TJX','TMO',
    'TMUS','TPR','TRGP','TRMB','TROW','TRV','TSCO','TSLA','TSN','TT','TTWO','TXN',
    'TXT','TYL',
    'UAL','UDR','UHS','ULTA','UNH','UNP','UPS','URI','USB',
    'V','VICI','VLO','VLTO','VMC','VRSK','VRSN','VRTX','VST','VTR','VTRS','VZ',
    'WAB','WAT','WBD','WDC','WEC','WELL','WFC','WHR','WM','WMB','WMT',
    'WRB','WST','WTW','WY','WYNN',
    'XEL','XOM','XRAY','XYL','YUM','ZBH','ZBRA','ZION','ZTS'
]

# S&P MidCap 400 representative constituents
_SP400 = [
    'ACGL','ACM','ACIW','AEIS','AFG','AGCO','AIT','ALKS','AMKR','AOS',
    'ASGN','ASGN','ATR','AXON','AYI',    'BC','BJ','BRKR','BWXT','BYD',
    'CACI','CALM','CARG','CASY','CBSH','CC','CHDN','CHE','CIEN','CLH','COLM',
    'CRI','CRUS','CUZ','CW','CZR',
    'DCI','DINO','DOCS','DOCU','DT','DUOL','DXC',
    'EGP','EHC','ENSG','EPRT','ESI','ETSY','EVR','EWBC','EXEL','EXP',
    'FAF','FIVE','FHN','FIX','FN','FNF','FRPT','FLS','FROG',
    'G','GATX','GGG','GLOB','GNRC','GNTX','GTES','GWRE',
    'HAE','HAYW','HE','HESM','HGV','HLI','HLNE','HQY','HRB',
    'IART','IBP','ICFI','ICLR','IESC','IEX','INGR','IOSP',    'ITT','JAZZ','JBGS','JBSS','JEF','JHG','JLL',    'KBR','KEX','KMPR','KNSL','KNX','KNTK','KRG',
    'LAUR','LEA','LII','LITE','LIVN','LNTH','LSTR','LW','LXP',
    'MANH','MAN','MASI','MDGL','MEDP','MIDD','MKSI','MKTX','MLI','MOD',
    'MTH','MTSI','MUR','MUSA',
    'NBIX','NCNO','NEU','NOVT','NVT','NXST','NYT',
    'OC','OGE','OGN','OGS','OLED','OLN','ONEW','ORA','OSK','OTTR',    'PAYC','PB','PBF','PCOR','PEN','PII','PLNT','PNFP','POR',
    'POST','POWL','PPG','PRIM','PRGS','PRI','PTCT','PVH',
    'QLYS','QRVO','RBC','RBRK','RDN','REXR','RGLD','RH','RHP','RIG','RLI',
    'RMBS','RNR','ROIV','RPM','RS','RXO',
    'SAM','SAIC','SAIA','SBRA','SCI','SEIC','SF','SITE','SLM','SM',
    'SMPL','SON','SSD','STAG','STRA','SUM','SXT',
    'TDC','TECH','TENB','TFSL','TGNA','TKO','TKR','TMHC','TOL','TPG',    'TTMI','TW','TXRH','TYL',
    'UBSI','UFPI','UMBF','UNM','UPBD','URBN',
    'VCYT','VEEV','VFC','VIRT','VRNS','VRRM','VVV',
    'WAL','WDAY','WEX','WH','WHD','WING','WK','WOLF','WPC','WPM','WTM','WTRG',
    'X','XNCR','XPO','XRAY',
    'YEXT','ZWS'
]

# S&P SmallCap 600 representative constituents
_SP600 = [
    'ABCB','ABG','ABM','ACAD','ACCO','ACLS','ADNT','AEHL','AEHR',
    'AEO','AGEN','AGYS','AIN','AKR','ALGT','ALLO','ALRM',
    'AMBA','AMKR','AMPH','ANF','ANGO','ANIK','AORT','AM',
    'APPF','APPS','ARCB','ARDX','AROC','ARRY','ASB','ASIX','ASTE',
    'ATEC','ATGE','ATNI','ATRO','AVAV','AVO','AVNT','AX',
    'BALY','BDC','BEAT','BFAM','BGS','BHE','BJRI','BKE','BKH','BL','BLKB',
    'BMBL','BRC','BRZE','BTU','BWA','BXMT',
    'CAKE','CALX','CAR','CARG','CARS','CATY','CBT','CCS','CCSI','CDNA',
    'CENX','CEVA','CHH','CHWY','CIVB','CLDX','CLF',    'CMP','CMPR','CNK','CNMD','CNO','CNS','CNXN','COHU','CORT',
    'CPRX','CPK','CRAI','CROX','CSL','CSTL','CSTM','CTBI','CTLP',
    'CTO','CVBF','CVCO','CVI','CVLT','CWST','CWT','CXW',
    'DDS','DIOD','DLB','DLX','DNLI','DORM','DRH','DSP',
    'ECPG','EGO','EIG','ENR','ENSG','ENV','EPR','ESNT',    'EVTC','EXLS','EXPI','EXPO','EZPW',
    'FBP','FCFS','FCNCA','FDP','FELE','FHB','FHI','FIZZ','FNB',
    'FOLD','FORM','FOXF','FRGE','FRME','FWRD','GBX',
    'GIII','GNTX','GOLF','GPI','GPOR','GRWG','GSHD','GTY','GWRS',
    'HAIN','HAYW','HCAT','HCSG','HELE','HESM','HFWA','HLF','HMN',
    'HNI','HUBG','HURN','HWC','HWKN',
    'IAC','IBKR','IBOC','ICFI','ICHR','IDCC','INDB','INSM','INSW',
    'IOSP','IPAR','IRT','IRWD',    'JACK','JBGS','JBLU','JJSF','JKHY','JOE','JRVR',
    'KALU','KFRC','KMT','KN','KNSA','KREF','KWR',
    'LBRT','LCII','LDI','LFUS','LGIH','LIVN','LMAT','LMNR','LGND',
    'LOPE','LPLA','LPX','LQDT','LSCC','LUNA','LXP',
    'MATX','MBC','MCRI','MD','MGEE','MGPI','MGRC','MLAB',
    'MLKN','MMSI','MNKD','MOD','MORN','MRCY','MSGE','MSGS','MTDR','MTLS',
    'MTRN','MVRL','MWA','MXL','MYE',
    'NABL','NAVI','NBR','NBTB','NEOG','NHC','NINE','NOG','NOVT',    'NSA','NSP','NSSC','NTB','NTCT','NTST','NWE',    'OFG','OII','OLPX','OMI','ONB','ONTO','OOMA','OPK','OSIS',
    'OTTR','OXM','PATK','PBH','PLXS','PRAA','POWL','PRA',
    'PRLB','PRPL','PTGX','PUMP','PVH',
    'QTWO','R','RCKT','RCKY','RDNT','REPL','REZI','RGR','RIG',
    'RLGT','RMBS','RNG','RNST','ROCK','ROG','RPD','RRBI','RRR','RUN',
    'SABR','SAFE','SAH','SAIL','SANM','SBH','SBRA','SCHL','SEM','SGHT',
    'SHC','SITM','SKWD','SKY','SLGN','SLM','SMBC','SMPL','SNBR','SNEX',
    'SPNT','SPSC','SR','SRI','STAA','STEP','SWIM','SWX',
    'TCBI','TDC','TERN','TGTX','TILE','TNC','TNDM','TRNO','TRNR','TRUP','TTI',
    'TVTX','TXRH','TYL',
    'UFPI','UMBF','UNIT','UPWK','USPH','UTL',
    'VAC','VCYT','VECO','VERX','VG','VIR','VIRC','VIRT',    'VRNS','VRRM','VSEC','VVV',
    'WABC','WAFD','WBS','WD','WK','WOR','WRBY','WSBF','WSC',
    'XNCR','XRX',
    'YEXT','YORW',
    'ZION','ZWS'
]

# Additional popular stocks not in S&P indices (meme, EV, crypto, cannabis, etc.)
_ADDITIONAL = [
    'RDDT','PLTR','HOOD','SOFI','COIN','MSTR','RBLX','SNAP','PINS','ROKU',
    'DKNG','DASH','U','SHOP','TTD','DDOG','NET','CRWD','ZS','MDB','SNOW',
    'OKTA','MNDY','BILL','ESTC','CFLT','DOCN','FROG',
    'RIVN','LCID','NIO','XPEV','LI','JOBY','CHPT','BLNK',
    'QS','BE','BNTX','CRSP','NTLA','BEAM','EDIT','ARWR',
    'IONS','RARE','AVXL',
    'GME','AMC','BB','DJT','ASTS','IONQ',
    'MARA','RIOT','CLSK','HUT','BITF','IREN','APLD',
    'AFRM','UPST','HIMS','RXRX','RCAT','LUNR','SOUN','BBAI','AI','GLBE','PATH',
    'CELH','DUOL','CAVA','BIRK',
    'SPY','QQQ','IWM','DIA','VTI','VOO'
]


def _get_broad_market_tickers():
    """Get comprehensive US market ticker list (1500+ stocks) - hardcoded for reliability"""
    dashboard_symbols = _SP500 + _SP400 + _SP600 + _ADDITIONAL
    # Deduplicate while preserving order
    seen = set()
    merged_symbols = []
    for s in dashboard_symbols:
        su = s.strip().upper()
        if su and su not in seen:
            seen.add(su)
            merged_symbols.append(su)
    return filter_valid_symbols(merged_symbols)


def calculate_market_breadth():
    """Calculate market breadth for 1500+ US stocks using yf.download()"""
    now = datetime.now()

    # Return cached data if valid
    with _market_breadth_lock:
        if (_market_breadth_cache['data'] is not None and
            _market_breadth_cache['timestamp'] is not None):
            age = (now - _market_breadth_cache['timestamp']).total_seconds()
            if age < _market_breadth_cache['cache_ttl']:
                return _market_breadth_cache['data']

    tickers = _get_broad_market_tickers()
    if not tickers:
        return None

    try:
        # Batch download - much faster than individual requests
        # Process in chunks to avoid timeout on large lists
        all_changes = pd.Series(dtype=float)
        chunk_size = 300  # Smaller chunks to avoid rate limits
        max_retries = 2
        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i:i + chunk_size]
            for attempt in range(max_retries + 1):
                try:
                    data = yf.download(chunk, period='2d', threads=True, progress=False, timeout=60)
                    if data.empty:
                        if attempt < max_retries:
                            import time
                            time.sleep(2 * (attempt + 1))  # Back off on empty results
                            continue
                        break
                    close = data['Close'] if isinstance(data.columns, pd.MultiIndex) else data
                    if isinstance(close, pd.Series) or len(close) < 2:
                        break
                    prev_close = close.iloc[-2]
                    curr_close = close.iloc[-1]
                    changes = (curr_close / prev_close - 1).dropna()
                    all_changes = pd.concat([all_changes, changes])
                    break  # Success, move to next chunk
                except Exception as e:
                    err_str = str(e).lower()
                    if 'rate' in err_str and attempt < max_retries:
                        import time
                        time.sleep(3 * (attempt + 1))  # Longer back-off for rate limits
                        continue
                    print(f"Market breadth chunk {i}-{i+chunk_size} error: {e}")
                    break
            # Small delay between chunks to avoid rate limiting
            import time
            time.sleep(0.5)

        if all_changes.empty:
            return None

        advancing = int((all_changes > 0).sum())
        declining = int((all_changes < 0).sum())
        unchanged = int((all_changes == 0).sum())
        total = advancing + declining + unchanged

        # Calculate advance/decline ratio and breadth indicators
        ad_ratio = round(advancing / max(declining, 1), 2)
        breadth_pct = round((advancing / max(total, 1)) * 100, 1)

        result = {
            'advancing': advancing,
            'declining': declining,
            'unchanged': unchanged,
            'total': total,
            'advance_decline_ratio': ad_ratio,
            'breadth_pct': breadth_pct,
            'source': f'US Market ({total} stocks)'
        }

        # Cache the result
        with _market_breadth_lock:
            _market_breadth_cache['data'] = result
            _market_breadth_cache['timestamp'] = now

        print(f"📊 Market Breadth: {advancing} advancing, {declining} declining "
              f"out of {total} stocks (A/D ratio: {ad_ratio})")
        return result

    except Exception as e:
        print(f"Market breadth calculation error: {e}")
        return None


# Batch data cache with TTL
_batch_cache = {
    'data': None,
    'timestamp': None,
    'cache_ttl': 300  # seconds (5 min) - reduced API calls significantly
}
_batch_cache_lock = threading.Lock()

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
            # Single symbol → flat columns (Close, Open, etc.)
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
            # Multiple symbols → MultiIndex columns (symbol, field)
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

def _get_batch_dashboard_data():
    """Internal function to fetch all dashboard data in one call"""
    # Get market status
    status, status_text = get_market_status()
    
    # Collect ALL symbols we need to fetch
    all_symbols = []
    
    # Index symbols
    index_symbols = list(MAJOR_INDICES.keys())
    all_symbols.extend(index_symbols)
    
    # Sector ETF symbols  
    sector_symbols = list(SECTOR_ETFS.keys())
    all_symbols.extend(sector_symbols)
    
    # Compact movers watchlist (~50 most-liquid names across sectors)
    # Reduced from 189 symbols to cut API payload size
    movers_watchlist = [
        # Tech (10)
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD', 'CRM', 'AVGO',
        # Communication (5)
        'NFLX', 'DIS', 'CMCSA', 'T', 'TMUS',
        # Healthcare (5)
        'JNJ', 'UNH', 'LLY', 'PFE', 'ABBV',
        # Financials (5)
        'JPM', 'BAC', 'GS', 'V', 'MA',
        # Consumer (5)
        'WMT', 'HD', 'COST', 'MCD', 'KO',
        # Industrials (5)
        'BA', 'CAT', 'HON', 'UNP', 'GE',
        # Energy (5)
        'XOM', 'CVX', 'COP', 'SLB', 'OXY',
        # EV & Clean Energy (5)
        'RIVN', 'F', 'GM', 'FSLR', 'ENPH',
        # REITs (3)
        'AMT', 'PLD', 'EQIX',
        # High-vol retail favourites (5)
        'PLTR', 'COIN', 'SOFI', 'HOOD', 'SHOP',
    ]
    movers_watchlist = list(set(movers_watchlist))
    all_symbols.extend(movers_watchlist)
    
    # Fetch ALL quotes in one batch call
    all_quotes = _fetch_all_quotes_batch(all_symbols)
    
    # Build indices from cached quotes
    indices = []
    for symbol, name in MAJOR_INDICES.items():
        if symbol in all_quotes:
            quote = all_quotes[symbol].copy()
            quote['name'] = name
            indices.append(quote)
    
    # Build sectors from cached quotes
    sectors = []
    for symbol, sector_name in SECTOR_ETFS.items():
        if symbol in all_quotes:
            quote = all_quotes[symbol]
            sectors.append({
                'sector': sector_name,
                'changePct': quote.get('changePct', 0),
                'price': quote.get('price', 0),
                'symbol': symbol
            })
    sectors = sorted(sectors, key=lambda x: x['changePct'], reverse=True)
    
    # Build movers from cached quotes
    movers_data = []
    for symbol in movers_watchlist:
        if symbol in all_quotes:
            movers_data.append(all_quotes[symbol])
    
    gainers = sorted(
        [m for m in movers_data if m.get('changePct', 0) > 0],
        key=lambda x: x.get('changePct', 0),
        reverse=True
    )[:20]
    
    losers = sorted(
        [m for m in movers_data if m.get('changePct', 0) < 0],
        key=lambda x: x.get('changePct', 0)
    )[:20]
    
    # Get extended hours data based on market status
    extended_hours = None
    if status in ['PRE_MARKET', 'AFTER_HOURS', 'CLOSED']:
        try:
            extended_data = get_premarket_movers(limit=15)
            extended_hours = {
                'gainers': extended_data['gainers'],
                'losers': extended_data['losers'],
                'session': 'pre-market' if status == 'PRE_MARKET' else 'after-hours'
            }
        except Exception as e:
            print(f"Extended hours fetch error: {e}")
    
    # Calculate market pulse from comprehensive US market breadth (1800+ stocks)
    market_pulse = calculate_market_breadth()
    if market_pulse is None:
        # Fallback to simple calculation from fetched watchlist
        advancing = 0
        declining = 0
        unchanged = 0
        for symbol, quote in all_quotes.items():
            pct = quote.get('changePct', 0)
            if pct > 0:
                advancing += 1
            elif pct < 0:
                declining += 1
            else:
                unchanged += 1
        market_pulse = {
            'advancing': advancing,
            'declining': declining,
            'unchanged': unchanged,
            'total': len(all_quotes),
            'advance_decline_ratio': round(advancing / max(declining, 1), 2),
            'breadth_pct': round((advancing / max(len(all_quotes), 1)) * 100, 1),
            'source': 'dashboard watchlist (fallback)'
        }
    
    return {
        'timestamp': datetime.now().isoformat(),
        'market_status': {
            'status': status,
            'text': status_text
        },
        'indices': indices,
        'sectors': sectors,
        'gainers': gainers,
        'losers': losers,
        'extended_hours': extended_hours,
        'market_pulse': market_pulse,
        'symbols_fetched': len(all_quotes),
        'cached': False
    }

@app.route('/api/dashboard/batch')
def dashboard_batch():
    """
    UNIFIED BATCH ENDPOINT - Returns all dashboard data in a single API call.
    Reduces yfinance API hits by fetching all symbols once and reusing data.
    
    Returns:
        - market_status: Current market status (OPEN/CLOSED/PRE_MARKET/AFTER_HOURS)
        - indices: Major market indices (S&P 500, Dow, NASDAQ, etc.)
        - sectors: Sector ETF performance
        - gainers: Top gaining stocks
        - losers: Top losing stocks
        - extended_hours: Pre-market/after-hours movers (when applicable)
    """
    try:
        with _batch_cache_lock:
            # Check cache
            if _batch_cache['data'] is not None and _batch_cache['timestamp'] is not None:
                age = (datetime.now() - _batch_cache['timestamp']).total_seconds()
                if age < _batch_cache['cache_ttl']:
                    cached_data = _batch_cache['data'].copy()
                    cached_data['cached'] = True
                    cached_data['cache_age_seconds'] = int(age)
                    return jsonify({'success': True, **cached_data})
        
        # Fetch fresh data
        data = _get_batch_dashboard_data()
        
        # Update cache
        with _batch_cache_lock:
            _batch_cache['data'] = data
            _batch_cache['timestamp'] = datetime.now()
        
        return jsonify({'success': True, **data})
    
    except Exception as e:
        print(f"Error in batch dashboard API: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dashboard/batch', methods=['POST'])
def dashboard_batch_custom():
    """
    Custom batch endpoint - fetch only specific data sections.
    
    POST body (JSON):
        {
            "sections": ["indices", "sectors", "gainers", "losers", "extended_hours"],
            "symbols": ["AAPL", "MSFT"]  // Optional: additional symbols to fetch
        }
    """
    try:
        req = request.get_json(force=True) or {}
        sections = req.get('sections', ['indices', 'sectors', 'gainers', 'losers'])
        extra_symbols = req.get('symbols', [])
        
        result = {
            'timestamp': datetime.now().isoformat(),
            'success': True
        }
        
        all_symbols = list(extra_symbols)
        
        # Collect symbols based on requested sections
        if 'indices' in sections:
            all_symbols.extend(MAJOR_INDICES.keys())
        if 'sectors' in sections:
            all_symbols.extend(SECTOR_ETFS.keys())
        if 'gainers' in sections or 'losers' in sections:
            all_symbols.extend([
                'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD',
                'NFLX', 'DIS', 'PYPL', 'INTC', 'BABA', 'PFE', 'WMT', 'JPM',
                'BAC', 'XOM', 'CVX', 'T', 'VZ', 'KO', 'PEP', 'NKE', 'BA'
            ])
        
        # Fetch all at once
        all_quotes = _fetch_all_quotes_batch(all_symbols)
        
        # Build response based on sections
        if 'indices' in sections:
            indices = []
            for symbol, name in MAJOR_INDICES.items():
                if symbol in all_quotes:
                    quote = all_quotes[symbol].copy()
                    quote['name'] = name
                    indices.append(quote)
            result['indices'] = indices
        
        if 'sectors' in sections:
            sectors = []
            for symbol, sector_name in SECTOR_ETFS.items():
                if symbol in all_quotes:
                    sectors.append({
                        'sector': sector_name,
                        'changePct': all_quotes[symbol].get('changePct', 0),
                        'price': all_quotes[symbol].get('price', 0),
                        'symbol': symbol
                    })
            result['sectors'] = sorted(sectors, key=lambda x: x['changePct'], reverse=True)
        
        if 'gainers' in sections or 'losers' in sections:
            movers = [q for q in all_quotes.values() if q.get('changePct') is not None]
            if 'gainers' in sections:
                result['gainers'] = sorted(
                    [m for m in movers if m.get('changePct', 0) > 0],
                    key=lambda x: x.get('changePct', 0), reverse=True
                )[:20]
            if 'losers' in sections:
                result['losers'] = sorted(
                    [m for m in movers if m.get('changePct', 0) < 0],
                    key=lambda x: x.get('changePct', 0)
                )[:20]
        
        # Custom symbols
        if extra_symbols:
            result['custom_quotes'] = {sym: all_quotes.get(sym) for sym in extra_symbols if sym in all_quotes}
        
        status, status_text = get_market_status()
        result['market_status'] = {'status': status, 'text': status_text}
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# INDIVIDUAL MARKET ENDPOINTS (kept for backward compatibility)
# ============================================================================

@app.route('/api/market/overview')
def market_overview():
    """Get market indices and status"""
    try:
        status, status_text = get_market_status()
        
        # Batch fetch all index symbols in one call
        index_symbols = list(MAJOR_INDICES.keys())
        all_quotes = _fetch_all_quotes_batch(index_symbols)
        
        indices = []
        for symbol, name in MAJOR_INDICES.items():
            if symbol in all_quotes:
                quote = all_quotes[symbol].copy()
                quote['name'] = name
                indices.append(quote)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'market_status': {
                'status': status,
                'text': status_text
            },
            'marketStatus': status,
            'marketStatusText': status_text,
            'indices': indices
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/sectors')
def market_sectors():
    """Get sector performance heatmap data"""
    try:
        sectors = get_sector_performance()
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'sectors': sectors
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/sector/<sector_etf>/stocks')
def sector_stocks(sector_etf):
    """Get stocks for a specific sector with live quotes"""
    try:
        sector_etf = sector_etf.upper()
        if sector_etf not in SECTOR_STOCKS:
            return jsonify({'success': False, 'error': f'Unknown sector ETF: {sector_etf}'}), 400
        
        sector_name = SECTOR_ETFS.get(sector_etf, sector_etf)
        symbols = SECTOR_STOCKS[sector_etf]
        quotes_map = _fetch_all_quotes_batch(symbols)
        stocks = []

        for symbol in symbols:
            quote = quotes_map.get(symbol)
            if not quote:
                continue
            info = _get_cached_info_only(symbol)
            stocks.append({
                'symbol': symbol,
                'name': info.get('shortName', symbol) if isinstance(info, dict) else symbol,
                'price': float(quote.get('price', 0) or 0),
                'change': float(quote.get('change', 0) or 0),
                'changePct': float(quote.get('changePct', 0) or 0),
                'volume': int(quote.get('volume', 0) or 0),
                'marketCap': info.get('marketCap', 0) if isinstance(info, dict) else 0,
                'high': float(quote.get('high', 0) or 0),
                'low': float(quote.get('low', 0) or 0)
            })

        # Sort by market cap descending
        stocks.sort(key=lambda x: x.get('marketCap', 0), reverse=True)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'sector': sector_name,
            'sectorETF': sector_etf,
            'stocks': stocks
        })
    except Exception as e:
        print(f"Error fetching sector stocks: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/movers/<direction>')
def market_movers(direction):
    """Get top gainers or losers"""
    try:
        if direction not in ['gainers', 'losers']:
            return jsonify({'success': False, 'error': 'Invalid direction'}), 400
        
        movers = get_top_movers(direction=direction, limit=20)
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'direction': direction,
            'movers': movers
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/premarket')
def premarket_analysis():
    """Get pre-market top movers analysis"""
    try:
        market_state, market_desc = get_market_status()
        data = get_premarket_movers(limit=20)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'marketState': market_state,
            'marketDescription': market_desc,
            'gainers': data['gainers'],
            'losers': data['losers'],
            'totalScanned': data['total_scanned']
        })
    except Exception as e:
        print(f"Error in pre-market analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/afterhours')
def afterhours_analysis():
    """Get after-hours top movers analysis"""
    try:
        market_state, market_desc = get_market_status()
        data = get_afterhours_movers(limit=20)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'marketState': market_state,
            'marketDescription': market_desc,
            'gainers': data['gainers'],
            'losers': data['losers'],
            'totalScanned': data['total_scanned']
        })
    except Exception as e:
        print(f"Error in after-hours analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/52week')
def week_52_extremes():
    """Get stocks touching 52-week highs and lows"""
    try:
        # Popular stocks to scan for 52-week extremes
        scan_symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'INTC', 'NFLX',
            'DIS', 'BA', 'JPM', 'GS', 'V', 'MA', 'PYPL', 'COIN', 'SHOP',
            'CRM', 'ORCL', 'ADBE', 'NOW', 'SNOW', 'PLTR', 'UBER', 'LYFT', 'ABNB', 'RIVN',
            'F', 'GM', 'NIO', 'LCID', 'XOM', 'CVX', 'COP', 'OXY', 'DVN',
            'WMT', 'COST', 'TGT', 'HD', 'LOW', 'NKE', 'LULU', 'SBUX', 'MCD', 'CMG',
            'PFE', 'JNJ', 'UNH', 'ABBV', 'LLY', 'MRK', 'BMY', 'GILD', 'MRNA', 'BNTX',
            'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'XLF', 'XLE', 'XLK', 'XLV', 'ARKK'
        ]
        
        highs = []
        lows = []
        
        def analyze_52week(symbol):
            try:
                hist = cached_get_history(symbol, period='1y', interval='1d')
                if hist is None or hist.empty or len(hist) < 50:
                    return None
                    
                current_price = hist['Close'].iloc[-1]
                high_52w = hist['High'].max()
                low_52w = hist['Low'].min()
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
                
                # Calculate distance from extremes
                pct_from_high = ((current_price - high_52w) / high_52w) * 100
                pct_from_low = ((current_price - low_52w) / low_52w) * 100
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                info = cached_get_ticker_info(symbol)
                result = {
                    'symbol': symbol,
                    'name': info.get('shortName', symbol)[:30],
                    'price': float(current_price),
                    'change_pct': float(change_pct),
                    'high_52w': float(high_52w),
                    'low_52w': float(low_52w),
                    'pct_from_high': float(pct_from_high),
                    'pct_from_low': float(pct_from_low),
                    'volume': int(hist['Volume'].iloc[-1]) if 'Volume' in hist else 0
                }
                
                # Near 52-week high (within 3%)
                if pct_from_high >= -3:
                    result['is_high'] = True
                    return result
                # Near 52-week low (within 3%)  
                elif pct_from_low <= 3:
                    result['is_low'] = True
                    return result
                return None
            except Exception as e:
                return None
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(analyze_52week, scan_symbols))
        
        for r in results:
            if r:
                if r.get('is_high'):
                    highs.append(r)
                elif r.get('is_low'):
                    lows.append(r)
        
        # Sort: highs by closest to high, lows by closest to low
        highs.sort(key=lambda x: x['pct_from_high'], reverse=True)
        lows.sort(key=lambda x: x['pct_from_low'])
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'highs': highs[:15],
            'lows': lows[:15],
            'total_scanned': len(scan_symbols)
        })
    except Exception as e:
        print(f"Error in 52-week analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/market/crashed')
def crashed_stocks():
    """Get stocks crashed 30%+ from their 52-week high - potential value plays or falling knives"""
    try:
        # Extended list of stocks to scan for crashes
        scan_symbols = [
            # Big Tech
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'INTC', 'NFLX',
            # Financials
            'JPM', 'BAC', 'GS', 'MS', 'WFC', 'C', 'SCHW', 'BLK', 'V', 'MA',
            # Tech/Software
            'CRM', 'ORCL', 'ADBE', 'NOW', 'SNOW', 'PLTR', 'DDOG', 'MDB', 'NET', 'CRWD',
            'ZS', 'OKTA', 'TWLO', 'DOCU', 'ZM', 'ROKU', 'SHOP', 'PYPL', 'SOFI',
            # Consumer/Retail
            'DIS', 'SBUX', 'NKE', 'LULU', 'TGT', 'COST', 'WMT', 'HD', 'LOW', 'CMG',
            # EV & Auto
            'RIVN', 'LCID', 'NIO', 'XPEV', 'LI', 'F', 'GM', 'CVNA',
            # Healthcare/Biotech
            'PFE', 'MRNA', 'BNTX', 'BIIB', 'GILD', 'REGN', 'VRTX', 'ILMN', 'TDOC',
            # Crypto/Fintech
            'COIN', 'MSTR', 'HOOD', 'AFRM', 'UPST',
            # Growth/Speculative
            'ARKK', 'ARKG', 'ARKF', 'PATH', 'RBLX', 'U', 'SNAP', 'PINS', 'MTCH',
            # Energy
            'XOM', 'CVX', 'COP', 'OXY', 'DVN', 'FANG', 'EOG',
            # Semiconductors
            'QCOM', 'AVGO', 'MU', 'MRVL', 'ON', 'SWKS', 'QRVO', 'LRCX', 'AMAT', 'KLAC',
            # Other popular
            'BA', 'CAT', 'DE', 'MMM', 'RTX', 'LMT', 'NOC', 'GE', 'HON',
            'ABNB', 'UBER', 'LYFT', 'DASH', 'GRAB', 'SE'
        ]
        
        crashed_stocks_list = []
        
        def analyze_crash(symbol):
            try:
                # Use prepost=True for live prices during extended hours
                hist = cached_get_history(symbol, period='1y', interval='1d', prepost=True)
                if hist is None or hist.empty or len(hist) < 50:
                    return None
                    
                current_price = hist['Close'].iloc[-1]
                high_52w = hist['High'].max()
                low_52w = hist['Low'].min()
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else current_price
                
                # Calculate % drop from 52-week high
                pct_from_high = ((current_price - high_52w) / high_52w) * 100
                pct_from_low = ((current_price - low_52w) / low_52w) * 100
                change_pct = ((current_price - prev_close) / prev_close) * 100
                
                # Only include stocks that crashed 30%+ from 52-week high
                if pct_from_high <= -30:
                    info = cached_get_ticker_info(symbol)
                    market_cap = info.get('marketCap', 0)
                    
                    # Format market cap
                    if market_cap >= 1e12:
                        market_cap_str = f"${market_cap/1e12:.1f}T"
                    elif market_cap >= 1e9:
                        market_cap_str = f"${market_cap/1e9:.1f}B"
                    elif market_cap >= 1e6:
                        market_cap_str = f"${market_cap/1e6:.0f}M"
                    else:
                        market_cap_str = "N/A"
                    
                    return {
                        'symbol': symbol,
                        'name': info.get('shortName', symbol)[:35],
                        'price': float(current_price),
                        'change_pct': float(change_pct),
                        'high_52w': float(high_52w),
                        'low_52w': float(low_52w),
                        'pct_from_high': float(pct_from_high),
                        'pct_from_low': float(pct_from_low),
                        'market_cap': market_cap,
                        'market_cap_str': market_cap_str,
                        'volume': int(hist['Volume'].iloc[-1]) if 'Volume' in hist else 0,
                        'sector': info.get('sector', 'N/A'),
                        'pe_ratio': info.get('trailingPE', None),
                    }
                return None
            except Exception as e:
                return None
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            results = list(executor.map(analyze_crash, scan_symbols))
        
        for r in results:
            if r:
                crashed_stocks_list.append(r)
        
        # Sort by largest crash first (most negative pct_from_high)
        crashed_stocks_list.sort(key=lambda x: x['pct_from_high'])
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'stocks': crashed_stocks_list[:30],  # Top 30 crashed stocks
            'total_crashed': len(crashed_stocks_list),
            'total_scanned': len(scan_symbols),
            'threshold': -30  # 30% crash threshold
        })
    except Exception as e:
        print(f"Error in crashed stocks analysis: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/unified')
def unified_scanner():
    """Run unified trading system scanner with caching"""
    try:
        cache_key = 'unified'
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'picks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting unified scanner in background...")
                    system = UnifiedTradingSystem()
                    
                    # Limit stock universes to avoid rate limiting
                    system.stock_symbols = [
                        'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AMD',
                        'JPM', 'BAC', 'V', 'MA', 'WFC', 'GS',
                        'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK',
                        'WMT', 'HD', 'COST', 'NKE', 'MCD'
                    ]
                    system.etf_symbols = ['SPY', 'QQQ', 'IWM', 'DIA', 'XLF', 'XLK', 'XLE', 'XLV']
                    
                    picks = system.get_top_5_picks()
                    # Clean NaN values for JSON serialization
                    picks = clean_nan_values(picks)
                    scanner_cache[cache_key]['data'] = picks
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Unified scanner complete!")
                except Exception as e:
                    print(f"❌ Unified scanner error: {e}")
                    scanner_cache[cache_key]['data'] = {'options': [], 'stocks': [], 'etfs': []}
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'picks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'picks': {'options': [], 'stocks': [], 'etfs': []},
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/short-squeeze')
def short_squeeze():
    """Run short squeeze scanner with caching"""
    try:
        cache_key = 'short-squeeze'
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'candidates': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting short squeeze scanner in background...")
                    scanner = ShortSqueezeScanner()
                    # Reduce workers to avoid rate limiting (3 instead of 10)
                    # Focus on popular squeeze candidates to avoid API limits
                    priority_symbols = [
                        'GME', 'AMC', 'BB', 'KOSS', 'DJT',
                        'HOOD', 'SOFI', 'PLTR', 'RIVN', 'LCID', 'NIO', 'TSLA',
                        'NVDA', 'AMD', 'AAPL', 'MSFT', 'META', 'GOOGL', 'AMZN',
                        'MARA', 'RIOT', 'BLNK'
                    ]
                    # Limit to priority symbols to avoid rate limits
                    scanner.symbols = filter_valid_symbols(priority_symbols)
                    
                    # Use sequential processing (max_workers=1) to avoid rate limiting
                    # Batch processing: 5 stocks, then 10s wait
                    df = scanner.scan_market(min_squeeze_score=40, max_workers=1)
                    
                    if not df.empty:
                        results = df.head(20).to_dict('records')
                        # Clean NaN values for JSON serialization
                        results = clean_nan_values(results)
                    else:
                        print("⚠️ No squeeze candidates found (API may be rate limited)")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Short squeeze scanner complete! Found {len(results)} candidates")
                except Exception as e:
                    print(f"❌ Short squeeze scanner error: {e}")
                    # On error, return empty results
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'candidates': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'candidates': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/weekly-screener')
def weekly_screener():
    """Run weekly screener with caching"""
    try:
        cache_key = 'weekly-screener'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting weekly screener in background...")
                    
                    try:
                        from weekly_screener_top100 import WeeklyStockScreener
                        screener = WeeklyStockScreener()
                        
                        # Limit stock universe to top 50 most liquid stocks
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO', 'ORCL', 'ADBE',
                            'CRM', 'CSCO', 'AMD', 'INTC', 'QCOM', 'NFLX', 'TXN', 'INTU', 'NOW',
                            'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'SPGI', 'BLK',
                            'LLY', 'UNH', 'JNJ', 'ABBV', 'MRK', 'TMO', 'ABT', 'AMGN', 'DHR', 'PFE',
                            'WMT', 'HD', 'PG', 'COST', 'KO', 'MCD', 'PEP', 'NKE', 'TGT', 'SBUX'
                        ]
                        screener.stock_universe = filter_valid_symbols(limited_universe)
                        
                        # Reduce workers to avoid rate limiting
                        # Lower min_score to 3 (weekly scanner has different scoring: max ~15)
                        df = screener.scan_market(min_score=3, max_workers=3)
                        
                        if not df.empty:
                            results = df.head(20).to_dict('records')
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No weekly signals found matching criteria")
                            results = []
                    except ImportError:
                        print("❌ Weekly screener module not available")
                        results = []
                    except Exception as scan_error:
                        print(f"❌ Weekly scan error: {scan_error}")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Weekly screener complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Weekly screener error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/quality-stocks')
def quality_stocks():
    """Run beaten down quality stocks scanner with caching"""
    try:
        cache_key = 'quality-stocks'
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting quality stocks scanner in background...")
                    scanner = BeatenDownQualityScanner()
                    
                    # Use full universe including high-growth small-caps (VERO, MARA, etc.)
                    # No need to limit - we want to find explosive growth opportunities
                    
                    # Reduce workers to avoid rate limiting
                    # Relaxed filters: -5% drawdown (instead of -15%), quality 30+ (instead of 50+)
                    df = scanner.scan_market(min_drawdown=-5, min_quality_score=30, max_workers=3)
                    
                    if not df.empty:
                        results = df.head(20).to_dict('records')
                        results = clean_nan_values(results)
                    else:
                        print("⚠️ No quality stocks found matching criteria")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Quality stocks scanner complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Quality stocks scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/golden-cross')
def golden_cross_scanner():
    """Run golden cross scanner with caching"""
    try:
        cache_key = 'golden-cross'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting golden cross scanner in background...")
                    
                    try:
                        from us_market_golden_cross_scanner import USMarketScanner
                        scanner = USMarketScanner()
                        
                        # Limit to most popular liquid stocks to avoid rate limits
                        limited_sectors = {
                            'Technology': ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'AMD', 'INTC', 'CSCO', 'ORCL'],
                            'Financial': ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'V', 'MA', 'C', 'AXP', 'BLK'],
                            'Healthcare': ['UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'PFE', 'TMO', 'ABT'],
                            'Consumer': ['WMT', 'HD', 'NKE', 'MCD', 'SBUX', 'COST', 'TGT']
                        }
                        
                        # Override to use limited list
                        scanner.get_all_us_stocks = lambda: limited_sectors
                        
                        # Scan with retries and lower threshold
                        sector_results, all_results = scanner.scan_market()
                        
                        # Flatten sector results into a single list
                        results = []
                        if sector_results:
                            for sector, df in sector_results.items():
                                if not df.empty:
                                    # Convert DataFrame to dict
                                    for _, row in df.iterrows():
                                        try:
                                            record = {
                                                'symbol': str(row.get('Ticker', '')),
                                                'company': str(row.get('Company', '')),
                                                'sector': sector,
                                                'price': float(row.get('Price', 0)),
                                                'rsi': float(row.get('RSI', 50)),
                                                'volume': int(row.get('Volume', 0)),
                                                'score': int(row.get('Score', 0)),
                                                'ema_50': float(row.get('50 EMA', 0)),
                                                'ema_200': float(row.get('200 EMA', 0)),
                                                'entry_price': float(row.get('Entry', row.get('Price', 0))),
                                                'stop_loss': float(row.get('Stop Loss', 0)),
                                                'target': float(row.get('Take Profit', 0)),
                                                'risk': float(row.get('Risk', 0)),
                                                'reward': float(row.get('Reward', 0)),
                                                'risk_reward': float(row.get('Risk/Reward', 5)),
                                                'market_cap': int(row.get('Market Cap', 0)),
                                                'golden_cross': True,
                                                'recommendation': 'BUY',
                                                'type': 'STOCK'
                                            }
                                            results.append(record)
                                        except Exception as e:
                                            print(f"Error parsing row: {e}")
                                            continue
                        
                        # Sort by score and limit to top 20
                        if results:
                            results = clean_nan_values(results)
                            results = sorted(results, key=lambda x: x.get('score', 0), reverse=True)[:20]
                                
                    except ImportError:
                        print("❌ Golden cross scanner module not available")
                        results = []
                    except Exception as scan_error:
                        print(f"❌ Scan error: {scan_error}")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Golden cross scanner complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Golden cross scanner error: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/triple-confirmation')
def triple_confirmation_scanner():
    """Run triple confirmation scanner with caching (default swing trades)"""
    try:
        cache_key = 'triple-confirmation'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting triple confirmation scanner in background...")
                    
                    if TripleConfirmationScanner:
                        scanner = TripleConfirmationScanner()
                        
                        # Limit to top liquid stocks to avoid rate limits
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'AMD', 'INTC', 'CRM',
                            'JPM', 'BAC', 'GS', 'MS', 'WFC', 'C', 'V', 'MA', 'AXP',
                            'UNH', 'JNJ', 'PFE', 'ABBV', 'TMO', 'MRK', 'LLY',
                            'WMT', 'HD', 'NKE', 'MCD', 'SBUX', 'TGT', 'COST'
                        ]
                        scanner.universe = filter_valid_symbols(limited_universe)
                        
                        scanner.scan_market()  # Populates scanner.results
                        
                        if scanner.results:
                            results = scanner.results[:20]  # Top 20
                            # Normalize field names: ticker -> symbol
                            for item in results:
                                if 'ticker' in item and 'symbol' not in item:
                                    item['symbol'] = item['ticker']
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No triple confirmation signals found")
                            results = []
                    else:
                        print("⚠️ Triple confirmation scanner not available")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Triple confirmation scanner complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Triple confirmation scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/triple-intraday')
def triple_confirmation_intraday():
    """Run triple confirmation intraday scanner with caching"""
    try:
        cache_key = 'triple-intraday'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting triple confirmation intraday scanner in background...")
                    
                    if TripleConfirmationIntraday:
                        scanner = TripleConfirmationIntraday()
                        
                        # Limit to most active intraday stocks
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN',
                            'SPY', 'QQQ', 'IWM', 'DIA',
                            'JPM', 'BAC', 'WFC', 'GS',
                            'NFLX', 'PLTR', 'SOFI', 'RIVN', 'NIO'
                        ]
                        scanner.universe = filter_valid_symbols(limited_universe)
                        
                        scanner.scan_market()  # Populates scanner.results
                        
                        if scanner.results:
                            results = scanner.results[:20]  # Top 20
                            # Normalize field names: ticker -> symbol
                            for item in results:
                                if 'ticker' in item and 'symbol' not in item:
                                    item['symbol'] = item['ticker']
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No intraday triple confirmation signals found")
                            results = []
                    else:
                        print("⚠️ Triple confirmation intraday scanner not available")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Triple confirmation intraday complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Triple confirmation intraday error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/triple-positional')
def triple_confirmation_positional():
    """Run triple confirmation positional scanner with caching"""
    try:
        cache_key = 'triple-positional'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting triple confirmation positional scanner in background...")
                    
                    if TripleConfirmationPositional:
                        scanner = TripleConfirmationPositional()
                        
                        # Limit to stable blue-chip stocks for positional trades
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'ORCL', 'ADBE', 'CRM',
                            'JPM', 'BAC', 'WFC', 'V', 'MA', 'GS', 'MS',
                            'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'PFE', 'TMO',
                            'WMT', 'HD', 'COST', 'NKE', 'MCD', 'SBUX',
                            'XOM', 'CVX', 'COP'
                        ]
                        scanner.universe = filter_valid_symbols(limited_universe)
                        
                        scanner.scan_market()  # Populates scanner.results
                        
                        if scanner.results:
                            results = scanner.results[:20]  # Top 20
                            # Normalize field names: ticker -> symbol
                            for item in results:
                                if 'ticker' in item and 'symbol' not in item:
                                    item['symbol'] = item['ticker']
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No positional triple confirmation signals found")
                            results = []
                    else:
                        print("⚠️ Triple confirmation positional scanner not available")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Triple confirmation positional complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Triple confirmation positional error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/triple-confirmation-all')
def triple_confirmation_all():
    """Run all three Triple Confirmation scanners and return combined results"""
    try:
        from triple_confirmation_scanner import TripleConfirmationScanner
        from triple_confirmation_intraday import TripleConfirmationIntraday
        from triple_confirmation_positional import TripleConfirmationPositional
        
        cache_key = 'triple-confirmation-all'
        cache_entry = scanner_cache[cache_key]
        
        # Check if scan is already running
        if cache_entry['running']:
            if cache_entry['data'] is not None:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'swing': cache_entry['data'].get('swing', []),
                    'intraday': cache_entry['data'].get('intraday', []),
                    'positional': cache_entry['data'].get('positional', []),
                    'scanning': True,
                    'message': 'Scanner still running. Showing partial results.'
                })
            else:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'swing': [],
                    'intraday': [],
                    'positional': [],
                    'scanning': True,
                    'message': 'Scanner running in background. Please wait...'
                })
        
        # Check cache (5 minutes)
        if cache_entry['data'] is not None and cache_entry['timestamp']:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:  # 5 minutes
                return jsonify({
                    'success': True,
                    'timestamp': cache_entry['timestamp'].isoformat(),
                    'swing': cache_entry['data'].get('swing', []),
                    'intraday': cache_entry['data'].get('intraday', []),
                    'positional': cache_entry['data'].get('positional', []),
                    'cached': True
                })
        
        # Run all three scanners in background
        def run_all_scans():
            try:
                scanner_cache[cache_key]['running'] = True
                
                # Run all three scanners
                swing_scanner = TripleConfirmationScanner()
                intraday_scanner = TripleConfirmationIntraday()
                positional_scanner = TripleConfirmationPositional()
                
                swing_results = []
                intraday_results = []
                positional_results = []
                
                # Scan swing trades
                try:
                    swing_data = swing_scanner.scan_market()
                    if swing_data:  # scan_market returns a list, not DataFrame
                        swing_results = swing_data[:20]  # Top 20
                        # Normalize field names: ticker -> symbol
                        for item in swing_results:
                            if 'ticker' in item and 'symbol' not in item:
                                item['symbol'] = item['ticker']
                        swing_results = clean_nan_values(swing_results)
                except Exception as e:
                    print(f"Error in swing scanner: {e}")
                
                # Scan intraday
                try:
                    intraday_data = intraday_scanner.scan_market()
                    if intraday_data:  # scan_market returns a list, not DataFrame
                        intraday_results = intraday_data[:20]  # Top 20
                        # Normalize field names: ticker -> symbol
                        for item in intraday_results:
                            if 'ticker' in item and 'symbol' not in item:
                                item['symbol'] = item['ticker']
                        intraday_results = clean_nan_values(intraday_results)
                except Exception as e:
                    print(f"Error in intraday scanner: {e}")
                
                # Scan positional
                try:
                    positional_data = positional_scanner.scan_market()
                    if positional_data:  # scan_market returns a list, not DataFrame
                        positional_results = positional_data[:20]  # Top 20
                        # Normalize field names: ticker -> symbol
                        for item in positional_results:
                            if 'ticker' in item and 'symbol' not in item:
                                item['symbol'] = item['ticker']
                        positional_results = clean_nan_values(positional_results)
                except Exception as e:
                    print(f"Error in positional scanner: {e}")
                
                # Store combined results
                combined_data = {
                    'swing': swing_results,
                    'intraday': intraday_results,
                    'positional': positional_results
                }
                
                scanner_cache[cache_key]['data'] = combined_data
                scanner_cache[cache_key]['timestamp'] = datetime.now()
                
            except Exception as e:
                print(f"Error in triple confirmation all: {e}")
                scanner_cache[cache_key]['data'] = {'swing': [], 'intraday': [], 'positional': []}
            finally:
                scanner_cache[cache_key]['running'] = False
        
        threading.Thread(target=run_all_scans, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'swing': cache_entry['data'].get('swing', []),
                'intraday': cache_entry['data'].get('intraday', []),
                'positional': cache_entry['data'].get('positional', []),
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'swing': [],
                'intraday': [],
                'positional': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/volume-spike')
def volume_spike_scanner():
    """Scan for stocks with unusual volume and price spikes (like VERO)"""
    try:
        cache_key = 'volume-spike'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if market is open (9:30 AM - 4:00 PM ET, weekdays, excluding holidays)
        import pytz
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        is_weekday = now_et.weekday() < 5  # Monday=0, Friday=4
        is_market_hours = (now_et.hour == 9 and now_et.minute >= 30) or (10 <= now_et.hour < 16)
        
        # Check for major US holidays (2026 stock market holidays)
        us_holidays_2026 = [
            (1, 1),   # New Year's Day
            (1, 19),  # MLK Day (3rd Monday of Jan 2026)
            (2, 16),  # Presidents Day
            (4, 3),   # Good Friday
            (5, 25),  # Memorial Day
            (6, 19),  # Juneteenth
            (7, 3),   # Independence Day observed
            (9, 7),   # Labor Day
            (11, 26), # Thanksgiving
            (12, 25), # Christmas
        ]
        is_holiday = (now_et.month, now_et.day) in us_holidays_2026
        market_open = is_weekday and is_market_hours and not is_holiday
        
        # Check cache
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            try:
                cache_ts = cache_entry['timestamp']
                if cache_ts.tzinfo is not None:
                    cache_ts = cache_ts.replace(tzinfo=None)
                age = (datetime.now() - cache_ts).total_seconds()
            except:
                age = 999  # Force refresh on error
            if age < 60:  # 1 minute cache for real-time detection
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age),
                    'market_open': market_open
                })
        
        # Start background scan
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print("🔄 Volume spike scanner starting...")
                    
                    results = []
                    all_movers = []  # Fallback list for when no volume spikes found
                    # Expanded watchlist - high-growth stocks, small caps, and volatile names
                    # Using list(set()) to remove duplicates
                    watchlist = list(set([
                        # High-growth small caps
                        'VERO', 'MARA', 'RIOT', 'IONQ', 'QUBT', 'RGTI', 'SOFI', 'HOOD', 
                        'LCID', 'RIVN', 'NIO', 'XPEV', 'LI',
                        'BLNK', 'CHPT', 'EVGO', 'OPEN', 'GLBE',
                        'ENVX', 'QS', 'LAZR', 'RKLB', 'LUNR', 'MAXN',
                        'PLTR', 'AI', 'BBAI', 'SOUN', 'LUNR', 'ASTS',
                        # Popular volatile stocks
                        'AMD', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 'NFLX', 'UBER',
                        'LYFT', 'SNAP', 'SPOT', 'ZM', 'DOCU', 'TWLO', 'SNOW', 'DDOG',
                        'NET', 'CRWD', 'ZS', 'OKTA', 'MDB', 'TEAM', 'COIN',
                        'ROKU', 'ABNB', 'DASH', 'SHOP', 'PINS', 'DKNG', 'PENN', 'BABA',
                        # Meme/Reddit stocks
                        'GME', 'AMC', 'BB', 'NOK',
                        # Crypto/blockchain
                        'MSTR', 'HUT', 'BITF', 'CLSK',
                        # EV sector
                        'JOBY',
                        # Biotech/pharma volatile
                        'MRNA', 'BNTX', 'CRIS', 'EDIT', 'CRSP'
                    ]))
                    
                    for ticker in watchlist:
                        try:
                            hist = cached_get_history(ticker, period='5d', interval='1d')
                            
                            # Skip symbols with insufficient data
                            if hist is None or hist.empty or len(hist) < 3:
                                if ticker not in KNOWN_DELISTED and not _is_rate_limited(ticker):
                                    print(f"[VolSpike] {ticker}: insufficient data")
                                continue
                            
                            # Calculate metrics
                            current_price = hist['Close'].iloc[-1]
                            
                            # Skip if price is invalid (0 or None)
                            if current_price is None or current_price <= 0:
                                print(f"[VolSpike] {ticker}: invalid price")
                                continue
                            prev_close = hist['Close'].iloc[-2]
                            price_change_pct = ((current_price - prev_close) / prev_close) * 100
                            
                            current_volume = hist['Volume'].iloc[-1]
                            avg_volume = hist['Volume'].iloc[:-1].mean()
                            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
                            
                            # Collect ALL stocks with valid data for fallback
                            try:
                                info = cached_get_ticker_info(ticker)
                                if not info or info.get('regularMarketPrice') is None:
                                    continue
                            except:
                                continue
                            
                            stock_data = {
                                'symbol': ticker,
                                'price': float(current_price),
                                'price_change_pct': float(price_change_pct),
                                'volume': int(current_volume),
                                'avg_volume': int(avg_volume),
                                'volume_ratio': float(volume_ratio),
                                'market_cap': info.get('marketCap', 0),
                                'company_name': info.get('shortName', ticker),
                                'sector': info.get('sector', 'Unknown'),
                                'direction': 'BULLISH' if price_change_pct > 0 else 'BEARISH'
                            }
                            
                            # Check if qualifies for volume spike (strict criteria)
                            if volume_ratio >= 1.5 and abs(price_change_pct) >= 3.0:
                                results.append(stock_data)
                            # Also track all movers with significant price change (fallback)
                            elif abs(price_change_pct) >= 2.0:
                                all_movers.append(stock_data)
                                
                        except Exception as e:
                            print(f"Error scanning {ticker}: {e}")
                            continue
                    
                    # If no volume spikes found, use top movers by price change as fallback
                    if len(results) == 0 and len(all_movers) > 0:
                        print("📊 No volume spikes found, showing top price movers instead")
                        # Sort by absolute price change
                        all_movers.sort(key=lambda x: abs(x['price_change_pct']), reverse=True)
                        results = all_movers[:15]  # Top 15 movers
                    
                    # Sort by direction (BULLISH first), then volume ratio (highest spikes first)
                    results.sort(key=lambda x: (0 if x['direction'] == 'BULLISH' else 1, -abs(x['price_change_pct'])))
                    results = results[:20]  # Top 20
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Volume spike scanner complete! Found {len(results)} stocks")
                    
                except Exception as e:
                    print(f"❌ Volume spike scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Refresh in 30 seconds.'
            })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# ETF SCANNER - Stocks, Commodities, Crypto ETFs
# ============================================================================

# ETF Universe categorized by type
ETF_UNIVERSE = {
    'Stock Index ETFs': {
        'SPY': 'S&P 500',
        'QQQ': 'NASDAQ 100',
        'DIA': 'Dow Jones',
        'IWM': 'Russell 2000',
        'VTI': 'Total Stock Market',
        'VOO': 'Vanguard S&P 500',
        'VUG': 'Vanguard Growth',
        'VTV': 'Vanguard Value',
        'ARKK': 'ARK Innovation',
        'ARKG': 'ARK Genomics',
        'ARKW': 'ARK Internet',
        'ARKF': 'ARK Fintech'
    },
    'Sector ETFs': {
        'XLK': 'Technology',
        'XLF': 'Financials',
        'XLV': 'Healthcare',
        'XLE': 'Energy',
        'XLI': 'Industrials',
        'XLP': 'Consumer Staples',
        'XLY': 'Consumer Discretionary',
        'XLB': 'Materials',
        'XLRE': 'Real Estate',
        'XLC': 'Communication',
        'XLU': 'Utilities',
        'SMH': 'Semiconductors',
        'XBI': 'Biotech',
        'KRE': 'Regional Banks'
    },
    'Commodity ETFs': {
        'GLD': 'Gold',
        'SLV': 'Silver',
        'USO': 'Oil',
        'UNG': 'Natural Gas',
        'DBC': 'Commodities Basket',
        'DBA': 'Agriculture',
        'PDBC': 'Diversified Commodities',
        'CORN': 'Corn',
        'WEAT': 'Wheat',
        'SOYB': 'Soybeans',
        'CPER': 'Copper',
        'URA': 'Uranium'
    },
    'Crypto ETFs': {
        'BITO': 'Bitcoin Futures',
        'GBTC': 'Grayscale Bitcoin',
        'ETHE': 'Grayscale Ethereum',
        'BLOK': 'Blockchain Companies',
        'BTCW': 'Bitcoin Strategy',
        'BITQ': 'Crypto Industry',
        'IBIT': 'iShares Bitcoin',
        'FBTC': 'Fidelity Bitcoin',
        'ARKB': 'ARK Bitcoin'
    }
}

@app.route('/api/scanner/etf-scanner')
def etf_scanner():
    """Scan ETFs across Stocks, Commodities, and Crypto categories"""
    try:
        cache_key = 'etf-scanner'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check cache
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            try:
                cache_ts = cache_entry['timestamp']
                if cache_ts.tzinfo is not None:
                    cache_ts = cache_ts.replace(tzinfo=None)
                age = (datetime.now() - cache_ts).total_seconds()
            except:
                age = 999
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print("🔄 ETF Scanner starting...")
                    
                    results = []
                    
                    for category, etfs in ETF_UNIVERSE.items():
                        for symbol, name in etfs.items():
                            try:
                                hist = cached_get_history(symbol, period='3mo', interval='1d')
                                
                                if hist is None or hist.empty or len(hist) < 20:
                                    continue
                                
                                current_price = hist['Close'].iloc[-1]
                                prev_close = hist['Close'].iloc[-2]
                                price_change = current_price - prev_close
                                price_change_pct = (price_change / prev_close) * 100
                                
                                # Calculate indicators
                                hist['SMA20'] = hist['Close'].rolling(20).mean()
                                hist['SMA50'] = hist['Close'].rolling(50).mean() if len(hist) >= 50 else hist['Close'].rolling(20).mean()
                                hist['EMA9'] = hist['Close'].ewm(span=9, adjust=False).mean()
                                hist['EMA21'] = hist['Close'].ewm(span=21, adjust=False).mean()
                                
                                # RSI
                                delta = hist['Close'].diff()
                                gain = delta.where(delta > 0, 0).rolling(14).mean()
                                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                                rs = gain / loss
                                hist['RSI'] = 100 - (100 / (1 + rs))
                                
                                # ATR
                                hist['ATR'] = (hist['High'] - hist['Low']).rolling(14).mean()
                                
                                # Volume analysis
                                avg_volume = hist['Volume'].rolling(20).mean().iloc[-1]
                                current_volume = hist['Volume'].iloc[-1]
                                volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
                                
                                # Get latest values
                                sma20 = hist['SMA20'].iloc[-1]
                                sma50 = hist['SMA50'].iloc[-1]
                                ema9 = hist['EMA9'].iloc[-1]
                                ema21 = hist['EMA21'].iloc[-1]
                                rsi = hist['RSI'].iloc[-1]
                                atr = hist['ATR'].iloc[-1]
                                
                                # 52-week high/low
                                high_52w = hist['High'].max()
                                low_52w = hist['Low'].min()
                                pct_from_high = ((high_52w - current_price) / high_52w) * 100
                                pct_from_low = ((current_price - low_52w) / low_52w) * 100
                                
                                # Score calculation (0-15)
                                score = 0
                                signals = []
                                
                                # Trend signals
                                if current_price > sma20:
                                    score += 2
                                    signals.append("Above SMA20")
                                if current_price > sma50:
                                    score += 2
                                    signals.append("Above SMA50")
                                if ema9 > ema21:
                                    score += 2
                                    signals.append("EMA9 > EMA21 (Bullish)")
                                
                                # RSI signals
                                if 40 <= rsi <= 60:
                                    score += 2
                                    signals.append("RSI Neutral")
                                elif rsi < 30:
                                    score += 3
                                    signals.append("RSI Oversold (<30)")
                                elif rsi > 70:
                                    score -= 1
                                    signals.append("RSI Overbought (>70)")
                                
                                # Volume signal
                                if volume_ratio > 1.5:
                                    score += 2
                                    signals.append(f"High Volume ({volume_ratio:.1f}x)")
                                
                                # Momentum
                                if price_change_pct > 1:
                                    score += 2
                                    signals.append(f"Strong Momentum (+{price_change_pct:.1f}%)")
                                elif price_change_pct < -1:
                                    score -= 1
                                
                                # Near 52-week high/low
                                if pct_from_high < 5:
                                    score += 1
                                    signals.append("Near 52W High")
                                if pct_from_low < 10:
                                    score += 1
                                    signals.append("Near 52W Low (Potential Bounce)")
                                
                                # Determine direction
                                if score >= 8:
                                    direction = 'BULLISH'
                                elif score <= 3:
                                    direction = 'BEARISH'
                                else:
                                    direction = 'NEUTRAL'
                                
                                results.append({
                                    'symbol': symbol,
                                    'name': f"{name} ({category})",
                                    'category': category,
                                    'price': round(current_price, 2),
                                    'change': round(price_change, 2),
                                    'change_pct': round(price_change_pct, 2),
                                    'score': max(0, min(15, score)),
                                    'direction': direction,
                                    'rsi': round(rsi, 1) if not np.isnan(rsi) else None,
                                    'sma20': round(sma20, 2),
                                    'sma50': round(sma50, 2),
                                    'ema9': round(ema9, 2),
                                    'ema21': round(ema21, 2),
                                    'atr': round(atr, 2),
                                    'volume': int(current_volume),
                                    'volume_ratio': round(volume_ratio, 2),
                                    'high_52w': round(high_52w, 2),
                                    'low_52w': round(low_52w, 2),
                                    'pct_from_high': round(pct_from_high, 1),
                                    'pct_from_low': round(pct_from_low, 1),
                                    'signals': signals
                                })
                                
                                # Small delay to avoid rate limiting
                                import time
                                time.sleep(0.1)
                                
                            except Exception as e:
                                print(f"⚠️ Error scanning {symbol}: {e}")
                                continue
                    
                    # Sort by score descending
                    results.sort(key=lambda x: (-x['score'], x['symbol']))
                    
                    # Clean NaN values
                    results = clean_nan_values(results)
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ ETF Scanner complete! Found {len(results)} ETFs")
                    
                except Exception as e:
                    print(f"❌ ETF Scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'ETF Scanner running in background. Refresh in 30 seconds.'
            })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def generate_trade_recommendation(data, support_levels, resistance_levels):
    """Generate buy/sell recommendation with targets based on trend analysis"""
    current_price = data['price']
    rsi = data.get('rsi', 50)
    ema9 = data.get('ema9', current_price)
    ema21 = data.get('ema21', current_price)
    sma20 = data.get('sma20', current_price)
    sma50 = data.get('sma50', current_price)
    atr = data.get('atr', current_price * 0.02)
    
    # Determine trend
    bullish_signals = 0
    bearish_signals = 0
    
    # EMA trend
    if ema9 > ema21:
        bullish_signals += 1
    else:
        bearish_signals += 1
    
    # Price vs SMAs
    if current_price > sma20:
        bullish_signals += 1
    else:
        bearish_signals += 1
        
    if current_price > sma50:
        bullish_signals += 1
    else:
        bearish_signals += 1
    
    # RSI momentum
    if 40 <= rsi <= 60:
        bullish_signals += 1
    elif rsi < 30:
        bullish_signals += 1  # Oversold bounce
    elif rsi > 70:
        bearish_signals += 1  # Overbought
    
    # Generate recommendation
    recommendation = {}
    
    if bullish_signals >= 3:
        # BUY recommendation
        recommendation['recommendation'] = 'BUY'
        recommendation['recommendation_color'] = 'green'
        recommendation['entry_price'] = current_price
        
        # Stop Loss: Tight stop below entry (1.5% or 1.5*ATR, whichever is tighter)
        # For BUY, we want to limit loss if price goes DOWN against us
        atr_based_stop = current_price - (atr * 1.5)
        percentage_based_stop = current_price * 0.985  # 1.5% below entry
        recommendation['stop_loss'] = float(max(atr_based_stop, percentage_based_stop))
        
        # Targets: Only resistances ABOVE current price (going UP)
        # Must be at least 1% above to be valid targets
        resistances_above = [r for r in resistance_levels if r > current_price * 1.01]
        
        if len(resistances_above) >= 3:
            # Sort ascending to get nearest first
            resistances_above_sorted = sorted(resistances_above)
            recommendation['target_1'] = float(resistances_above_sorted[0])  # Nearest
            recommendation['target_2'] = float(resistances_above_sorted[1])  # Middle
            recommendation['target_3'] = float(resistances_above_sorted[2])  # Furthest
        elif len(resistances_above) >= 2:
            resistances_above_sorted = sorted(resistances_above)
            recommendation['target_1'] = float(resistances_above_sorted[0])
            recommendation['target_2'] = float(resistances_above_sorted[1])
            recommendation['target_3'] = float(current_price * 1.12)  # Fallback
        elif len(resistances_above) >= 1:
            recommendation['target_1'] = float(resistances_above[0])
            recommendation['target_2'] = float(current_price * 1.08)  # Fallback
            recommendation['target_3'] = float(current_price * 1.12)  # Fallback
        else:
            # No resistances above - use percentage targets
            recommendation['target_1'] = float(current_price * 1.05)
            recommendation['target_2'] = float(current_price * 1.08)
            recommendation['target_3'] = float(current_price * 1.12)
        
        # Risk/Reward ratios
        risk = current_price - recommendation['stop_loss']
        reward_1 = recommendation['target_1'] - current_price
        recommendation['risk_reward_1'] = round(reward_1 / risk, 2) if risk > 0 else 0
        
        stop_pct = ((current_price - recommendation['stop_loss']) / current_price) * 100
        recommendation['recommendation_reason'] = f"Bullish trend ({bullish_signals}/4 signals). Tight stop at {stop_pct:.1f}% below entry"
        
    elif bearish_signals >= 3:
        # SELL recommendation
        recommendation['recommendation'] = 'SELL'
        recommendation['recommendation_color'] = 'red'
        recommendation['entry_price'] = current_price
        
        # Stop Loss: Tight stop above entry (1.5% or 1.5*ATR, whichever is tighter)
        # For SELL, we want to limit loss if price goes UP against us
        atr_based_stop = current_price + (atr * 1.5)
        percentage_based_stop = current_price * 1.015  # 1.5% above entry
        recommendation['stop_loss'] = float(min(atr_based_stop, percentage_based_stop))
        
        # Targets: Only supports BELOW current price (going DOWN)
        # Must be at least 1% below to be valid targets
        supports_below = [s for s in support_levels if s < current_price * 0.99]
        
        if len(supports_below) >= 3:
            # Sort descending to get nearest first
            supports_below_sorted = sorted(supports_below, reverse=True)
            recommendation['target_1'] = float(supports_below_sorted[0])  # Nearest (highest support below)
            recommendation['target_2'] = float(supports_below_sorted[1])  # Middle
            recommendation['target_3'] = float(supports_below_sorted[2])  # Furthest (lowest support)
        elif len(supports_below) >= 2:
            supports_below_sorted = sorted(supports_below, reverse=True)
            recommendation['target_1'] = float(supports_below_sorted[0])
            recommendation['target_2'] = float(supports_below_sorted[1])
            recommendation['target_3'] = float(current_price * 0.88)  # Fallback
        elif len(supports_below) >= 1:
            recommendation['target_1'] = float(supports_below[0])
            recommendation['target_2'] = float(current_price * 0.92)  # Fallback
            recommendation['target_3'] = float(current_price * 0.88)  # Fallback
        else:
            # No supports below - use percentage targets
            recommendation['target_1'] = float(current_price * 0.95)
            recommendation['target_2'] = float(current_price * 0.92)
            recommendation['target_3'] = float(current_price * 0.88)
        
        # Risk/Reward ratios
        risk = recommendation['stop_loss'] - current_price
        reward_1 = current_price - recommendation['target_1']
        recommendation['risk_reward_1'] = round(reward_1 / risk, 2) if risk > 0 else 0
        
        stop_pct = ((recommendation['stop_loss'] - current_price) / current_price) * 100
        recommendation['recommendation_reason'] = f"Bearish trend ({bearish_signals}/4 signals). Tight stop at {stop_pct:.1f}% above entry"
        
    else:
        # HOLD - No clear trend
        recommendation['recommendation'] = 'HOLD'
        recommendation['recommendation_color'] = 'gray'
        recommendation['recommendation_reason'] = f"Mixed signals (Bullish: {bullish_signals}, Bearish: {bearish_signals}). Wait for clearer trend"
        recommendation['entry_price'] = None
        recommendation['stop_loss'] = None
        recommendation['target_1'] = None
        recommendation['target_2'] = None
        recommendation['target_3'] = None
        recommendation['risk_reward_1'] = None
    
    return recommendation

@app.route('/api/scanner/custom-analyzer', methods=['POST'])
def custom_analyzer():
    """Analyze custom stock list with support/resistance levels"""
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        if not symbols:
            return jsonify({'success': False, 'error': 'No symbols provided'}), 400
        # Resolve each symbol or name to a ticker symbol
        resolved_symbols = []
        for s in symbols[:20]:
            resolved = resolve_symbol_or_name(s)
            if resolved:
                resolved_symbols.append(resolved)
            else:
                print(f"[custom_analyzer] Could not resolve '{s}' to a valid symbol.")
        if not resolved_symbols:
            return jsonify({'success': False, 'error': 'No valid symbols found'}), 400
        # Use unified system for analysis
        system = UnifiedTradingSystem()
        results = []
        
        def analyze_with_support_resistance(symbol):
            """Analyze stock and calculate support/resistance"""
            try:
                result = system.get_live_data(symbol)
                if not result:
                    return {
                        'symbol': symbol,
                        'error': True,
                        'error_message': f'⚠️ {symbol} appears to be delisted or has no available market data. Please verify the ticker symbol.'
                    }
                
                # Calculate score using correct method
                score, signals = system.score_asset(result, 'STOCK')
                result['score'] = score
                result['signals'] = signals
                
                # Calculate support and resistance levels
                hist = cached_get_history(symbol, period='3mo', interval='1d')
                
                if len(hist) >= 20:
                    current_price = result['price']
                    
                    # Support levels - only use lows that are BELOW current price
                    # Find supports within reasonable range (not more than 20% below)
                    all_lows = hist['Low'].tail(60)
                    valid_supports = all_lows[(all_lows < current_price * 0.99) & (all_lows > current_price * 0.80)]
                    
                    if len(valid_supports) >= 3:
                        support_levels = sorted(valid_supports.nsmallest(5).unique())[:3]
                    else:
                        # Use percentage-based supports if not enough historical levels
                        support_levels = [current_price * 0.95, current_price * 0.92, current_price * 0.88]
                    
                    # Resistance levels - only use highs that are ABOVE current price
                    # Find resistances within reasonable range (not more than 20% above)
                    all_highs = hist['High'].tail(60)
                    valid_resistances = all_highs[(all_highs > current_price * 1.01) & (all_highs < current_price * 1.20)]
                    
                    if len(valid_resistances) >= 3:
                        resistance_levels = sorted(valid_resistances.nlargest(5).unique(), reverse=True)[:3]
                    else:
                        # Use percentage-based resistances if not enough historical levels
                        resistance_levels = [current_price * 1.12, current_price * 1.08, current_price * 1.05]
                    
                    result['support_levels'] = [float(s) for s in support_levels]
                    result['resistance_levels'] = [float(r) for r in resistance_levels]
                    
                    # Find nearest support/resistance
                    supports_below = [s for s in support_levels if s < current_price]
                    resistances_above = [r for r in resistance_levels if r > current_price]
                    
                    result['nearest_support'] = float(supports_below[-1]) if supports_below else None
                    result['nearest_resistance'] = float(resistances_above[0]) if resistances_above else None
                    
                    # Calculate distance to nearest levels
                    if result['nearest_support']:
                        result['support_distance_pct'] = ((current_price - result['nearest_support']) / current_price) * 100
                    if result['nearest_resistance']:
                        result['resistance_distance_pct'] = ((result['nearest_resistance'] - current_price) / current_price) * 100
                    
                    # Generate Buy/Sell recommendation based on trend
                    recommendation = generate_trade_recommendation(result, support_levels, resistance_levels)
                    result.update(recommendation)
                else:
                    result['support_levels'] = []
                    result['resistance_levels'] = []
                    result['nearest_support'] = None
                    result['nearest_resistance'] = None
                    result['recommendation'] = 'HOLD'
                    result['recommendation_reason'] = 'Insufficient data'
                
                return result
            except Exception as e:
                print(f"Error analyzing {symbol}: {e}")
                # Check if it's a delisted stock error
                error_msg = str(e).lower()
                if 'delisted' in error_msg or 'no data found' in error_msg:
                    return {
                        'symbol': symbol,
                        'error': True,
                        'error_message': f'⚠️ {symbol} appears to be delisted or has no available market data. Please verify the ticker symbol.'
                    }
                return None
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_symbol = {executor.submit(analyze_with_support_resistance, sym): sym for sym in resolved_symbols}
            for future in as_completed(future_to_symbol):
                try:
                    result = future.result()
                    if result:
                        # Clean NaN values before adding to results
                        cleaned_result = clean_nan_values(result)
                        results.append(cleaned_result)
                except Exception as e:
                    print(f"Error processing future: {e}")
                    continue
        
        # Sort by score
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'stocks': results,
            'count': len(results)
        })
            
    except Exception as e:
        print(f"Custom analyzer error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================================
# POSITIONS & MONITORING ENDPOINTS
# ============================================================================

@app.route('/api/positions/active')
def active_positions():
    """Get currently active positions with live prices"""
    try:
        force_live = request.args.get('force_live', '0').lower() in ('1', 'true', 'yes')

        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions_dict = json.load(f)
        else:
            positions_dict = {}
        
        # Calculate stats from all positions
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        closed_pnl = 0
        wins_amounts = []
        losses_amounts = []
        closed_positions_array = []
        
        for key, pos in positions_dict.items():
            if pos.get('status') == 'closed':
                total_trades += 1
                exit_price = pos.get('exit', pos.get('current_price', pos.get('entry', 0)))
                entry_price = pos.get('entry', 0)
                quantity = pos.get('quantity', 1)
                is_option = pos.get('type') == 'option'
                direction = pos.get('direction', 'LONG').upper()

                # Options: multiply by 100 (1 contract = 100 shares)
                multiplier = 100 if is_option else 1

                # Use stored pnl if available, else calculate direction-aware pnl
                if 'pnl' in pos:
                    pnl = float(pos['pnl'])
                elif direction == 'SHORT':
                    # Short: profit when price drops (entry_sold - exit_bought)
                    pnl = (entry_price - exit_price) * quantity * multiplier
                else:
                    pnl = (exit_price - entry_price) * quantity * multiplier
                
                closed_pnl += pnl
                if pnl > 0:
                    winning_trades += 1
                    wins_amounts.append(pnl)
                else:
                    losing_trades += 1
                    losses_amounts.append(abs(pnl))
                
                # Build closed position object
                closed_pos = {
                    'position_key': key,
                    'symbol': pos.get('ticker', key),
                    'type': pos.get('type', 'stock').upper(),
                    'direction': pos.get('direction', 'LONG').upper() if not is_option else pos.get('direction', 'CALL').upper(),
                    'entry_price': float(entry_price),
                    'exit_price': float(exit_price),
                    'quantity': int(quantity),
                    'pnl': float(pnl),
                    'pnl_pct': float(pos.get('pnl_pct', ((entry_price - exit_price) / entry_price * 100 if direction == 'SHORT' else (exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0)),
                    'date_added': pos.get('date_added', ''),
                    'date_closed': pos.get('date_closed', ''),
                    'source': pos.get('source', None),
                    'close_reason': pos.get('close_reason', 'Closed'),
                }
                
                if is_option:
                    closed_pos['strike'] = float(pos.get('strike', 0))
                    closed_pos['expiration'] = pos.get('expiration', '')
                
                closed_positions_array.append(closed_pos)
        
        # Convert dictionary to array and fetch live prices
        active_stock_symbols = []
        for _, p in positions_dict.items():
            if p.get('status') == 'active' and p.get('type') != 'option':
                ticker = p.get('ticker')
                if ticker:
                    active_stock_symbols.append(ticker)
        stock_live_prices = cached_batch_prices(
            list(set(active_stock_symbols)),
            period='1d',
            interval='1m',
            prepost=True,
            use_cache=not force_live
        ) if active_stock_symbols else {}

        positions_array = []
        for key, pos in positions_dict.items():
            # Only include active positions
            if pos.get('status') == 'active':
                # Determine if this is an option
                is_option = pos.get('type') == 'option'
                
                # Initialize premium_source for all positions
                premium_source = 'MARKET'  # Default for stocks
                
                # For options, fetch LIVE option premium from market
                if is_option:
                    entry_price = pos.get('entry', 0)
                    stop_loss = pos.get('stop_loss', 0)
                    target = pos.get('target_3', pos.get('target_2', pos.get('target_1', 0)))
                    
                    # Try to get live option premium
                    current_price = entry_price  # Default fallback
                    premium_source = 'ENTRY'
                    
                    # Only try live fetch if we have valid strike and it's not 0
                    strike = pos.get('strike', 0)
                    expiration = pos.get('expiration', '')
                    if strike > 0 and expiration:
                        try:
                            direction = pos.get('direction', 'CALL')
                            
                            # Get option chain for the position's expiration date
                            opt_chain = cached_get_option_chain(pos['ticker'], expiration, use_cache=not force_live)
                            if opt_chain is None:
                                raise ValueError(f"No option chain for {expiration}")
                            chain = opt_chain.calls if direction.upper() == 'CALL' else opt_chain.puts
                            
                            # Find the matching strike
                            chain['strike_diff'] = abs(chain['strike'] - strike)
                            best_match = chain.loc[chain['strike_diff'].idxmin()]
                            
                            # Use mid price for most accurate current value
                            bid = float(best_match['bid']) if best_match['bid'] > 0 else 0
                            ask = float(best_match['ask']) if best_match['ask'] > 0 else 0
                            
                            if bid > 0 and ask > 0:
                                current_price = (bid + ask) / 2
                                premium_source = 'LIVE'
                            elif best_match['lastPrice'] > 0:
                                current_price = float(best_match['lastPrice'])
                                premium_source = 'LAST'
                        except Exception as e:
                            print(f"Could not fetch live premium for {pos['ticker']} ${strike} {direction} exp {expiration}: {e}")
                            # Keep entry price as fallback
                    else:
                        print(f"⚠️  Skipping live premium for {pos['ticker']} - invalid strike: {strike} or expiration: {expiration}")
                else:
                    # Prefer batched live price; fallback to last known/current entry
                    ticker = pos.get('ticker')
                    if ticker in stock_live_prices:
                        current_price = float(stock_live_prices[ticker])
                    else:
                        current_price = float(pos.get('current_price', pos.get('entry', 0)) or pos.get('entry', 0))
                    
                    entry_price = pos.get('entry', 0)
                    stop_loss = pos.get('stop_loss', 0)
                    # Primary target - use highest available
                    target = pos.get('target_1', pos.get('target', 0))
                
                # Determine direction for stocks (auto-infer if missing)
                if is_option:
                    stock_direction = pos.get('direction', 'CALL').upper()
                else:
                    stock_direction = pos.get('direction', '').upper()
                    if not stock_direction:
                        # Auto-infer: if stop_loss > entry → SHORT, else LONG
                        if stop_loss > entry_price and entry_price > 0:
                            stock_direction = 'SHORT'
                        else:
                            stock_direction = 'LONG'
                
                # Determine if we got a LIVE or CACHED price (vs entry fallback)
                _got_live_price = (
                    (not is_option and pos.get('ticker') in stock_live_prices) or
                    (is_option and premium_source in ('LIVE', 'LAST'))
                )
                
                # Build position object with consistent field names
                position = {
                    'position_key': key,
                    'symbol': pos.get('ticker', key),
                    'type': pos.get('type', 'stock').upper(),
                    'direction': stock_direction,
                    'entry_price': float(entry_price),
                    'current_price': float(current_price),
                    'quantity': int(pos.get('quantity', 1)),
                    'stop_loss': float(stop_loss),
                    'target': float(target),
                    'status': pos.get('status', 'active'),
                    'last_price_update': datetime.now().isoformat() if _got_live_price else pos.get('last_price_update'),
                    'last_checked': datetime.now().isoformat(),
                    'price_update_status': 'updated' if _got_live_price else 'stale',
                    'date_added': pos.get('date_added', ''),
                    'date_closed': pos.get('date_closed', ''),
                    'source': pos.get('source', None),
                    'close_reason': pos.get('close_reason', ''),
                }
                
                # Add option-specific fields or stock multi-targets
                if is_option:
                    position['strike'] = float(pos.get('strike', 0))
                    position['expiration'] = pos.get('expiration', '')
                    position['premium_source'] = premium_source  # NEW: LIVE or ENTRY
                    # Add all premium targets for display
                    position['target_1'] = float(pos.get('target_1', 0))
                    position['target_2'] = float(pos.get('target_2', 0))
                    position['target_3'] = float(pos.get('target_3', 0))
                else:
                    # For stocks, also include multiple targets if they exist
                    if 'target_1' in pos or 'target_2' in pos or 'target_3' in pos:
                        position['target_1'] = float(pos.get('target_1', 0))
                        position['target_2'] = float(pos.get('target_2', 0))
                        position['target_3'] = float(pos.get('target_3', 0))
                
                positions_array.append(position)
        
        # Calculate stats
        avg_win = sum(wins_amounts) / len(wins_amounts) if wins_amounts else 0
        avg_loss = sum(losses_amounts) / len(losses_amounts) if losses_amounts else 0
        total_wins = sum(wins_amounts) if wins_amounts else 0
        total_losses = sum(losses_amounts) if losses_amounts else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else (float('inf') if total_wins > 0 else 0)
        largest_win = max(wins_amounts) if wins_amounts else 0
        
        stats = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'closed_pnl': closed_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor if profit_factor != float('inf') else 999.99,
            'largest_win': largest_win
        }
        
        response = jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'positions': positions_array,
            'closed_positions': closed_positions_array,
            'stats': stats,
            'count': len(positions_array),
            'force_live': force_live
        })
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        print(f"Error in active_positions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/positions/reload', methods=['POST'])
def reload_positions():
    """Force reload/sync `active_positions.json` and notify connected clients."""
    try:
        if not os.path.exists('active_positions.json'):
            return jsonify({'success': False, 'error': 'active_positions.json not found'}), 404

        with open('active_positions.json', 'r') as f:
            positions = json.load(f)

        # Notify connected UIs to refresh
        try:
            socketio.emit('positions_updated', {'count': len(positions)})
        except Exception as e:
            print(f"Warning: could not emit positions_updated: {e}")

        return jsonify({'success': True, 'message': 'Positions reloaded', 'count': len(positions)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/positions/restore', methods=['POST'])
def restore_position_from_backup():
    """Restore a single position from the most recent backup into active_positions.json.
    Request JSON: { 'position_key': 'KEY', 'backup_filename': optional, 'force': optional }
    """
    try:
        data = request.json or {}
        key = data.get('position_key')
        if not key:
            return jsonify({'success': False, 'error': 'position_key required'}), 400

        # Find backup file if not provided
        backup_file = data.get('backup_filename')
        if not backup_file:
            # find latest active_positions.json.bak.* in cwd
            bak_files = [f for f in os.listdir('.') if f.startswith('active_positions.json.bak')]
            if not bak_files:
                return jsonify({'success': False, 'error': 'No backup files found'}), 404
            bak_files.sort()
            backup_file = bak_files[-1]

        if not os.path.exists(backup_file):
            return jsonify({'success': False, 'error': f'Backup file not found: {backup_file}'}), 404

        with open(backup_file, 'r') as f:
            backup_data = json.load(f)

        if key not in backup_data:
            return jsonify({'success': False, 'error': f'Position {key} not found in backup {backup_file}'}), 404

        # Load current positions
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                current = json.load(f)
        else:
            current = {}

        force = bool(data.get('force', False))
        if key in current and current[key].get('status') == 'active' and not force:
            # Don't block restore — create a unique key for restored position to avoid overwriting
            timestamp_suffix = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_key = f"{key}_{timestamp_suffix}"
            restored_entry = backup_data[key]
            # mark restored source
            restored_entry['source'] = restored_entry.get('source', 'restored')
            restored_entry['date_restored_from'] = backup_file
            current[new_key] = restored_entry
            restored_key = new_key
        else:
            # Restore single position entry (overwrite or create)
            current[key] = backup_data[key]
            restored_key = key

        # Save and notify
        with open('active_positions.json', 'w') as f:
            json.dump(current, f, indent=2)

        try:
            socketio.emit('positions_updated', {'restored': restored_key})
        except Exception:
            pass

        return jsonify({'success': True, 'message': f'Restored {restored_key} from {backup_file}', 'restored_key': restored_key}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/add', methods=['POST'])
def add_position():
    """Add new position to monitoring"""
    try:
        data = request.json
        
        # Load existing positions (dict format)
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            positions = {}
        
        # Generate position key
        ticker = data.get('symbol', data.get('ticker', 'UNKNOWN'))
        position_type = data.get('type', 'stock').lower()
        
        if position_type == 'option':
            direction = data.get('direction', 'CALL').upper()
            strike = data.get('strike', 0)
            position_key = f"{ticker}_{direction}_{int(strike)}"
        else:
            position_key = ticker
        
        # Check if position already exists
        if position_key in positions:
            existing_position = positions[position_key]
            if existing_position.get('status') == 'active':
                # ADD TO EXISTING ACTIVE POSITION (weighted average entry)
                # Only merge when allowed or when source matches (avoid bot overriding manual trades)
                incoming_source = data.get('source', 'manual')
                allow_merge = bool(data.get('allow_merge', False))
                existing_source = existing_position.get('source', 'manual')

                if not allow_merge and existing_source != incoming_source:
                    # Conflict: do not merge different-source active positions
                    return jsonify({
                        'success': False,
                        'error': 'Conflict: existing active position from different source. Set allow_merge=true to override.'
                    }), 409

                # Merge allowed: compute weighted average entry
                old_qty = existing_position.get('quantity', 0)
                old_entry = existing_position.get('entry', existing_position.get('entry_premium', 0))
                new_qty = int(data.get('quantity', 1))
                new_entry = float(data.get('entry_price', data.get('entry', 0)))

                total_qty = old_qty + new_qty
                avg_entry = ((old_qty * old_entry) + (new_qty * new_entry)) / total_qty if total_qty > 0 else new_entry

                # Update existing position
                existing_position['quantity'] = total_qty
                existing_position['entry'] = round(avg_entry, 4)
                existing_position['date_added'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Update SL and targets if provided (use new values)
                if data.get('stop_loss') and float(data.get('stop_loss', 0)) > 0:
                    existing_position['stop_loss'] = float(data['stop_loss'])
                if data.get('target_1'):
                    existing_position['target_1'] = float(data['target_1'])
                if data.get('target_2'):
                    existing_position['target_2'] = float(data['target_2'])
                if data.get('target_3'):
                    existing_position['target_3'] = float(data['target_3'])
                
                # Save updated positions
                with open('active_positions.json', 'w') as f:
                    json.dump(positions, f, indent=2)
                
                return jsonify({
                    'success': True,
                    'message': f'Added to existing position: {position_key} (now {total_qty} qty @ ${avg_entry:.2f} avg)',
                    'position_key': position_key,
                    'position': existing_position,
                    'added_to_existing': True
                })
            else:
                # Position exists but is CLOSED - create new key with timestamp
                print(f"🔄 Closed position {position_key} exists. Creating new position with unique key.")
                timestamp_suffix = datetime.now().strftime('%Y%m%d_%H%M%S')
                position_key = f"{position_key}_{timestamp_suffix}"
                print(f"✅ New position key: {position_key}")
        
        # Build new position
        entry_price = float(data.get('entry_price', data.get('entry', 0)))
        
        # Calculate default SL and targets if not provided
        stop_loss = data.get('stop_loss')
        if stop_loss and float(stop_loss) > 0:
            stop_loss = float(stop_loss)
        else:
            # Default: 5% below entry for stocks, 50% for options
            if position_type == 'option':
                stop_loss = entry_price * 0.5
            else:
                stop_loss = entry_price * 0.95
        
        new_position = {
            'ticker': ticker,
            'type': position_type,
            'entry': entry_price,
            # record canonical option naming when relevant
            'entry_premium': entry_price if position_type == 'option' else None,
            'stop_loss': stop_loss,
            'quantity': int(data.get('quantity', 1)),
            'status': 'active',
            'source': data.get('source', 'manual'),
            'date_added': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Add option-specific fields
        if position_type == 'option':
            new_position['direction'] = data.get('direction', 'CALL').upper()
            new_position['strike'] = float(data.get('strike', 0))
            new_position['expiration'] = data.get('expiration', '')
            if 'current_price' in data:
                new_position['current_price'] = float(data['current_price'])
            if 'confidence' in data:
                new_position['confidence'] = int(data['confidence'])
            # Also keep legacy 'entry' field consistent
            new_position['entry'] = entry_price
            new_position['entry_premium'] = entry_price
        else:
            # Stock direction: use explicit value, or infer from SL/target vs entry
            direction = data.get('direction', '').upper()
            if not direction:
                # Auto-infer: if SL > entry → SHORT, if SL < entry → LONG
                if stop_loss > entry_price:
                    direction = 'SHORT'
                else:
                    direction = 'LONG'
            new_position['direction'] = direction
        
        # Add targets (support both single target and multiple targets)
        # Calculate defaults if not provided
        if 'target' in data:
            new_position['target_1'] = float(data['target'])
        if 'target_1' in data:
            new_position['target_1'] = float(data['target_1'])
        if 'target_2' in data:
            new_position['target_2'] = float(data['target_2'])
        if 'target_3' in data:
            new_position['target_3'] = float(data['target_3'])
        
        # Set default targets if none provided
        if 'target_1' not in new_position:
            if position_type == 'option':
                # Options: 2x, 3x, 4x entry
                new_position['target_1'] = entry_price * 2
                new_position['target_2'] = entry_price * 3
                new_position['target_3'] = entry_price * 4
            else:
                # Stocks: 10%, 20%, 30% above entry
                new_position['target_1'] = entry_price * 1.10
                new_position['target_2'] = entry_price * 1.20
                new_position['target_3'] = entry_price * 1.30
        
        # Add optional fields
        if 'notes' in data:
            new_position['notes'] = data['notes']
        
        # Add to positions dict
        positions[position_key] = new_position
        
        # Save positions
        with open('active_positions.json', 'w') as f:
            json.dump(positions, f, indent=2)
        
        return jsonify({
            'success': True,
            'message': f'Position added: {position_key}',
            'position_key': position_key,
            'position': new_position
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/delete/<int:index>', methods=['DELETE'])
def delete_position(index):
    """Delete position by index (legacy support - converts to dict lookup)"""
    try:
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            return jsonify({'success': False, 'error': 'No positions found'}), 404
        
        # Convert to list to support index-based deletion
        if isinstance(positions, dict):
            position_keys = list(positions.keys())
            if 0 <= index < len(position_keys):
                position_key = position_keys[index]
                deleted = positions.pop(position_key)
                
                # Save updated positions
                with open('active_positions.json', 'w') as f:
                    json.dump(positions, f, indent=2)
                
                return jsonify({
                    'success': True,
                    'message': f'Position deleted: {deleted.get("ticker")}',
                    'deleted': deleted
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid position index'}), 400
        else:
            # Legacy list format (shouldn't happen but handle it)
            if 0 <= index < len(positions):
                deleted = positions.pop(index)
                
                # Save updated positions
                with open('active_positions.json', 'w') as f:
                    json.dump(positions, f, indent=2)
                
                return jsonify({
                    'success': True,
                    'message': f'Position deleted: {deleted.get("symbol")}',
                    'deleted': deleted
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid position index'}), 400
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/delete/<position_key>', methods=['DELETE'])
def delete_position_by_key(position_key):
    """Delete position by key"""
    try:
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            return jsonify({'success': False, 'error': 'No positions found'}), 404
        
        if position_key in positions:
            deleted = positions.pop(position_key)
            
            # Save updated positions
            with open('active_positions.json', 'w') as f:
                json.dump(positions, f, indent=2)
            
            return jsonify({
                'success': True,
                'message': f'Position deleted: {deleted.get("ticker")}',
                'deleted': deleted
            })
        else:
            return jsonify({'success': False, 'error': 'Position not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/positions/close', methods=['POST'])
def close_position():
    """Close position at specified price"""
    try:
        data = request.json
        position_key = data.get('position_key')
        exit_price = float(data.get('exit_price', 0))
        reason = data.get('reason', 'Manual Close')
        
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            return jsonify({'success': False, 'error': 'No positions found'}), 404
        
        if position_key not in positions:
            return jsonify({'success': False, 'error': 'Position not found'}), 404
        
        position = positions[position_key]
        
        # Calculate P&L
        # Support legacy naming: entry or entry_premium
        entry_price = position.get('entry', position.get('entry_premium', 0))
        quantity = position.get('quantity', 1)
        is_option = position.get('type') == 'option'
        
        # Options: multiply by 100 (1 contract = 100 shares)
        multiplier = 100 if is_option else 1
        pnl = (exit_price - entry_price) * quantity * multiplier
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        
        # Update position to closed
        position['status'] = 'closed'
        position['exit'] = exit_price
        # Write secondary naming for option workflows
        if position.get('type') == 'option':
            position['exit_premium'] = exit_price
            # Ensure entry_premium exists for downstream tools
            position['entry_premium'] = position.get('entry_premium', position.get('entry'))
        position['date_closed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        position['close_reason'] = reason
        position['pnl'] = pnl
        position['pnl_pct'] = pnl_pct
        
        positions[position_key] = position
        
        # Save updated positions
        with open('active_positions.json', 'w') as f:
            json.dump(positions, f, indent=2)
        
        return jsonify({
            'success': True,
            'message': f'Position closed: {position.get("ticker")}',
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'position': position
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/status')
def scanner_status():
    """Get status of all scanners"""
    try:
        status = {}
        for key, cache_entry in scanner_cache.items():
            status[key] = {
                'running': cache_entry['running'],
                'has_data': cache_entry['data'] is not None,
                'data_age_seconds': None
            }
            if cache_entry['timestamp'] is not None:
                age = (datetime.now() - cache_entry['timestamp']).total_seconds()
                status[key]['data_age_seconds'] = int(age)
                status[key]['cache_valid'] = age < scanner_cache_timeout
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'scanners': status
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scanner/trigger-all', methods=['POST'])
def trigger_all_scanners():
    """Manually trigger all scanners to run in background"""
    try:
        # Trigger each scanner by calling their endpoints
        triggered = []
        for scanner_type in ['unified', 'short-squeeze', 'quality-stocks']:
            cache_entry = scanner_cache[scanner_type]
            if not cache_entry['running']:
                triggered.append(scanner_type)
        
        # The actual triggering happens when someone accesses the endpoints
        return jsonify({
            'success': True,
            'message': f'Access scanner endpoints to trigger: {triggered}',
            'scanners_to_trigger': triggered
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/quote/<symbol>')
def get_quote(symbol):
    """Get live quote for a symbol"""
    try:
        quote = get_live_quote(symbol.upper())
        if quote:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'quote': quote
            })
        else:
            return jsonify({'success': False, 'error': 'Quote not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/options/analysis')
def options_analysis():
    """Get options analysis with caching, custom symbol support, and expiry filters"""
    try:
        # Check for parameters
        custom_symbols = request.args.get('symbols', '')
        expiry_type = request.args.get('expiry', '0dte')  # 0dte, daily, weekly, monthly
        is_custom_search = bool(custom_symbols)
        
        if custom_symbols:
            # Parse custom symbols (allow name or symbol)
            raw_tickers = [s.strip() for s in custom_symbols.split(',') if s.strip()]
            tickers = []
            for s in raw_tickers:
                resolved = resolve_symbol_or_name(s)
                if resolved:
                    tickers.append(resolved)
            tickers = tickers[:20]
            cache_key = f'options-analysis-{expiry_type}-{"-".join(sorted(tickers))}'
        else:
            # Use default tickers - include 0DTE eligible stocks
            if expiry_type == '0dte':
                tickers = ['SPY', 'QQQ', 'AAPL', 'TSLA', 'NVDA', 'AMD', 'MSFT', 'META', 'AMZN', 'GOOGL', 'IWM']
            else:
                tickers = ['SPY', 'QQQ', 'AAPL', 'TSLA', 'NVDA', 'AMD', 'MSFT', 'META', 'AMZN', 'GOOGL']
            cache_key = f'options-analysis-{expiry_type}'
        
        # Initialize cache entry if needed
        if cache_key not in scanner_cache:
            scanner_cache[cache_key] = {'data': None, 'timestamp': None, 'running': False}
        
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify(clean_nan_values({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'predictions': cache_entry['data'],
                    'weak_signals': cache_entry.get('weak_signals', []),
                    'cached': True,
                    'age_seconds': int(age),
                    'expiry_type': expiry_type
                }))
        
        # Start background analysis if not already running
        if not cache_entry['running']:
            def run_analysis():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting options analysis ({expiry_type}) for {len(tickers)} symbols...")
                    predictor = NextDayOptionsPredictor(real_time_mode=False)
                    
                    predictions = []
                    weak_signals = []
                    
                    for ticker in tickers:
                        try:
                            # Use verbose=False to suppress console output
                            result = predictor.analyze_stock(ticker, verbose=False, expiry_type=expiry_type)
                            if result:
                                result['expiry_type'] = expiry_type  # Tag with expiry type
                                if result.get('is_weak_signal', False):
                                    weak_signals.append(result)
                                else:
                                    predictions.append(result)
                        except Exception as e:
                            # Log the error but continue
                            print(f"❌ Error analyzing {ticker}: {str(e)}")
                            continue
                    
                    # Sort by signal strength
                    predictions.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
                    weak_signals.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
                    
                    scanner_cache[cache_key]['data'] = predictions
                    scanner_cache[cache_key]['expiry_type'] = expiry_type
                    scanner_cache[cache_key]['weak_signals'] = weak_signals
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Options analysis complete! Found {len(predictions)} strong signals, {len(weak_signals)} weak signals")
                except Exception as e:
                    print(f"❌ Options analysis error: {e}")
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_analysis, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify(clean_nan_values({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'predictions': cache_entry['data'],
                'weak_signals': cache_entry.get('weak_signals', []),
                'cached': True,
                'expiry_type': expiry_type
            }))
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'predictions': [],
                'weak_signals': [],
                'scanning': True,
                'message': f'Analyzing {len(tickers)} stocks. Please wait 1-2 minutes...',
                'expiry_type': expiry_type
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/options/refresh', methods=['POST'])
def refresh_options():
    """Force refresh options data (clear cache and re-analyze with live premiums)"""
    try:
        # Clear all options cache
        keys_to_clear = [k for k in scanner_cache.keys() if k.startswith('options-analysis')]
        for key in keys_to_clear:
            scanner_cache[key] = {'data': None, 'timestamp': None, 'running': False}
        
        return jsonify({
            'success': True,
            'message': f'Cleared {len(keys_to_clear)} cache entries. Refresh to get new data with live premiums.',
            'cleared_keys': keys_to_clear
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/options/pcr')
def options_pcr():
    """Get Put/Call Ratio for major stocks and ETFs - calculated from live options data"""
    try:
        # Define symbols to analyze
        stock_symbols = ['AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN']
        etf_symbols = ['SPY', 'QQQ', 'IWM', 'DIA', 'XLF']
        
        def calculate_pcr(symbol):
            """Calculate Put/Call Ratio from options chain"""
            try:
                # Get options expiration dates
                expirations = cached_get_option_dates(symbol)
                if not expirations or len(expirations) == 0:
                    return None
                
                # Use first expiration (nearest term)
                expiry = expirations[0]
                
                # Get options chain
                opt_chain = cached_get_option_chain(symbol, expiry)
                if opt_chain is None:
                    return None
                calls = opt_chain.calls
                puts = opt_chain.puts
                
                # Calculate total volume for puts and calls
                put_volume = puts['volume'].fillna(0).sum()
                call_volume = calls['volume'].fillna(0).sum()
                
                # Calculate PCR (avoid division by zero)
                if call_volume > 0:
                    pcr = put_volume / call_volume
                else:
                    pcr = 0
                
                # Determine sentiment
                if pcr < 0.7:
                    sentiment = 'Bullish'
                elif pcr > 1.0:
                    sentiment = 'Bearish'
                else:
                    sentiment = 'Neutral'
                
                total_volume = int(put_volume + call_volume)
                
                return {
                    'symbol': symbol,
                    'pcr': round(pcr, 2),
                    'sentiment': sentiment,
                    'volume': total_volume,
                    'put_volume': int(put_volume),
                    'call_volume': int(call_volume)
                }
                
            except Exception as e:
                print(f"Error calculating PCR for {symbol}: {e}")
                return None
        
        # Calculate PCR for stocks
        stock_pcr = []
        for symbol in stock_symbols:
            result = calculate_pcr(symbol)
            if result:
                stock_pcr.append(result)
        
        # Calculate PCR for ETFs
        etf_pcr = []
        for symbol in etf_symbols:
            result = calculate_pcr(symbol)
            if result:
                etf_pcr.append(result)
        
        # Sort by volume (highest first)
        stock_pcr.sort(key=lambda x: x['volume'], reverse=True)
        etf_pcr.sort(key=lambda x: x['volume'], reverse=True)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'stock_pcr': stock_pcr[:5],  # Top 5
            'etf_pcr': etf_pcr[:5]       # Top 5
        })
    except Exception as e:
        print(f"Error in options_pcr: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/options/swing')
def options_swing_trade():
    """Get swing trade options with premium filters and expiry types"""
    try:
        # Get filter parameters
        max_premium = float(request.args.get('max_premium', 30))
        expiry_type = request.args.get('expiry', 'weekly')  # weekly, monthly
        custom_symbols = request.args.get('symbols', '')
        
        # Premium tiers - each tier shows options AT that price level
        # $10: Show ATM options (lower premium = further OTM)
        # $20-50: Show options closer to money (higher premium)
        min_premium = 0.50  # Minimum across all tiers
        
        # Define default symbols
        if custom_symbols:
            symbols = [s.strip().upper() for s in custom_symbols.split(',') if s.strip()][:15]
        else:
            symbols = ['SPY', 'QQQ', 'AAPL', 'TSLA', 'NVDA', 'AMD', 'MSFT', 'META', 'AMZN', 'GOOGL', 'IWM', 'DIA']
        
        results = []
        
        for symbol in symbols:
            try:
                # Get current stock price (check pre/post market first)
                info = cached_get_ticker_info(symbol)
                current_price = (info.get('preMarketPrice') or 
                               info.get('postMarketPrice') or 
                               info.get('currentPrice') or 
                               info.get('regularMarketPrice') or 
                               info.get('previousClose', 0))
                
                if not current_price or current_price <= 0:
                    continue
                
                # Get expiration dates
                expirations = cached_get_option_dates(symbol)
                if not expirations:
                    continue
                
                # Filter expirations based on type
                from datetime import datetime, timedelta
                today = datetime.now().date()
                
                valid_expirations = []
                for exp_str in expirations:
                    try:
                        exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
                        days_to_exp = (exp_date - today).days
                        
                        if expiry_type == 'weekly' and 2 <= days_to_exp <= 10:
                            valid_expirations.append((exp_str, days_to_exp))
                        elif expiry_type == 'monthly' and 15 <= days_to_exp <= 45:
                            valid_expirations.append((exp_str, days_to_exp))
                    except:
                        continue
                
                if not valid_expirations:
                    # Fallback to nearest available
                    for exp_str in expirations[:3]:
                        try:
                            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
                            days_to_exp = (exp_date - today).days
                            if days_to_exp > 0:
                                valid_expirations.append((exp_str, days_to_exp))
                        except:
                            continue
                
                if not valid_expirations:
                    continue
                
                # Use the first valid expiration
                target_expiry, dte = valid_expirations[0]
                
                # Get options chain
                opt_chain = cached_get_option_chain(symbol, target_expiry)
                if opt_chain is None:
                    continue
                calls = opt_chain.calls.copy()
                puts = opt_chain.puts.copy()
                
                # Calculate mid-price for live pricing (bid+ask)/2, fallback to lastPrice
                calls['mid_price'] = calls.apply(
                    lambda r: (r['bid'] + r['ask']) / 2 if r['bid'] > 0 and r['ask'] > 0 else r['lastPrice'], 
                    axis=1
                )
                puts['mid_price'] = puts.apply(
                    lambda r: (r['bid'] + r['ask']) / 2 if r['bid'] > 0 and r['ask'] > 0 else r['lastPrice'], 
                    axis=1
                )
                
                # Get historical data for scoring
                hist = cached_get_history(symbol, period='1mo', interval='1d')
                if hist is not None and len(hist) >= 5:
                    recent_change = (hist['Close'].iloc[-1] / hist['Close'].iloc[-5] - 1) * 100
                else:
                    recent_change = 0
                
                # Filter calls - OTM (strike > current price) using mid-price for filtering
                otm_calls = calls[calls['strike'] >= current_price * 1.01]
                otm_calls = otm_calls[otm_calls['mid_price'] >= min_premium]
                otm_calls = otm_calls[otm_calls['mid_price'] <= max_premium]
                otm_calls = otm_calls[otm_calls['volume'].fillna(0) > 0]
                
                if len(otm_calls) > 0:
                    # For different premium tiers, select different strikes
                    # Higher premium = closer to money (first rows), Lower premium = further OTM
                    if max_premium <= 10:
                        # Cheapest tier - show furthest OTM (last rows)
                        idx = min(len(otm_calls) - 1, len(otm_calls) // 2 + 1)
                    elif max_premium <= 20:
                        idx = min(len(otm_calls) - 1, len(otm_calls) // 3)
                    elif max_premium <= 30:
                        idx = min(len(otm_calls) - 1, len(otm_calls) // 4)
                    elif max_premium <= 40:
                        idx = min(len(otm_calls) - 1, 1)
                    else:  # $50 tier - closest to money
                        idx = 0
                    
                    best_call = otm_calls.iloc[idx]
                    score = 7 + min(4, max(-2, recent_change))
                    
                    # Use mid-price (bid+ask)/2 for live pricing, fallback to lastPrice
                    bid = float(best_call.get('bid', 0) or 0)
                    ask = float(best_call.get('ask', 0) or 0)
                    if bid > 0 and ask > 0:
                        live_premium = round((bid + ask) / 2, 2)
                    else:
                        live_premium = float(best_call['lastPrice'])
                    
                    results.append({
                        'symbol': symbol,
                        'type': 'CALL',
                        'stock_price': round(current_price, 2),
                        'strike': float(best_call['strike']),
                        'premium': live_premium,
                        'bid': bid,
                        'ask': ask,
                        'total_premium': round(live_premium * 100, 2),  # 1 contract = 100 shares
                        'expiry': target_expiry,
                        'dte': dte,
                        'iv': float(best_call.get('impliedVolatility', 0)),
                        'volume': int(best_call.get('volume', 0) or 0),
                        'open_interest': int(best_call.get('openInterest', 0) or 0),
                        'score': int(score)
                    })
                
                # Filter puts - OTM (strike < current price) using mid-price for filtering
                otm_puts = puts[puts['strike'] <= current_price * 0.99]
                otm_puts = otm_puts[otm_puts['mid_price'] >= min_premium]
                otm_puts = otm_puts[otm_puts['mid_price'] <= max_premium]
                otm_puts = otm_puts[otm_puts['volume'].fillna(0) > 0]
                
                if len(otm_puts) > 0:
                    # For puts, higher strike = closer to money (more expensive)
                    if max_premium <= 10:
                        idx = len(otm_puts) // 2  # Further OTM  
                    elif max_premium <= 20:
                        idx = len(otm_puts) * 2 // 3
                    elif max_premium <= 30:
                        idx = len(otm_puts) * 3 // 4
                    elif max_premium <= 40:
                        idx = max(0, len(otm_puts) - 2)
                    else:  # $50 tier
                        idx = len(otm_puts) - 1  # Closest to money
                    
                    best_put = otm_puts.iloc[min(idx, len(otm_puts) - 1)]
                    score = 7 - min(4, max(-2, recent_change))
                    
                    # Use mid-price (bid+ask)/2 for live pricing, fallback to lastPrice
                    bid = float(best_put.get('bid', 0) or 0)
                    ask = float(best_put.get('ask', 0) or 0)
                    if bid > 0 and ask > 0:
                        live_premium = round((bid + ask) / 2, 2)
                    else:
                        live_premium = float(best_put['lastPrice'])
                    
                    results.append({
                        'symbol': symbol,
                        'type': 'PUT',
                        'stock_price': round(current_price, 2),
                        'strike': float(best_put['strike']),
                        'premium': live_premium,
                        'bid': bid,
                        'ask': ask,
                        'total_premium': round(live_premium * 100, 2),  # 1 contract = 100 shares
                        'expiry': target_expiry,
                        'dte': dte,
                        'iv': float(best_put.get('impliedVolatility', 0)),
                        'volume': int(best_put.get('volume', 0) or 0),
                        'open_interest': int(best_put.get('openInterest', 0) or 0),
                        'score': int(score)
                    })
                
                # Small delay to avoid rate limiting
                import time
                time.sleep(0.2)
                
            except Exception as e:
                print(f"⚠️ Error processing {symbol} for swing trade: {e}")
                continue
        
        # Sort by score descending
        results.sort(key=lambda x: (-x['score'], x['premium']))
        
        # Clean NaN values
        results = clean_nan_values(results)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'options': results,
            'filters': {
                'max_premium': max_premium,
                'expiry_type': expiry_type
            }
        })
        
    except Exception as e:
        print(f"Error in options_swing_trade: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# TECHNICAL ANALYSIS ENDPOINTS
# ============================================================================

@app.route('/api/technical/chart-data', methods=['POST'])
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

@app.route('/api/technical/analyze', methods=['POST'])
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

# ============================================================================
# WEBSOCKET HANDLERS
# ============================================================================

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    client_sid = request.sid
    print(f"🔌 Client connected: {client_sid}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    client_sid = request.sid
    print(f"🔌 Client disconnected: {client_sid}")
    # Stop subscription thread for this client
    if client_sid in active_subscriptions:
        active_subscriptions[client_sid]['active'] = False
        del active_subscriptions[client_sid]

@socketio.on('subscribe_quotes')
def handle_subscribe_quotes(data):
    """Subscribe to real-time quotes for symbols - with rate limiting protection"""
    symbols = data.get('symbols', [])
    client_sid = request.sid  # Capture session ID before thread starts
    
    # Don't subscribe if no symbols or already subscribed
    if not symbols:
        print(f"📊 Client {client_sid} tried to subscribe with no symbols")
        return
    
    # Stop existing subscription for this client
    if client_sid in active_subscriptions:
        active_subscriptions[client_sid]['active'] = False
    
    print(f"📊 Client {client_sid} subscribed to: {symbols}")
    
    # Create subscription control
    active_subscriptions[client_sid] = {'active': True}
    
    # Start background thread to send updates
    def send_quote_updates():
        update_interval = 30  # Increased to 30 seconds to avoid rate limits
        while active_subscriptions.get(client_sid, {}).get('active', False):
            quotes_map = _fetch_all_quotes_batch(symbols)
            quotes = [quotes_map[s] for s in symbols if s in quotes_map]
            
            if quotes:
                try:
                    socketio.emit('quote_update', {
                        'timestamp': datetime.now().isoformat(),
                        'quotes': quotes
                    }, room=client_sid)
                except:
                    # Client disconnected, stop thread
                    break
            
            time.sleep(update_interval)
        
        # Cleanup
        if client_sid in active_subscriptions:
            del active_subscriptions[client_sid]
    
    thread = threading.Thread(target=send_quote_updates, daemon=True)
    thread.start()

# ============================================================================
# AUTONOMOUS TRADING API ENDPOINTS
# ============================================================================

@app.route('/api/autonomous/status')
def autonomous_status():
    """Get current autonomous trading status"""
    import os
    
    # Check if API key is set
    api_key_set = bool(os.environ.get('DEEPSEEK_API_KEY'))
    alpaca_key_set = bool(os.environ.get('ALPACA_API_KEY'))
    
    # Load state from file if exists
    state_file = 'autonomous_trader_state.json'
    positions = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                saved_state = json.load(f)
                positions = saved_state.get('positions', {})
        except:
            pass
    
    # Load trade log
    trade_log = []
    log_file = 'autonomous_trade_log.json'
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r') as f:
                trade_log = json.load(f)
        except:
            pass
    
    return jsonify({
        'success': True,
        'data': {
            'running': autonomous_trader_state['running'],
            'api_key_set': api_key_set,
            'broker_connected': alpaca_key_set,
            'paper_trading': autonomous_trader_state['settings']['paper_trading'],
            'positions': positions,
            'position_count': len(positions),
            'trade_log': trade_log[-20:],  # Last 20 trades
            'trade_count': len(trade_log),
            'daily_pnl': autonomous_trader_state['daily_pnl'],
            'last_scan': autonomous_trader_state['last_scan'],
            'settings': autonomous_trader_state['settings']
        }
    })

@app.route('/api/autonomous/start', methods=['POST'])
def autonomous_start():
    """Start autonomous trading"""
    import os
    
    if autonomous_trader_state['running']:
        return jsonify({'success': False, 'error': 'Autonomous trader is already running'})
    
    if not AUTONOMOUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'Autonomous trading module not available'})
    
    # Get API key from environment or request
    data = request.get_json() or {}
    deepseek_key = data.get('deepseek_api_key') or os.environ.get('DEEPSEEK_API_KEY')
    alpaca_key = data.get('alpaca_api_key') or os.environ.get('ALPACA_API_KEY')
    alpaca_secret = data.get('alpaca_secret_key') or os.environ.get('ALPACA_SECRET_KEY')
    
    if not deepseek_key:
        return jsonify({'success': False, 'error': 'DeepSeek API key not provided'})
    
    # Get settings
    paper_trading = data.get('paper_trading', True)
    scan_interval = data.get('scan_interval', 5)
    min_confidence = data.get('min_confidence', 75)
    
    try:
        # Create trader instance
        trader = AutonomousTrader(
            deepseek_api_key=deepseek_key,
            alpaca_key=alpaca_key,
            alpaca_secret=alpaca_secret,
            paper_trading=paper_trading
        )
        trader.scan_interval = scan_interval * 60
        trader.min_confidence = min_confidence
        
        # Store reference
        autonomous_trader_state['trader'] = trader
        autonomous_trader_state['running'] = True
        autonomous_trader_state['settings']['paper_trading'] = paper_trading
        autonomous_trader_state['settings']['scan_interval'] = scan_interval
        autonomous_trader_state['settings']['min_confidence'] = min_confidence
        autonomous_trader_state['settings']['api_key_set'] = True
        autonomous_trader_state['settings']['broker_connected'] = bool(alpaca_key)
        
        # Start in background thread
        def run_trader():
            try:
                trader.start()
            except Exception as e:
                print(f"Autonomous trader error: {e}")
            finally:
                autonomous_trader_state['running'] = False
        
        thread = threading.Thread(target=run_trader, daemon=True)
        autonomous_trader_state['thread'] = thread
        thread.start()
        
        return jsonify({
            'success': True,
            'message': f'Autonomous trader started in {"PAPER" if paper_trading else "LIVE"} mode'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/autonomous/stop', methods=['POST'])
def autonomous_stop():
    """Stop autonomous trading"""
    if not autonomous_trader_state['running']:
        return jsonify({'success': False, 'error': 'Autonomous trader is not running'})
    
    try:
        trader = autonomous_trader_state['trader']
        if trader:
            trader.running = False
        
        autonomous_trader_state['running'] = False
        autonomous_trader_state['trader'] = None
        
        return jsonify({
            'success': True,
            'message': 'Autonomous trader stopped'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/autonomous/analyze', methods=['POST'])
def autonomous_analyze():
    """Run AI analysis on a specific ticker"""
    import os
    
    if not AUTONOMOUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'Autonomous trading module not available'})
    
    data = request.get_json() or {}
    ticker = data.get('ticker', '').upper()
    
    if not ticker:
        return jsonify({'success': False, 'error': 'Ticker symbol required'})
    
    deepseek_key = os.environ.get('DEEPSEEK_API_KEY')
    if not deepseek_key:
        return jsonify({'success': False, 'error': 'DeepSeek API key not set'})
    
    try:
        # Get market data
        scanner = UnifiedTradingSystem(alert_interval=5)
        market_data = scanner.get_live_data(ticker)
        
        if not market_data:
            return jsonify({'success': False, 'error': f'Could not fetch data for {ticker}'})
        
        # Run AI analysis
        ai = DeepSeekAnalyzer(deepseek_key)
        decision = ai.analyze_market(market_data)
        
        return jsonify({
            'success': True,
            'data': {
                'ticker': ticker,
                'market_data': clean_nan_values(market_data),
                'ai_decision': decision
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/autonomous/settings', methods=['POST'])
def autonomous_settings():
    """Update autonomous trading settings"""
    data = request.get_json() or {}
    
    if 'scan_interval' in data:
        autonomous_trader_state['settings']['scan_interval'] = int(data['scan_interval'])
    if 'min_confidence' in data:
        autonomous_trader_state['settings']['min_confidence'] = int(data['min_confidence'])
    if 'max_positions' in data:
        autonomous_trader_state['settings']['max_positions'] = int(data['max_positions'])
    if 'risk_per_trade' in data:
        autonomous_trader_state['settings']['risk_per_trade'] = float(data['risk_per_trade'])
    if 'paper_trading' in data:
        autonomous_trader_state['settings']['paper_trading'] = bool(data['paper_trading'])
    
    return jsonify({
        'success': True,
        'settings': autonomous_trader_state['settings']
    })

@app.route('/api/autonomous/positions')
def autonomous_positions():
    """Get current autonomous trading positions"""
    import os
    
    state_file = 'autonomous_trader_state.json'
    positions = {}
    
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r') as f:
                saved_state = json.load(f)
                positions = saved_state.get('positions', {})
        except:
            pass
    
    # Also get from active trader if running
    if autonomous_trader_state['trader']:
        positions = autonomous_trader_state['trader'].positions
    
    return jsonify({
        'success': True,
        'positions': positions,
        'count': len(positions)
    })

@app.route('/api/autonomous/trades')
def autonomous_trades():
    """Get trade history"""
    import os
    
    log_file = 'autonomous_trade_log.json'
    trades = []
    
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r') as f:
                trades = json.load(f)
        except:
            pass
    
    return jsonify({
        'success': True,
        'trades': trades,
        'count': len(trades)
    })

# ============================================================================
# AI TRADING BOT API ENDPOINTS
# ============================================================================

# Bot state management
bot_state = {
    'running': False,
    'auto_trade': False,  # Auto-execute trades when signals meet criteria
    'account_mode': 'demo',
    'strategy': 'trend_following',
    'settings': {
        'watchlist': 'top_50',
        'scan_interval': 5,
        'min_confidence': 85,
        'min_option_dte_days': 1,
        'max_positions': 3,
        'max_daily_trades': 20,
        'max_per_symbol_daily': 6,
        'reentry_cooldown_minutes': 10,
        'position_size': 4000,
        'stop_loss': 2.0,
        'take_profit': 4.0,
        'trailing_stop': 'atr',
        'partial_profit_taking': True,
        'close_0dte_before_expiry': True
    },
    'last_scan': None,
    'signals': [],
    'demo_account': {
        'balance': 10000.0,
        'initial_balance': 10000.0,
        'positions': [],
        'trades': []
    },
    'real_account': {
        'balance': 0,
        'positions': [],
        'trades': []
    }
}

BOT_STATE_FILE = 'ai_bot_state.json'
BOT_STATE_LOCK = threading.Lock()
AUTO_TRADE_DEDUP_LOCK = threading.Lock()
AUTO_TRADE_EXECUTION_GUARD = {}  # key -> unix timestamp
AUTO_TRADE_DEDUP_SECONDS = 60

def recalculate_balance(account):
    """
    Recalculate account balance from trade history to prevent drift.
    
    Balance = initial_balance + realized_P&L - cost_of_LONG_positions + proceeds_from_SHORT_positions
    
    This is the authoritative calculation. Incremental balance tracking (adding/subtracting
    on each trade) is fragile — if any position is removed without a proper counterbalancing
    trade, the balance drifts permanently. This function corrects that.
    """
    initial_balance = account.get('initial_balance', 10000.0)
    trades = account.get('trades', [])
    positions = account.get('positions', [])
    
    # Sum all realized P&L from closed trades (exit trades that have pnl field)
    realized_pnl = 0.0
    for t in trades:
        pnl = t.get('pnl')
        if pnl is not None:
            realized_pnl += pnl
    
    # Calculate net capital tied up in open positions
    # LONG positions: we SPENT money (subtract from balance)
    # SHORT positions: we RECEIVED money (add to balance) - but owe it back on cover
    net_position_cost = 0.0
    for pos in positions:
        is_option = pos.get('instrument_type') == 'option'
        multiplier = 100 if is_option else 1
        qty = pos.get('quantity', 0)
        entry = pos.get('entry_price', 0)
        cost = entry * qty * multiplier
        
        if pos.get('side') == 'SHORT':
            net_position_cost -= cost  # Short sale proceeds (money IN)
        else:
            net_position_cost += cost  # Long purchase cost (money OUT)
    
    correct_balance = initial_balance + realized_pnl - net_position_cost
    return round(correct_balance, 2)

def generate_daily_trade_analysis(account, analysis_date_str=None):
    """Generate daily trade performance analysis with focus on stop-loss impact."""
    if analysis_date_str is None:
        analysis_date_str = datetime.now().strftime('%Y-%m-%d')

    trades = account.get('trades', [])
    today_trades = [t for t in trades if t.get('timestamp', '').startswith(analysis_date_str)]

    # Realized exits/partials (entries typically don't carry pnl)
    realized = [
        t for t in today_trades
        if t.get('pnl') is not None and t.get('action') in ('SELL', 'BUY_TO_COVER', 'CLOSE', 'PARTIAL_SELL', 'PARTIAL_COVER')
    ]

    stock_realized = [t for t in realized if t.get('instrument_type', 'stock') != 'option']
    option_realized = [t for t in realized if t.get('instrument_type') == 'option']

    sl_reasons = {'STOP_LOSS', 'TRAILING_STOP', 'MAX_LOSS_GUARD'}
    target_reasons = {'TARGET_HIT', 'PARTIAL_TARGET_HIT'}

    def summarize(bucket):
        wins = sum(1 for t in bucket if float(t.get('pnl', 0) or 0) > 0)
        losses = sum(1 for t in bucket if float(t.get('pnl', 0) or 0) < 0)
        breakeven = sum(1 for t in bucket if float(t.get('pnl', 0) or 0) == 0)
        decisive = wins + losses
        win_rate = round((wins / decisive) * 100, 1) if decisive > 0 else 0.0
        net_pnl = round(sum(float(t.get('pnl', 0) or 0) for t in bucket), 2)

        stop_loss_trades = [t for t in bucket if t.get('reason') in sl_reasons]
        target_trades = [t for t in bucket if t.get('reason') in target_reasons]

        stop_loss_count = len(stop_loss_trades)
        target_count = len(target_trades)
        stop_loss_pnl = round(sum(float(t.get('pnl', 0) or 0) for t in stop_loss_trades), 2)
        target_pnl = round(sum(float(t.get('pnl', 0) or 0) for t in target_trades), 2)

        largest_win = max((float(t.get('pnl', 0) or 0) for t in bucket), default=0.0)
        largest_loss = min((float(t.get('pnl', 0) or 0) for t in bucket), default=0.0)

        return {
            'total': len(bucket),
            'wins': wins,
            'losses': losses,
            'breakeven': breakeven,
            'win_rate': win_rate,
            'net_pnl': net_pnl,
            'stop_loss_count': stop_loss_count,
            'target_hit_count': target_count,
            'stop_loss_pnl': stop_loss_pnl,
            'target_pnl': target_pnl,
            'largest_win': round(largest_win, 2),
            'largest_loss': round(largest_loss, 2)
        }

    all_summary = summarize(realized)
    stock_summary = summarize(stock_realized)
    option_summary = summarize(option_realized)

    notes = []
    if stock_summary['total'] > 0:
        if stock_summary['stop_loss_count'] > stock_summary['target_hit_count']:
            notes.append('Stock exits hit stop-loss more often than targets today.')
        if stock_summary['stop_loss_pnl'] < 0 and abs(stock_summary['stop_loss_pnl']) > abs(stock_summary['target_pnl']):
            notes.append('Stock stop-loss losses outweighed stock target gains today.')
        if stock_summary['win_rate'] < 45:
            notes.append('Stock win rate is weak today; consider tighter signal filtering before auto-entry.')

    return {
        'date': analysis_date_str,
        'generated_at': datetime.now().isoformat(),
        'overall': all_summary,
        'stocks': stock_summary,
        'options': option_summary,
        'notes': notes
    }

def reconcile_orphan_positions(account, acct_key=''):
    """
    On startup, detect and auto-close orphan positions to prevent discrepancies.

    Catches:
    1. Day-trade positions from a PREVIOUS day that were never closed (e.g. server
       restarted and the EOD sweep was missed).
    2. Positions whose matching exit trade already exists in the trade history
       (duplicate in positions list).
    3. Expired option positions (expiry date in the past).

    Any orphan found is removed from positions[] and a synthetic CLOSE exit trade
    is appended to trades[] so the P&L ledger stays consistent.
    Returns the number of orphans fixed.
    """
    positions = account.get('positions', [])
    trades = account.get('trades', [])
    if not positions:
        return 0

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore

    now_et = datetime.now(ZoneInfo('US/Eastern'))
    today_str = now_et.strftime('%Y-%m-%d')

    orphans = []  # (position, reason)

    for pos in list(positions):
        symbol = pos.get('symbol', '?')
        side = pos.get('side', 'LONG')
        qty = pos.get('quantity', 0)
        entry_price = pos.get('entry_price', 0)
        instrument_type = pos.get('instrument_type', 'stock')
        trade_type = pos.get('trade_type', 'swing')
        source = pos.get('source', 'bot')

        # --- 1. Stale day-trade from a previous day ---
        pos_ts = pos.get('timestamp', '')
        pos_date = pos_ts[:10] if len(pos_ts) >= 10 else ''
        if trade_type == 'day' and pos_date and pos_date < today_str:
            orphans.append((pos, f'STALE_DAY_TRADE (opened {pos_date}, missed EOD close)'))
            continue

        # --- 2. Position already has a matching exit in trade history ---
        exit_actions = ('SELL', 'BUY_TO_COVER', 'CLOSE')
        has_exit = False
        for t in trades:
            if (t.get('symbol') == symbol and
                    t.get('action') in exit_actions and
                    t.get('source', '') == source and
                    t.get('instrument_type', 'stock') == instrument_type and
                    t.get('quantity', 0) == qty and
                    t.get('pnl') is not None):
                # Match by timestamp proximity — exit must be AFTER position open
                if pos_ts and t.get('timestamp', '') > pos_ts:
                    has_exit = True
                    break
        if has_exit:
            orphans.append((pos, 'ALREADY_CLOSED (exit trade exists in history)'))
            continue

        # --- 3. Expired option position ---
        if instrument_type == 'option':
            expiry_str = pos.get('expiry', '')
            if expiry_str:
                try:
                    expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                    if expiry_date < now_et.date():
                        orphans.append((pos, f'EXPIRED_OPTION (expiry {expiry_str})'))
                        continue
                except ValueError:
                    pass

    # --- Close each orphan and record a synthetic exit trade ---
    fixed = 0
    for pos, reason in orphans:
        symbol = pos.get('symbol', '?')
        side = pos.get('side', 'LONG')
        qty = pos.get('quantity', 0)
        entry_price = pos.get('entry_price', 0)
        instrument_type = pos.get('instrument_type', 'stock')
        is_option = instrument_type == 'option'
        multiplier = 100 if is_option else 1

        # If an exit already exists in history, only remove stale position.
        # Do not append another synthetic exit trade (prevents duplicate ALREADY_CLOSED logs).
        if reason.startswith('ALREADY_CLOSED'):
            if pos in positions:
                positions.remove(pos)
            display = pos.get('contract', symbol) if is_option else symbol
            print(f"🔧 STARTUP RECONCILE: Removed duplicate orphan {display} ({reason})")
            fixed += 1
            continue

        # Use entry_price for stale exits (market is closed, no live quote)
        exit_price = entry_price
        pnl = 0.0  # Neutral P&L when we can't get a real price

        # For expired options, assume worthless
        if 'EXPIRED_OPTION' in reason:
            exit_price = 0.0
            if side == 'LONG':
                pnl = -entry_price * qty * multiplier
            else:
                pnl = entry_price * qty * multiplier

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
        if side == 'SHORT':
            pnl_pct = -pnl_pct

        exit_trade = {
            'symbol': symbol,
            'contract': pos.get('contract', ''),
            'action': 'SELL' if side == 'LONG' else 'BUY_TO_COVER',
            'side': side,
            'instrument_type': instrument_type,
            'option_type': pos.get('option_type', ''),
            'source': pos.get('source', 'bot'),
            'quantity': qty,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'price': exit_price,
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 2),
            'reason': reason.split(' ')[0],  # e.g. STALE_DAY_TRADE
            'timestamp': datetime.now().isoformat(),
            'auto_exit': True,
            'reconciled_on_startup': True,
            'trade_type': pos.get('trade_type', 'swing')
        }
        trades.append(exit_trade)

        if pos in positions:
            positions.remove(pos)

        display = pos.get('contract', symbol) if is_option else symbol
        print(f"🔧 STARTUP RECONCILE: Closed orphan {display} ({reason}) | P&L: ${pnl:.2f}")
        fixed += 1

    return fixed


def load_bot_state():
    global bot_state
    if os.path.exists(BOT_STATE_FILE):
        try:
            with open(BOT_STATE_FILE, 'r') as f:
                saved = json.load(f)
                bot_state.update(saved)

            # Backfill new settings keys for older state files
            if 'settings' not in bot_state:
                bot_state['settings'] = {}
            if 'min_option_dte_days' not in bot_state['settings']:
                bot_state['settings']['min_option_dte_days'] = 1
            if 'max_per_symbol_daily' not in bot_state['settings']:
                bot_state['settings']['max_per_symbol_daily'] = 6
            if 'reentry_cooldown_minutes' not in bot_state['settings']:
                bot_state['settings']['reentry_cooldown_minutes'] = 10
            # Migrate: backfill 'source' field on existing positions
            for acct_key in ('demo_account', 'real_account'):
                acct = bot_state.get(acct_key, {})
                for pos in acct.get('positions', []):
                    if 'source' not in pos:
                        pos['source'] = 'bot' if pos.get('auto_trade') else 'manual'

                # --- Reconcile orphan positions on every load/restart ---
                orphan_count = reconcile_orphan_positions(acct, acct_key)
                if orphan_count > 0:
                    print(f"🔧 [{acct_key}] Reconciled {orphan_count} orphan position(s) on startup")

                # Recalculate balance from trade history to prevent drift
                correct_balance = recalculate_balance(acct)
                if abs(acct.get('balance', 0) - correct_balance) > 0.01:
                    print(f"🔧 [{acct_key}] Balance corrected: ${acct.get('balance', 0):.2f} → ${correct_balance:.2f} (drift of ${acct.get('balance', 0) - correct_balance:.2f})")
                    acct['balance'] = correct_balance

            # Persist any reconciliation changes
            save_bot_state()
        except:
            pass
    return bot_state

def save_bot_state():
    with open(BOT_STATE_FILE, 'w') as f:
        json.dump(bot_state, f, indent=2, default=str)

# Load saved state on module initialization
load_bot_state()

# Watchlist definitions (simple hardcoded presets — no master merge)
WATCHLISTS = {
    'top_50': [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'UNH', 'JPM',
        'V', 'JNJ', 'XOM', 'WMT', 'MA', 'PG', 'HD', 'CVX', 'MRK', 'ABBV',
        'LLY', 'BAC', 'KO', 'PEP', 'COST', 'AVGO', 'TMO', 'MCD', 'DIS', 'CSCO',
        'ABT', 'ACN', 'VZ', 'ADBE', 'WFC', 'INTC', 'NKE', 'CRM', 'AMD', 'CMCSA',
        'TXN', 'PM', 'UPS', 'NEE', 'MS', 'HON', 'RTX', 'ORCL', 'QCOM', 'BMY'
    ],
    'sp500': [
        'SPY', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'UNH'
    ],
    'tech': [
        'AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMD', 'CRM', 'ADBE', 'INTC', 'ORCL',
        'CSCO', 'IBM', 'AVGO', 'TXN', 'QCOM', 'NOW', 'SNOW', 'PLTR', 'NET', 'DDOG'
    ],
    'custom': []
}

def calculate_technical_indicators(symbol):
    """Calculate technical indicators for a stock (uses cached history)"""
    try:
        # Filter out delisted/invalid symbols before any API call
        if not resolve_symbol_or_name.is_valid_symbol(symbol):
            return None
        df = cached_get_history(symbol, period='3mo', interval='1d')
        if df is None or df.empty or len(df) < 30:
            return None
        
        # Calculate indicators
        df['SMA20'] = df['Close'].rolling(20).mean()
        df['SMA50'] = df['Close'].rolling(50).mean() if len(df) >= 50 else df['SMA20']
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        
        # RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        df['MACD'] = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()
        df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()
        
        # Bollinger Bands
        df['BB_Middle'] = df['Close'].rolling(20).mean()
        bb_std = df['Close'].rolling(20).std()
        df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
        df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
        
        # ATR
        high_low = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift())
        low_close = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()
        
        # Volume
        df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        return {
            'symbol': symbol,
            'price': float(latest['Close']),
            'change': float((latest['Close'] - prev['Close']) / prev['Close'] * 100),
            'volume': int(latest['Volume']),
            'vol_avg': float(latest['Vol_SMA20']) if not pd.isna(latest['Vol_SMA20']) else 0,
            'sma20': float(latest['SMA20']) if not pd.isna(latest['SMA20']) else 0,
            'sma50': float(latest['SMA50']) if not pd.isna(latest['SMA50']) else 0,
            'ema9': float(latest['EMA9']) if not pd.isna(latest['EMA9']) else 0,
            'ema21': float(latest['EMA21']) if not pd.isna(latest['EMA21']) else 0,
            'rsi': float(latest['RSI']) if not pd.isna(latest['RSI']) else 50,
            'macd': float(latest['MACD']) if not pd.isna(latest['MACD']) else 0,
            'macd_signal': float(latest['MACD_Signal']) if not pd.isna(latest['MACD_Signal']) else 0,
            'bb_upper': float(latest['BB_Upper']) if not pd.isna(latest['BB_Upper']) else 0,
            'bb_lower': float(latest['BB_Lower']) if not pd.isna(latest['BB_Lower']) else 0,
            'atr': float(latest['ATR']) if not pd.isna(latest['ATR']) else 0
        }
    except Exception as e:
        print(f"Error calculating indicators for {symbol}: {e}")
        return None

# Attach cached is_valid_symbol to resolve_symbol_or_name for global use
resolve_symbol_or_name.is_valid_symbol = is_valid_symbol_cached

def analyze_for_strategy(data, strategy):
    """Analyze stock data based on selected strategy"""
    if not data:
        return None
    
    signals = []
    confidence = 50
    action = None
    trend = 'neutral'
    
    price = data['price']
    rsi = data['rsi']
    macd = data['macd']
    macd_signal = data['macd_signal']
    ema9 = data['ema9']
    ema21 = data['ema21']
    sma20 = data['sma20']
    bb_upper = data['bb_upper']
    bb_lower = data['bb_lower']
    volume = data['volume']
    vol_avg = data['vol_avg']
    atr = data['atr']
    
    if strategy == 'trend_following':
        # EMA crossover
        ema_bullish = ema9 > ema21
        ema_bearish = ema9 < ema21
        if ema_bullish:
            signals.append('EMA9 > EMA21 (bullish)')
            confidence += 10
            trend = 'bullish'
        elif ema_bearish:
            signals.append('EMA9 < EMA21 (bearish)')
            confidence -= 10
            trend = 'bearish'
        
        # MACD
        macd_bullish = macd > macd_signal and macd > 0
        macd_bearish = macd < macd_signal and macd < 0
        if macd_bullish:
            signals.append('MACD bullish crossover')
            confidence += 15
        elif macd_bearish:
            signals.append('MACD bearish crossover')
            confidence -= 15
        
        # === SIGNAL CONFLICT FILTER (trend_following) ===
        # Reject if EMA and MACD disagree on direction
        if ema_bullish and macd_bearish:
            return None  # EMA says up, MACD says down - conflicting
        if ema_bearish and macd_bullish:
            return None  # EMA says down, MACD says up - conflicting
        
        # RSI
        if 30 < rsi < 70:
            if rsi > 50:
                signals.append(f'RSI bullish ({rsi:.0f})')
                confidence += 5
            else:
                signals.append(f'RSI bearish ({rsi:.0f})')
                confidence -= 5
        
        # Trend strength
        if price > sma20 and ema9 > ema21:
            action = 'BUY'
        elif price < sma20 and ema9 < ema21:
            action = 'SELL'
            
    elif strategy == 'mean_reversion':
        # Bollinger Bands
        if price < bb_lower:
            signals.append('Price below lower BB (oversold)')
            confidence += 20
            trend = 'bullish'
            action = 'BUY'
        elif price > bb_upper:
            signals.append('Price above upper BB (overbought)')
            confidence += 20
            trend = 'bearish'
            action = 'SELL'
        
        # RSI extremes
        if rsi < 30:
            signals.append(f'RSI oversold ({rsi:.0f})')
            confidence += 15
            if not action:
                action = 'BUY'
                trend = 'bullish'
        elif rsi > 70:
            signals.append(f'RSI overbought ({rsi:.0f})')
            confidence += 15
            if not action:
                action = 'SELL'
                trend = 'bearish'
                
    elif strategy == 'breakout':
        # Volume breakout
        vol_ratio = volume / vol_avg if vol_avg > 0 else 1
        
        if vol_ratio > 1.5:
            signals.append(f'High volume ({vol_ratio:.1f}x avg)')
            confidence += 10
            
            # Price breakout
            if price > bb_upper:
                signals.append('Breaking upper resistance')
                confidence += 20
                action = 'BUY'
                trend = 'bullish'
            elif price < bb_lower:
                signals.append('Breaking lower support')
                confidence += 20
                action = 'SELL'
                trend = 'bearish'
        
        # MACD momentum
        if abs(macd) > abs(macd_signal) * 1.5:
            signals.append('Strong MACD momentum')
            confidence += 10
            
    elif strategy == 'scalping':
        # Quick signals for scalping
        if rsi < 35 and macd > macd_signal:
            signals.append('RSI bounce setup')
            confidence += 15
            action = 'BUY'
            trend = 'bullish'
        elif rsi > 65 and macd < macd_signal:
            signals.append('RSI rejection setup')
            confidence += 15
            action = 'SELL'
            trend = 'bearish'
        
        # EMA proximity
        if abs(price - ema9) / price < 0.005:
            signals.append('Price at EMA9 support/resistance')
            confidence += 10
    
    if not action or confidence < 60:
        return None
    
    # Calculate stop loss and target based on ATR
    atr_mult = 1.5 if strategy == 'scalping' else 2.0
    stop_loss = price - (atr * atr_mult) if action == 'BUY' else price + (atr * atr_mult)
    target = price + (atr * atr_mult * 2) if action == 'BUY' else price - (atr * atr_mult * 2)
    
    return {
        'symbol': data['symbol'],
        'action': action,
        'entry': price,
        'stop_loss': stop_loss,
        'target': target,
        'confidence': min(confidence, 95),
        'trend': trend,
        'reason': '; '.join(signals),
        'rsi': rsi,
        'macd': macd,
        'volume_ratio': volume / vol_avg if vol_avg > 0 else 1
    }

def add_or_update_position(account, symbol, side, quantity, price, stop_loss=None, target=None, extra_fields=None):
    """
    Add a new position or update existing position for same symbol/side/source.
    Uses weighted average for entry price when adding to existing position.
    Only merges positions of the same instrument_type AND source (bot vs manual).
    Returns: (position, is_new)
    """
    # Determine instrument type and source from extra_fields
    instrument_type = (extra_fields or {}).get('instrument_type', 'stock')
    source = (extra_fields or {}).get('source', 'manual')
    
    # Find existing position with same symbol, side, instrument type, AND source
    existing_pos = None
    for pos in account.get('positions', []):
        if (pos.get('symbol') == symbol and 
            pos.get('side') == side and 
            pos.get('instrument_type', 'stock') == instrument_type and
            pos.get('source', 'manual') == source):
            existing_pos = pos
            break
    
    if existing_pos:
        # Update existing position with weighted average entry
        old_qty = existing_pos.get('quantity', 0)
        old_entry = existing_pos.get('entry_price', price)
        new_qty = old_qty + quantity
        
        # Weighted average entry price
        avg_entry = ((old_qty * old_entry) + (quantity * price)) / new_qty
        
        existing_pos['quantity'] = new_qty
        existing_pos['entry_price'] = round(avg_entry, 4)
        existing_pos['current_price'] = price
        existing_pos['timestamp'] = datetime.now().isoformat()
        
        # Update SL/target if provided (use new values)
        if stop_loss is not None:
            existing_pos['stop_loss'] = float(stop_loss)
        if target is not None:
            existing_pos['target'] = float(target)
        
        # Add any extra fields
        if extra_fields:
            existing_pos.update(extra_fields)
        
        return existing_pos, False  # Not a new position
    else:
        # Create new position
        position = {
            'symbol': symbol,
            'side': side,
            'instrument_type': instrument_type,
            'source': source,
            'quantity': quantity,
            'entry_price': price,
            'current_price': price,
            'stop_loss': float(stop_loss) if stop_loss else price * (0.95 if side == 'LONG' else 1.05),
            'target': float(target) if target else price * (1.10 if side == 'LONG' else 0.90),
            'timestamp': datetime.now().isoformat()
        }
        
        # Add any extra fields
        if extra_fields:
            position.update(extra_fields)
        
        account['positions'].append(position)
        return position, True  # New position

def update_positions_with_live_prices(positions, force_live=False):
    """Update positions with current market prices.
    For stocks: batch fetch prices via cached_batch_prices (single API call).
    For options: fetch option premium from option chains.
    When force_live=True, bypasses all caches to guarantee fresh data.
    """
    if not positions:
        return positions
    
    use_cache = not force_live
    update_source = 'live' if force_live else 'cache_or_live'
    
    # Separate stock and option positions
    stock_positions = [p for p in positions if p.get('instrument_type', 'stock') != 'option']
    option_positions = [p for p in positions if p.get('instrument_type') == 'option']
    
    now_iso = datetime.now().isoformat()

    def _mark_price_update(pos, status, reason=''):
        pos['price_update_mode'] = 'force_live' if force_live else 'cache_enabled'
        pos['price_update_source'] = update_source
        pos['price_update_status'] = status
        pos['price_update_reason'] = reason
        # Always record when the system last attempted to refresh this position
        pos['last_checked'] = now_iso

    for pos in stock_positions:
        _mark_price_update(pos, 'pending', 'awaiting_batch_quote')
    for pos in option_positions:
        _mark_price_update(pos, 'pending', 'awaiting_option_chain')

    # --- Update stock positions (BATCHED — single API call for all symbols) ---
    stock_symbols = list(set(p['symbol'] for p in stock_positions))
    if stock_symbols:
        stock_prices = cached_batch_prices(stock_symbols, period='1d', interval='1m', prepost=True, use_cache=use_cache)
        quote_api_prices = fetch_quote_api_batch(stock_symbols) if force_live else {}
        for pos in stock_positions:
            if pos['symbol'] in stock_prices:
                pos['current_price'] = stock_prices[pos['symbol']]
                pos['last_price_update'] = now_iso
                _mark_price_update(pos, 'updated', 'stock_quote_refreshed')
            elif pos['symbol'] in quote_api_prices:
                pos['current_price'] = float(quote_api_prices[pos['symbol']])
                pos['last_price_update'] = now_iso
                _mark_price_update(pos, 'updated', 'stock_quote_api_refreshed')
            else:
                fallback_price, _ = cached_get_price(pos['symbol'], period='1d', interval='1m', prepost=True, use_cache=use_cache)
                if fallback_price is not None:
                    pos['current_price'] = float(fallback_price)
                    pos['last_price_update'] = now_iso
                    _mark_price_update(pos, 'updated', 'stock_quote_fallback_refreshed')
                else:
                    info = cached_get_ticker_info(pos['symbol'])
                    info_price = None
                    if info:
                        info_price = info.get('regularMarketPrice') or info.get('currentPrice')
                    if info_price is not None:
                        pos['current_price'] = float(info_price)
                        pos['last_price_update'] = now_iso
                        _mark_price_update(pos, 'updated', 'stock_info_refreshed')
                    else:
                        _mark_price_update(pos, 'skipped', 'stock_quote_unavailable')
    else:
        for pos in stock_positions:
            _mark_price_update(pos, 'skipped', 'no_stock_symbols')
    
    # --- Update option positions with live premium ---
    option_by_symbol = {}
    for pos in option_positions:
        sym = pos['symbol']
        if sym not in option_by_symbol:
            option_by_symbol[sym] = []
        option_by_symbol[sym].append(pos)
    
    # Batch fetch underlying prices for all option symbols too
    option_symbols = list(option_by_symbol.keys())
    if option_symbols:
        underlying_prices = cached_batch_prices(option_symbols, period='1d', interval='1m', prepost=True, use_cache=use_cache)
    else:
        underlying_prices = {}
    
    def _normalize_option_ticker(value):
        if not value:
            return ''
        return str(value).strip().upper()

    def _extract_option_ticker_from_contract(contract_value):
        if not contract_value:
            return ''
        contract_str = str(contract_value).strip().upper()
        if re.fullmatch(r'[A-Z]{1,6}\d{6}[CP]\d{8}', contract_str):
            return contract_str
        return ''

    for symbol, opt_positions in option_by_symbol.items():
        try:
            underlying_price = underlying_prices.get(symbol) 
            available_dates = cached_get_option_dates(symbol)
            
            for pos in opt_positions:
                expiry = pos.get('expiry', '')
                strike = pos.get('strike', 0)
                opt_type = str(pos.get('option_type', 'call') or 'call').lower()
                option_ticker = _normalize_option_ticker(
                    pos.get('option_ticker') or
                    pos.get('contract_symbol') or
                    _extract_option_ticker_from_contract(pos.get('contract', ''))
                )
                
                if underlying_price is not None:
                    pos['underlying_price'] = underlying_price
                    pos['last_price_update'] = now_iso
                
                if not expiry or expiry not in available_dates:
                    _mark_price_update(pos, 'skipped', 'expiry_not_listed_for_symbol')
                    continue

                chain = cached_get_option_chain(symbol, expiry, use_cache=use_cache)
                if not chain:
                    _mark_price_update(pos, 'skipped', 'option_chain_unavailable')
                    continue

                df = chain.calls if opt_type == 'call' else chain.puts
                if df is None or df.empty:
                    _mark_price_update(pos, 'skipped', 'option_side_empty')
                    continue

                row = None
                if option_ticker and 'contractSymbol' in df.columns:
                    ticker_match = df[df['contractSymbol'].astype(str).str.upper() == option_ticker]
                    if not ticker_match.empty:
                        row = ticker_match.iloc[0]
                elif strike and 'strike' in df.columns:
                    # Legacy bootstrap path: resolve exact strike on exact expiry only once,
                    # then persist the concrete contractSymbol for strict future matching.
                    strike_match = df[abs(df['strike'].astype(float) - float(strike)) < 0.01]
                    if not strike_match.empty and 'contractSymbol' in strike_match.columns:
                        row = strike_match.iloc[0]
                        pos['option_ticker'] = str(row.get('contractSymbol', '')).upper()

                if row is None:
                    _mark_price_update(pos, 'skipped', 'contract_symbol_not_found')
                    continue

                last = float(row.get('lastPrice', 0) or 0)
                bid = float(row.get('bid', 0) or 0)
                ask = float(row.get('ask', 0) or 0)

                if last > 0:
                    pos['current_price'] = last
                elif bid > 0 and ask > 0:
                    pos['current_price'] = round((bid + ask) / 2, 2)
                elif ask > 0:
                    pos['current_price'] = ask
                elif bid > 0:
                    pos['current_price'] = bid
                else:
                    _mark_price_update(pos, 'skipped', 'no_trade_or_quote_price')
                    continue

                pos['current_bid'] = bid
                pos['current_ask'] = ask
                pos['last_price_update'] = now_iso
                _mark_price_update(pos, 'updated', 'option_contract_refreshed')
        except Exception as e:
            for pos in opt_positions:
                _mark_price_update(pos, 'error', f"options_update_exception: {type(e).__name__}")
            if not _is_expected_no_data_error(e):
                _log_fetch_event('position-options-error', symbol, f"Error fetching options data for {symbol}: {e}", cooldown=180)
    
    return positions

def get_live_option_premium(symbol, expiry, strike, option_type='call', fallback=None, option_ticker=''):
    """Fetch a best-effort live option premium from cached option chain.
    Returns fallback when live premium is unavailable.
    """
    try:
        available_dates = cached_get_option_dates(symbol)
        if not available_dates:
            return float(fallback) if fallback else None

        if not expiry or expiry not in available_dates:
            return float(fallback) if fallback else None

        chain = cached_get_option_chain(symbol, expiry)
        if not chain:
            return float(fallback) if fallback else None

        df = chain.calls if option_type == 'call' else chain.puts
        if df is None or df.empty:
            return float(fallback) if fallback else None

        row = None
        normalized_ticker = str(option_ticker or '').strip().upper()
        if normalized_ticker and 'contractSymbol' in df.columns:
            ticker_match = df[df['contractSymbol'].astype(str).str.upper() == normalized_ticker]
            if not ticker_match.empty:
                row = ticker_match.iloc[0]

        if row is None:
            return float(fallback) if fallback else None
        last = float(row.get('lastPrice', 0) or 0)
        bid = float(row.get('bid', 0) or 0)
        ask = float(row.get('ask', 0) or 0)

        if last > 0:
            return last
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        if ask > 0:
            return ask
        if bid > 0:
            return bid
    except Exception:
        pass

    return float(fallback) if fallback else None


def is_zero_dte_or_expired(expiry_str):
    """True when option expiry is today or in the past."""
    if not expiry_str:
        return False
    try:
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return expiry_date <= datetime.now().date()
    except Exception:
        return False


def get_min_option_dte_days():
    """Return minimum allowed DTE for option entries."""
    try:
        return max(1, int(bot_state.get('settings', {}).get('min_option_dte_days', 1)))
    except Exception:
        return 1


def get_option_dte(expiry_str):
    """Return DTE as integer days, or None when expiry is invalid."""
    if not expiry_str:
        return None
    try:
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return (expiry_date - datetime.now().date()).days
    except Exception:
        return None


def is_option_expiry_blocked(expiry_str, min_dte_days=None):
    """True when option expiry is below configured minimum DTE."""
    dte = get_option_dte(expiry_str)
    if dte is None:
        return False
    if min_dte_days is None:
        min_dte_days = get_min_option_dte_days()
    return dte < max(1, int(min_dte_days))

def refresh_signal_entries_with_live_prices(signals, force_refresh=False):
    """Refresh signal entry/SL/target values with latest live prices.

    - Stocks: refresh `entry` from live stock quote and preserve risk/reward proportions.
    - Options: refresh `entry`/`premium` from option premium and preserve SL/target multipliers.
    """
    if not signals:
        return signals

    now_iso = datetime.now().isoformat()

    stock_symbols = sorted({
        s.get('symbol') for s in signals
        if s.get('instrument_type', 'stock') != 'option' and s.get('symbol')
    })

    if force_refresh and stock_symbols:
        with _price_cache_lock:
            for sym in stock_symbols:
                _price_cache.pop(sym, None)

    stock_prices = cached_batch_prices(stock_symbols, period='1d', interval='1m', prepost=True) if stock_symbols else {}

    for signal in signals:
        instrument_type = signal.get('instrument_type', 'stock')
        action = signal.get('action', 'BUY')

        if instrument_type != 'option':
            symbol = signal.get('symbol')
            live_entry = stock_prices.get(symbol)
            if not live_entry or live_entry <= 0:
                continue

            old_entry = float(signal.get('entry', live_entry) or live_entry)
            old_stop = float(signal.get('stop_loss', old_entry * (0.95 if action == 'BUY' else 1.05)) or (old_entry * (0.95 if action == 'BUY' else 1.05)))
            old_target = float(signal.get('target', old_entry * (1.10 if action == 'BUY' else 0.90)) or (old_entry * (1.10 if action == 'BUY' else 0.90)))

            if old_entry <= 0:
                old_entry = float(live_entry)

            if action == 'SELL':
                risk_pct = max(0.005, min(0.5, (old_stop / old_entry) - 1)) if old_entry > 0 else 0.05
                reward_pct = max(0.005, min(2.0, 1 - (old_target / old_entry))) if old_entry > 0 else 0.10
                signal['stop_loss'] = round(float(live_entry) * (1 + risk_pct), 2)
                signal['target'] = round(float(live_entry) * (1 - reward_pct), 2)
            else:
                risk_pct = max(0.005, min(0.5, 1 - (old_stop / old_entry))) if old_entry > 0 else 0.05
                reward_pct = max(0.005, min(2.0, (old_target / old_entry) - 1)) if old_entry > 0 else 0.10
                signal['stop_loss'] = round(float(live_entry) * (1 - risk_pct), 2)
                signal['target'] = round(float(live_entry) * (1 + reward_pct), 2)

            signal['entry'] = round(float(live_entry), 2)
            signal['live_price_update'] = now_iso
            continue

        symbol = signal.get('symbol')
        strike = signal.get('strike', 0)
        expiry = signal.get('expiry', '')
        option_type = signal.get('option_type', 'call')
        old_entry = float(signal.get('entry', signal.get('premium', 0)) or 0)
        old_stop = float(signal.get('stop_loss', old_entry * 0.5) or (old_entry * 0.5))
        old_target = float(signal.get('target', old_entry * 2.0) or (old_entry * 2.0))

        if force_refresh and symbol:
            with _price_cache_lock:
                _price_cache.pop(symbol, None)

        live_premium = get_live_option_premium(
            symbol,
            expiry,
            strike,
            option_type,
            fallback=old_entry,
            option_ticker=signal.get('option_ticker', '')
        )
        if not live_premium or live_premium <= 0:
            continue

        if old_entry <= 0:
            old_entry = float(live_premium)

        risk_pct = max(0.01, min(0.95, 1 - (old_stop / old_entry))) if old_entry > 0 else 0.5
        target_mult = max(1.01, min(10.0, old_target / old_entry)) if old_entry > 0 else 2.0

        signal['entry'] = round(float(live_premium), 2)
        signal['premium'] = round(float(live_premium), 2)
        signal['stop_loss'] = round(float(live_premium) * (1 - risk_pct), 2)
        signal['target'] = round(float(live_premium) * target_mult, 2)

        underlying = stock_prices.get(symbol)
        if underlying and underlying > 0:
            signal['stock_price'] = round(float(underlying), 2)

        signal['live_price_update'] = now_iso

    return signals

@app.route('/api/bot/status')
def bot_status():
    """Get bot status and account info"""
    # First, get the state data inside the lock
    with BOT_STATE_LOCK:
        load_bot_state()
        
        account_mode = bot_state['account_mode']
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        # Recalculate balance from trade history (authoritative source of truth)
        account['balance'] = recalculate_balance(account)
        
        # Make a copy of positions to update outside the lock
        positions = [pos.copy() for pos in account.get('positions', [])]
        
        # Copy other needed state
        state_copy = {
            'running': bot_state['running'],
            'auto_trade': bot_state.get('auto_trade', False),
            'account_mode': account_mode,
            'strategy': bot_state['strategy'],
            'settings': bot_state['settings'],
            'last_scan': bot_state['last_scan'],
            'signals': bot_state.get('signals', []),
            'trades': account.get('trades', []),
            'demo_account': bot_state['demo_account'].copy(),
            'daily_analysis': bot_state.get('daily_analysis')
        }
    
    # Update positions with live prices OUTSIDE the lock (force_live bypasses cache)
    force_live = request.args.get('force_live', '0').lower() in ('1', 'true', 'yes')
    if force_live:
        clear_rate_limit_blocks()
    positions = update_positions_with_live_prices(positions, force_live=force_live)
    signals = refresh_signal_entries_with_live_prices(state_copy.get('signals', []), force_refresh=force_live)

    # Persist refreshed signals to keep UI and auto-cycle state aligned
    with BOT_STATE_LOCK:
        load_bot_state()
        bot_state['signals'] = signals
        save_bot_state()

    # Persist refreshed position prices so subsequent polls keep latest known values
    with BOT_STATE_LOCK:
        load_bot_state()
        account_mode_latest = bot_state['account_mode']
        account_latest = bot_state['demo_account'] if account_mode_latest == 'demo' else bot_state['real_account']
        stored_positions = account_latest.get('positions', [])
        changed = False

        # Best-effort index-based sync (positions copy preserves order from source)
        for idx, updated_pos in enumerate(positions):
            if idx >= len(stored_positions):
                break
            stored_pos = stored_positions[idx]

            # Safety guard: only sync when these core identifiers match
            if (
                stored_pos.get('symbol') != updated_pos.get('symbol')
                or stored_pos.get('timestamp') != updated_pos.get('timestamp')
            ):
                continue

            for field in ('current_price', 'current_bid', 'current_ask', 'underlying_price', 'last_price_update', 'last_checked'):
                if field in updated_pos and stored_pos.get(field) != updated_pos.get(field):
                    stored_pos[field] = updated_pos.get(field)
                    changed = True

        if changed:
            save_bot_state()
    
    # Calculate total unrealized P&L
    total_pnl = 0
    for pos in positions:
        entry = pos.get('entry_price', 0)
        current = pos.get('current_price', entry)
        qty = pos.get('quantity', 0)
        is_option = pos.get('instrument_type') == 'option'
        multiplier = 100 if is_option else 1
        side_mult = 1 if pos.get('side') == 'LONG' else -1
        total_pnl += (current - entry) * qty * multiplier * side_mult
    
    response = jsonify({
        'success': True,
        'running': state_copy['running'],
        'auto_trade': state_copy['auto_trade'],
        'account_mode': state_copy['account_mode'],
        'strategy': state_copy['strategy'],
        'settings': state_copy['settings'],
        'last_scan': state_copy['last_scan'],
        'signals': signals,
        'daily_analysis': state_copy.get('daily_analysis'),
        'positions': positions,
        'trades': state_copy['trades'],
        'demo_account': {
            **state_copy['demo_account'],
            'positions': positions if state_copy['account_mode'] == 'demo' else state_copy['demo_account']['positions'],
            'unrealized_pnl': total_pnl
        },
        'force_live': force_live
    })
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/bot/start', methods=['POST'])
def bot_start():
    """Start the trading bot"""
    global bot_state
    
    req = request.get_json(force=True)
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        bot_state['running'] = True
        bot_state['auto_trade'] = req.get('auto_trade', False)  # Enable auto-trading
        bot_state['account_mode'] = req.get('account_mode', 'demo')
        bot_state['strategy'] = req.get('strategy', 'trend_following')
        bot_state['last_scan'] = None  # Clear last scan so first auto_cycle runs immediately
        bot_state['settings'] = {
            'watchlist': req.get('watchlist', 'top_50'),
            'scan_interval': req.get('scan_interval', 5),
            'min_confidence': req.get('min_confidence', 75),
            'min_option_dte_days': req.get('min_option_dte_days', 1),
            'max_positions': req.get('max_positions', 5),
            'max_daily_trades': req.get('max_daily_trades', 20),
            'max_per_symbol_daily': req.get('max_per_symbol_daily', 6),
            'reentry_cooldown_minutes': req.get('reentry_cooldown_minutes', 10),
            'position_size': req.get('position_size', 1000),
            'stop_loss': req.get('stop_loss', 2.0),
            'take_profit': req.get('take_profit', 4.0),
            'trailing_stop': req.get('trailing_stop', '2'),
            'instrument_type': req.get('instrument_type', 'stocks')
        }
        save_bot_state()
    
    # --- STARTUP HEALTH CHECK: log all potential blockers upfront ---
    _acct = bot_state['demo_account'] if bot_state.get('account_mode') == 'demo' else bot_state['real_account']
    _pos_count = len(_acct.get('positions', []))
    _max_pos = bot_state['settings'].get('max_positions', 5)
    _balance = _acct.get('balance', 0)
    _pos_size = bot_state['settings'].get('position_size', 1000)
    _blockers = []
    if _pos_count >= _max_pos:
        _blockers.append(f"MAX_POSITIONS: {_pos_count}/{_max_pos} slots full")
    if _balance < _pos_size:
        _blockers.append(f"LOW_BALANCE: ${_balance:.2f} < position_size ${_pos_size}")
    if _is_rate_limited('__global__'):
        _blockers.append("RATE_LIMITED: yfinance globally rate-limited")
    if _blockers:
        print(f"⚠️ BOT HEALTH CHECK — {len(_blockers)} blocker(s) detected at startup:")
        for b in _blockers:
            print(f"   🔴 {b}")
    else:
        print(f"✅ BOT HEALTH CHECK — No blockers. balance=${_balance:.2f}, positions={_pos_count}/{_max_pos}, mode={bot_state.get('account_mode')}")
    
    return jsonify({
        'success': True,
        'message': f"Bot started in {bot_state['account_mode']} mode with {bot_state['strategy']} strategy",
        'auto_trade': bot_state['auto_trade']
    })

@app.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    """Stop the trading bot"""
    global bot_state
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        bot_state['running'] = False
        # DON'T reset auto_trade - preserve user's preference for next start
        # bot_state['auto_trade'] = False  # REMOVED - keep user preference
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': 'Bot stopped'
    })

@app.route('/api/bot/update_settings', methods=['POST'])
def bot_update_settings():
    """Update bot settings without starting/stopping the bot.
    Allows persisting settings changes immediately.
    """
    global bot_state
    
    req = request.get_json(force=True)
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        
        # Update auto_trade if provided
        if 'auto_trade' in req:
            bot_state['auto_trade'] = req['auto_trade']
            print(f"\U0001f527 Updated auto_trade: {bot_state['auto_trade']}")
        
        # Update strategy if provided
        if 'strategy' in req:
            bot_state['strategy'] = req['strategy']
        
        # Update account_mode if provided
        if 'account_mode' in req:
            bot_state['account_mode'] = req['account_mode']
        
        # Update individual settings if provided
        settings_fields = ['watchlist', 'scan_interval', 'min_confidence', 'max_positions',
              'max_daily_trades', 'max_per_symbol_daily', 'reentry_cooldown_minutes', 'min_option_dte_days', 'position_size', 'stop_loss', 'take_profit', 'trailing_stop', 'instrument_type',
                          'partial_profit_taking', 'close_0dte_before_expiry']
        
        # Track if routing-critical settings changed
        routing_changed = False
        for field in settings_fields:
            if field in req:
                old_val = bot_state['settings'].get(field)
                bot_state['settings'][field] = req[field]
                if field in ('watchlist', 'instrument_type') and old_val != req[field]:
                    routing_changed = True
                    print(f"\U0001f527 Setting '{field}' changed: {old_val} → {req[field]}")
        
        # When watchlist or instrument_type changes, clear stale cached signals
        # This prevents old daily/swing signals from being auto-traded after switching to intraday
        if routing_changed:
            bot_state['signals'] = []
            bot_state['last_scan'] = None  # Force fresh scan on next auto_cycle
            print(f"\U0001f527 Cleared cached signals and last_scan due to settings change")
        
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': 'Settings updated',
        'auto_trade': bot_state.get('auto_trade', False),
        'settings': bot_state['settings']
    })

@app.route('/api/bot/switch_account', methods=['POST'])
def bot_switch_account():
    """Switch between demo and real account mode"""
    global bot_state
    
    req = request.get_json(force=True)
    account_mode = req.get('account_mode', 'demo')
    
    if account_mode not in ['demo', 'real']:
        return jsonify({'success': False, 'error': 'Invalid account mode'}), 400
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        bot_state['account_mode'] = account_mode
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': f'Switched to {account_mode} account',
        'account_mode': account_mode
    })

@app.route('/api/bot/test_trade', methods=['POST'])
def bot_test_trade():
    """Test trade execution - creates a test trade to verify the system works"""
    global bot_state
    
    req = request.get_json(force=True)
    symbol = req.get('symbol', 'AAPL')
    action = req.get('action', 'BUY')
    quantity = req.get('quantity', 1)
    price = req.get('price', 100.0)
    account_mode = req.get('account_mode', 'demo')
    
    load_bot_state()
    
    with BOT_STATE_LOCK:
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        if action == 'BUY':
            cost = quantity * price
            if cost > account.get('balance', 0):
                return jsonify({'success': False, 'error': f'Insufficient balance: ${account.get("balance", 0):.2f} < ${cost:.2f}'})
            
            # Add to existing position or create new one
            default_sl = price * 0.95
            default_target = price * 1.10
            position, is_new = add_or_update_position(
                account, symbol, 'LONG', quantity, price,
                stop_loss=default_sl, target=default_target,
                extra_fields={'test_trade': True, 'source': 'manual'}
            )
            
            # Log trade
            trade = {
                'symbol': symbol,
                'action': 'BUY',
                'quantity': quantity,
                'price': price,
                'timestamp': datetime.now().isoformat(),
                'test_trade': True
            }
            account['trades'].append(trade)
            # Recalculate balance from trade history (authoritative)
            account['balance'] = recalculate_balance(account)
            save_bot_state()
            
            return jsonify({
                'success': True,
                'message': f'Bought {quantity} {symbol} @ ${price:.2f}',
                'balance': account['balance']
            })
        
        return jsonify({'success': False, 'error': 'Only BUY supported for test trades'})

@app.route('/api/bot/reset_demo', methods=['POST'])
def bot_reset_demo():
    """Reset demo account to starting balance"""
    global bot_state
    
    load_bot_state()
    
    with BOT_STATE_LOCK:
        # Reset demo account to defaults
        bot_state['demo_account'] = {
            'balance': 10000.0,
            'initial_balance': 10000.0,
            'positions': [],
            'trades': [],
            'pnl_history': []
        }
        
        # Clear signals and trades from bot state
        bot_state['signals'] = []
        bot_state['executed_trades'] = []
        bot_state['today_pnl'] = 0.0
        bot_state['running'] = False
        bot_state['auto_trade'] = False
        
        save_bot_state()
        
        print("🔄 [RESET] Demo account reset to $10,000")
        
        return jsonify({
            'success': True,
            'message': 'Demo account reset to $10,000',
            'balance': 10000.0
        })

@app.route('/api/bot/import_positions', methods=['POST'])
def bot_import_positions():
    """Import active positions from monitoring system (active_positions.json) into AI Bot"""
    global bot_state
    
    req = request.get_json(force=True)
    account_mode = req.get('account_mode', 'demo')
    
    # Load active positions from monitoring system
    positions_file = 'active_positions.json'
    if not os.path.exists(positions_file):
        return jsonify({'success': False, 'error': 'No monitoring positions found'}), 404
    
    try:
        with open(positions_file, 'r') as f:
            monitoring_positions = json.load(f)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to read positions: {str(e)}'}), 500
    
    imported = []
    with BOT_STATE_LOCK:
        load_bot_state()
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        for key, pos in monitoring_positions.items():
            # Only import active stock positions
            if pos.get('status') != 'active':
                continue
            if pos.get('type') == 'option':
                continue  # Skip options for now
            
            symbol = pos.get('ticker')
            if not symbol:
                continue
            
            # Check if position already exists
            existing = next((p for p in account['positions'] if p['symbol'] == symbol), None)
            if existing:
                continue  # Skip duplicates
            
            # Get current price
            try:
                price, _ = cached_get_price(symbol, period='1d', interval='1d', prepost=False)
                current_price = float(price) if price is not None else pos.get('entry', 0)
            except:
                current_price = pos.get('entry', 0)
            
            # Create position for AI Bot
            new_pos = {
                'symbol': symbol,
                'side': 'LONG',  # Monitoring positions are long by default
                'quantity': pos.get('quantity', 1),
                'entry_price': pos.get('entry', 0),
                'current_price': current_price,
                'stop_loss': pos.get('stop_loss', pos.get('entry', 0) * 0.95),
                'target': pos.get('target_1', pos.get('entry', 0) * 1.10),
                'timestamp': pos.get('date_added', datetime.now().isoformat()),
                'imported_from': 'monitoring'
            }
            
            # Deduct cost from balance
            cost = new_pos['quantity'] * new_pos['entry_price']
            if cost <= account['balance']:
                account['positions'].append(new_pos)
                # Also log as a BUY trade so P&L tracking works
                trade = {
                    'symbol': symbol,
                    'action': 'BUY',
                    'side': 'LONG',
                    'instrument_type': 'stock',
                    'source': 'imported',
                    'quantity': new_pos['quantity'],
                    'price': new_pos['entry_price'],
                    'timestamp': new_pos['timestamp']
                }
                account['trades'].append(trade)
                imported.append(symbol)
        
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
    
    if imported:
        return jsonify({
            'success': True,
            'message': f'Imported {len(imported)} positions: {", ".join(imported)}',
            'imported': imported
        })
    else:
        return jsonify({
            'success': True,
            'message': 'No new positions to import (all already exist or are options/closed)',
            'imported': []
        })

@app.route('/api/bot/scan', methods=['POST'])
def bot_scan():
    """Run a market scan based on strategy"""
    global bot_state
    
    req = request.get_json(force=True)
    strategy = req.get('strategy', bot_state['strategy'])
    watchlist_name = req.get('watchlist', bot_state['settings']['watchlist'])
    instrument_type = req.get('instrument_type', bot_state['settings'].get('instrument_type', 'stocks'))

    # Respect explicit watchlist mode even if persisted instrument_type is stale
    if watchlist_name == 'intraday_options':
        instrument_type = 'options'
    elif watchlist_name == 'intraday_stocks' and instrument_type == 'options':
        instrument_type = 'stocks'
    
    signals = []
    response_message = None
    
    # Determine which scanners to run based on instrument_type AND watchlist
    # instrument_type constrains WHAT to scan: 'stocks' (no options), 'options' (no stocks), 'both'
    # watchlist determines HOW to scan: 'intraday_stocks'/'intraday_options' = intraday, others = daily/swing
    can_scan_stocks = instrument_type in ('stocks', 'both')
    can_scan_options = instrument_type in ('options', 'both')
    # Options are always intraday (no daily options scanner exists)
    is_intraday_mode = (watchlist_name in ('intraday_stocks', 'intraday_options') 
                        or instrument_type in ('both', 'options'))
    
    run_options_scan = can_scan_options and is_intraday_mode
    run_intraday_stocks = can_scan_stocks and is_intraday_mode
    run_daily_scan = can_scan_stocks and not is_intraday_mode
    
    print(f"🔍 Manual scan: instrument_type={instrument_type}, watchlist={watchlist_name}, "
          f"options={run_options_scan}, intraday_stocks={run_intraday_stocks}, daily={run_daily_scan}")
    
    if run_options_scan:
        # Intraday options scanner
        results = run_intraday_scan_batched(
            INTRADAY_OPTIONS_UNIVERSE,
            scan_intraday_option,
            max_workers=4,
            batch_size=5,
            batch_delay=0.7,
            fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
        )

        if not results:
            cached_options = scanner_cache.get('intraday-options', {}).get('data') or []
            if cached_options:
                results = cached_options

        option_candidates = []
        min_conf = bot_state['settings'].get('min_confidence', 75)
        
        for r in sorted(results, key=lambda x: x['score'], reverse=True)[:10]:
            confidence = min(100, max(50, int(40 + r['score'] * 4)))
            option_signal = {
                'symbol': r['symbol'],
                'action': 'BUY',
                'confidence': confidence,
                'entry': r['premium'],
                'stop_loss': r['stop_premium'],
                'target': r['target_1_premium'],
                'reason': ', '.join(r['signals'][:3]),
                'instrument_type': 'option',
                'option_type': r['option_type'],
                'contract': r['contract'],
                'strike': r['strike'],
                'expiry': r['expiry'],
                'dte': r['dte'],
                'premium': r['premium'],
                'stock_price': r['price'],
                'scan_type': 'intraday'
            }
            option_candidates.append(option_signal)
            if confidence >= min_conf:
                signals.append(option_signal)

        # If options mode is active but strict threshold filtered everything,
        # show top candidates for DISPLAY ONLY (marked below-threshold so auto-trade skips them).
        if not signals and instrument_type == 'options':
            if option_candidates:
                for oc in option_candidates[:5]:
                    oc['_below_threshold'] = True
                signals = option_candidates[:5]
                response_message = f'No options met {min_conf}% confidence; showing top candidates (will NOT auto-trade).'
            else:
                live_fallback = build_live_option_fallback_signals(
                    ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
                    max_candidates=6
                )
                if live_fallback:
                    for lf in live_fallback:
                        lf['_below_threshold'] = True
                    signals = live_fallback
                    response_message = f'No options met {min_conf}% confidence; showing fallback candidates (will NOT auto-trade).'
    
    if run_intraday_stocks:
        # Intraday stock scanner
        results = run_intraday_scan_batched(
            INTRADAY_STOCK_UNIVERSE,
            scan_intraday_stock,
            max_workers=5,
            batch_size=6,
            batch_delay=0.6,
            fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
        )

        if not results:
            cached_stocks = scanner_cache.get('intraday-stocks', {}).get('data') or []
            if cached_stocks:
                results = cached_stocks
        
        for r in sorted(results, key=lambda x: x['score'], reverse=True)[:10]:
            confidence = min(100, max(50, int(40 + r['score'] * 4)))
            if confidence >= bot_state['settings'].get('min_confidence', 75):
                signals.append({
                    'symbol': r['symbol'],
                    'action': 'BUY' if r['direction'] == 'BULLISH' else 'SELL',
                    'confidence': confidence,
                    'entry': r['price'],
                    'stop_loss': r['stop_loss'],
                    'target': r['target_1'],
                    'reason': ', '.join(r['signals'][:3]),
                    'instrument_type': 'stock',
                    'atr': r.get('atr', 0),
                    'scan_type': 'intraday'
                })
    
    if run_daily_scan:
        # Standard daily stock scan (swing trades)
        symbols = WATCHLISTS.get(watchlist_name, WATCHLISTS['top_50'])
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(calculate_technical_indicators, sym): sym for sym in symbols}
            
            for future in as_completed(futures):
                try:
                    data = future.result()
                    if data:
                        signal = analyze_for_strategy(data, strategy)
                        if signal and signal['confidence'] >= bot_state['settings'].get('min_confidence', 75):
                            signal['instrument_type'] = 'stock'
                            signal['scan_type'] = 'daily'
                            signals.append(signal)
                except Exception as e:
                    print(f"Scan error: {e}")
    
    # Sort by confidence
    signals.sort(key=lambda x: x['confidence'], reverse=True)
    signals = signals[:10]  # Top 10 signals

    if not signals and (run_options_scan or run_intraday_stocks) and not run_daily_scan:
        response_message = 'No intraday setups found. Try again in 1-2 minutes.'

    # Local fallback when live market provider is rate-limited
    min_conf = bot_state['settings'].get('min_confidence', 75)
    if not signals:
        fallback_signals = load_local_fallback_signals(
            is_intraday_mode=is_intraday_mode,
            instrument_type=instrument_type,
            min_confidence=min_conf
        )
        if fallback_signals:
            signals = fallback_signals[:10]
            response_message = 'Using local cached scanner results (live provider temporarily rate-limited).'

    # Final pass: always refresh signal entry prices to latest available quotes
    signals = refresh_signal_entries_with_live_prices(signals, force_refresh=True)
    
    with BOT_STATE_LOCK:
        bot_state['signals'] = signals
        bot_state['last_scan'] = datetime.now().isoformat()
        save_bot_state()
    
    return jsonify({
        'success': True,
        'signals': signals,
        'count': len(signals),
        'timestamp': bot_state['last_scan'],
        'message': response_message
    })


def load_local_fallback_signals(is_intraday_mode: bool, instrument_type: str, min_confidence: int = 75):
    """Load fallback signals from local scanner output files when live scans fail.

    This keeps the UI useful during Yahoo rate-limit windows.
    """
    try:
        fallback = []
        allow_stock = instrument_type in ('stocks', 'both')
        allow_options = instrument_type in ('options', 'both')

        if is_intraday_mode:
            # No reliable local options fallback file exists; avoid returning stock fallbacks in options-only mode.
            if not allow_stock and allow_options:
                return []

            intraday_path = 'triple_confirmation_intraday.json'
            if os.path.exists(intraday_path):
                with open(intraday_path, 'r') as f:
                    data = json.load(f) or {}
                rows = data.get('results', [])[:20]
                for row in rows:
                    confidence = min(95, max(55, int(45 + float(row.get('score', 0)) * 4)))
                    if confidence < min_confidence:
                        continue
                    direction = row.get('direction', 'BULLISH')
                    entry = float(row.get('current_price', 0) or 0)
                    stop = float(row.get('stop_loss', 0) or 0)
                    target = float(row.get('target_1', 0) or 0)
                    if entry <= 0:
                        continue
                    fallback.append({
                        'symbol': row.get('ticker', ''),
                        'action': 'BUY' if direction == 'BULLISH' else 'SELL',
                        'confidence': confidence,
                        'entry': entry,
                        'stop_loss': stop if stop > 0 else round(entry * 0.98, 2),
                        'target': target if target > 0 else round(entry * 1.02, 2),
                        'reason': ', '.join((row.get('signals') or [])[:3]),
                        'instrument_type': 'stock',
                        'scan_type': 'intraday',
                        'rsi': 50.0,
                        'volume_ratio': float(row.get('volume_ratio', 1.0) or 1.0),
                    })
        else:
            if not allow_stock:
                return []

            picks_path = 'top_picks.json'
            if os.path.exists(picks_path):
                with open(picks_path, 'r') as f:
                    data = json.load(f) or {}
                rows = (data.get('stocks', []) + data.get('etfs', []))[:20]
                for row in rows:
                    score = float(row.get('score', 0) or 0)
                    confidence = min(95, max(50, int(40 + score * 6)))
                    if confidence < min_confidence:
                        continue
                    md = row.get('data') or {}
                    entry = float(md.get('price', 0) or 0)
                    atr = float(md.get('atr', 0) or 0)
                    if entry <= 0:
                        continue
                    signals = row.get('signals') or []
                    bullish = any('bullish' in str(s).lower() for s in signals)
                    fallback.append({
                        'symbol': row.get('ticker', ''),
                        'action': 'BUY' if bullish else 'SELL',
                        'confidence': confidence,
                        'entry': entry,
                        'stop_loss': round(entry - (atr * 1.5), 2) if atr > 0 else round(entry * 0.97, 2),
                        'target': round(entry + (atr * 2.0), 2) if atr > 0 else round(entry * 1.03, 2),
                        'reason': ', '.join(signals[:3]),
                        'instrument_type': 'stock',
                        'scan_type': 'daily',
                        'rsi': float(md.get('rsi', 50.0) or 50.0),
                        'volume_ratio': float(md.get('volume_ratio', 1.0) or 1.0),
                        'atr': atr,
                    })

        fallback = [s for s in fallback if s.get('symbol')]
        fallback.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        return fallback[:10]
    except Exception as e:
        print(f"Fallback signal load error: {e}")
        return []

def build_live_option_fallback_signals(symbols, max_candidates=6):
    """Build lightweight intraday option candidates from live option chains."""
    candidates = []
    min_dte_days = get_min_option_dte_days()
    for symbol in symbols:
        try:
            spot, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
            if not spot or spot <= 0:
                continue

            expirations = cached_get_option_dates(symbol)
            if not expirations:
                continue

            today_dt = datetime.now()
            best_exp = expirations[0]
            for exp in expirations:
                try:
                    dte = (datetime.strptime(exp, '%Y-%m-%d') - today_dt).days
                    if min_dte_days <= dte <= 5:
                        best_exp = exp
                        break
                except Exception:
                    pass

            chain = cached_get_option_chain(symbol, best_exp)
            if not chain:
                continue

            def _atm_signal(df, option_type):
                if df is None or df.empty:
                    return None
                rows = df.copy()
                rows['dist'] = abs(rows['strike'] - float(spot))
                atm = rows.nsmallest(1, 'dist')
                if atm.empty:
                    return None
                row = atm.iloc[0]
                strike = float(row.get('strike', 0) or 0)
                last = float(row.get('lastPrice', 0) or 0)
                bid = float(row.get('bid', 0) or 0)
                ask = float(row.get('ask', 0) or 0)
                premium = last if last > 0 else (round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0)
                if premium <= 0:
                    return None
                try:
                    dte_val = max(0, (datetime.strptime(best_exp, '%Y-%m-%d') - today_dt).days)
                except Exception:
                    dte_val = 0
                if dte_val < min_dte_days:
                    return None
                return {
                    'symbol': symbol,
                    'action': 'BUY',
                    'confidence': 55,
                    'entry': round(premium, 2),
                    'stop_loss': round(premium * 0.6, 2),
                    'target': round(premium * 1.4, 2),
                    'target_2': round(premium * 1.8, 2),
                    'reason': 'Live option-chain fallback candidate',
                    'instrument_type': 'option',
                    'option_type': option_type,
                    'contract': f"{symbol} {best_exp} {strike:.0f}{'C' if option_type == 'call' else 'P'}",
                    'strike': strike,
                    'expiry': best_exp,
                    'dte': dte_val,
                    'premium': round(premium, 2),
                    'stock_price': round(float(spot), 2),
                    'scan_type': 'intraday'
                }

            call_sig = _atm_signal(chain.calls, 'call')
            put_sig = _atm_signal(chain.puts, 'put')
            if call_sig:
                candidates.append(call_sig)
            if put_sig:
                candidates.append(put_sig)

            if len(candidates) >= max_candidates:
                break
        except Exception:
            continue

    return candidates[:max_candidates]

@app.route('/api/bot/auto_cycle', methods=['POST'])
def bot_auto_cycle():
    """
    Run automatic trading cycle:
    1. Check existing positions for stop loss / target hits
    2. Scan for signals if bot is running
    3. Execute trades automatically if auto_trade is enabled
    """
    global bot_state
    
    # Quick check in-memory flag BEFORE loading full state from disk
    # Avoids reading ~7K-line JSON file every 10s when bot is stopped
    if not bot_state.get('running', False):
        return jsonify({
            'success': False,
            'message': 'Bot is not running',
            'trades_executed': [],
            'exits_triggered': []
        })
    
    # Reload state from file to ensure we have latest
    load_bot_state()
    
    # Re-check after reload (in case bot was stopped via another client)
    if not bot_state.get('running', False):
        return jsonify({
            'success': False,
            'message': 'Bot is not running',
            'trades_executed': [],
            'exits_triggered': []
        })
    
    # Debug: log auto_trade status
    print(f"🤖 Auto-cycle: running={bot_state.get('running')}, auto_trade={bot_state.get('auto_trade')}")
    
    # =========================================================================
    # STEP 1: Check existing positions for SL/Target hits (ALWAYS runs)
    # =========================================================================
    exits_triggered = []
    account_mode = bot_state.get('account_mode', 'demo')
    account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
    positions = account.get('positions', [])
    
    if positions:
        # Get live prices for all position symbols
        # For stocks: fetch stock price. For options: fetch option premium.
        position_symbols = list(set(p['symbol'] for p in positions))
        live_stock_prices = {}
        
        # Batch fetch live stock prices (single API call via cache)
        live_stock_prices = cached_batch_prices(position_symbols, period='1d', interval='1m', prepost=True)
        missing_symbols = [s for s in position_symbols if s not in live_stock_prices]
        if missing_symbols:
            quote_api_prices = fetch_quote_api_batch(missing_symbols)
            for sym, price in quote_api_prices.items():
                if sym not in live_stock_prices:
                    live_stock_prices[sym] = price
        still_missing = [s for s in position_symbols if s not in live_stock_prices]
        for sym in still_missing:
            info = cached_get_ticker_info(sym)
            info_price = None
            if info:
                info_price = info.get('regularMarketPrice') or info.get('currentPrice')
            if info_price is not None:
                live_stock_prices[sym] = float(info_price)
        
        # Fetch live option premiums for option positions (cached chains)
        option_premiums = {}  # keyed by (symbol, expiry, strike, opt_type)
        option_positions_by_symbol = {}
        for pos in positions:
            if pos.get('instrument_type') == 'option':
                sym = pos['symbol']
                if sym not in option_positions_by_symbol:
                    option_positions_by_symbol[sym] = []
                option_positions_by_symbol[sym].append(pos)
        
        for symbol, opt_positions in option_positions_by_symbol.items():
            try:
                available_dates = cached_get_option_dates(symbol)
                for pos in opt_positions:
                    expiry = pos.get('expiry', '')
                    strike = pos.get('strike', 0)
                    opt_type = pos.get('option_type', 'call')
                    
                    matched_expiry = expiry if expiry in available_dates else None
                    if not matched_expiry:
                        for d in available_dates:
                            if expiry and expiry in d:
                                matched_expiry = d
                                break
                    
                    if matched_expiry:
                        chain = cached_get_option_chain(symbol, matched_expiry)
                        if chain:
                            df = chain.calls if opt_type == 'call' else chain.puts
                            if not df.empty and strike > 0:
                                strike_match = df[abs(df['strike'] - strike) < 0.01]
                                if not strike_match.empty:
                                    row = strike_match.iloc[0]
                                    last = float(row.get('lastPrice', 0))
                                    bid = float(row.get('bid', 0))
                                    ask = float(row.get('ask', 0))
                                    premium = last if last > 0 else (round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0)
                                    if premium > 0:
                                        key = (symbol, expiry, strike, opt_type)
                                        option_premiums[key] = premium
            except Exception as e:
                if not _is_expected_no_data_error(e):
                    _log_fetch_event('bot-options-error', symbol, f"⚠️ Error fetching options data for {symbol}: {e}", cooldown=180)
        
        # Check each position for exit triggers
        positions_to_remove = []
        
        # Pre-fetch 5-min data for all day-trade stock positions (single batch)
        # This avoids per-position API calls inside the loop
        dynamic_any_updated = False
        intraday_5min_data = {}  # {symbol: DataFrame}
        day_trade_stock_symbols = list(set(
            p['symbol'] for p in positions
            if p.get('trade_type') == 'day' and p.get('instrument_type') != 'option'
        ))
        if day_trade_stock_symbols:
            def _fetch_5min(sym):
                return (sym, cached_get_history(sym, period='5d', interval='5m'))
            with ThreadPoolExecutor(max_workers=min(len(day_trade_stock_symbols), 10)) as executor:
                for sym, df_5m in executor.map(lambda s: _fetch_5min(s), day_trade_stock_symbols):
                    if df_5m is not None and not df_5m.empty:
                        intraday_5min_data[sym] = df_5m
        
        # Pre-fetch daily data for swing stock positions to compute high watermark
        # This ensures trailing stops account for after-hours/pre-market peaks
        # that may have occurred while the bot wasn't running
        swing_high_watermarks = {}  # {symbol: highest_price_since_entry}
        swing_low_watermarks = {}   # {symbol: lowest_price_since_entry}
        swing_stock_symbols = list(set(
            p['symbol'] for p in positions
            if p.get('trade_type') == 'swing' and p.get('instrument_type') != 'option'
        ))
        if swing_stock_symbols:
            def _fetch_swing_hist(sym):
                # Get intraday data with pre/post market to capture after-hours peaks
                return (sym, cached_get_history(sym, period='5d', interval='5m'))
            with ThreadPoolExecutor(max_workers=min(len(swing_stock_symbols), 10)) as executor:
                for sym, df_hist in executor.map(lambda s: _fetch_swing_hist(s), swing_stock_symbols):
                    if df_hist is not None and not df_hist.empty:
                        # Find the position's entry timestamp to filter relevant data
                        sym_positions = [p for p in positions if p['symbol'] == sym]
                        for sp in sym_positions:
                            entry_ts = sp.get('timestamp', '')
                            if entry_ts:
                                try:
                                    from datetime import datetime as dt_cls
                                    entry_dt = dt_cls.fromisoformat(entry_ts.replace('Z', '+00:00'))
                                    # Make entry_dt timezone-aware if df_hist index is tz-aware
                                    if df_hist.index.tz is not None and entry_dt.tzinfo is None:
                                        import pytz
                                        entry_dt = pytz.UTC.localize(entry_dt)
                                    elif df_hist.index.tz is None and entry_dt.tzinfo is not None:
                                        entry_dt = entry_dt.replace(tzinfo=None)
                                    # Get data since entry
                                    since_entry = df_hist[df_hist.index >= entry_dt]
                                    if not since_entry.empty:
                                        swing_high_watermarks[sym] = float(since_entry['High'].max())
                                        swing_low_watermarks[sym] = float(since_entry['Low'].min())
                                except Exception as e:
                                    print(f"⚠️ Error computing watermark for {sym}: {e}")
                            
                            # Fallback: if no valid timestamp, use all available data
                            if sym not in swing_high_watermarks:
                                swing_high_watermarks[sym] = float(df_hist['High'].max())
                                swing_low_watermarks[sym] = float(df_hist['Low'].min())
        
        
        _cycle_now_iso = datetime.now().isoformat()
        _any_price_updated = False
        for pos in positions:
            symbol = pos['symbol']
            is_option = pos.get('instrument_type') == 'option'
            
            if is_option:
                key = (symbol, pos.get('expiry', ''), pos.get('strike', 0), pos.get('option_type', 'call'))
                if key not in option_premiums:
                    continue
                current_price = option_premiums[key]
                pos['current_price'] = current_price  # Update for display
                pos['last_price_update'] = _cycle_now_iso
                _any_price_updated = True
            else:
                if symbol not in live_stock_prices:
                    continue
                current_price = live_stock_prices[symbol]
                pos['current_price'] = current_price
                pos['last_price_update'] = _cycle_now_iso
                _any_price_updated = True
                
            entry_price = pos.get('entry_price', 0)
            stop_loss = pos.get('stop_loss', 0)
            target = pos.get('target', 0)
            quantity = pos.get('quantity', 0)
            side = pos.get('side', 'LONG')
            multiplier = 100 if is_option else 1
            
            # DYNAMIC SL/TARGET UPDATE for intraday stocks in uptrend
            # Uses VWAP, ATR, EMA to recalculate SL and target based on current conditions
            # Falls back to simple percentage trailing stop for options or if data unavailable
            dynamic_updated = False
            if not is_option and entry_price > 0 and pos.get('trade_type', 'swing') == 'day':
                pre_df = intraday_5min_data.get(symbol)
                result = recalculate_intraday_sl_target(symbol, entry_price, stop_loss, target, side, df=pre_df)
                if result:
                    new_sl, new_target, signals = result
                    sl_changed = (new_sl != stop_loss)
                    tgt_changed = (new_target != target)
                    if sl_changed or tgt_changed:
                        dynamic_updated = True
                        dynamic_any_updated = True
                        if sl_changed:
                            pos['stop_loss'] = new_sl
                            stop_loss = new_sl
                        if tgt_changed:
                            pos['target'] = new_target
                            target = new_target
                        sig_str = ', '.join(signals)
                        print(f"🔄 Dynamic SL/Target update for {symbol}: SL=${stop_loss:.2f} Target=${target:.2f} [{sig_str}]")
            
            # TRAILING STOP FALLBACK: ATR-based or simple % trailing for options or if dynamic didn't update
            if not dynamic_updated:
                trailing_mode = str(bot_state['settings'].get('trailing_stop', 'atr')).strip()
                if not trailing_mode:
                    trailing_mode = 'atr'  # Default to ATR if empty
                min_profit_to_trail = 0.005  # 0.5% minimum profit before trailing activates
                
                # For SWING trades: use high/low watermark instead of current_price
                # This catches after-hours/pre-market peaks that occurred while bot was offline
                is_swing = pos.get('trade_type', 'swing') == 'swing'
                if is_swing and not is_option:
                    if side == 'LONG':
                        trail_reference_price = swing_high_watermarks.get(symbol, current_price)
                        # Use the higher of current price and historical high
                        trail_reference_price = max(trail_reference_price, current_price)
                        if trail_reference_price > current_price:
                            print(f"📊 {symbol} SWING: Using high watermark ${trail_reference_price:.2f} for trailing (current: ${current_price:.2f})")
                    else:  # SHORT
                        trail_reference_price = swing_low_watermarks.get(symbol, current_price)
                        trail_reference_price = min(trail_reference_price, current_price)
                        if trail_reference_price < current_price:
                            print(f"📊 {symbol} SWING: Using low watermark ${trail_reference_price:.2f} for trailing (current: ${current_price:.2f})")
                else:
                    trail_reference_price = current_price
                
                if trailing_mode == 'atr' and entry_price > 0:
                    # ==== ATR-BASED TRAILING STOP ====
                    # Uses 1.5x ATR from reference price (high watermark for swing, current for day)
                    # This lets trades breathe during normal volatility
                    pos_atr = pos.get('_cached_atr', 0)
                    if pos_atr <= 0:
                        # Estimate ATR from entry price (roughly 1-2% for most stocks)
                        pos_atr = entry_price * 0.015
                    atr_multiplier = 1.5
                    
                    if side == 'LONG':
                        profit_pct = (trail_reference_price - entry_price) / entry_price
                        if profit_pct >= min_profit_to_trail:
                            new_trailing_stop = round(trail_reference_price - (pos_atr * atr_multiplier), 2)
                            if new_trailing_stop > stop_loss and new_trailing_stop > entry_price:
                                pos['stop_loss'] = new_trailing_stop
                                stop_loss = new_trailing_stop
                                print(f"📈 ATR Trailing stop UP for {symbol}: ${stop_loss:.2f} (ATR=${pos_atr:.2f}, ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
                    else:  # SHORT
                        profit_pct = (entry_price - trail_reference_price) / entry_price
                        if profit_pct >= min_profit_to_trail:
                            new_trailing_stop = round(trail_reference_price + (pos_atr * atr_multiplier), 2)
                            if (stop_loss == 0 or new_trailing_stop < stop_loss) and new_trailing_stop < entry_price:
                                pos['stop_loss'] = new_trailing_stop
                                stop_loss = new_trailing_stop
                                print(f"📉 ATR Trailing stop DOWN for {symbol}: ${stop_loss:.2f} (ATR=${pos_atr:.2f}, ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
                else:
                    # ==== FIXED % TRAILING STOP (legacy fallback) ====
                    try:
                        trailing_pct = float(trailing_mode) / 100
                    except (ValueError, TypeError):
                        trailing_pct = 0.02  # Default 2%
                    if trailing_pct > 0 and entry_price > 0:
                        if side == 'LONG':
                            profit_pct = (trail_reference_price - entry_price) / entry_price
                            if profit_pct >= min_profit_to_trail:
                                new_trailing_stop = trail_reference_price * (1 - trailing_pct)
                                if new_trailing_stop > stop_loss and new_trailing_stop > entry_price:
                                    pos['stop_loss'] = round(new_trailing_stop, 2)
                                    stop_loss = pos['stop_loss']
                                    print(f"📈 Trailing stop moved UP for {symbol}: ${stop_loss:.2f} (ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
                        else:  # SHORT
                            profit_pct = (entry_price - trail_reference_price) / entry_price
                            if profit_pct >= min_profit_to_trail:
                                new_trailing_stop = trail_reference_price * (1 + trailing_pct)
                                if (stop_loss == 0 or new_trailing_stop < stop_loss) and new_trailing_stop < entry_price:
                                    pos['stop_loss'] = round(new_trailing_stop, 2)
                                    stop_loss = pos['stop_loss']
                                    print(f"📉 Trailing stop moved DOWN for {symbol}: ${stop_loss:.2f} (ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
            
            exit_reason = None
            exit_price = current_price
            
            # Track whether stop was trailed into profit (for exit reason labeling)
            _stop_is_trailing = (stop_loss > entry_price) if side == 'LONG' else (0 < stop_loss < entry_price)
            
            # ===== MAX LOSS % GUARD FOR OPTIONS =====
            # Prevents catastrophic option losses (e.g., -50% GOOGL calls)
            if not exit_reason and is_option and entry_price > 0:
                max_option_loss_pct = float(bot_state['settings'].get('max_option_loss_pct', 40)) / 100
                if side == 'LONG':
                    option_loss_pct = (entry_price - current_price) / entry_price
                else:
                    option_loss_pct = (current_price - entry_price) / entry_price
                if option_loss_pct >= max_option_loss_pct:
                    exit_reason = 'MAX_LOSS_GUARD'
                    exit_price = current_price
                    display_name = pos.get('contract', symbol)
                    print(f"🛡️ MAX LOSS GUARD: {display_name} down {option_loss_pct*100:.1f}% (limit: {max_option_loss_pct*100:.0f}%) — force closing")
            
            # ===== 0DTE EXPIRY PROTECTION =====
            # Close options that expire today or tomorrow if not in profit
            # Prevents holding short-dated options overnight that will likely expire worthless
            if is_option and bot_state['settings'].get('close_0dte_before_expiry', True):
                try:
                    import pytz
                    et_tz = pytz.timezone('US/Eastern')
                    now_et = datetime.now(et_tz)
                    expiry_str = pos.get('expiry', '')
                    if expiry_str:
                        expiry_dt = datetime.strptime(expiry_str, '%Y-%m-%d')
                        days_to_expiry = (expiry_dt.date() - now_et.date()).days
                        
                        # Close at 3:00 PM ET if expiring today (0DTE)
                        if days_to_expiry <= 0 and now_et.hour >= 15:
                            pnl_check = (current_price - entry_price) if side == 'LONG' else (entry_price - current_price)
                            exit_reason = 'EXPIRY_PROTECTION'
                            exit_price = current_price
                            print(f"⏰ EXPIRY PROTECTION: Closing {pos.get('contract', symbol)} - expires today, P&L: ${pnl_check * quantity * multiplier:.2f}")
                        
                        # Close at 3:45 PM ET if expiring tomorrow and losing money
                        elif days_to_expiry == 1 and now_et.hour >= 15 and now_et.minute >= 45:
                            pnl_check = (current_price - entry_price) if side == 'LONG' else (entry_price - current_price)
                            if pnl_check < 0:  # Only force-close if losing
                                exit_reason = 'EXPIRY_PROTECTION'
                                exit_price = current_price
                                print(f"⏰ EXPIRY PROTECTION: Closing losing {pos.get('contract', symbol)} - expires tomorrow, P&L: ${pnl_check * quantity * multiplier:.2f}")
                except Exception as e:
                    print(f"⚠️ Error in expiry protection for {symbol}: {e}")
            
            # ===== PARTIAL PROFIT-TAKING =====
            # When target is hit: sell 50% at Target 1, move stop to breakeven, let rest run to Target 2
            if not exit_reason and bot_state['settings'].get('partial_profit_taking', True):
                target_2 = pos.get('target_2', 0)
                partial_sold = pos.get('_partial_sold', False)
                
                if not partial_sold and target > 0 and quantity >= 2:
                    target_hit = (side == 'LONG' and current_price >= target) or \
                                 (side == 'SHORT' and current_price <= target)
                    
                    if target_hit:
                        # Sell 50% of position
                        sell_qty = max(1, quantity // 2)
                        remaining_qty = quantity - sell_qty
                        
                        if side == 'LONG':
                            partial_pnl = (current_price - entry_price) * sell_qty * multiplier
                        else:
                            partial_pnl = (entry_price - current_price) * sell_qty * multiplier
                        partial_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                        if side == 'SHORT':
                            partial_pnl_pct = -partial_pnl_pct
                        
                        # Log partial exit trade
                        partial_trade = {
                            'symbol': symbol,
                            'contract': pos.get('contract', ''),
                            'action': 'PARTIAL_SELL' if side == 'LONG' else 'PARTIAL_COVER',
                            'side': side,
                            'instrument_type': pos.get('instrument_type', 'stock'),
                            'option_type': pos.get('option_type', ''),
                            'source': pos.get('source', 'bot'),
                            'quantity': sell_qty,
                            'entry_price': entry_price,
                            'exit_price': current_price,
                            'price': current_price,
                            'pnl': partial_pnl,
                            'pnl_pct': partial_pnl_pct,
                            'reason': 'PARTIAL_TARGET_HIT',
                            'timestamp': datetime.now().isoformat(),
                            'auto_exit': True,
                            'trade_type': pos.get('trade_type', 'swing')
                        }
                        account['trades'].append(partial_trade)
                        
                        # Update position: reduce quantity, move stop to breakeven, set new target
                        pos['quantity'] = remaining_qty
                        pos['stop_loss'] = entry_price  # Move stop to breakeven
                        if target_2 > 0:
                            pos['target'] = target_2  # Set target to Target 2
                        else:
                            # If no target_2, set to 1.5x the original target distance
                            if side == 'LONG':
                                pos['target'] = round(entry_price + (target - entry_price) * 1.5, 2)
                            else:
                                pos['target'] = round(entry_price - (entry_price - target) * 1.5, 2)
                        pos['_partial_sold'] = True
                        
                        # Update local vars for continued processing
                        quantity = remaining_qty
                        stop_loss = pos['stop_loss']
                        target = pos['target']
                        
                        display_name = pos.get('contract', symbol) if is_option else symbol
                        print(f"🎯 PARTIAL EXIT: Sold {sell_qty} of {display_name} @ ${current_price:.2f} | P&L: ${partial_pnl:.2f} ({partial_pnl_pct:.1f}%) | Remaining: {remaining_qty}, new SL=breakeven, new Target=${target:.2f}")
                        
                        exits_triggered.append({
                            'symbol': symbol,
                            'contract': pos.get('contract', ''),
                            'instrument_type': pos.get('instrument_type', 'stock'),
                            'reason': 'PARTIAL_TARGET_HIT',
                            'entry_price': entry_price,
                            'exit_price': current_price,
                            'current_price': current_price,
                            'pnl': partial_pnl,
                            'pnl_pct': partial_pnl_pct,
                            'quantity': sell_qty,
                            'note': f'Partial exit {sell_qty} of {sell_qty + remaining_qty}'
                        })
                        
                        # Don't set exit_reason — position stays open with remaining qty
                        # Continue to check other exit conditions with updated values
            
            # Check exit conditions based on position side (full exit)
            if not exit_reason:
                if side == 'LONG':
                    if stop_loss > 0 and current_price <= stop_loss:
                        exit_reason = 'TRAILING_STOP' if _stop_is_trailing else 'STOP_LOSS'
                        exit_price = current_price  # Use actual fill price, not stop level
                    elif target > 0 and current_price >= target:
                        exit_reason = 'TARGET_HIT'
                        exit_price = current_price
                else:  # SHORT position
                    if stop_loss > 0 and current_price >= stop_loss:
                        exit_reason = 'TRAILING_STOP' if _stop_is_trailing else 'STOP_LOSS'
                        exit_price = current_price  # Use actual fill price, not stop level
                    elif target > 0 and current_price <= target:
                        exit_reason = 'TARGET_HIT'
                        exit_price = current_price
            
            # TIME-BASED EXIT: Close DAY TRADE positions at 3:45 PM ET
            # Only applies to positions with trade_type='day', NOT swing trades
            if not exit_reason:
                try:
                    import pytz
                    et_tz = pytz.timezone('US/Eastern')
                    now_et = datetime.now(et_tz)
                    # EOD at 3:45 PM ET (15 min before close to ensure fills)
                    is_eod = (now_et.hour == 15 and now_et.minute >= 45) or now_et.hour >= 16
                    
                    # Only close if explicitly marked as day trade
                    trade_type = pos.get('trade_type', 'swing')  # Default to swing if not specified
                    is_day_trade = (trade_type == 'day')
                    
                    if is_eod and is_day_trade:
                        exit_reason = 'END_OF_DAY'
                        exit_price = current_price
                        print(f"⏰ END_OF_DAY exit triggered for {symbol} @ ${current_price:.2f} (day trade)")
                except Exception as e:
                    print(f"⚠️ Error checking EOD exit for {symbol}: {e}")
            
            if exit_reason:
                # Calculate P&L with multiplier (×100 for options)
                if side == 'LONG':
                    pnl = (exit_price - entry_price) * quantity * multiplier
                else:
                    pnl = (entry_price - exit_price) * quantity * multiplier
                
                pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                if side == 'SHORT':
                    pnl_pct = -pnl_pct
                
                # Mark position for removal
                positions_to_remove.append(pos)
                
                # Log the exit trade with instrument info
                exit_trade = {
                    'symbol': symbol,
                    'contract': pos.get('contract', ''),
                    'action': 'SELL' if side == 'LONG' else 'BUY_TO_COVER',
                    'side': side,
                    'instrument_type': pos.get('instrument_type', 'stock'),
                    'option_type': pos.get('option_type', ''),
                    'source': pos.get('source', 'bot'),
                    'quantity': quantity,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'price': exit_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'reason': exit_reason,
                    'timestamp': datetime.now().isoformat(),
                    'auto_exit': True,
                    'trade_type': pos.get('trade_type', 'swing')
                }
                account['trades'].append(exit_trade)
                
                display_name = pos.get('contract', symbol) if is_option else symbol
                exits_triggered.append({
                    'symbol': symbol,
                    'contract': pos.get('contract', ''),
                    'instrument_type': pos.get('instrument_type', 'stock'),
                    'reason': exit_reason,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'current_price': current_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'quantity': quantity
                })
                
                emoji_map = {'TARGET_HIT': '🎯', 'TRAILING_STOP': '📈', 'MAX_LOSS_GUARD': '🛡️', 'END_OF_DAY': '⏰', 'EXPIRY_PROTECTION': '⏰'}
                emoji = emoji_map.get(exit_reason, '🛑')
                print(f"{emoji} AUTO-EXIT: {display_name} - {exit_reason} | Entry: ${entry_price:.2f} → Exit: ${exit_price:.2f} | P&L: ${pnl:.2f} ({pnl_pct:.1f}%)")
        
        # Save state if any price/SL/target updates were made
        # Only persist when something actually changed to avoid unnecessary disk I/O
        if (dynamic_any_updated or _any_price_updated) and not positions_to_remove:
            with BOT_STATE_LOCK:
                save_bot_state()
        
        # Remove closed positions and recalculate balance
        if positions_to_remove:
            with BOT_STATE_LOCK:
                for pos in positions_to_remove:
                    if pos in account['positions']:
                        account['positions'].remove(pos)
                # Recalculate balance from trade history (authoritative)
                account['balance'] = recalculate_balance(account)
                save_bot_state()
    
    # =========================================================================
    # TRADING HOURS CHECK: Only scan/trade 8:30 AM - 3:00 PM CT
    # Position monitoring (Step 1 above) still runs 24/7
    # =========================================================================
    now_ct = datetime.now(ZoneInfo('America/Chicago'))
    ct_hour, ct_min = now_ct.hour, now_ct.minute

    # End-of-session analysis: run once/day at or after 3:00 PM CT
    # Focuses on stop-loss impact vs target outcomes to explain win-rate drops
    if now_ct.weekday() < 5 and (ct_hour > 15 or (ct_hour == 15 and ct_min >= 0)):
        analysis_date = now_ct.strftime('%Y-%m-%d')
        existing_analysis = bot_state.get('daily_analysis', {})
        if existing_analysis.get('date') != analysis_date:
            with BOT_STATE_LOCK:
                load_bot_state()
                account_mode = bot_state.get('account_mode', 'demo')
                account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                daily_analysis = generate_daily_trade_analysis(account, analysis_date_str=analysis_date)
                bot_state['daily_analysis'] = daily_analysis
                save_bot_state()
            print(
                f"📊 END-SESSION ANALYSIS ({analysis_date}) | "
                f"Stocks WR: {daily_analysis['stocks']['win_rate']}% | "
                f"Stock SL: {daily_analysis['stocks']['stop_loss_count']} | "
                f"Stock Targets: {daily_analysis['stocks']['target_hit_count']} | "
                f"Stock P&L: ${daily_analysis['stocks']['net_pnl']:.2f}"
            )

    market_open = (ct_hour > 8 or (ct_hour == 8 and ct_min >= 30))  # 8:30 AM CT
    market_close = ct_hour < 15  # Before 3:00 PM CT
    in_trading_hours = market_open and market_close and now_ct.weekday() < 5  # Mon-Fri only

    if not in_trading_hours:
        return jsonify({
            'success': True,
            'message': f'Outside trading hours (8:30 AM - 3:00 PM CT). Current CT: {now_ct.strftime("%I:%M %p")}. Position monitoring still active.',
            'signals': bot_state.get('signals', []),
            'daily_analysis': bot_state.get('daily_analysis'),
            'trades_executed': [],
            'exits_triggered': exits_triggered,
            'skipped_scan': True
        })

    # =========================================================================
    # STEP 2: Check scan interval / rate limiting
    # =========================================================================
    scan_interval = bot_state['settings'].get('scan_interval', 5) * 60  # Convert minutes to seconds
    last_scan = bot_state.get('last_scan')
    skip_scan = False
    skip_message = ''
    
    if last_scan:
        try:
            last_scan_time = datetime.fromisoformat(last_scan)
            seconds_since_scan = (datetime.now() - last_scan_time).total_seconds()
            # Only skip if auto_trade is OFF - when auto_trade is ON, always scan
            if seconds_since_scan < scan_interval and not bot_state.get('auto_trade', False):
                return jsonify({
                    'success': True,
                    'message': f'Next scan in {int(scan_interval - seconds_since_scan)}s',
                    'signals': bot_state.get('signals', []),
                    'trades_executed': [],
                    'exits_triggered': exits_triggered,
                    'skipped_scan': True
                })
            # Even with auto_trade, don't scan more than once per 30 seconds to avoid rate limits
            # BUT still try to execute trades from cached signals
            elif seconds_since_scan < 30:
                skip_scan = True
                skip_message = f'Rate limited, using cached signals (next scan in {int(30 - seconds_since_scan)}s)'
        except:
            pass
    
    signals = []
    
    # Run scan (unless rate limited)
    if not skip_scan:
        strategy = bot_state.get('strategy', 'trend_following')
        watchlist_name = bot_state['settings'].get('watchlist', 'top_50')
        instrument_type = bot_state['settings'].get('instrument_type', 'stocks')
        min_confidence = bot_state['settings'].get('min_confidence', 75)

        # Helper: filter out delisted/invalid symbols
        def filter_valid_symbols(symbols):
            return [s for s in symbols if resolve_symbol_or_name.is_valid_symbol(s)]

        # Determine which scanners to run based on instrument_type AND watchlist
        can_scan_stocks = instrument_type in ('stocks', 'both')
        can_scan_options = instrument_type in ('options', 'both')
        is_intraday_mode = (watchlist_name in ('intraday_stocks', 'intraday_options') 
                            or instrument_type in ('both', 'options'))
        
        run_options_scan = can_scan_options and is_intraday_mode
        run_intraday_stocks = can_scan_stocks and is_intraday_mode
        run_daily_scan = can_scan_stocks and not is_intraday_mode
        
        print(f"🤖 Auto-cycle scan: instrument_type={instrument_type}, watchlist={watchlist_name}, "
              f"options={run_options_scan}, intraday_stocks={run_intraday_stocks}, daily={run_daily_scan}")
        
        if run_options_scan:
            print(f"🤖 Running INTRADAY OPTIONS scanner")
            valid_options = filter_valid_symbols(INTRADAY_OPTIONS_UNIVERSE)
            intraday_results = run_intraday_scan_batched(
                valid_options,
                scan_intraday_option,
                max_workers=4,
                batch_size=5,
                batch_delay=0.7,
                fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
            )

            option_candidates = []
            for r in sorted(intraday_results, key=lambda x: x['score'], reverse=True)[:10]:
                confidence = min(100, max(50, int(40 + r['score'] * 4)))
                option_signal = {
                    'symbol': r['symbol'],
                    'action': 'BUY',
                    'confidence': confidence,
                    'entry': r['premium'],
                    'stop_loss': r['stop_premium'],
                    'target': r['target_1_premium'],
                    'target_2': r.get('target_2_premium'),
                    'reason': ', '.join(r['signals'][:3]),
                    'score': r['score'],
                    'direction': r['direction'],
                    'rsi': r['rsi'],
                    'volume_ratio': r['volume_ratio'],
                    'instrument_type': 'option',
                    'option_type': r['option_type'],
                    'contract': r['contract'],
                    'strike': r['strike'],
                    'expiry': r['expiry'],
                    'dte': r['dte'],
                    'premium': r['premium'],
                    'stock_price': r['price'],
                    'iv': r.get('iv', 0),
                    'open_interest': r.get('open_interest', 0),
                    'scan_type': 'intraday'
                }
                option_candidates.append(option_signal)
                if confidence >= min_confidence:
                    signals.append(option_signal)

            # In options-only mode, keep the feed populated for DISPLAY ONLY (below-threshold flag blocks auto-trade)
            if not signals and instrument_type == 'options':
                if option_candidates:
                    for oc in option_candidates[:5]:
                        oc['_below_threshold'] = True
                    signals = option_candidates[:5]
                    print(f"🤖 No options met {min_confidence}% confidence; showing top candidates (display only)")
                else:
                    live_fallback = build_live_option_fallback_signals(
                        ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
                        max_candidates=6
                    )
                    if live_fallback:
                        for lf in live_fallback:
                            lf['_below_threshold'] = True
                        signals = live_fallback
                        print(f"🤖 Using live option fallback (display only, below {min_confidence}% threshold)")
            print(f"🤖 Options scan complete: {len(intraday_results)} results, {len(signals)} signals above {min_confidence}% confidence")
        
        if run_intraday_stocks:
            print(f"🤖 Running INTRADAY STOCKS scanner")
            valid_stocks = filter_valid_symbols(INTRADAY_STOCK_UNIVERSE)
            intraday_results = run_intraday_scan_batched(
                valid_stocks,
                scan_intraday_stock,
                max_workers=5,
                batch_size=6,
                batch_delay=0.6,
                fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
            )
            for r in sorted(intraday_results, key=lambda x: x['score'], reverse=True)[:10]:
                confidence = min(100, max(50, int(40 + r['score'] * 4)))
                if confidence >= min_confidence:
                    signals.append({
                        'symbol': r['symbol'],
                        'action': 'BUY' if r['direction'] == 'BULLISH' else 'SELL',
                        'confidence': confidence,
                        'entry': r['price'],
                        'stop_loss': r['stop_loss'],
                        'target': r['target_1'],
                        'target_2': r.get('target_2'),
                        'reason': ', '.join(r['signals'][:3]),
                        'score': r['score'],
                        'direction': r['direction'],
                        'rsi': r['rsi'],
                        'volume_ratio': r['volume_ratio'],
                        'atr': r.get('atr', 0),
                        'instrument_type': 'stock',
                        'scan_type': 'intraday'
                    })
        
        if run_daily_scan:
            print(f"🤖 Running DAILY/SWING scanner on {watchlist_name}")
            symbols = WATCHLISTS.get(watchlist_name, WATCHLISTS['top_50'])
            valid_symbols = filter_valid_symbols(symbols)
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(calculate_technical_indicators, sym): sym for sym in valid_symbols}
                for future in as_completed(futures):
                    try:
                        data = future.result()
                        if data:
                            signal = analyze_for_strategy(data, strategy)
                            if signal and signal['confidence'] >= min_confidence:
                                signal['instrument_type'] = 'stock'
                                signal['scan_type'] = 'daily'
                                signals.append(signal)
                    except Exception as e:
                        print(f"Auto-scan error: {e}")
        signals.sort(key=lambda x: x['confidence'], reverse=True)
        signals = signals[:10]

        if not signals:
            fallback_signals = load_local_fallback_signals(
                is_intraday_mode=is_intraday_mode,
                instrument_type=instrument_type,
                min_confidence=min_confidence
            )
            if fallback_signals:
                signals = fallback_signals[:10]
                print("🤖 Using local cached fallback signals (live provider rate-limited)")

        # Final pass: always refresh signal entry prices to latest available quotes
        signals = refresh_signal_entries_with_live_prices(signals, force_refresh=True)

        with BOT_STATE_LOCK:
            bot_state['signals'] = signals
            bot_state['last_scan'] = datetime.now().isoformat()
            save_bot_state()
    else:
        # Use cached signals when rate limited
        signals = bot_state.get('signals', [])
        current_instrument = bot_state.get('settings', {}).get('instrument_type', 'stocks')
        if current_instrument == 'options':
            signals = [s for s in signals if s.get('instrument_type') == 'option']
            if not signals:
                min_conf_skip = bot_state['settings'].get('min_confidence', 75)
                fallback = build_live_option_fallback_signals(
                    ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
                    max_candidates=5
                )
                if fallback:
                    for lf in fallback:
                        lf['_below_threshold'] = True
                    signals = fallback
                    skip_message = (skip_message + f' | showing live option fallback (below {min_conf_skip}% threshold, display only)') if skip_message else f'showing live option fallback (below {min_conf_skip}% threshold, display only)'
        elif current_instrument == 'stocks':
            signals = [s for s in signals if s.get('instrument_type') != 'option']
        print(f"🤖 {skip_message} - using {len(signals)} cached signals")
    
    trades_executed = []

    # De-duplicate signals by execution identity (keep highest confidence per key)
    if signals:
        deduped = {}
        for sig in signals:
            sig_key = (
                sig.get('instrument_type', 'stock'),
                sig.get('symbol', ''),
                sig.get('contract', ''),
                sig.get('action', 'BUY')
            )
            prev = deduped.get(sig_key)
            if not prev or float(sig.get('confidence', 0) or 0) > float(prev.get('confidence', 0) or 0):
                deduped[sig_key] = sig
        signals = list(deduped.values())

    # Keep bot_status/live pane aligned with what auto_cycle is actively using,
    # especially in rate-limited skip-scan paths where we build fallback signals.
    if skip_scan:
        with BOT_STATE_LOCK:
            load_bot_state()
            bot_state['signals'] = signals
            save_bot_state()
    
    # Auto-execute trades if enabled
    auto_trade_enabled = bot_state.get('auto_trade', False)
    print(f"🤖 Checking auto-trade: enabled={auto_trade_enabled}, signals_count={len(signals)}")
    
    skipped_reasons = []  # Track why signals were skipped
    daily_trade_count = 0
    today_closed_count = 0
    
    if auto_trade_enabled and signals:
        account_mode = bot_state.get('account_mode', 'demo')
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        max_positions = bot_state['settings'].get('max_positions', 5)
        position_size = bot_state['settings'].get('position_size', 1000)
        execution_signals = list(signals)

        def _get_live_available_balance():
            with BOT_STATE_LOCK:
                load_bot_state()
                acct_live = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                return float(recalculate_balance(acct_live))

        def _reserve_execution_slot(exec_key):
            now_ts = time.time()
            with AUTO_TRADE_DEDUP_LOCK:
                stale_keys = [k for k, ts in AUTO_TRADE_EXECUTION_GUARD.items() if now_ts - ts > AUTO_TRADE_DEDUP_SECONDS * 3]
                for k in stale_keys:
                    AUTO_TRADE_EXECUTION_GUARD.pop(k, None)

                last_exec = AUTO_TRADE_EXECUTION_GUARD.get(exec_key, 0)
                remaining = int(AUTO_TRADE_DEDUP_SECONDS - (now_ts - last_exec))
                if now_ts - last_exec < AUTO_TRADE_DEDUP_SECONDS:
                    return False, max(1, remaining)

                AUTO_TRADE_EXECUTION_GUARD[exec_key] = now_ts
                return True, 0
        
        # Determine what the current settings expect
        current_watchlist = bot_state['settings'].get('watchlist', 'top_50')
        current_instrument = bot_state['settings'].get('instrument_type', 'stocks')
        expect_intraday_only = (current_watchlist in ('intraday_stocks', 'intraday_options') 
                                or current_instrument in ('options', 'both'))
        
        # Check if we're past trading cutoff (3:30 PM ET for day trades)
        import pytz
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        is_past_day_trade_cutoff = (now_et.hour == 15 and now_et.minute >= 30) or now_et.hour >= 16
        
        current_positions = len(account.get('positions', []))
        current_symbols = [p['symbol'] for p in account.get('positions', [])]
        current_stock_sides = {
            p.get('symbol'): p.get('side', 'LONG')
            for p in account.get('positions', [])
            if p.get('instrument_type', 'stock') != 'option'
        }
        # For options, also track contracts to avoid duplicates
        current_contracts = [p.get('contract', '') for p in account.get('positions', []) if p.get('instrument_type') == 'option']
        
        # --- OVERTRADING GUARDS ---
        # 1. Max total trades per day (entries only)
        today_str = datetime.now().strftime('%Y-%m-%d')
        today_entries = [t for t in account.get('trades', []) 
                if t.get('timestamp', '').startswith(today_str) 
                and not t.get('auto_exit') 
                and t.get('action') in ('BUY', 'SELL', 'SHORT')
                and (t.get('auto_trade') or t.get('source') == 'bot')]
        daily_trade_count = len(today_entries)
        MAX_DAILY_TRADES = int(bot_state['settings'].get('max_daily_trades', 20))

        # Closed trades for today's P&L/calendar comparison (different from entry cap)
        today_closed_trades = [t for t in account.get('trades', [])
            if t.get('timestamp', '').startswith(today_str)
            and t.get('pnl') is not None
            and (t.get('auto_trade') or t.get('source') == 'bot')]
        today_closed_count = len(today_closed_trades)
        
        # 2. Track per-symbol entry count today
        from collections import Counter
        sym_trade_counts = Counter(t['symbol'] for t in today_entries)
        MAX_PER_SYMBOL = max(1, int(bot_state['settings'].get('max_per_symbol_daily', 6)))

        # Prefer symbols with fewer entries today to avoid repeatedly picking the same top ticker
        signals = sorted(
            signals,
            key=lambda s: (
                sym_trade_counts.get(s.get('symbol', ''), 0),
                -float(s.get('confidence', 0) or 0),
                s.get('symbol', '')
            )
        )
        
        # 3. Cooldown: track recent stop-loss exits (no re-entry within 30 min)
        recent_stop_losses = {}  # {symbol: last_stop_loss_timestamp}
        recent_auto_exits = {}   # {symbol: last_auto_exit_timestamp}
        for t in account.get('trades', []):
            if (t.get('timestamp', '').startswith(today_str) 
                and t.get('reason') == 'STOP_LOSS' 
                and t.get('auto_exit')):
                recent_stop_losses[t['symbol']] = t['timestamp']
            if (t.get('timestamp', '').startswith(today_str)
                and t.get('auto_exit')
                and t.get('reason') not in ('ALREADY_CLOSED',)):
                sym = t.get('symbol')
                ts = t.get('timestamp')
                if sym and ts:
                    prev = recent_auto_exits.get(sym)
                    if not prev or ts > prev:
                        recent_auto_exits[sym] = ts
        COOLDOWN_MINUTES = 30
        REENTRY_COOLDOWN_MINUTES = int(bot_state['settings'].get('reentry_cooldown_minutes', 10))
        
        if daily_trade_count >= MAX_DAILY_TRADES:
            skipped_reasons.append(f"Max daily trades reached ({MAX_DAILY_TRADES})")
            execution_signals = []  # Skip execution only; keep display signals
        
        # Early guard: if balance is too low for even one position, skip execution loop entirely
        _available_balance = float(account.get('balance', 0))
        if _available_balance < position_size * 0.5 and execution_signals:
            skipped_reasons.append(f"Insufficient balance: ${_available_balance:.2f} < 50% of position_size ${position_size}")
            print(f"⚠️ Balance too low for trading: ${_available_balance:.2f} (need ≥${position_size * 0.5:.2f})")
            execution_signals = []
        
        print(f"\U0001f916 Auto-trade check: balance=${account.get('balance', 0):.2f}, positions={current_positions}/{max_positions}, today_entries={daily_trade_count}/{MAX_DAILY_TRADES}, today_closed={today_closed_count}, past_cutoff={is_past_day_trade_cutoff}")
        
        for signal in execution_signals:
            # Check if we can add more positions
            if current_positions >= max_positions:
                skipped_reasons.append(f"{signal['symbol']}: Max positions reached ({max_positions})")
                break
            
            # CONFIDENCE GUARD: Never auto-trade signals that were below the min_confidence threshold
            min_conf_exec = bot_state['settings'].get('min_confidence', 75)
            if signal.get('_below_threshold') or (signal.get('confidence', 0) < min_conf_exec):
                skipped_reasons.append(f"{signal.get('contract', signal['symbol'])}: Below {min_conf_exec}% confidence ({signal.get('confidence', 0)}%)")
                continue
            
            is_option_signal = signal.get('instrument_type') == 'option'
            
            # Skip if already have position in this symbol (for stocks) or contract (for options)
            if is_option_signal:
                if signal.get('contract', '') in current_contracts:
                    skipped_reasons.append(f"{signal.get('contract')}: Already have this option position")
                    continue
            else:
                intended_side = 'LONG' if signal.get('action') == 'BUY' else 'SHORT'
                existing_side = current_stock_sides.get(signal['symbol'])
                if existing_side and existing_side != intended_side:
                    skipped_reasons.append(f"{signal['symbol']}: Opposite-side position already open ({existing_side})")
                    continue
            
            # --- PER-SYMBOL OVERTRADING GUARDS ---
            sym = signal['symbol']
            
            # Check per-symbol daily cap
            if sym_trade_counts.get(sym, 0) >= MAX_PER_SYMBOL:
                skipped_reasons.append(f"{sym}: Max {MAX_PER_SYMBOL} trades/day reached")
                continue
            
            # Check cooldown after stop loss (30 min)
            if sym in recent_stop_losses:
                last_sl_time = datetime.fromisoformat(recent_stop_losses[sym])
                minutes_since_sl = (datetime.now() - last_sl_time).total_seconds() / 60
                if minutes_since_sl < COOLDOWN_MINUTES:
                    skipped_reasons.append(f"{sym}: Cooldown ({COOLDOWN_MINUTES - minutes_since_sl:.0f} min left after SL)")
                    continue

            # Short cooldown after any auto-exit to prevent immediate re-entry loops
            if REENTRY_COOLDOWN_MINUTES > 0 and sym in recent_auto_exits:
                try:
                    last_exit_time = datetime.fromisoformat(recent_auto_exits[sym])
                    minutes_since_exit = (datetime.now() - last_exit_time).total_seconds() / 60
                    if minutes_since_exit < REENTRY_COOLDOWN_MINUTES:
                        skipped_reasons.append(f"{sym}: Re-entry cooldown ({REENTRY_COOLDOWN_MINUTES - minutes_since_exit:.0f} min left)")
                        continue
                except Exception:
                    pass
            
            # Determine trade type based on scan_type and time
            scan_type = signal.get('scan_type', 'daily')
            is_intraday_signal = scan_type == 'intraday'
            
            # GUARD: Reject stale daily/swing signals when intraday mode is active
            # This catches cached signals from a previous scan with different settings
            if expect_intraday_only and scan_type == 'daily':
                skipped_reasons.append(f"{signal['symbol']}: Stale daily/swing signal rejected (intraday mode active)")
                print(f"\u26a0\ufe0f Rejected stale DAILY signal for {signal['symbol']} - current mode expects intraday only")
                continue
            
            # GUARD: Reject option signals when instrument_type is 'stocks' only
            if current_instrument == 'stocks' and is_option_signal:
                skipped_reasons.append(f"{signal['symbol']}: Option signal rejected (instrument_type is stocks)")
                continue
            
            # GUARD: Reject stock signals when instrument_type is 'options' only
            if current_instrument == 'options' and not is_option_signal:
                skipped_reasons.append(f"{signal['symbol']}: Stock signal rejected (instrument_type is options)")
                continue
            
            # Skip day trades after 3:30 PM ET cutoff
            if is_intraday_signal and is_past_day_trade_cutoff:
                skipped_reasons.append(f"{signal['symbol']}: Past day trade cutoff (3:30 PM ET)")
                continue
            
            # Only execute BUY signals automatically (safer) - in demo mode allow SELL too
            if signal['action'] != 'BUY':
                if account_mode == 'demo':
                    pass  # Allow it to continue
                else:
                    skipped_reasons.append(f"{signal['symbol']}: SELL signal (auto-trade only executes BUY for safety)")
                    print(f"\U0001f916 Skipping {signal['symbol']}: SELL signal not auto-traded in real mode")
                    continue

            # Duplicate execution guard across overlapping auto-cycle requests/tabs
            exec_key = (
                signal.get('instrument_type', 'stock'),
                signal.get('symbol', ''),
                signal.get('contract', ''),
                signal.get('action', 'BUY')
            )
            if is_option_signal:
                # === Option auto-trade ===
                expiry = signal.get('expiry', '')
                min_dte_days = get_min_option_dte_days()
                if is_option_expiry_blocked(expiry, min_dte_days=min_dte_days):
                    skipped_reasons.append(f"{signal.get('contract', signal.get('symbol'))}: blocked (min DTE {min_dte_days})")
                    continue

                signal_premium = float(signal.get('premium', signal['entry']))
                premium = get_live_option_premium(
                    signal['symbol'],
                    expiry,
                    signal.get('strike', 0),
                    signal.get('option_type', 'call'),
                    fallback=signal_premium,
                    option_ticker=signal.get('option_ticker', '')
                )
                if not premium or premium <= 0:
                    premium = signal_premium

                signal_stop = float(signal.get('stop_loss', premium * 0.5) or (premium * 0.5))
                risk_pct = max(0.01, min(0.95, 1 - (signal_stop / signal_premium))) if signal_premium > 0 else 0.5
                live_stop_loss = round(premium * (1 - risk_pct), 2)

                contracts_qty = max(1, int(position_size / (premium * 100)))
                cost = contracts_qty * premium * 100

                pre_balance = _get_live_available_balance()
                if cost > pre_balance:
                    skipped_reasons.append(f"{signal.get('contract')}: Insufficient balance (need ${cost:.2f}, have ${pre_balance:.2f})")
                    continue
                
                # Options are always day trades (especially 0-2 DTE)
                option_trade_type = 'day' if signal.get('dte', 0) <= 2 else 'swing'

                reserved, remaining = _reserve_execution_slot(exec_key)
                if not reserved:
                    skipped_reasons.append(f"{signal['symbol']}: Duplicate auto-trade blocked ({remaining}s cooldown)")
                    continue
                
                with BOT_STATE_LOCK:
                    load_bot_state()
                    account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                    current_balance = float(recalculate_balance(account))
                    if cost > current_balance:
                        skipped_reasons.append(f"{signal.get('contract')}: Balance changed before execution (need ${cost:.2f}, have ${current_balance:.2f})")
                        continue

                    # Estimate ATR from signal data (no extra API call)
                    _cached_atr = premium * 0.15  # ~15% of premium for options
                    
                    position = {
                        'symbol': signal['symbol'],
                        'contract': signal.get('contract', ''),
                        'option_ticker': signal.get('option_ticker', ''),
                        'instrument_type': 'option',
                        'option_type': signal.get('option_type', 'call'),
                        'strike': signal.get('strike', 0),
                        'expiry': signal.get('expiry', ''),
                        'dte': signal.get('dte', 0),
                        'side': 'LONG',
                        'quantity': contracts_qty,
                        'entry_price': premium,
                        'current_price': premium,
                        'stop_loss': live_stop_loss,
                        'target': signal['target'],
                        'target_2': signal.get('target_2', signal['target'] * 1.5),
                        'timestamp': datetime.now().isoformat(),
                        'auto_trade': True,
                        'source': 'bot',
                        'trade_type': option_trade_type,
                        '_cached_atr': _cached_atr
                    }
                    account['positions'].append(position)
                    
                    trade = {
                        'symbol': signal['symbol'],
                        'contract': signal.get('contract', ''),
                        'option_ticker': signal.get('option_ticker', ''),
                        'action': 'BUY',
                        'side': 'LONG',
                        'instrument_type': 'option',
                        'option_type': signal.get('option_type', 'call'),
                        'quantity': contracts_qty,
                        'price': premium,
                        'cost': cost,
                        'strike': signal.get('strike', 0),
                        'expiry': signal.get('expiry', ''),
                        'confidence': signal['confidence'],
                        'reason': signal.get('reason', ''),
                        'timestamp': datetime.now().isoformat(),
                        'auto_trade': True,
                        'source': 'bot'
                    }
                    account['trades'].append(trade)
                    # Recalculate balance from trade history (authoritative)
                    account['balance'] = recalculate_balance(account)
                    save_bot_state()
                
                trades_executed.append({
                    'symbol': signal['symbol'],
                    'contract': signal.get('contract', ''),
                    'option_ticker': signal.get('option_ticker', ''),
                    'action': 'BUY',
                    'side': 'LONG',
                    'instrument_type': 'option',
                    'option_type': signal.get('option_type', 'call'),
                    'quantity': contracts_qty,
                    'price': premium,
                    'confidence': signal['confidence']
                })
                
                current_positions += 1
                current_contracts.append(signal.get('contract', ''))
                
                print(f"\U0001f916 AUTO-TRADE: \U0001f7e2 BUY {contracts_qty} x {signal.get('contract', signal['symbol'])} @ ${premium:.2f} (OPTION)")
            
            else:
                # === Stock auto-trade (existing logic) ===
                signal_entry = float(signal['entry'])
                live_price, _ = cached_get_price(signal['symbol'], period='1d', interval='1m', prepost=True)
                price = float(live_price) if live_price and live_price > 0 else signal_entry

                signal_stop = float(signal.get('stop_loss', signal_entry * 0.95) or (signal_entry * 0.95))
                risk_pct = max(0.005, min(0.5, 1 - (signal_stop / signal_entry))) if signal_entry > 0 else 0.05
                live_stop_loss = round(price * (1 - risk_pct), 2)

                quantity = int(position_size / price)
                
                if quantity < 1:
                    skipped_reasons.append(f"{signal['symbol']}: Quantity < 1 (price too high for position size)")
                    continue
                
                cost = quantity * price

                pre_balance = _get_live_available_balance()
                if cost > pre_balance:
                    skipped_reasons.append(f"{signal['symbol']}: Insufficient balance (need ${cost:.2f}, have ${pre_balance:.2f})")
                    continue
                
                side = 'LONG' if signal['action'] == 'BUY' else 'SHORT'
                
                # CRITICAL: Intraday scans MUST create day trades (close at EOD)
                # Swing trades from intraday scanner would block new trades forever
                scan_type = signal.get('scan_type', 'daily')
                trade_type = 'day' if scan_type == 'intraday' else 'swing'
                
                if scan_type == 'intraday' and trade_type != 'day':
                    print(f"⚠️ ALERT: Intraday signal for {signal['symbol']} forced to day trade (was {trade_type})")
                    trade_type = 'day'
                
                # Estimate ATR from signal's own atr field (no extra API call)
                _cached_atr_stock = signal.get('atr', price * 0.015)

                reserved, remaining = _reserve_execution_slot(exec_key)
                if not reserved:
                    skipped_reasons.append(f"{signal['symbol']}: Duplicate auto-trade blocked ({remaining}s cooldown)")
                    continue
                
                with BOT_STATE_LOCK:
                    load_bot_state()
                    account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                    current_balance = float(recalculate_balance(account))
                    if cost > current_balance:
                        skipped_reasons.append(f"{signal['symbol']}: Balance changed before execution (need ${cost:.2f}, have ${current_balance:.2f})")
                        continue

                    position, is_new = add_or_update_position(
                        account, signal['symbol'], side, quantity, price,
                        stop_loss=live_stop_loss, target=signal['target'],
                        extra_fields={'auto_trade': True, 'source': 'bot', 'instrument_type': 'stock', 'trade_type': trade_type, '_cached_atr': _cached_atr_stock}
                    )
                    
                    trade = {
                        'symbol': signal['symbol'],
                        'action': signal['action'],
                        'side': side,
                        'instrument_type': 'stock',
                        'quantity': quantity,
                        'price': price,
                        'confidence': signal['confidence'],
                        'reason': signal.get('reason', ''),
                        'timestamp': datetime.now().isoformat(),
                        'auto_trade': True,
                        'source': 'bot',
                        'trade_type': trade_type
                    }
                    account['trades'].append(trade)
                    # Recalculate balance from trade history (authoritative)
                    account['balance'] = recalculate_balance(account)
                    save_bot_state()
                
                trades_executed.append({
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'side': side,
                    'instrument_type': 'stock',
                    'quantity': quantity,
                    'price': price,
                    'confidence': signal['confidence']
                })
                
                current_positions += 1
                current_symbols.append(signal['symbol'])
                current_stock_sides[signal['symbol']] = side
                
                action_emoji = '\U0001f7e2' if signal['action'] == 'BUY' else '\U0001f534'
                print(f"\U0001f916 AUTO-TRADE: {action_emoji} {signal['action']} {quantity} shares of {signal['symbol']} at ${price:.2f} ({side})")
    
    return jsonify({
        'success': True,
        'signals': signals,
        'count': len(signals),
        'trades_executed': trades_executed,
        'exits_triggered': exits_triggered,
        'today_entry_count': daily_trade_count if auto_trade_enabled else 0,
        'today_closed_count': today_closed_count if auto_trade_enabled else 0,
        'skipped_reasons': skipped_reasons,
        'auto_trade_enabled': bot_state.get('auto_trade', False),
        'timestamp': bot_state.get('last_scan'),
        'skipped_scan': skip_scan,
        'message': skip_message if skip_scan else None
    })

@app.route('/api/bot/trade', methods=['POST'])
def bot_trade():
    """Execute a trade"""
    global bot_state
    
    req = request.get_json(force=True)
    symbol = req.get('symbol')
    action = req.get('action')
    quantity = req.get('quantity')
    price = req.get('price')
    account_mode = req.get('account_mode', 'demo')
    
    if not all([symbol, action, quantity, price]):
        return jsonify({'success': False, 'error': 'Missing trade parameters'}), 400

    try:
        quantity = int(quantity)
        price = float(price)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid quantity/price format'}), 400

    if quantity <= 0 or price <= 0:
        return jsonify({'success': False, 'error': 'Quantity and price must be > 0'}), 400
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']

        live_exec_price, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
        if live_exec_price and live_exec_price > 0:
            price = float(live_exec_price)
        
        if action == 'BUY':
            cost = quantity * price
            if cost > account['balance']:
                return jsonify({'success': False, 'error': 'Insufficient balance'}), 400

            # Determine trade_type based on current settings (intraday or swing)
            settings = bot_state.get('settings', {})
            watchlist = settings.get('watchlist', 'top_50')
            instrument_type = settings.get('instrument_type', 'stocks')
            is_intraday_mode = (watchlist in ('intraday_stocks', 'intraday_options') or instrument_type in ('both', 'options'))
            trade_type = 'day' if is_intraday_mode else 'swing'

            # Add to existing position or create new one
            requested_stop = req.get('stop_loss')
            requested_target = req.get('target')
            if requested_stop is not None and float(req.get('price', 0) or 0) > 0:
                requested_price = float(req.get('price'))
                requested_stop = float(requested_stop)
                risk_pct = max(0.005, min(0.5, 1 - (requested_stop / requested_price)))
            else:
                risk_pct = 0.05

            stop_loss = round(price * (1 - risk_pct), 2)

            if requested_target is not None and float(req.get('price', 0) or 0) > 0:
                requested_price = float(req.get('price'))
                target_pct = max(0.005, min(2.0, (float(requested_target) / requested_price) - 1))
                target = round(price * (1 + target_pct), 2)
            else:
                target = round(price * 1.10, 2)

            position, is_new = add_or_update_position(
                account, symbol, 'LONG', quantity, price,
                stop_loss=stop_loss, target=target,
                extra_fields={'instrument_type': 'stock', 'trade_type': trade_type, 'source': 'manual'}
            )

            # Log trade
            trade = {
                'symbol': symbol,
                'action': 'BUY',
                'side': 'LONG',
                'instrument_type': 'stock',
                'source': 'manual',
                'quantity': quantity,
                'price': price,
                'trade_type': trade_type,
                'added_to_existing': not is_new,
                'timestamp': datetime.now().isoformat()
            }
            account['trades'].append(trade)
            
        elif action == 'SELL':
            # First, check if there's a LONG stock position to close
            # Prefer closing manual positions first when selling manually
            long_position = None
            # First pass: look for manual positions
            for pos in account['positions']:
                if pos['symbol'] == symbol and pos.get('side') == 'LONG' and pos.get('instrument_type', 'stock') != 'option' and pos.get('source', 'manual') == 'manual':
                    long_position = pos
                    break
            # Second pass: if no manual position, close any matching position (bot included)
            if not long_position:
                for pos in account['positions']:
                    if pos['symbol'] == symbol and pos.get('side') == 'LONG' and pos.get('instrument_type', 'stock') != 'option':
                        long_position = pos
                        break
            
            if long_position:
                # Close the LONG position - use live price for accuracy
                try:
                    live_price, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
                    if live_price and live_price > 0:
                        price = live_price
                except:
                    pass  # Fall back to passed-in price
                
                pnl = (price - long_position['entry_price']) * long_position['quantity']
                
                # Log trade with P&L
                trade = {
                    'symbol': symbol,
                    'action': 'SELL',
                    'side': 'LONG',
                    'source': long_position.get('source', 'manual'),
                    'quantity': long_position['quantity'],
                    'entry_price': long_position['entry_price'],
                    'price': price,
                    'pnl': pnl,
                    'pnl_pct': ((price - long_position['entry_price']) / long_position['entry_price'] * 100) if long_position['entry_price'] > 0 else 0,
                    'timestamp': datetime.now().isoformat()
                }
                account['trades'].append(trade)
                account['positions'].remove(long_position)
            else:
                # No LONG position to close - create or add to SHORT position (demo only)
                if account_mode == 'demo':
                    requested_stop = req.get('stop_loss')
                    requested_target = req.get('target')
                    if requested_stop is not None and float(req.get('price', 0) or 0) > 0:
                        requested_price = float(req.get('price'))
                        requested_stop = float(requested_stop)
                        risk_pct = max(0.005, min(0.5, (requested_stop / requested_price) - 1))
                    else:
                        risk_pct = 0.05

                    stop_loss = round(price * (1 + risk_pct), 2)

                    if requested_target is not None and float(req.get('price', 0) or 0) > 0:
                        requested_price = float(req.get('price'))
                        target_pct = max(0.005, min(2.0, 1 - (float(requested_target) / requested_price)))
                        target = round(price * (1 - target_pct), 2)
                    else:
                        target = round(price * 0.90, 2)

                    position, is_new = add_or_update_position(
                        account, symbol, 'SHORT', quantity, price,
                        stop_loss=stop_loss, target=target
                    )
                    
                    trade = {
                        'symbol': symbol,
                        'action': 'SELL',
                        'side': 'SHORT',
                        'quantity': quantity,
                        'price': price,
                        'added_to_existing': not is_new,
                        'timestamp': datetime.now().isoformat()
                    }
                    account['trades'].append(trade)
        
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': f'{action} executed for {symbol}',
        'balance': account['balance'],
        'executed_price': round(float(price), 2)
    })

@app.route('/api/bot/trade_option', methods=['POST'])
def bot_trade_option():
    """Execute an option trade (buy call/put) from the intraday options scanner"""
    global bot_state

    req = request.get_json(force=True)
    symbol = req.get('symbol')
    contract = req.get('contract', '')
    option_ticker = req.get('option_ticker', req.get('contract_symbol', ''))
    option_type = req.get('option_type', 'call')  # call or put
    strike = req.get('strike', 0)
    expiry = req.get('expiry', '')
    dte = req.get('dte', 0)
    premium = req.get('premium', 0)
    contracts = req.get('contracts', 1)
    direction = req.get('direction', 'BULLISH')
    stop_premium = req.get('stop_premium', premium * 0.5)
    target_1 = req.get('target_1_premium', premium * 2.0)
    target_2 = req.get('target_2_premium', premium * 3.0)
    account_mode = req.get('account_mode', 'demo')

    try:
        premium = float(premium)
        contracts = int(contracts)
        strike = float(strike or 0)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid option trade parameters'}), 400

    if not symbol or premium <= 0 or contracts <= 0:
        return jsonify({'success': False, 'error': 'Missing option parameters'}), 400

    min_dte_days = get_min_option_dte_days()
    if is_option_expiry_blocked(expiry, min_dte_days=min_dte_days):
        return jsonify({'success': False, 'error': f'Option trade blocked by risk policy: minimum DTE is {min_dte_days} day(s)'}), 400

    live_premium = get_live_option_premium(
        symbol,
        expiry,
        strike,
        option_type,
        fallback=premium,
        option_ticker=option_ticker
    )
    if live_premium and live_premium > 0:
        premium = float(live_premium)

    try:
        stop_premium = float(stop_premium)
    except Exception:
        stop_premium = premium * 0.5

    req_premium = float(req.get('premium', premium) or premium)
    risk_pct = max(0.01, min(0.95, 1 - (stop_premium / req_premium))) if req_premium > 0 else 0.5
    stop_premium = round(premium * (1 - risk_pct), 2)

    try:
        target_1 = float(target_1)
        target_2 = float(target_2)
    except Exception:
        target_1 = premium * 2.0
        target_2 = premium * 3.0

    if req_premium > 0:
        t1_mult = max(1.01, min(10.0, target_1 / req_premium))
        t2_mult = max(1.01, min(15.0, target_2 / req_premium))
        target_1 = round(premium * t1_mult, 2)
        target_2 = round(premium * t2_mult, 2)
    else:
        target_1 = round(target_1, 2)
        target_2 = round(target_2, 2)

    cost = contracts * premium * 100  # Each contract = 100 shares

    with BOT_STATE_LOCK:
        load_bot_state()
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']

        if cost > account.get('balance', 0):
            return jsonify({'success': False, 'error': f'Insufficient balance: ${account["balance"]:.2f} < ${cost:.2f}'}), 400

        # Create option position
        position = {
            'symbol': symbol,
            'contract': contract,
            'option_ticker': option_ticker,
            'instrument_type': 'option',
            'option_type': option_type,
            'strike': strike,
            'expiry': expiry,
            'dte': dte,
            'side': 'LONG',
            'source': 'manual',
            'quantity': contracts,
            'entry_price': premium,
            'current_price': premium,
            'stop_loss': stop_premium,
            'target': target_1,
            'target_2': target_2,
            'timestamp': datetime.now().isoformat()
        }
        account['positions'].append(position)

        # Log trade
        trade = {
            'symbol': symbol,
            'contract': contract,
            'option_ticker': option_ticker,
            'action': 'BUY',
            'side': 'LONG',
            'instrument_type': 'option',
            'option_type': option_type,
            'source': 'manual',
            'quantity': contracts,
            'price': premium,
            'cost': cost,
            'strike': strike,
            'expiry': expiry,
            'timestamp': datetime.now().isoformat()
        }
        account['trades'].append(trade)
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()

    return jsonify({
        'success': True,
        'message': f'Bought {contracts} x {contract} @ ${premium:.2f}',
        'cost': cost,
        'balance': account['balance'],
        'executed_premium': round(float(premium), 2),
        'executed_stop_premium': round(float(stop_premium), 2),
        'executed_target_1_premium': round(float(target_1), 2),
        'executed_target_2_premium': round(float(target_2), 2)
    })

@app.route('/api/bot/close', methods=['POST'])
def bot_close_position():
    """Close a specific position (stock or option)"""
    global bot_state
    
    req = request.get_json(force=True)
    symbol = req.get('symbol')
    account_mode = req.get('account_mode', 'demo')
    instrument_type = req.get('instrument_type', 'stock')  # 'stock' or 'option'
    contract = req.get('contract', '')  # Option contract name for matching
    source = req.get('source', '')  # 'manual' or 'bot' - helps match correct position
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        target_pos = None
        fallback_pos = None
        for pos in account['positions']:
            pos_type = pos.get('instrument_type', 'stock')
            pos_source = pos.get('source', 'manual')
            if instrument_type == 'option':
                # Match option by contract name (unique) or symbol + instrument_type
                if pos_type == 'option' and (pos.get('contract') == contract or pos.get('symbol') == symbol):
                    if source and pos_source == source:
                        target_pos = pos
                        break
                    elif not fallback_pos:
                        fallback_pos = pos
            else:
                # Match stock by symbol, excluding option positions
                if pos.get('symbol') == symbol and pos_type != 'option':
                    if source and pos_source == source:
                        target_pos = pos
                        break
                    elif not fallback_pos:
                        fallback_pos = pos
        
        if not target_pos:
            target_pos = fallback_pos
        
        if not target_pos:
            return jsonify({'success': False, 'error': f'Position not found for {symbol}'}), 404
        
        is_option = target_pos.get('instrument_type') == 'option'
        display_name = target_pos.get('contract', symbol) if is_option else symbol
        
        # Get current price
        try:
            stock_price, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
            if stock_price is None:
                stock_price = target_pos['current_price']
        except:
            stock_price = target_pos['current_price']
        
        if is_option:
            # For options: use current_price (premium) for P&L, multiply by 100
            price = target_pos['current_price']  # Current premium
            pnl = (price - target_pos['entry_price']) * target_pos['quantity'] * 100
        else:
            price = stock_price
            if target_pos['side'] == 'LONG':
                pnl = (price - target_pos['entry_price']) * target_pos['quantity']
            else:
                pnl = (target_pos['entry_price'] - price) * target_pos['quantity']
        
        trade = {
            'symbol': symbol,
            'contract': target_pos.get('contract', ''),
            'action': 'CLOSE',
            'side': target_pos.get('side', 'LONG'),
            'instrument_type': target_pos.get('instrument_type', 'stock'),
            'source': target_pos.get('source', 'manual'),
            'quantity': target_pos['quantity'],
            'entry_price': target_pos['entry_price'],
            'price': price,
            'pnl': pnl,
            'pnl_pct': ((price - target_pos['entry_price']) / target_pos['entry_price'] * 100) if target_pos['entry_price'] > 0 else 0,
            'timestamp': datetime.now().isoformat()
        }
        account['trades'].append(trade)
        account['positions'].remove(target_pos)
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
        
        return jsonify({
            'success': True,
            'message': f'Position closed for {display_name}',
            'pnl': pnl
        })

@app.route('/api/bot/close-all', methods=['POST'])
def bot_close_all():
    """Close all positions"""
    global bot_state
    
    req = request.get_json(force=True)
    account_mode = req.get('account_mode', 'demo')
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        # Batch fetch all position prices in a single API call
        all_symbols = list(set(pos['symbol'] for pos in account['positions']))
        batch_prices = cached_batch_prices(all_symbols, period='1d', interval='1m', prepost=True) if all_symbols else {}
        
        total_pnl = 0
        for pos in list(account['positions']):
            is_option = pos.get('instrument_type') == 'option'
            stock_price = batch_prices.get(pos['symbol'], pos['current_price'])
            
            if is_option:
                price = pos['current_price']  # Option premium
                pnl = (price - pos['entry_price']) * pos['quantity'] * 100
            else:
                price = stock_price
                if pos['side'] == 'LONG':
                    pnl = (price - pos['entry_price']) * pos['quantity']
                else:
                    pnl = (pos['entry_price'] - price) * pos['quantity']
            
            total_pnl += pnl
            
            trade = {
                'symbol': pos['symbol'],
                'contract': pos.get('contract', ''),
                'action': 'CLOSE',
                'side': pos.get('side', 'LONG'),
                'instrument_type': pos.get('instrument_type', 'stock'),
                'source': pos.get('source', 'manual'),
                'quantity': pos['quantity'],
                'entry_price': pos['entry_price'],
                'price': price,
                'pnl': pnl,
                'pnl_pct': ((price - pos['entry_price']) / pos['entry_price'] * 100) if pos.get('entry_price', 0) > 0 else 0,
                'timestamp': datetime.now().isoformat()
            }
            account['trades'].append(trade)
        
        account['positions'] = []
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': 'All positions closed',
        'total_pnl': total_pnl
    })


@app.route('/api/bot/cleanup_duplicate_exits', methods=['POST'])
def bot_cleanup_duplicate_exits():
    """Remove duplicate synthetic exit rows (ALREADY_CLOSED) from trade history.

    Request JSON (optional):
      - account_mode: 'demo' | 'real' | 'all' (default: 'all')
      - dry_run: bool (default: True)
    """
    global bot_state

    req = request.get_json(silent=True) or {}
    account_mode = (req.get('account_mode') or 'all').lower()
    dry_run = bool(req.get('dry_run', True))

    if account_mode not in ('demo', 'real', 'all'):
        return jsonify({'success': False, 'error': 'Invalid account_mode'}), 400

    target_accounts = ['demo', 'real'] if account_mode == 'all' else [account_mode]

    with BOT_STATE_LOCK:
        load_bot_state()

        backup_name = None
        if not dry_run:
            ts = datetime.now().strftime('%Y%m%d%H%M%S')
            backup_name = f"{BOT_STATE_FILE}.bak.cleanup_duplicates.{ts}"
            try:
                Path(backup_name).write_text(json.dumps(bot_state, indent=2))
            except Exception as e:
                return jsonify({'success': False, 'error': f'Failed to create backup: {e}'}), 500

        removed_by_account = {'demo': 0, 'real': 0}

        for acct in target_accounts:
            account = bot_state['demo_account'] if acct == 'demo' else bot_state['real_account']
            trades = account.get('trades', [])
            kept = []
            removed = 0

            for trade in trades:
                if trade.get('reason') == 'ALREADY_CLOSED' and trade.get('auto_exit'):
                    removed += 1
                    continue
                kept.append(trade)

            removed_by_account[acct] = removed
            if not dry_run and removed > 0:
                account['trades'] = kept
                account['balance'] = recalculate_balance(account)

        total_removed = removed_by_account['demo'] + removed_by_account['real']

        if not dry_run and total_removed > 0:
            save_bot_state()

    return jsonify({
        'success': True,
        'dry_run': dry_run,
        'account_mode': account_mode,
        'removed': total_removed,
        'removed_by_account': removed_by_account,
        'backup_file': backup_name,
        'message': (
            f"Would remove {total_removed} duplicate synthetic exits" if dry_run
            else f"Removed {total_removed} duplicate synthetic exits"
        )
    })

# ============================================================================
# INTRADAY SCANNERS FOR AI BOT PAGE
# ============================================================================

# Intraday stock universe - highly liquid names ideal for day trading
# Keep this list SMALL (~25) to avoid Yahoo rate limits
INTRADAY_STOCK_UNIVERSE = [
    'SPY', 'QQQ', 'IWM', 'DIA',
    'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'AMZN', 'META', 'GOOGL', 'NFLX',
    'JPM', 'BAC', 'GS', 'MS', 'C',
    'XOM', 'CVX', 'UNH', 'WMT', 'COST',
    'NKE', 'DIS', 'UBER', 'PYPL', 'COIN',
    'SOFI', 'PLTR', 'SNAP', 'ROKU',
]

# Options-friendly tickers (high volume, tight bid-ask, weekly options)
# Keep this list SMALL (~30) to avoid Yahoo rate limits
INTRADAY_OPTIONS_UNIVERSE = [
    # Core ETFs with massive options volume
    'SPY', 'QQQ', 'IWM', 'DIA', 'GLD', 'SLV',
    # Mega-cap with weekly options
    'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'AMZN', 'META', 'GOOGL', 'NFLX',
    'AVGO', 'CRM', 'ADBE', 'ORCL',
    # Financials
    'JPM', 'BAC', 'V', 'MA', 'WFC',
    # Healthcare
    'UNH', 'LLY', 'ABBV', 'MRK', 'JNJ',
    # Consumer / retail
    'WMT', 'COST', 'MCD', 'HD',
    # High-beta momentum (great for 0-DTE)
    'COIN', 'SOFI', 'PLTR', 'UBER',
]


def recalculate_intraday_sl_target(symbol, entry_price, current_sl, current_target, side='LONG', df=None):
    """
    Dynamically recalculate SL & target for an active intraday stock position
    using 5-min VWAP, ATR, and EMA data.
    
    Args:
        df: Optional pre-fetched 5-min DataFrame. If None, skips (no extra API call).
    
    Rules:
    - Only adjusts if stock is still trending in the position's direction
    - SL only moves UP for LONG (tighter protection), never DOWN
    - Target only moves UP for LONG (capturing more upside), never DOWN
    - Uses VWAP as a floor for SL (price shouldn't close below VWAP in uptrend)
    - ATR-based: SL = max(VWAP, price - 1.5*ATR), Target based on R:R floor
    
    Returns: (new_sl, new_target, signals_list) or None if no data/no update needed
    """
    try:
        if df is None or df.empty or len(df) < 50:
            return None

        # --- VWAP ---
        df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Date'] = df.index.date
        df['Cum_TV'] = df.groupby('Date').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
        df['Cum_V'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TV'] / df['Cum_V']

        # --- EMA ---
        df['EMA5'] = df['Close'].ewm(span=5, adjust=False).mean()
        df['EMA13'] = df['Close'].ewm(span=13, adjust=False).mean()

        # --- ATR ---
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()

        # --- RSI ---
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # Filter to latest session
        latest_date = df.index.date[-1]
        today = df[df.index.date == latest_date]
        if today.empty or len(today) < 5:
            return None

        bar = today.iloc[-1]
        price = float(bar['Close'])
        vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
        atr = float(bar['ATR']) if not pd.isna(bar['ATR']) else price * 0.01
        ema5 = float(bar['EMA5']) if not pd.isna(bar['EMA5']) else price
        ema13 = float(bar['EMA13']) if not pd.isna(bar['EMA13']) else price
        rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50

        signals = []

        if side == 'LONG':
            # Only update if still in uptrend: above VWAP AND EMA5 > EMA13
            above_vwap = price > vwap
            ema_bullish = ema5 > ema13

            if not above_vwap and not ema_bullish:
                return None  # Trend broken — don't revise, let existing SL/target handle it

            if above_vwap:
                signals.append('Above VWAP')
            if ema_bullish:
                signals.append('EMA Bullish')

            # --- Dynamic SL: max(VWAP, price - 1.5*ATR) ---
            # VWAP acts as a strong support floor in an uptrend
            atr_stop = round(price - atr * 1.5, 2)
            vwap_stop = round(vwap, 2)
            new_sl = max(atr_stop, vwap_stop)

            # SL must be above entry (lock in profit) when price has moved enough
            profit_pct = (price - entry_price) / entry_price if entry_price > 0 else 0
            if profit_pct > 0.005:  # >0.5% profit: don't let SL go below entry
                new_sl = max(new_sl, entry_price)

            # Only move SL UP, never down
            if new_sl <= current_sl:
                new_sl = current_sl  # Keep existing (higher) SL

            # --- Dynamic Target: risk-based minimum 1:1.5 R:R, or 2×ATR if larger ---
            is_strong = above_vwap and ema_bullish and rsi < 75
            risk_distance = abs(price - new_sl) if new_sl > 0 else atr * 1.5
            min_target_dist = risk_distance * 1.5  # Floor: 1:1.5 R:R
            target_mult = 2.5 if is_strong else 2.0
            atr_target_dist = atr * target_mult
            target_distance = max(min_target_dist, atr_target_dist)
            new_target = round(price + target_distance, 2)

            # Only move target UP, never down
            if new_target <= current_target:
                new_target = current_target  # Keep existing (higher) target

            if is_strong:
                signals.append(f'Strong trend (RSI {rsi:.0f})')
            if rsi > 75:
                signals.append(f'⚠️ Overbought RSI {rsi:.0f}')

            signals.append(f'ATR ${atr:.2f}')

        else:  # SHORT
            below_vwap = price < vwap
            ema_bearish = ema5 < ema13

            if not below_vwap and not ema_bearish:
                return None

            if below_vwap:
                signals.append('Below VWAP')
            if ema_bearish:
                signals.append('EMA Bearish')

            atr_stop = round(price + atr * 1.5, 2)
            vwap_stop = round(vwap, 2)
            new_sl = min(atr_stop, vwap_stop)

            profit_pct = (entry_price - price) / entry_price if entry_price > 0 else 0
            if profit_pct > 0.005:
                new_sl = min(new_sl, entry_price)

            # Only move SL DOWN for shorts
            if current_sl > 0 and new_sl >= current_sl:
                new_sl = current_sl

            is_strong = below_vwap and ema_bearish and rsi > 25
            risk_distance = abs(new_sl - price) if new_sl > 0 else atr * 1.5
            min_target_dist = risk_distance * 1.5
            target_mult = 2.5 if is_strong else 2.0
            atr_target_dist = atr * target_mult
            target_distance = max(min_target_dist, atr_target_dist)
            new_target = round(price - target_distance, 2)

            # Only move target DOWN for shorts
            if current_target > 0 and new_target >= current_target:
                new_target = current_target

            signals.append(f'ATR ${atr:.2f}')

        return (round(new_sl, 2), round(new_target, 2), signals)

    except Exception as e:
        print(f"⚠️ Error recalculating SL/target for {symbol}: {e}")
        return None


def scan_intraday_stock(symbol):
    """
    Intraday stock scanner using 5-min data.
    Returns a signal dict with VWAP, RSI, MACD, volume spike, and momentum.
    """
    try:
        df = cached_get_history(symbol, period='5d', interval='5m')

        if df is None or df.empty or len(df) < 50:
            return None

        # --- VWAP ---
        df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Date'] = df.index.date
        df['Cum_TV'] = df.groupby('Date').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
        df['Cum_V'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TV'] / df['Cum_V']

        # --- RSI (14-period on 5-min bars) ---
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # --- MACD (fast settings for intraday) ---
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['MACD'] = df['EMA9'] - df['EMA21']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # --- EMA crossover ---
        df['EMA5'] = df['Close'].ewm(span=5, adjust=False).mean()
        df['EMA13'] = df['Close'].ewm(span=13, adjust=False).mean()

        # --- ATR ---
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()

        # Filter to latest session
        latest_date = df.index.date[-1]
        today = df[df.index.date == latest_date]
        if today.empty or len(today) < 10:
            return None

        bar = today.iloc[-1]
        prev = today.iloc[-2]
        price = float(bar['Close'])
        vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
        rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50
        macd_hist = float(bar['MACD_Hist']) if not pd.isna(bar['MACD_Hist']) else 0
        atr = float(bar['ATR']) if not pd.isna(bar['ATR']) else price * 0.01

        avg_vol = float(today['Volume'].mean())
        cur_vol = float(bar['Volume'])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

        ema5 = float(bar['EMA5']) if not pd.isna(bar['EMA5']) else price
        ema13 = float(bar['EMA13']) if not pd.isna(bar['EMA13']) else price
        prev_ema5 = float(prev['EMA5']) if not pd.isna(prev['EMA5']) else price
        prev_ema13 = float(prev['EMA13']) if not pd.isna(prev['EMA13']) else price

        # --- Scoring (0-15) ---
        score = 0
        signals = []
        direction = None

        # VWAP position
        above_vwap = price > vwap
        if above_vwap:
            score += 2
            signals.append('Above VWAP')
        else:
            score += 1
            signals.append('Below VWAP')

        # EMA crossover
        bullish_cross = ema5 > ema13 and prev_ema5 <= prev_ema13
        bearish_cross = ema5 < ema13 and prev_ema5 >= prev_ema13
        ema_bullish = ema5 > ema13
        ema_bearish = ema5 < ema13

        if bullish_cross:
            score += 3
            signals.append('🔥 Fresh EMA Bullish Cross')
        elif ema_bullish:
            score += 2
            signals.append('✅ EMA Bullish')
        elif bearish_cross:
            score += 3
            signals.append('🔥 Fresh EMA Bearish Cross')
        elif ema_bearish:
            score += 2
            signals.append('🔴 EMA Bearish')

        # MACD momentum
        if macd_hist > 0.1:
            score += 3
            signals.append('📈 Strong MACD Momentum')
        elif macd_hist > 0:
            score += 2
            signals.append('MACD Positive')
        elif macd_hist < -0.1:
            score += 3
            signals.append('📉 Strong MACD Bearish')
        elif macd_hist < 0:
            score += 2
            signals.append('MACD Negative')

        # RSI zones
        if rsi < 30:
            score += 2
            signals.append(f'🟢 Oversold RSI {rsi:.0f}')
        elif rsi > 70:
            score += 2
            signals.append(f'🔴 Overbought RSI {rsi:.0f}')
        elif 40 < rsi < 60:
            score += 1
            signals.append(f'RSI Neutral {rsi:.0f}')

        # Volume spike
        if vol_ratio > 2.5:
            score += 3
            signals.append(f'🔥 Surge Vol {vol_ratio:.1f}x')
        elif vol_ratio > 1.5:
            score += 2
            signals.append(f'📊 High Vol {vol_ratio:.1f}x')
        else:
            score += 1

        # ===== SIGNAL CONFLICT FILTER =====
        # Reject signals where EMA and MACD disagree in direction
        # This prevents entries like "EMA Bullish + Strong MACD Bearish"
        ema_direction = 'bullish' if ema_bullish else ('bearish' if ema_bearish else 'neutral')
        macd_direction = 'bullish' if macd_hist > 0 else ('bearish' if macd_hist < 0 else 'neutral')
        
        if ema_direction != 'neutral' and macd_direction != 'neutral' and ema_direction != macd_direction:
            # EMA and MACD conflict — skip this signal
            return None

        # Determine direction
        # VWAP is the primary trend filter:
        # - BULLISH requires price above VWAP (buying into strength)
        # - BEARISH requires price below VWAP (selling into weakness)
        # This prevents shorting in a rising market or buying in a falling one
        # All 3 indicators (VWAP, EMA, MACD) must agree for entry
        bull_count = sum([above_vwap, ema_bullish, macd_hist > 0, rsi < 60])
        bear_count = sum([not above_vwap, ema_bearish, macd_hist < 0, rsi > 40])

        if bull_count >= 3 and above_vwap:
            direction = 'BULLISH'
        elif bear_count >= 3 and not above_vwap:
            direction = 'BEARISH'
        else:
            return None  # No clear direction or VWAP disagrees

        # Minimum score threshold (raised from 7 to 8 for higher quality)
        if score < 8:
            return None

        # Targets & stops
        # Ensure target distance >= stop distance for minimum 1:1 R:R
        if direction == 'BULLISH':
            stop_loss = round(max(vwap, price - atr * 1.5), 2)
            risk = abs(price - stop_loss)
            # Target at 2×ATR, but at minimum must equal the risk distance (1:1 R:R floor)
            target_distance = max(atr * 2.0, risk * 1.5)  # Minimum 1:1.5 R:R
            target_1 = round(price + target_distance, 2)
            target_2 = round(price + target_distance * 1.5, 2)
        else:
            stop_loss = round(min(vwap, price + atr * 1.5), 2)
            risk = abs(stop_loss - price)
            target_distance = max(atr * 2.0, risk * 1.5)
            target_1 = round(price - target_distance, 2)
            target_2 = round(price - target_distance * 1.5, 2)

        reward = abs(target_1 - price)
        rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        return {
            'symbol': symbol,
            'direction': direction,
            'score': score,
            'price': round(price, 2),
            'vwap': round(vwap, 2),
            'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4),
            'volume_ratio': round(vol_ratio, 2),
            'atr': round(atr, 2),
            'stop_loss': stop_loss,
            'target_1': target_1,
            'target_2': target_2,
            'risk_reward': rr_ratio,
            'signals': signals,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        print(f"Intraday scan error for {symbol}: {e}")
        return None


def run_intraday_scan_batched(symbols, scan_fn, max_workers=5, batch_size=6, batch_delay=0.6, fallback_symbols=None):
    """Run intraday scans in small batches to reduce yfinance 429 rate limits.
    Returns a plain list of results (no metadata).
    """
    filtered_symbols = [s for s in symbols if s and s.upper() not in KNOWN_DELISTED]
    if not filtered_symbols:
        return []

    results = []
    for i in range(0, len(filtered_symbols), batch_size):
        chunk = filtered_symbols[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(scan_fn, sym): sym for sym in chunk}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    pass  # individual failures are expected (rate limits, no data)

        if i + batch_size < len(filtered_symbols):
            time.sleep(batch_delay)

    # Fallback: if primary scan returned nothing, try core symbols one by one
    if not results and fallback_symbols:
        fallback_filtered = [s for s in fallback_symbols if s and s.upper() not in KNOWN_DELISTED]
        for symbol in fallback_filtered:
            try:
                result = scan_fn(symbol)
                if result:
                    results.append(result)
                time.sleep(max(0.8, batch_delay))
            except Exception:
                pass

    return results


def scan_intraday_option(symbol):
    """
    Intraday options scanner.
    Finds actionable 0-2 DTE call/put setups based on momentum + options chain data.
    """
    try:
        # --- 1. Momentum analysis on 5-min data (cached) ---
        df = cached_get_history(symbol, period='5d', interval='5m')
        if df is None or df.empty or len(df) < 50:
            return None

        # VWAP
        df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Date'] = df.index.date
        df['Cum_TV'] = df.groupby('Date').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
        df['Cum_V'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TV'] / df['Cum_V']

        # RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # MACD
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['MACD'] = df['EMA9'] - df['EMA21']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # ATR
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr_vals = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr_vals.rolling(14).mean()

        latest_date = df.index.date[-1]
        today = df[df.index.date == latest_date]
        if today.empty or len(today) < 5:
            return None

        bar = today.iloc[-1]
        price = float(bar['Close'])
        vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
        rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50
        macd_hist = float(bar['MACD_Hist']) if not pd.isna(bar['MACD_Hist']) else 0
        atr = float(bar['ATR']) if not pd.isna(bar['ATR']) else price * 0.01

        avg_vol = float(today['Volume'].mean())
        cur_vol = float(bar['Volume'])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

        # Determine direction
        above_vwap = price > vwap
        ema_bull = float(bar['EMA9']) > float(bar['EMA21'])
        ema_bear = not ema_bull
        macd_bull = macd_hist > 0
        macd_bear = macd_hist < 0

        # === SIGNAL CONFLICT FILTER (options) ===
        # Reject if EMA and MACD disagree on direction
        if ema_bull and macd_bear:
            return None  # Conflicting signals
        if ema_bear and macd_bull:
            return None  # Conflicting signals

        bull_pts = sum([above_vwap, ema_bull, macd_bull, rsi < 65])
        bear_pts = sum([not above_vwap, ema_bear, not macd_bull, rsi > 35])

        if bull_pts >= 3:
            direction = 'BULLISH'
            opt_type = 'call'
        elif bear_pts >= 3:
            direction = 'BEARISH'
            opt_type = 'put'
        else:
            return None

        # --- 2. Options chain lookup (cached) ---
        expirations = cached_get_option_dates(symbol)
        if not expirations:
            return None

        # Find nearest expiration based on configured minimum DTE
        today_dt = datetime.now()
        min_dte_days = get_min_option_dte_days()
        best_exp = None
        for exp in expirations:
            exp_dt = datetime.strptime(exp, '%Y-%m-%d')
            dte = (exp_dt - today_dt).days
            if min_dte_days <= dte <= 3:
                best_exp = exp
                break

        if not best_exp:
            # Fallback to closest non-0DTE available
            for exp in expirations:
                try:
                    dte = (datetime.strptime(exp, '%Y-%m-%d') - today_dt).days
                    if dte >= min_dte_days:
                        best_exp = exp
                        break
                except Exception:
                    continue
            if not best_exp:
                return None

        chain = cached_get_option_chain(symbol, best_exp)
        if chain:
            opts = chain.calls if opt_type == 'call' else chain.puts
        else:
            return None

        if opts.empty:
            return None

        # Pick strike closest to current price (ATM)
        opts = opts.copy()
        opts['dist'] = abs(opts['strike'] - price)
        atm = opts.nsmallest(3, 'dist')

        # Pick the best row (highest open interest among close strikes)
        if 'openInterest' in atm.columns:
            atm = atm.fillna({'openInterest': 0})
            best_row = atm.sort_values('openInterest', ascending=False).iloc[0]
        else:
            best_row = atm.iloc[0]

        strike = float(best_row['strike'])
        premium = float(best_row['lastPrice']) if not pd.isna(best_row.get('lastPrice', None)) else 0
        bid = float(best_row['bid']) if not pd.isna(best_row.get('bid', None)) else 0
        ask = float(best_row['ask']) if not pd.isna(best_row.get('ask', None)) else 0
        iv = float(best_row['impliedVolatility']) if not pd.isna(best_row.get('impliedVolatility', None)) else 0
        oi = int(best_row.get('openInterest', 0)) if not pd.isna(best_row.get('openInterest', 0)) else 0
        opt_vol = int(best_row.get('volume', 0)) if not pd.isna(best_row.get('volume', 0)) else 0

        if premium <= 0 and ask > 0:
            premium = round((bid + ask) / 2, 2)
        if premium <= 0:
            return None

        # Scoring
        score = 0
        signals = []

        if direction == 'BULLISH':
            signals.append(f'📞 {opt_type.upper()} setup')
        else:
            signals.append(f'📉 {opt_type.upper()} setup')

        if vol_ratio > 2.0:
            score += 3
            signals.append(f'🔥 Vol Spike {vol_ratio:.1f}x')
        elif vol_ratio > 1.3:
            score += 2
            signals.append(f'📊 High Vol {vol_ratio:.1f}x')
        else:
            score += 1

        if abs(macd_hist) > 0.1:
            score += 3
            signals.append('Strong Momentum')
        elif abs(macd_hist) > 0.03:
            score += 2
        else:
            score += 1

        if iv > 0.5:
            score += 2
            signals.append(f'IV {iv*100:.0f}%')
        elif iv > 0.3:
            score += 1

        if oi > 1000:
            score += 2
            signals.append(f'OI {oi:,}')
        elif oi > 300:
            score += 1

        if opt_vol > 500:
            score += 2
            signals.append(f'Opt Vol {opt_vol:,}')
        elif opt_vol > 100:
            score += 1

        # R:R on premium
        sl_premium = round(premium * 0.50, 2)  # 50% stop
        tp_1 = round(premium * 2.0, 2)          # 2x target
        tp_2 = round(premium * 3.0, 2)          # 3x target

        dte = (datetime.strptime(best_exp, '%Y-%m-%d') - today_dt).days

        # Hard block contracts below configured minimum DTE
        if dte < min_dte_days:
            return None

        if score < 6:
            return None

        contract_name = f"{symbol} ${strike:.0f}{opt_type[0].upper()} {best_exp}"

        return {
            'symbol': symbol,
            'direction': direction,
            'option_type': opt_type,
            'contract': contract_name,
            'option_ticker': str(best_row.get('contractSymbol', '') or ''),
            'strike': strike,
            'expiry': best_exp,
            'dte': max(dte, 0),
            'premium': round(premium, 2),
            'bid': round(bid, 2),
            'ask': round(ask, 2),
            'iv': round(iv * 100, 1),
            'open_interest': oi,
            'option_volume': opt_vol,
            'score': score,
            'price': round(price, 2),
            'vwap': round(vwap, 2),
            'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4),
            'volume_ratio': round(vol_ratio, 2),
            'atr': round(atr, 2),
            'stop_premium': sl_premium,
            'target_1_premium': tp_1,
            'target_2_premium': tp_2,
            'signals': signals,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        print(f"Options scan error for {symbol}: {e}")
        return None


@app.route('/api/bot/intraday-stocks')
def bot_intraday_stocks():
    """Intraday stock scanner for AI Bot page"""
    try:
        cache_key = 'intraday-stocks'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})

        # Return cached data if fresh
        if cache_entry['data'] is not None and cache_entry['timestamp']:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:  # 5 min cache for intraday
                return jsonify({
                    'success': True,
                    'data': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })

        if cache_entry.get('running'):
            return jsonify({
                'success': True,
                'data': cache_entry.get('data') or [],
                'running': True,
                'message': 'Intraday stock scan in progress...'
            })

        # Run scan in background
        def run_scan():
            try:
                scanner_cache[cache_key]['running'] = True
                previous_data = scanner_cache[cache_key].get('data') or []
                results = run_intraday_scan_batched(
                    INTRADAY_STOCK_UNIVERSE,
                    scan_intraday_stock,
                    max_workers=5,
                    batch_size=6,
                    batch_delay=0.6,
                    fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
                )

                if not results and previous_data:
                    results = previous_data

                results.sort(key=lambda x: x['score'], reverse=True)

                scanner_cache[cache_key]['data'] = results
                scanner_cache[cache_key]['timestamp'] = datetime.now()
                print(f"✅ Intraday stock scan complete: {len(results)} setups found")
            except Exception as e:
                print(f"❌ Intraday stock scan error: {e}")
                scanner_cache[cache_key]['data'] = []
                scanner_cache[cache_key]['timestamp'] = datetime.now()
            finally:
                scanner_cache[cache_key]['running'] = False

        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        return jsonify({
            'success': True,
            'data': cache_entry.get('data') or [],
            'running': True,
            'message': 'Intraday stock scan started...'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/bot/intraday-options')
def bot_intraday_options():
    """Intraday options scanner for AI Bot page"""
    try:
        cache_key = 'intraday-options'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})

        # Return cached data if fresh
        if cache_entry['data'] is not None and cache_entry['timestamp']:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:
                return jsonify({
                    'success': True,
                    'data': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })

        if cache_entry.get('running'):
            return jsonify({
                'success': True,
                'data': cache_entry.get('data') or [],
                'running': True,
                'message': 'Intraday options scan in progress...'
            })

        # Run scan in background
        def run_scan():
            try:
                scanner_cache[cache_key]['running'] = True
                previous_data = scanner_cache[cache_key].get('data') or []
                results = run_intraday_scan_batched(
                    INTRADAY_OPTIONS_UNIVERSE,
                    scan_intraday_option,
                    max_workers=4,
                    batch_size=5,
                    batch_delay=0.7,
                    fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
                )

                if not results and previous_data:
                    results = previous_data

                results.sort(key=lambda x: x['score'], reverse=True)

                scanner_cache[cache_key]['data'] = results
                scanner_cache[cache_key]['timestamp'] = datetime.now()
                print(f"✅ Intraday options scan complete: {len(results)} setups found")
            except Exception as e:
                print(f"❌ Intraday options scan error: {e}")
                scanner_cache[cache_key]['data'] = []
                scanner_cache[cache_key]['timestamp'] = datetime.now()
            finally:
                scanner_cache[cache_key]['running'] = False

        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        return jsonify({
            'success': True,
            'data': cache_entry.get('data') or [],
            'running': True,
            'message': 'Intraday options scan started...'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# CACHE MANAGEMENT API
# ============================================================================

@app.route('/api/cache/stats')
def cache_stats():
    """Get cache statistics to monitor API usage reduction"""
    with _price_cache_lock:
        price_entries = len(_price_cache)
    with _history_cache_lock:
        history_entries = len(_history_cache)
    with _chain_cache_lock:
        chain_entries = len(_chain_cache)
    with _options_dates_lock:
        dates_entries = len(_options_dates_cache)
    with _ticker_info_lock:
        info_entries = len(_ticker_info_cache)
    
    return jsonify({
        'success': True,
        'caches': {
            'price_cache': {'entries': price_entries, 'ttl_seconds': _price_cache_ttl},
            'history_cache': {'entries': history_entries, 'ttl_seconds': _history_cache_ttl},
            'option_chain_cache': {'entries': chain_entries, 'ttl_seconds': _chain_cache_ttl},
            'option_dates_cache': {'entries': dates_entries, 'ttl_seconds': _options_dates_ttl},
            'ticker_info_cache': {'entries': info_entries, 'ttl_seconds': _ticker_info_ttl}
        },
        'total_cached_items': price_entries + history_entries + chain_entries + dates_entries + info_entries
    })

@app.route('/api/cache/clear', methods=['POST'])
def cache_clear():
    """Clear all caches for forced refresh"""
    clear_all_caches()
    return jsonify({'success': True, 'message': 'All caches cleared'})


# ============================================================================
# BACKGROUND BOT ENGINE
# ============================================================================
# This is the PRIMARY trade execution engine. It runs the full auto_cycle
# (exit monitoring + scanning + auto-trade) every 10s server-side, completely
# independent of the browser.  The frontend is just a UI — closing or hiding
# the tab has ZERO effect on trade execution.

_bg_monitor_running = False
_bg_monitor_thread = None
_BG_INTERVAL = 10  # seconds — matches the frontend auto-cycle rate

def _background_position_monitor():
    """Server-side bot engine that runs the full auto_cycle independently of the browser.
    
    This is the primary mechanism for:
    - Checking positions for stop-loss / target / EOD exits
    - Scanning for new signals
    - Auto-executing trades when auto_trade is enabled
    
    The browser tab is optional — it just displays what the server is already doing.
    """
    global _bg_monitor_running
    _bg_monitor_running = True
    print(f"🔄 Background bot engine started ({_BG_INTERVAL}s interval) — trades execute even with no browser open")
    
    while _bg_monitor_running:
        try:
            time.sleep(_BG_INTERVAL)
            
            # Only run if bot is active (quick in-memory check, no disk read)
            if not bot_state.get('running', False):
                continue
            
            # Check if it's a weekday and roughly within market-adjacent hours (4 AM - 8 PM ET)
            try:
                import pytz
                et_tz = pytz.timezone('US/Eastern')
                now_et = datetime.now(et_tz)
                if now_et.weekday() >= 5:  # Weekend
                    continue
                if now_et.hour < 4 or now_et.hour >= 20:  # Outside extended hours
                    continue
            except Exception:
                pass  # If pytz fails, still run the check
            
            # Call the auto_cycle endpoint internally via test_client
            with app.test_client() as client:
                resp = client.post('/api/bot/auto_cycle',
                                   content_type='application/json',
                                   data='{}')
                if resp.status_code == 200:
                    data = resp.get_json()
                    exits = data.get('exits_triggered', [])
                    trades = data.get('trades_executed', [])
                    if exits or trades:
                        print(f"🔄 BG Engine: {len(exits)} exits, {len(trades)} trades executed")
                elif resp.status_code != 200:
                    # Don't spam logs for expected "bot not running" responses
                    data = resp.get_json() or {}
                    if 'not running' not in data.get('message', ''):
                        print(f"⚠️ BG Engine: auto_cycle returned {resp.status_code}")
        except Exception as e:
            print(f"⚠️ BG Engine error: {e}")

def start_background_monitor():
    """Start the background position monitor thread if not already running."""
    global _bg_monitor_thread, _bg_monitor_running
    if _bg_monitor_thread and _bg_monitor_thread.is_alive():
        return  # Already running
    _bg_monitor_running = True
    _bg_monitor_thread = threading.Thread(target=_background_position_monitor, daemon=True)
    _bg_monitor_thread.start()

def stop_background_monitor():
    """Stop the background monitor gracefully."""
    global _bg_monitor_running
    _bg_monitor_running = False


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == '__main__':
    import webbrowser
    import atexit
    
    print("\n" + "="*100)
    print("🚀 US MARKET TRADING DASHBOARD - Starting Web Server")
    print("="*100)
    print("\n📊 Dashboard Features:")
    print("   ✅ Real-time market indices & sector performance")
    print("   ✅ Top gainers/losers scanner")
    print("   ✅ Integrated trading system scanners")
    print("   ✅ Live position monitoring")
    print("   ✅ WebSocket real-time updates")
    print("   ✅ Options analysis with next-day predictions")
    print("   ✅ Custom stock analysis with buy/sell signals")
    PORT = int(os.environ.get('PORT', 5000))
    
    # Kill any stale process on the port before starting
    import subprocess as _sp
    import signal
    try:
        result = _sp.run(['lsof', '-ti', f':{PORT}'], capture_output=True, text=True)
        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                pid = pid.strip()
                if pid and pid != str(os.getpid()):
                    print(f"⚠️  Killing stale process {pid} on port {PORT}")
                    os.kill(int(pid), signal.SIGKILL)
            import time as _time
            _time.sleep(1)
    except Exception:
        pass
    
    # Clear stale yfinance cookie/crumb cache to prevent 429 rate-limit loops
    try:
        import shutil as _shutil
        _yf_cache = YF_CACHE_DIR
        if os.path.exists(_yf_cache):
            _shutil.rmtree(_yf_cache)
            print("🔄 Cleared stale yfinance cookie cache")
        os.makedirs(_yf_cache, exist_ok=True)
    except Exception:
        pass

    # Verify yfinance can fetch data — if not, retry after clearing cache
    try:
        _test_ticker = yf.Ticker('SPY')
        _test_hist = _test_ticker.history(period='5d', interval='1d')
        if len(_test_hist) > 0:
            print(f"✅ yfinance OK: SPY ${_test_hist['Close'].iloc[-1]:.2f}")
        else:
            raise Exception("Empty data")
    except Exception as _e:
        _log_fetch_event('startup-yf-check', 'SPY', f"⚠️ Initial yfinance check failed: {_e}", cooldown=60)
        try:
            import shutil as _shutil
            _yf_cache = YF_CACHE_DIR
            if os.path.exists(_yf_cache):
                _shutil.rmtree(_yf_cache)
            os.makedirs(_yf_cache, exist_ok=True)
            _test_hist = yf.Ticker('SPY').history(period='5d', interval='1d')
            if len(_test_hist) > 0:
                print(f"✅ yfinance recovered: SPY ${_test_hist['Close'].iloc[-1]:.2f}")
            else:
                print("🔴 yfinance still returning empty data — Yahoo may be rate-limiting. Data will load from cache when available.")
        except Exception as _e2:
            print(f"🔴 yfinance still failing ({_e2}) — will rely on cache fallbacks")

    print(f"\n🌐 Open your browser and go to: http://localhost:{PORT}")
    print("="*100 + "\n")

    # Start background position monitor (runs exit checks even when no browser tab is open)
    start_background_monitor()
    atexit.register(stop_background_monitor)

    socketio.run(app, host='0.0.0.0', port=PORT, debug=False, allow_unsafe_werkzeug=True)
