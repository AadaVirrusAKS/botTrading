"""
AI Trading Routes - Bot control, auto-cycle, trade execution, intraday scanning.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import os
import time
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf
import numpy as np
import pandas as pd

from services.utils import clean_nan_values
from services.market_data import (
    cached_batch_prices, cached_get_price, cached_get_history,
    cached_get_option_dates, cached_get_option_chain, cached_get_ticker_info,
    fetch_quote_api_batch, scanner_cache, scanner_cache_timeout,
    _is_rate_limited, _log_fetch_event,
    _is_rate_limit_error, _is_expected_no_data_error, _mark_rate_limited,
    _mark_global_rate_limit, clear_rate_limit_blocks
)
from services.bot_engine import (
    bot_state, BOT_STATE_FILE, BOT_STATE_LOCK,
    AUTO_TRADE_DEDUP_LOCK, AUTO_TRADE_EXECUTION_GUARD, AUTO_TRADE_DEDUP_SECONDS,
    recalculate_balance, generate_daily_trade_analysis, reconcile_orphan_positions,
    load_bot_state, save_bot_state,
    calculate_technical_indicators, analyze_for_strategy,
    add_or_update_position, update_positions_with_live_prices,
    get_live_option_premium, is_zero_dte_or_expired,
    get_min_option_dte_days, get_option_dte, is_option_expiry_blocked,
    refresh_signal_entries_with_live_prices, WATCHLISTS
)
from services.symbols import is_valid_symbol_cached, filter_valid_symbols, resolve_symbol_or_name, KNOWN_DELISTED

ai_trading_bp = Blueprint("ai_trading", __name__)

@ai_trading_bp.route('/api/bot/status')
def bot_status():
    """Get bot status and account info"""
    # First, get the state data inside the lock
    with BOT_STATE_LOCK:
        load_bot_state()
        
        account_mode = bot_state['account_mode']
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        # Recalculate balance from trade history (authoritative source of truth)
        account['balance'] = recalculate_balance(account)
        
        # Make a copy of positions to update outside the lock
        positions = [pos.copy() for pos in account.get('positions', [])]
        
        # Copy other needed state
        state_copy = {
            'running': bot_state['running'],
            'auto_trade': bot_state.get('auto_trade', False),
            'account_mode': account_mode,
            'strategy': bot_state['strategy'],
            'settings': bot_state['settings'],
            'last_scan': bot_state['last_scan'],
            'signals': bot_state.get('signals', []),
            'trades': account.get('trades', []),
            'demo_account': bot_state['demo_account'].copy(),
            'daily_analysis': bot_state.get('daily_analysis')
        }
    
    # Update positions with live prices OUTSIDE the lock (force_live bypasses cache)
    force_live = request.args.get('force_live', '0').lower() in ('1', 'true', 'yes')
    if force_live:
        clear_rate_limit_blocks()
    positions = update_positions_with_live_prices(positions, force_live=force_live)
    signals = refresh_signal_entries_with_live_prices(state_copy.get('signals', []), force_refresh=force_live)

    # Persist refreshed signals to keep UI and auto-cycle state aligned
    with BOT_STATE_LOCK:
        load_bot_state()
        bot_state['signals'] = signals
        save_bot_state()

    # Persist refreshed position prices so subsequent polls keep latest known values
    with BOT_STATE_LOCK:
        load_bot_state()
        account_mode_latest = bot_state['account_mode']
        account_latest = bot_state['demo_account'] if account_mode_latest == 'demo' else bot_state['real_account']
        stored_positions = account_latest.get('positions', [])
        changed = False

        # Best-effort index-based sync (positions copy preserves order from source)
        for idx, updated_pos in enumerate(positions):
            if idx >= len(stored_positions):
                break
            stored_pos = stored_positions[idx]

            # Safety guard: only sync when these core identifiers match
            if (
                stored_pos.get('symbol') != updated_pos.get('symbol')
                or stored_pos.get('timestamp') != updated_pos.get('timestamp')
            ):
                continue

            for field in ('current_price', 'current_bid', 'current_ask', 'underlying_price', 'last_price_update', 'last_checked'):
                if field in updated_pos and stored_pos.get(field) != updated_pos.get(field):
                    stored_pos[field] = updated_pos.get(field)
                    changed = True

        if changed:
            save_bot_state()
    
    # Calculate total unrealized P&L
    total_pnl = 0
    for pos in positions:
        entry = pos.get('entry_price', 0)
        current = pos.get('current_price', entry)
        qty = pos.get('quantity', 0)
        is_option = pos.get('instrument_type') == 'option'
        multiplier = 100 if is_option else 1
        side_mult = 1 if pos.get('side') == 'LONG' else -1
        total_pnl += (current - entry) * qty * multiplier * side_mult
    
    response = jsonify({
        'success': True,
        'running': state_copy['running'],
        'auto_trade': state_copy['auto_trade'],
        'account_mode': state_copy['account_mode'],
        'strategy': state_copy['strategy'],
        'settings': state_copy['settings'],
        'last_scan': state_copy['last_scan'],
        'signals': signals,
        'daily_analysis': state_copy.get('daily_analysis'),
        'positions': positions,
        'trades': state_copy['trades'],
        'demo_account': {
            **state_copy['demo_account'],
            'positions': positions if state_copy['account_mode'] == 'demo' else state_copy['demo_account']['positions'],
            'unrealized_pnl': total_pnl
        },
        'force_live': force_live
    })
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@ai_trading_bp.route('/api/bot/start', methods=['POST'])
def bot_start():
    """Start the trading bot"""
    global bot_state
    
    req = request.get_json(force=True)
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        bot_state['running'] = True
        bot_state['auto_trade'] = req.get('auto_trade', False)  # Enable auto-trading
        bot_state['account_mode'] = req.get('account_mode', 'demo')
        bot_state['strategy'] = req.get('strategy', 'trend_following')
        bot_state['last_scan'] = None  # Clear last scan so first auto_cycle runs immediately
        bot_state['settings'] = {
            'watchlist': req.get('watchlist', 'top_50'),
            'scan_interval': req.get('scan_interval', 5),
            'min_confidence': req.get('min_confidence', 75),
            'min_option_dte_days': req.get('min_option_dte_days', 1),
            'max_positions': req.get('max_positions', 5),
            'max_daily_trades': req.get('max_daily_trades', 20),
            'max_per_symbol_daily': req.get('max_per_symbol_daily', 6),
            'reentry_cooldown_minutes': req.get('reentry_cooldown_minutes', 10),
            'position_size': req.get('position_size', 1000),
            'stop_loss': req.get('stop_loss', 2.0),
            'take_profit': req.get('take_profit', 4.0),
            'trailing_stop': req.get('trailing_stop', '2'),
            'instrument_type': req.get('instrument_type', 'stocks')
        }
        save_bot_state()
    
    # --- STARTUP HEALTH CHECK: log all potential blockers upfront ---
    _acct = bot_state['demo_account'] if bot_state.get('account_mode') == 'demo' else bot_state['real_account']
    _pos_count = len(_acct.get('positions', []))
    _max_pos = bot_state['settings'].get('max_positions', 5)
    _balance = _acct.get('balance', 0)
    _pos_size = bot_state['settings'].get('position_size', 1000)
    _blockers = []
    if _pos_count >= _max_pos:
        _blockers.append(f"MAX_POSITIONS: {_pos_count}/{_max_pos} slots full")
    if _balance < _pos_size:
        _blockers.append(f"LOW_BALANCE: ${_balance:.2f} < position_size ${_pos_size}")
    if _is_rate_limited('__global__'):
        _blockers.append("RATE_LIMITED: yfinance globally rate-limited")
    if _blockers:
        print(f"⚠️ BOT HEALTH CHECK — {len(_blockers)} blocker(s) detected at startup:")
        for b in _blockers:
            print(f"   🔴 {b}")
    else:
        print(f"✅ BOT HEALTH CHECK — No blockers. balance=${_balance:.2f}, positions={_pos_count}/{_max_pos}, mode={bot_state.get('account_mode')}")
    
    return jsonify({
        'success': True,
        'message': f"Bot started in {bot_state['account_mode']} mode with {bot_state['strategy']} strategy",
        'auto_trade': bot_state['auto_trade']
    })

@ai_trading_bp.route('/api/bot/stop', methods=['POST'])
def bot_stop():
    """Stop the trading bot"""
    global bot_state
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        bot_state['running'] = False
        # DON'T reset auto_trade - preserve user's preference for next start
        # bot_state['auto_trade'] = False  # REMOVED - keep user preference
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': 'Bot stopped'
    })

@ai_trading_bp.route('/api/bot/update_settings', methods=['POST'])
def bot_update_settings():
    """Update bot settings without starting/stopping the bot.
    Allows persisting settings changes immediately.
    """
    global bot_state
    
    req = request.get_json(force=True)
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        
        # Update auto_trade if provided
        if 'auto_trade' in req:
            bot_state['auto_trade'] = req['auto_trade']
            print(f"\U0001f527 Updated auto_trade: {bot_state['auto_trade']}")
        
        # Update strategy if provided
        if 'strategy' in req:
            bot_state['strategy'] = req['strategy']
        
        # Update account_mode if provided
        if 'account_mode' in req:
            bot_state['account_mode'] = req['account_mode']
        
        # Update individual settings if provided
        settings_fields = ['watchlist', 'scan_interval', 'min_confidence', 'max_positions',
              'max_daily_trades', 'max_per_symbol_daily', 'reentry_cooldown_minutes', 'min_option_dte_days', 'position_size', 'stop_loss', 'take_profit', 'trailing_stop', 'instrument_type',
                          'partial_profit_taking', 'close_0dte_before_expiry']
        
        # Track if routing-critical settings changed
        routing_changed = False
        for field in settings_fields:
            if field in req:
                old_val = bot_state['settings'].get(field)
                bot_state['settings'][field] = req[field]
                if field in ('watchlist', 'instrument_type') and old_val != req[field]:
                    routing_changed = True
                    print(f"\U0001f527 Setting '{field}' changed: {old_val} → {req[field]}")
        
        # When watchlist or instrument_type changes, clear stale cached signals
        # This prevents old daily/swing signals from being auto-traded after switching to intraday
        if routing_changed:
            bot_state['signals'] = []
            bot_state['last_scan'] = None  # Force fresh scan on next auto_cycle
            print(f"\U0001f527 Cleared cached signals and last_scan due to settings change")
        
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': 'Settings updated',
        'auto_trade': bot_state.get('auto_trade', False),
        'settings': bot_state['settings']
    })

@ai_trading_bp.route('/api/bot/switch_account', methods=['POST'])
def bot_switch_account():
    """Switch between demo and real account mode"""
    global bot_state
    
    req = request.get_json(force=True)
    account_mode = req.get('account_mode', 'demo')
    
    if account_mode not in ['demo', 'real']:
        return jsonify({'success': False, 'error': 'Invalid account mode'}), 400
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        bot_state['account_mode'] = account_mode
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': f'Switched to {account_mode} account',
        'account_mode': account_mode
    })

@ai_trading_bp.route('/api/bot/test_trade', methods=['POST'])
def bot_test_trade():
    """Test trade execution - creates a test trade to verify the system works"""
    global bot_state
    
    req = request.get_json(force=True)
    symbol = req.get('symbol', 'AAPL')
    action = req.get('action', 'BUY')
    quantity = req.get('quantity', 1)
    price = req.get('price', 100.0)
    account_mode = req.get('account_mode', 'demo')
    
    load_bot_state()
    
    with BOT_STATE_LOCK:
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        if action == 'BUY':
            cost = quantity * price
            if cost > account.get('balance', 0):
                return jsonify({'success': False, 'error': f'Insufficient balance: ${account.get("balance", 0):.2f} < ${cost:.2f}'})
            
            # Add to existing position or create new one
            default_sl = price * 0.95
            default_target = price * 1.10
            position, is_new = add_or_update_position(
                account, symbol, 'LONG', quantity, price,
                stop_loss=default_sl, target=default_target,
                extra_fields={'test_trade': True, 'source': 'manual'}
            )
            
            # Log trade
            trade = {
                'symbol': symbol,
                'action': 'BUY',
                'quantity': quantity,
                'price': price,
                'timestamp': datetime.now().isoformat(),
                'test_trade': True
            }
            account['trades'].append(trade)
            # Recalculate balance from trade history (authoritative)
            account['balance'] = recalculate_balance(account)
            save_bot_state()
            
            return jsonify({
                'success': True,
                'message': f'Bought {quantity} {symbol} @ ${price:.2f}',
                'balance': account['balance']
            })
        
        return jsonify({'success': False, 'error': 'Only BUY supported for test trades'})

@ai_trading_bp.route('/api/bot/reset_demo', methods=['POST'])
def bot_reset_demo():
    """Reset demo account to starting balance"""
    global bot_state
    
    load_bot_state()
    
    with BOT_STATE_LOCK:
        # Reset demo account to defaults
        bot_state['demo_account'] = {
            'balance': 10000.0,
            'initial_balance': 10000.0,
            'positions': [],
            'trades': [],
            'pnl_history': []
        }
        
        # Clear signals and trades from bot state
        bot_state['signals'] = []
        bot_state['executed_trades'] = []
        bot_state['today_pnl'] = 0.0
        bot_state['running'] = False
        bot_state['auto_trade'] = False
        
        save_bot_state()
        
        print("🔄 [RESET] Demo account reset to $10,000")
        
        return jsonify({
            'success': True,
            'message': 'Demo account reset to $10,000',
            'balance': 10000.0
        })

@ai_trading_bp.route('/api/bot/import_positions', methods=['POST'])
def bot_import_positions():
    """Import active positions from monitoring system (active_positions.json) into AI Bot"""
    global bot_state
    
    req = request.get_json(force=True)
    account_mode = req.get('account_mode', 'demo')
    
    # Load active positions from monitoring system
    positions_file = 'active_positions.json'
    if not os.path.exists(positions_file):
        return jsonify({'success': False, 'error': 'No monitoring positions found'}), 404
    
    try:
        with open(positions_file, 'r') as f:
            monitoring_positions = json.load(f)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Failed to read positions: {str(e)}'}), 500
    
    imported = []
    with BOT_STATE_LOCK:
        load_bot_state()
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        for key, pos in monitoring_positions.items():
            # Only import active stock positions
            if pos.get('status') != 'active':
                continue
            if pos.get('type') == 'option':
                continue  # Skip options for now
            
            symbol = pos.get('ticker')
            if not symbol:
                continue
            
            # Check if position already exists
            existing = next((p for p in account['positions'] if p['symbol'] == symbol), None)
            if existing:
                continue  # Skip duplicates
            
            # Get current price
            try:
                price, _ = cached_get_price(symbol, period='1d', interval='1d', prepost=False)
                current_price = float(price) if price is not None else pos.get('entry', 0)
            except:
                current_price = pos.get('entry', 0)
            
            # Create position for AI Bot
            new_pos = {
                'symbol': symbol,
                'side': 'LONG',  # Monitoring positions are long by default
                'quantity': pos.get('quantity', 1),
                'entry_price': pos.get('entry', 0),
                'current_price': current_price,
                'stop_loss': pos.get('stop_loss', pos.get('entry', 0) * 0.95),
                'target': pos.get('target_1', pos.get('entry', 0) * 1.10),
                'timestamp': pos.get('date_added', datetime.now().isoformat()),
                'imported_from': 'monitoring'
            }
            
            # Deduct cost from balance
            cost = new_pos['quantity'] * new_pos['entry_price']
            if cost <= account['balance']:
                account['positions'].append(new_pos)
                # Also log as a BUY trade so P&L tracking works
                trade = {
                    'symbol': symbol,
                    'action': 'BUY',
                    'side': 'LONG',
                    'instrument_type': 'stock',
                    'source': 'imported',
                    'quantity': new_pos['quantity'],
                    'price': new_pos['entry_price'],
                    'timestamp': new_pos['timestamp']
                }
                account['trades'].append(trade)
                imported.append(symbol)
        
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
    
    if imported:
        return jsonify({
            'success': True,
            'message': f'Imported {len(imported)} positions: {", ".join(imported)}',
            'imported': imported
        })
    else:
        return jsonify({
            'success': True,
            'message': 'No new positions to import (all already exist or are options/closed)',
            'imported': []
        })

@ai_trading_bp.route('/api/bot/scan', methods=['POST'])
def bot_scan():
    """Run a market scan based on strategy"""
    global bot_state
    
    # Bot must be running (Start Trading Bot button must be ON)
    if not bot_state.get('running', False):
        return jsonify({'success': False, 'error': 'Bot is not running. Click "Start Trading Bot" first.'}), 400
    
    req = request.get_json(force=True)
    strategy = req.get('strategy', bot_state['strategy'])
    watchlist_name = req.get('watchlist', bot_state['settings']['watchlist'])
    instrument_type = req.get('instrument_type', bot_state['settings'].get('instrument_type', 'stocks'))

    # Respect explicit watchlist mode even if persisted instrument_type is stale
    if watchlist_name == 'intraday_options':
        instrument_type = 'options'
    elif watchlist_name == 'intraday_stocks' and instrument_type == 'options':
        instrument_type = 'stocks'
    
    signals = []
    response_message = None
    
    # Determine which scanners to run based on instrument_type AND watchlist
    # instrument_type constrains WHAT to scan: 'stocks' (no options), 'options' (no stocks), 'both'
    # watchlist determines HOW to scan: 'intraday_stocks'/'intraday_options' = intraday, others = daily/swing
    can_scan_stocks = instrument_type in ('stocks', 'both')
    can_scan_options = instrument_type in ('options', 'both')
    # Options are always intraday (no daily options scanner exists)
    is_intraday_mode = (watchlist_name in ('intraday_stocks', 'intraday_options') 
                        or instrument_type in ('both', 'options'))
    
    run_options_scan = can_scan_options and is_intraday_mode
    run_intraday_stocks = can_scan_stocks and is_intraday_mode
    run_daily_scan = can_scan_stocks and not is_intraday_mode
    
    print(f"🔍 Manual scan: instrument_type={instrument_type}, watchlist={watchlist_name}, "
          f"options={run_options_scan}, intraday_stocks={run_intraday_stocks}, daily={run_daily_scan}")
    
    if run_options_scan:
        # Intraday options scanner
        results = run_intraday_scan_batched(
            INTRADAY_OPTIONS_UNIVERSE,
            scan_intraday_option,
            max_workers=4,
            batch_size=5,
            batch_delay=0.7,
            fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
        )

        if not results:
            cached_options = scanner_cache.get('intraday-options', {}).get('data') or []
            if cached_options:
                results = cached_options

        option_candidates = []
        min_conf = bot_state['settings'].get('min_confidence', 75)
        
        for r in sorted(results, key=lambda x: x['score'], reverse=True)[:10]:
            confidence = min(100, max(50, int(40 + r['score'] * 4)))
            option_signal = {
                'symbol': r['symbol'],
                'action': 'BUY',
                'confidence': confidence,
                'entry': r['premium'],
                'stop_loss': r['stop_premium'],
                'target': r['target_1_premium'],
                'reason': ', '.join(r['signals'][:3]),
                'instrument_type': 'option',
                'option_type': r['option_type'],
                'contract': r['contract'],
                'strike': r['strike'],
                'expiry': r['expiry'],
                'dte': r['dte'],
                'premium': r['premium'],
                'stock_price': r['price'],
                'scan_type': 'intraday'
            }
            option_candidates.append(option_signal)
            if confidence >= min_conf:
                signals.append(option_signal)

        # If options mode is active but strict threshold filtered everything,
        # show top candidates for DISPLAY ONLY (marked below-threshold so auto-trade skips them).
        if not signals and instrument_type == 'options':
            if option_candidates:
                for oc in option_candidates[:5]:
                    oc['_below_threshold'] = True
                signals = option_candidates[:5]
                response_message = f'No options met {min_conf}% confidence; showing top candidates (will NOT auto-trade).'
            else:
                live_fallback = build_live_option_fallback_signals(
                    ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
                    max_candidates=6
                )
                if live_fallback:
                    for lf in live_fallback:
                        lf['_below_threshold'] = True
                    signals = live_fallback
                    response_message = f'No options met {min_conf}% confidence; showing fallback candidates (will NOT auto-trade).'
    
    if run_intraday_stocks:
        # Intraday stock scanner
        results = run_intraday_scan_batched(
            INTRADAY_STOCK_UNIVERSE,
            scan_intraday_stock,
            max_workers=5,
            batch_size=6,
            batch_delay=0.6,
            fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
        )

        if not results:
            cached_stocks = scanner_cache.get('intraday-stocks', {}).get('data') or []
            if cached_stocks:
                results = cached_stocks
        
        for r in sorted(results, key=lambda x: x['score'], reverse=True)[:10]:
            confidence = min(100, max(50, int(40 + r['score'] * 4)))
            if confidence >= bot_state['settings'].get('min_confidence', 75):
                signals.append({
                    'symbol': r['symbol'],
                    'action': 'BUY' if r['direction'] == 'BULLISH' else 'SELL',
                    'confidence': confidence,
                    'entry': r['price'],
                    'stop_loss': r['stop_loss'],
                    'target': r['target_1'],
                    'reason': ', '.join(r['signals'][:3]),
                    'instrument_type': 'stock',
                    'atr': r.get('atr', 0),
                    'scan_type': 'intraday'
                })
    
    if run_daily_scan:
        # Standard daily stock scan (swing trades)
        symbols = WATCHLISTS.get(watchlist_name, WATCHLISTS['top_50'])
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(calculate_technical_indicators, sym): sym for sym in symbols}
            
            for future in as_completed(futures):
                try:
                    data = future.result()
                    if data:
                        signal = analyze_for_strategy(data, strategy)
                        if signal and signal['confidence'] >= bot_state['settings'].get('min_confidence', 75):
                            signal['instrument_type'] = 'stock'
                            signal['scan_type'] = 'daily'
                            signals.append(signal)
                except Exception as e:
                    print(f"Scan error: {e}")
    
    # Sort by confidence
    signals.sort(key=lambda x: x['confidence'], reverse=True)
    signals = signals[:10]  # Top 10 signals

    if not signals and (run_options_scan or run_intraday_stocks) and not run_daily_scan:
        response_message = 'No intraday setups found. Try again in 1-2 minutes.'

    # Local fallback when live market provider is rate-limited
    min_conf = bot_state['settings'].get('min_confidence', 75)
    if not signals:
        fallback_signals = load_local_fallback_signals(
            is_intraday_mode=is_intraday_mode,
            instrument_type=instrument_type,
            min_confidence=min_conf
        )
        if fallback_signals:
            signals = fallback_signals[:10]
            response_message = 'Using local cached scanner results (live provider temporarily rate-limited).'

    # Final pass: always refresh signal entry prices to latest available quotes
    signals = refresh_signal_entries_with_live_prices(signals, force_refresh=True)
    
    with BOT_STATE_LOCK:
        bot_state['signals'] = signals
        bot_state['last_scan'] = datetime.now().isoformat()
        save_bot_state()
    
    return jsonify({
        'success': True,
        'signals': signals,
        'count': len(signals),
        'timestamp': bot_state['last_scan'],
        'message': response_message
    })


def load_local_fallback_signals(is_intraday_mode: bool, instrument_type: str, min_confidence: int = 75):
    """Load fallback signals from local scanner output files when live scans fail.

    This keeps the UI useful during Yahoo rate-limit windows.
    """
    try:
        fallback = []
        allow_stock = instrument_type in ('stocks', 'both')
        allow_options = instrument_type in ('options', 'both')

        if is_intraday_mode:
            # No reliable local options fallback file exists; avoid returning stock fallbacks in options-only mode.
            if not allow_stock and allow_options:
                return []

            intraday_path = 'triple_confirmation_intraday.json'
            if os.path.exists(intraday_path):
                with open(intraday_path, 'r') as f:
                    data = json.load(f) or {}
                rows = data.get('results', [])[:20]
                for row in rows:
                    confidence = min(95, max(55, int(45 + float(row.get('score', 0)) * 4)))
                    if confidence < min_confidence:
                        continue
                    direction = row.get('direction', 'BULLISH')
                    entry = float(row.get('current_price', 0) or 0)
                    stop = float(row.get('stop_loss', 0) or 0)
                    target = float(row.get('target_1', 0) or 0)
                    if entry <= 0:
                        continue
                    fallback.append({
                        'symbol': row.get('ticker', ''),
                        'action': 'BUY' if direction == 'BULLISH' else 'SELL',
                        'confidence': confidence,
                        'entry': entry,
                        'stop_loss': stop if stop > 0 else round(entry * 0.98, 2),
                        'target': target if target > 0 else round(entry * 1.02, 2),
                        'reason': ', '.join((row.get('signals') or [])[:3]),
                        'instrument_type': 'stock',
                        'scan_type': 'intraday',
                        'rsi': 50.0,
                        'volume_ratio': float(row.get('volume_ratio', 1.0) or 1.0),
                    })
        else:
            if not allow_stock:
                return []

            picks_path = 'top_picks.json'
            if os.path.exists(picks_path):
                with open(picks_path, 'r') as f:
                    data = json.load(f) or {}
                rows = (data.get('stocks', []) + data.get('etfs', []))[:20]
                for row in rows:
                    score = float(row.get('score', 0) or 0)
                    confidence = min(95, max(50, int(40 + score * 6)))
                    if confidence < min_confidence:
                        continue
                    md = row.get('data') or {}
                    entry = float(md.get('price', 0) or 0)
                    atr = float(md.get('atr', 0) or 0)
                    if entry <= 0:
                        continue
                    signals = row.get('signals') or []
                    bullish = any('bullish' in str(s).lower() for s in signals)
                    fallback.append({
                        'symbol': row.get('ticker', ''),
                        'action': 'BUY' if bullish else 'SELL',
                        'confidence': confidence,
                        'entry': entry,
                        'stop_loss': round(entry - (atr * 1.5), 2) if atr > 0 else round(entry * 0.97, 2),
                        'target': round(entry + (atr * 2.0), 2) if atr > 0 else round(entry * 1.03, 2),
                        'reason': ', '.join(signals[:3]),
                        'instrument_type': 'stock',
                        'scan_type': 'daily',
                        'rsi': float(md.get('rsi', 50.0) or 50.0),
                        'volume_ratio': float(md.get('volume_ratio', 1.0) or 1.0),
                        'atr': atr,
                    })

        fallback = [s for s in fallback if s.get('symbol')]
        fallback.sort(key=lambda x: x.get('confidence', 0), reverse=True)
        return fallback[:10]
    except Exception as e:
        print(f"Fallback signal load error: {e}")
        return []

def build_live_option_fallback_signals(symbols, max_candidates=6):
    """Build lightweight intraday option candidates from live option chains."""
    candidates = []
    min_dte_days = get_min_option_dte_days()
    for symbol in symbols:
        try:
            spot, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
            if not spot or spot <= 0:
                continue

            expirations = cached_get_option_dates(symbol)
            if not expirations:
                continue

            today_dt = datetime.now()
            best_exp = expirations[0]
            for exp in expirations:
                try:
                    dte = (datetime.strptime(exp, '%Y-%m-%d') - today_dt).days
                    if min_dte_days <= dte <= 5:
                        best_exp = exp
                        break
                except Exception:
                    pass

            chain = cached_get_option_chain(symbol, best_exp)
            if not chain:
                continue

            def _atm_signal(df, option_type):
                if df is None or df.empty:
                    return None
                rows = df.copy()
                rows['dist'] = abs(rows['strike'] - float(spot))
                atm = rows.nsmallest(1, 'dist')
                if atm.empty:
                    return None
                row = atm.iloc[0]
                strike = float(row.get('strike', 0) or 0)
                last = float(row.get('lastPrice', 0) or 0)
                bid = float(row.get('bid', 0) or 0)
                ask = float(row.get('ask', 0) or 0)
                premium = last if last > 0 else (round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0)
                if premium <= 0:
                    return None
                try:
                    dte_val = max(0, (datetime.strptime(best_exp, '%Y-%m-%d') - today_dt).days)
                except Exception:
                    dte_val = 0
                if dte_val < min_dte_days:
                    return None
                return {
                    'symbol': symbol,
                    'action': 'BUY',
                    'confidence': 55,
                    'entry': round(premium, 2),
                    'stop_loss': round(premium * 0.6, 2),
                    'target': round(premium * 1.4, 2),
                    'target_2': round(premium * 1.8, 2),
                    'reason': 'Live option-chain fallback candidate',
                    'instrument_type': 'option',
                    'option_type': option_type,
                    'contract': f"{symbol} {best_exp} {strike:.0f}{'C' if option_type == 'call' else 'P'}",
                    'strike': strike,
                    'expiry': best_exp,
                    'dte': dte_val,
                    'premium': round(premium, 2),
                    'stock_price': round(float(spot), 2),
                    'scan_type': 'intraday'
                }

            call_sig = _atm_signal(chain.calls, 'call')
            put_sig = _atm_signal(chain.puts, 'put')
            if call_sig:
                candidates.append(call_sig)
            if put_sig:
                candidates.append(put_sig)

            if len(candidates) >= max_candidates:
                break
        except Exception:
            continue

    return candidates[:max_candidates]

@ai_trading_bp.route('/api/bot/auto_cycle', methods=['POST'])
def bot_auto_cycle():
    """
    Run automatic trading cycle:
    1. Check existing positions for stop loss / target hits
    2. Scan for signals if bot is running
    3. Execute trades automatically if auto_trade is enabled
    """
    global bot_state
    
    # Quick check in-memory flag BEFORE loading full state from disk
    # Avoids reading ~7K-line JSON file every 10s when bot is stopped
    if not bot_state.get('running', False):
        return jsonify({
            'success': False,
            'message': 'Bot is not running',
            'trades_executed': [],
            'exits_triggered': []
        })
    
    # Reload state from file to ensure we have latest
    load_bot_state()
    
    # Re-check after reload (in case bot was stopped via another client)
    if not bot_state.get('running', False):
        return jsonify({
            'success': False,
            'message': 'Bot is not running',
            'trades_executed': [],
            'exits_triggered': []
        })
    
    # Debug: log auto_trade status
    print(f"🤖 Auto-cycle: running={bot_state.get('running')}, auto_trade={bot_state.get('auto_trade')}")
    
    # =========================================================================
    # STEP 1: Check existing positions for SL/Target hits (ALWAYS runs)
    # =========================================================================
    exits_triggered = []
    account_mode = bot_state.get('account_mode', 'demo')
    account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
    positions = account.get('positions', [])
    
    if positions:
        # Get live prices for all position symbols
        # For stocks: fetch stock price. For options: fetch option premium.
        position_symbols = list(set(p['symbol'] for p in positions))
        live_stock_prices = {}
        
        # Batch fetch live stock prices (single API call via cache)
        live_stock_prices = cached_batch_prices(position_symbols, period='1d', interval='1m', prepost=True)
        missing_symbols = [s for s in position_symbols if s not in live_stock_prices]
        if missing_symbols:
            quote_api_prices = fetch_quote_api_batch(missing_symbols)
            for sym, price in quote_api_prices.items():
                if sym not in live_stock_prices:
                    live_stock_prices[sym] = price
        still_missing = [s for s in position_symbols if s not in live_stock_prices]
        for sym in still_missing:
            info = cached_get_ticker_info(sym)
            info_price = None
            if info:
                info_price = info.get('regularMarketPrice') or info.get('currentPrice')
            if info_price is not None:
                live_stock_prices[sym] = float(info_price)
        
        # Fetch live option premiums for option positions (cached chains)
        option_premiums = {}  # keyed by (symbol, expiry, strike, opt_type)
        option_positions_by_symbol = {}
        for pos in positions:
            if pos.get('instrument_type') == 'option':
                sym = pos['symbol']
                if sym not in option_positions_by_symbol:
                    option_positions_by_symbol[sym] = []
                option_positions_by_symbol[sym].append(pos)
        
        for symbol, opt_positions in option_positions_by_symbol.items():
            try:
                available_dates = cached_get_option_dates(symbol)
                for pos in opt_positions:
                    expiry = pos.get('expiry', '')
                    strike = pos.get('strike', 0)
                    opt_type = pos.get('option_type', 'call')
                    
                    matched_expiry = expiry if expiry in available_dates else None
                    if not matched_expiry:
                        for d in available_dates:
                            if expiry and expiry in d:
                                matched_expiry = d
                                break
                    
                    if matched_expiry:
                        chain = cached_get_option_chain(symbol, matched_expiry)
                        if chain:
                            df = chain.calls if opt_type == 'call' else chain.puts
                            if not df.empty and strike > 0:
                                strike_match = df[abs(df['strike'] - strike) < 0.01]
                                if not strike_match.empty:
                                    row = strike_match.iloc[0]
                                    last = float(row.get('lastPrice', 0))
                                    bid = float(row.get('bid', 0))
                                    ask = float(row.get('ask', 0))
                                    premium = last if last > 0 else (round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0)
                                    if premium > 0:
                                        key = (symbol, expiry, strike, opt_type)
                                        option_premiums[key] = premium
            except Exception as e:
                if not _is_expected_no_data_error(e):
                    _log_fetch_event('bot-options-error', symbol, f"⚠️ Error fetching options data for {symbol}: {e}", cooldown=180)
        
        # Check each position for exit triggers
        positions_to_remove = []
        
        # Pre-fetch 5-min data for all day-trade stock positions (single batch)
        # This avoids per-position API calls inside the loop
        dynamic_any_updated = False
        intraday_5min_data = {}  # {symbol: DataFrame}
        day_trade_stock_symbols = list(set(
            p['symbol'] for p in positions
            if p.get('trade_type') == 'day' and p.get('instrument_type') != 'option'
        ))
        if day_trade_stock_symbols:
            def _fetch_5min(sym):
                return (sym, cached_get_history(sym, period='5d', interval='5m'))
            with ThreadPoolExecutor(max_workers=min(len(day_trade_stock_symbols), 10)) as executor:
                for sym, df_5m in executor.map(lambda s: _fetch_5min(s), day_trade_stock_symbols):
                    if df_5m is not None and not df_5m.empty:
                        intraday_5min_data[sym] = df_5m
        
        # Pre-fetch daily data for swing stock positions to compute high watermark
        # This ensures trailing stops account for after-hours/pre-market peaks
        # that may have occurred while the bot wasn't running
        swing_high_watermarks = {}  # {symbol: highest_price_since_entry}
        swing_low_watermarks = {}   # {symbol: lowest_price_since_entry}
        swing_stock_symbols = list(set(
            p['symbol'] for p in positions
            if p.get('trade_type') == 'swing' and p.get('instrument_type') != 'option'
        ))
        if swing_stock_symbols:
            def _fetch_swing_hist(sym):
                # Get intraday data with pre/post market to capture after-hours peaks
                return (sym, cached_get_history(sym, period='5d', interval='5m'))
            with ThreadPoolExecutor(max_workers=min(len(swing_stock_symbols), 10)) as executor:
                for sym, df_hist in executor.map(lambda s: _fetch_swing_hist(s), swing_stock_symbols):
                    if df_hist is not None and not df_hist.empty:
                        # Find the position's entry timestamp to filter relevant data
                        sym_positions = [p for p in positions if p['symbol'] == sym]
                        for sp in sym_positions:
                            entry_ts = sp.get('timestamp', '')
                            if entry_ts:
                                try:
                                    from datetime import datetime as dt_cls
                                    entry_dt = dt_cls.fromisoformat(entry_ts.replace('Z', '+00:00'))
                                    # Make entry_dt timezone-aware if df_hist index is tz-aware
                                    if df_hist.index.tz is not None and entry_dt.tzinfo is None:
                                        import pytz
                                        entry_dt = pytz.UTC.localize(entry_dt)
                                    elif df_hist.index.tz is None and entry_dt.tzinfo is not None:
                                        entry_dt = entry_dt.replace(tzinfo=None)
                                    # Get data since entry
                                    since_entry = df_hist[df_hist.index >= entry_dt]
                                    if not since_entry.empty:
                                        swing_high_watermarks[sym] = float(since_entry['High'].max())
                                        swing_low_watermarks[sym] = float(since_entry['Low'].min())
                                except Exception as e:
                                    print(f"⚠️ Error computing watermark for {sym}: {e}")
                            
                            # Fallback: if no valid timestamp, use all available data
                            if sym not in swing_high_watermarks:
                                swing_high_watermarks[sym] = float(df_hist['High'].max())
                                swing_low_watermarks[sym] = float(df_hist['Low'].min())
        
        
        _cycle_now_iso = datetime.now().isoformat()
        _any_price_updated = False
        for pos in positions:
            symbol = pos['symbol']
            is_option = pos.get('instrument_type') == 'option'
            
            if is_option:
                key = (symbol, pos.get('expiry', ''), pos.get('strike', 0), pos.get('option_type', 'call'))
                if key not in option_premiums:
                    continue
                current_price = option_premiums[key]
                pos['current_price'] = current_price  # Update for display
                pos['last_price_update'] = _cycle_now_iso
                _any_price_updated = True
            else:
                if symbol not in live_stock_prices:
                    continue
                current_price = live_stock_prices[symbol]
                pos['current_price'] = current_price
                pos['last_price_update'] = _cycle_now_iso
                _any_price_updated = True
                
            entry_price = pos.get('entry_price', 0)
            stop_loss = pos.get('stop_loss', 0)
            target = pos.get('target', 0)
            quantity = pos.get('quantity', 0)
            side = pos.get('side', 'LONG')
            multiplier = 100 if is_option else 1
            
            # DYNAMIC SL/TARGET UPDATE for intraday stocks in uptrend
            # Uses VWAP, ATR, EMA to recalculate SL and target based on current conditions
            # Falls back to simple percentage trailing stop for options or if data unavailable
            dynamic_updated = False
            if not is_option and entry_price > 0 and pos.get('trade_type', 'swing') == 'day':
                pre_df = intraday_5min_data.get(symbol)
                result = recalculate_intraday_sl_target(symbol, entry_price, stop_loss, target, side, df=pre_df)
                if result:
                    new_sl, new_target, signals = result
                    sl_changed = (new_sl != stop_loss)
                    tgt_changed = (new_target != target)
                    if sl_changed or tgt_changed:
                        dynamic_updated = True
                        dynamic_any_updated = True
                        if sl_changed:
                            pos['stop_loss'] = new_sl
                            stop_loss = new_sl
                        if tgt_changed:
                            pos['target'] = new_target
                            target = new_target
                        sig_str = ', '.join(signals)
                        print(f"🔄 Dynamic SL/Target update for {symbol}: SL=${stop_loss:.2f} Target=${target:.2f} [{sig_str}]")
            
            # TRAILING STOP FALLBACK: ATR-based or simple % trailing for options or if dynamic didn't update
            if not dynamic_updated:
                trailing_mode = str(bot_state['settings'].get('trailing_stop', 'atr')).strip()
                if not trailing_mode:
                    trailing_mode = 'atr'  # Default to ATR if empty
                min_profit_to_trail = 0.005  # 0.5% minimum profit before trailing activates
                
                # For SWING trades: use high/low watermark instead of current_price
                # This catches after-hours/pre-market peaks that occurred while bot was offline
                is_swing = pos.get('trade_type', 'swing') == 'swing'
                if is_swing and not is_option:
                    if side == 'LONG':
                        trail_reference_price = swing_high_watermarks.get(symbol, current_price)
                        # Use the higher of current price and historical high
                        trail_reference_price = max(trail_reference_price, current_price)
                        if trail_reference_price > current_price:
                            print(f"📊 {symbol} SWING: Using high watermark ${trail_reference_price:.2f} for trailing (current: ${current_price:.2f})")
                    else:  # SHORT
                        trail_reference_price = swing_low_watermarks.get(symbol, current_price)
                        trail_reference_price = min(trail_reference_price, current_price)
                        if trail_reference_price < current_price:
                            print(f"📊 {symbol} SWING: Using low watermark ${trail_reference_price:.2f} for trailing (current: ${current_price:.2f})")
                else:
                    trail_reference_price = current_price
                
                if trailing_mode == 'atr' and entry_price > 0:
                    # ==== ATR-BASED TRAILING STOP ====
                    # Uses 1.5x ATR from reference price (high watermark for swing, current for day)
                    # This lets trades breathe during normal volatility
                    pos_atr = pos.get('_cached_atr', 0)
                    if pos_atr <= 0:
                        # Estimate ATR from entry price (roughly 1-2% for most stocks)
                        pos_atr = entry_price * 0.015
                    atr_multiplier = 1.5
                    
                    if side == 'LONG':
                        profit_pct = (trail_reference_price - entry_price) / entry_price
                        if profit_pct >= min_profit_to_trail:
                            new_trailing_stop = round(trail_reference_price - (pos_atr * atr_multiplier), 2)
                            if new_trailing_stop > stop_loss and new_trailing_stop > entry_price:
                                pos['stop_loss'] = new_trailing_stop
                                stop_loss = new_trailing_stop
                                print(f"📈 ATR Trailing stop UP for {symbol}: ${stop_loss:.2f} (ATR=${pos_atr:.2f}, ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
                    else:  # SHORT
                        profit_pct = (entry_price - trail_reference_price) / entry_price
                        if profit_pct >= min_profit_to_trail:
                            new_trailing_stop = round(trail_reference_price + (pos_atr * atr_multiplier), 2)
                            if (stop_loss == 0 or new_trailing_stop < stop_loss) and new_trailing_stop < entry_price:
                                pos['stop_loss'] = new_trailing_stop
                                stop_loss = new_trailing_stop
                                print(f"📉 ATR Trailing stop DOWN for {symbol}: ${stop_loss:.2f} (ATR=${pos_atr:.2f}, ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
                else:
                    # ==== FIXED % TRAILING STOP (legacy fallback) ====
                    try:
                        trailing_pct = float(trailing_mode) / 100
                    except (ValueError, TypeError):
                        trailing_pct = 0.02  # Default 2%
                    if trailing_pct > 0 and entry_price > 0:
                        if side == 'LONG':
                            profit_pct = (trail_reference_price - entry_price) / entry_price
                            if profit_pct >= min_profit_to_trail:
                                new_trailing_stop = trail_reference_price * (1 - trailing_pct)
                                if new_trailing_stop > stop_loss and new_trailing_stop > entry_price:
                                    pos['stop_loss'] = round(new_trailing_stop, 2)
                                    stop_loss = pos['stop_loss']
                                    print(f"📈 Trailing stop moved UP for {symbol}: ${stop_loss:.2f} (ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
                        else:  # SHORT
                            profit_pct = (entry_price - trail_reference_price) / entry_price
                            if profit_pct >= min_profit_to_trail:
                                new_trailing_stop = trail_reference_price * (1 + trailing_pct)
                                if (stop_loss == 0 or new_trailing_stop < stop_loss) and new_trailing_stop < entry_price:
                                    pos['stop_loss'] = round(new_trailing_stop, 2)
                                    stop_loss = pos['stop_loss']
                                    print(f"📉 Trailing stop moved DOWN for {symbol}: ${stop_loss:.2f} (ref=${trail_reference_price:.2f}, profit: {profit_pct*100:.2f}%)")
            
            exit_reason = None
            exit_price = current_price
            
            # Track whether stop was trailed into profit (for exit reason labeling)
            _stop_is_trailing = (stop_loss > entry_price) if side == 'LONG' else (0 < stop_loss < entry_price)
            
            # ===== MAX LOSS % GUARD FOR OPTIONS =====
            # Prevents catastrophic option losses (e.g., -50% GOOGL calls)
            if not exit_reason and is_option and entry_price > 0:
                max_option_loss_pct = float(bot_state['settings'].get('max_option_loss_pct', 40)) / 100
                if side == 'LONG':
                    option_loss_pct = (entry_price - current_price) / entry_price
                else:
                    option_loss_pct = (current_price - entry_price) / entry_price
                if option_loss_pct >= max_option_loss_pct:
                    exit_reason = 'MAX_LOSS_GUARD'
                    exit_price = current_price
                    display_name = pos.get('contract', symbol)
                    print(f"🛡️ MAX LOSS GUARD: {display_name} down {option_loss_pct*100:.1f}% (limit: {max_option_loss_pct*100:.0f}%) — force closing")
            
            # ===== 0DTE EXPIRY PROTECTION =====
            # Close options that expire today or tomorrow if not in profit
            # Prevents holding short-dated options overnight that will likely expire worthless
            if is_option and bot_state['settings'].get('close_0dte_before_expiry', True):
                try:
                    import pytz
                    et_tz = pytz.timezone('US/Eastern')
                    now_et = datetime.now(et_tz)
                    expiry_str = pos.get('expiry', '')
                    if expiry_str:
                        expiry_dt = datetime.strptime(expiry_str, '%Y-%m-%d')
                        days_to_expiry = (expiry_dt.date() - now_et.date()).days
                        
                        # Close at 3:00 PM ET if expiring today (0DTE)
                        if days_to_expiry <= 0 and now_et.hour >= 15:
                            pnl_check = (current_price - entry_price) if side == 'LONG' else (entry_price - current_price)
                            exit_reason = 'EXPIRY_PROTECTION'
                            exit_price = current_price
                            print(f"⏰ EXPIRY PROTECTION: Closing {pos.get('contract', symbol)} - expires today, P&L: ${pnl_check * quantity * multiplier:.2f}")
                        
                        # Close at 3:45 PM ET if expiring tomorrow and losing money
                        elif days_to_expiry == 1 and now_et.hour >= 15 and now_et.minute >= 45:
                            pnl_check = (current_price - entry_price) if side == 'LONG' else (entry_price - current_price)
                            if pnl_check < 0:  # Only force-close if losing
                                exit_reason = 'EXPIRY_PROTECTION'
                                exit_price = current_price
                                print(f"⏰ EXPIRY PROTECTION: Closing losing {pos.get('contract', symbol)} - expires tomorrow, P&L: ${pnl_check * quantity * multiplier:.2f}")
                except Exception as e:
                    print(f"⚠️ Error in expiry protection for {symbol}: {e}")
            
            # ===== PARTIAL PROFIT-TAKING =====
            # When target is hit: sell 50% at Target 1, move stop to breakeven, let rest run to Target 2
            if not exit_reason and bot_state['settings'].get('partial_profit_taking', True):
                target_2 = pos.get('target_2', 0)
                partial_sold = pos.get('_partial_sold', False)
                
                if not partial_sold and target > 0 and quantity >= 2:
                    target_hit = (side == 'LONG' and current_price >= target) or \
                                 (side == 'SHORT' and current_price <= target)
                    
                    if target_hit:
                        # Sell 50% of position
                        sell_qty = max(1, quantity // 2)
                        remaining_qty = quantity - sell_qty
                        
                        if side == 'LONG':
                            partial_pnl = (current_price - entry_price) * sell_qty * multiplier
                        else:
                            partial_pnl = (entry_price - current_price) * sell_qty * multiplier
                        partial_pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                        if side == 'SHORT':
                            partial_pnl_pct = -partial_pnl_pct
                        
                        # Log partial exit trade
                        partial_trade = {
                            'symbol': symbol,
                            'contract': pos.get('contract', ''),
                            'action': 'PARTIAL_SELL' if side == 'LONG' else 'PARTIAL_COVER',
                            'side': side,
                            'instrument_type': pos.get('instrument_type', 'stock'),
                            'option_type': pos.get('option_type', ''),
                            'source': pos.get('source', 'bot'),
                            'quantity': sell_qty,
                            'entry_price': entry_price,
                            'exit_price': current_price,
                            'price': current_price,
                            'pnl': partial_pnl,
                            'pnl_pct': partial_pnl_pct,
                            'reason': 'PARTIAL_TARGET_HIT',
                            'timestamp': datetime.now().isoformat(),
                            'auto_exit': True,
                            'trade_type': pos.get('trade_type', 'swing')
                        }
                        account['trades'].append(partial_trade)
                        
                        # Update position: reduce quantity, move stop to breakeven, set new target
                        pos['quantity'] = remaining_qty
                        pos['stop_loss'] = entry_price  # Move stop to breakeven
                        if target_2 > 0:
                            pos['target'] = target_2  # Set target to Target 2
                        else:
                            # If no target_2, set to 1.5x the original target distance
                            if side == 'LONG':
                                pos['target'] = round(entry_price + (target - entry_price) * 1.5, 2)
                            else:
                                pos['target'] = round(entry_price - (entry_price - target) * 1.5, 2)
                        pos['_partial_sold'] = True
                        
                        # Update local vars for continued processing
                        quantity = remaining_qty
                        stop_loss = pos['stop_loss']
                        target = pos['target']
                        
                        display_name = pos.get('contract', symbol) if is_option else symbol
                        print(f"🎯 PARTIAL EXIT: Sold {sell_qty} of {display_name} @ ${current_price:.2f} | P&L: ${partial_pnl:.2f} ({partial_pnl_pct:.1f}%) | Remaining: {remaining_qty}, new SL=breakeven, new Target=${target:.2f}")
                        
                        exits_triggered.append({
                            'symbol': symbol,
                            'contract': pos.get('contract', ''),
                            'instrument_type': pos.get('instrument_type', 'stock'),
                            'reason': 'PARTIAL_TARGET_HIT',
                            'entry_price': entry_price,
                            'exit_price': current_price,
                            'current_price': current_price,
                            'pnl': partial_pnl,
                            'pnl_pct': partial_pnl_pct,
                            'quantity': sell_qty,
                            'note': f'Partial exit {sell_qty} of {sell_qty + remaining_qty}'
                        })
                        
                        # Don't set exit_reason — position stays open with remaining qty
                        # Continue to check other exit conditions with updated values
            
            # Check exit conditions based on position side (full exit)
            if not exit_reason:
                if side == 'LONG':
                    if stop_loss > 0 and current_price <= stop_loss:
                        exit_reason = 'TRAILING_STOP' if _stop_is_trailing else 'STOP_LOSS'
                        exit_price = current_price  # Use actual fill price, not stop level
                    elif target > 0 and current_price >= target:
                        exit_reason = 'TARGET_HIT'
                        exit_price = current_price
                else:  # SHORT position
                    if stop_loss > 0 and current_price >= stop_loss:
                        exit_reason = 'TRAILING_STOP' if _stop_is_trailing else 'STOP_LOSS'
                        exit_price = current_price  # Use actual fill price, not stop level
                    elif target > 0 and current_price <= target:
                        exit_reason = 'TARGET_HIT'
                        exit_price = current_price
            
            # TIME-BASED EXIT: Close DAY TRADE positions at 3:45 PM ET
            # Only applies to positions with trade_type='day', NOT swing trades
            if not exit_reason:
                try:
                    import pytz
                    et_tz = pytz.timezone('US/Eastern')
                    now_et = datetime.now(et_tz)
                    # EOD at 3:45 PM ET (15 min before close to ensure fills)
                    is_eod = (now_et.hour == 15 and now_et.minute >= 45) or now_et.hour >= 16
                    
                    # Only close if explicitly marked as day trade
                    trade_type = pos.get('trade_type', 'swing')  # Default to swing if not specified
                    is_day_trade = (trade_type == 'day')
                    
                    if is_eod and is_day_trade:
                        exit_reason = 'END_OF_DAY'
                        exit_price = current_price
                        print(f"⏰ END_OF_DAY exit triggered for {symbol} @ ${current_price:.2f} (day trade)")
                except Exception as e:
                    print(f"⚠️ Error checking EOD exit for {symbol}: {e}")
            
            if exit_reason:
                # Calculate P&L with multiplier (×100 for options)
                if side == 'LONG':
                    pnl = (exit_price - entry_price) * quantity * multiplier
                else:
                    pnl = (entry_price - exit_price) * quantity * multiplier
                
                pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                if side == 'SHORT':
                    pnl_pct = -pnl_pct
                
                # Mark position for removal
                positions_to_remove.append(pos)
                
                # Log the exit trade with instrument info
                exit_trade = {
                    'symbol': symbol,
                    'contract': pos.get('contract', ''),
                    'action': 'SELL' if side == 'LONG' else 'BUY_TO_COVER',
                    'side': side,
                    'instrument_type': pos.get('instrument_type', 'stock'),
                    'option_type': pos.get('option_type', ''),
                    'source': pos.get('source', 'bot'),
                    'quantity': quantity,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'price': exit_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'reason': exit_reason,
                    'timestamp': datetime.now().isoformat(),
                    'auto_exit': True,
                    'trade_type': pos.get('trade_type', 'swing')
                }
                account['trades'].append(exit_trade)
                
                display_name = pos.get('contract', symbol) if is_option else symbol
                exits_triggered.append({
                    'symbol': symbol,
                    'contract': pos.get('contract', ''),
                    'instrument_type': pos.get('instrument_type', 'stock'),
                    'reason': exit_reason,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'current_price': current_price,
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'quantity': quantity
                })
                
                emoji_map = {'TARGET_HIT': '🎯', 'TRAILING_STOP': '📈', 'MAX_LOSS_GUARD': '🛡️', 'END_OF_DAY': '⏰', 'EXPIRY_PROTECTION': '⏰'}
                emoji = emoji_map.get(exit_reason, '🛑')
                print(f"{emoji} AUTO-EXIT: {display_name} - {exit_reason} | Entry: ${entry_price:.2f} → Exit: ${exit_price:.2f} | P&L: ${pnl:.2f} ({pnl_pct:.1f}%)")
        
        # Save state if any price/SL/target updates were made
        # Only persist when something actually changed to avoid unnecessary disk I/O
        if (dynamic_any_updated or _any_price_updated) and not positions_to_remove:
            with BOT_STATE_LOCK:
                save_bot_state()
        
        # Remove closed positions and recalculate balance
        if positions_to_remove:
            with BOT_STATE_LOCK:
                for pos in positions_to_remove:
                    if pos in account['positions']:
                        account['positions'].remove(pos)
                # Recalculate balance from trade history (authoritative)
                account['balance'] = recalculate_balance(account)
                save_bot_state()
    
    # =========================================================================
    # TRADING HOURS CHECK: Only scan/trade 8:30 AM - 3:00 PM CT
    # Position monitoring (Step 1 above) still runs 24/7
    # =========================================================================
    now_ct = datetime.now(ZoneInfo('America/Chicago'))
    ct_hour, ct_min = now_ct.hour, now_ct.minute

    # End-of-session analysis: run once/day at or after 3:00 PM CT
    # Focuses on stop-loss impact vs target outcomes to explain win-rate drops
    if now_ct.weekday() < 5 and (ct_hour > 15 or (ct_hour == 15 and ct_min >= 0)):
        analysis_date = now_ct.strftime('%Y-%m-%d')
        existing_analysis = bot_state.get('daily_analysis', {})
        if existing_analysis.get('date') != analysis_date:
            with BOT_STATE_LOCK:
                load_bot_state()
                account_mode = bot_state.get('account_mode', 'demo')
                account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                daily_analysis = generate_daily_trade_analysis(account, analysis_date_str=analysis_date)
                bot_state['daily_analysis'] = daily_analysis
                save_bot_state()
            print(
                f"📊 END-SESSION ANALYSIS ({analysis_date}) | "
                f"Stocks WR: {daily_analysis['stocks']['win_rate']}% | "
                f"Stock SL: {daily_analysis['stocks']['stop_loss_count']} | "
                f"Stock Targets: {daily_analysis['stocks']['target_hit_count']} | "
                f"Stock P&L: ${daily_analysis['stocks']['net_pnl']:.2f}"
            )

    market_open = (ct_hour > 8 or (ct_hour == 8 and ct_min >= 30))  # 8:30 AM CT
    market_close = ct_hour < 15  # Before 3:00 PM CT
    in_trading_hours = market_open and market_close and now_ct.weekday() < 5  # Mon-Fri only

    if not in_trading_hours:
        return jsonify({
            'success': True,
            'message': f'Outside trading hours (8:30 AM - 3:00 PM CT). Current CT: {now_ct.strftime("%I:%M %p")}. Position monitoring still active.',
            'signals': bot_state.get('signals', []),
            'daily_analysis': bot_state.get('daily_analysis'),
            'trades_executed': [],
            'exits_triggered': exits_triggered,
            'skipped_scan': True
        })

    # =========================================================================
    # STEP 2: Check scan interval / rate limiting
    # =========================================================================
    scan_interval = bot_state['settings'].get('scan_interval', 5) * 60  # Convert minutes to seconds
    last_scan = bot_state.get('last_scan')
    skip_scan = False
    skip_message = ''
    
    if last_scan:
        try:
            last_scan_time = datetime.fromisoformat(last_scan)
            seconds_since_scan = (datetime.now() - last_scan_time).total_seconds()
            # Only skip if auto_trade is OFF - when auto_trade is ON, always scan
            if seconds_since_scan < scan_interval and not bot_state.get('auto_trade', False):
                return jsonify({
                    'success': True,
                    'message': f'Next scan in {int(scan_interval - seconds_since_scan)}s',
                    'signals': bot_state.get('signals', []),
                    'trades_executed': [],
                    'exits_triggered': exits_triggered,
                    'skipped_scan': True
                })
            # Even with auto_trade, don't scan more than once per 30 seconds to avoid rate limits
            # BUT still try to execute trades from cached signals
            elif seconds_since_scan < 30:
                skip_scan = True
                skip_message = f'Rate limited, using cached signals (next scan in {int(30 - seconds_since_scan)}s)'
        except:
            pass
    
    signals = []
    
    # Run scan (unless rate limited)
    if not skip_scan:
        strategy = bot_state.get('strategy', 'trend_following')
        watchlist_name = bot_state['settings'].get('watchlist', 'top_50')
        instrument_type = bot_state['settings'].get('instrument_type', 'stocks')
        min_confidence = bot_state['settings'].get('min_confidence', 75)

        # Helper: filter out delisted/invalid symbols
        def filter_valid_symbols(symbols):
            return [s for s in symbols if resolve_symbol_or_name.is_valid_symbol(s)]

        # Determine which scanners to run based on instrument_type AND watchlist
        can_scan_stocks = instrument_type in ('stocks', 'both')
        can_scan_options = instrument_type in ('options', 'both')
        is_intraday_mode = (watchlist_name in ('intraday_stocks', 'intraday_options') 
                            or instrument_type in ('both', 'options'))
        
        run_options_scan = can_scan_options and is_intraday_mode
        run_intraday_stocks = can_scan_stocks and is_intraday_mode
        run_daily_scan = can_scan_stocks and not is_intraday_mode
        
        print(f"🤖 Auto-cycle scan: instrument_type={instrument_type}, watchlist={watchlist_name}, "
              f"options={run_options_scan}, intraday_stocks={run_intraday_stocks}, daily={run_daily_scan}")
        
        if run_options_scan:
            print(f"🤖 Running INTRADAY OPTIONS scanner")
            valid_options = filter_valid_symbols(INTRADAY_OPTIONS_UNIVERSE)
            intraday_results = run_intraday_scan_batched(
                valid_options,
                scan_intraday_option,
                max_workers=4,
                batch_size=5,
                batch_delay=0.7,
                fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
            )

            option_candidates = []
            for r in sorted(intraday_results, key=lambda x: x['score'], reverse=True)[:10]:
                confidence = min(100, max(50, int(40 + r['score'] * 4)))
                option_signal = {
                    'symbol': r['symbol'],
                    'action': 'BUY',
                    'confidence': confidence,
                    'entry': r['premium'],
                    'stop_loss': r['stop_premium'],
                    'target': r['target_1_premium'],
                    'target_2': r.get('target_2_premium'),
                    'reason': ', '.join(r['signals'][:3]),
                    'score': r['score'],
                    'direction': r['direction'],
                    'rsi': r['rsi'],
                    'volume_ratio': r['volume_ratio'],
                    'instrument_type': 'option',
                    'option_type': r['option_type'],
                    'contract': r['contract'],
                    'strike': r['strike'],
                    'expiry': r['expiry'],
                    'dte': r['dte'],
                    'premium': r['premium'],
                    'stock_price': r['price'],
                    'iv': r.get('iv', 0),
                    'open_interest': r.get('open_interest', 0),
                    'scan_type': 'intraday'
                }
                option_candidates.append(option_signal)
                if confidence >= min_confidence:
                    signals.append(option_signal)

            # In options-only mode, keep the feed populated for DISPLAY ONLY (below-threshold flag blocks auto-trade)
            if not signals and instrument_type == 'options':
                if option_candidates:
                    for oc in option_candidates[:5]:
                        oc['_below_threshold'] = True
                    signals = option_candidates[:5]
                    print(f"🤖 No options met {min_confidence}% confidence; showing top candidates (display only)")
                else:
                    live_fallback = build_live_option_fallback_signals(
                        ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
                        max_candidates=6
                    )
                    if live_fallback:
                        for lf in live_fallback:
                            lf['_below_threshold'] = True
                        signals = live_fallback
                        print(f"🤖 Using live option fallback (display only, below {min_confidence}% threshold)")
            print(f"🤖 Options scan complete: {len(intraday_results)} results, {len(signals)} signals above {min_confidence}% confidence")
        
        if run_intraday_stocks:
            print(f"🤖 Running INTRADAY STOCKS scanner")
            valid_stocks = filter_valid_symbols(INTRADAY_STOCK_UNIVERSE)
            intraday_results = run_intraday_scan_batched(
                valid_stocks,
                scan_intraday_stock,
                max_workers=5,
                batch_size=6,
                batch_delay=0.6,
                fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
            )
            for r in sorted(intraday_results, key=lambda x: x['score'], reverse=True)[:10]:
                confidence = min(100, max(50, int(40 + r['score'] * 4)))
                if confidence >= min_confidence:
                    signals.append({
                        'symbol': r['symbol'],
                        'action': 'BUY' if r['direction'] == 'BULLISH' else 'SELL',
                        'confidence': confidence,
                        'entry': r['price'],
                        'stop_loss': r['stop_loss'],
                        'target': r['target_1'],
                        'target_2': r.get('target_2'),
                        'reason': ', '.join(r['signals'][:3]),
                        'score': r['score'],
                        'direction': r['direction'],
                        'rsi': r['rsi'],
                        'volume_ratio': r['volume_ratio'],
                        'atr': r.get('atr', 0),
                        'instrument_type': 'stock',
                        'scan_type': 'intraday'
                    })
        
        if run_daily_scan:
            print(f"🤖 Running DAILY/SWING scanner on {watchlist_name}")
            symbols = WATCHLISTS.get(watchlist_name, WATCHLISTS['top_50'])
            valid_symbols = filter_valid_symbols(symbols)
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(calculate_technical_indicators, sym): sym for sym in valid_symbols}
                for future in as_completed(futures):
                    try:
                        data = future.result()
                        if data:
                            signal = analyze_for_strategy(data, strategy)
                            if signal and signal['confidence'] >= min_confidence:
                                signal['instrument_type'] = 'stock'
                                signal['scan_type'] = 'daily'
                                signals.append(signal)
                    except Exception as e:
                        print(f"Auto-scan error: {e}")
        signals.sort(key=lambda x: x['confidence'], reverse=True)
        signals = signals[:10]

        if not signals:
            fallback_signals = load_local_fallback_signals(
                is_intraday_mode=is_intraday_mode,
                instrument_type=instrument_type,
                min_confidence=min_confidence
            )
            if fallback_signals:
                signals = fallback_signals[:10]
                print("🤖 Using local cached fallback signals (live provider rate-limited)")

        # Final pass: always refresh signal entry prices to latest available quotes
        signals = refresh_signal_entries_with_live_prices(signals, force_refresh=True)

        with BOT_STATE_LOCK:
            bot_state['signals'] = signals
            bot_state['last_scan'] = datetime.now().isoformat()
            save_bot_state()
    else:
        # Use cached signals when rate limited
        signals = bot_state.get('signals', [])
        current_instrument = bot_state.get('settings', {}).get('instrument_type', 'stocks')
        if current_instrument == 'options':
            signals = [s for s in signals if s.get('instrument_type') == 'option']
            if not signals:
                min_conf_skip = bot_state['settings'].get('min_confidence', 75)
                fallback = build_live_option_fallback_signals(
                    ['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
                    max_candidates=5
                )
                if fallback:
                    for lf in fallback:
                        lf['_below_threshold'] = True
                    signals = fallback
                    skip_message = (skip_message + f' | showing live option fallback (below {min_conf_skip}% threshold, display only)') if skip_message else f'showing live option fallback (below {min_conf_skip}% threshold, display only)'
        elif current_instrument == 'stocks':
            signals = [s for s in signals if s.get('instrument_type') != 'option']
        print(f"🤖 {skip_message} - using {len(signals)} cached signals")
    
    trades_executed = []

    # De-duplicate signals by execution identity (keep highest confidence per key)
    if signals:
        deduped = {}
        for sig in signals:
            sig_key = (
                sig.get('instrument_type', 'stock'),
                sig.get('symbol', ''),
                sig.get('contract', ''),
                sig.get('action', 'BUY')
            )
            prev = deduped.get(sig_key)
            if not prev or float(sig.get('confidence', 0) or 0) > float(prev.get('confidence', 0) or 0):
                deduped[sig_key] = sig
        signals = list(deduped.values())

    # Keep bot_status/live pane aligned with what auto_cycle is actively using,
    # especially in rate-limited skip-scan paths where we build fallback signals.
    if skip_scan:
        with BOT_STATE_LOCK:
            load_bot_state()
            bot_state['signals'] = signals
            save_bot_state()
    
    # Auto-execute trades if enabled
    auto_trade_enabled = bot_state.get('auto_trade', False)
    print(f"🤖 Checking auto-trade: enabled={auto_trade_enabled}, signals_count={len(signals)}")
    
    skipped_reasons = []  # Track why signals were skipped
    daily_trade_count = 0
    today_closed_count = 0
    
    if auto_trade_enabled and signals:
        account_mode = bot_state.get('account_mode', 'demo')
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        max_positions = bot_state['settings'].get('max_positions', 5)
        position_size = bot_state['settings'].get('position_size', 1000)
        execution_signals = list(signals)

        def _get_live_available_balance():
            with BOT_STATE_LOCK:
                load_bot_state()
                acct_live = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                return float(recalculate_balance(acct_live))

        def _reserve_execution_slot(exec_key):
            now_ts = time.time()
            with AUTO_TRADE_DEDUP_LOCK:
                stale_keys = [k for k, ts in AUTO_TRADE_EXECUTION_GUARD.items() if now_ts - ts > AUTO_TRADE_DEDUP_SECONDS * 3]
                for k in stale_keys:
                    AUTO_TRADE_EXECUTION_GUARD.pop(k, None)

                last_exec = AUTO_TRADE_EXECUTION_GUARD.get(exec_key, 0)
                remaining = int(AUTO_TRADE_DEDUP_SECONDS - (now_ts - last_exec))
                if now_ts - last_exec < AUTO_TRADE_DEDUP_SECONDS:
                    return False, max(1, remaining)

                AUTO_TRADE_EXECUTION_GUARD[exec_key] = now_ts
                return True, 0
        
        # Determine what the current settings expect
        current_watchlist = bot_state['settings'].get('watchlist', 'top_50')
        current_instrument = bot_state['settings'].get('instrument_type', 'stocks')
        expect_intraday_only = (current_watchlist in ('intraday_stocks', 'intraday_options') 
                                or current_instrument in ('options', 'both'))
        
        # Check if we're past trading cutoff (3:30 PM ET for day trades)
        import pytz
        et_tz = pytz.timezone('US/Eastern')
        now_et = datetime.now(et_tz)
        is_past_day_trade_cutoff = (now_et.hour == 15 and now_et.minute >= 30) or now_et.hour >= 16
        
        current_positions = len(account.get('positions', []))
        current_symbols = [p['symbol'] for p in account.get('positions', [])]
        current_stock_sides = {
            p.get('symbol'): p.get('side', 'LONG')
            for p in account.get('positions', [])
            if p.get('instrument_type', 'stock') != 'option'
        }
        # For options, also track contracts to avoid duplicates
        current_contracts = [p.get('contract', '') for p in account.get('positions', []) if p.get('instrument_type') == 'option']
        
        # --- OVERTRADING GUARDS ---
        # 1. Max total trades per day (entries only)
        today_str = datetime.now().strftime('%Y-%m-%d')
        today_entries = [t for t in account.get('trades', []) 
                if t.get('timestamp', '').startswith(today_str) 
                and not t.get('auto_exit') 
                and t.get('action') in ('BUY', 'SELL', 'SHORT')
                and (t.get('auto_trade') or t.get('source') == 'bot')]
        daily_trade_count = len(today_entries)
        MAX_DAILY_TRADES = int(bot_state['settings'].get('max_daily_trades', 20))

        # Closed trades for today's P&L/calendar comparison (different from entry cap)
        today_closed_trades = [t for t in account.get('trades', [])
            if t.get('timestamp', '').startswith(today_str)
            and t.get('pnl') is not None
            and (t.get('auto_trade') or t.get('source') == 'bot')]
        today_closed_count = len(today_closed_trades)
        
        # 2. Track per-symbol entry count today
        from collections import Counter
        sym_trade_counts = Counter(t['symbol'] for t in today_entries)
        MAX_PER_SYMBOL = max(1, int(bot_state['settings'].get('max_per_symbol_daily', 6)))

        # Prefer symbols with fewer entries today to avoid repeatedly picking the same top ticker
        signals = sorted(
            signals,
            key=lambda s: (
                sym_trade_counts.get(s.get('symbol', ''), 0),
                -float(s.get('confidence', 0) or 0),
                s.get('symbol', '')
            )
        )
        
        # 3. Cooldown: track recent stop-loss exits (no re-entry within 30 min)
        recent_stop_losses = {}  # {symbol: last_stop_loss_timestamp}
        recent_auto_exits = {}   # {symbol: last_auto_exit_timestamp}
        for t in account.get('trades', []):
            if (t.get('timestamp', '').startswith(today_str) 
                and t.get('reason') == 'STOP_LOSS' 
                and t.get('auto_exit')):
                recent_stop_losses[t['symbol']] = t['timestamp']
            if (t.get('timestamp', '').startswith(today_str)
                and t.get('auto_exit')
                and t.get('reason') not in ('ALREADY_CLOSED',)):
                sym = t.get('symbol')
                ts = t.get('timestamp')
                if sym and ts:
                    prev = recent_auto_exits.get(sym)
                    if not prev or ts > prev:
                        recent_auto_exits[sym] = ts
        COOLDOWN_MINUTES = 30
        REENTRY_COOLDOWN_MINUTES = int(bot_state['settings'].get('reentry_cooldown_minutes', 10))
        
        if daily_trade_count >= MAX_DAILY_TRADES:
            skipped_reasons.append(f"Max daily trades reached ({MAX_DAILY_TRADES})")
            execution_signals = []  # Skip execution only; keep display signals
        
        # Early guard: if balance is too low for even one position, skip execution loop entirely
        _available_balance = float(account.get('balance', 0))
        if _available_balance < position_size * 0.5 and execution_signals:
            skipped_reasons.append(f"Insufficient balance: ${_available_balance:.2f} < 50% of position_size ${position_size}")
            print(f"⚠️ Balance too low for trading: ${_available_balance:.2f} (need ≥${position_size * 0.5:.2f})")
            execution_signals = []
        
        print(f"\U0001f916 Auto-trade check: balance=${account.get('balance', 0):.2f}, positions={current_positions}/{max_positions}, today_entries={daily_trade_count}/{MAX_DAILY_TRADES}, today_closed={today_closed_count}, past_cutoff={is_past_day_trade_cutoff}")
        
        for signal in execution_signals:
            # Check if we can add more positions
            if current_positions >= max_positions:
                skipped_reasons.append(f"{signal['symbol']}: Max positions reached ({max_positions})")
                break
            
            # CONFIDENCE GUARD: Never auto-trade signals that were below the min_confidence threshold
            min_conf_exec = bot_state['settings'].get('min_confidence', 75)
            if signal.get('_below_threshold') or (signal.get('confidence', 0) < min_conf_exec):
                skipped_reasons.append(f"{signal.get('contract', signal['symbol'])}: Below {min_conf_exec}% confidence ({signal.get('confidence', 0)}%)")
                continue
            
            is_option_signal = signal.get('instrument_type') == 'option'
            
            # Skip if already have position in this symbol (for stocks) or contract (for options)
            if is_option_signal:
                if signal.get('contract', '') in current_contracts:
                    skipped_reasons.append(f"{signal.get('contract')}: Already have this option position")
                    continue
            else:
                intended_side = 'LONG' if signal.get('action') == 'BUY' else 'SHORT'
                existing_side = current_stock_sides.get(signal['symbol'])
                if existing_side and existing_side != intended_side:
                    skipped_reasons.append(f"{signal['symbol']}: Opposite-side position already open ({existing_side})")
                    continue
            
            # --- PER-SYMBOL OVERTRADING GUARDS ---
            sym = signal['symbol']
            
            # Check per-symbol daily cap
            if sym_trade_counts.get(sym, 0) >= MAX_PER_SYMBOL:
                skipped_reasons.append(f"{sym}: Max {MAX_PER_SYMBOL} trades/day reached")
                continue
            
            # Check cooldown after stop loss (30 min)
            if sym in recent_stop_losses:
                last_sl_time = datetime.fromisoformat(recent_stop_losses[sym])
                minutes_since_sl = (datetime.now() - last_sl_time).total_seconds() / 60
                if minutes_since_sl < COOLDOWN_MINUTES:
                    skipped_reasons.append(f"{sym}: Cooldown ({COOLDOWN_MINUTES - minutes_since_sl:.0f} min left after SL)")
                    continue

            # Short cooldown after any auto-exit to prevent immediate re-entry loops
            if REENTRY_COOLDOWN_MINUTES > 0 and sym in recent_auto_exits:
                try:
                    last_exit_time = datetime.fromisoformat(recent_auto_exits[sym])
                    minutes_since_exit = (datetime.now() - last_exit_time).total_seconds() / 60
                    if minutes_since_exit < REENTRY_COOLDOWN_MINUTES:
                        skipped_reasons.append(f"{sym}: Re-entry cooldown ({REENTRY_COOLDOWN_MINUTES - minutes_since_exit:.0f} min left)")
                        continue
                except Exception:
                    pass
            
            # Determine trade type based on scan_type and time
            scan_type = signal.get('scan_type', 'daily')
            is_intraday_signal = scan_type == 'intraday'
            
            # GUARD: Reject stale daily/swing signals when intraday mode is active
            # This catches cached signals from a previous scan with different settings
            if expect_intraday_only and scan_type == 'daily':
                skipped_reasons.append(f"{signal['symbol']}: Stale daily/swing signal rejected (intraday mode active)")
                print(f"\u26a0\ufe0f Rejected stale DAILY signal for {signal['symbol']} - current mode expects intraday only")
                continue
            
            # GUARD: Reject option signals when instrument_type is 'stocks' only
            if current_instrument == 'stocks' and is_option_signal:
                skipped_reasons.append(f"{signal['symbol']}: Option signal rejected (instrument_type is stocks)")
                continue
            
            # GUARD: Reject stock signals when instrument_type is 'options' only
            if current_instrument == 'options' and not is_option_signal:
                skipped_reasons.append(f"{signal['symbol']}: Stock signal rejected (instrument_type is options)")
                continue
            
            # Skip day trades after 3:30 PM ET cutoff
            if is_intraday_signal and is_past_day_trade_cutoff:
                skipped_reasons.append(f"{signal['symbol']}: Past day trade cutoff (3:30 PM ET)")
                continue
            
            # Only execute BUY signals automatically (safer) - in demo mode allow SELL too
            if signal['action'] != 'BUY':
                if account_mode == 'demo':
                    pass  # Allow it to continue
                else:
                    skipped_reasons.append(f"{signal['symbol']}: SELL signal (auto-trade only executes BUY for safety)")
                    print(f"\U0001f916 Skipping {signal['symbol']}: SELL signal not auto-traded in real mode")
                    continue

            # Duplicate execution guard across overlapping auto-cycle requests/tabs
            exec_key = (
                signal.get('instrument_type', 'stock'),
                signal.get('symbol', ''),
                signal.get('contract', ''),
                signal.get('action', 'BUY')
            )
            if is_option_signal:
                # === Option auto-trade ===
                expiry = signal.get('expiry', '')
                min_dte_days = get_min_option_dte_days()
                if is_option_expiry_blocked(expiry, min_dte_days=min_dte_days):
                    skipped_reasons.append(f"{signal.get('contract', signal.get('symbol'))}: blocked (min DTE {min_dte_days})")
                    continue

                signal_premium = float(signal.get('premium', signal['entry']))
                premium = get_live_option_premium(
                    signal['symbol'],
                    expiry,
                    signal.get('strike', 0),
                    signal.get('option_type', 'call'),
                    fallback=signal_premium,
                    option_ticker=signal.get('option_ticker', '')
                )
                if not premium or premium <= 0:
                    premium = signal_premium

                signal_stop = float(signal.get('stop_loss', premium * 0.5) or (premium * 0.5))
                risk_pct = max(0.01, min(0.95, 1 - (signal_stop / signal_premium))) if signal_premium > 0 else 0.5
                live_stop_loss = round(premium * (1 - risk_pct), 2)

                contracts_qty = max(1, int(position_size / (premium * 100)))
                cost = contracts_qty * premium * 100

                pre_balance = _get_live_available_balance()
                if cost > pre_balance:
                    skipped_reasons.append(f"{signal.get('contract')}: Insufficient balance (need ${cost:.2f}, have ${pre_balance:.2f})")
                    continue
                
                # Options are always day trades (especially 0-2 DTE)
                option_trade_type = 'day' if signal.get('dte', 0) <= 2 else 'swing'

                reserved, remaining = _reserve_execution_slot(exec_key)
                if not reserved:
                    skipped_reasons.append(f"{signal['symbol']}: Duplicate auto-trade blocked ({remaining}s cooldown)")
                    continue
                
                with BOT_STATE_LOCK:
                    load_bot_state()
                    account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                    current_balance = float(recalculate_balance(account))
                    if cost > current_balance:
                        skipped_reasons.append(f"{signal.get('contract')}: Balance changed before execution (need ${cost:.2f}, have ${current_balance:.2f})")
                        continue

                    # Estimate ATR from signal data (no extra API call)
                    _cached_atr = premium * 0.15  # ~15% of premium for options
                    
                    position = {
                        'symbol': signal['symbol'],
                        'contract': signal.get('contract', ''),
                        'option_ticker': signal.get('option_ticker', ''),
                        'instrument_type': 'option',
                        'option_type': signal.get('option_type', 'call'),
                        'strike': signal.get('strike', 0),
                        'expiry': signal.get('expiry', ''),
                        'dte': signal.get('dte', 0),
                        'side': 'LONG',
                        'quantity': contracts_qty,
                        'entry_price': premium,
                        'current_price': premium,
                        'stop_loss': live_stop_loss,
                        'target': signal['target'],
                        'target_2': signal.get('target_2', signal['target'] * 1.5),
                        'timestamp': datetime.now().isoformat(),
                        'auto_trade': True,
                        'source': 'bot',
                        'trade_type': option_trade_type,
                        '_cached_atr': _cached_atr
                    }
                    account['positions'].append(position)
                    
                    trade = {
                        'symbol': signal['symbol'],
                        'contract': signal.get('contract', ''),
                        'option_ticker': signal.get('option_ticker', ''),
                        'action': 'BUY',
                        'side': 'LONG',
                        'instrument_type': 'option',
                        'option_type': signal.get('option_type', 'call'),
                        'quantity': contracts_qty,
                        'price': premium,
                        'cost': cost,
                        'strike': signal.get('strike', 0),
                        'expiry': signal.get('expiry', ''),
                        'confidence': signal['confidence'],
                        'reason': signal.get('reason', ''),
                        'timestamp': datetime.now().isoformat(),
                        'auto_trade': True,
                        'source': 'bot'
                    }
                    account['trades'].append(trade)
                    # Recalculate balance from trade history (authoritative)
                    account['balance'] = recalculate_balance(account)
                    save_bot_state()
                
                trades_executed.append({
                    'symbol': signal['symbol'],
                    'contract': signal.get('contract', ''),
                    'option_ticker': signal.get('option_ticker', ''),
                    'action': 'BUY',
                    'side': 'LONG',
                    'instrument_type': 'option',
                    'option_type': signal.get('option_type', 'call'),
                    'quantity': contracts_qty,
                    'price': premium,
                    'confidence': signal['confidence']
                })
                
                current_positions += 1
                current_contracts.append(signal.get('contract', ''))
                
                print(f"\U0001f916 AUTO-TRADE: \U0001f7e2 BUY {contracts_qty} x {signal.get('contract', signal['symbol'])} @ ${premium:.2f} (OPTION)")
            
            else:
                # === Stock auto-trade (existing logic) ===
                signal_entry = float(signal['entry'])
                live_price, _ = cached_get_price(signal['symbol'], period='1d', interval='1m', prepost=True)
                price = float(live_price) if live_price and live_price > 0 else signal_entry

                signal_stop = float(signal.get('stop_loss', signal_entry * 0.95) or (signal_entry * 0.95))
                risk_pct = max(0.005, min(0.5, 1 - (signal_stop / signal_entry))) if signal_entry > 0 else 0.05
                live_stop_loss = round(price * (1 - risk_pct), 2)

                quantity = int(position_size / price)
                
                if quantity < 1:
                    skipped_reasons.append(f"{signal['symbol']}: Quantity < 1 (price too high for position size)")
                    continue
                
                cost = quantity * price

                pre_balance = _get_live_available_balance()
                if cost > pre_balance:
                    skipped_reasons.append(f"{signal['symbol']}: Insufficient balance (need ${cost:.2f}, have ${pre_balance:.2f})")
                    continue
                
                side = 'LONG' if signal['action'] == 'BUY' else 'SHORT'
                
                # CRITICAL: Intraday scans MUST create day trades (close at EOD)
                # Swing trades from intraday scanner would block new trades forever
                scan_type = signal.get('scan_type', 'daily')
                trade_type = 'day' if scan_type == 'intraday' else 'swing'
                
                if scan_type == 'intraday' and trade_type != 'day':
                    print(f"⚠️ ALERT: Intraday signal for {signal['symbol']} forced to day trade (was {trade_type})")
                    trade_type = 'day'
                
                # Estimate ATR from signal's own atr field (no extra API call)
                _cached_atr_stock = signal.get('atr', price * 0.015)

                reserved, remaining = _reserve_execution_slot(exec_key)
                if not reserved:
                    skipped_reasons.append(f"{signal['symbol']}: Duplicate auto-trade blocked ({remaining}s cooldown)")
                    continue
                
                with BOT_STATE_LOCK:
                    load_bot_state()
                    account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
                    current_balance = float(recalculate_balance(account))
                    if cost > current_balance:
                        skipped_reasons.append(f"{signal['symbol']}: Balance changed before execution (need ${cost:.2f}, have ${current_balance:.2f})")
                        continue

                    position, is_new = add_or_update_position(
                        account, signal['symbol'], side, quantity, price,
                        stop_loss=live_stop_loss, target=signal['target'],
                        extra_fields={'auto_trade': True, 'source': 'bot', 'instrument_type': 'stock', 'trade_type': trade_type, '_cached_atr': _cached_atr_stock}
                    )
                    
                    trade = {
                        'symbol': signal['symbol'],
                        'action': signal['action'],
                        'side': side,
                        'instrument_type': 'stock',
                        'quantity': quantity,
                        'price': price,
                        'confidence': signal['confidence'],
                        'reason': signal.get('reason', ''),
                        'timestamp': datetime.now().isoformat(),
                        'auto_trade': True,
                        'source': 'bot',
                        'trade_type': trade_type
                    }
                    account['trades'].append(trade)
                    # Recalculate balance from trade history (authoritative)
                    account['balance'] = recalculate_balance(account)
                    save_bot_state()
                
                trades_executed.append({
                    'symbol': signal['symbol'],
                    'action': signal['action'],
                    'side': side,
                    'instrument_type': 'stock',
                    'quantity': quantity,
                    'price': price,
                    'confidence': signal['confidence']
                })
                
                current_positions += 1
                current_symbols.append(signal['symbol'])
                current_stock_sides[signal['symbol']] = side
                
                action_emoji = '\U0001f7e2' if signal['action'] == 'BUY' else '\U0001f534'
                print(f"\U0001f916 AUTO-TRADE: {action_emoji} {signal['action']} {quantity} shares of {signal['symbol']} at ${price:.2f} ({side})")
    
    return jsonify({
        'success': True,
        'signals': signals,
        'count': len(signals),
        'trades_executed': trades_executed,
        'exits_triggered': exits_triggered,
        'today_entry_count': daily_trade_count if auto_trade_enabled else 0,
        'today_closed_count': today_closed_count if auto_trade_enabled else 0,
        'skipped_reasons': skipped_reasons,
        'auto_trade_enabled': bot_state.get('auto_trade', False),
        'timestamp': bot_state.get('last_scan'),
        'skipped_scan': skip_scan,
        'message': skip_message if skip_scan else None
    })

@ai_trading_bp.route('/api/bot/trade', methods=['POST'])
def bot_trade():
    """Execute a trade"""
    global bot_state
    
    # Bot must be running (Start Trading Bot button must be ON)
    if not bot_state.get('running', False):
        return jsonify({'success': False, 'error': 'Bot is not running. Click "Start Trading Bot" first.'}), 400
    
    req = request.get_json(force=True)
    symbol = req.get('symbol')
    action = req.get('action')
    quantity = req.get('quantity')
    price = req.get('price')
    account_mode = req.get('account_mode', 'demo')
    
    if not all([symbol, action, quantity, price]):
        return jsonify({'success': False, 'error': 'Missing trade parameters'}), 400

    try:
        quantity = int(quantity)
        price = float(price)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid quantity/price format'}), 400

    if quantity <= 0 or price <= 0:
        return jsonify({'success': False, 'error': 'Quantity and price must be > 0'}), 400
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']

        live_exec_price, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
        if live_exec_price and live_exec_price > 0:
            price = float(live_exec_price)
        
        if action == 'BUY':
            cost = quantity * price
            if cost > account['balance']:
                return jsonify({'success': False, 'error': 'Insufficient balance'}), 400

            # Determine trade_type based on current settings (intraday or swing)
            settings = bot_state.get('settings', {})
            watchlist = settings.get('watchlist', 'top_50')
            instrument_type = settings.get('instrument_type', 'stocks')
            is_intraday_mode = (watchlist in ('intraday_stocks', 'intraday_options') or instrument_type in ('both', 'options'))
            trade_type = 'day' if is_intraday_mode else 'swing'

            # Add to existing position or create new one
            requested_stop = req.get('stop_loss')
            requested_target = req.get('target')
            if requested_stop is not None and float(req.get('price', 0) or 0) > 0:
                requested_price = float(req.get('price'))
                requested_stop = float(requested_stop)
                risk_pct = max(0.005, min(0.5, 1 - (requested_stop / requested_price)))
            else:
                risk_pct = 0.05

            stop_loss = round(price * (1 - risk_pct), 2)

            if requested_target is not None and float(req.get('price', 0) or 0) > 0:
                requested_price = float(req.get('price'))
                target_pct = max(0.005, min(2.0, (float(requested_target) / requested_price) - 1))
                target = round(price * (1 + target_pct), 2)
            else:
                target = round(price * 1.10, 2)

            position, is_new = add_or_update_position(
                account, symbol, 'LONG', quantity, price,
                stop_loss=stop_loss, target=target,
                extra_fields={'instrument_type': 'stock', 'trade_type': trade_type, 'source': 'manual'}
            )

            # Log trade
            trade = {
                'symbol': symbol,
                'action': 'BUY',
                'side': 'LONG',
                'instrument_type': 'stock',
                'source': 'manual',
                'quantity': quantity,
                'price': price,
                'trade_type': trade_type,
                'added_to_existing': not is_new,
                'timestamp': datetime.now().isoformat()
            }
            account['trades'].append(trade)
            
        elif action == 'SELL':
            # First, check if there's a LONG stock position to close
            # Prefer closing manual positions first when selling manually
            long_position = None
            # First pass: look for manual positions
            for pos in account['positions']:
                if pos['symbol'] == symbol and pos.get('side') == 'LONG' and pos.get('instrument_type', 'stock') != 'option' and pos.get('source', 'manual') == 'manual':
                    long_position = pos
                    break
            # Second pass: if no manual position, close any matching position (bot included)
            if not long_position:
                for pos in account['positions']:
                    if pos['symbol'] == symbol and pos.get('side') == 'LONG' and pos.get('instrument_type', 'stock') != 'option':
                        long_position = pos
                        break
            
            if long_position:
                # Close the LONG position - use live price for accuracy
                try:
                    live_price, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
                    if live_price and live_price > 0:
                        price = live_price
                except:
                    pass  # Fall back to passed-in price
                
                pnl = (price - long_position['entry_price']) * long_position['quantity']
                
                # Log trade with P&L
                trade = {
                    'symbol': symbol,
                    'action': 'SELL',
                    'side': 'LONG',
                    'source': long_position.get('source', 'manual'),
                    'quantity': long_position['quantity'],
                    'entry_price': long_position['entry_price'],
                    'price': price,
                    'pnl': pnl,
                    'pnl_pct': ((price - long_position['entry_price']) / long_position['entry_price'] * 100) if long_position['entry_price'] > 0 else 0,
                    'timestamp': datetime.now().isoformat()
                }
                account['trades'].append(trade)
                account['positions'].remove(long_position)
            else:
                # No LONG position to close - create or add to SHORT position (demo only)
                if account_mode == 'demo':
                    requested_stop = req.get('stop_loss')
                    requested_target = req.get('target')
                    if requested_stop is not None and float(req.get('price', 0) or 0) > 0:
                        requested_price = float(req.get('price'))
                        requested_stop = float(requested_stop)
                        risk_pct = max(0.005, min(0.5, (requested_stop / requested_price) - 1))
                    else:
                        risk_pct = 0.05

                    stop_loss = round(price * (1 + risk_pct), 2)

                    if requested_target is not None and float(req.get('price', 0) or 0) > 0:
                        requested_price = float(req.get('price'))
                        target_pct = max(0.005, min(2.0, 1 - (float(requested_target) / requested_price)))
                        target = round(price * (1 - target_pct), 2)
                    else:
                        target = round(price * 0.90, 2)

                    position, is_new = add_or_update_position(
                        account, symbol, 'SHORT', quantity, price,
                        stop_loss=stop_loss, target=target
                    )
                    
                    trade = {
                        'symbol': symbol,
                        'action': 'SELL',
                        'side': 'SHORT',
                        'quantity': quantity,
                        'price': price,
                        'added_to_existing': not is_new,
                        'timestamp': datetime.now().isoformat()
                    }
                    account['trades'].append(trade)
        
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': f'{action} executed for {symbol}',
        'balance': account['balance'],
        'executed_price': round(float(price), 2)
    })

@ai_trading_bp.route('/api/bot/trade_option', methods=['POST'])
def bot_trade_option():
    """Execute an option trade (buy call/put) from the intraday options scanner"""
    global bot_state

    # Bot must be running (Start Trading Bot button must be ON)
    if not bot_state.get('running', False):
        return jsonify({'success': False, 'error': 'Bot is not running. Click "Start Trading Bot" first.'}), 400

    req = request.get_json(force=True)
    symbol = req.get('symbol')
    contract = req.get('contract', '')
    option_ticker = req.get('option_ticker', req.get('contract_symbol', ''))
    option_type = req.get('option_type', 'call')  # call or put
    strike = req.get('strike', 0)
    expiry = req.get('expiry', '')
    dte = req.get('dte', 0)
    premium = req.get('premium', 0)
    contracts = req.get('contracts', 1)
    direction = req.get('direction', 'BULLISH')
    stop_premium = req.get('stop_premium', premium * 0.5)
    target_1 = req.get('target_1_premium', premium * 2.0)
    target_2 = req.get('target_2_premium', premium * 3.0)
    account_mode = req.get('account_mode', 'demo')

    try:
        premium = float(premium)
        contracts = int(contracts)
        strike = float(strike or 0)
    except Exception:
        return jsonify({'success': False, 'error': 'Invalid option trade parameters'}), 400

    if not symbol or premium <= 0 or contracts <= 0:
        return jsonify({'success': False, 'error': 'Missing option parameters'}), 400

    min_dte_days = get_min_option_dte_days()
    if is_option_expiry_blocked(expiry, min_dte_days=min_dte_days):
        return jsonify({'success': False, 'error': f'Option trade blocked by risk policy: minimum DTE is {min_dte_days} day(s)'}), 400

    live_premium = get_live_option_premium(
        symbol,
        expiry,
        strike,
        option_type,
        fallback=premium,
        option_ticker=option_ticker
    )
    if live_premium and live_premium > 0:
        premium = float(live_premium)

    try:
        stop_premium = float(stop_premium)
    except Exception:
        stop_premium = premium * 0.5

    req_premium = float(req.get('premium', premium) or premium)
    risk_pct = max(0.01, min(0.95, 1 - (stop_premium / req_premium))) if req_premium > 0 else 0.5
    stop_premium = round(premium * (1 - risk_pct), 2)

    try:
        target_1 = float(target_1)
        target_2 = float(target_2)
    except Exception:
        target_1 = premium * 2.0
        target_2 = premium * 3.0

    if req_premium > 0:
        t1_mult = max(1.01, min(10.0, target_1 / req_premium))
        t2_mult = max(1.01, min(15.0, target_2 / req_premium))
        target_1 = round(premium * t1_mult, 2)
        target_2 = round(premium * t2_mult, 2)
    else:
        target_1 = round(target_1, 2)
        target_2 = round(target_2, 2)

    cost = contracts * premium * 100  # Each contract = 100 shares

    with BOT_STATE_LOCK:
        load_bot_state()
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']

        if cost > account.get('balance', 0):
            return jsonify({'success': False, 'error': f'Insufficient balance: ${account["balance"]:.2f} < ${cost:.2f}'}), 400

        # Create option position
        position = {
            'symbol': symbol,
            'contract': contract,
            'option_ticker': option_ticker,
            'instrument_type': 'option',
            'option_type': option_type,
            'strike': strike,
            'expiry': expiry,
            'dte': dte,
            'side': 'LONG',
            'source': 'manual',
            'quantity': contracts,
            'entry_price': premium,
            'current_price': premium,
            'stop_loss': stop_premium,
            'target': target_1,
            'target_2': target_2,
            'timestamp': datetime.now().isoformat()
        }
        account['positions'].append(position)

        # Log trade
        trade = {
            'symbol': symbol,
            'contract': contract,
            'option_ticker': option_ticker,
            'action': 'BUY',
            'side': 'LONG',
            'instrument_type': 'option',
            'option_type': option_type,
            'source': 'manual',
            'quantity': contracts,
            'price': premium,
            'cost': cost,
            'strike': strike,
            'expiry': expiry,
            'timestamp': datetime.now().isoformat()
        }
        account['trades'].append(trade)
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()

    return jsonify({
        'success': True,
        'message': f'Bought {contracts} x {contract} @ ${premium:.2f}',
        'cost': cost,
        'balance': account['balance'],
        'executed_premium': round(float(premium), 2),
        'executed_stop_premium': round(float(stop_premium), 2),
        'executed_target_1_premium': round(float(target_1), 2),
        'executed_target_2_premium': round(float(target_2), 2)
    })

@ai_trading_bp.route('/api/bot/close', methods=['POST'])
def bot_close_position():
    """Close a specific position (stock or option)"""
    global bot_state
    
    req = request.get_json(force=True)
    symbol = req.get('symbol')
    account_mode = req.get('account_mode', 'demo')
    instrument_type = req.get('instrument_type', 'stock')  # 'stock' or 'option'
    contract = req.get('contract', '')  # Option contract name for matching
    source = req.get('source', '')  # 'manual' or 'bot' - helps match correct position
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        target_pos = None
        fallback_pos = None
        for pos in account['positions']:
            pos_type = pos.get('instrument_type', 'stock')
            pos_source = pos.get('source', 'manual')
            if instrument_type == 'option':
                # Match option by contract name (unique) or symbol + instrument_type
                if pos_type == 'option' and (pos.get('contract') == contract or pos.get('symbol') == symbol):
                    if source and pos_source == source:
                        target_pos = pos
                        break
                    elif not fallback_pos:
                        fallback_pos = pos
            else:
                # Match stock by symbol, excluding option positions
                if pos.get('symbol') == symbol and pos_type != 'option':
                    if source and pos_source == source:
                        target_pos = pos
                        break
                    elif not fallback_pos:
                        fallback_pos = pos
        
        if not target_pos:
            target_pos = fallback_pos
        
        if not target_pos:
            return jsonify({'success': False, 'error': f'Position not found for {symbol}'}), 404
        
        is_option = target_pos.get('instrument_type') == 'option'
        display_name = target_pos.get('contract', symbol) if is_option else symbol
        
        # Get current price
        try:
            stock_price, _ = cached_get_price(symbol, period='1d', interval='1m', prepost=True)
            if stock_price is None:
                stock_price = target_pos['current_price']
        except:
            stock_price = target_pos['current_price']
        
        if is_option:
            # For options: use current_price (premium) for P&L, multiply by 100
            price = target_pos['current_price']  # Current premium
            pnl = (price - target_pos['entry_price']) * target_pos['quantity'] * 100
        else:
            price = stock_price
            if target_pos['side'] == 'LONG':
                pnl = (price - target_pos['entry_price']) * target_pos['quantity']
            else:
                pnl = (target_pos['entry_price'] - price) * target_pos['quantity']
        
        trade = {
            'symbol': symbol,
            'contract': target_pos.get('contract', ''),
            'action': 'CLOSE',
            'side': target_pos.get('side', 'LONG'),
            'instrument_type': target_pos.get('instrument_type', 'stock'),
            'source': target_pos.get('source', 'manual'),
            'quantity': target_pos['quantity'],
            'entry_price': target_pos['entry_price'],
            'price': price,
            'pnl': pnl,
            'pnl_pct': ((price - target_pos['entry_price']) / target_pos['entry_price'] * 100) if target_pos['entry_price'] > 0 else 0,
            'timestamp': datetime.now().isoformat()
        }
        account['trades'].append(trade)
        account['positions'].remove(target_pos)
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
        
        return jsonify({
            'success': True,
            'message': f'Position closed for {display_name}',
            'pnl': pnl
        })

@ai_trading_bp.route('/api/bot/close-all', methods=['POST'])
def bot_close_all():
    """Close all positions"""
    global bot_state
    
    req = request.get_json(force=True)
    account_mode = req.get('account_mode', 'demo')
    
    with BOT_STATE_LOCK:
        load_bot_state()  # Load existing state first
        account = bot_state['demo_account'] if account_mode == 'demo' else bot_state['real_account']
        
        # Batch fetch all position prices in a single API call
        all_symbols = list(set(pos['symbol'] for pos in account['positions']))
        batch_prices = cached_batch_prices(all_symbols, period='1d', interval='1m', prepost=True) if all_symbols else {}
        
        total_pnl = 0
        for pos in list(account['positions']):
            is_option = pos.get('instrument_type') == 'option'
            stock_price = batch_prices.get(pos['symbol'], pos['current_price'])
            
            if is_option:
                price = pos['current_price']  # Option premium
                pnl = (price - pos['entry_price']) * pos['quantity'] * 100
            else:
                price = stock_price
                if pos['side'] == 'LONG':
                    pnl = (price - pos['entry_price']) * pos['quantity']
                else:
                    pnl = (pos['entry_price'] - price) * pos['quantity']
            
            total_pnl += pnl
            
            trade = {
                'symbol': pos['symbol'],
                'contract': pos.get('contract', ''),
                'action': 'CLOSE',
                'side': pos.get('side', 'LONG'),
                'instrument_type': pos.get('instrument_type', 'stock'),
                'source': pos.get('source', 'manual'),
                'quantity': pos['quantity'],
                'entry_price': pos['entry_price'],
                'price': price,
                'pnl': pnl,
                'pnl_pct': ((price - pos['entry_price']) / pos['entry_price'] * 100) if pos.get('entry_price', 0) > 0 else 0,
                'timestamp': datetime.now().isoformat()
            }
            account['trades'].append(trade)
        
        account['positions'] = []
        # Recalculate balance from trade history (authoritative)
        account['balance'] = recalculate_balance(account)
        save_bot_state()
    
    return jsonify({
        'success': True,
        'message': 'All positions closed',
        'total_pnl': total_pnl
    })


@ai_trading_bp.route('/api/bot/cleanup_duplicate_exits', methods=['POST'])
def bot_cleanup_duplicate_exits():
    """Remove duplicate synthetic exit rows (ALREADY_CLOSED) from trade history.

    Request JSON (optional):
      - account_mode: 'demo' | 'real' | 'all' (default: 'all')
      - dry_run: bool (default: True)
    """
    global bot_state

    req = request.get_json(silent=True) or {}
    account_mode = (req.get('account_mode') or 'all').lower()
    dry_run = bool(req.get('dry_run', True))

    if account_mode not in ('demo', 'real', 'all'):
        return jsonify({'success': False, 'error': 'Invalid account_mode'}), 400

    target_accounts = ['demo', 'real'] if account_mode == 'all' else [account_mode]

    with BOT_STATE_LOCK:
        load_bot_state()

        backup_name = None
        if not dry_run:
            ts = datetime.now().strftime('%Y%m%d%H%M%S')
            backup_name = f"{BOT_STATE_FILE}.bak.cleanup_duplicates.{ts}"
            try:
                Path(backup_name).write_text(json.dumps(bot_state, indent=2))
            except Exception as e:
                return jsonify({'success': False, 'error': f'Failed to create backup: {e}'}), 500

        removed_by_account = {'demo': 0, 'real': 0}

        for acct in target_accounts:
            account = bot_state['demo_account'] if acct == 'demo' else bot_state['real_account']
            trades = account.get('trades', [])
            kept = []
            removed = 0

            for trade in trades:
                if trade.get('reason') == 'ALREADY_CLOSED' and trade.get('auto_exit'):
                    removed += 1
                    continue
                kept.append(trade)

            removed_by_account[acct] = removed
            if not dry_run and removed > 0:
                account['trades'] = kept
                account['balance'] = recalculate_balance(account)

        total_removed = removed_by_account['demo'] + removed_by_account['real']

        if not dry_run and total_removed > 0:
            save_bot_state()

    return jsonify({
        'success': True,
        'dry_run': dry_run,
        'account_mode': account_mode,
        'removed': total_removed,
        'removed_by_account': removed_by_account,
        'backup_file': backup_name,
        'message': (
            f"Would remove {total_removed} duplicate synthetic exits" if dry_run
            else f"Removed {total_removed} duplicate synthetic exits"
        )
    })

# ============================================================================
# INTRADAY SCANNERS FOR AI BOT PAGE
# ============================================================================

# Intraday stock universe - highly liquid names ideal for day trading
# Keep this list SMALL (~25) to avoid Yahoo rate limits
INTRADAY_STOCK_UNIVERSE = [
    'SPY', 'QQQ', 'IWM', 'DIA',
    'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'AMZN', 'META', 'GOOGL', 'NFLX',
    'JPM', 'BAC', 'GS', 'MS', 'C',
    'XOM', 'CVX', 'UNH', 'WMT', 'COST',
    'NKE', 'DIS', 'UBER', 'PYPL', 'COIN',
    'SOFI', 'PLTR', 'SNAP', 'ROKU',
]

# Options-friendly tickers (high volume, tight bid-ask, weekly options)
# Keep this list SMALL (~30) to avoid Yahoo rate limits
INTRADAY_OPTIONS_UNIVERSE = [
    # Core ETFs with massive options volume
    'SPY', 'QQQ', 'IWM', 'DIA', 'GLD', 'SLV',
    # Mega-cap with weekly options
    'AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMD', 'AMZN', 'META', 'GOOGL', 'NFLX',
    'AVGO', 'CRM', 'ADBE', 'ORCL',
    # Financials
    'JPM', 'BAC', 'V', 'MA', 'WFC',
    # Healthcare
    'UNH', 'LLY', 'ABBV', 'MRK', 'JNJ',
    # Consumer / retail
    'WMT', 'COST', 'MCD', 'HD',
    # High-beta momentum (great for 0-DTE)
    'COIN', 'SOFI', 'PLTR', 'UBER',
]


def recalculate_intraday_sl_target(symbol, entry_price, current_sl, current_target, side='LONG', df=None):
    """
    Dynamically recalculate SL & target for an active intraday stock position
    using 5-min VWAP, ATR, and EMA data.
    
    Args:
        df: Optional pre-fetched 5-min DataFrame. If None, skips (no extra API call).
    
    Rules:
    - Only adjusts if stock is still trending in the position's direction
    - SL only moves UP for LONG (tighter protection), never DOWN
    - Target only moves UP for LONG (capturing more upside), never DOWN
    - Uses VWAP as a floor for SL (price shouldn't close below VWAP in uptrend)
    - ATR-based: SL = max(VWAP, price - 1.5*ATR), Target based on R:R floor
    
    Returns: (new_sl, new_target, signals_list) or None if no data/no update needed
    """
    try:
        if df is None or df.empty or len(df) < 50:
            return None

        # --- VWAP ---
        df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Date'] = df.index.date
        df['Cum_TV'] = df.groupby('Date').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
        df['Cum_V'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TV'] / df['Cum_V']

        # --- EMA ---
        df['EMA5'] = df['Close'].ewm(span=5, adjust=False).mean()
        df['EMA13'] = df['Close'].ewm(span=13, adjust=False).mean()

        # --- ATR ---
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()

        # --- RSI ---
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # Filter to latest session
        latest_date = df.index.date[-1]
        today = df[df.index.date == latest_date]
        if today.empty or len(today) < 5:
            return None

        bar = today.iloc[-1]
        price = float(bar['Close'])
        vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
        atr = float(bar['ATR']) if not pd.isna(bar['ATR']) else price * 0.01
        ema5 = float(bar['EMA5']) if not pd.isna(bar['EMA5']) else price
        ema13 = float(bar['EMA13']) if not pd.isna(bar['EMA13']) else price
        rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50

        signals = []

        if side == 'LONG':
            # Only update if still in uptrend: above VWAP AND EMA5 > EMA13
            above_vwap = price > vwap
            ema_bullish = ema5 > ema13

            if not above_vwap and not ema_bullish:
                return None  # Trend broken — don't revise, let existing SL/target handle it

            if above_vwap:
                signals.append('Above VWAP')
            if ema_bullish:
                signals.append('EMA Bullish')

            # --- Dynamic SL: max(VWAP, price - 1.5*ATR) ---
            # VWAP acts as a strong support floor in an uptrend
            atr_stop = round(price - atr * 1.5, 2)
            vwap_stop = round(vwap, 2)
            new_sl = max(atr_stop, vwap_stop)

            # SL must be above entry (lock in profit) when price has moved enough
            profit_pct = (price - entry_price) / entry_price if entry_price > 0 else 0
            if profit_pct > 0.005:  # >0.5% profit: don't let SL go below entry
                new_sl = max(new_sl, entry_price)

            # Only move SL UP, never down
            if new_sl <= current_sl:
                new_sl = current_sl  # Keep existing (higher) SL

            # --- Dynamic Target: risk-based minimum 1:1.5 R:R, or 2×ATR if larger ---
            is_strong = above_vwap and ema_bullish and rsi < 75
            risk_distance = abs(price - new_sl) if new_sl > 0 else atr * 1.5
            min_target_dist = risk_distance * 1.5  # Floor: 1:1.5 R:R
            target_mult = 2.5 if is_strong else 2.0
            atr_target_dist = atr * target_mult
            target_distance = max(min_target_dist, atr_target_dist)
            new_target = round(price + target_distance, 2)

            # Only move target UP, never down
            if new_target <= current_target:
                new_target = current_target  # Keep existing (higher) target

            if is_strong:
                signals.append(f'Strong trend (RSI {rsi:.0f})')
            if rsi > 75:
                signals.append(f'⚠️ Overbought RSI {rsi:.0f}')

            signals.append(f'ATR ${atr:.2f}')

        else:  # SHORT
            below_vwap = price < vwap
            ema_bearish = ema5 < ema13

            if not below_vwap and not ema_bearish:
                return None

            if below_vwap:
                signals.append('Below VWAP')
            if ema_bearish:
                signals.append('EMA Bearish')

            atr_stop = round(price + atr * 1.5, 2)
            vwap_stop = round(vwap, 2)
            new_sl = min(atr_stop, vwap_stop)

            profit_pct = (entry_price - price) / entry_price if entry_price > 0 else 0
            if profit_pct > 0.005:
                new_sl = min(new_sl, entry_price)

            # Only move SL DOWN for shorts
            if current_sl > 0 and new_sl >= current_sl:
                new_sl = current_sl

            is_strong = below_vwap and ema_bearish and rsi > 25
            risk_distance = abs(new_sl - price) if new_sl > 0 else atr * 1.5
            min_target_dist = risk_distance * 1.5
            target_mult = 2.5 if is_strong else 2.0
            atr_target_dist = atr * target_mult
            target_distance = max(min_target_dist, atr_target_dist)
            new_target = round(price - target_distance, 2)

            # Only move target DOWN for shorts
            if current_target > 0 and new_target >= current_target:
                new_target = current_target

            signals.append(f'ATR ${atr:.2f}')

        return (round(new_sl, 2), round(new_target, 2), signals)

    except Exception as e:
        print(f"⚠️ Error recalculating SL/target for {symbol}: {e}")
        return None


def scan_intraday_stock(symbol):
    """
    Intraday stock scanner using 5-min data.
    Returns a signal dict with VWAP, RSI, MACD, volume spike, and momentum.
    """
    try:
        df = cached_get_history(symbol, period='5d', interval='5m')

        if df is None or df.empty or len(df) < 50:
            return None

        # --- VWAP ---
        df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Date'] = df.index.date
        df['Cum_TV'] = df.groupby('Date').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
        df['Cum_V'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TV'] / df['Cum_V']

        # --- RSI (14-period on 5-min bars) ---
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # --- MACD (fast settings for intraday) ---
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['MACD'] = df['EMA9'] - df['EMA21']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # --- EMA crossover ---
        df['EMA5'] = df['Close'].ewm(span=5, adjust=False).mean()
        df['EMA13'] = df['Close'].ewm(span=13, adjust=False).mean()

        # --- ATR ---
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()

        # Filter to latest session
        latest_date = df.index.date[-1]
        today = df[df.index.date == latest_date]
        if today.empty or len(today) < 10:
            return None

        bar = today.iloc[-1]
        prev = today.iloc[-2]
        price = float(bar['Close'])
        vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
        rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50
        macd_hist = float(bar['MACD_Hist']) if not pd.isna(bar['MACD_Hist']) else 0
        atr = float(bar['ATR']) if not pd.isna(bar['ATR']) else price * 0.01

        avg_vol = float(today['Volume'].mean())
        cur_vol = float(bar['Volume'])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

        ema5 = float(bar['EMA5']) if not pd.isna(bar['EMA5']) else price
        ema13 = float(bar['EMA13']) if not pd.isna(bar['EMA13']) else price
        prev_ema5 = float(prev['EMA5']) if not pd.isna(prev['EMA5']) else price
        prev_ema13 = float(prev['EMA13']) if not pd.isna(prev['EMA13']) else price

        # --- Scoring (0-15) ---
        score = 0
        signals = []
        direction = None

        # VWAP position
        above_vwap = price > vwap
        if above_vwap:
            score += 2
            signals.append('Above VWAP')
        else:
            score += 1
            signals.append('Below VWAP')

        # EMA crossover
        bullish_cross = ema5 > ema13 and prev_ema5 <= prev_ema13
        bearish_cross = ema5 < ema13 and prev_ema5 >= prev_ema13
        ema_bullish = ema5 > ema13
        ema_bearish = ema5 < ema13

        if bullish_cross:
            score += 3
            signals.append('🔥 Fresh EMA Bullish Cross')
        elif ema_bullish:
            score += 2
            signals.append('✅ EMA Bullish')
        elif bearish_cross:
            score += 3
            signals.append('🔥 Fresh EMA Bearish Cross')
        elif ema_bearish:
            score += 2
            signals.append('🔴 EMA Bearish')

        # MACD momentum
        if macd_hist > 0.1:
            score += 3
            signals.append('📈 Strong MACD Momentum')
        elif macd_hist > 0:
            score += 2
            signals.append('MACD Positive')
        elif macd_hist < -0.1:
            score += 3
            signals.append('📉 Strong MACD Bearish')
        elif macd_hist < 0:
            score += 2
            signals.append('MACD Negative')

        # RSI zones
        if rsi < 30:
            score += 2
            signals.append(f'🟢 Oversold RSI {rsi:.0f}')
        elif rsi > 70:
            score += 2
            signals.append(f'🔴 Overbought RSI {rsi:.0f}')
        elif 40 < rsi < 60:
            score += 1
            signals.append(f'RSI Neutral {rsi:.0f}')

        # Volume spike
        if vol_ratio > 2.5:
            score += 3
            signals.append(f'🔥 Surge Vol {vol_ratio:.1f}x')
        elif vol_ratio > 1.5:
            score += 2
            signals.append(f'📊 High Vol {vol_ratio:.1f}x')
        else:
            score += 1

        # ===== SIGNAL CONFLICT FILTER =====
        # Reject signals where EMA and MACD disagree in direction
        # This prevents entries like "EMA Bullish + Strong MACD Bearish"
        ema_direction = 'bullish' if ema_bullish else ('bearish' if ema_bearish else 'neutral')
        macd_direction = 'bullish' if macd_hist > 0 else ('bearish' if macd_hist < 0 else 'neutral')
        
        if ema_direction != 'neutral' and macd_direction != 'neutral' and ema_direction != macd_direction:
            # EMA and MACD conflict — skip this signal
            return None

        # Determine direction
        # VWAP is the primary trend filter:
        # - BULLISH requires price above VWAP (buying into strength)
        # - BEARISH requires price below VWAP (selling into weakness)
        # This prevents shorting in a rising market or buying in a falling one
        # All 3 indicators (VWAP, EMA, MACD) must agree for entry
        bull_count = sum([above_vwap, ema_bullish, macd_hist > 0, rsi < 60])
        bear_count = sum([not above_vwap, ema_bearish, macd_hist < 0, rsi > 40])

        if bull_count >= 3 and above_vwap:
            direction = 'BULLISH'
        elif bear_count >= 3 and not above_vwap:
            direction = 'BEARISH'
        else:
            return None  # No clear direction or VWAP disagrees

        # Minimum score threshold (raised from 7 to 8 for higher quality)
        if score < 8:
            return None

        # Targets & stops
        # Ensure target distance >= stop distance for minimum 1:1 R:R
        if direction == 'BULLISH':
            stop_loss = round(max(vwap, price - atr * 1.5), 2)
            risk = abs(price - stop_loss)
            # Target at 2×ATR, but at minimum must equal the risk distance (1:1 R:R floor)
            target_distance = max(atr * 2.0, risk * 1.5)  # Minimum 1:1.5 R:R
            target_1 = round(price + target_distance, 2)
            target_2 = round(price + target_distance * 1.5, 2)
        else:
            stop_loss = round(min(vwap, price + atr * 1.5), 2)
            risk = abs(stop_loss - price)
            target_distance = max(atr * 2.0, risk * 1.5)
            target_1 = round(price - target_distance, 2)
            target_2 = round(price - target_distance * 1.5, 2)

        reward = abs(target_1 - price)
        rr_ratio = round(reward / risk, 2) if risk > 0 else 0

        return {
            'symbol': symbol,
            'direction': direction,
            'score': score,
            'price': round(price, 2),
            'vwap': round(vwap, 2),
            'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4),
            'volume_ratio': round(vol_ratio, 2),
            'atr': round(atr, 2),
            'stop_loss': stop_loss,
            'target_1': target_1,
            'target_2': target_2,
            'risk_reward': rr_ratio,
            'signals': signals,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        print(f"Intraday scan error for {symbol}: {e}")
        return None


def run_intraday_scan_batched(symbols, scan_fn, max_workers=5, batch_size=6, batch_delay=0.6, fallback_symbols=None):
    """Run intraday scans in small batches to reduce yfinance 429 rate limits.
    Returns a plain list of results (no metadata).
    """
    filtered_symbols = [s for s in symbols if s and s.upper() not in KNOWN_DELISTED]
    if not filtered_symbols:
        return []

    results = []
    for i in range(0, len(filtered_symbols), batch_size):
        chunk = filtered_symbols[i:i + batch_size]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(scan_fn, sym): sym for sym in chunk}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                except Exception as e:
                    pass  # individual failures are expected (rate limits, no data)

        if i + batch_size < len(filtered_symbols):
            time.sleep(batch_delay)

    # Fallback: if primary scan returned nothing, try core symbols one by one
    if not results and fallback_symbols:
        fallback_filtered = [s for s in fallback_symbols if s and s.upper() not in KNOWN_DELISTED]
        for symbol in fallback_filtered:
            try:
                result = scan_fn(symbol)
                if result:
                    results.append(result)
                time.sleep(max(0.8, batch_delay))
            except Exception:
                pass

    return results


def scan_intraday_option(symbol):
    """
    Intraday options scanner.
    Finds actionable 0-2 DTE call/put setups based on momentum + options chain data.
    """
    try:
        # --- 1. Momentum analysis on 5-min data (cached) ---
        df = cached_get_history(symbol, period='5d', interval='5m')
        if df is None or df.empty or len(df) < 50:
            return None

        # VWAP
        df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
        df['Date'] = df.index.date
        df['Cum_TV'] = df.groupby('Date').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
        df['Cum_V'] = df.groupby('Date')['Volume'].cumsum()
        df['VWAP'] = df['Cum_TV'] / df['Cum_V']

        # RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))

        # MACD
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['MACD'] = df['EMA9'] - df['EMA21']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # ATR
        hl = df['High'] - df['Low']
        hc = abs(df['High'] - df['Close'].shift())
        lc = abs(df['Low'] - df['Close'].shift())
        tr_vals = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df['ATR'] = tr_vals.rolling(14).mean()

        latest_date = df.index.date[-1]
        today = df[df.index.date == latest_date]
        if today.empty or len(today) < 5:
            return None

        bar = today.iloc[-1]
        price = float(bar['Close'])
        vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
        rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50
        macd_hist = float(bar['MACD_Hist']) if not pd.isna(bar['MACD_Hist']) else 0
        atr = float(bar['ATR']) if not pd.isna(bar['ATR']) else price * 0.01

        avg_vol = float(today['Volume'].mean())
        cur_vol = float(bar['Volume'])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1

        # Determine direction
        above_vwap = price > vwap
        ema_bull = float(bar['EMA9']) > float(bar['EMA21'])
        ema_bear = not ema_bull
        macd_bull = macd_hist > 0
        macd_bear = macd_hist < 0

        # === SIGNAL CONFLICT FILTER (options) ===
        # Reject if EMA and MACD disagree on direction
        if ema_bull and macd_bear:
            return None  # Conflicting signals
        if ema_bear and macd_bull:
            return None  # Conflicting signals

        bull_pts = sum([above_vwap, ema_bull, macd_bull, rsi < 65])
        bear_pts = sum([not above_vwap, ema_bear, not macd_bull, rsi > 35])

        if bull_pts >= 3:
            direction = 'BULLISH'
            opt_type = 'call'
        elif bear_pts >= 3:
            direction = 'BEARISH'
            opt_type = 'put'
        else:
            return None

        # --- 2. Options chain lookup (cached) ---
        expirations = cached_get_option_dates(symbol)
        if not expirations:
            return None

        # Find nearest expiration based on configured minimum DTE
        today_dt = datetime.now()
        min_dte_days = get_min_option_dte_days()
        best_exp = None
        for exp in expirations:
            exp_dt = datetime.strptime(exp, '%Y-%m-%d')
            dte = (exp_dt - today_dt).days
            if min_dte_days <= dte <= 3:
                best_exp = exp
                break

        if not best_exp:
            # Fallback to closest non-0DTE available
            for exp in expirations:
                try:
                    dte = (datetime.strptime(exp, '%Y-%m-%d') - today_dt).days
                    if dte >= min_dte_days:
                        best_exp = exp
                        break
                except Exception:
                    continue
            if not best_exp:
                return None

        chain = cached_get_option_chain(symbol, best_exp)
        if chain:
            opts = chain.calls if opt_type == 'call' else chain.puts
        else:
            return None

        if opts.empty:
            return None

        # Pick strike closest to current price (ATM)
        opts = opts.copy()
        opts['dist'] = abs(opts['strike'] - price)
        atm = opts.nsmallest(3, 'dist')

        # Pick the best row (highest open interest among close strikes)
        if 'openInterest' in atm.columns:
            atm = atm.fillna({'openInterest': 0})
            best_row = atm.sort_values('openInterest', ascending=False).iloc[0]
        else:
            best_row = atm.iloc[0]

        strike = float(best_row['strike'])
        premium = float(best_row['lastPrice']) if not pd.isna(best_row.get('lastPrice', None)) else 0
        bid = float(best_row['bid']) if not pd.isna(best_row.get('bid', None)) else 0
        ask = float(best_row['ask']) if not pd.isna(best_row.get('ask', None)) else 0
        iv = float(best_row['impliedVolatility']) if not pd.isna(best_row.get('impliedVolatility', None)) else 0
        oi = int(best_row.get('openInterest', 0)) if not pd.isna(best_row.get('openInterest', 0)) else 0
        opt_vol = int(best_row.get('volume', 0)) if not pd.isna(best_row.get('volume', 0)) else 0

        if premium <= 0 and ask > 0:
            premium = round((bid + ask) / 2, 2)
        if premium <= 0:
            return None

        # Scoring
        score = 0
        signals = []

        if direction == 'BULLISH':
            signals.append(f'📞 {opt_type.upper()} setup')
        else:
            signals.append(f'📉 {opt_type.upper()} setup')

        if vol_ratio > 2.0:
            score += 3
            signals.append(f'🔥 Vol Spike {vol_ratio:.1f}x')
        elif vol_ratio > 1.3:
            score += 2
            signals.append(f'📊 High Vol {vol_ratio:.1f}x')
        else:
            score += 1

        if abs(macd_hist) > 0.1:
            score += 3
            signals.append('Strong Momentum')
        elif abs(macd_hist) > 0.03:
            score += 2
        else:
            score += 1

        if iv > 0.5:
            score += 2
            signals.append(f'IV {iv*100:.0f}%')
        elif iv > 0.3:
            score += 1

        if oi > 1000:
            score += 2
            signals.append(f'OI {oi:,}')
        elif oi > 300:
            score += 1

        if opt_vol > 500:
            score += 2
            signals.append(f'Opt Vol {opt_vol:,}')
        elif opt_vol > 100:
            score += 1

        # R:R on premium
        sl_premium = round(premium * 0.50, 2)  # 50% stop
        tp_1 = round(premium * 2.0, 2)          # 2x target
        tp_2 = round(premium * 3.0, 2)          # 3x target

        dte = (datetime.strptime(best_exp, '%Y-%m-%d') - today_dt).days

        # Hard block contracts below configured minimum DTE
        if dte < min_dte_days:
            return None

        if score < 6:
            return None

        contract_name = f"{symbol} ${strike:.0f}{opt_type[0].upper()} {best_exp}"

        return {
            'symbol': symbol,
            'direction': direction,
            'option_type': opt_type,
            'contract': contract_name,
            'option_ticker': str(best_row.get('contractSymbol', '') or ''),
            'strike': strike,
            'expiry': best_exp,
            'dte': max(dte, 0),
            'premium': round(premium, 2),
            'bid': round(bid, 2),
            'ask': round(ask, 2),
            'iv': round(iv * 100, 1),
            'open_interest': oi,
            'option_volume': opt_vol,
            'score': score,
            'price': round(price, 2),
            'vwap': round(vwap, 2),
            'rsi': round(rsi, 1),
            'macd_hist': round(macd_hist, 4),
            'volume_ratio': round(vol_ratio, 2),
            'atr': round(atr, 2),
            'stop_premium': sl_premium,
            'target_1_premium': tp_1,
            'target_2_premium': tp_2,
            'signals': signals,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        print(f"Options scan error for {symbol}: {e}")
        return None


@ai_trading_bp.route('/api/bot/intraday-stocks')
def bot_intraday_stocks():
    """Intraday stock scanner for AI Bot page"""
    try:
        cache_key = 'intraday-stocks'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})

        # Return cached data if fresh
        if cache_entry['data'] is not None and cache_entry['timestamp']:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:  # 5 min cache for intraday
                return jsonify({
                    'success': True,
                    'data': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })

        if cache_entry.get('running'):
            return jsonify({
                'success': True,
                'data': cache_entry.get('data') or [],
                'running': True,
                'message': 'Intraday stock scan in progress...'
            })

        # Run scan in background
        def run_scan():
            try:
                scanner_cache[cache_key]['running'] = True
                previous_data = scanner_cache[cache_key].get('data') or []
                results = run_intraday_scan_batched(
                    INTRADAY_STOCK_UNIVERSE,
                    scan_intraday_stock,
                    max_workers=5,
                    batch_size=6,
                    batch_delay=0.6,
                    fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
                )

                if not results and previous_data:
                    results = previous_data

                results.sort(key=lambda x: x['score'], reverse=True)

                scanner_cache[cache_key]['data'] = results
                scanner_cache[cache_key]['timestamp'] = datetime.now()
                print(f"✅ Intraday stock scan complete: {len(results)} setups found")
            except Exception as e:
                print(f"❌ Intraday stock scan error: {e}")
                scanner_cache[cache_key]['data'] = []
                scanner_cache[cache_key]['timestamp'] = datetime.now()
            finally:
                scanner_cache[cache_key]['running'] = False

        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        return jsonify({
            'success': True,
            'data': cache_entry.get('data') or [],
            'running': True,
            'message': 'Intraday stock scan started...'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_trading_bp.route('/api/bot/intraday-options')
def bot_intraday_options():
    """Intraday options scanner for AI Bot page"""
    try:
        cache_key = 'intraday-options'
        cache_entry = scanner_cache.get(cache_key, {'data': None, 'timestamp': None, 'running': False})

        # Return cached data if fresh
        if cache_entry['data'] is not None and cache_entry['timestamp']:
            age = (datetime.now() - cache_entry['timestamp']).total_seconds()
            if age < 300:
                return jsonify({
                    'success': True,
                    'data': cache_entry['data'],
                    'cached': True,
                    'age_seconds': int(age)
                })

        if cache_entry.get('running'):
            return jsonify({
                'success': True,
                'data': cache_entry.get('data') or [],
                'running': True,
                'message': 'Intraday options scan in progress...'
            })

        # Run scan in background
        def run_scan():
            try:
                scanner_cache[cache_key]['running'] = True
                previous_data = scanner_cache[cache_key].get('data') or []
                results = run_intraday_scan_batched(
                    INTRADAY_OPTIONS_UNIVERSE,
                    scan_intraday_option,
                    max_workers=4,
                    batch_size=5,
                    batch_delay=0.7,
                    fallback_symbols=['SPY', 'QQQ', 'IWM', 'DIA', 'AAPL', 'MSFT', 'NVDA', 'TSLA']
                )

                if not results and previous_data:
                    results = previous_data

                results.sort(key=lambda x: x['score'], reverse=True)

                scanner_cache[cache_key]['data'] = results
                scanner_cache[cache_key]['timestamp'] = datetime.now()
                print(f"✅ Intraday options scan complete: {len(results)} setups found")
            except Exception as e:
                print(f"❌ Intraday options scan error: {e}")
                scanner_cache[cache_key]['data'] = []
                scanner_cache[cache_key]['timestamp'] = datetime.now()
            finally:
                scanner_cache[cache_key]['running'] = False

        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        return jsonify({
            'success': True,
            'data': cache_entry.get('data') or [],
            'running': True,
            'message': 'Intraday options scan started...'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


