# Trading System - AI Agent Instructions

## Overview
Python-based algorithmic trading platform for options, stocks, and ETFs. All data from **yfinance** (no API keys needed). Two interfaces: **Flask web dashboard** (primary) and CLI scripts.

## Project Structure
```
TradingCode/
├── run.py                  # Entry point → app/web_app.py
├── app/                    # Flask web application
│   ├── __init__.py
│   └── web_app.py
├── config/                 # Configuration & stock lists
│   └── master_stock_list.py
├── scanners/               # Market analysis & screening
│   ├── unified_trading_system.py
│   ├── short_squeeze_scanner.py
│   ├── triple_confirmation_scanner.py
│   ├── next_day_options_predictor.py
│   └── ...
├── trading/                # Trade execution & automation
│   ├── autonomous_deepseek_trader.py
│   ├── spy_qqq_options_trader.py
│   └── ...
├── monitoring/             # Position monitoring & alerts
│   ├── trade_monitor_alerts.py
│   ├── analyze_and_monitor.py
│   └── ...
├── routes/                 # Flask API blueprints
├── services/               # Shared business logic
├── templates/              # HTML pages
├── static/                 # CSS/JS assets
├── scripts/                # Launcher & scheduler scripts
├── data/                   # Runtime data (gitignored)
└── docs/                   # Documentation guides
```

## Quick Start
```bash
python3 run.py                                       # Web UI at http://localhost:5000
python3 -m scanners.unified_trading_system           # CLI: Top 5 picks per asset type
python3 -m scanners.short_squeeze_scanner            # CLI: Squeeze candidates
```

## Architecture

### Data Layer
- **State**: JSON files only in `data/` directory (`data/active_positions.json`, `data/top_picks.json`, `data/*.csv`)
- **No databases** - all state file-based for portability
- **Market data**: `yf.Ticker(symbol).history()` for prices, `.info` for fundamentals

### Web App (`app/web_app.py`)
Flask + SocketIO app with scanner caching. Key patterns:
- Routes return `jsonify({'success': True, 'data': clean_nan_values(result)})`
- Scanners run async with `scanner_cache` dict tracking state
- Templates in `/templates`, static assets in `/static/css|js`

### Scanner Classes
All scanners follow this pattern (see `scanners/unified_trading_system.py`):
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

### Technical Indicators (from `scanners/unified_trading_system.py`)
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
| `scanners/unified_trading_system.py` | Daily top 5 picks | `top_picks.json` |
| `scanners/short_squeeze_scanner.py` | Squeeze candidates (SI >20%) | `short_squeeze_candidates.csv` |
| `scanners/triple_confirmation_scanner.py` | SuperTrend+VWAP+MACD aligned | `triple_confirmation_picks.json` |
| `scanners/next_day_options_predictor.py` | 1DTE option setups | Live display |

### Position Monitoring
```bash
python3 -m monitoring.trade_monitor_alerts --setup   # Register positions
python3 -m monitoring.analyze_and_monitor             # Full pipeline: analyze → monitor
```
Monitors check prices every 5 min against targets/stops in `data/active_positions.json`.

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
Validate with:
```bash
# Reduce universe to 3-5 tickers in any scanner for quick tests
```

## Adding Features
1. Use JSON state (no databases)
2. Copy indicator calculations from `scanners/unified_trading_system.py`
3. Use 0-15 scoring system
4. Make scripts standalone (add `sys.path.insert` for project root)
5. Output data files to `DATA_DIR` (from `config import DATA_DIR`)
5. Add web endpoint in `app/web_app.py` if needed
6. Document with `docs/*_GUIDE.md` file
