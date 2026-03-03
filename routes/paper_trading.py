"""
Paper Trading Routes - Paper trading simulation endpoints.
"""
from flask import Blueprint, jsonify, request
import json
import os
import threading
from datetime import datetime

paper_bp = Blueprint("paper_trading", __name__)

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
@paper_bp.route('/api/paper/start', methods=['POST'])
def paper_start():
    with PAPER_TRADING_LOCK:
        state = reset_paper_state()
    return jsonify({'success': True, 'data': state})

@paper_bp.route('/api/paper/balance', methods=['GET'])
def paper_balance():
    with PAPER_TRADING_LOCK:
        state = load_paper_state()
    return jsonify({'success': True, 'data': {'balance': state['balance'], 'capital': state['capital']}})

@paper_bp.route('/api/paper/positions', methods=['GET'])
def paper_positions():
    with PAPER_TRADING_LOCK:
        state = load_paper_state()
    return jsonify({'success': True, 'data': state['positions']})

@paper_bp.route('/api/paper/trade', methods=['POST'])
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

@paper_bp.route('/api/paper/close', methods=['POST'])
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

@paper_bp.route('/api/paper/reset', methods=['POST'])
def paper_reset():
    with PAPER_TRADING_LOCK:
        state = reset_paper_state()
    return jsonify({'success': True, 'data': state})
