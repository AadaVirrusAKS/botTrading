#!/usr/bin/env python3
"""
US Market Trading Dashboard - Web Application Entry Point
Modular Flask + SocketIO application.

Provides real-time market data, scanners, sector analysis, and trade monitoring
via a modern web interface.
"""

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import os
import time
import threading
from datetime import datetime
import yfinance as yf

# =============================
# FLASK APP INITIALIZATION
# =============================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'trading-dashboard-secret-2026'
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max request size
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# =============================
# GLOBAL ERROR HANDLERS
# =============================
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'error': 'Resource not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'success': False, 'error': 'Internal server error'}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    if hasattr(e, 'code'):
        return jsonify({'success': False, 'error': str(e)}), e.code
    print(f"Unhandled exception: {e}")
    return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500

# =============================
# PREVENT BROWSER CACHING ON API RESPONSES
# =============================
@app.after_request
def add_no_cache_headers(response):
    """Add Cache-Control headers to all /api/ responses to prevent stale browser cache."""
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# =============================
# REGISTER ALL BLUEPRINTS
# =============================
from routes import register_blueprints
register_blueprints(app)

# =============================
# WEBSOCKET HANDLERS
# =============================
from services.market_data import active_subscriptions, _fetch_all_quotes_batch

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
    if client_sid in active_subscriptions:
        active_subscriptions[client_sid]['active'] = False
        del active_subscriptions[client_sid]

@socketio.on('subscribe_quotes')
def handle_subscribe_quotes(data):
    """Subscribe to real-time quotes for symbols - with rate limiting protection"""
    symbols = data.get('symbols', [])
    client_sid = request.sid

    if not symbols:
        print(f"📊 Client {client_sid} tried to subscribe with no symbols")
        return

    if client_sid in active_subscriptions:
        active_subscriptions[client_sid]['active'] = False

    print(f"📊 Client {client_sid} subscribed to: {symbols}")
    active_subscriptions[client_sid] = {'active': True}

    def send_quote_updates():
        update_interval = 30
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
                    break

            time.sleep(update_interval)

        if client_sid in active_subscriptions:
            del active_subscriptions[client_sid]

    thread = threading.Thread(target=send_quote_updates, daemon=True)
    thread.start()

# =============================
# MAIN EXECUTION
# =============================
if __name__ == '__main__':
    import webbrowser
    import atexit

    from services.market_data import _log_fetch_event, YF_CACHE_DIR
    from routes.cache_admin import start_background_monitor, stop_background_monitor

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

    # Verify yfinance can fetch data (non-blocking — don't let startup failures
    # poison the rate-limit state for the rest of the session)
    try:
        _test_ticker = yf.Ticker('SPY')
        _test_hist = _test_ticker.history(period='5d', interval='1d')
        if len(_test_hist) > 0:
            print(f"✅ yfinance OK: SPY ${_test_hist['Close'].iloc[-1]:.2f}")
        else:
            raise Exception("Empty data")
    except Exception as _e:
        print(f"⚠️ Initial yfinance check failed: {_e}")
        # Clear cookies and try ONE more time
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
                print("🔴 yfinance returning empty data — Yahoo may be rate-limiting. Will use API fallbacks.")
        except Exception as _e2:
            print(f"🔴 yfinance still failing ({_e2}) — will use API fallbacks")
    
    # CRITICAL: Always clear any rate-limit blocks that may have been set during
    # startup checks. The startup check is just a health probe — it should never
    # block the entire session's price fetching.
    try:
        from services.market_data import clear_rate_limit_blocks
        clear_rate_limit_blocks()
        print("✅ Rate-limit blocks cleared (clean start)")
    except Exception:
        pass

    print(f"\n🌐 Open your browser and go to: http://localhost:{PORT}")
    print("="*100 + "\n")

    # Start background position monitor
    start_background_monitor(app)
    atexit.register(stop_background_monitor)

    socketio.run(app, host='0.0.0.0', port=PORT, debug=False, allow_unsafe_werkzeug=True)
