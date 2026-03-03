# Trading System - AI Agent Instructions

## Overview
Python-based algorithmic trading platform for options, stocks, and ETFs. All data from **yfinance** (no API keys needed). Two interfaces: **Flask web dashboard** (primary) and CLI scripts.

## Quick Start
```bash
python3 launch_dashboard.py        # Web UI at http://localhost:5000
python3 unified_trading_system.py  # CLI: Top 5 picks per asset type
python3 short_squeeze_scanner.py   # CLI: Squeeze candidates
```

## Architecture

### Data Layer
- **State**: JSON files only (`active_positions.json`, `top_picks.json`, `*.csv`)
- **No databases** - all state file-based for portability
- **Market data**: `yf.Ticker(symbol).history()` for prices, `.info` for fundamentals

### Web App (`web_app.py`)
Flask + SocketIO app with scanner caching. Key patterns:
- Routes return `jsonify({'success': True, 'data': clean_nan_values(result)})`
- Scanners run async with `scanner_cache` dict tracking state
- Templates in `/templates`, static assets in `/static/css|js`

### Scanner Classes
All scanners follow this pattern (see `unified_trading_system.py`):
```python
class Scanner:
    def __init__(self):
        self.universe = ['AAPL', 'MSFT', ...]  # Watchlist
        self.output_file = 'results.json'
    
    def get_live_data(self, ticker) -> Optional[Dict]:
        """Fetch price + calculate indicators. Return None on error."""
    
    def score_asset(self, data) -> Tuple[int, List[str]]:
        """0-15 score + signal list. RSI >80 = PENALTY."""
```

### Technical Indicators (copy from `unified_trading_system.py:77-115`)
```python
daily['SMA20'] = daily['Close'].rolling(20).mean()
daily['EMA9'] = daily['Close'].ewm(span=9, adjust=False).mean()
daily['RSI'] = 100 - (100 / (1 + gain.rolling(14).mean() / loss.rolling(14).mean()))
daily['MACD'] = daily['Close'].ewm(12).mean() - daily['Close'].ewm(26).mean()
daily['ATR'] = (daily['High'] - daily['Low']).rolling(14).mean()
```

## Key Workflows

### Trading Scanners
| Scanner | Purpose | Output |
|---------|---------|--------|
| `unified_trading_system.py` | Daily top 5 picks | `top_picks.json` |
| `short_squeeze_scanner.py` | Squeeze candidates (SI >20%) | `short_squeeze_candidates.csv` |
| `triple_confirmation_scanner.py` | SuperTrend+VWAP+MACD aligned | `triple_confirmation_picks.json` |
| `next_day_options_predictor.py` | 1DTE option setups | Live display |

### Position Monitoring
```bash
python3 trade_monitor_alerts.py --setup  # Register positions
python3 analyze_and_monitor.py           # Full pipeline: analyze → monitor
```
Monitors check prices every 5 min against targets/stops in `active_positions.json`.

## Code Conventions

### Error Handling
Fail silently per ticker, continue scanning:
```python
try:
    result = analyze_stock(symbol)
    if result:
        results.append(result)
except Exception as e:
    print(f"Error analyzing {symbol}: {e}")
    return None  # Don't crash scanner
```

### Concurrency
Use `ThreadPoolExecutor(max_workers=15)` for parallel ticker analysis. Add rate limiting for yfinance (0.5s delay between batches).

### Output Style
- Emoji indicators: 🎯 (target), ✅ (bullish), 🔴 (bearish), ⚠️ (warning)
- Dividers: `"=" * 100` for sections
- Prices: `f"${value:.2f}"` always 2 decimals

### Risk Constants
```python
stop_loss_options = entry_premium * 0.5   # 50% stop
targets = [entry * 2, entry * 3, entry * 4]  # 1:2, 1:3, 1:4
stop_loss_stock = entry - (ATR * 1.5)
mandatory_exit = "3:45 PM ET"  # Options must close before expiry
```

## Testing
No formal test suite. Validate with:
```bash
python3 demo_monitoring.py  # Test without real positions
# Or reduce universe to 3-5 tickers in any scanner for quick tests
```

## Adding Features
1. Use JSON state (no databases)
2. Copy indicator calculations from `unified_trading_system.py`
3. Use 0-15 scoring system
4. Make scripts standalone (`python3 new_scanner.py`)
5. Add web endpoint in `web_app.py` if needed
6. Document with `*_GUIDE.md` file
