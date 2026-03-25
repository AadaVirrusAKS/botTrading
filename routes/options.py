"""
Options Routes - Options analysis, swing trades, PCR.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import time
import re
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
import numpy as np
import pandas as pd

from services.utils import clean_nan_values, SECTOR_ETFS
from services.symbols import resolve_symbol_or_name
from config.master_stock_list import (
    OPTIONS_ELIGIBLE_STOCKS, OPTIONS_ELIGIBLE_ETFS, get_options_eligible,
)
from services.market_data import (
    cached_get_history, cached_get_option_dates, cached_get_option_chain,
    cached_get_ticker_info, cached_get_price, _log_fetch_event, _is_rate_limited,
    _is_rate_limit_error, _mark_rate_limited, _mark_global_rate_limit,
    _is_expected_no_data_error, scanner_cache, scanner_cache_timeout
)
from scanners.next_day_options_predictor import NextDayOptionsPredictor

options_bp = Blueprint("options", __name__)

@options_bp.route('/api/options/analysis')
def options_analysis():
    """Get options analysis with caching, custom symbol support, and expiry filters"""
    try:
        # Check for parameters
        custom_symbols = request.args.get('symbols', '')
        expiry_type = request.args.get('expiry', '0dte')  # 0dte, daily, weekly, monthly
        is_custom_search = bool(custom_symbols)
        
        if custom_symbols:
            # Parse custom symbols (allow name or symbol)
            raw_tickers = [s.strip() for s in custom_symbols.split(',') if s.strip()]
            tickers = []
            for s in raw_tickers:
                resolved = resolve_symbol_or_name(s)
                if resolved:
                    tickers.append(resolved)
            tickers = tickers[:20]
            cache_key = f'options-analysis-{expiry_type}-{"-".join(sorted(tickers))}'
        else:
            # Use default tickers - include 0DTE eligible stocks
            if expiry_type == '0dte':
                tickers = get_options_eligible(include_etfs=True)
            else:
                tickers = list(OPTIONS_ELIGIBLE_STOCKS)
            cache_key = f'options-analysis-{expiry_type}'
        
        # Initialize cache entry if needed
        if cache_key not in scanner_cache:
            scanner_cache[cache_key] = {'data': None, 'timestamp': None, 'running': False, 'run_started': None}
        
        cache_entry = scanner_cache[cache_key]
        
        # Reset stuck running flag (if running for more than 5 minutes, assume crashed)
        if cache_entry.get('running') and cache_entry.get('run_started'):
            run_age = (datetime.now() - cache_entry['run_started']).total_seconds()
            if run_age > 300:  # 5 minutes max
                print(f"⚠️ Options scan '{cache_key}' stuck for {int(run_age)}s — resetting running flag")
                cache_entry['running'] = False
        
        # Check if we have valid cached data (don't serve empty results as valid)
        if cache_entry['data'] is not None and cache_entry['timestamp'] is not None:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            has_results = len(cache_entry['data']) > 0 or len(cache_entry.get('weak_signals', [])) > 0
            if age < scanner_cache_timeout and has_results:
                return jsonify(clean_nan_values({
                    'success': True,
                    'timestamp': datetime.now().isoformat(),
                    'predictions': cache_entry['data'],
                    'weak_signals': cache_entry.get('weak_signals', []),
                    'cached': True,
                    'age_seconds': int(age),
                    'expiry_type': expiry_type
                }))
        
        # Start background analysis if not already running
        if not cache_entry['running']:
            def run_analysis():
                try:
                    scanner_cache[cache_key]['running'] = True
                    scanner_cache[cache_key]['run_started'] = datetime.now()
                    print(f"🔄 Starting options analysis ({expiry_type}) for {len(tickers)} symbols...")
                    predictor = NextDayOptionsPredictor(real_time_mode=False)
                    
                    predictions = []
                    weak_signals = []
                    
                    for ticker in tickers:
                        try:
                            # Use verbose=False to suppress console output
                            result = predictor.analyze_stock(ticker, verbose=False, expiry_type=expiry_type)
                            if result:
                                result['expiry_type'] = expiry_type  # Tag with expiry type
                                if result.get('is_weak_signal', False):
                                    weak_signals.append(result)
                                else:
                                    predictions.append(result)
                        except Exception as e:
                            # Log the error but continue
                            print(f"❌ Error analyzing {ticker}: {str(e)}")
                            continue
                    
                    # Sort by signal strength
                    predictions.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
                    weak_signals.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
                    
                    # Only cache if we got actual results (don't cache empty due to rate limiting)
                    if predictions or weak_signals:
                        scanner_cache[cache_key]['data'] = predictions
                        scanner_cache[cache_key]['expiry_type'] = expiry_type
                        scanner_cache[cache_key]['weak_signals'] = weak_signals
                        scanner_cache[cache_key]['timestamp'] = datetime.now()
                        print(f"✅ Options analysis complete! Found {len(predictions)} strong signals, {len(weak_signals)} weak signals")
                    else:
                        print(f"⚠️ Options analysis found 0 results (possible rate limiting) — not caching empty results")
                except Exception as e:
                    print(f"❌ Options analysis error: {e}")
                finally:
                    scanner_cache[cache_key]['running'] = False
            
            threading.Thread(target=run_analysis, daemon=True).start()
        
        # Return cached data or placeholder
        if cache_entry['data'] is not None:
            return jsonify(clean_nan_values({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'predictions': cache_entry['data'],
                'weak_signals': cache_entry.get('weak_signals', []),
                'cached': True,
                'expiry_type': expiry_type
            }))
        else:
            return jsonify({
                'success': True,
                'timestamp': datetime.now().isoformat(),
                'predictions': [],
                'weak_signals': [],
                'scanning': True,
                'message': f'Analyzing {len(tickers)} stocks. Please wait 1-2 minutes...',
                'expiry_type': expiry_type
            })
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@options_bp.route('/api/options/refresh', methods=['POST'])
def refresh_options():
    """Force refresh options data (clear cache and re-analyze with live premiums)"""
    try:
        # Clear all options cache
        keys_to_clear = [k for k in scanner_cache.keys() if k.startswith('options-analysis')]
        for key in keys_to_clear:
            scanner_cache[key] = {'data': None, 'timestamp': None, 'running': False}
        
        return jsonify({
            'success': True,
            'message': f'Cleared {len(keys_to_clear)} cache entries. Refresh to get new data with live premiums.',
            'cleared_keys': keys_to_clear
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@options_bp.route('/api/options/pcr')
def options_pcr():
    """Get Put/Call Ratio for major stocks and ETFs - calculated from live options data"""
    try:
        # Define symbols to analyze - options-eligible stocks and ETFs
        stock_symbols = list(OPTIONS_ELIGIBLE_STOCKS)[:30]  # Top 30 for PCR
        etf_symbols = list(OPTIONS_ELIGIBLE_ETFS)[:10]
        
        def calculate_pcr(symbol):
            """Calculate Put/Call Ratio from options chain"""
            try:
                # Get options expiration dates (live)
                expirations = cached_get_option_dates(symbol, force_live=True)
                if not expirations or len(expirations) == 0:
                    return None
                
                # Use first expiration (nearest term)
                expiry = expirations[0]
                
                # Get options chain (live - bypass cache for fresh premiums)
                opt_chain = cached_get_option_chain(symbol, expiry, use_cache=False)
                if opt_chain is None:
                    return None
                calls = opt_chain.calls
                puts = opt_chain.puts
                
                # Calculate total volume for puts and calls
                put_volume = puts['volume'].fillna(0).sum()
                call_volume = calls['volume'].fillna(0).sum()
                
                # Calculate PCR (avoid division by zero)
                if call_volume > 0:
                    pcr = put_volume / call_volume
                else:
                    pcr = 0
                
                # Determine sentiment
                if pcr < 0.7:
                    sentiment = 'Bullish'
                elif pcr > 1.0:
                    sentiment = 'Bearish'
                else:
                    sentiment = 'Neutral'
                
                total_volume = int(put_volume + call_volume)
                
                return {
                    'symbol': symbol,
                    'pcr': round(pcr, 2),
                    'sentiment': sentiment,
                    'volume': total_volume,
                    'put_volume': int(put_volume),
                    'call_volume': int(call_volume)
                }
                
            except Exception as e:
                print(f"Error calculating PCR for {symbol}: {e}")
                return None
        
        # Calculate PCR for stocks
        stock_pcr = []
        for symbol in stock_symbols:
            result = calculate_pcr(symbol)
            if result:
                stock_pcr.append(result)
        
        # Calculate PCR for ETFs
        etf_pcr = []
        for symbol in etf_symbols:
            result = calculate_pcr(symbol)
            if result:
                etf_pcr.append(result)
        
        # Sort by volume (highest first)
        stock_pcr.sort(key=lambda x: x['volume'], reverse=True)
        etf_pcr.sort(key=lambda x: x['volume'], reverse=True)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'stock_pcr': stock_pcr[:5],  # Top 5
            'etf_pcr': etf_pcr[:5]       # Top 5
        })
    except Exception as e:
        print(f"Error in options_pcr: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@options_bp.route('/api/options/swing')
def options_swing_trade():
    """Get swing trade options with premium filters and expiry types"""
    try:
        # Get filter parameters
        max_premium = float(request.args.get('max_premium', 30))
        expiry_type = request.args.get('expiry', 'weekly')  # weekly, monthly
        custom_symbols = request.args.get('symbols', '')
        
        # Premium tiers - each tier shows options AT that price level
        # $10: Show ATM options (lower premium = further OTM)
        # $20-50: Show options closer to money (higher premium)
        min_premium = 0.50  # Minimum across all tiers
        
        # Define default symbols
        if custom_symbols:
            symbols = [s.strip().upper() for s in custom_symbols.split(',') if s.strip()][:15]
        else:
            symbols = get_options_eligible(include_etfs=True)
        
        results = []
        
        for symbol in symbols:
            try:
                # Get current stock price (live - bypass cache for fresh data)
                info = cached_get_ticker_info(symbol, force_live=True)
                current_price = (info.get('preMarketPrice') or 
                               info.get('postMarketPrice') or 
                               info.get('currentPrice') or 
                               info.get('regularMarketPrice') or 
                               info.get('previousClose', 0))
                
                if not current_price or current_price <= 0:
                    continue
                
                # Get expiration dates (live)
                expirations = cached_get_option_dates(symbol, force_live=True)
                if not expirations:
                    continue
                
                # Filter expirations based on type
                from datetime import datetime, timedelta
                today = datetime.now().date()
                
                valid_expirations = []
                for exp_str in expirations:
                    try:
                        exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
                        days_to_exp = (exp_date - today).days
                        
                        if expiry_type == 'weekly' and 2 <= days_to_exp <= 10:
                            valid_expirations.append((exp_str, days_to_exp))
                        elif expiry_type == 'monthly' and 15 <= days_to_exp <= 45:
                            valid_expirations.append((exp_str, days_to_exp))
                    except:
                        continue
                
                if not valid_expirations:
                    # Fallback to nearest available
                    for exp_str in expirations[:3]:
                        try:
                            exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
                            days_to_exp = (exp_date - today).days
                            if days_to_exp > 0:
                                valid_expirations.append((exp_str, days_to_exp))
                        except:
                            continue
                
                if not valid_expirations:
                    continue
                
                # Use the first valid expiration
                target_expiry, dte = valid_expirations[0]
                
                # Get options chain (live - bypass cache for fresh premiums)
                opt_chain = cached_get_option_chain(symbol, target_expiry, use_cache=False)
                if opt_chain is None:
                    continue
                calls = opt_chain.calls.copy()
                puts = opt_chain.puts.copy()
                
                # Calculate mid-price for live pricing (bid+ask)/2, fallback to lastPrice
                calls['mid_price'] = calls.apply(
                    lambda r: (r['bid'] + r['ask']) / 2 if r['bid'] > 0 and r['ask'] > 0 else r['lastPrice'], 
                    axis=1
                )
                puts['mid_price'] = puts.apply(
                    lambda r: (r['bid'] + r['ask']) / 2 if r['bid'] > 0 and r['ask'] > 0 else r['lastPrice'], 
                    axis=1
                )
                
                # Get historical data for scoring (live)
                hist = cached_get_history(symbol, period='1mo', interval='1d', force_live=True)
                if hist is not None and len(hist) >= 5:
                    recent_change = (hist['Close'].iloc[-1] / hist['Close'].iloc[-5] - 1) * 100
                else:
                    recent_change = 0
                
                # Filter calls - OTM (strike > current price) using mid-price for filtering
                otm_calls = calls[calls['strike'] >= current_price * 1.01]
                otm_calls = otm_calls[otm_calls['mid_price'] >= min_premium]
                otm_calls = otm_calls[otm_calls['mid_price'] <= max_premium]
                otm_calls = otm_calls[otm_calls['volume'].fillna(0) > 0]
                
                if len(otm_calls) > 0:
                    # For different premium tiers, select different strikes
                    # Higher premium = closer to money (first rows), Lower premium = further OTM
                    if max_premium <= 10:
                        # Cheapest tier - show furthest OTM (last rows)
                        idx = min(len(otm_calls) - 1, len(otm_calls) // 2 + 1)
                    elif max_premium <= 20:
                        idx = min(len(otm_calls) - 1, len(otm_calls) // 3)
                    elif max_premium <= 30:
                        idx = min(len(otm_calls) - 1, len(otm_calls) // 4)
                    elif max_premium <= 40:
                        idx = min(len(otm_calls) - 1, 1)
                    else:  # $50 tier - closest to money
                        idx = 0
                    
                    best_call = otm_calls.iloc[idx]
                    score = 7 + min(4, max(-2, recent_change))
                    
                    # Use mid-price (bid+ask)/2 for live pricing, fallback to lastPrice
                    bid = float(best_call.get('bid', 0) or 0)
                    ask = float(best_call.get('ask', 0) or 0)
                    if bid > 0 and ask > 0:
                        live_premium = round((bid + ask) / 2, 2)
                    else:
                        live_premium = float(best_call['lastPrice'])
                    
                    results.append({
                        'symbol': symbol,
                        'type': 'CALL',
                        'stock_price': round(current_price, 2),
                        'strike': float(best_call['strike']),
                        'premium': live_premium,
                        'bid': bid,
                        'ask': ask,
                        'total_premium': round(live_premium * 100, 2),  # 1 contract = 100 shares
                        'expiry': target_expiry,
                        'dte': dte,
                        'iv': float(best_call.get('impliedVolatility', 0)),
                        'volume': int(best_call.get('volume', 0) or 0),
                        'open_interest': int(best_call.get('openInterest', 0) or 0),
                        'score': int(score)
                    })
                
                # Filter puts - OTM (strike < current price) using mid-price for filtering
                otm_puts = puts[puts['strike'] <= current_price * 0.99]
                otm_puts = otm_puts[otm_puts['mid_price'] >= min_premium]
                otm_puts = otm_puts[otm_puts['mid_price'] <= max_premium]
                otm_puts = otm_puts[otm_puts['volume'].fillna(0) > 0]
                
                if len(otm_puts) > 0:
                    # For puts, higher strike = closer to money (more expensive)
                    if max_premium <= 10:
                        idx = len(otm_puts) // 2  # Further OTM  
                    elif max_premium <= 20:
                        idx = len(otm_puts) * 2 // 3
                    elif max_premium <= 30:
                        idx = len(otm_puts) * 3 // 4
                    elif max_premium <= 40:
                        idx = max(0, len(otm_puts) - 2)
                    else:  # $50 tier
                        idx = len(otm_puts) - 1  # Closest to money
                    
                    best_put = otm_puts.iloc[min(idx, len(otm_puts) - 1)]
                    score = 7 - min(4, max(-2, recent_change))
                    
                    # Use mid-price (bid+ask)/2 for live pricing, fallback to lastPrice
                    bid = float(best_put.get('bid', 0) or 0)
                    ask = float(best_put.get('ask', 0) or 0)
                    if bid > 0 and ask > 0:
                        live_premium = round((bid + ask) / 2, 2)
                    else:
                        live_premium = float(best_put['lastPrice'])
                    
                    results.append({
                        'symbol': symbol,
                        'type': 'PUT',
                        'stock_price': round(current_price, 2),
                        'strike': float(best_put['strike']),
                        'premium': live_premium,
                        'bid': bid,
                        'ask': ask,
                        'total_premium': round(live_premium * 100, 2),  # 1 contract = 100 shares
                        'expiry': target_expiry,
                        'dte': dte,
                        'iv': float(best_put.get('impliedVolatility', 0)),
                        'volume': int(best_put.get('volume', 0) or 0),
                        'open_interest': int(best_put.get('openInterest', 0) or 0),
                        'score': int(score)
                    })
                
                # Small delay to avoid rate limiting
                import time
                time.sleep(0.2)
                
            except Exception as e:
                print(f"⚠️ Error processing {symbol} for swing trade: {e}")
                continue
        
        # Sort by score descending
        results.sort(key=lambda x: (-x['score'], x['premium']))
        
        # Clean NaN values
        results = clean_nan_values(results)
        
        return jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'options': results,
            'filters': {
                'max_premium': max_premium,
                'expiry_type': expiry_type
            }
        })
        
    except Exception as e:
        print(f"Error in options_swing_trade: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


