"""
Alpaca Real-Time Market Data Service.

Provides near-zero-latency stock prices via Alpaca's Data API:
  1. REST Snapshots — on-demand batch price lookups (replaces yfinance for live prices)
  2. WebSocket Streaming — continuous price feed for monitored positions

Requires alpaca-py SDK (already in requirements.txt) and valid Alpaca credentials.
Free tier gets IEX real-time data; paid tier gets SIP (full market).
"""
import os
import threading
import time
import ssl
import logging
from datetime import datetime
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# Fix macOS Python SSL: point OpenSSL at certifi's CA bundle
# so Alpaca WebSocket/REST connections can verify certificates.
try:
    import certifi
    os.environ.setdefault('SSL_CERT_FILE', certifi.where())
except ImportError:
    pass

# =============================
# LAZY CLIENT MANAGEMENT
# =============================
_data_client = None
_data_client_lock = threading.Lock()
_stream = None
_stream_lock = threading.Lock()
_stream_thread = None

# In-memory latest-price store populated by WebSocket stream
_live_prices: Dict[str, dict] = {}  # {symbol: {'price': float, 'ts': datetime, 'bid': float, 'ask': float}}
_live_prices_lock = threading.Lock()

# Subscribers: external callbacks notified on every price tick
_subscribers: Dict[str, Callable] = {}  # {id: callback(symbol, price_dict)}
_subscribers_lock = threading.Lock()


def _get_credentials():
    """Borrow credentials from the existing alpaca_service module."""
    try:
        from services.alpaca_service import get_credentials, is_configured
        if not is_configured():
            return None, None
        return get_credentials()
    except Exception:
        return None, None


def is_available() -> bool:
    """Check if Alpaca real-time data is usable (SDK installed + configured)."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient  # noqa: F401
        api_key, secret_key = _get_credentials()
        return bool(api_key and secret_key)
    except ImportError:
        return False


def _get_data_client():
    """Lazy singleton for Alpaca StockHistoricalDataClient (REST)."""
    global _data_client
    with _data_client_lock:
        if _data_client is not None:
            return _data_client
        api_key, secret_key = _get_credentials()
        if not api_key or not secret_key:
            return None
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            _data_client = StockHistoricalDataClient(api_key, secret_key)
            return _data_client
        except Exception as e:
            logger.warning(f"[AlpacaRT] Failed to create data client: {e}")
            return None


def reset_client():
    """Reset the cached data client (e.g. after credential change)."""
    global _data_client
    with _data_client_lock:
        _data_client = None


# =============================
# REST: SNAPSHOT PRICES
# =============================

def get_snapshot_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch latest snapshot prices for multiple symbols via Alpaca REST API.

    Returns {symbol: price} for symbols that succeeded.
    Uses Alpaca's /v2/stocks/snapshots endpoint — single HTTP call for all symbols.
    """
    if not symbols:
        return {}
    client = _get_data_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import StockSnapshotRequest
        # Alpaca accepts up to ~200 symbols per snapshot request
        # Filter out index symbols (^GSPC, ^IXIC, etc.) — Alpaca only supports tradeable stocks/ETFs
        unique = list(set(
            s.strip().upper() for s in symbols
            if s and not s.strip().startswith('^')
        ))
        if not unique:
            return {}
        results = {}
        BATCH = 200
        for i in range(0, len(unique), BATCH):
            chunk = unique[i:i + BATCH]
            req = StockSnapshotRequest(symbol_or_symbols=chunk)
            snapshots = client.get_stock_snapshot(req)
            for sym, snap in snapshots.items():
                try:
                    price = float(snap.latest_trade.price) if snap.latest_trade else None
                    if price and price > 0:
                        results[sym] = price
                        # Also update in-memory live store
                        with _live_prices_lock:
                            _live_prices[sym] = {
                                'price': price,
                                'ts': datetime.now(),
                                'bid': float(snap.latest_quote.bid_price) if snap.latest_quote else None,
                                'ask': float(snap.latest_quote.ask_price) if snap.latest_quote else None,
                                'source': 'alpaca_snapshot',
                            }
                except Exception:
                    pass
        return results
    except Exception as e:
        logger.warning(f"[AlpacaRT] Snapshot failed: {e}")
        return {}


def get_snapshot_price(symbol: str) -> Optional[float]:
    """Get a single symbol's latest price via snapshot."""
    prices = get_snapshot_prices([symbol])
    return prices.get(symbol.upper())


def get_snapshot_quotes(symbols: List[str]) -> Dict[str, dict]:
    """Fetch full snapshot quotes including change% for multiple symbols.

    Returns {symbol: {'price', 'change', 'changePct', 'volume', 'high', 'low', 'open', ...}}.
    Uses daily_bar + previous_daily_bar from Alpaca for accurate change calculations.
    """
    if not symbols:
        return {}
    client = _get_data_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import StockSnapshotRequest
        unique = list(set(
            s.strip().upper() for s in symbols
            if s and not s.strip().startswith('^')
        ))
        if not unique:
            return {}
        results = {}
        BATCH = 200
        for i in range(0, len(unique), BATCH):
            chunk = unique[i:i + BATCH]
            req = StockSnapshotRequest(symbol_or_symbols=chunk)
            snapshots = client.get_stock_snapshot(req)
            for sym, snap in snapshots.items():
                try:
                    price = float(snap.latest_trade.price) if snap.latest_trade else None
                    if not price or price <= 0:
                        continue

                    # Use previous_daily_bar.close as the true previous close
                    prev_close = None
                    if snap.previous_daily_bar:
                        prev_close = float(snap.previous_daily_bar.close)

                    change = round(price - prev_close, 2) if prev_close else 0
                    change_pct = round((change / prev_close) * 100, 2) if prev_close and prev_close > 0 else 0

                    # Today's bar for OHLV
                    daily = snap.daily_bar
                    results[sym] = {
                        'symbol': sym,
                        'price': round(price, 2),
                        'change': change,
                        'changePct': change_pct,
                        'volume': int(daily.volume) if daily and daily.volume else 0,
                        'high': round(float(daily.high), 2) if daily and daily.high else 0,
                        'low': round(float(daily.low), 2) if daily and daily.low else 0,
                        'open': round(float(daily.open), 2) if daily and daily.open else 0,
                        'prev_close': round(prev_close, 2) if prev_close else 0,
                        'bid': float(snap.latest_quote.bid_price) if snap.latest_quote else None,
                        'ask': float(snap.latest_quote.ask_price) if snap.latest_quote else None,
                        'source': 'alpaca_snapshot',
                    }
                except Exception:
                    pass
        return results
    except Exception as e:
        logger.warning(f"[AlpacaRT] Snapshot quotes failed: {e}")
        return {}


def get_latest_quotes(symbols: List[str]) -> Dict[str, dict]:
    """Fetch latest bid/ask quotes for symbols.

    Returns {symbol: {'bid': float, 'ask': float, 'mid': float, 'ts': datetime}}.
    """
    if not symbols:
        return {}
    client = _get_data_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import StockLatestQuoteRequest
        # Filter out index symbols (^GSPC etc.)
        unique = list(set(
            s.strip().upper() for s in symbols
            if s and not s.strip().startswith('^')
        ))
        if not unique:
            return {}
        results = {}
        req = StockLatestQuoteRequest(symbol_or_symbols=unique)
        quotes = client.get_stock_latest_quote(req)
        for sym, q in quotes.items():
            try:
                bid = float(q.bid_price) if q.bid_price else 0
                ask = float(q.ask_price) if q.ask_price else 0
                mid = (bid + ask) / 2 if bid and ask else bid or ask
                results[sym] = {
                    'bid': bid,
                    'ask': ask,
                    'mid': round(mid, 4),
                    'ts': datetime.now(),
                }
            except Exception:
                pass
        return results
    except Exception as e:
        logger.warning(f"[AlpacaRT] Latest quotes failed: {e}")
        return {}


# =============================
# REST: OPTION SNAPSHOTS
# =============================

_option_client = None
_option_client_lock = threading.Lock()


def _get_option_client():
    """Lazy singleton for Alpaca OptionHistoricalDataClient (REST)."""
    global _option_client
    with _option_client_lock:
        if _option_client is not None:
            return _option_client
        api_key, secret_key = _get_credentials()
        if not api_key or not secret_key:
            return None
        try:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            _option_client = OptionHistoricalDataClient(api_key, secret_key)
            return _option_client
        except Exception as e:
            logger.warning(f"[AlpacaRT] Failed to create option data client: {e}")
            return None


def get_option_snapshot_quotes(option_symbols: List[str]) -> Dict[str, dict]:
    """Fetch real-time option bid/ask/last via Alpaca Option Snapshot API.

    Args:
        option_symbols: List of OCC-format option symbols
                        e.g. ['TSLA260330P00390000', 'AAPL260402C00230000']

    Returns:
        {symbol: {'bid': float, 'ask': float, 'mid': float, 'last': float,
                  'underlying': str}} for symbols that succeeded.
    """
    if not option_symbols:
        return {}
    client = _get_option_client()
    if client is None:
        return {}

    try:
        from alpaca.data.requests import OptionSnapshotRequest
        unique = list(set(s.strip().upper() for s in option_symbols if s))
        if not unique:
            return {}
        results = {}
        BATCH = 100
        for i in range(0, len(unique), BATCH):
            chunk = unique[i:i + BATCH]
            req = OptionSnapshotRequest(symbol_or_symbols=chunk)
            snapshots = client.get_option_snapshot(req)
            for sym, snap in snapshots.items():
                try:
                    bid = float(snap.latest_quote.bid_price) if snap.latest_quote else 0
                    ask = float(snap.latest_quote.ask_price) if snap.latest_quote else 0
                    last = float(snap.latest_trade.price) if snap.latest_trade else 0
                    mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0

                    results[sym] = {
                        'bid': bid,
                        'ask': ask,
                        'mid': mid,
                        'last': last,
                    }
                except Exception:
                    pass
        return results
    except Exception as e:
        logger.warning(f"[AlpacaRT] Option snapshot failed: {e}")
        return {}


# =============================
# WEBSOCKET: LIVE STREAMING
# =============================

# Module-level handler references — set by _ensure_stream_running, used by subscribe_symbols
_on_trade_handler = None
_on_quote_handler = None


def _ensure_stream_running():
    """Start the Alpaca WebSocket stream thread if not already running."""
    global _stream, _stream_thread, _on_trade_handler, _on_quote_handler
    with _stream_lock:
        if _stream_thread is not None and _stream_thread.is_alive():
            return True
        api_key, secret_key = _get_credentials()
        if not api_key or not secret_key:
            return False
        try:
            from alpaca.data.live import StockDataStream
            _stream = StockDataStream(api_key, secret_key)

            async def _on_trade(trade):
                sym = trade.symbol
                price = float(trade.price)
                now = datetime.now()
                with _live_prices_lock:
                    _live_prices[sym] = {
                        'price': price,
                        'ts': now,
                        'bid': _live_prices.get(sym, {}).get('bid'),
                        'ask': _live_prices.get(sym, {}).get('ask'),
                        'source': 'alpaca_stream',
                    }
                # Notify subscribers
                with _subscribers_lock:
                    for cb in list(_subscribers.values()):
                        try:
                            cb(sym, {'price': price, 'ts': now})
                        except Exception:
                            pass

            async def _on_quote(quote):
                sym = quote.symbol
                bid = float(quote.bid_price) if quote.bid_price else None
                ask = float(quote.ask_price) if quote.ask_price else None
                with _live_prices_lock:
                    entry = _live_prices.get(sym, {})
                    entry['bid'] = bid
                    entry['ask'] = ask
                    if bid and ask:
                        entry['mid'] = round((bid + ask) / 2, 4)
                    _live_prices[sym] = entry

            _stream.subscribe_trades(_on_trade)
            _stream.subscribe_quotes(_on_quote)

            # Store handlers at module level for subscribe_symbols
            _on_trade_handler = _on_trade
            _on_quote_handler = _on_quote

            def _run_stream():
                try:
                    _stream.run()
                except ssl.SSLCertVerificationError as e:
                    logger.error(f"[AlpacaRT] SSL certificate error — WebSocket disabled: {e}")
                    logger.error("[AlpacaRT] Fix: run '/Applications/Python 3.12/Install Certificates.command' or 'pip install certifi'")
                except Exception as e:
                    logger.error(f"[AlpacaRT] Stream error: {e}")

            _stream_thread = threading.Thread(target=_run_stream, daemon=True, name="alpaca-stream")
            _stream_thread.start()
            logger.info("[AlpacaRT] WebSocket stream started")
            return True
        except Exception as e:
            logger.error(f"[AlpacaRT] Failed to start stream: {e}")
            return False


def subscribe_symbols(symbols: List[str]):
    """Subscribe to live trade/quote updates for additional symbols."""
    if not _stream:
        if not _ensure_stream_running():
            return False
    try:
        # Filter out index symbols (^GSPC etc.) — Alpaca only supports tradeable stocks/ETFs
        clean = [s.upper() for s in symbols if s and not s.strip().startswith('^')]
        if not clean:
            return True
        _stream.subscribe_trades(_on_trade_handler, *clean)
        _stream.subscribe_quotes(_on_quote_handler, *clean)
        return True
    except Exception as e:
        logger.warning(f"[AlpacaRT] Subscribe failed: {e}")
        return False


def unsubscribe_symbols(symbols: List[str]):
    """Unsubscribe from live updates for symbols."""
    if not _stream:
        return
    try:
        clean = [s.upper() for s in symbols if s and not s.strip().startswith('^')]
        if not clean:
            return
        _stream.unsubscribe_trades(*clean)
        _stream.unsubscribe_quotes(*clean)
    except Exception:
        pass


def get_live_price(symbol: str) -> Optional[float]:
    """Get the latest streamed price for a symbol (from in-memory store).

    Returns None if no stream data available for that symbol.
    Prices older than 60s are considered stale.
    """
    with _live_prices_lock:
        entry = _live_prices.get(symbol.upper())
        if not entry or 'price' not in entry:
            return None
        age = (datetime.now() - entry['ts']).total_seconds()
        if age > 60:
            return None  # stale
        return entry['price']


def get_live_prices(symbols: List[str]) -> Dict[str, float]:
    """Get latest streamed prices for multiple symbols.

    Only returns symbols with fresh data (< 60s old).
    """
    results = {}
    now = datetime.now()
    with _live_prices_lock:
        for sym in symbols:
            s = sym.upper()
            entry = _live_prices.get(s)
            if entry and 'price' in entry:
                age = (now - entry['ts']).total_seconds()
                if age <= 60:
                    results[s] = entry['price']
    return results


def get_full_quote(symbol: str) -> Optional[dict]:
    """Get full live quote data: price, bid, ask, timestamp, source."""
    with _live_prices_lock:
        entry = _live_prices.get(symbol.upper())
        if not entry:
            return None
        return dict(entry)


# =============================
# SUBSCRIBER MANAGEMENT
# =============================

def add_subscriber(subscriber_id: str, callback: Callable):
    """Register a callback that fires on every trade tick.

    callback(symbol: str, data: dict) where data has 'price' and 'ts'.
    """
    with _subscribers_lock:
        _subscribers[subscriber_id] = callback


def remove_subscriber(subscriber_id: str):
    """Unregister a subscriber."""
    with _subscribers_lock:
        _subscribers.pop(subscriber_id, None)


# =============================
# CONVENIENCE: WARM-UP
# =============================

def warmup(symbols: List[str]):
    """Fetch initial snapshots and start streaming for a list of symbols.

    Call this at startup or when the watchlist changes.
    """
    if not is_available():
        return
    # REST snapshot for immediate data
    get_snapshot_prices(symbols)
    # Start WebSocket stream for continuous updates
    _ensure_stream_running()
    subscribe_symbols(symbols)
