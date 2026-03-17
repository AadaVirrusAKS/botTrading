"""
Autonomous Trading Routes - Autonomous trader status, control, analysis.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import os
import threading
from datetime import datetime

from services.utils import clean_nan_values
from services.market_data import autonomous_trader_state
from config import DATA_DIR

# Import Autonomous Trading Agent
try:
    from trading.autonomous_deepseek_trader import AutonomousTrader, DeepSeekAnalyzer, RiskManager
    AUTONOMOUS_AVAILABLE = True
except ImportError:
    AUTONOMOUS_AVAILABLE = False

autonomous_bp = Blueprint("autonomous", __name__)

@autonomous_bp.route('/api/autonomous/status')
def autonomous_status():
    """Get current autonomous trading status"""
    import os
    
    # Check if API key is set
    api_key_set = bool(os.environ.get('DEEPSEEK_API_KEY'))
    alpaca_key_set = bool(os.environ.get('ALPACA_API_KEY'))
    
    # Load state from file if exists
    state_file = os.path.join(DATA_DIR, 'autonomous_trader_state.json')
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
    log_file = os.path.join(DATA_DIR, 'autonomous_trade_log.json')
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

@autonomous_bp.route('/api/autonomous/start', methods=['POST'])
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

@autonomous_bp.route('/api/autonomous/stop', methods=['POST'])
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

@autonomous_bp.route('/api/autonomous/analyze', methods=['POST'])
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

@autonomous_bp.route('/api/autonomous/settings', methods=['POST'])
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

@autonomous_bp.route('/api/autonomous/positions')
def autonomous_positions():
    """Get current autonomous trading positions"""
    import os
    
    state_file = os.path.join(DATA_DIR, 'autonomous_trader_state.json')
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

@autonomous_bp.route('/api/autonomous/trades')
def autonomous_trades():
    """Get trade history"""
    import os
    
    log_file = os.path.join(DATA_DIR, 'autonomous_trade_log.json')
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

