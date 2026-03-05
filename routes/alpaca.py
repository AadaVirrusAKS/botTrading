"""
Alpaca Paper Trading Routes - API endpoints for Alpaca integration.
"""
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
    config_file = 'alpaca_config.json'
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
