"""
Scanner Routes - All stock/ETF scanner endpoints.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import numpy as np
import pandas as pd

from services.utils import clean_nan_values, SECTOR_ETFS
from services.market_data import (
    cached_batch_prices, cached_get_price, cached_get_history,
    cached_get_ticker_info, cached_get_option_dates, cached_get_option_chain,
    scanner_cache, scanner_cache_timeout,
    _is_rate_limited, _log_fetch_event,
    _is_rate_limit_error, _mark_rate_limited, _mark_global_rate_limit,
    _is_expected_no_data_error, fetch_quote_api_batch
)
from services.symbols import is_valid_symbol_cached, filter_valid_symbols

# Import scanner modules
from unified_trading_system import UnifiedTradingSystem
from short_squeeze_scanner import ShortSqueezeScanner
from beaten_down_quality_scanner import BeatenDownQualityScanner

try:
    from weekly_screener_top100 import WeeklyStockScreener
except ImportError:
    WeeklyStockScreener = None

try:
    from us_market_golden_cross_scanner import USMarketScanner
except ImportError:
    USMarketScanner = None

try:
    from triple_confirmation_scanner import TripleConfirmationScanner
except ImportError:
    TripleConfirmationScanner = None

try:
    from triple_confirmation_intraday import TripleConfirmationIntraday
except ImportError:
    TripleConfirmationIntraday = None

try:
    from triple_confirmation_positional import TripleConfirmationPositional
except ImportError:
    TripleConfirmationPositional = None

scanners_bp = Blueprint("scanners", __name__)

@scanners_bp.route('/api/scanner/unified')
def unified_scanner():
    """Run unified trading system scanner with caching"""
    try:
        cache_key = 'unified'
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'picks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting unified scanner in background...")
                    system = UnifiedTradingSystem()
                    
                    # Limit stock universes to avoid rate limiting
                    system.stock_symbols = [
                        'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AMD',
                        'JPM', 'BAC', 'V', 'MA', 'WFC', 'GS',
                        'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK',
                        'WMT', 'HD', 'COST', 'NKE', 'MCD'
                    ]
                    system.etf_symbols = ['SPY', 'QQQ', 'IWM', 'DIA', 'XLF', 'XLK', 'XLE', 'XLV']
                    
                    picks = system.get_top_5_picks()
                    # Clean NaN values for JSON serialization
                    picks = clean_nan_values(picks)
                    scanner_cache[cache_key]['data'] = picks
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Unified scanner complete!")
                except Exception as e:
                    print(f"❌ Unified scanner error: {e}")
                    scanner_cache[cache_key]['data'] = {'options': [], 'stocks': [], 'etfs': []}
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'picks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'picks': {'options': [], 'stocks': [], 'etfs': []},
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/short-squeeze')
def short_squeeze():
    """Run short squeeze scanner with caching"""
    try:
        cache_key = 'short-squeeze'
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'candidates': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting short squeeze scanner in background...")
                    scanner = ShortSqueezeScanner()
                    # Reduce workers to avoid rate limiting (3 instead of 10)
                    # Focus on popular squeeze candidates to avoid API limits
                    priority_symbols = [
                        'GME', 'AMC', 'BB', 'KOSS', 'DJT',
                        'HOOD', 'SOFI', 'PLTR', 'RIVN', 'LCID', 'NIO', 'TSLA',
                        'NVDA', 'AMD', 'AAPL', 'MSFT', 'META', 'GOOGL', 'AMZN',
                        'MARA', 'RIOT', 'BLNK'
                    ]
                    # Limit to priority symbols to avoid rate limits
                    scanner.symbols = filter_valid_symbols(priority_symbols)
                    
                    # Use sequential processing (max_workers=1) to avoid rate limiting
                    # Batch processing: 5 stocks, then 10s wait
                    df = scanner.scan_market(min_squeeze_score=40, max_workers=1)
                    
                    if not df.empty:
                        results = df.head(20).to_dict('records')
                        # Clean NaN values for JSON serialization
                        results = clean_nan_values(results)
                    else:
                        print("⚠️ No squeeze candidates found (API may be rate limited)")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Short squeeze scanner complete! Found {len(results)} candidates")
                except Exception as e:
                    print(f"❌ Short squeeze scanner error: {e}")
                    # On error, return empty results
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'candidates': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'candidates': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/weekly-screener')
def weekly_screener():
    """Run weekly screener with caching"""
    try:
        cache_key = 'weekly-screener'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting weekly screener in background...")
                    
                    try:
                        from weekly_screener_top100 import WeeklyStockScreener
                        screener = WeeklyStockScreener()
                        
                        # Limit stock universe to top 50 most liquid stocks
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSLA', 'AVGO', 'ORCL', 'ADBE',
                            'CRM', 'CSCO', 'AMD', 'INTC', 'QCOM', 'NFLX', 'TXN', 'INTU', 'NOW',
                            'JPM', 'V', 'MA', 'BAC', 'WFC', 'GS', 'MS', 'SPGI', 'BLK',
                            'LLY', 'UNH', 'JNJ', 'ABBV', 'MRK', 'TMO', 'ABT', 'AMGN', 'DHR', 'PFE',
                            'WMT', 'HD', 'PG', 'COST', 'KO', 'MCD', 'PEP', 'NKE', 'TGT', 'SBUX'
                        ]
                        screener.stock_universe = filter_valid_symbols(limited_universe)
                        
                        # Reduce workers to avoid rate limiting
                        # Lower min_score to 3 (weekly scanner has different scoring: max ~15)
                        df = screener.scan_market(min_score=3, max_workers=3)
                        
                        if not df.empty:
                            results = df.head(20).to_dict('records')
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No weekly signals found matching criteria")
                            results = []
                    except ImportError:
                        print("❌ Weekly screener module not available")
                        results = []
                    except Exception as scan_error:
                        print(f"❌ Weekly scan error: {scan_error}")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Weekly screener complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Weekly screener error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/quality-stocks')
def quality_stocks():
    """Run beaten down quality stocks scanner with caching"""
    try:
        cache_key = 'quality-stocks'
        cache_entry = scanner_cache[cache_key]
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting quality stocks scanner in background...")
                    scanner = BeatenDownQualityScanner()
                    
                    # Use full universe including high-growth small-caps (VERO, MARA, etc.)
                    # No need to limit - we want to find explosive growth opportunities
                    
                    # Reduce workers to avoid rate limiting
                    # Relaxed filters: -5% drawdown (instead of -15%), quality 30+ (instead of 50+)
                    df = scanner.scan_market(min_drawdown=-5, min_quality_score=30, max_workers=3)
                    
                    if not df.empty:
                        results = df.head(20).to_dict('records')
                        results = clean_nan_values(results)
                    else:
                        print("⚠️ No quality stocks found matching criteria")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Quality stocks scanner complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Quality stocks scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/golden-cross')
def golden_cross_scanner():
    """Run golden cross scanner with caching"""
    try:
        cache_key = 'golden-cross'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting golden cross scanner in background...")
                    
                    try:
                        from us_market_golden_cross_scanner import USMarketScanner
                        scanner = USMarketScanner()
                        
                        # Limit to most popular liquid stocks to avoid rate limits
                        limited_sectors = {
                            'Technology': ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'AMD', 'INTC', 'CSCO', 'ORCL'],
                            'Financial': ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'V', 'MA', 'C', 'AXP', 'BLK'],
                            'Healthcare': ['UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'PFE', 'TMO', 'ABT'],
                            'Consumer': ['WMT', 'HD', 'NKE', 'MCD', 'SBUX', 'COST', 'TGT']
                        }
                        
                        # Override to use limited list
                        scanner.get_all_us_stocks = lambda: limited_sectors
                        
                        # Scan with retries and lower threshold
                        sector_results, all_results = scanner.scan_market()
                        
                        # Flatten sector results into a single list
                        results = []
                        if sector_results:
                            for sector, df in sector_results.items():
                                if not df.empty:
                                    # Convert DataFrame to dict
                                    for _, row in df.iterrows():
                                        try:
                                            record = {
                                                'symbol': str(row.get('Ticker', '')),
                                                'company': str(row.get('Company', '')),
                                                'sector': sector,
                                                'price': float(row.get('Price', 0)),
                                                'rsi': float(row.get('RSI', 50)),
                                                'volume': int(row.get('Volume', 0)),
                                                'score': int(row.get('Score', 0)),
                                                'ema_50': float(row.get('50 EMA', 0)),
                                                'ema_200': float(row.get('200 EMA', 0)),
                                                'entry_price': float(row.get('Entry', row.get('Price', 0))),
                                                'stop_loss': float(row.get('Stop Loss', 0)),
                                                'target': float(row.get('Take Profit', 0)),
                                                'risk': float(row.get('Risk', 0)),
                                                'reward': float(row.get('Reward', 0)),
                                                'risk_reward': float(row.get('Risk/Reward', 5)),
                                                'market_cap': int(row.get('Market Cap', 0)),
                                                'golden_cross': True,
                                                'recommendation': 'BUY',
                                                'type': 'STOCK'
                                            }
                                            results.append(record)
                                        except Exception as e:
                                            print(f"Error parsing row: {e}")
                                            continue
                        
                        # Sort by score and limit to top 20
                        if results:
                            results = clean_nan_values(results)
                            results = sorted(results, key=lambda x: x.get('score', 0), reverse=True)[:20]
                                
                    except ImportError:
                        print("❌ Golden cross scanner module not available")
                        results = []
                    except Exception as scan_error:
                        print(f"❌ Scan error: {scan_error}")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Golden cross scanner complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Golden cross scanner error: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 2-3 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/triple-confirmation')
def triple_confirmation_scanner():
    """Run triple confirmation scanner with caching (default swing trades)"""
    try:
        cache_key = 'triple-confirmation'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting triple confirmation scanner in background...")
                    
                    if TripleConfirmationScanner:
                        scanner = TripleConfirmationScanner()
                        
                        # Limit to top liquid stocks to avoid rate limits
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'META', 'AMZN', 'AMD', 'INTC', 'CRM',
                            'JPM', 'BAC', 'GS', 'MS', 'WFC', 'C', 'V', 'MA', 'AXP',
                            'UNH', 'JNJ', 'PFE', 'ABBV', 'TMO', 'MRK', 'LLY',
                            'WMT', 'HD', 'NKE', 'MCD', 'SBUX', 'TGT', 'COST'
                        ]
                        scanner.universe = filter_valid_symbols(limited_universe)
                        
                        scanner.scan_market()  # Populates scanner.results
                        
                        if scanner.results:
                            results = scanner.results[:20]  # Top 20
                            # Normalize field names: ticker -> symbol
                            for item in results:
                                if 'ticker' in item and 'symbol' not in item:
                                    item['symbol'] = item['ticker']
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No triple confirmation signals found")
                            results = []
                    else:
                        print("⚠️ Triple confirmation scanner not available")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Triple confirmation scanner complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Triple confirmation scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/triple-intraday')
def triple_confirmation_intraday():
    """Run triple confirmation intraday scanner with caching"""
    try:
        cache_key = 'triple-intraday'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting triple confirmation intraday scanner in background...")
                    
                    if TripleConfirmationIntraday:
                        scanner = TripleConfirmationIntraday()
                        
                        # Limit to most active intraday stocks
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'META', 'GOOGL', 'AMZN',
                            'SPY', 'QQQ', 'IWM', 'DIA',
                            'JPM', 'BAC', 'WFC', 'GS',
                            'NFLX', 'PLTR', 'SOFI', 'RIVN', 'NIO'
                        ]
                        scanner.universe = filter_valid_symbols(limited_universe)
                        
                        scanner.scan_market()  # Populates scanner.results
                        
                        if scanner.results:
                            results = scanner.results[:20]  # Top 20
                            # Normalize field names: ticker -> symbol
                            for item in results:
                                if 'ticker' in item and 'symbol' not in item:
                                    item['symbol'] = item['ticker']
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No intraday triple confirmation signals found")
                            results = []
                    else:
                        print("⚠️ Triple confirmation intraday scanner not available")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Triple confirmation intraday complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Triple confirmation intraday error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/triple-positional')
def triple_confirmation_positional():
    """Run triple confirmation positional scanner with caching"""
    try:
        cache_key = 'triple-positional'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if we have valid cached data
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan if not already running
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print(f"🔄 Starting triple confirmation positional scanner in background...")
                    
                    if TripleConfirmationPositional:
                        scanner = TripleConfirmationPositional()
                        
                        # Limit to stable blue-chip stocks for positional trades
                        limited_universe = [
                            'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'ORCL', 'ADBE', 'CRM',
                            'JPM', 'BAC', 'WFC', 'V', 'MA', 'GS', 'MS',
                            'UNH', 'JNJ', 'LLY', 'ABBV', 'MRK', 'PFE', 'TMO',
                            'WMT', 'HD', 'COST', 'NKE', 'MCD', 'SBUX',
                            'XOM', 'CVX', 'COP'
                        ]
                        scanner.universe = filter_valid_symbols(limited_universe)
                        
                        scanner.scan_market()  # Populates scanner.results
                        
                        if scanner.results:
                            results = scanner.results[:20]  # Top 20
                            # Normalize field names: ticker -> symbol
                            for item in results:
                                if 'ticker' in item and 'symbol' not in item:
                                    item['symbol'] = item['ticker']
                            results = clean_nan_values(results)
                        else:
                            print("⚠️ No positional triple confirmation signals found")
                            results = []
                    else:
                        print("⚠️ Triple confirmation positional scanner not available")
                        results = []
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Triple confirmation positional complete! Found {len(results)} stocks")
                except Exception as e:
                    print(f"❌ Triple confirmation positional error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/triple-confirmation-all')
def triple_confirmation_all():
    """Run all three Triple Confirmation scanners and return combined results"""
    try:
        from triple_confirmation_scanner import TripleConfirmationScanner
        from triple_confirmation_intraday import TripleConfirmationIntraday
        from triple_confirmation_positional import TripleConfirmationPositional
        
        cache_key = 'triple-confirmation-all'
        cache_entry = scanner_cache[cache_key]
        
        # Check if scan is already running
        if cache_entry['running']:
            if cache_entry['data'] is not None:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'swing': cache_entry['data'].get('swing', []),
                    'intraday': cache_entry['data'].get('intraday', []),
                    'positional': cache_entry['data'].get('positional', []),
                    'scanning': True,
                    'message': 'Scanner still running. Showing partial results.'
                })
            else:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'swing': [],
                    'intraday': [],
                    'positional': [],
                    'scanning': True,
                    'message': 'Scanner running in background. Please wait...'
                })
        
        # Check cache (5 minutes)
        if cache_entry['data'] is not None and cache_entry['timestamp']:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:  # 5 minutes
                return jsonify({
                    'success': True,
                    'timestamp': cache_entry['timestamp'].isoformat(),
                    'swing': cache_entry['data'].get('swing', []),
                    'intraday': cache_entry['data'].get('intraday', []),
                    'positional': cache_entry['data'].get('positional', []),
                    'cached': True
                })
        
        # Run all three scanners in background
        def run_all_scans():
            try:
                scanner_cache[cache_key]['running'] = True
                
                # Run all three scanners
                swing_scanner = TripleConfirmationScanner()
                intraday_scanner = TripleConfirmationIntraday()
                positional_scanner = TripleConfirmationPositional()
                
                swing_results = []
                intraday_results = []
                positional_results = []
                
                # Scan swing trades
                try:
                    swing_data = swing_scanner.scan_market()
                    if swing_data:  # scan_market returns a list, not DataFrame
                        swing_results = swing_data[:20]  # Top 20
                        # Normalize field names: ticker -> symbol
                        for item in swing_results:
                            if 'ticker' in item and 'symbol' not in item:
                                item['symbol'] = item['ticker']
                        swing_results = clean_nan_values(swing_results)
                except Exception as e:
                    print(f"Error in swing scanner: {e}")
                
                # Scan intraday
                try:
                    intraday_data = intraday_scanner.scan_market()
                    if intraday_data:  # scan_market returns a list, not DataFrame
                        intraday_results = intraday_data[:20]  # Top 20
                        # Normalize field names: ticker -> symbol
                        for item in intraday_results:
                            if 'ticker' in item and 'symbol' not in item:
                                item['symbol'] = item['ticker']
                        intraday_results = clean_nan_values(intraday_results)
                except Exception as e:
                    print(f"Error in intraday scanner: {e}")
                
                # Scan positional
                try:
                    positional_data = positional_scanner.scan_market()
                    if positional_data:  # scan_market returns a list, not DataFrame
                        positional_results = positional_data[:20]  # Top 20
                        # Normalize field names: ticker -> symbol
                        for item in positional_results:
                            if 'ticker' in item and 'symbol' not in item:
                                item['symbol'] = item['ticker']
                        positional_results = clean_nan_values(positional_results)
                except Exception as e:
                    print(f"Error in positional scanner: {e}")
                
                # Store combined results
                combined_data = {
                    'swing': swing_results,
                    'intraday': intraday_results,
                    'positional': positional_results
                }
                
                scanner_cache[cache_key]['data'] = combined_data
                scanner_cache[cache_key]['timestamp'] = datetime.now()
                
            except Exception as e:
                print(f"Error in triple confirmation all: {e}")
                scanner_cache[cache_key]['data'] = {'swing': [], 'intraday': [], 'positional': []}
            finally:
                scanner_cache[cache_key]['running'] = False
        
        threading.Thread(target=run_all_scans, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'swing': cache_entry['data'].get('swing', []),
                'intraday': cache_entry['data'].get('intraday', []),
                'positional': cache_entry['data'].get('positional', []),
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'swing': [],
                'intraday': [],
                'positional': [],
                'scanning': True,
                'message': 'Scanner running in background. Please refresh in 1-2 minutes.'
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@scanners_bp.route('/api/scanner/volume-spike')
def volume_spike_scanner():
    """Scan for stocks with unusual volume and price spikes (like VERO)"""
    try:
        cache_key = 'volume-spike'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check if market is open (9:30 AM - 4:00 PM ET, weekdays, excluding holidays)
        import pytz
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        is_weekday = now_et.weekday() < 5  # Monday=0, Friday=4
        is_market_hours = (now_et.hour == 9 and now_et.minute >= 30) or (10 <= now_et.hour < 16)
        
        # Check for major US holidays (2026 stock market holidays)
        us_holidays_2026 = [
            (1, 1),   # New Year's Day
            (1, 19),  # MLK Day (3rd Monday of Jan 2026)
            (2, 16),  # Presidents Day
            (4, 3),   # Good Friday
            (5, 25),  # Memorial Day
            (6, 19),  # Juneteenth
            (7, 3),   # Independence Day observed
            (9, 7),   # Labor Day
            (11, 26), # Thanksgiving
            (12, 25), # Christmas
        ]
        is_holiday = (now_et.month, now_et.day) in us_holidays_2026
        market_open = is_weekday and is_market_hours and not is_holiday
        
        # Check cache
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            try:
                cache_ts = cache_entry['timestamp']
                if cache_ts.tzinfo is not None:
                    cache_ts = cache_ts.replace(tzinfo=None)
                age = (datetime.now() - cache_ts).total_seconds()
            except:
                age = 999  # Force refresh on error
            if age < 60:  # 1 minute cache for real-time detection
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age),
                    'market_open': market_open
                })
        
        # Start background scan
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print("🔄 Volume spike scanner starting...")
                    
                    results = []
                    all_movers = []  # Fallback list for when no volume spikes found
                    # Expanded watchlist - high-growth stocks, small caps, and volatile names
                    # Using list(set()) to remove duplicates
                    watchlist = list(set([
                        # High-growth small caps
                        'VERO', 'MARA', 'RIOT', 'IONQ', 'QUBT', 'RGTI', 'SOFI', 'HOOD', 
                        'LCID', 'RIVN', 'NIO', 'XPEV', 'LI',
                        'BLNK', 'CHPT', 'EVGO', 'OPEN', 'GLBE',
                        'ENVX', 'QS', 'LAZR', 'RKLB', 'LUNR', 'MAXN',
                        'PLTR', 'AI', 'BBAI', 'SOUN', 'LUNR', 'ASTS',
                        # Popular volatile stocks
                        'AMD', 'NVDA', 'TSLA', 'META', 'GOOGL', 'AMZN', 'NFLX', 'UBER',
                        'LYFT', 'SNAP', 'SPOT', 'ZM', 'DOCU', 'TWLO', 'SNOW', 'DDOG',
                        'NET', 'CRWD', 'ZS', 'OKTA', 'MDB', 'TEAM', 'COIN',
                        'ROKU', 'ABNB', 'DASH', 'SHOP', 'PINS', 'DKNG', 'PENN', 'BABA',
                        # Meme/Reddit stocks
                        'GME', 'AMC', 'BB', 'NOK',
                        # Crypto/blockchain
                        'MSTR', 'HUT', 'BITF', 'CLSK',
                        # EV sector
                        'JOBY',
                        # Biotech/pharma volatile
                        'MRNA', 'BNTX', 'CRIS', 'EDIT', 'CRSP'
                    ]))
                    
                    for ticker in watchlist:
                        try:
                            hist = cached_get_history(ticker, period='5d', interval='1d')
                            
                            # Skip symbols with insufficient data
                            if hist is None or hist.empty or len(hist) < 3:
                                if ticker not in KNOWN_DELISTED and not _is_rate_limited(ticker):
                                    print(f"[VolSpike] {ticker}: insufficient data")
                                continue
                            
                            # Calculate metrics
                            current_price = hist['Close'].iloc[-1]
                            
                            # Skip if price is invalid (0 or None)
                            if current_price is None or current_price <= 0:
                                print(f"[VolSpike] {ticker}: invalid price")
                                continue
                            prev_close = hist['Close'].iloc[-2]
                            price_change_pct = ((current_price - prev_close) / prev_close) * 100
                            
                            current_volume = hist['Volume'].iloc[-1]
                            avg_volume = hist['Volume'].iloc[:-1].mean()
                            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
                            
                            # Collect ALL stocks with valid data for fallback
                            try:
                                info = cached_get_ticker_info(ticker)
                                if not info or info.get('regularMarketPrice') is None:
                                    continue
                            except:
                                continue
                            
                            stock_data = {
                                'symbol': ticker,
                                'price': float(current_price),
                                'price_change_pct': float(price_change_pct),
                                'volume': int(current_volume),
                                'avg_volume': int(avg_volume),
                                'volume_ratio': float(volume_ratio),
                                'market_cap': info.get('marketCap', 0),
                                'company_name': info.get('shortName', ticker),
                                'sector': info.get('sector', 'Unknown'),
                                'direction': 'BULLISH' if price_change_pct > 0 else 'BEARISH'
                            }
                            
                            # Check if qualifies for volume spike (strict criteria)
                            if volume_ratio >= 1.5 and abs(price_change_pct) >= 3.0:
                                results.append(stock_data)
                            # Also track all movers with significant price change (fallback)
                            elif abs(price_change_pct) >= 2.0:
                                all_movers.append(stock_data)
                                
                        except Exception as e:
                            print(f"Error scanning {ticker}: {e}")
                            continue
                    
                    # If no volume spikes found, use top movers by price change as fallback
                    if len(results) == 0 and len(all_movers) > 0:
                        print("📊 No volume spikes found, showing top price movers instead")
                        # Sort by absolute price change
                        all_movers.sort(key=lambda x: abs(x['price_change_pct']), reverse=True)
                        results = all_movers[:15]  # Top 15 movers
                    
                    # Sort by direction (BULLISH first), then volume ratio (highest spikes first)
                    results.sort(key=lambda x: (0 if x['direction'] == 'BULLISH' else 1, -abs(x['price_change_pct'])))
                    results = results[:20]  # Top 20
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ Volume spike scanner complete! Found {len(results)} stocks")
                    
                except Exception as e:
                    print(f"❌ Volume spike scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'Scanner running in background. Refresh in 30 seconds.'
            })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# ETF SCANNER - Stocks, Commodities, Crypto ETFs
# ============================================================================

# ETF Universe categorized by type
ETF_UNIVERSE = {
    'Stock Index ETFs': {
        'SPY': 'S&P 500',
        'QQQ': 'NASDAQ 100',
        'DIA': 'Dow Jones',
        'IWM': 'Russell 2000',
        'VTI': 'Total Stock Market',
        'VOO': 'Vanguard S&P 500',
        'VUG': 'Vanguard Growth',
        'VTV': 'Vanguard Value',
        'ARKK': 'ARK Innovation',
        'ARKG': 'ARK Genomics',
        'ARKW': 'ARK Internet',
        'ARKF': 'ARK Fintech'
    },
    'Sector ETFs': {
        'XLK': 'Technology',
        'XLF': 'Financials',
        'XLV': 'Healthcare',
        'XLE': 'Energy',
        'XLI': 'Industrials',
        'XLP': 'Consumer Staples',
        'XLY': 'Consumer Discretionary',
        'XLB': 'Materials',
        'XLRE': 'Real Estate',
        'XLC': 'Communication',
        'XLU': 'Utilities',
        'SMH': 'Semiconductors',
        'XBI': 'Biotech',
        'KRE': 'Regional Banks'
    },
    'Commodity ETFs': {
        'GLD': 'Gold',
        'SLV': 'Silver',
        'USO': 'Oil',
        'UNG': 'Natural Gas',
        'DBC': 'Commodities Basket',
        'DBA': 'Agriculture',
        'PDBC': 'Diversified Commodities',
        'CORN': 'Corn',
        'WEAT': 'Wheat',
        'SOYB': 'Soybeans',
        'CPER': 'Copper',
        'URA': 'Uranium'
    },
    'Crypto ETFs': {
        'BITO': 'Bitcoin Futures',
        'GBTC': 'Grayscale Bitcoin',
        'ETHE': 'Grayscale Ethereum',
        'BLOK': 'Blockchain Companies',
        'BTCW': 'Bitcoin Strategy',
        'BITQ': 'Crypto Industry',
        'IBIT': 'iShares Bitcoin',
        'FBTC': 'Fidelity Bitcoin',
        'ARKB': 'ARK Bitcoin'
    }
}

@scanners_bp.route('/api/scanner/etf-scanner')
def etf_scanner():
    """Scan ETFs across Stocks, Commodities, and Crypto categories"""
    try:
        cache_key = 'etf-scanner'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})
        
        # Check cache
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            try:
                cache_ts = cache_entry['timestamp']
                if cache_ts.tzinfo is not None:
                    cache_ts = cache_ts.replace(tzinfo=None)
                age = (datetime.now() - cache_ts).total_seconds()
            except:
                age = 999
            if age < scanner_cache_timeout:
                return jsonify({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'stocks': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })
        
        # Start background scan
        if not cache_entry['running']:
            def run_scan():
                try:
                    scanner_cache[cache_key]['running'] = True
                    print("🔄 ETF Scanner starting...")
                    
                    results = []
                    
                    for category, etfs in ETF_UNIVERSE.items():
                        for symbol, name in etfs.items():
                            try:
                                hist = cached_get_history(symbol, period='3mo', interval='1d')
                                
                                if hist is None or hist.empty or len(hist) < 20:
                                    continue
                                
                                current_price = hist['Close'].iloc[-1]
                                prev_close = hist['Close'].iloc[-2]
                                price_change = current_price - prev_close
                                price_change_pct = (price_change / prev_close) * 100
                                
                                # Calculate indicators
                                hist['SMA20'] = hist['Close'].rolling(20).mean()
                                hist['SMA50'] = hist['Close'].rolling(50).mean() if len(hist) >= 50 else hist['Close'].rolling(20).mean()
                                hist['EMA9'] = hist['Close'].ewm(span=9, adjust=False).mean()
                                hist['EMA21'] = hist['Close'].ewm(span=21, adjust=False).mean()
                                
                                # RSI
                                delta = hist['Close'].diff()
                                gain = delta.where(delta > 0, 0).rolling(14).mean()
                                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                                rs = gain / loss
                                hist['RSI'] = 100 - (100 / (1 + rs))
                                
                                # ATR
                                hist['ATR'] = (hist['High'] - hist['Low']).rolling(14).mean()
                                
                                # Volume analysis
                                avg_volume = hist['Volume'].rolling(20).mean().iloc[-1]
                                current_volume = hist['Volume'].iloc[-1]
                                volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
                                
                                # Get latest values
                                sma20 = hist['SMA20'].iloc[-1]
                                sma50 = hist['SMA50'].iloc[-1]
                                ema9 = hist['EMA9'].iloc[-1]
                                ema21 = hist['EMA21'].iloc[-1]
                                rsi = hist['RSI'].iloc[-1]
                                atr = hist['ATR'].iloc[-1]
                                
                                # 52-week high/low
                                high_52w = hist['High'].max()
                                low_52w = hist['Low'].min()
                                pct_from_high = ((high_52w - current_price) / high_52w) * 100
                                pct_from_low = ((current_price - low_52w) / low_52w) * 100
                                
                                # Score calculation (0-15)
                                score = 0
                                signals = []
                                
                                # Trend signals
                                if current_price > sma20:
                                    score += 2
                                    signals.append("Above SMA20")
                                if current_price > sma50:
                                    score += 2
                                    signals.append("Above SMA50")
                                if ema9 > ema21:
                                    score += 2
                                    signals.append("EMA9 > EMA21 (Bullish)")
                                
                                # RSI signals
                                if 40 <= rsi <= 60:
                                    score += 2
                                    signals.append("RSI Neutral")
                                elif rsi < 30:
                                    score += 3
                                    signals.append("RSI Oversold (<30)")
                                elif rsi > 70:
                                    score -= 1
                                    signals.append("RSI Overbought (>70)")
                                
                                # Volume signal
                                if volume_ratio > 1.5:
                                    score += 2
                                    signals.append(f"High Volume ({volume_ratio:.1f}x)")
                                
                                # Momentum
                                if price_change_pct > 1:
                                    score += 2
                                    signals.append(f"Strong Momentum (+{price_change_pct:.1f}%)")
                                elif price_change_pct < -1:
                                    score -= 1
                                
                                # Near 52-week high/low
                                if pct_from_high < 5:
                                    score += 1
                                    signals.append("Near 52W High")
                                if pct_from_low < 10:
                                    score += 1
                                    signals.append("Near 52W Low (Potential Bounce)")
                                
                                # Determine direction
                                if score >= 8:
                                    direction = 'BULLISH'
                                elif score <= 3:
                                    direction = 'BEARISH'
                                else:
                                    direction = 'NEUTRAL'
                                
                                results.append({
                                    'symbol': symbol,
                                    'name': f"{name} ({category})",
                                    'category': category,
                                    'price': round(current_price, 2),
                                    'change': round(price_change, 2),
                                    'change_pct': round(price_change_pct, 2),
                                    'score': max(0, min(15, score)),
                                    'direction': direction,
                                    'rsi': round(rsi, 1) if not np.isnan(rsi) else None,
                                    'sma20': round(sma20, 2),
                                    'sma50': round(sma50, 2),
                                    'ema9': round(ema9, 2),
                                    'ema21': round(ema21, 2),
                                    'atr': round(atr, 2),
                                    'volume': int(current_volume),
                                    'volume_ratio': round(volume_ratio, 2),
                                    'high_52w': round(high_52w, 2),
                                    'low_52w': round(low_52w, 2),
                                    'pct_from_high': round(pct_from_high, 1),
                                    'pct_from_low': round(pct_from_low, 1),
                                    'signals': signals
                                })
                                
                                # Small delay to avoid rate limiting
                                import time
                                time.sleep(0.1)
                                
                            except Exception as e:
                                print(f"⚠️ Error scanning {symbol}: {e}")
                                continue
                    
                    # Sort by score descending
                    results.sort(key=lambda x: (-x['score'], x['symbol']))
                    
                    # Clean NaN values
                    results = clean_nan_values(results)
                    
                    scanner_cache[cache_key]['data'] = results
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                    print(f"✅ ETF Scanner complete! Found {len(results)} ETFs")
                    
                except Exception as e:
                    print(f"❌ ETF Scanner error: {e}")
                    scanner_cache[cache_key]['data'] = []
                    scanner_cache[cache_key]['timestamp'] = datetime.now()
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_scan, daemon=True).start()
        
        # Return cached or placeholder
        if cache_entry['data'] is not None:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': cache_entry['data'],
                'cached': True
            })
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'stocks': [],
                'scanning': True,
                'message': 'ETF Scanner running in background. Refresh in 30 seconds.'
            })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def generate_trade_recommendation(data, support_levels, resistance_levels):
    """Generate buy/sell recommendation with targets based on trend analysis"""
    current_price = data['price']
    rsi = data.get('rsi', 50)
    ema9 = data.get('ema9', current_price)
    ema21 = data.get('ema21', current_price)
    sma20 = data.get('sma20', current_price)
    sma50 = data.get('sma50', current_price)
    atr = data.get('atr', current_price * 0.02)
    
    # Determine trend
    bullish_signals = 0
    bearish_signals = 0
    
    # EMA trend
    if ema9 > ema21:
        bullish_signals += 1
    else:
        bearish_signals += 1
    
    # Price vs SMAs
    if current_price > sma20:
        bullish_signals += 1
    else:
        bearish_signals += 1
        
    if current_price > sma50:
        bullish_signals += 1
    else:
        bearish_signals += 1
    
    # RSI momentum
    if 40 <= rsi <= 60:
        bullish_signals += 1
    elif rsi < 30:
        bullish_signals += 1  # Oversold bounce
    elif rsi > 70:
        bearish_signals += 1  # Overbought
    
    # Generate recommendation
    recommendation = {}
    
    if bullish_signals >= 3:
        # BUY recommendation
        recommendation['recommendation'] = 'BUY'
        recommendation['recommendation_color'] = 'green'
        recommendation['entry_price'] = current_price
        
        # Stop Loss: Tight stop below entry (1.5% or 1.5*ATR, whichever is tighter)
        # For BUY, we want to limit loss if price goes DOWN against us
        atr_based_stop = current_price - (atr * 1.5)
        percentage_based_stop = current_price * 0.985  # 1.5% below entry
        recommendation['stop_loss'] = float(max(atr_based_stop, percentage_based_stop))
        
        # Targets: Only resistances ABOVE current price (going UP)
        # Must be at least 1% above to be valid targets
        resistances_above = [r for r in resistance_levels if r > current_price * 1.01]
        
        if len(resistances_above) >= 3:
            # Sort ascending to get nearest first
            resistances_above_sorted = sorted(resistances_above)
            recommendation['target_1'] = float(resistances_above_sorted[0])  # Nearest
            recommendation['target_2'] = float(resistances_above_sorted[1])  # Middle
            recommendation['target_3'] = float(resistances_above_sorted[2])  # Furthest
        elif len(resistances_above) >= 2:
            resistances_above_sorted = sorted(resistances_above)
            recommendation['target_1'] = float(resistances_above_sorted[0])
            recommendation['target_2'] = float(resistances_above_sorted[1])
            recommendation['target_3'] = float(current_price * 1.12)  # Fallback
        elif len(resistances_above) >= 1:
            recommendation['target_1'] = float(resistances_above[0])
            recommendation['target_2'] = float(current_price * 1.08)  # Fallback
            recommendation['target_3'] = float(current_price * 1.12)  # Fallback
        else:
            # No resistances above - use percentage targets
            recommendation['target_1'] = float(current_price * 1.05)
            recommendation['target_2'] = float(current_price * 1.08)
            recommendation['target_3'] = float(current_price * 1.12)
        
        # Risk/Reward ratios
        risk = current_price - recommendation['stop_loss']
        reward_1 = recommendation['target_1'] - current_price
        recommendation['risk_reward_1'] = round(reward_1 / risk, 2) if risk > 0 else 0
        
        stop_pct = ((current_price - recommendation['stop_loss']) / current_price) * 100
        recommendation['recommendation_reason'] = f"Bullish trend ({bullish_signals}/4 signals). Tight stop at {stop_pct:.1f}% below entry"
        
    elif bearish_signals >= 3:
        # SELL recommendation
        recommendation['recommendation'] = 'SELL'
        recommendation['recommendation_color'] = 'red'
        recommendation['entry_price'] = current_price
        
        # Stop Loss: Tight stop above entry (1.5% or 1.5*ATR, whichever is tighter)
        # For SELL, we want to limit loss if price goes UP against us
        atr_based_stop = current_price + (atr * 1.5)
        percentage_based_stop = current_price * 1.015  # 1.5% above entry
        recommendation['stop_loss'] = float(min(atr_based_stop, percentage_based_stop))
        
        # Targets: Only supports BELOW current price (going DOWN)
        # Must be at least 1% below to be valid targets
        supports_below = [s for s in support_levels if s < current_price * 0.99]
        
        if len(supports_below) >= 3:
            # Sort descending to get nearest first
            supports_below_sorted = sorted(supports_below, reverse=True)
            recommendation['target_1'] = float(supports_below_sorted[0])  # Nearest (highest support below)
            recommendation['target_2'] = float(supports_below_sorted[1])  # Middle
            recommendation['target_3'] = float(supports_below_sorted[2])  # Furthest (lowest support)
        elif len(supports_below) >= 2:
            supports_below_sorted = sorted(supports_below, reverse=True)
            recommendation['target_1'] = float(supports_below_sorted[0])
            recommendation['target_2'] = float(supports_below_sorted[1])
            recommendation['target_3'] = float(current_price * 0.88)  # Fallback
        elif len(supports_below) >= 1:
            recommendation['target_1'] = float(supports_below[0])
            recommendation['target_2'] = float(current_price * 0.92)  # Fallback
            recommendation['target_3'] = float(current_price * 0.88)  # Fallback
        else:
            # No supports below - use percentage targets
            recommendation['target_1'] = float(current_price * 0.95)
            recommendation['target_2'] = float(current_price * 0.92)
            recommendation['target_3'] = float(current_price * 0.88)
        
        # Risk/Reward ratios
        risk = recommendation['stop_loss'] - current_price
        reward_1 = current_price - recommendation['target_1']
        recommendation['risk_reward_1'] = round(reward_1 / risk, 2) if risk > 0 else 0
        
        stop_pct = ((recommendation['stop_loss'] - current_price) / current_price) * 100
        recommendation['recommendation_reason'] = f"Bearish trend ({bearish_signals}/4 signals). Tight stop at {stop_pct:.1f}% above entry"
        
    else:
        # HOLD - No clear trend
        recommendation['recommendation'] = 'HOLD'
        recommendation['recommendation_color'] = 'gray'
        recommendation['recommendation_reason'] = f"Mixed signals (Bullish: {bullish_signals}, Bearish: {bearish_signals}). Wait for clearer trend"
        recommendation['entry_price'] = None
        recommendation['stop_loss'] = None
        recommendation['target_1'] = None
        recommendation['target_2'] = None
        recommendation['target_3'] = None
        recommendation['risk_reward_1'] = None
    
    return recommendation

@scanners_bp.route('/api/scanner/custom-analyzer', methods=['POST'])
def custom_analyzer():
    """Analyze custom stock list with support/resistance levels"""
    try:
        data = request.get_json()
        symbols = data.get('symbols', [])
        if not symbols:
            return jsonify({'success': False, 'error': 'No symbols provided'}), 400
        # Resolve each symbol or name to a ticker symbol
        resolved_symbols = []
        for s in symbols[:20]:
            resolved = resolve_symbol_or_name(s)
            if resolved:
                resolved_symbols.append(resolved)
            else:
                print(f"[custom_analyzer] Could not resolve '{s}' to a valid symbol.")
        if not resolved_symbols:
            return jsonify({'success': False, 'error': 'No valid symbols found'}), 400
        # Use unified system for analysis
        system = UnifiedTradingSystem()
        results = []
        
        def analyze_with_support_resistance(symbol):
            """Analyze stock and calculate support/resistance"""
            try:
                result = system.get_live_data(symbol)
                if not result:
                    return {
                        'symbol': symbol,
                        'error': True,
                        'error_message': f'⚠️ {symbol} appears to be delisted or has no available market data. Please verify the ticker symbol.'
                    }
                
                # Calculate score using correct method
                score, signals = system.score_asset(result, 'STOCK')
                result['score'] = score
                result['signals'] = signals
                
                # Calculate support and resistance levels
                hist = cached_get_history(symbol, period='3mo', interval='1d')
                
                if len(hist) >= 20:
                    current_price = result['price']
                    
                    # Support levels - only use lows that are BELOW current price
                    # Find supports within reasonable range (not more than 20% below)
                    all_lows = hist['Low'].tail(60)
                    valid_supports = all_lows[(all_lows < current_price * 0.99) & (all_lows > current_price * 0.80)]
                    
                    if len(valid_supports) >= 3:
                        support_levels = sorted(valid_supports.nsmallest(5).unique())[:3]
                    else:
                        # Use percentage-based supports if not enough historical levels
                        support_levels = [current_price * 0.95, current_price * 0.92, current_price * 0.88]
                    
                    # Resistance levels - only use highs that are ABOVE current price
                    # Find resistances within reasonable range (not more than 20% above)
                    all_highs = hist['High'].tail(60)
                    valid_resistances = all_highs[(all_highs > current_price * 1.01) & (all_highs < current_price * 1.20)]
                    
                    if len(valid_resistances) >= 3:
                        resistance_levels = sorted(valid_resistances.nlargest(5).unique(), reverse=True)[:3]
                    else:
                        # Use percentage-based resistances if not enough historical levels
                        resistance_levels = [current_price * 1.12, current_price * 1.08, current_price * 1.05]
                    
                    result['support_levels'] = [float(s) for s in support_levels]
                    result['resistance_levels'] = [float(r) for r in resistance_levels]
                    
                    # Find nearest support/resistance
                    supports_below = [s for s in support_levels if s < current_price]
                    resistances_above = [r for r in resistance_levels if r > current_price]
                    
                    result['nearest_support'] = float(supports_below[-1]) if supports_below else None
                    result['nearest_resistance'] = float(resistances_above[0]) if resistances_above else None
                    
                    # Calculate distance to nearest levels
                    if result['nearest_support']:
                        result['support_distance_pct'] = ((current_price - result['nearest_support']) / current_price) * 100
                    if result['nearest_resistance']:
                        result['resistance_distance_pct'] = ((result['nearest_resistance'] - current_price) / current_price) * 100
                    
                    # Generate Buy/Sell recommendation based on trend
                    recommendation = generate_trade_recommendation(result, support_levels, resistance_levels)
                    result.update(recommendation)
                else:
                    result['support_levels'] = []
                    result['resistance_levels'] = []
                    result['nearest_support'] = None
                    result['nearest_resistance'] = None
                    result['recommendation'] = 'HOLD'
                    result['recommendation_reason'] = 'Insufficient data'
                
                return result
            except Exception as e:
                print(f"Error analyzing {symbol}: {e}")
                # Check if it's a delisted stock error
                error_msg = str(e).lower()
                if 'delisted' in error_msg or 'no data found' in error_msg:
                    return {
                        'symbol': symbol,
                        'error': True,
                        'error_message': f'⚠️ {symbol} appears to be delisted or has no available market data. Please verify the ticker symbol.'
                    }
                return None
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_symbol = {executor.submit(analyze_with_support_resistance, sym): sym for sym in resolved_symbols}
            for future in as_completed(future_to_symbol):
                try:
                    result = future.result()
                    if result:
                        # Clean NaN values before adding to results
                        cleaned_result = clean_nan_values(result)
                        results.append(cleaned_result)
                except Exception as e:
                    print(f"Error processing future: {e}")
                    continue
        
        # Sort by score
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'stocks': results,
            'count': len(results)
        })
            
    except Exception as e:
        print(f"Custom analyzer error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

