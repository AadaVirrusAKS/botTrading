"""
Monitoring Routes - Active positions, add/delete/close positions.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import os
import time
import copy
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
import numpy as np

from services.utils import clean_nan_values
from services.market_data import (
    cached_batch_prices, cached_get_price, cached_get_option_dates,
    cached_get_option_chain, cached_get_ticker_info, fetch_quote_api_batch,
    _is_rate_limited, _log_fetch_event,
    _is_rate_limit_error, _mark_rate_limited, _mark_global_rate_limit,
    _is_expected_no_data_error
)

monitoring_bp = Blueprint("monitoring", __name__)

# ============================================================================
# POSITIONS & MONITORING ENDPOINTS
# ============================================================================

@monitoring_bp.route('/api/positions/active')
def active_positions():
    """Get currently active positions with live prices"""
    try:
        force_live = request.args.get('force_live', '0').lower() in ('1', 'true', 'yes')

        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions_dict = json.load(f)
        else:
            positions_dict = {}
        
        # Calculate stats from all positions
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        closed_pnl = 0
        wins_amounts = []
        losses_amounts = []
        closed_positions_array = []
        
        for key, pos in positions_dict.items():
            if pos.get('status') == 'closed':
                total_trades += 1
                exit_price = pos.get('exit', pos.get('current_price', pos.get('entry', 0)))
                entry_price = pos.get('entry', 0)
                quantity = pos.get('quantity', 1)
                is_option = pos.get('type') == 'option'
                direction = pos.get('direction', 'LONG').upper()

                # Options: multiply by 100 (1 contract = 100 shares)
                multiplier = 100 if is_option else 1

                # Use stored pnl if available, else calculate direction-aware pnl
                if 'pnl' in pos:
                    pnl = float(pos['pnl'])
                elif direction == 'SHORT':
                    # Short: profit when price drops (entry_sold - exit_bought)
                    pnl = (entry_price - exit_price) * quantity * multiplier
                else:
                    pnl = (exit_price - entry_price) * quantity * multiplier
                
                closed_pnl += pnl
                if pnl > 0:
                    winning_trades += 1
                    wins_amounts.append(pnl)
                else:
                    losing_trades += 1
                    losses_amounts.append(abs(pnl))
                
                # Build closed position object
                closed_pos = {
                    'position_key': key,
                    'symbol': pos.get('ticker', key),
                    'type': pos.get('type', 'stock').upper(),
                    'direction': pos.get('direction', 'LONG').upper() if not is_option else pos.get('direction', 'CALL').upper(),
                    'entry_price': float(entry_price),
                    'exit_price': float(exit_price),
                    'quantity': int(quantity),
                    'pnl': float(pnl),
                    'pnl_pct': float(pos.get('pnl_pct', ((entry_price - exit_price) / entry_price * 100 if direction == 'SHORT' else (exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0)),
                    'date_added': pos.get('date_added', ''),
                    'date_closed': pos.get('date_closed', ''),
                    'source': pos.get('source', None),
                    'close_reason': pos.get('close_reason', 'Closed'),
                }
                
                if is_option:
                    closed_pos['strike'] = float(pos.get('strike', 0))
                    closed_pos['expiration'] = pos.get('expiration', '')
                
                closed_positions_array.append(closed_pos)
        
        # Convert dictionary to array and fetch live prices
        active_stock_symbols = []
        for _, p in positions_dict.items():
            if p.get('status') == 'active' and p.get('type') != 'option':
                ticker = p.get('ticker')
                if ticker:
                    active_stock_symbols.append(ticker)
        
        stock_live_prices = {}
        if active_stock_symbols:
            unique_symbols = list(set(active_stock_symbols))
            stock_live_prices = cached_batch_prices(
                unique_symbols,
                period='5d',
                interval='5m',
                prepost=True,
                use_cache=not force_live
            )
            missing = len(unique_symbols) - len(stock_live_prices)
            if missing > 0:
                print(f"[Monitoring] ⚠️ Missing prices for {missing}/{len(unique_symbols)} symbols")

        positions_array = []
        for key, pos in positions_dict.items():
            # Only include active positions
            if pos.get('status') == 'active':
                # Determine if this is an option
                is_option = pos.get('type') == 'option'
                
                # Initialize premium_source for all positions
                premium_source = 'MARKET'  # Default for stocks
                
                # For options, fetch LIVE option premium from market
                if is_option:
                    entry_price = pos.get('entry', 0)
                    stop_loss = pos.get('stop_loss', 0)
                    target = pos.get('target_3', pos.get('target_2', pos.get('target_1', 0)))
                    
                    # Try to get live option premium
                    current_price = entry_price  # Default fallback
                    premium_source = 'ENTRY'
                    
                    # Only try live fetch if we have valid strike and it's not 0
                    strike = pos.get('strike', 0)
                    expiration = pos.get('expiration', '')
                    if strike > 0 and expiration:
                        try:
                            direction = pos.get('direction', 'CALL')
                            
                            # Get option chain for the position's expiration date
                            opt_chain = cached_get_option_chain(pos['ticker'], expiration, use_cache=not force_live)
                            if opt_chain is None:
                                raise ValueError(f"No option chain for {expiration}")
                            chain = opt_chain.calls if direction.upper() == 'CALL' else opt_chain.puts
                            
                            # Find the matching strike
                            chain['strike_diff'] = abs(chain['strike'] - strike)
                            best_match = chain.loc[chain['strike_diff'].idxmin()]
                            
                            # Use mid price for most accurate current value
                            bid = float(best_match['bid']) if best_match['bid'] > 0 else 0
                            ask = float(best_match['ask']) if best_match['ask'] > 0 else 0
                            
                            if bid > 0 and ask > 0:
                                current_price = (bid + ask) / 2
                                premium_source = 'LIVE'
                            elif best_match['lastPrice'] > 0:
                                current_price = float(best_match['lastPrice'])
                                premium_source = 'LAST'
                        except Exception as e:
                            print(f"Could not fetch live premium for {pos['ticker']} ${strike} {direction} exp {expiration}: {e}")
                            # Keep entry price as fallback
                    else:
                        print(f"⚠️  Skipping live premium for {pos['ticker']} - invalid strike: {strike} or expiration: {expiration}")
                else:
                    # Prefer batched live price; fallback to last known/current entry
                    ticker = pos.get('ticker')
                    if ticker in stock_live_prices:
                        current_price = float(stock_live_prices[ticker])
                    else:
                        current_price = float(pos.get('current_price', pos.get('entry', 0)) or pos.get('entry', 0))
                    
                    entry_price = pos.get('entry', 0)
                    stop_loss = pos.get('stop_loss', 0)
                    # Primary target - use highest available
                    target = pos.get('target_1', pos.get('target', 0))
                
                # Determine direction for stocks (auto-infer if missing)
                if is_option:
                    stock_direction = pos.get('direction', 'CALL').upper()
                else:
                    stock_direction = pos.get('direction', '').upper()
                    if not stock_direction:
                        # Auto-infer: if stop_loss > entry → SHORT, else LONG
                        if stop_loss > entry_price and entry_price > 0:
                            stock_direction = 'SHORT'
                        else:
                            stock_direction = 'LONG'
                
                # Determine if we got a LIVE or CACHED price (vs entry fallback)
                _got_live_price = (
                    (not is_option and pos.get('ticker') in stock_live_prices) or
                    (is_option and premium_source in ('LIVE', 'LAST'))
                )
                
                # Build position object with consistent field names
                position = {
                    'position_key': key,
                    'symbol': pos.get('ticker', key),
                    'type': pos.get('type', 'stock').upper(),
                    'direction': stock_direction,
                    'entry_price': float(entry_price),
                    'current_price': float(current_price),
                    'quantity': int(pos.get('quantity', 1)),
                    'stop_loss': float(stop_loss),
                    'target': float(target),
                    'status': pos.get('status', 'active'),
                    'last_price_update': datetime.now().isoformat() if _got_live_price else pos.get('last_price_update'),
                    'last_checked': datetime.now().isoformat(),
                    'price_update_status': 'updated' if _got_live_price else 'stale',
                    'date_added': pos.get('date_added', ''),
                    'date_closed': pos.get('date_closed', ''),
                    'source': pos.get('source', None),
                    'close_reason': pos.get('close_reason', ''),
                }
                
                # Add option-specific fields or stock multi-targets
                if is_option:
                    position['strike'] = float(pos.get('strike', 0))
                    position['expiration'] = pos.get('expiration', '')
                    position['premium_source'] = premium_source  # NEW: LIVE or ENTRY
                    # Add all premium targets for display
                    position['target_1'] = float(pos.get('target_1', 0))
                    position['target_2'] = float(pos.get('target_2', 0))
                    position['target_3'] = float(pos.get('target_3', 0))
                else:
                    # For stocks, also include multiple targets if they exist
                    if 'target_1' in pos or 'target_2' in pos or 'target_3' in pos:
                        position['target_1'] = float(pos.get('target_1', 0))
                        position['target_2'] = float(pos.get('target_2', 0))
                        position['target_3'] = float(pos.get('target_3', 0))
                
                positions_array.append(position)
        
        # Calculate stats
        avg_win = sum(wins_amounts) / len(wins_amounts) if wins_amounts else 0
        avg_loss = sum(losses_amounts) / len(losses_amounts) if losses_amounts else 0
        total_wins = sum(wins_amounts) if wins_amounts else 0
        total_losses = sum(losses_amounts) if losses_amounts else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else (float('inf') if total_wins > 0 else 0)
        largest_win = max(wins_amounts) if wins_amounts else 0
        
        stats = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'closed_pnl': closed_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor if profit_factor != float('inf') else 999.99,
            'largest_win': largest_win
        }
        
        response = jsonify({
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'positions': positions_array,
            'closed_positions': closed_positions_array,
            'stats': stats,
            'count': len(positions_array),
            'force_live': force_live
        })
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    except Exception as e:
        print(f"Error in active_positions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@monitoring_bp.route('/api/positions/reload', methods=['POST'])
def reload_positions():
    """Force reload/sync `active_positions.json` and notify connected clients."""
    try:
        if not os.path.exists('active_positions.json'):
            return jsonify({'success': False, 'error': 'active_positions.json not found'}), 404

        with open('active_positions.json', 'r') as f:
            positions = json.load(f)

        # Notify connected UIs to refresh
        try:
            socketio.emit('positions_updated', {'count': len(positions)})
        except Exception as e:
            print(f"Warning: could not emit positions_updated: {e}")

        return jsonify({'success': True, 'message': 'Positions reloaded', 'count': len(positions)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@monitoring_bp.route('/api/positions/restore', methods=['POST'])
def restore_position_from_backup():
    """Restore a single position from the most recent backup into active_positions.json.
    Request JSON: { 'position_key': 'KEY', 'backup_filename': optional, 'force': optional }
    """
    try:
        data = request.json or {}
        key = data.get('position_key')
        if not key:
            return jsonify({'success': False, 'error': 'position_key required'}), 400

        # Find backup file if not provided
        backup_file = data.get('backup_filename')
        if not backup_file:
            # find latest active_positions.json.bak.* in cwd
            bak_files = [f for f in os.listdir('.') if f.startswith('active_positions.json.bak')]
            if not bak_files:
                return jsonify({'success': False, 'error': 'No backup files found'}), 404
            bak_files.sort()
            backup_file = bak_files[-1]

        if not os.path.exists(backup_file):
            return jsonify({'success': False, 'error': f'Backup file not found: {backup_file}'}), 404

        with open(backup_file, 'r') as f:
            backup_data = json.load(f)

        if key not in backup_data:
            return jsonify({'success': False, 'error': f'Position {key} not found in backup {backup_file}'}), 404

        # Load current positions
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                current = json.load(f)
        else:
            current = {}

        force = bool(data.get('force', False))
        if key in current and current[key].get('status') == 'active' and not force:
            # Don't block restore — create a unique key for restored position to avoid overwriting
            timestamp_suffix = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_key = f"{key}_{timestamp_suffix}"
            restored_entry = backup_data[key]
            # mark restored source
            restored_entry['source'] = restored_entry.get('source', 'restored')
            restored_entry['date_restored_from'] = backup_file
            current[new_key] = restored_entry
            restored_key = new_key
        else:
            # Restore single position entry (overwrite or create)
            current[key] = backup_data[key]
            restored_key = key

        # Save and notify
        with open('active_positions.json', 'w') as f:
            json.dump(current, f, indent=2)

        try:
            socketio.emit('positions_updated', {'restored': restored_key})
        except Exception:
            pass

        return jsonify({'success': True, 'message': f'Restored {restored_key} from {backup_file}', 'restored_key': restored_key}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@monitoring_bp.route('/api/positions/add', methods=['POST'])
def add_position():
    """Add new position to monitoring"""
    try:
        data = request.json
        
        # Load existing positions (dict format)
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            positions = {}
        
        # Generate position key
        ticker = data.get('symbol', data.get('ticker', 'UNKNOWN'))
        position_type = data.get('type', 'stock').lower()
        
        if position_type == 'option':
            direction = data.get('direction', 'CALL').upper()
            strike = data.get('strike', 0)
            position_key = f"{ticker}_{direction}_{int(strike)}"
        else:
            position_key = ticker
        
        # Check if position already exists
        if position_key in positions:
            existing_position = positions[position_key]
            if existing_position.get('status') == 'active':
                # ADD TO EXISTING ACTIVE POSITION (weighted average entry)
                # Only merge when allowed or when source matches (avoid bot overriding manual trades)
                incoming_source = data.get('source', 'manual')
                allow_merge = bool(data.get('allow_merge', False))
                existing_source = existing_position.get('source', 'manual')

                if not allow_merge and existing_source != incoming_source:
                    # Conflict: do not merge different-source active positions
                    return jsonify({
                        'success': False,
                        'error': 'Conflict: existing active position from different source. Set allow_merge=true to override.'
                    }), 409

                # Merge allowed: compute weighted average entry
                old_qty = existing_position.get('quantity', 0)
                old_entry = existing_position.get('entry', existing_position.get('entry_premium', 0))
                new_qty = int(data.get('quantity', 1))
                new_entry = float(data.get('entry_price', data.get('entry', 0)))

                total_qty = old_qty + new_qty
                avg_entry = ((old_qty * old_entry) + (new_qty * new_entry)) / total_qty if total_qty > 0 else new_entry

                # Update existing position
                existing_position['quantity'] = total_qty
                existing_position['entry'] = round(avg_entry, 4)
                existing_position['date_added'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Update SL and targets if provided (use new values)
                if data.get('stop_loss') and float(data.get('stop_loss', 0)) > 0:
                    existing_position['stop_loss'] = float(data['stop_loss'])
                if data.get('target_1'):
                    existing_position['target_1'] = float(data['target_1'])
                if data.get('target_2'):
                    existing_position['target_2'] = float(data['target_2'])
                if data.get('target_3'):
                    existing_position['target_3'] = float(data['target_3'])
                
                # Save updated positions
                with open('active_positions.json', 'w') as f:
                    json.dump(positions, f, indent=2)
                
                return jsonify({
                    'success': True,
                    'message': f'Added to existing position: {position_key} (now {total_qty} qty @ ${avg_entry:.2f} avg)',
                    'position_key': position_key,
                    'position': existing_position,
                    'added_to_existing': True
                })
            else:
                # Position exists but is CLOSED - create new key with timestamp
                print(f"🔄 Closed position {position_key} exists. Creating new position with unique key.")
                timestamp_suffix = datetime.now().strftime('%Y%m%d_%H%M%S')
                position_key = f"{position_key}_{timestamp_suffix}"
                print(f"✅ New position key: {position_key}")
        
        # Build new position
        entry_price = float(data.get('entry_price', data.get('entry', 0)))
        
        # Calculate default SL and targets if not provided
        stop_loss = data.get('stop_loss')
        if stop_loss and float(stop_loss) > 0:
            stop_loss = float(stop_loss)
        else:
            # Default: 5% below entry for stocks, 50% for options
            if position_type == 'option':
                stop_loss = entry_price * 0.5
            else:
                stop_loss = entry_price * 0.95
        
        new_position = {
            'ticker': ticker,
            'type': position_type,
            'entry': entry_price,
            # record canonical option naming when relevant
            'entry_premium': entry_price if position_type == 'option' else None,
            'stop_loss': stop_loss,
            'quantity': int(data.get('quantity', 1)),
            'status': 'active',
            'source': data.get('source', 'manual'),
            'date_added': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Add option-specific fields
        if position_type == 'option':
            new_position['direction'] = data.get('direction', 'CALL').upper()
            new_position['strike'] = float(data.get('strike', 0))
            new_position['expiration'] = data.get('expiration', '')
            if 'current_price' in data:
                new_position['current_price'] = float(data['current_price'])
            if 'confidence' in data:
                new_position['confidence'] = int(data['confidence'])
            # Also keep legacy 'entry' field consistent
            new_position['entry'] = entry_price
            new_position['entry_premium'] = entry_price
        else:
            # Stock direction: use explicit value, or infer from SL/target vs entry
            direction = data.get('direction', '').upper()
            if not direction:
                # Auto-infer: if SL > entry → SHORT, if SL < entry → LONG
                if stop_loss > entry_price:
                    direction = 'SHORT'
                else:
                    direction = 'LONG'
            new_position['direction'] = direction
        
        # Add targets (support both single target and multiple targets)
        # Calculate defaults if not provided
        if 'target' in data:
            new_position['target_1'] = float(data['target'])
        if 'target_1' in data:
            new_position['target_1'] = float(data['target_1'])
        if 'target_2' in data:
            new_position['target_2'] = float(data['target_2'])
        if 'target_3' in data:
            new_position['target_3'] = float(data['target_3'])
        
        # Set default targets if none provided
        if 'target_1' not in new_position:
            if position_type == 'option':
                # Options: 2x, 3x, 4x entry
                new_position['target_1'] = entry_price * 2
                new_position['target_2'] = entry_price * 3
                new_position['target_3'] = entry_price * 4
            else:
                # Stocks: 10%, 20%, 30% above entry
                new_position['target_1'] = entry_price * 1.10
                new_position['target_2'] = entry_price * 1.20
                new_position['target_3'] = entry_price * 1.30
        
        # Add optional fields
        if 'notes' in data:
            new_position['notes'] = data['notes']
        
        # Add to positions dict
        positions[position_key] = new_position
        
        # Save positions
        with open('active_positions.json', 'w') as f:
            json.dump(positions, f, indent=2)
        
        return jsonify({
            'success': True,
            'message': f'Position added: {position_key}',
            'position_key': position_key,
            'position': new_position
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@monitoring_bp.route('/api/positions/delete/<int:index>', methods=['DELETE'])
def delete_position(index):
    """Delete position by index (legacy support - converts to dict lookup)"""
    try:
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            return jsonify({'success': False, 'error': 'No positions found'}), 404
        
        # Convert to list to support index-based deletion
        if isinstance(positions, dict):
            position_keys = list(positions.keys())
            if 0 <= index < len(position_keys):
                position_key = position_keys[index]
                deleted = positions.pop(position_key)
                
                # Save updated positions
                with open('active_positions.json', 'w') as f:
                    json.dump(positions, f, indent=2)
                
                return jsonify({
                    'success': True,
                    'message': f'Position deleted: {deleted.get("ticker")}',
                    'deleted': deleted
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid position index'}), 400
        else:
            # Legacy list format (shouldn't happen but handle it)
            if 0 <= index < len(positions):
                deleted = positions.pop(index)
                
                # Save updated positions
                with open('active_positions.json', 'w') as f:
                    json.dump(positions, f, indent=2)
                
                return jsonify({
                    'success': True,
                    'message': f'Position deleted: {deleted.get("symbol")}',
                    'deleted': deleted
                })
            else:
                return jsonify({'success': False, 'error': 'Invalid position index'}), 400
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@monitoring_bp.route('/api/positions/delete/<position_key>', methods=['DELETE'])
def delete_position_by_key(position_key):
    """Delete position by key"""
    try:
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            return jsonify({'success': False, 'error': 'No positions found'}), 404
        
        if position_key in positions:
            deleted = positions.pop(position_key)
            
            # Save updated positions
            with open('active_positions.json', 'w') as f:
                json.dump(positions, f, indent=2)
            
            return jsonify({
                'success': True,
                'message': f'Position deleted: {deleted.get("ticker")}',
                'deleted': deleted
            })
        else:
            return jsonify({'success': False, 'error': 'Position not found'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@monitoring_bp.route('/api/positions/close', methods=['POST'])
def close_position():
    """Close position at specified price"""
    try:
        data = request.json
        position_key = data.get('position_key')
        exit_price = float(data.get('exit_price', 0))
        reason = data.get('reason', 'Manual Close')
        
        if os.path.exists('active_positions.json'):
            with open('active_positions.json', 'r') as f:
                positions = json.load(f)
        else:
            return jsonify({'success': False, 'error': 'No positions found'}), 404
        
        if position_key not in positions:
            return jsonify({'success': False, 'error': 'Position not found'}), 404
        
        position = positions[position_key]
        
        # Calculate P&L
        # Support legacy naming: entry or entry_premium
        entry_price = position.get('entry', position.get('entry_premium', 0))
        quantity = position.get('quantity', 1)
        is_option = position.get('type') == 'option'
        
        # Options: multiply by 100 (1 contract = 100 shares)
        multiplier = 100 if is_option else 1
        pnl = (exit_price - entry_price) * quantity * multiplier
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        
        # Update position to closed
        position['status'] = 'closed'
        position['exit'] = exit_price
        # Write secondary naming for option workflows
        if position.get('type') == 'option':
            position['exit_premium'] = exit_price
            # Ensure entry_premium exists for downstream tools
            position['entry_premium'] = position.get('entry_premium', position.get('entry'))
        position['date_closed'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        position['close_reason'] = reason
        position['pnl'] = pnl
        position['pnl_pct'] = pnl_pct
        
        positions[position_key] = position
        
        # Save updated positions
        with open('active_positions.json', 'w') as f:
            json.dump(positions, f, indent=2)
        
        return jsonify({
            'success': True,
            'message': f'Position closed: {position.get("ticker")}',
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'position': position
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

