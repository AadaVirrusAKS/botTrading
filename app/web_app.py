#!/usr/bin/env python3
"""
US Market Trading Dashboard - Web Application Entry Point
Modular Flask + SocketIO application.

Provides real-time market data, scanners, sector analysis, and trade monitoring
via a modern web interface.
"""

import os
import sys

# Ensure project root is on sys.path so sibling packages (routes, services, config) resolve
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import time
import threading
from datetime import datetime
import yfinance as yf
import socket


def _get_local_ip():
    """Return this machine's LAN IP address (e.g. 192.168.x.x)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


# =============================
# FLASK APP INITIALIZATION
# =============================
app = Flask(__name__,
            template_folder=os.path.join(PROJECT_ROOT, 'templates'),
            static_folder=os.path.join(PROJECT_ROOT, 'static'))
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
# REGISTER ALL BLUEPRINTS (incl. Auth)
# =============================
from routes import register_blueprints
register_blueprints(app)

# =============================
# LOGIN-REQUIRED GUARD (protect all pages except auth routes & static)
# =============================
from flask_login import current_user
import secrets as _secrets

PUBLIC_PREFIXES = ('/login', '/register', '/forgot-password', '/reset-password', '/static/', '/api/health')

# Internal key for background engine requests (generated at startup, not guessable)
# Stored on app.config so the background engine (which may import this module as
# a *different* module name than __main__) always reads the SAME key.
BOT_INTERNAL_KEY = _secrets.token_hex(16)
app.config['BOT_INTERNAL_KEY'] = BOT_INTERNAL_KEY

@app.before_request
def require_login():
    """Redirect unauthenticated users to the login page, and switch to per-user bot state."""
    # Allow internal background engine requests (bot auto-cycle)
    if request.headers.get('X-Bot-Internal') == BOT_INTERNAL_KEY:
        from services.bot_engine import set_active_user, load_bot_state, _active_user_id, _bg_owner_user_id
        owner = _bg_owner_user_id()
        if owner and _active_user_id != owner:
            set_active_user(owner)
            load_bot_state()
        return
    if current_user.is_authenticated:
        # Skip bot state switching for static files
        if not request.path.startswith('/static/'):
            from services.bot_engine import set_active_user, load_bot_state, _active_user_id
            if _active_user_id != current_user.id:
                set_active_user(current_user.id)
                load_bot_state()
        return
    if request.path.startswith(PUBLIC_PREFIXES):
        return
    # Allow Socket.IO handshake
    if request.path.startswith('/socket.io'):
        return
    from flask import redirect, url_for
    return redirect(url_for('auth.login', next=request.path))

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
    """Subscribe to real-time quotes for symbols.
    
    When Alpaca is configured, uses near-zero-latency streaming (2-5s updates).
    Otherwise falls back to yfinance polling (30s updates).
    """
    symbols = data.get('symbols', [])
    client_sid = request.sid

    if not symbols:
        print(f"📊 Client {client_sid} tried to subscribe with no symbols")
        return

    if client_sid in active_subscriptions:
        active_subscriptions[client_sid]['active'] = False

    # Determine if Alpaca real-time is available
    alpaca_available = False
    try:
        from services.alpaca_realtime import is_available, warmup, get_live_prices, subscribe_symbols, add_subscriber, remove_subscriber
        alpaca_available = is_available()
    except ImportError:
        pass

    active_subscriptions[client_sid] = {'active': True}

    if alpaca_available:
        # --- Alpaca path: WebSocket streaming with fast push ---
        upper_symbols = [s.upper() for s in symbols]
        print(f"📊 Client {client_sid} subscribed (Alpaca real-time): {upper_symbols}")
        warmup(upper_symbols)

        def _on_tick(sym, price_data):
            """Called on every Alpaca trade tick for subscribed symbols."""
            if sym not in upper_symbols:
                return
            if not active_subscriptions.get(client_sid, {}).get('active', False):
                return
            try:
                socketio.emit('quote_update', {
                    'timestamp': datetime.now().isoformat(),
                    'quotes': [{'symbol': sym, 'price': round(price_data['price'], 2), 'source': 'alpaca_stream'}]
                }, room=client_sid)
            except Exception:
                pass

        sub_id = f"ws_{client_sid}"
        add_subscriber(sub_id, _on_tick)

        # Also send a periodic full snapshot (every 5s) so the UI stays in sync
        def send_periodic_snapshots():
            while active_subscriptions.get(client_sid, {}).get('active', False):
                live = get_live_prices(upper_symbols)
                quotes = [{'symbol': s, 'price': round(live[s], 2), 'source': 'alpaca_stream'}
                          for s in upper_symbols if s in live]
                if quotes:
                    try:
                        socketio.emit('quote_update', {
                            'timestamp': datetime.now().isoformat(),
                            'quotes': quotes
                        }, room=client_sid)
                    except Exception:
                        break
                time.sleep(5)
            remove_subscriber(sub_id)
            if client_sid in active_subscriptions:
                del active_subscriptions[client_sid]

        thread = threading.Thread(target=send_periodic_snapshots, daemon=True)
        thread.start()
    else:
        # --- yfinance fallback path: polling ---
        print(f"📊 Client {client_sid} subscribed (yfinance polling): {symbols}")

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
                    except Exception:
                        break

                time.sleep(update_interval)

            if client_sid in active_subscriptions:
                del active_subscriptions[client_sid]

        thread = threading.Thread(target=send_quote_updates, daemon=True)
        thread.start()

# =============================
# MAIN EXECUTION
# =============================
def main():
    """Start the trading dashboard web server."""
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

    # --- Alpaca real-time warmup (non-blocking) ---
    try:
        from services.alpaca_realtime import is_available as _alpaca_ready, warmup as _alpaca_warmup
        if _alpaca_ready():
            _core_symbols = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMZN', 'META', 'GOOGL', 'TSLA']
            _alpaca_warmup(_core_symbols)
            print("✅ Alpaca real-time data active (streaming + snapshots)")
        else:
            print("ℹ️  Alpaca not configured — using yfinance for market data")
    except Exception as _alp_err:
        print(f"ℹ️  Alpaca real-time unavailable: {_alp_err}")

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

    # ── Public tunnel via Cloudflare (free, no signup needed) ───────
    # Auto-activates when network allows it. Disable with TUNNEL=0 env var.
    public_url = None
    _tunnel_proc = None
    if os.environ.get('TUNNEL', '1') != '0':
        try:
            import subprocess as _sp, re as _re
            _cf_bin = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'cloudflared')
            if not os.path.isfile(_cf_bin):
                raise FileNotFoundError("cloudflared binary not found in .venv/bin/")
            _tunnel_proc = _sp.Popen(
                [_cf_bin, 'tunnel', '--url', f'http://localhost:{PORT}'],
                stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True
            )
            # Read output until we find the public URL (with timeout)
            _deadline = time.time() + 25
            while time.time() < _deadline:
                line = _tunnel_proc.stdout.readline()
                if not line:
                    break
                # Check for failure early
                if 'failed to request quick Tunnel' in line or 'no such host' in line:
                    print("⚠️  Tunnel blocked by network (corporate/VPN) — LAN-only mode")
                    _tunnel_proc.terminate()
                    _tunnel_proc = None
                    break
                m = _re.search(r'(https://[a-z0-9][\w-]*\.trycloudflare\.com)', line)
                if m:
                    public_url = m.group(1)
                    break

            if public_url:
                import config as _cfg
                _cfg.BASE_URL = public_url
                app.config['BASE_URL'] = public_url

            # Keep reading tunnel output in background so pipe doesn't block
            if _tunnel_proc and _tunnel_proc.poll() is None:
                def _drain_tunnel():
                    try:
                        for _line in _tunnel_proc.stdout:
                            pass
                    except Exception:
                        pass
                threading.Thread(target=_drain_tunnel, daemon=True).start()

        except Exception as _cf_err:
            print(f"⚠️  Cloudflare tunnel failed ({_cf_err}) — app still available on LAN")

    # Clean up tunnel on exit
    def _kill_tunnel():
        if _tunnel_proc and _tunnel_proc.poll() is None:
            _tunnel_proc.terminate()
    atexit.register(_kill_tunnel)

    local_ip = _get_local_ip()
    print("\n" + "=" * 100)
    print("🌐  ACCESS URLS")
    print(f"   Local :  http://localhost:{PORT}")
    if local_ip:
        print(f"   LAN   :  http://{local_ip}:{PORT}   (same Wi-Fi)")
    if public_url:
        print(f"   Public:  {public_url}   ← share this with anyone")
    else:
        print("   Public:  Not available (blocked by network). Try from home Wi-Fi.")
    print("=" * 100 + "\n")

    # Start background position monitor
    start_background_monitor(app)
    atexit.register(stop_background_monitor)

    socketio.run(app, host='0.0.0.0', port=PORT, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()
