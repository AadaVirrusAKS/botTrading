"""
Alpaca Paper Trading Service - Wrapper for Alpaca Markets API.

Provides account management, order placement, position tracking,
and order history via Alpaca's paper trading environment.

Environment variables required:
    ALPACA_API_KEY    - Your Alpaca API key
    ALPACA_SECRET_KEY - Your Alpaca secret key
"""
import os
import json
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

# Alpaca SDK imports
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest,
        LimitOrderRequest,
        StopOrderRequest,
        StopLimitOrderRequest,
        GetOrdersRequest,
    )
    from alpaca.trading.enums import (
        OrderSide,
        TimeInForce,
        OrderType,
        OrderStatus,
        QueryOrderStatus,
    )
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False

# =============================
# CONFIGURATION
# =============================
from config import DATA_DIR
CONFIG_FILE = os.path.join(DATA_DIR, 'alpaca_config.json')
CONFIG_LOCK = threading.Lock()


def _load_config() -> Dict:
    """Load Alpaca config from file (keys stored locally, never committed)."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}


def _save_config(config: Dict):
    """Save Alpaca config to file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


def get_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Get Alpaca credentials from env vars or config file."""
    api_key = os.environ.get('ALPACA_API_KEY')
    secret_key = os.environ.get('ALPACA_SECRET_KEY')
    if api_key and secret_key:
        return api_key, secret_key
    config = _load_config()
    return config.get('api_key'), config.get('secret_key')


def save_credentials(api_key: str, secret_key: str):
    """Save Alpaca credentials to config file."""
    with CONFIG_LOCK:
        config = _load_config()
        config['api_key'] = api_key
        config['secret_key'] = secret_key
        _save_config(config)


def is_configured() -> bool:
    """Check if Alpaca credentials are available."""
    api_key, secret_key = get_credentials()
    return bool(api_key and secret_key)


# =============================
# CLIENT MANAGEMENT
# =============================
_client: Optional[object] = None
_client_lock = threading.Lock()


def _get_client() -> object:
    """Get or create the Alpaca TradingClient (paper=True)."""
    global _client
    if not ALPACA_AVAILABLE:
        raise RuntimeError("alpaca-py is not installed. Run: pip install alpaca-py")
    with _client_lock:
        if _client is None:
            api_key, secret_key = get_credentials()
            if not api_key or not secret_key:
                raise ValueError("Alpaca credentials not configured. Set ALPACA_API_KEY and ALPACA_SECRET_KEY.")
            _client = TradingClient(api_key, secret_key, paper=True)
        return _client


def reset_client():
    """Reset the cached client (e.g. after credential change)."""
    global _client
    with _client_lock:
        _client = None


def validate_credentials(api_key: str, secret_key: str) -> Tuple[bool, str]:
    """Test if the provided credentials are valid by fetching account info."""
    if not ALPACA_AVAILABLE:
        return False, "alpaca-py is not installed. Run: pip install alpaca-py"
    try:
        test_client = TradingClient(api_key, secret_key, paper=True)
        account = test_client.get_account()
        return True, f"Connected! Account: {account.account_number}"
    except Exception as e:
        return False, f"Authentication failed: {str(e)}"


# =============================
# ACCOUNT
# =============================
def get_account() -> Dict:
    """Get Alpaca paper trading account details."""
    client = _get_client()
    account = client.get_account()
    return {
        'account_number': str(account.account_number),
        'status': str(account.status),
        'currency': str(account.currency),
        'cash': float(account.cash),
        'portfolio_value': float(account.portfolio_value),
        'buying_power': float(account.buying_power),
        'equity': float(account.equity),
        'last_equity': float(account.last_equity),
        'long_market_value': float(account.long_market_value),
        'short_market_value': float(account.short_market_value),
        'initial_margin': float(account.initial_margin),
        'maintenance_margin': float(account.maintenance_margin),
        'daytrade_count': int(account.daytrade_count),
        'pattern_day_trader': bool(account.pattern_day_trader),
        'trading_blocked': bool(account.trading_blocked),
        'account_blocked': bool(account.account_blocked),
        'day_pnl': float(account.equity) - float(account.last_equity),
        'day_pnl_pct': ((float(account.equity) - float(account.last_equity)) / float(account.last_equity) * 100) if float(account.last_equity) > 0 else 0,
    }


# =============================
# POSITIONS
# =============================
def get_positions() -> List[Dict]:
    """Get all open positions."""
    client = _get_client()
    positions = client.get_all_positions()
    result = []
    for pos in positions:
        result.append({
            'symbol': str(pos.symbol),
            'qty': float(pos.qty),
            'side': str(pos.side),
            'avg_entry_price': float(pos.avg_entry_price),
            'current_price': float(pos.current_price),
            'market_value': float(pos.market_value),
            'cost_basis': float(pos.cost_basis),
            'unrealized_pl': float(pos.unrealized_pl),
            'unrealized_plpc': float(pos.unrealized_plpc) * 100,
            'change_today': float(pos.change_today) * 100 if pos.change_today else 0,
        })
    return result


def close_position(symbol: str, qty: Optional[float] = None) -> Dict:
    """Close a position (fully or partially). Verifies position exists to prevent accidental shorts."""
    client = _get_client()
    
    # Verify the position exists on Alpaca before selling
    try:
        position = client.get_open_position(symbol)
        current_qty = float(position.qty)
    except Exception:
        # Position does not exist on Alpaca — do NOT submit a sell order
        print(f"⚠️ ALPACA: No open position for {symbol} — skipping close to prevent accidental short")
        return {'symbol': symbol, 'status': 'no_position', 'error': 'Position not found on Alpaca'}
    
    if qty:
        # Cap sell qty to actual position size to prevent accidental shorts
        sell_qty = min(float(qty), current_qty)
        if sell_qty <= 0:
            print(f"⚠️ ALPACA: Position {symbol} has 0 qty — skipping close")
            return {'symbol': symbol, 'status': 'no_position', 'error': 'Position qty is 0'}
        if sell_qty < float(qty):
            print(f"⚠️ ALPACA: Requested sell {qty} but only {current_qty} available for {symbol}, capping to {sell_qty}")
        # Partial close via market sell
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=sell_qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = client.submit_order(order_data)
        return _format_order(order)
    else:
        # Full close
        client.close_position(symbol)
        return {'symbol': symbol, 'status': 'closed'}


def close_all_positions() -> Dict:
    """Close all open positions."""
    client = _get_client()
    client.close_all_positions(cancel_orders=True)
    return {'status': 'all_positions_closed'}


# =============================
# ORDERS
# =============================
def place_order(
    symbol: str,
    qty: float,
    side: str,
    order_type: str = 'market',
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    time_in_force: str = 'day',
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
) -> Dict:
    """
    Place an order on Alpaca paper trading.
    
    Args:
        symbol: Ticker symbol
        qty: Number of shares
        side: 'buy' or 'sell'
        order_type: 'market', 'limit', 'stop', 'stop_limit'
        limit_price: Price for limit orders
        stop_price: Price for stop orders
        time_in_force: 'day', 'gtc', 'ioc', 'fok'
        take_profit: Take profit price (bracket order)
        stop_loss: Stop loss price (bracket order)
    """
    client = _get_client()

    order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL

    tif_map = {
        'day': TimeInForce.DAY,
        'gtc': TimeInForce.GTC,
        'ioc': TimeInForce.IOC,
        'fok': TimeInForce.FOK,
    }
    tif = tif_map.get(time_in_force.lower(), TimeInForce.DAY)

    if order_type == 'market':
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
        )
    elif order_type == 'limit':
        if not limit_price:
            raise ValueError("limit_price required for limit orders")
        order_data = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            limit_price=limit_price,
        )
    elif order_type == 'stop':
        if not stop_price:
            raise ValueError("stop_price required for stop orders")
        order_data = StopOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            stop_price=stop_price,
        )
    elif order_type == 'stop_limit':
        if not limit_price or not stop_price:
            raise ValueError("limit_price and stop_price required for stop-limit orders")
        order_data = StopLimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=tif,
            limit_price=limit_price,
            stop_price=stop_price,
        )
    else:
        raise ValueError(f"Unknown order type: {order_type}")

    # Bracket order with take profit and stop loss
    if take_profit:
        order_data.take_profit = {"limit_price": take_profit}
    if stop_loss:
        order_data.stop_loss = {"stop_price": stop_loss}

    order = client.submit_order(order_data)
    return _format_order(order)


def get_orders(status: str = 'all', limit: int = 50) -> List[Dict]:
    """Get orders by status: 'open', 'closed', 'all'."""
    client = _get_client()

    status_map = {
        'open': QueryOrderStatus.OPEN,
        'closed': QueryOrderStatus.CLOSED,
        'all': QueryOrderStatus.ALL,
    }
    query_status = status_map.get(status, QueryOrderStatus.ALL)

    request_params = GetOrdersRequest(
        status=query_status,
        limit=limit,
    )
    orders = client.get_orders(filter=request_params)
    return [_format_order(o) for o in orders]


def get_order_by_id(order_id: str) -> Dict:
    """Fetch a single order by its ID. Returns formatted order dict."""
    client = _get_client()
    order = client.get_order_by_id(order_id)
    return _format_order(order)


def cancel_order(order_id: str) -> Dict:
    """Cancel a specific order by ID."""
    client = _get_client()
    client.cancel_order_by_id(order_id)
    return {'order_id': order_id, 'status': 'cancelled'}


def cancel_all_orders() -> Dict:
    """Cancel all open orders."""
    client = _get_client()
    client.cancel_orders()
    return {'status': 'all_orders_cancelled'}


def _format_order(order) -> Dict:
    """Convert Alpaca order object to serializable dict."""
    return {
        'id': str(order.id),
        'symbol': str(order.symbol),
        'qty': float(order.qty) if order.qty else 0,
        'filled_qty': float(order.filled_qty) if order.filled_qty else 0,
        'side': str(order.side.value) if hasattr(order.side, 'value') else str(order.side),
        'type': str(order.type.value) if hasattr(order.type, 'value') else str(order.type),
        'time_in_force': str(order.time_in_force.value) if hasattr(order.time_in_force, 'value') else str(order.time_in_force),
        'status': str(order.status.value) if hasattr(order.status, 'value') else str(order.status),
        'limit_price': float(order.limit_price) if order.limit_price else None,
        'stop_price': float(order.stop_price) if order.stop_price else None,
        'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else None,
        'submitted_at': order.submitted_at.isoformat() if order.submitted_at else None,
        'filled_at': order.filled_at.isoformat() if order.filled_at else None,
        'created_at': order.created_at.isoformat() if order.created_at else None,
    }
