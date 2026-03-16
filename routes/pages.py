"""
Page Routes & Misc API - Template rendering, symbol suggest, quote, scanner status.
"""
from flask import Blueprint, render_template, jsonify, request
from datetime import datetime
import yfinance as yf
import time

from services.utils import clean_nan_values
from services.market_helpers import get_live_quote
from services.market_data import scanner_cache, scanner_cache_timeout, cached_get_ticker_info, _is_globally_rate_limited
from services.symbols import COMMON_STOCKS, is_valid_symbol_cached

pages_bp = Blueprint("pages", __name__)

@pages_bp.route('/api/health')
def api_health():
    """Health check endpoint for frontend reconnection detection."""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'rate_limited': _is_globally_rate_limited()
    })


@pages_bp.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')

@pages_bp.route('/test-debug')
def test_debug():
    """Debug test page"""
    return render_template('test_debug.html')

@pages_bp.route('/scanners')
def scanners_page():
    """Scanner results page"""
    return render_template('scanners.html')

@pages_bp.route('/options')
def options_page():
    """Options analysis page"""
    return render_template('options.html')

@pages_bp.route('/monitoring')
def monitoring_page():
    """Live position monitoring page"""
    return render_template('monitoring.html')

@pages_bp.route('/technical-analysis')
def technical_analysis_page():
    """Advanced technical analysis page with TradingView-like indicators"""
    return render_template('technical_analysis.html')

@pages_bp.route('/test-chart')
def test_chart():
    """Simple chart test page"""
    return render_template('test_chart_simple.html')

@pages_bp.route('/diagnostic')
def diagnostic():
    """Chart diagnostic page"""
    return render_template('chart_diagnostic.html')

@pages_bp.route('/autonomous')
def autonomous_page():
    """Autonomous AI Trading page"""
    return render_template('autonomous.html')

@pages_bp.route('/ai-trading')
def ai_trading_page():
    """AI Trading Bot page with Demo/Real account modes"""
    return render_template('ai_trading.html')

@pages_bp.route('/crypto')
def crypto_page():
    """Crypto Dashboard page"""
    return render_template('crypto.html')

@pages_bp.route('/alpaca')
def alpaca_page():
    """Alpaca Paper Trading page"""
    return render_template('alpaca.html')

@pages_bp.route('/ai-analysis')
def ai_analysis_page():
    """AI Stock Analysis - Candlestick pattern recognition & price prediction"""
    return render_template('ai_analysis.html')

@pages_bp.route('/daily-agent')
def daily_agent_page():
    """Daily Analysis Agent - Automated bot performance analysis & suggestions"""
    return render_template('daily_agent.html')


@pages_bp.route('/api/symbol/suggest')
def symbol_suggest():
    """
    Suggest ticker symbols for a partial company name or symbol.
    Returns a list of {symbol, name, exchange, type} dicts.
    """
    import requests as req_lib
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify({'success': False, 'error': 'No query provided', 'suggestions': []}), 400
    
    suggestions = []
    seen_symbols = set()
    
    # First, check common stocks mapping for instant results
    for name_key, stock_info in COMMON_STOCKS.items():
        if query in name_key or query in stock_info['symbol'].lower():
            if stock_info['symbol'] not in seen_symbols:
                suggestions.append({
                    'symbol': stock_info['symbol'],
                    'name': stock_info['name'],
                    'exchange': 'NASDAQ/NYSE',
                    'type': 'EQUITY'
                })
                seen_symbols.add(stock_info['symbol'])
    
    # If we have enough suggestions from common stocks, return them
    if len(suggestions) >= 5:
        return jsonify({'success': True, 'suggestions': suggestions[:10]})
    
    # Try Yahoo Finance API as fallback
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=10&newsCount=0"
        resp = req_lib.get(url, timeout=5, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('quotes', []):
                if item.get('quoteType') == 'EQUITY' and 'symbol' in item:
                    symbol = item['symbol']
                    if symbol not in seen_symbols:
                        suggestions.append({
                            'symbol': symbol,
                            'name': item.get('shortname') or item.get('longname') or item.get('name') or '',
                            'exchange': item.get('exchange', ''),
                            'type': item.get('quoteType', '')
                        })
                        seen_symbols.add(symbol)
    except Exception as e:
        print(f"[symbol_suggest] Yahoo API error: {e}")
    
    # If still no suggestions, try to validate as direct symbol
    if not suggestions and len(query) <= 6:
        try:
            ticker = yf.Ticker(query.upper())
            info = ticker.info
            if info and info.get('regularMarketPrice'):
                suggestions.append({
                    'symbol': query.upper(),
                    'name': info.get('shortName', info.get('longName', query.upper())),
                    'exchange': info.get('exchange', ''),
                    'type': 'EQUITY'
                })
        except Exception:
            pass
    
    return jsonify({'success': True, 'suggestions': suggestions[:10]})

# Cache for reducing API calls
quote_cache = {}
cache_timeout = 60  # seconds (increased from 15 to reduce Yahoo API hits)
active_subscriptions = {}  # Track active subscription threads



@pages_bp.route('/api/scanner/status')
def scanner_status():
    """Get status of all scanners"""
    try:
        status = {}
        for key, cache_entry in scanner_cache.items():
            status[key] = {
                'running': cache_entry['running'],
                'has_data': cache_entry['data'] is not None,
                'data_age_seconds': None
            }
            if cache_entry['timestamp'] is not None:
                age = (datetime.now() - cache_entry['timestamp']).total_seconds()
                status[key]['data_age_seconds'] = int(age)
                status[key]['cache_valid'] = age < scanner_cache_timeout
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'scanners': status
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@pages_bp.route('/api/scanner/trigger-all', methods=['POST'])
def trigger_all_scanners():
    """Manually trigger all scanners to run in background"""
    try:
        # Trigger each scanner by calling their endpoints
        triggered = []
        for scanner_type in ['unified', 'short-squeeze', 'quality-stocks']:
            cache_entry = scanner_cache[scanner_type]
            if not cache_entry['running']:
                triggered.append(scanner_type)
        
        # The actual triggering happens when someone accesses the endpoints
        return jsonify({
            'success': True,
            'message': f'Access scanner endpoints to trigger: {triggered}',
            'scanners_to_trigger': triggered
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@pages_bp.route('/api/quote/<symbol>')
def get_quote(symbol):
    """Get live quote for a symbol"""
    try:
        quote = get_live_quote(symbol.upper())
        if quote:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'quote': quote
            })
        else:
            return jsonify({'success': False, 'error': 'Quote not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

