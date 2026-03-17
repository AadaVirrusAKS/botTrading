"""
Cache Admin Routes - Cache statistics, clearing, and background monitoring.
"""
from flask import Blueprint, jsonify, request
import os
import json
import time
import threading
from datetime import datetime
import yfinance as yf

from services.market_data import (
    _price_cache, _price_cache_lock, _price_cache_ttl,
    _history_cache, _history_cache_lock, _history_cache_ttl,
    _chain_cache, _chain_cache_lock, _chain_cache_ttl,
    _options_dates_cache, _options_dates_lock, _options_dates_ttl,
    _ticker_info_cache, _ticker_info_lock, _ticker_info_ttl,
    clear_all_caches, YF_CACHE_DIR,
    _global_rate_limit_until, _global_rate_limit_lock,
    _global_rate_limit_consecutive
)
from services.bot_engine import (
    bot_state, BOT_STATE_LOCK, update_positions_with_live_prices,
    save_bot_state, load_bot_state, recalculate_balance,
    is_zero_dte_or_expired, get_live_option_premium
)
from services.market_data import _log_fetch_event

cache_bp = Blueprint("cache_admin", __name__)

# ============================================================================
# CACHE MANAGEMENT API
# ============================================================================

@cache_bp.route('/api/cache/stats')
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

@cache_bp.route('/api/cache/clear', methods=['POST'])
def cache_clear():
    """Clear all caches for forced refresh"""
    clear_all_caches()
    return jsonify({'success': True, 'message': 'All caches cleared'})

@cache_bp.route('/api/cache/rate_limit')
def rate_limit_status():
    """Check current rate limit state"""
    import services.market_data as md
    now_ts = time.time()
    with md._global_rate_limit_lock:
        until = md._global_rate_limit_until
        consecutive = md._global_rate_limit_consecutive
    is_limited = now_ts < until
    remaining = max(0, until - now_ts) if is_limited else 0
    return jsonify({
        'success': True,
        'rate_limited': is_limited,
        'remaining_seconds': round(remaining, 1),
        'consecutive_429s': consecutive
    })

@cache_bp.route('/api/cache/rate_limit/reset', methods=['POST'])
def rate_limit_reset():
    """Reset the global rate limit to allow requests again"""
    import services.market_data as md
    with md._global_rate_limit_lock:
        md._global_rate_limit_until = 0.0
        md._global_rate_limit_consecutive = 0
    return jsonify({'success': True, 'message': 'Rate limit reset'})


# ============================================================================
# BACKGROUND BOT ENGINE
# ============================================================================
# This is the PRIMARY trade execution engine. It runs the full auto_cycle
# (exit monitoring + scanning + auto-trade) every 10s server-side, completely
# independent of the browser.  The frontend is just a UI — closing or hiding
# the tab has ZERO effect on trade execution.

_bg_monitor_running = False
_bg_monitor_thread = None
_bg_app = None  # Flask app reference, set by start_background_monitor()
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
    
    # Delay first cycle so the initial page load + scan don't compete
    # with the background engine for yfinance API slots.
    time.sleep(30)
    
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
            with _bg_app.test_client() as client:
                # Get internal auth key from web_app module
                try:
                    from app.web_app import BOT_INTERNAL_KEY
                    headers = {'X-Bot-Internal': BOT_INTERNAL_KEY}
                except ImportError:
                    headers = {}
                resp = client.post('/api/bot/auto_cycle',
                                   headers=headers,
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

def start_background_monitor(app=None):
    """Start the background position monitor thread if not already running.
    
    Args:
        app: Flask app instance (required for test_client calls from background thread).
    """
    global _bg_monitor_thread, _bg_monitor_running, _bg_app
    if _bg_monitor_thread and _bg_monitor_thread.is_alive():
        return  # Already running
    if app is not None:
        _bg_app = app
    if _bg_app is None:
        print('⚠️ BG Engine: No Flask app provided — background monitor not started')
        return
    _bg_monitor_running = True
    _bg_monitor_thread = threading.Thread(target=_background_position_monitor, daemon=True)
    _bg_monitor_thread.start()

def stop_background_monitor():
    """Stop the background monitor gracefully."""
    global _bg_monitor_running
    _bg_monitor_running = False

