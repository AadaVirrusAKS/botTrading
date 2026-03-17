"""
Bot Engine - AI Trading Bot state management, position management,
balance calculations, and signal processing.
"""
import os
import json
import threading
import time
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
import yfinance as yf
import numpy as np
import pandas as pd

from services.market_data import (
    cached_batch_prices, cached_get_price, cached_get_history,
    cached_get_option_dates, cached_get_option_chain, cached_get_ticker_info,
    fetch_quote_api_batch, _is_rate_limited, _log_fetch_event,
    _is_rate_limit_error, _is_expected_no_data_error, _mark_rate_limited,
    _mark_global_rate_limit,
    _price_cache, _price_cache_lock
)
from services.symbols import resolve_symbol_or_name, is_valid_symbol_cached

# ============================================================================
# AI TRADING BOT API ENDPOINTS
# ============================================================================

# Bot state management
bot_state = {
    'running': False,
    'auto_trade': False,  # Auto-execute trades when signals meet criteria
    'alpaca_execution': False,  # Execute trades via Alpaca paper trading
    'account_mode': 'demo',
    'strategy': 'trend_following',
    'settings': {
        'watchlist': 'top_50',
        'scan_interval': 5,
        'min_confidence': 85,
        'min_option_dte_days': 1,
        'max_positions': 3,
        'max_daily_trades': 20,
        'max_per_symbol_daily': 2,
        'max_per_symbol_daily': 4,
        'reentry_cooldown_minutes': 10,
        'position_size': 4000,
        'stop_loss': 2.0,
        'take_profit': 4.0,
        'trailing_stop': 'atr',
        'partial_profit_taking': True,
        'close_0dte_before_expiry': True,
        'max_loss_per_trade': 500,
        'min_option_premium': 1.0,
        'market_regime_filter': True
    },
    'last_scan': None,
    'signals': [],
    'demo_account': {
        'balance': 10000.0,
        'initial_balance': 10000.0,
        'positions': [],
        'trades': []
    },
    'real_account': {
        'balance': 0,
        'positions': [],
        'trades': []
    }
}

from config import DATA_DIR
BOT_STATE_FILE = os.path.join(DATA_DIR, 'ai_bot_state.json')
BOT_STATE_LOCK = threading.RLock()

# Per-user state support
_active_user_id = None


def _bg_owner_user_id():
    """Return the user ID whose bot the background engine should operate on.
    Uses the last active user, falling back to user 1 (primary owner)."""
    if _active_user_id is not None:
        return _active_user_id
    # Default: primary user (ID 1)
    path = os.path.join(DATA_DIR, 'bot_state_user_1.json')
    if os.path.exists(path):
        return 1
    return None


def _user_state_path(user_id):
    """Return the bot-state file path for a specific user."""
    return os.path.join(DATA_DIR, f'bot_state_user_{user_id}.json')


def set_active_user(user_id):
    """Switch bot state file to the given user's file.
    Resets in-memory state to defaults so load_bot_state() loads cleanly.
    Thread-safe via BOT_STATE_LOCK."""
    global BOT_STATE_FILE, _active_user_id, bot_state
    with BOT_STATE_LOCK:
        if user_id == _active_user_id:
            return  # Already on this user
        if user_id is None:
            BOT_STATE_FILE = os.path.join(DATA_DIR, 'ai_bot_state.json')
            _active_user_id = None
        else:
            BOT_STATE_FILE = _user_state_path(user_id)
            _active_user_id = user_id
        # Reset in-memory state so load_bot_state() won't be blocked by the
        # "0 trades vs N trades" safety guard when switching between users
        bot_state.update({
            'running': False, 'auto_trade': False, 'alpaca_execution': False,
            'account_mode': 'demo', 'strategy': 'trend_following',
            'last_scan': None, 'signals': [],
            'demo_account': {'balance': 10000.0, 'initial_balance': 10000.0, 'positions': [], 'trades': []},
            'real_account': {'balance': 0, 'positions': [], 'trades': []}
        })


def init_user_bot_state(user_id):
    """Create a fresh $10,000 demo bot state file for a new user."""
    path = _user_state_path(user_id)
    if os.path.exists(path):
        return  # Already exists
    fresh_state = {
        'running': False,
        'auto_trade': False,
        'alpaca_execution': False,
        'account_mode': 'demo',
        'strategy': 'trend_following',
        'settings': {
            'watchlist': 'top_50',
            'scan_interval': 5,
            'min_confidence': 85,
            'min_option_dte_days': 1,
            'max_positions': 3,
            'max_daily_trades': 20,
            'max_per_symbol_daily': 4,
            'reentry_cooldown_minutes': 10,
            'position_size': 4000,
            'stop_loss': 2.0,
            'take_profit': 4.0,
            'trailing_stop': 'atr',
            'partial_profit_taking': True,
            'close_0dte_before_expiry': True,
            'max_loss_per_trade': 500,
            'min_option_premium': 1.0,
            'market_regime_filter': True
        },
        'last_scan': None,
        'signals': [],
        'demo_account': {
            'balance': 10000.0,
            'initial_balance': 10000.0,
            'positions': [],
            'trades': []
        },
        'real_account': {
            'balance': 0,
            'positions': [],
            'trades': []
        }
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(fresh_state, f, indent=2)
    print(f"✅ Initialized $10,000 demo account for user {user_id}")
AUTO_TRADE_DEDUP_LOCK = threading.Lock()
AUTO_TRADE_EXECUTION_GUARD = {}  # key -> unix timestamp
AUTO_TRADE_DEDUP_SECONDS = 60

def recalculate_balance(account):
    """
    Recalculate account balance from trade history to prevent drift.
    
    Balance = initial_balance + realized_P&L - cost_of_LONG_positions + proceeds_from_SHORT_positions
    
    This is the authoritative calculation. Incremental balance tracking (adding/subtracting
    on each trade) is fragile — if any position is removed without a proper counterbalancing
    trade, the balance drifts permanently. This function corrects that.
    """
    initial_balance = account.get('initial_balance', 10000.0)
    trades = account.get('trades', [])
    positions = account.get('positions', [])
    
    # Sum all realized P&L from closed trades (exit trades that have pnl field)
    realized_pnl = 0.0
    for t in trades:
        pnl = t.get('pnl')
        if pnl is not None:
            realized_pnl += pnl
    
    # Calculate net capital tied up in open positions
    # LONG positions: we SPENT money (subtract from balance)
    # SHORT positions: we RECEIVED money (add to balance) - but owe it back on cover
    net_position_cost = 0.0
    for pos in positions:
        is_option = pos.get('instrument_type') == 'option'
        multiplier = 100 if is_option else 1
        qty = pos.get('quantity', 0)
        entry = pos.get('entry_price', 0)
        cost = entry * qty * multiplier
        
        if pos.get('side') == 'SHORT':
            net_position_cost -= cost  # Short sale proceeds (money IN)
        else:
            net_position_cost += cost  # Long purchase cost (money OUT)
    
    correct_balance = initial_balance + realized_pnl - net_position_cost
    return round(correct_balance, 2)

def generate_daily_trade_analysis(account, analysis_date_str=None):
    """Generate daily trade performance analysis with focus on stop-loss impact."""
    if analysis_date_str is None:
        analysis_date_str = datetime.now().strftime('%Y-%m-%d')

    trades = account.get('trades', [])
    today_trades = [t for t in trades if t.get('timestamp', '').startswith(analysis_date_str)]

    # Realized exits/partials (entries typically don't carry pnl)
    realized = [
        t for t in today_trades
        if t.get('pnl') is not None and t.get('action') in ('SELL', 'BUY_TO_COVER', 'CLOSE', 'PARTIAL_SELL', 'PARTIAL_COVER')
    ]

    stock_realized = [t for t in realized if t.get('instrument_type', 'stock') != 'option']
    option_realized = [t for t in realized if t.get('instrument_type') == 'option']

    sl_reasons = {'STOP_LOSS', 'TRAILING_STOP', 'MAX_LOSS_GUARD'}
    target_reasons = {'TARGET_HIT', 'PARTIAL_TARGET_HIT'}

    def summarize(bucket):
        wins = sum(1 for t in bucket if float(t.get('pnl', 0) or 0) > 0)
        losses = sum(1 for t in bucket if float(t.get('pnl', 0) or 0) < 0)
        breakeven = sum(1 for t in bucket if float(t.get('pnl', 0) or 0) == 0)
        decisive = wins + losses
        win_rate = round((wins / decisive) * 100, 1) if decisive > 0 else 0.0
        net_pnl = round(sum(float(t.get('pnl', 0) or 0) for t in bucket), 2)

        stop_loss_trades = [t for t in bucket if t.get('reason') in sl_reasons]
        target_trades = [t for t in bucket if t.get('reason') in target_reasons]

        stop_loss_count = len(stop_loss_trades)
        target_count = len(target_trades)
        stop_loss_pnl = round(sum(float(t.get('pnl', 0) or 0) for t in stop_loss_trades), 2)
        target_pnl = round(sum(float(t.get('pnl', 0) or 0) for t in target_trades), 2)

        largest_win = max((float(t.get('pnl', 0) or 0) for t in bucket), default=0.0)
        largest_loss = min((float(t.get('pnl', 0) or 0) for t in bucket), default=0.0)

        return {
            'total': len(bucket),
            'wins': wins,
            'losses': losses,
            'breakeven': breakeven,
            'win_rate': win_rate,
            'net_pnl': net_pnl,
            'stop_loss_count': stop_loss_count,
            'target_hit_count': target_count,
            'stop_loss_pnl': stop_loss_pnl,
            'target_pnl': target_pnl,
            'largest_win': round(largest_win, 2),
            'largest_loss': round(largest_loss, 2)
        }

    all_summary = summarize(realized)
    stock_summary = summarize(stock_realized)
    option_summary = summarize(option_realized)

    notes = []
    if stock_summary['total'] > 0:
        if stock_summary['stop_loss_count'] > stock_summary['target_hit_count']:
            notes.append('Stock exits hit stop-loss more often than targets today.')
        if stock_summary['stop_loss_pnl'] < 0 and abs(stock_summary['stop_loss_pnl']) > abs(stock_summary['target_pnl']):
            notes.append('Stock stop-loss losses outweighed stock target gains today.')
        if stock_summary['win_rate'] < 45:
            notes.append('Stock win rate is weak today; consider tighter signal filtering before auto-entry.')

    return {
        'date': analysis_date_str,
        'generated_at': datetime.now().isoformat(),
        'overall': all_summary,
        'stocks': stock_summary,
        'options': option_summary,
        'notes': notes
    }

def reconcile_orphan_positions(account, acct_key=''):
    """
    On startup, detect and auto-close orphan positions to prevent discrepancies.

    Catches:
    1. Day-trade positions from a PREVIOUS day that were never closed (e.g. server
       restarted and the EOD sweep was missed).
    2. Positions whose matching exit trade already exists in the trade history
       (duplicate in positions list).
    3. Expired option positions (expiry date in the past).

    Any orphan found is removed from positions[] and a synthetic CLOSE exit trade
    is appended to trades[] so the P&L ledger stays consistent.
    Returns the number of orphans fixed.
    """
    positions = account.get('positions', [])
    trades = account.get('trades', [])
    if not positions:
        return 0

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore

    now_et = datetime.now(ZoneInfo('US/Eastern'))
    today_str = now_et.strftime('%Y-%m-%d')

    orphans = []  # (position, reason)

    for pos in list(positions):
        symbol = pos.get('symbol', '?')
        side = pos.get('side', 'LONG')
        qty = pos.get('quantity', 0)
        entry_price = pos.get('entry_price', 0)
        instrument_type = pos.get('instrument_type', 'stock')
        trade_type = pos.get('trade_type', 'swing')
        source = pos.get('source', 'bot')

        # --- 1. Stale day-trade from a previous day ---
        pos_ts = pos.get('timestamp', '')
        pos_date = pos_ts[:10] if len(pos_ts) >= 10 else ''
        if trade_type == 'day' and pos_date and pos_date < today_str:
            orphans.append((pos, f'STALE_DAY_TRADE (opened {pos_date}, missed EOD close)'))
            continue

        # --- 2. Position already has a matching exit in trade history ---
        exit_actions = ('SELL', 'BUY_TO_COVER', 'CLOSE')
        has_exit = False
        for t in trades:
            if (t.get('symbol') == symbol and
                    t.get('action') in exit_actions and
                    t.get('source', '') == source and
                    t.get('instrument_type', 'stock') == instrument_type and
                    t.get('quantity', 0) == qty and
                    t.get('pnl') is not None):
                # Match by timestamp proximity — exit must be AFTER position open
                if pos_ts and t.get('timestamp', '') > pos_ts:
                    has_exit = True
                    break
        if has_exit:
            orphans.append((pos, 'ALREADY_CLOSED (exit trade exists in history)'))
            continue

        # --- 3. Expired option position ---
        if instrument_type == 'option':
            expiry_str = pos.get('expiry', '')
            if expiry_str:
                try:
                    expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                    if expiry_date < now_et.date():
                        orphans.append((pos, f'EXPIRED_OPTION (expiry {expiry_str})'))
                        continue
                except ValueError:
                    pass

    # --- Close each orphan and record a synthetic exit trade ---
    fixed = 0
    for pos, reason in orphans:
        symbol = pos.get('symbol', '?')
        side = pos.get('side', 'LONG')
        qty = pos.get('quantity', 0)
        entry_price = pos.get('entry_price', 0)
        instrument_type = pos.get('instrument_type', 'stock')
        is_option = instrument_type == 'option'
        multiplier = 100 if is_option else 1

        # If an exit already exists in history, only remove stale position.
        # Do not append another synthetic exit trade (prevents duplicate ALREADY_CLOSED logs).
        if reason.startswith('ALREADY_CLOSED'):
            if pos in positions:
                positions.remove(pos)
            display = pos.get('contract', symbol) if is_option else symbol
            print(f"🔧 STARTUP RECONCILE: Removed duplicate orphan {display} ({reason})")
            fixed += 1
            continue

        # Use entry_price for stale exits (market is closed, no live quote)
        exit_price = entry_price
        pnl = 0.0  # Neutral P&L when we can't get a real price

        # For expired options, assume worthless
        if 'EXPIRED_OPTION' in reason:
            exit_price = 0.0
            if side == 'LONG':
                pnl = -entry_price * qty * multiplier
            else:
                pnl = entry_price * qty * multiplier

        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
        if side == 'SHORT':
            pnl_pct = -pnl_pct

        exit_trade = {
            'symbol': symbol,
            'contract': pos.get('contract', ''),
            'action': 'SELL' if side == 'LONG' else 'BUY_TO_COVER',
            'side': side,
            'instrument_type': instrument_type,
            'option_type': pos.get('option_type', ''),
            'source': pos.get('source', 'bot'),
            'quantity': qty,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'price': exit_price,
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 2),
            'reason': reason.split(' ')[0],  # e.g. STALE_DAY_TRADE
            'timestamp': datetime.now().isoformat(),
            'auto_exit': True,
            'reconciled_on_startup': True,
            'trade_type': pos.get('trade_type', 'swing')
        }
        trades.append(exit_trade)

        if pos in positions:
            positions.remove(pos)

        display = pos.get('contract', symbol) if is_option else symbol
        print(f"🔧 STARTUP RECONCILE: Closed orphan {display} ({reason}) | P&L: ${pnl:.2f}")
        fixed += 1

    return fixed


def _try_load_json(filepath):
    """Attempt to load and validate a bot state JSON file. Returns dict or None."""
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        # Sanity check: must be a dict with expected keys
        if not isinstance(data, dict):
            return None
        # A valid state file with trade history will have demo_account with trades list
        return data
    except (json.JSONDecodeError, IOError, OSError) as e:
        print(f"⚠️ Failed to load {filepath}: {e}")
        return None


def load_bot_state():
    global bot_state
    if not os.path.exists(BOT_STATE_FILE):
        return bot_state

    saved = _try_load_json(BOT_STATE_FILE)

    # If main file is corrupt or empty, try backups in order
    if saved is None:
        print("⚠️ Main state file corrupt or unreadable, trying backups...")
        for suffix in ('.bak1', '.bak2', '.bak3'):
            backup_path = BOT_STATE_FILE + suffix
            if os.path.exists(backup_path):
                saved = _try_load_json(backup_path)
                if saved is not None:
                    print(f"✅ Restored state from {backup_path}")
                    break
    if saved is None:
        print("❌ All state files unreadable — keeping defaults")
        return bot_state

    # Guard against loading a reset/empty state over a richer one already in memory
    saved_trades = len(saved.get('demo_account', {}).get('trades', []))
    mem_trades = len(bot_state.get('demo_account', {}).get('trades', []))
    if saved_trades == 0 and mem_trades > 10:
        print(f"⚠️ State file has 0 trades but memory has {mem_trades} — skipping load to protect data")
        return bot_state

    bot_state.update(saved)

    # Backfill new settings keys for older state files
    if 'settings' not in bot_state:
        bot_state['settings'] = {}
    if 'min_option_dte_days' not in bot_state['settings']:
        bot_state['settings']['min_option_dte_days'] = 1
    if 'max_per_symbol_daily' not in bot_state['settings']:
        bot_state['settings']['max_per_symbol_daily'] = 6
    if 'reentry_cooldown_minutes' not in bot_state['settings']:
        bot_state['settings']['reentry_cooldown_minutes'] = 10
    if 'max_loss_per_trade' not in bot_state['settings']:
        bot_state['settings']['max_loss_per_trade'] = 500
    # Migrate: backfill 'source' field on existing positions
    _needs_save = False
    for acct_key in ('demo_account', 'real_account'):
        acct = bot_state.get(acct_key, {})
        for pos in acct.get('positions', []):
            if 'source' not in pos:
                pos['source'] = 'bot' if pos.get('auto_trade') else 'manual'
                _needs_save = True

        # --- Reconcile orphan positions on every load/restart ---
        orphan_count = reconcile_orphan_positions(acct, acct_key)
        if orphan_count > 0:
            print(f"🔧 [{acct_key}] Reconciled {orphan_count} orphan position(s) on startup")
            _needs_save = True

        # Recalculate balance from trade history to prevent drift
        correct_balance = recalculate_balance(acct)
        if abs(acct.get('balance', 0) - correct_balance) > 0.01:
            print(f"🔧 [{acct_key}] Balance corrected: ${acct.get('balance', 0):.2f} → ${correct_balance:.2f} (drift of ${acct.get('balance', 0) - correct_balance:.2f})")
            acct['balance'] = correct_balance
            _needs_save = True

    # Only persist if reconciliation actually changed something
    if _needs_save:
        save_bot_state()
    return bot_state

def save_bot_state():
    # Rotate backups before writing (keeps last 3 good copies)
    import shutil
    if os.path.exists(BOT_STATE_FILE):
        try:
            # Only back up if the current file has meaningful data (> 1KB)
            if os.path.getsize(BOT_STATE_FILE) > 1024:
                for i in (3, 2, 1):
                    src = BOT_STATE_FILE + (f'.bak{i-1}' if i > 1 else '')
                    dst = BOT_STATE_FILE + f'.bak{i}'
                    if i == 1:
                        src = BOT_STATE_FILE
                    if os.path.exists(src):
                        shutil.copy2(src, dst)
        except OSError as e:
            print(f"⚠️ Backup rotation failed: {e}")

    # Write to temp file first, then atomic rename to prevent corruption
    tmp_path = BOT_STATE_FILE + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(bot_state, f, indent=2, default=str)
    os.replace(tmp_path, BOT_STATE_FILE)


# =============================
# ALPACA EXECUTION HELPERS
# =============================
def is_alpaca_execution_enabled():
    """Check if Alpaca execution is enabled and configured."""
    if not bot_state.get('alpaca_execution', False):
        return False
    try:
        from services.alpaca_service import is_configured, ALPACA_AVAILABLE
        return ALPACA_AVAILABLE and is_configured()
    except ImportError:
        return False


def execute_alpaca_entry(symbol, qty, side, order_type='market', stop_loss=None, take_profit=None):
    """
    Execute a BUY/SELL entry order on Alpaca paper trading.
    Returns dict with 'success', 'order', and 'error' keys.
    """
    try:
        from services.alpaca_service import place_order
        alpaca_side = 'buy' if side == 'LONG' else 'sell'
        result = place_order(
            symbol=symbol,
            qty=qty,
            side=alpaca_side,
            order_type=order_type,
            stop_loss=round(stop_loss, 2) if stop_loss else None,
            take_profit=round(take_profit, 2) if take_profit else None,
        )
        print(f"📈 ALPACA ORDER: {alpaca_side.upper()} {qty} {symbol} → {result.get('status', 'unknown')} (ID: {result.get('id', 'N/A')})")
        return {'success': True, 'order': result}
    except Exception as e:
        print(f"❌ ALPACA ORDER FAILED: {symbol} - {e}")
        return {'success': False, 'error': str(e)}


def execute_alpaca_exit(symbol, qty=None):
    """
    Close a position on Alpaca paper trading (full or partial).
    Returns dict with 'success', 'order'/'error' keys.
    """
    try:
        from services.alpaca_service import close_position
        result = close_position(symbol, qty=float(qty) if qty else None)
        print(f"📉 ALPACA CLOSE: {symbol} → {result.get('status', 'closed')}")
        return {'success': True, 'order': result}
    except Exception as e:
        print(f"❌ ALPACA CLOSE FAILED: {symbol} - {e}")
        return {'success': False, 'error': str(e)}


# Load saved state on module initialization
load_bot_state()

# Watchlist definitions (simple hardcoded presets — no master merge)
WATCHLISTS = {
    'top_50': [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'UNH', 'JPM',
        'V', 'JNJ', 'XOM', 'WMT', 'MA', 'PG', 'HD', 'CVX', 'MRK', 'ABBV',
        'LLY', 'BAC', 'KO', 'PEP', 'COST', 'AVGO', 'TMO', 'MCD', 'DIS', 'CSCO',
        'ABT', 'ACN', 'VZ', 'ADBE', 'WFC', 'INTC', 'NKE', 'CRM', 'AMD', 'CMCSA',
        'TXN', 'PM', 'UPS', 'NEE', 'MS', 'HON', 'RTX', 'ORCL', 'QCOM', 'BMY'
    ],
    'sp500': [
        'SPY', 'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK-B', 'UNH'
    ],
    'tech': [
        'AAPL', 'MSFT', 'GOOGL', 'META', 'NVDA', 'AMD', 'CRM', 'ADBE', 'INTC', 'ORCL',
        'CSCO', 'IBM', 'AVGO', 'TXN', 'QCOM', 'NOW', 'SNOW', 'PLTR', 'NET', 'DDOG'
    ],
    'custom': []
}

def calculate_technical_indicators(symbol):
    """Calculate technical indicators for a stock (uses cached history)"""
    try:
        # Filter out delisted/invalid symbols before any API call
        if not resolve_symbol_or_name.is_valid_symbol(symbol):
            return None
        df = cached_get_history(symbol, period='3mo', interval='1d')
        if df is None or df.empty or len(df) < 30:
            return None
        
        # Calculate indicators
        df['SMA20'] = df['Close'].rolling(20).mean()
        df['SMA50'] = df['Close'].rolling(50).mean() if len(df) >= 50 else df['SMA20']
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
        
        # RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # MACD
        df['MACD'] = df['Close'].ewm(span=12).mean() - df['Close'].ewm(span=26).mean()
        df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()
        
        # Bollinger Bands
        df['BB_Middle'] = df['Close'].rolling(20).mean()
        bb_std = df['Close'].rolling(20).std()
        df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
        df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)
        
        # ATR
        high_low = df['High'] - df['Low']
        high_close = abs(df['High'] - df['Close'].shift())
        low_close = abs(df['Low'] - df['Close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR'] = tr.rolling(14).mean()
        
        # Volume
        df['Vol_SMA20'] = df['Volume'].rolling(20).mean()
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        return {
            'symbol': symbol,
            'price': float(latest['Close']),
            'change': float((latest['Close'] - prev['Close']) / prev['Close'] * 100),
            'volume': int(latest['Volume']),
            'vol_avg': float(latest['Vol_SMA20']) if not pd.isna(latest['Vol_SMA20']) else 0,
            'sma20': float(latest['SMA20']) if not pd.isna(latest['SMA20']) else 0,
            'sma50': float(latest['SMA50']) if not pd.isna(latest['SMA50']) else 0,
            'ema9': float(latest['EMA9']) if not pd.isna(latest['EMA9']) else 0,
            'ema21': float(latest['EMA21']) if not pd.isna(latest['EMA21']) else 0,
            'rsi': float(latest['RSI']) if not pd.isna(latest['RSI']) else 50,
            'macd': float(latest['MACD']) if not pd.isna(latest['MACD']) else 0,
            'macd_signal': float(latest['MACD_Signal']) if not pd.isna(latest['MACD_Signal']) else 0,
            'bb_upper': float(latest['BB_Upper']) if not pd.isna(latest['BB_Upper']) else 0,
            'bb_lower': float(latest['BB_Lower']) if not pd.isna(latest['BB_Lower']) else 0,
            'atr': float(latest['ATR']) if not pd.isna(latest['ATR']) else 0
        }
    except Exception as e:
        print(f"Error calculating indicators for {symbol}: {e}")
        return None

# Attach cached is_valid_symbol to resolve_symbol_or_name for global use
resolve_symbol_or_name.is_valid_symbol = is_valid_symbol_cached

def analyze_for_strategy(data, strategy):
    """Analyze stock data based on selected strategy"""
    if not data:
        return None
    
    signals = []
    confidence = 50
    action = None
    trend = 'neutral'
    
    price = data['price']
    rsi = data['rsi']
    macd = data['macd']
    macd_signal = data['macd_signal']
    ema9 = data['ema9']
    ema21 = data['ema21']
    sma20 = data['sma20']
    bb_upper = data['bb_upper']
    bb_lower = data['bb_lower']
    volume = data['volume']
    vol_avg = data['vol_avg']
    atr = data['atr']
    
    if strategy == 'trend_following':
        # EMA crossover
        ema_bullish = ema9 > ema21
        ema_bearish = ema9 < ema21
        if ema_bullish:
            signals.append('EMA9 > EMA21 (bullish)')
            confidence += 10
            trend = 'bullish'
        elif ema_bearish:
            signals.append('EMA9 < EMA21 (bearish)')
            confidence -= 10
            trend = 'bearish'
        
        # MACD
        macd_bullish = macd > macd_signal and macd > 0
        macd_bearish = macd < macd_signal and macd < 0
        if macd_bullish:
            signals.append('MACD bullish crossover')
            confidence += 15
        elif macd_bearish:
            signals.append('MACD bearish crossover')
            confidence -= 15
        
        # === SIGNAL CONFLICT FILTER (trend_following) ===
        # Reject if EMA and MACD disagree on direction
        if ema_bullish and macd_bearish:
            return None  # EMA says up, MACD says down - conflicting
        if ema_bearish and macd_bullish:
            return None  # EMA says down, MACD says up - conflicting
        
        # RSI
        if 30 < rsi < 70:
            if rsi > 50:
                signals.append(f'RSI bullish ({rsi:.0f})')
                confidence += 5
            else:
                signals.append(f'RSI bearish ({rsi:.0f})')
                confidence -= 5
        
        # Trend strength
        if price > sma20 and ema9 > ema21:
            action = 'BUY'
        elif price < sma20 and ema9 < ema21:
            action = 'SELL'
            
    elif strategy == 'mean_reversion':
        # Bollinger Bands
        if price < bb_lower:
            signals.append('Price below lower BB (oversold)')
            confidence += 20
            trend = 'bullish'
            action = 'BUY'
        elif price > bb_upper:
            signals.append('Price above upper BB (overbought)')
            confidence += 20
            trend = 'bearish'
            action = 'SELL'
        
        # RSI extremes
        if rsi < 30:
            signals.append(f'RSI oversold ({rsi:.0f})')
            confidence += 15
            if not action:
                action = 'BUY'
                trend = 'bullish'
        elif rsi > 70:
            signals.append(f'RSI overbought ({rsi:.0f})')
            confidence += 15
            if not action:
                action = 'SELL'
                trend = 'bearish'
                
    elif strategy == 'breakout':
        # Volume breakout
        vol_ratio = volume / vol_avg if vol_avg > 0 else 1
        
        if vol_ratio > 1.5:
            signals.append(f'High volume ({vol_ratio:.1f}x avg)')
            confidence += 10
            
            # Price breakout
            if price > bb_upper:
                signals.append('Breaking upper resistance')
                confidence += 20
                action = 'BUY'
                trend = 'bullish'
            elif price < bb_lower:
                signals.append('Breaking lower support')
                confidence += 20
                action = 'SELL'
                trend = 'bearish'
        
        # MACD momentum
        if abs(macd) > abs(macd_signal) * 1.5:
            signals.append('Strong MACD momentum')
            confidence += 10
            
    elif strategy == 'scalping':
        # Quick signals for scalping
        if rsi < 35 and macd > macd_signal:
            signals.append('RSI bounce setup')
            confidence += 15
            action = 'BUY'
            trend = 'bullish'
        elif rsi > 65 and macd < macd_signal:
            signals.append('RSI rejection setup')
            confidence += 15
            action = 'SELL'
            trend = 'bearish'
        
        # EMA proximity
        if abs(price - ema9) / price < 0.005:
            signals.append('Price at EMA9 support/resistance')
            confidence += 10
    
    if not action or confidence < 60:
        return None
    
    # Calculate stop loss and target based on ATR
    atr_mult = 1.5 if strategy == 'scalping' else 2.0
    stop_loss = price - (atr * atr_mult) if action == 'BUY' else price + (atr * atr_mult)
    target = price + (atr * atr_mult * 2) if action == 'BUY' else price - (atr * atr_mult * 2)
    
    return {
        'symbol': data['symbol'],
        'action': action,
        'entry': price,
        'stop_loss': stop_loss,
        'target': target,
        'confidence': min(confidence, 95),
        'trend': trend,
        'reason': '; '.join(signals),
        'rsi': rsi,
        'macd': macd,
        'volume_ratio': volume / vol_avg if vol_avg > 0 else 1
    }

def add_or_update_position(account, symbol, side, quantity, price, stop_loss=None, target=None, extra_fields=None):
    """
    Add a new position or update existing position for same symbol/side/source.
    Uses weighted average for entry price when adding to existing position.
    Only merges positions of the same instrument_type AND source (bot vs manual).
    Returns: (position, is_new)
    """
    # Determine instrument type and source from extra_fields
    instrument_type = (extra_fields or {}).get('instrument_type', 'stock')
    source = (extra_fields or {}).get('source', 'manual')
    
    # Find existing position with same symbol, side, instrument type, AND source
    existing_pos = None
    for pos in account.get('positions', []):
        if (pos.get('symbol') == symbol and 
            pos.get('side') == side and 
            pos.get('instrument_type', 'stock') == instrument_type and
            pos.get('source', 'manual') == source):
            existing_pos = pos
            break
    
    if existing_pos:
        # Update existing position with weighted average entry
        old_qty = existing_pos.get('quantity', 0)
        old_entry = existing_pos.get('entry_price', price)
        new_qty = old_qty + quantity
        
        # Weighted average entry price
        avg_entry = ((old_qty * old_entry) + (quantity * price)) / new_qty
        
        existing_pos['quantity'] = new_qty
        existing_pos['entry_price'] = round(avg_entry, 4)
        existing_pos['current_price'] = price
        existing_pos['timestamp'] = datetime.now().isoformat()
        
        # Update SL/target if provided (use new values)
        if stop_loss is not None:
            existing_pos['stop_loss'] = float(stop_loss)
        if target is not None:
            existing_pos['target'] = float(target)
        
        # Add any extra fields
        if extra_fields:
            existing_pos.update(extra_fields)
        
        return existing_pos, False  # Not a new position
    else:
        # Create new position
        position = {
            'symbol': symbol,
            'side': side,
            'instrument_type': instrument_type,
            'source': source,
            'quantity': quantity,
            'entry_price': price,
            'current_price': price,
            'stop_loss': float(stop_loss) if stop_loss else price * (0.95 if side == 'LONG' else 1.05),
            'target': float(target) if target else price * (1.10 if side == 'LONG' else 0.90),
            'timestamp': datetime.now().isoformat()
        }
        
        # Add any extra fields
        if extra_fields:
            position.update(extra_fields)
        
        account['positions'].append(position)
        return position, True  # New position

def update_positions_with_live_prices(positions, force_live=False):
    """Update positions with current market prices.
    For stocks: batch fetch prices via cached_batch_prices (single API call).
    For options: fetch option premium from option chains.
    When force_live=True, bypasses all caches to guarantee fresh data.
    """
    if not positions:
        return positions
    
    use_cache = not force_live
    update_source = 'live' if force_live else 'cache_or_live'
    
    # Separate stock and option positions
    stock_positions = [p for p in positions if p.get('instrument_type', 'stock') != 'option']
    option_positions = [p for p in positions if p.get('instrument_type') == 'option']
    
    now_iso = datetime.now().isoformat()

    def _mark_price_update(pos, status, reason=''):
        pos['price_update_mode'] = 'force_live' if force_live else 'cache_enabled'
        pos['price_update_source'] = update_source
        pos['price_update_status'] = status
        pos['price_update_reason'] = reason
        # Always record when the system last attempted to refresh this position
        pos['last_checked'] = now_iso

    for pos in stock_positions:
        _mark_price_update(pos, 'pending', 'awaiting_batch_quote')
    for pos in option_positions:
        _mark_price_update(pos, 'pending', 'awaiting_option_chain')

    # --- Update stock positions (BATCHED — single API call for all symbols) ---
    stock_symbols = list(set(p['symbol'] for p in stock_positions))
    if stock_symbols:
        stock_prices = cached_batch_prices(stock_symbols, period='5d', interval='5m', prepost=True, use_cache=use_cache)
        quote_api_prices = fetch_quote_api_batch(stock_symbols) if force_live else {}
        for pos in stock_positions:
            if pos['symbol'] in stock_prices:
                pos['current_price'] = stock_prices[pos['symbol']]
                pos['last_price_update'] = now_iso
                _mark_price_update(pos, 'updated', 'stock_quote_refreshed')
            elif pos['symbol'] in quote_api_prices:
                pos['current_price'] = float(quote_api_prices[pos['symbol']])
                pos['last_price_update'] = now_iso
                _mark_price_update(pos, 'updated', 'stock_quote_api_refreshed')
            else:
                fallback_price, _ = cached_get_price(pos['symbol'], period='5d', interval='5m', prepost=True, use_cache=use_cache)
                if fallback_price is not None:
                    pos['current_price'] = float(fallback_price)
                    pos['last_price_update'] = now_iso
                    _mark_price_update(pos, 'updated', 'stock_quote_fallback_refreshed')
                else:
                    info = cached_get_ticker_info(pos['symbol'])
                    info_price = None
                    if info:
                        info_price = info.get('regularMarketPrice') or info.get('currentPrice')
                    if info_price is not None:
                        pos['current_price'] = float(info_price)
                        pos['last_price_update'] = now_iso
                        _mark_price_update(pos, 'updated', 'stock_info_refreshed')
                    else:
                        _mark_price_update(pos, 'skipped', 'stock_quote_unavailable')
    else:
        for pos in stock_positions:
            _mark_price_update(pos, 'skipped', 'no_stock_symbols')
    
    # --- Update option positions with live premium ---
    option_by_symbol = {}
    for pos in option_positions:
        sym = pos['symbol']
        if sym not in option_by_symbol:
            option_by_symbol[sym] = []
        option_by_symbol[sym].append(pos)
    
    # Batch fetch underlying prices for all option symbols too
    option_symbols = list(option_by_symbol.keys())
    if option_symbols:
        underlying_prices = cached_batch_prices(option_symbols, period='5d', interval='5m', prepost=True, use_cache=use_cache)
    else:
        underlying_prices = {}
    
    def _normalize_option_ticker(value):
        if not value:
            return ''
        return str(value).strip().upper()

    def _extract_option_ticker_from_contract(contract_value):
        if not contract_value:
            return ''
        contract_str = str(contract_value).strip().upper()
        if re.fullmatch(r'[A-Z]{1,6}\d{6}[CP]\d{8}', contract_str):
            return contract_str
        return ''

    for symbol, opt_positions in option_by_symbol.items():
        try:
            underlying_price = underlying_prices.get(symbol) 
            available_dates = cached_get_option_dates(symbol)
            
            for pos in opt_positions:
                expiry = pos.get('expiry', '')
                strike = pos.get('strike', 0)
                opt_type = str(pos.get('option_type', 'call') or 'call').lower()
                option_ticker = _normalize_option_ticker(
                    pos.get('option_ticker') or
                    pos.get('contract_symbol') or
                    _extract_option_ticker_from_contract(pos.get('contract', ''))
                )
                
                if underlying_price is not None:
                    pos['underlying_price'] = underlying_price
                    pos['last_price_update'] = now_iso
                
                if not expiry:
                    _mark_price_update(pos, 'skipped', 'no_expiry_on_position')
                    continue
                
                # If we got dates and the expiry isn't in them → genuinely unavailable.
                # If dates list is empty (likely rate-limited), try fetching chain directly.
                if available_dates and expiry not in available_dates:
                    _mark_price_update(pos, 'skipped', 'expiry_not_listed_for_symbol')
                    continue

                chain = cached_get_option_chain(symbol, expiry, use_cache=use_cache)
                if not chain:
                    _mark_price_update(pos, 'skipped', 'option_chain_unavailable')
                    continue

                df = chain.calls if opt_type == 'call' else chain.puts
                if df is None or df.empty:
                    _mark_price_update(pos, 'skipped', 'option_side_empty')
                    continue

                row = None
                if option_ticker and 'contractSymbol' in df.columns:
                    ticker_match = df[df['contractSymbol'].astype(str).str.upper() == option_ticker]
                    if not ticker_match.empty:
                        row = ticker_match.iloc[0]
                elif strike and 'strike' in df.columns:
                    # Legacy bootstrap path: resolve exact strike on exact expiry only once,
                    # then persist the concrete contractSymbol for strict future matching.
                    strike_match = df[abs(df['strike'].astype(float) - float(strike)) < 0.01]
                    if not strike_match.empty and 'contractSymbol' in strike_match.columns:
                        row = strike_match.iloc[0]
                        pos['option_ticker'] = str(row.get('contractSymbol', '')).upper()

                if row is None:
                    _mark_price_update(pos, 'skipped', 'contract_symbol_not_found')
                    continue

                last = float(row.get('lastPrice', 0) or 0)
                bid = float(row.get('bid', 0) or 0)
                ask = float(row.get('ask', 0) or 0)

                if last > 0:
                    pos['current_price'] = last
                elif bid > 0 and ask > 0:
                    pos['current_price'] = round((bid + ask) / 2, 2)
                elif ask > 0:
                    pos['current_price'] = ask
                elif bid > 0:
                    pos['current_price'] = bid
                else:
                    _mark_price_update(pos, 'skipped', 'no_trade_or_quote_price')
                    continue

                pos['current_bid'] = bid
                pos['current_ask'] = ask
                pos['last_price_update'] = now_iso
                _mark_price_update(pos, 'updated', 'option_contract_refreshed')
        except Exception as e:
            for pos in opt_positions:
                _mark_price_update(pos, 'error', f"options_update_exception: {type(e).__name__}")
            if not _is_expected_no_data_error(e):
                _log_fetch_event('position-options-error', symbol, f"Error fetching options data for {symbol}: {e}", cooldown=180)
    
    return positions

def get_live_option_premium(symbol, expiry, strike, option_type='call', fallback=None, option_ticker=''):
    """Fetch a best-effort live option premium from cached option chain.
    Returns fallback when live premium is unavailable.
    """
    try:
        available_dates = cached_get_option_dates(symbol)
        if not available_dates:
            return float(fallback) if fallback else None

        if not expiry or expiry not in available_dates:
            return float(fallback) if fallback else None

        chain = cached_get_option_chain(symbol, expiry)
        if not chain:
            return float(fallback) if fallback else None

        df = chain.calls if option_type == 'call' else chain.puts
        if df is None or df.empty:
            return float(fallback) if fallback else None

        row = None
        normalized_ticker = str(option_ticker or '').strip().upper()
        if normalized_ticker and 'contractSymbol' in df.columns:
            ticker_match = df[df['contractSymbol'].astype(str).str.upper() == normalized_ticker]
            if not ticker_match.empty:
                row = ticker_match.iloc[0]

        if row is None:
            return float(fallback) if fallback else None
        last = float(row.get('lastPrice', 0) or 0)
        bid = float(row.get('bid', 0) or 0)
        ask = float(row.get('ask', 0) or 0)

        if last > 0:
            return last
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        if ask > 0:
            return ask
        if bid > 0:
            return bid
    except Exception:
        pass

    return float(fallback) if fallback else None


def is_zero_dte_or_expired(expiry_str):
    """True when option expiry is today or in the past."""
    if not expiry_str:
        return False
    try:
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return expiry_date <= datetime.now().date()
    except Exception:
        return False


def get_min_option_dte_days():
    """Return minimum allowed DTE for option entries."""
    try:
        return max(1, int(bot_state.get('settings', {}).get('min_option_dte_days', 1)))
    except Exception:
        return 1


def get_option_dte(expiry_str):
    """Return DTE as integer days, or None when expiry is invalid."""
    if not expiry_str:
        return None
    try:
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
        return (expiry_date - datetime.now().date()).days
    except Exception:
        return None


def is_option_expiry_blocked(expiry_str, min_dte_days=None):
    """True when option expiry is below configured minimum DTE."""
    dte = get_option_dte(expiry_str)
    if dte is None:
        return False
    if min_dte_days is None:
        min_dte_days = get_min_option_dte_days()
    return dte < max(1, int(min_dte_days))

def refresh_signal_entries_with_live_prices(signals, force_refresh=False):
    """Refresh signal entry/SL/target values with latest live prices.

    - Stocks: refresh `entry` from live stock quote and preserve risk/reward proportions.
    - Options: refresh `entry`/`premium` from option premium and preserve SL/target multipliers.
    """
    if not signals:
        return signals

    now_iso = datetime.now().isoformat()

    stock_symbols = sorted({
        s.get('symbol') for s in signals
        if s.get('instrument_type', 'stock') != 'option' and s.get('symbol')
    })

    if force_refresh and stock_symbols:
        with _price_cache_lock:
            for sym in stock_symbols:
                _price_cache.pop(sym, None)

    stock_prices = cached_batch_prices(stock_symbols, period='5d', interval='5m', prepost=True) if stock_symbols else {}

    for signal in signals:
        instrument_type = signal.get('instrument_type', 'stock')
        action = signal.get('action', 'BUY')

        if instrument_type != 'option':
            symbol = signal.get('symbol')
            live_entry = stock_prices.get(symbol)
            if not live_entry or live_entry <= 0:
                continue

            old_entry = float(signal.get('entry', live_entry) or live_entry)
            old_stop = float(signal.get('stop_loss', old_entry * (0.95 if action == 'BUY' else 1.05)) or (old_entry * (0.95 if action == 'BUY' else 1.05)))
            old_target = float(signal.get('target', old_entry * (1.10 if action == 'BUY' else 0.90)) or (old_entry * (1.10 if action == 'BUY' else 0.90)))

            if old_entry <= 0:
                old_entry = float(live_entry)

            if action == 'SELL':
                risk_pct = max(0.005, min(0.5, (old_stop / old_entry) - 1)) if old_entry > 0 else 0.05
                reward_pct = max(0.005, min(2.0, 1 - (old_target / old_entry))) if old_entry > 0 else 0.10
                signal['stop_loss'] = round(float(live_entry) * (1 + risk_pct), 2)
                signal['target'] = round(float(live_entry) * (1 - reward_pct), 2)
            else:
                risk_pct = max(0.005, min(0.5, 1 - (old_stop / old_entry))) if old_entry > 0 else 0.05
                reward_pct = max(0.005, min(2.0, (old_target / old_entry) - 1)) if old_entry > 0 else 0.10
                signal['stop_loss'] = round(float(live_entry) * (1 - risk_pct), 2)
                signal['target'] = round(float(live_entry) * (1 + reward_pct), 2)

            signal['entry'] = round(float(live_entry), 2)
            signal['live_price_update'] = now_iso
            continue

        symbol = signal.get('symbol')
        strike = signal.get('strike', 0)
        expiry = signal.get('expiry', '')
        option_type = signal.get('option_type', 'call')
        old_entry = float(signal.get('entry', signal.get('premium', 0)) or 0)
        old_stop = float(signal.get('stop_loss', old_entry * 0.5) or (old_entry * 0.5))
        old_target = float(signal.get('target', old_entry * 2.0) or (old_entry * 2.0))

        if force_refresh and symbol:
            with _price_cache_lock:
                _price_cache.pop(symbol, None)

        live_premium = get_live_option_premium(
            symbol,
            expiry,
            strike,
            option_type,
            fallback=old_entry,
            option_ticker=signal.get('option_ticker', '')
        )
        if not live_premium or live_premium <= 0:
            continue

        if old_entry <= 0:
            old_entry = float(live_premium)

        risk_pct = max(0.01, min(0.95, 1 - (old_stop / old_entry))) if old_entry > 0 else 0.5
        target_mult = max(1.01, min(10.0, old_target / old_entry)) if old_entry > 0 else 2.0

        signal['entry'] = round(float(live_premium), 2)
        signal['premium'] = round(float(live_premium), 2)
        signal['stop_loss'] = round(float(live_premium) * (1 - risk_pct), 2)
        signal['target'] = round(float(live_premium) * target_mult, 2)

        underlying = stock_prices.get(symbol)
        if underlying and underlying > 0:
            signal['stock_price'] = round(float(underlying), 2)

        signal['live_price_update'] = now_iso

    return signals

