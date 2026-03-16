"""
Crypto Routes - Cryptocurrency data, movers, search, quote.
"""
from flask import Blueprint, jsonify, request
import json
import os
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
import numpy as np

from services.utils import clean_nan_values
from services.market_data import (
    cached_get_ticker_info, _log_fetch_event, _is_rate_limited,
    _ticker_info_cache, _ticker_info_lock, _ticker_info_ttl,
    _fetch_all_quotes_batch, quote_cache
)

crypto_bp = Blueprint("crypto", __name__)

# ---------------------------------------------------------------------------
# Crypto disk-cache (stale-while-revalidate)
# ---------------------------------------------------------------------------
_CRYPTO_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.dashboard_cache')
_CRYPTO_CACHE_FILE = os.path.join(_CRYPTO_CACHE_DIR, 'crypto_cache.json')
_crypto_mem_cache = {}        # symbol → quote dict
_crypto_mem_ts = 0.0          # epoch when mem cache was populated
_CRYPTO_MEM_TTL = 300         # 5 min fresh
_CRYPTO_DISK_ACCEPT = 3600    # 1 hr stale-but-acceptable from disk
_crypto_refreshing = False


def _load_crypto_disk_cache():
    """Load crypto cache from disk on startup."""
    global _crypto_mem_cache, _crypto_mem_ts
    try:
        if os.path.exists(_CRYPTO_CACHE_FILE):
            with open(_CRYPTO_CACHE_FILE, 'r') as f:
                payload = json.load(f)
            saved_ts = payload.get('ts', 0)
            if time.time() - saved_ts < _CRYPTO_DISK_ACCEPT:
                _crypto_mem_cache = payload.get('data', {})
                _crypto_mem_ts = saved_ts
                print(f"📀 Crypto cache loaded from disk: {len(_crypto_mem_cache)} symbols (age {int(time.time()-saved_ts)}s)")
    except Exception as e:
        print(f"📀 Crypto disk cache load error: {e}")


def _save_crypto_disk_cache(data_dict):
    """Persist crypto quotes to disk."""
    try:
        os.makedirs(_CRYPTO_CACHE_DIR, exist_ok=True)
        payload = {'ts': time.time(), 'data': data_dict}
        tmp = _CRYPTO_CACHE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(payload, f)
        os.replace(tmp, _CRYPTO_CACHE_FILE)
    except Exception as e:
        print(f"📀 Crypto disk cache save error: {e}")


# Load on import
_load_crypto_disk_cache()

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


def _direct_crypto_download(symbols):
    """Fallback: fetch crypto quotes one at a time via Ticker.history() (v8 API).

    yf.download() uses a different Yahoo endpoint that gets rate-limited much
    more aggressively.  Ticker.history() uses the v8 price API which usually
    keeps working even when the bulk endpoint is blocked.
    """
    results = {}
    fetch_time = datetime.now()
    BATCH = 10

    for i in range(0, len(symbols), BATCH):
        batch = symbols[i:i+BATCH]
        if i > 0:
            time.sleep(0.5)

        for sym in batch:
            try:
                t = yf.Ticker(sym)
                hist = t.history(period='5d', interval='1d')
                if hist is None or hist.empty or len(hist) < 2:
                    continue
                cv = hist['Close'].dropna()
                if len(cv) < 2:
                    continue
                cur = float(cv.iloc[-1])
                prev = float(cv.iloc[-2])
                if cur <= 0:
                    continue
                change = cur - prev
                vol = int(hist['Volume'].iloc[-1]) if 'Volume' in hist.columns else 0
                hi = round(float(hist['High'].iloc[-1]), 2) if 'High' in hist.columns else 0
                lo = round(float(hist['Low'].iloc[-1]), 2) if 'Low' in hist.columns else 0

                q = {
                    'symbol': sym, 'price': round(cur, 2),
                    'change': round(change, 2),
                    'changePct': round((change / prev * 100) if prev else 0, 2),
                    'volume': vol, 'high': hi, 'low': lo,
                }
                results[sym] = q
                quote_cache[sym] = (q, fetch_time)
            except Exception:
                continue

    print(f"🪙 Crypto v8 fetch: got {len(results)}/{len(symbols)} symbols")
    return results


def _refresh_crypto_background(symbols):
    """Background thread: fetch fresh crypto data and update caches."""
    global _crypto_mem_cache, _crypto_mem_ts, _crypto_refreshing
    try:
        # Try shared batch fetcher first
        quotes_map = _fetch_all_quotes_batch(symbols)

        # Fallback: direct batched download
        if not quotes_map:
            quotes_map = _direct_crypto_download(symbols)

        if quotes_map:
            _crypto_mem_cache = quotes_map
            _crypto_mem_ts = time.time()
            _save_crypto_disk_cache(quotes_map)
            print(f"🪙 Crypto background refresh: {len(quotes_map)} symbols cached")
    except Exception as e:
        print(f"🪙 Crypto background refresh error: {e}")
    finally:
        _crypto_refreshing = False


def _get_crypto_data_batch(symbols):
    """Get crypto quote rows with stale-while-revalidate caching.

    1) If mem cache is fresh (<5 min) → return immediately.
    2) If mem cache is stale but within disk-accept window → return stale,
       kick off background refresh.
    3) If nothing cached → blocking fetch (small batches).
    """
    global _crypto_mem_cache, _crypto_mem_ts, _crypto_refreshing

    now = time.time()
    age = now - _crypto_mem_ts if _crypto_mem_ts else float('inf')

    # --- Serve from mem cache if fresh ---
    if _crypto_mem_cache and age < _CRYPTO_MEM_TTL:
        return _build_crypto_list(symbols, _crypto_mem_cache)

    # --- Stale-while-revalidate: return stale, refresh in background ---
    if _crypto_mem_cache and age < _CRYPTO_DISK_ACCEPT:
        if not _crypto_refreshing:
            _crypto_refreshing = True
            t = threading.Thread(target=_refresh_crypto_background, args=(symbols,), daemon=True)
            t.start()
        return _build_crypto_list(symbols, _crypto_mem_cache)

    # --- Cold start: blocking fetch ---
    quotes_map = _fetch_all_quotes_batch(symbols)

    if not quotes_map:
        quotes_map = _direct_crypto_download(symbols)

    if quotes_map:
        _crypto_mem_cache = quotes_map
        _crypto_mem_ts = time.time()
        _save_crypto_disk_cache(quotes_map)
    elif _crypto_mem_cache:
        # All fetches failed but we have very old mem cache — still serve it
        print("🪙 Crypto: all fetches failed, serving very stale cache")
        quotes_map = _crypto_mem_cache

    return _build_crypto_list(symbols, quotes_map)


def _build_crypto_list(symbols, quotes_map):
    """Convert quotes_map into the list-of-dicts format the endpoints expect."""
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

@crypto_bp.route('/api/crypto/all')
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

@crypto_bp.route('/api/crypto/movers/<direction>')
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

@crypto_bp.route('/api/crypto/search')
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

@crypto_bp.route('/api/crypto/quote/<symbol>')
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

