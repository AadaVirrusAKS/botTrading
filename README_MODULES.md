# 📦 Module Reference Guide

> **US Market Trading Dashboard** — Modular Architecture Documentation  
> Refactored from an 11,692-line monolith into 21 focused modules across 3 layers.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Entry Point](#entry-point)
  - [web_app.py](#web_apppy)
- [Services Layer](#services-layer)
  - [services/market_data.py](#servicesmarket_datapy)
  - [services/utils.py](#servicesutilspy)
  - [services/market_helpers.py](#servicesmarket_helperspy)
  - [services/symbols.py](#servicessymbolspy)
  - [services/indicators.py](#servicesindicatorspy)
  - [services/bot_engine.py](#servicesbot_enginepy)
- [Routes Layer](#routes-layer)
  - [routes/pages.py](#routespagespy)
  - [routes/dashboard.py](#routesdashboardpy)
  - [routes/scanners.py](#routesscannerspy)
  - [routes/options.py](#routesoptionspy)
  - [routes/monitoring.py](#routesmonitoringpy)
  - [routes/technical.py](#routestechnicalpy)
  - [routes/ai_trading.py](#routesai_tradingpy)
  - [routes/ai_analysis.py](#routesai_analysispy)
  - [routes/autonomous.py](#routesautonomouspy)
  - [routes/crypto.py](#routescryptopy)
  - [routes/paper_trading.py](#routespaper_tradingpy)
  - [routes/cache_admin.py](#routescache_adminpy)
- [Frontend Layer](#frontend-layer)
  - [Templates](#templates)
  - [Static Assets](#static-assets)
- [Dependency Graph](#dependency-graph)
- [Screen-to-Module Mapping](#screen-to-module-mapping)
- [API Endpoint Reference](#api-endpoint-reference)

---

## Architecture Overview

The application follows a **three-layer** architecture:

```
┌──────────────────────────────────────────────────────────────┐
│                     web_app.py (Entry Point)                 │
│            Flask + SocketIO + CORS + Error Handlers          │
├──────────────────────────────────────────────────────────────┤
│                        Routes Layer                          │
│  12 Flask Blueprints — one per UI screen / feature area      │
│  pages │ dashboard │ scanners │ options │ monitoring │ ...   │
├──────────────────────────────────────────────────────────────┤
│                       Services Layer                         │
│  6 shared service modules — business logic & data access     │
│  market_data │ utils │ market_helpers │ symbols │ indicators │
│  bot_engine                                                  │
├──────────────────────────────────────────────────────────────┤
│                       Data Layer                             │
│  yfinance API │ JSON state files │ CSV exports               │
│  ai_bot_state.json │ active_positions.json │ *.csv           │
└──────────────────────────────────────────────────────────────┘
```

**Key metrics:**

| Layer | Files | Lines of Code |
|-------|-------|---------------|
| Entry Point | 1 | 194 |
| Services | 7 (incl. `__init__`) | 2,864 |
| Routes | 13 (incl. `__init__`) | 8,988 |
| **Backend Total** | **21** | **12,046** |
| Templates (HTML) | 13 | 12,031 |
| Static (CSS/JS) | 4 | 2,989 |
| **Frontend Total** | **17** | **15,020** |

---

## Project Structure

```
TradingCode/
├── web_app.py                  # Entry point (194 lines)
│
├── services/                   # Shared business logic (2,864 lines)
│   ├── __init__.py             # Package marker
│   ├── market_data.py          # Cache layer & yfinance API (689 lines)
│   ├── utils.py                # Constants & helpers (110 lines)
│   ├── market_helpers.py       # Market status & quotes (278 lines)
│   ├── symbols.py              # Symbol validation & search (215 lines)
│   ├── indicators.py           # Technical indicator engine (518 lines)
│   └── bot_engine.py           # AI bot state & trade logic (1,053 lines)
│
├── routes/                     # Flask Blueprints — 86 endpoints (8,988 lines)
│   ├── __init__.py             # Blueprint registration (33 lines)
│   ├── pages.py                # Page rendering & misc API (224 lines)
│   ├── dashboard.py            # Market overview & sectors (911 lines)
│   ├── scanners.py             # All scanner endpoints (1,627 lines)
│   ├── options.py              # Options analysis & PCR (465 lines)
│   ├── monitoring.py           # Position management (677 lines)
│   ├── technical.py            # Chart data & TA (511 lines)
│   ├── ai_trading.py           # AI bot trading engine (3,439 lines)
│   ├── ai_analysis.py          # AI stock analysis (293 lines)
│   ├── autonomous.py           # Autonomous trader control (265 lines)
│   ├── crypto.py               # Cryptocurrency data (256 lines)
│   ├── paper_trading.py        # Paper trading simulation (143 lines)
│   └── cache_admin.py          # Cache stats & background monitor (144 lines)
│
├── templates/                  # Jinja2 HTML templates (12,031 lines)
│   ├── index.html              # Main dashboard (916 lines)
│   ├── scanners.html           # Scanners page (555 lines)
│   ├── options.html            # Options analysis (1,198 lines)
│   ├── monitoring.html         # Position monitoring (415 lines)
│   ├── technical_analysis.html # Technical charts (1,744 lines)
│   ├── ai_trading.html         # AI trading bot (3,448 lines)
│   ├── ai_analysis.html        # AI analysis (1,765 lines)
│   ├── autonomous.html         # Autonomous trader (809 lines)
│   └── crypto.html             # Crypto dashboard (798 lines)
│
├── static/
│   ├── css/style.css           # Global styles (583 lines)
│   └── js/
│       ├── dashboard.js        # Main dashboard JS (2,225 lines)
│       ├── symbol-autocomplete.js  # Autocomplete widget (174 lines)
│       └── lightweight-charts.js   # TradingView charts lib (7 lines)
│
├── *.py                        # Standalone CLI scanners
├── *.json                      # State files (bot, positions, picks)
└── *.csv                       # Scanner output files
```

---

## Entry Point

### `web_app.py`

**Lines:** 194 | **Purpose:** Application entry point

The slim entry point that initializes Flask, SocketIO, CORS, registers all 12 blueprints, and handles WebSocket connections.

**Responsibilities:**
- Flask app creation with secret key and JSON config
- CORS setup (all origins allowed)
- Global error handlers (404, 500, unhandled exceptions)
- Blueprint registration via `routes.register_blueprints(app)`
- WebSocket handlers: `connect`, `disconnect`, `subscribe_quotes`
- Startup routine: port cleanup, yfinance cache reset, SPY connectivity check
- Background position monitor start/stop via `atexit`

**Key Configuration:**
```python
app.config['SECRET_KEY'] = 'trading-dashboard-secret-2026'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
PORT = int(os.environ.get('PORT', 5000))
```

**Startup Sequence:**
1. Kill any stale process on the port
2. Clear yfinance cookie/crumb cache
3. Verify yfinance connectivity (fetch SPY price)
4. Start background position monitor thread
5. Open browser to `http://localhost:5000`
6. Launch SocketIO server with threading async mode

---

## Services Layer

The services layer contains **6 modules** (2,864 lines) with shared business logic. These modules have **no Flask route handlers** — they are pure data access and computation.

---

### `services/market_data.py`

**Lines:** 689 | **Purpose:** Centralized caching layer for all yfinance API calls

This is the **foundation module** — every route blueprint depends on it. It wraps all yfinance calls with thread-safe caching, rate-limit detection, and back-off logic to prevent 429 errors.

**Cache Architecture:**

| Cache | TTL | Purpose |
|-------|-----|---------|
| `_price_cache` | 30s | Single-symbol live prices |
| `_history_cache` | 300s | Historical OHLCV data |
| `_chain_cache` | 120s | Option chain data |
| `_options_dates_cache` | 600s | Option expiry dates |
| `_ticker_info_cache` | 1800s | Ticker fundamental info |
| `quote_cache` | 60s | Legacy quote cache |
| `scanner_cache` | 600s | Scanner result cache |

Each cache uses a `threading.Lock` for thread safety.

**Rate Limiting:**
- Per-symbol back-off: blocks repeated fetches for a symbol after 429 errors
- Global back-off: blocks ALL fetches when yfinance returns rate-limit errors
- `_is_rate_limit_error(e)` detects "Too Many Requests", "429"
- `_mark_global_rate_limit()` sets a cooldown timestamp

**Exported Functions:**

| Function | Description |
|----------|-------------|
| `cached_get_price(symbol)` | Get live price for a single symbol |
| `cached_batch_prices(symbols)` | Get live prices for multiple symbols |
| `fetch_quote_api_batch(symbols)` | Raw batch quote fetch from yfinance |
| `cached_get_history(symbol, period, interval)` | Historical OHLCV data |
| `cached_get_option_dates(symbol)` | Available option expiry dates |
| `cached_get_option_chain(symbol, expiry)` | Full option chain for an expiry |
| `cached_get_ticker_info(symbol)` | Ticker fundamentals (market cap, P/E, etc.) |
| `_fetch_all_quotes_batch(symbols)` | Bulk quote fetch with fallback logic |
| `clear_all_caches()` | Purge all caches |
| `clear_rate_limit_blocks()` | Reset rate-limit state |
| `_log_fetch_event(kind, key, msg)` | Throttled event logging |

**Shared State:**

| Variable | Type | Used By |
|----------|------|---------|
| `active_subscriptions` | `dict` | WebSocket quote streaming (web_app.py) |
| `scanner_cache` | `dict` | All scanner routes |
| `autonomous_trader_state` | `dict` | Autonomous trading routes |

---

### `services/utils.py`

**Lines:** 110 | **Purpose:** Shared utility functions and constants

**Functions:**

| Function | Description |
|----------|-------------|
| `clean_nan_values(data)` | Recursively converts NaN, inf, numpy types to JSON-safe Python types. Essential for all API responses containing yfinance data. |

**Constants:**

| Constant | Description |
|----------|-------------|
| `MAJOR_INDICES` | Dict mapping index names → ticker symbols (`{'S&P 500': 'SPY', 'NASDAQ': 'QQQ', ...}`) |
| `SECTOR_ETFS` | Dict mapping sector names → ETF symbols (11 sectors: Technology→XLK, Healthcare→XLV, etc.) |
| `SECTOR_STOCKS` | Dict mapping sector names → lists of representative stocks |
| `US_MARKET_HOLIDAYS` | Set of `datetime.date` objects for 2025-2026 market holidays |

---

### `services/market_helpers.py`

**Lines:** 278 | **Purpose:** Market status detection, live quotes, sector performance, and movers

**Functions:**

| Function | Description |
|----------|-------------|
| `get_market_status()` | Returns dict with `is_open`, `status_text`, `next_event` — includes holiday detection using `US_MARKET_HOLIDAYS`, pre/post market detection |
| `get_live_quote(symbol, use_cache)` | Returns comprehensive quote dict: price, change, volume, day range, 52-week range, market cap |
| `get_sector_performance()` | Fetches all 11 sector ETFs, returns sorted performance list |
| `get_top_movers(direction, limit)` | Scans broad universe for top gainers or losers |
| `get_extended_hours_data(symbol)` | Pre-market and after-hours price data for a symbol |
| `get_premarket_movers(limit)` | Top pre-market percentage movers |
| `get_afterhours_movers(limit)` | Top after-hours percentage movers |

**Dependencies:** `services.market_data` (cached functions, rate limiting), `services.utils` (constants)

---

### `services/symbols.py`

**Lines:** 215 | **Purpose:** Symbol validation, name-to-ticker resolution, and autocomplete

**Functions:**

| Function | Description |
|----------|-------------|
| `resolve_symbol_or_name(query)` | Takes a company name (e.g., "Apple") or ticker ("AAPL") and returns the validated ticker symbol. Searches `COMMON_STOCKS` dict first, then tries yfinance lookup. |
| `is_valid_symbol_cached(symbol)` | Thread-safe cached symbol validation. Checks against `KNOWN_DELISTED` set, then verifies via yfinance. Results cached in `_VALID_SYMBOLS_CACHE`/`_INVALID_SYMBOLS_CACHE`. |
| `filter_valid_symbols(symbols)` | Filters a list to return only valid symbols, using parallel validation with ThreadPoolExecutor. |

**Constants:**

| Constant | Description |
|----------|-------------|
| `KNOWN_DELISTED` | Set of ~30 symbols known to be delisted/invalid |
| `COMMON_STOCKS` | Dict of ~60 ticker→company name mappings for autocomplete |

---

### `services/indicators.py`

**Lines:** 518 | **Purpose:** Technical indicator calculation engine

The most **self-contained** service — depends only on `numpy` and `pandas`. No yfinance or Flask imports.

**Functions:**

| Function | Description |
|----------|-------------|
| `calculate_comprehensive_indicators(hist, symbol)` | Calculates 20+ indicators from OHLCV DataFrame: SMA (20, 50, 200), EMA (9, 21), RSI (14), MACD (12/26/9), Bollinger Bands (20,2), ATR (14), Stochastic (14,3), ADX (14), OBV, CMF, Ichimoku Cloud, VWAP, SuperTrend, Volume analysis, Support/Resistance levels, Fibonacci retracements |
| `detect_chart_patterns(df)` | Detects: Double Top/Bottom, Head & Shoulders, Rising/Falling Wedge, Triangle patterns, Channel patterns |
| `generate_trading_signals(df)` | Generates buy/sell signals from indicator crossovers: MACD cross, RSI overbought/oversold, Bollinger squeeze, Golden/Death cross, Volume breakouts |

**Indicator Summary:**

| Category | Indicators |
|----------|------------|
| Moving Averages | SMA20, SMA50, SMA200, EMA9, EMA21 |
| Momentum | RSI(14), Stochastic(14,3), ADX(14) |
| Trend | MACD(12,26,9), SuperTrend, Ichimoku Cloud |
| Volatility | Bollinger Bands(20,2), ATR(14) |
| Volume | OBV, CMF, VWAP, Volume SMA20 |
| Levels | Support/Resistance, Fibonacci Retracements |

---

### `services/bot_engine.py`

**Lines:** 1,053 | **Purpose:** AI trading bot state management, position lifecycle, and signal processing

The **heaviest service** — manages the entire AI trading bot's state, including demo and real account management, position tracking, balance calculation, and trade signal analysis.

**State Management:**

| Item | Description |
|------|-------------|
| `BOT_STATE_FILE` | `'ai_bot_state.json'` — persisted bot state |
| `BOT_STATE_LOCK` | Threading lock for state file access |
| `AUTO_TRADE_DEDUP_LOCK` | Prevents duplicate trade execution |
| `AUTO_TRADE_EXECUTION_GUARD` | Guards concurrent auto-cycle runs |
| `bot_state` | In-memory bot state (loaded from JSON) |

**Core Functions:**

| Function | Description |
|----------|-------------|
| `load_bot_state()` | Loads state from `ai_bot_state.json`, initializes defaults if missing |
| `save_bot_state()` | Persists state to JSON with thread safety |
| `recalculate_balance(account)` | Recalculates account balance from open positions and closed P&L |
| `add_or_update_position(account, symbol, side, qty, price, ...)` | Adds new position or averages into existing one |
| `update_positions_with_live_prices(positions, force_live)` | Refreshes all position prices from yfinance, handles stocks and options differently |

**Trading Functions:**

| Function | Description |
|----------|-------------|
| `calculate_technical_indicators(symbol)` | Quick TA scan: fetches 100d history, calculates RSI, MACD, SMA20/50 |
| `analyze_for_strategy(data, strategy)` | Scores a symbol 0-15 for a given strategy (momentum, swing, value, etc.) |
| `get_live_option_premium(symbol, expiry, strike, option_type)` | Fetches live option premium from yfinance chain |
| `is_zero_dte_or_expired(expiry_str)` | Checks if an option is 0DTE or expired |
| `is_option_expiry_blocked(expiry_str, min_dte_days)` | Checks if expiry is too close to trade |
| `refresh_signal_entries_with_live_prices(signals)` | Updates entry prices in signal list with live data |

**Analysis Functions:**

| Function | Description |
|----------|-------------|
| `generate_daily_trade_analysis(account, date)` | Generates daily P&L summary with detailed trade breakdown |
| `reconcile_orphan_positions(account, key)` | Finds positions stuck in inconsistent state and repairs them |

**Watchlists:**

| Strategy | Symbols |
|----------|---------|
| Momentum | High-beta tech/growth (50+ symbols) |
| Swing | Mid-cap momentum plays |
| Value | Blue-chip dividend stocks |
| Options | High-liquidity option underlyings |

---

## Routes Layer

The routes layer contains **12 Flask Blueprints** (8,988 lines) with **86 API endpoints**. Each blueprint maps to a UI screen or feature area.

---

### `routes/pages.py`

**Lines:** 224 | **Blueprint:** `pages_bp` | **Purpose:** HTML page rendering and miscellaneous API

Serves all 13 HTML templates and provides utility endpoints for health checks, symbol autocomplete, and scanner status.

**Page Routes (13):**

| URL | Template | Description |
|-----|----------|-------------|
| `GET /` | `index.html` | Main dashboard |
| `GET /scanners` | `scanners.html` | Stock scanners |
| `GET /options` | `options.html` | Options analysis |
| `GET /monitoring` | `monitoring.html` | Position monitoring |
| `GET /technical-analysis` | `technical_analysis.html` | Technical charts |
| `GET /ai-trading` | `ai_trading.html` | AI trading bot |
| `GET /ai-analysis` | `ai_analysis.html` | AI stock analysis |
| `GET /autonomous` | `autonomous.html` | Autonomous trader |
| `GET /crypto` | `crypto.html` | Cryptocurrency dashboard |
| `GET /test-debug` | `test_debug.html` | Debug test page |
| `GET /test-chart` | `test_chart_simple.html` | Chart test page |
| `GET /diagnostic` | `chart_diagnostic.html` | Chart diagnostic page |

**API Routes (4):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/health` | Health check — returns server status, uptime, market status |
| `GET` | `/api/symbol/suggest?q=...` | Symbol autocomplete — searches `COMMON_STOCKS` + yfinance |
| `GET` | `/api/scanner/status` | Returns running/completed status of all scanner types |
| `POST` | `/api/scanner/trigger-all` | Triggers all scanners to run in background threads |
| `GET` | `/api/quote/<symbol>` | Quick single-symbol quote lookup |

---

### `routes/dashboard.py`

**Lines:** 911 | **Blueprint:** `dashboard_bp` | **Purpose:** Main dashboard data — market overview, sectors, movers

Provides the data APIs consumed by the main dashboard (`index.html`). Handles market breadth calculation, batch data aggregation, sector analysis, and stock discovery screens.

**API Routes (10):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/dashboard/batch` | Aggregated dashboard data: indices, sectors, movers, market status — single call for page load |
| `POST` | `/api/dashboard/batch` | Custom batch request with selected data sections |
| `GET` | `/api/market/overview` | Market overview: major indices with real-time prices and changes |
| `GET` | `/api/market/sectors` | All 11 sector ETF performances with change percentages |
| `GET` | `/api/market/sector/<etf>/stocks` | Individual stocks within a sector ETF |
| `GET` | `/api/market/movers/<direction>` | Top gainers (`up`) or losers (`down`) from broad market |
| `GET` | `/api/market/premarket` | Pre-market movers and extended hours data |
| `GET` | `/api/market/afterhours` | After-hours movers and extended hours data |
| `GET` | `/api/market/52week` | Stocks near 52-week highs and lows |
| `GET` | `/api/market/crashed` | Stocks that have crashed >30% from recent highs |

**Internal Functions:**

| Function | Description |
|----------|-------------|
| `_get_broad_market_tickers()` | Builds list of ~200 tickers for market-wide scanning |
| `calculate_market_breadth()` | Calculates advance/decline ratio, up/down volume |
| `_get_batch_dashboard_data()` | Aggregates all dashboard sections into one response |

---

### `routes/scanners.py`

**Lines:** 1,627 | **Blueprint:** `scanners_bp` | **Purpose:** All stock/ETF scanner endpoints

The scanner hub — integrates with external scanner classes (`UnifiedTradingSystem`, `ShortSqueezeScanner`, `BeatenDownQualityScanner`) and implements several inline scanners.

**API Routes (12):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/scanner/unified` | Top picks from Unified Trading System (scored 0-15) |
| `GET` | `/api/scanner/short-squeeze` | Short squeeze candidates (SI >20%, high borrow cost) |
| `GET` | `/api/scanner/weekly-screener` | Weekly stock screener (top 100 by composite score) |
| `GET` | `/api/scanner/quality-stocks` | Beaten-down quality stocks (>30% off highs, strong fundamentals) |
| `GET` | `/api/scanner/golden-cross` | Golden cross scanner (SMA50 crossing above SMA200) |
| `GET` | `/api/scanner/triple-confirmation` | Triple confirmation: SuperTrend + VWAP + MACD aligned |
| `GET` | `/api/scanner/triple-intraday` | Intraday triple confirmation (5-min timeframe) |
| `GET` | `/api/scanner/triple-positional` | Positional triple confirmation (daily timeframe) |
| `GET` | `/api/scanner/triple-confirmation-all` | All three triple confirmation types combined |
| `GET` | `/api/scanner/volume-spike` | Volume spike detector (>2x average volume) |
| `GET` | `/api/scanner/etf-scanner` | ETF scanner with sector rotation signals |
| `POST` | `/api/scanner/custom-analyzer` | Custom symbol analysis with user-provided ticker list |

**Scanner Integration:**

| External Scanner | Import | Used By |
|-----------------|--------|---------|
| `UnifiedTradingSystem` | `unified_trading_system` | `/api/scanner/unified` |
| `ShortSqueezeScanner` | `short_squeeze_scanner` | `/api/scanner/short-squeeze` |
| `BeatenDownQualityScanner` | `beaten_down_quality_scanner` | `/api/scanner/quality-stocks` |
| `WeeklyStockScreener` | `weekly_screener_top100` | `/api/scanner/weekly-screener` |
| `USMarketGoldenCrossScanner` | `us_market_golden_cross_scanner` | `/api/scanner/golden-cross` |
| `TripleConfirmationScanner` | `triple_confirmation_scanner` | `/api/scanner/triple-confirmation` |

All scanners use the `scanner_cache` from `services/market_data.py` with 10-minute TTL to avoid re-running expensive scans.

---

### `routes/options.py`

**Lines:** 465 | **Blueprint:** `options_bp` | **Purpose:** Options chain analysis, swing trades, and put/call ratio

**API Routes (4):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/options/analysis?symbol=...` | Full options chain analysis: IV percentile, Greeks, unusual activity, next-day prediction |
| `POST` | `/api/options/refresh` | Force refresh options data (cache bust) |
| `GET` | `/api/options/pcr?symbol=...` | Put/call ratio: volume PCR, open interest PCR, historical trend |
| `GET` | `/api/options/swing?symbol=...` | Swing trade option candidates: 30-60 DTE, delta 0.3-0.5, high probability setups |

**External Integration:**
- `NextDayOptionsPredictor` from `next_day_options_predictor.py` — provides ML-based next-day price predictions for option setup recommendations

---

### `routes/monitoring.py`

**Lines:** 677 | **Blueprint:** `monitoring_bp` | **Purpose:** Active position tracking and management

Manages the `active_positions.json` file — CRUD operations for trade positions with live price updates.

**API Routes (7):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/positions/active` | Get all active positions with live prices, P&L calculation, target/stop status |
| `POST` | `/api/positions/reload` | Reload positions from `active_positions.json` file |
| `POST` | `/api/positions/restore` | Restore a position from the backup history |
| `POST` | `/api/positions/add` | Add a new position (stock or option) with entry price, targets, stops |
| `DELETE` | `/api/positions/delete/<index>` | Delete position by numeric index |
| `DELETE` | `/api/positions/delete/<key>` | Delete position by unique key identifier |
| `POST` | `/api/positions/close` | Close a position, recording exit price and P&L |

**Position Schema:**
```json
{
  "symbol": "AAPL",
  "instrument_type": "stock|option",
  "side": "long|short",
  "entry_price": 175.50,
  "quantity": 100,
  "stop_loss": 170.00,
  "targets": [180.00, 185.00, 190.00],
  "current_price": 178.25,
  "pnl": 275.00,
  "pnl_pct": 1.57
}
```

---

### `routes/technical.py`

**Lines:** 511 | **Blueprint:** `technical_bp` | **Purpose:** Chart data and full technical analysis

Powers the Technical Analysis page with OHLCV chart data and comprehensive indicator overlays.

**API Routes (2):**

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/technical/chart-data` | OHLCV candlestick data with volume, configurable period/interval. Returns TradingView-compatible format. |
| `POST` | `/api/technical/analyze` | Full technical analysis: all indicators from `services/indicators.py`, chart patterns, buy/sell signals, support/resistance levels, score summary |

**Request Format:**
```json
{
  "symbol": "AAPL",
  "period": "6mo",
  "interval": "1d"
}
```

**Response includes:** Price data, SMA/EMA overlays, RSI, MACD histogram, Bollinger Bands, volume analysis, detected patterns, generated signals, overall score (0-100).

---

### `routes/ai_trading.py`

**Lines:** 3,439 | **Blueprint:** `ai_trading_bp` | **Purpose:** AI trading bot — the largest module

The AI trading bot engine. Manages bot lifecycle (start/stop), account switching (demo/real), signal scanning, automated trade execution, position management, and intraday scanning.

**API Routes (17):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/bot/status` | Bot status: running state, balance, positions with live prices, P&L, settings |
| `POST` | `/api/bot/start` | Start the AI trading bot |
| `POST` | `/api/bot/stop` | Stop the AI trading bot |
| `POST` | `/api/bot/update_settings` | Update bot settings (max positions, risk per trade, strategies, etc.) |
| `POST` | `/api/bot/switch_account` | Switch between demo and real account |
| `POST` | `/api/bot/test_trade` | Execute a test trade to verify the pipeline |
| `POST` | `/api/bot/reset_demo` | Reset demo account to $10,000 with no positions |
| `POST` | `/api/bot/import_positions` | Import positions from JSON/CSV |
| `POST` | `/api/bot/scan` | Manual scan: runs all strategy scanners and returns ranked signals |
| `POST` | `/api/bot/auto_cycle` | **Auto-cycle**: scan → filter → rank → execute trades automatically |
| `POST` | `/api/bot/trade` | Execute a stock trade (buy/sell with position sizing) |
| `POST` | `/api/bot/trade_option` | Execute an option trade (with contract matching) |
| `POST` | `/api/bot/close` | Close a single position |
| `POST` | `/api/bot/close-all` | Close all positions |
| `POST` | `/api/bot/cleanup_duplicate_exits` | Clean up orphaned/duplicate exit records |
| `GET` | `/api/bot/intraday-stocks` | Intraday stock scanner: scans universe for momentum/reversal setups |
| `GET` | `/api/bot/intraday-options` | Intraday options scanner: finds 0-2 DTE option setups |

**Auto-Cycle Pipeline:**
```
1. Load active watchlists per strategy
2. Scan all symbols (parallel, rate-limited)
3. Score signals (0-15 scale)
4. Filter: min confidence, max positions, dedup
5. Size positions (% of balance, risk-based)
6. Execute trades (with execution guard)
7. Save state
```

**Key Internal Functions:**

| Function | Description |
|----------|-------------|
| `load_local_fallback_signals()` | Loads cached scanner results when live scanning fails |
| `build_live_option_fallback_signals()` | Constructs option signals from live chain data |
| `recalculate_intraday_sl_target()` | Dynamically adjusts stop-loss and target based on intraday TA |
| `scan_intraday_stock(symbol)` | Full intraday scan for a single symbol |
| `scan_intraday_option(symbol)` | Full intraday option scan for a single symbol |
| `run_intraday_scan_batched()` | Rate-limited batch scanner runner with fallback |

---

### `routes/ai_analysis.py`

**Lines:** 293 | **Blueprint:** `ai_analysis_bp` | **Purpose:** AI-powered stock analysis with predictions

**API Routes (4):**

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/ai/analyze` | Full AI analysis: trend detection, pattern recognition, support/resistance, price prediction, risk assessment |
| `POST` | `/api/ai/heatmap` | Correlation heatmap for a group of symbols |
| `POST` | `/api/ai/patterns` | Pattern detection only (chart patterns + candlestick patterns) |
| `POST` | `/api/ai/predict` | Price prediction only (statistical + ML-based forecast) |

**Request Format:**
```json
{
  "symbol": "AAPL",
  "period": "6mo",
  "horizon": "1w"
}
```

---

### `routes/autonomous.py`

**Lines:** 265 | **Blueprint:** `autonomous_bp` | **Purpose:** Autonomous DeepSeek-powered trader control

Controls the autonomous trading agent that uses DeepSeek AI for analysis and decision-making.

**API Routes (7):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/autonomous/status` | Autonomous trader running status, metrics, last analysis time |
| `POST` | `/api/autonomous/start` | Start autonomous trading (launches background analysis loop) |
| `POST` | `/api/autonomous/stop` | Stop autonomous trading |
| `POST` | `/api/autonomous/analyze` | Trigger manual analysis cycle |
| `POST` | `/api/autonomous/settings` | Update autonomous trader settings (risk, universe, interval) |
| `GET` | `/api/autonomous/positions` | Get autonomous trader positions |
| `GET` | `/api/autonomous/trades` | Get autonomous trader trade history |

**External Integration:**
- `AutonomousTrader` from `autonomous_deepseek_trader.py` (optional — gracefully fails if not available)
- `DeepSeekAnalyzer`, `RiskManager` for AI-driven decisions

---

### `routes/crypto.py`

**Lines:** 256 | **Blueprint:** `crypto_bp` | **Purpose:** Cryptocurrency data and analysis

**API Routes (4):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/crypto/all` | All 30 cryptocurrency prices, changes, market caps |
| `GET` | `/api/crypto/movers/<direction>` | Top crypto gainers or losers |
| `GET` | `/api/crypto/search?q=...` | Search cryptocurrencies by name or symbol |
| `GET` | `/api/crypto/quote/<symbol>` | Single crypto detailed quote |

**Supported Cryptocurrencies (30):**
BTC, ETH, BNB, XRP, ADA, DOGE, SOL, DOT, MATIC, AVAX, LINK, UNI, ATOM, LTC, FIL, NEAR, APT, ARB, OP, IMX, AAVE, MKR, SNX, CRV, LDO, RUNE, INJ, SUI, TIA, SEI

All tickers use Yahoo Finance's `-USD` suffix format (e.g., `BTC-USD`).

---

### `routes/paper_trading.py`

**Lines:** 143 | **Blueprint:** `paper_bp` | **Purpose:** Paper trading simulation

A **fully self-contained** module with no service dependencies. Manages a simulated trading account with $10,000 starting capital.

**API Routes (6):**

| Method | URL | Description |
|--------|-----|-------------|
| `POST` | `/api/paper/start` | Initialize paper trading session |
| `GET` | `/api/paper/balance` | Get paper trading balance and P&L |
| `GET` | `/api/paper/positions` | Get paper trading positions |
| `POST` | `/api/paper/trade` | Execute a paper trade (validates balance, creates position) |
| `POST` | `/api/paper/close` | Close a paper trading position |
| `POST` | `/api/paper/reset` | Reset paper account to $10,000 |

**State File:** `paper_trading_state.json`

---

### `routes/cache_admin.py`

**Lines:** 144 | **Blueprint:** `cache_bp` | **Purpose:** Cache management and background monitoring

**API Routes (2):**

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/cache/stats` | Cache statistics: hit counts, sizes, TTLs for all cache types, yfinance disk cache size |
| `POST` | `/api/cache/clear` | Clear all in-memory caches and optionally yfinance disk cache |

**Background Monitor:**

| Function | Description |
|----------|-------------|
| `_background_position_monitor()` | Runs every 5 minutes: refreshes live prices for active bot positions |
| `start_background_monitor()` | Starts the monitor thread (called at startup) |
| `stop_background_monitor()` | Stops the monitor thread (called at shutdown via atexit) |

---

## Frontend Layer

### Templates

Each HTML template is a complete single-page application with embedded JavaScript that calls the corresponding route blueprint's API endpoints.

| Template | Lines | Screen | Primary API Blueprint |
|----------|-------|--------|-----------------------|
| `index.html` | 916 | Main Dashboard | `routes/dashboard.py` |
| `scanners.html` | 555 | Stock Scanners | `routes/scanners.py` |
| `options.html` | 1,198 | Options Analysis | `routes/options.py` |
| `monitoring.html` | 415 | Position Monitoring | `routes/monitoring.py` |
| `technical_analysis.html` | 1,744 | Technical Charts | `routes/technical.py` |
| `ai_trading.html` | 3,448 | AI Trading Bot | `routes/ai_trading.py` |
| `ai_analysis.html` | 1,765 | AI Analysis | `routes/ai_analysis.py` |
| `autonomous.html` | 809 | Autonomous Trader | `routes/autonomous.py` |
| `crypto.html` | 798 | Crypto Dashboard | `routes/crypto.py` |
| `chart_diagnostic.html` | 125 | Chart Diagnostic | `routes/technical.py` |
| `test_debug.html` | 133 | Debug Testing | `routes/pages.py` |
| `test_chart_simple.html` | 100 | Chart Testing | `routes/technical.py` |
| `test_api.html` | 25 | API Testing | Various |

### Static Assets

| File | Lines | Description |
|------|-------|-------------|
| `css/style.css` | 583 | Global dark-theme styles, responsive layout, component styles |
| `js/dashboard.js` | 2,225 | Main dashboard JavaScript: WebSocket connection, chart rendering, data fetching, UI updates |
| `js/symbol-autocomplete.js` | 174 | Symbol search autocomplete widget using `/api/symbol/suggest` |
| `js/lightweight-charts.js` | 7 | TradingView Lightweight Charts library loader |

---

## Dependency Graph

```
web_app.py
├── routes/__init__.py (registers all 12 blueprints)
│
├── routes/pages.py ──────────── services/market_data, market_helpers, symbols, utils
├── routes/dashboard.py ──────── services/market_data, market_helpers, symbols, utils
├── routes/scanners.py ───────── services/market_data, symbols, utils
│                                 + external: unified_trading_system, short_squeeze_scanner,
│                                   beaten_down_quality_scanner, weekly_screener_top100,
│                                   us_market_golden_cross_scanner, triple_confirmation_scanner
├── routes/options.py ────────── services/market_data, utils
│                                 + external: next_day_options_predictor
├── routes/monitoring.py ─────── services/market_data, utils
├── routes/technical.py ──────── services/market_data, indicators, utils
├── routes/ai_trading.py ─────── services/market_data, bot_engine, utils
├── routes/ai_analysis.py ────── services/market_data, symbols, utils
├── routes/autonomous.py ─────── services/market_data, utils
│                                 + external: autonomous_deepseek_trader
├── routes/crypto.py ─────────── services/market_data, utils
├── routes/paper_trading.py ──── (self-contained, no service imports)
└── routes/cache_admin.py ────── services/market_data, bot_engine

services/market_data.py ─── yfinance, threading, pandas, numpy
services/utils.py ────────── numpy, datetime
services/market_helpers.py ─ services/market_data, services/utils, yfinance
services/symbols.py ──────── yfinance, threading
services/indicators.py ───── numpy, pandas (standalone)
services/bot_engine.py ────── services/market_data, services/symbols, yfinance
```

---

## Screen-to-Module Mapping

Complete mapping from each UI screen to the backend modules that power it:

| # | Screen | URL | Template | Route Module | Services Used |
|---|--------|-----|----------|-------------|---------------|
| 1 | **Dashboard** | `/` | `index.html` | `routes/dashboard.py` | market_data, market_helpers, symbols, utils |
| 2 | **Scanners** | `/scanners` | `scanners.html` | `routes/scanners.py` | market_data, symbols, utils + external scanners |
| 3 | **Options** | `/options` | `options.html` | `routes/options.py` | market_data, utils |
| 4 | **Monitoring** | `/monitoring` | `monitoring.html` | `routes/monitoring.py` | market_data, utils |
| 5 | **Technical** | `/technical-analysis` | `technical_analysis.html` | `routes/technical.py` | market_data, indicators, utils |
| 6 | **AI Trading** | `/ai-trading` | `ai_trading.html` | `routes/ai_trading.py` | market_data, bot_engine, utils |
| 7 | **AI Analysis** | `/ai-analysis` | `ai_analysis.html` | `routes/ai_analysis.py` | market_data, symbols, utils |
| 8 | **Autonomous** | `/autonomous` | `autonomous.html` | `routes/autonomous.py` | market_data, utils |
| 9 | **Crypto** | `/crypto` | `crypto.html` | `routes/crypto.py` | market_data, utils |

---

## API Endpoint Reference

### Health & Utility
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/health` | pages |
| GET | `/api/symbol/suggest?q=` | pages |
| GET | `/api/quote/<symbol>` | pages |
| GET | `/api/scanner/status` | pages |
| POST | `/api/scanner/trigger-all` | pages |

### Dashboard & Market Data
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/dashboard/batch` | dashboard |
| POST | `/api/dashboard/batch` | dashboard |
| GET | `/api/market/overview` | dashboard |
| GET | `/api/market/sectors` | dashboard |
| GET | `/api/market/sector/<etf>/stocks` | dashboard |
| GET | `/api/market/movers/<direction>` | dashboard |
| GET | `/api/market/premarket` | dashboard |
| GET | `/api/market/afterhours` | dashboard |
| GET | `/api/market/52week` | dashboard |
| GET | `/api/market/crashed` | dashboard |

### Scanners
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/scanner/unified` | scanners |
| GET | `/api/scanner/short-squeeze` | scanners |
| GET | `/api/scanner/weekly-screener` | scanners |
| GET | `/api/scanner/quality-stocks` | scanners |
| GET | `/api/scanner/golden-cross` | scanners |
| GET | `/api/scanner/triple-confirmation` | scanners |
| GET | `/api/scanner/triple-intraday` | scanners |
| GET | `/api/scanner/triple-positional` | scanners |
| GET | `/api/scanner/triple-confirmation-all` | scanners |
| GET | `/api/scanner/volume-spike` | scanners |
| GET | `/api/scanner/etf-scanner` | scanners |
| POST | `/api/scanner/custom-analyzer` | scanners |

### Options
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/options/analysis?symbol=` | options |
| POST | `/api/options/refresh` | options |
| GET | `/api/options/pcr?symbol=` | options |
| GET | `/api/options/swing?symbol=` | options |

### Position Monitoring
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/positions/active` | monitoring |
| POST | `/api/positions/reload` | monitoring |
| POST | `/api/positions/restore` | monitoring |
| POST | `/api/positions/add` | monitoring |
| DELETE | `/api/positions/delete/<index>` | monitoring |
| DELETE | `/api/positions/delete/<key>` | monitoring |
| POST | `/api/positions/close` | monitoring |

### Technical Analysis
| Method | Endpoint | Module |
|--------|----------|--------|
| POST | `/api/technical/chart-data` | technical |
| POST | `/api/technical/analyze` | technical |

### AI Trading Bot
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/bot/status` | ai_trading |
| POST | `/api/bot/start` | ai_trading |
| POST | `/api/bot/stop` | ai_trading |
| POST | `/api/bot/update_settings` | ai_trading |
| POST | `/api/bot/switch_account` | ai_trading |
| POST | `/api/bot/test_trade` | ai_trading |
| POST | `/api/bot/reset_demo` | ai_trading |
| POST | `/api/bot/import_positions` | ai_trading |
| POST | `/api/bot/scan` | ai_trading |
| POST | `/api/bot/auto_cycle` | ai_trading |
| POST | `/api/bot/trade` | ai_trading |
| POST | `/api/bot/trade_option` | ai_trading |
| POST | `/api/bot/close` | ai_trading |
| POST | `/api/bot/close-all` | ai_trading |
| POST | `/api/bot/cleanup_duplicate_exits` | ai_trading |
| GET | `/api/bot/intraday-stocks` | ai_trading |
| GET | `/api/bot/intraday-options` | ai_trading |

### AI Analysis
| Method | Endpoint | Module |
|--------|----------|--------|
| POST | `/api/ai/analyze` | ai_analysis |
| POST | `/api/ai/heatmap` | ai_analysis |
| POST | `/api/ai/patterns` | ai_analysis |
| POST | `/api/ai/predict` | ai_analysis |

### Autonomous Trader
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/autonomous/status` | autonomous |
| POST | `/api/autonomous/start` | autonomous |
| POST | `/api/autonomous/stop` | autonomous |
| POST | `/api/autonomous/analyze` | autonomous |
| POST | `/api/autonomous/settings` | autonomous |
| GET | `/api/autonomous/positions` | autonomous |
| GET | `/api/autonomous/trades` | autonomous |

### Cryptocurrency
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/crypto/all` | crypto |
| GET | `/api/crypto/movers/<direction>` | crypto |
| GET | `/api/crypto/search?q=` | crypto |
| GET | `/api/crypto/quote/<symbol>` | crypto |

### Paper Trading
| Method | Endpoint | Module |
|--------|----------|--------|
| POST | `/api/paper/start` | paper_trading |
| GET | `/api/paper/balance` | paper_trading |
| GET | `/api/paper/positions` | paper_trading |
| POST | `/api/paper/trade` | paper_trading |
| POST | `/api/paper/close` | paper_trading |
| POST | `/api/paper/reset` | paper_trading |

### Cache Administration
| Method | Endpoint | Module |
|--------|----------|--------|
| GET | `/api/cache/stats` | cache_admin |
| POST | `/api/cache/clear` | cache_admin |

---

> **Total: 86 API endpoints across 12 Blueprint modules, powered by 6 service modules.**
