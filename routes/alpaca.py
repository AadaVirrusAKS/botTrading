"""
Alpaca Paper Trading Routes - API endpoints for Alpaca integration.
"""
import json
import os
from flask import Blueprint, jsonify, request
from services.alpaca_service import (
    ALPACA_AVAILABLE,
    is_configured,
    get_credentials,
    save_credentials,
    validate_credentials,
    reset_client,
    get_account,
    get_positions,
    close_position,
    close_all_positions,
    place_order,
    get_orders,
    cancel_order,
    cancel_all_orders,
)

alpaca_bp = Blueprint("alpaca", __name__)


# =============================
# STATUS & CONFIGURATION
# =============================
@alpaca_bp.route('/api/alpaca/status')
def alpaca_status():
    """Check Alpaca integration status."""
    return jsonify({
        'success': True,
        'data': {
            'sdk_installed': ALPACA_AVAILABLE,
            'configured': is_configured(),
            'has_keys': bool(get_credentials()[0]),
        }
    })


@alpaca_bp.route('/api/alpaca/connect', methods=['POST'])
def alpaca_connect():
    """Save and validate Alpaca API credentials."""
    req = request.get_json(force=True)
    api_key = req.get('api_key', '').strip()
    secret_key = req.get('secret_key', '').strip()

    if not api_key or not secret_key:
        return jsonify({'success': False, 'error': 'API key and secret key are required'}), 400

    if not ALPACA_AVAILABLE:
        return jsonify({'success': False, 'error': 'alpaca-py not installed. Run: pip install alpaca-py'}), 500

    valid, msg = validate_credentials(api_key, secret_key)
    if not valid:
        return jsonify({'success': False, 'error': msg}), 401

    save_credentials(api_key, secret_key)
    reset_client()
    return jsonify({'success': True, 'message': msg})


@alpaca_bp.route('/api/alpaca/disconnect', methods=['POST'])
def alpaca_disconnect():
    """Remove saved Alpaca credentials."""
    import os
    from config import DATA_DIR
    config_file = os.path.join(DATA_DIR, 'alpaca_config.json')
    if os.path.exists(config_file):
        os.remove(config_file)
    reset_client()
    return jsonify({'success': True, 'message': 'Disconnected from Alpaca'})


# =============================
# ACCOUNT
# =============================
@alpaca_bp.route('/api/alpaca/account')
def alpaca_account():
    """Get Alpaca paper trading account info."""
    try:
        account = get_account()
        return jsonify({'success': True, 'data': account})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================
# POSITIONS
# =============================
@alpaca_bp.route('/api/alpaca/positions')
def alpaca_positions():
    """Get all open Alpaca positions."""
    try:
        positions = get_positions()
        return jsonify({'success': True, 'data': positions})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@alpaca_bp.route('/api/alpaca/positions/<symbol>/close', methods=['POST'])
def alpaca_close_position(symbol):
    """Close a specific position."""
    try:
        req = request.get_json(force=True) if request.data else {}
        qty = req.get('qty')
        result = close_position(symbol, float(qty) if qty else None)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@alpaca_bp.route('/api/alpaca/positions/close-all', methods=['POST'])
def alpaca_close_all():
    """Close all open positions."""
    try:
        result = close_all_positions()
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================
# ORDERS
# =============================
@alpaca_bp.route('/api/alpaca/orders', methods=['GET'])
def alpaca_orders():
    """Get orders. Query params: status=open|closed|all, limit=50"""
    try:
        status = request.args.get('status', 'all')
        limit = int(request.args.get('limit', 50))
        orders = get_orders(status=status, limit=limit)
        return jsonify({'success': True, 'data': orders})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@alpaca_bp.route('/api/alpaca/orders', methods=['POST'])
def alpaca_place_order():
    """Place a new order."""
    try:
        req = request.get_json(force=True)
        symbol = req.get('symbol', '').strip().upper()
        qty = req.get('qty')
        side = req.get('side', '').lower()
        order_type = req.get('order_type', 'market').lower()
        limit_price = req.get('limit_price')
        stop_price = req.get('stop_price')
        time_in_force = req.get('time_in_force', 'day')
        take_profit = req.get('take_profit')
        stop_loss = req.get('stop_loss')

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol is required'}), 400
        if not qty or float(qty) <= 0:
            return jsonify({'success': False, 'error': 'Quantity must be positive'}), 400
        if side not in ('buy', 'sell'):
            return jsonify({'success': False, 'error': 'Side must be buy or sell'}), 400

        result = place_order(
            symbol=symbol,
            qty=float(qty),
            side=side,
            order_type=order_type,
            limit_price=float(limit_price) if limit_price else None,
            stop_price=float(stop_price) if stop_price else None,
            time_in_force=time_in_force,
            take_profit=float(take_profit) if take_profit else None,
            stop_loss=float(stop_loss) if stop_loss else None,
        )
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@alpaca_bp.route('/api/alpaca/orders/<order_id>/cancel', methods=['POST'])
def alpaca_cancel_order(order_id):
    """Cancel a specific order."""
    try:
        result = cancel_order(order_id)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@alpaca_bp.route('/api/alpaca/orders/cancel-all', methods=['POST'])
def alpaca_cancel_all():
    """Cancel all open orders."""
    try:
        result = cancel_all_orders()
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# =============================
# BOT P&L MAP
# =============================
@alpaca_bp.route('/api/alpaca/bot-pnl-map')
def alpaca_bot_pnl_map():
    """Return {alpaca_order_id: pnl} from bot trades so Alpaca page shows bot P&L."""
    try:
        from config import DATA_DIR
        # Use the same state file as ai_trading.py (bot_state_user_1.json)
        state_file = os.path.join(DATA_DIR, 'bot_state_user_1.json')
        if not os.path.exists(state_file):
            # Fallback to old file
            state_file = os.path.join(DATA_DIR, 'ai_bot_state.json')
        if not os.path.exists(state_file):
            return jsonify({'success': True, 'data': {}})

        with open(state_file, 'r') as f:
            state = json.load(f)

        pnl_map = {}
        acct = state.get('demo_account', {})
        for trade in acct.get('trades', []):
            order_id = trade.get('alpaca_order_id')
            pnl = trade.get('pnl')
            if order_id and pnl is not None:
                pnl_map[order_id] = round(pnl, 2)

        return jsonify({'success': True, 'data': pnl_map})
    except Exception as e:
        return jsonify({'success': False, 'data': {}, 'error': str(e)})


# =============================
# REAL-TIME MARKET DATA
# =============================
@alpaca_bp.route('/api/alpaca/realtime/status')
def alpaca_realtime_status():
    """Check if Alpaca real-time data is available and streaming."""
    try:
        from services.alpaca_realtime import is_available, _stream_thread, _live_prices
        streaming = _stream_thread is not None and _stream_thread.is_alive()
        return jsonify({
            'success': True,
            'data': {
                'available': is_available(),
                'streaming': streaming,
                'symbols_tracked': len(_live_prices),
            }
        })
    except ImportError:
        return jsonify({'success': True, 'data': {'available': False, 'streaming': False, 'symbols_tracked': 0}})


@alpaca_bp.route('/api/alpaca/realtime/prices', methods=['POST'])
def alpaca_realtime_prices():
    """Get real-time prices for a list of symbols.

    POST body: {"symbols": ["AAPL", "MSFT", ...]}
    Returns real-time Alpaca prices when available, falls back to cached yfinance data.
    """
    req = request.get_json(force=True)
    symbols = req.get('symbols', [])
    if not symbols:
        return jsonify({'success': False, 'error': 'No symbols provided'})

    prices = {}
    source = 'yfinance'

    try:
        from services.alpaca_realtime import is_available, get_live_prices, get_snapshot_prices
        if is_available():
            source = 'alpaca'
            # Try stream cache first
            prices = get_live_prices(symbols)
            # Snapshot for any missing
            missing = [s for s in symbols if s.upper() not in prices]
            if missing:
                snaps = get_snapshot_prices(missing)
                prices.update(snaps)
    except ImportError:
        pass

    # Fallback to yfinance for any still missing
    still_missing = [s for s in symbols if s.upper() not in prices]
    if still_missing:
        from services.market_data import cached_batch_prices
        yf_prices = cached_batch_prices(still_missing)
        for sym, p in yf_prices.items():
            if sym not in prices:
                prices[sym] = p

    return jsonify({
        'success': True,
        'data': {
            'prices': {k: round(v, 2) for k, v in prices.items()},
            'source': source,
            'timestamp': __import__('datetime').datetime.now().isoformat(),
        }
    })


@alpaca_bp.route('/api/alpaca/realtime/subscribe', methods=['POST'])
def alpaca_realtime_subscribe():
    """Start streaming symbols via Alpaca WebSocket.

    POST body: {"symbols": ["AAPL", "MSFT", ...]}
    """
    req = request.get_json(force=True)
    symbols = req.get('symbols', [])
    if not symbols:
        return jsonify({'success': False, 'error': 'No symbols provided'})

    try:
        from services.alpaca_realtime import is_available, warmup
        if not is_available():
            return jsonify({'success': False, 'error': 'Alpaca not configured'})
        warmup(symbols)
        return jsonify({'success': True, 'data': {'subscribed': [s.upper() for s in symbols]}})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
