"""
Symbol Services - Symbol validation, resolution, and suggestion.
"""
import re
import threading
import yfinance as yf

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

