# 🇺🇸 US Market Pulse — Algorithmic Trading Platform

A production-ready, Python-based algorithmic trading platform for **options, stocks, and ETFs**. Features a real-time **Flask web dashboard**, CLI scanners, autonomous trading agents, and position monitoring — all powered by **yfinance** (no paid API keys required).

---

## Quick Start

```bash
# 1. Clone & enter project
cd TradingCode

# 2. Create virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch dashboard (auto-installs missing deps)
python3 run.py
```

Open **http://localhost:5000** in your browser.

### CLI Usage

```bash
python3 -m scanners.unified_trading_system          # Top 5 picks per asset type
python3 -m scanners.short_squeeze_scanner            # Short squeeze candidates
python3 -m scanners.triple_confirmation_scanner      # SuperTrend+VWAP+MACD aligned
python3 -m monitoring.trade_monitor_alerts           # Position alert monitor
python3 -m scripts.auto_trading_scheduler            # Automated 0DTE trading scheduler
```

---

## Project Structure

```
TradingCode/
│
├── run.py                      # Thin entry point → launches app/web_app.py
├── requirements.txt            # Python dependencies
│
├── app/                        # ── Web Application ───────────────────
│   ├── __init__.py             #    Package init, sys.path setup
│   └── web_app.py              #    Flask + SocketIO app (port 5000)
│
├── config/                     # ── Configuration ──────────────────────
│   ├── __init__.py             #    PROJECT_ROOT & DATA_DIR constants
│   └── master_stock_list.py    #    Single source of truth for all symbol universes
│
├── scanners/                   # ── Market Analysis & Screening ───────
│   ├── unified_trading_system.py       # Core: Top 5 picks (options/stocks/ETFs)
│   ├── short_squeeze_scanner.py        # Squeeze candidates (SI >20%)
│   ├── triple_confirmation_scanner.py  # SuperTrend + VWAP + MACD (daily)
│   ├── triple_confirmation_intraday.py # SuperTrend + VWAP + MACD (5-15 min)
│   ├── triple_confirmation_positional.py # SuperTrend + VWAP + MACD (weekly)
│   ├── next_day_options_predictor.py   # 1DTE option setups (1:3–1:5 R:R)
│   ├── ai_stock_analysis.py           # 25+ candlestick patterns, price prediction
│   ├── beaten_down_quality_scanner.py  # Quality stocks beaten down in crashes
│   ├── market_news_scanner.py          # Pre/post-market news analysis
│   ├── weekly_screener_top100.py       # Weekly screener ($50–$200 range)
│   ├── us_market_golden_cross_scanner.py # Golden cross (50/200 EMA)
│   ├── swing_trading_strategy.py       # Swing strategy backtest with Plotly
│   └── analyze_custom_stocks.py        # Custom stock list analysis
│
├── trading/                    # ── Trade Execution & Automation ──────
│   ├── autonomous_deepseek_trader.py   # DeepSeek AI-powered autonomous trader
│   ├── autonomous_trading_agent.py     # Entry/exit/stop-loss/take-profit agent
│   ├── spy_qqq_options_trader.py       # SPY/QQQ daily options (1:10 R:R)
│   ├── auto_0dte_monitor.py            # Autonomous 0DTE trader with monitoring
│   ├── live_0dte_trader.py             # Live 0DTE options trader
│   └── execute_trade_setup.py          # Real-time options trade setup generator
│
├── monitoring/                 # ── Position Monitoring & Alerts ──────
│   ├── trade_monitor_alerts.py         # 5-min alert monitor for active positions
│   ├── analyze_and_monitor.py          # Full pipeline: predict → setup → monitor
│   ├── realtime_trade_monitor.py       # Real-time options position tracker
│   ├── monitor_options_positions.py    # SPY/QQQ options position monitor
│   ├── analyze_pnl_gap.py             # Bot vs Alpaca P&L comparison
│   └── analyze_pnl_gap_detail.py       # Trade-by-trade P&L detail comparison
│
├── routes/                     # ── Flask API Blueprints (14) ─────────
│   ├── dashboard.py            #    Market overview, sectors, movers
│   ├── scanners.py             #    All scanner endpoints
│   ├── options.py              #    Options analysis, PCR, swing trades
│   ├── monitoring.py           #    Active positions CRUD
│   ├── technical.py            #    Chart data, technical indicators
│   ├── ai_trading.py           #    AI bot control, auto-cycle, execution
│   ├── ai_analysis.py          #    AI patterns, predictions, heatmaps
│   ├── autonomous.py           #    Autonomous trader status & control
│   ├── crypto.py               #    Cryptocurrency data & movers
│   ├── alpaca.py               #    Alpaca paper trading API wrapper
│   ├── paper_trading.py        #    Paper trading simulation
│   ├── cache_admin.py          #    Cache stats & management
│   ├── daily_agent.py          #    Daily analysis agent API
│   └── pages.py                #    Template rendering, quotes, suggestions
│
├── services/                   # ── Shared Business Logic ─────────────
│   ├── market_data.py          #    Centralized yfinance caching & rate limiting
│   ├── bot_engine.py           #    AI trading bot state & position management
│   ├── alpaca_service.py       #    Alpaca Markets API wrapper
│   ├── indicators.py           #    Technical indicator calculations
│   ├── market_helpers.py       #    Market status, live quotes, sectors
│   ├── symbols.py              #    Symbol validation & resolution
│   ├── daily_analysis_agent.py #    Daily automated performance analysis
│   └── utils.py                #    Constants, clean_nan_values, holidays
│
├── scripts/                    # ── Scheduler & Launcher Scripts ──────
│   ├── auto_trading_scheduler.py  # Market-hours scheduler for 0DTE trading
│   ├── launch_dashboard.py        # One-click launcher with dependency checks
│   ├── launch.sh                  # Shell launcher wrapper
│   └── start_web_server.sh        # Alternative shell launcher
│
├── templates/                  # ── HTML Pages (12) ───────────────────
│   ├── index.html              #    Main dashboard
│   ├── scanners.html           #    Scanner results
│   ├── options.html            #    Options analysis
│   ├── monitoring.html         #    Position monitoring
│   ├── ai_trading.html         #    AI bot control panel
│   ├── ai_analysis.html        #    AI stock analysis
│   ├── autonomous.html         #    Autonomous trader
│   ├── crypto.html             #    Cryptocurrency quotes
│   ├── technical_analysis.html #    Technical chart analysis
│   ├── alpaca.html             #    Alpaca paper trading
│   ├── daily_agent.html        #    Daily analysis reports
│   └── chart_diagnostic.html   #    Chart diagnostic tool
│
├── static/                     # ── Frontend Assets ───────────────────
│   ├── css/style.css           #    Main stylesheet
│   └── js/
│       ├── dashboard.js        #    Dashboard interactivity
│       ├── lightweight-charts.js  # TradingView charting library
│       └── symbol-autocomplete.js # Symbol search autocomplete
│
├── docs/                       # ── Documentation ─────────────────────
│   ├── QUICKSTART.md
│   ├── README_WEB.md
│   ├── README_MODULES.md
│   ├── README_UNIFIED.md
│   ├── README_PaperTrading.md
│   ├── MONITORING_GUIDE.md
│   ├── REALTIME_TRADING_GUIDE.md
│   ├── 0DTE_Trading_Guide.md
│   ├── AUTO_STOP_LOSS_GUIDE.md
│   ├── CALL_PREMIUM_GUIDE.md
│   ├── PRE_POST_MARKET_FEATURE.md
│   ├── SHORT_SQUEEZE_EXPANSION.md
│   ├── WEB_APP_FEATURES.md
│   └── Weekly_Screener_Guide.md
│
├── data/                       # ── Runtime Data (gitignored) ────────
│   ├── active_positions.json
│   ├── ai_bot_state.json
│   ├── top_picks.json
│   ├── alpaca_config.json
│   ├── paper_trading_state.json
│   ├── *.csv / *.json scanner outputs
│   └── daily_analysis_reports/
│
```

---

## Architecture

### Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Dashboard                          │
│              Flask + SocketIO (port 5000)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │
│  │Dashboard │ │Scanners  │ │Options   │ │AI Trading│ ...   │
│  │  Page    │ │  Page    │ │  Page    │ │  Page    │       │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘      │
│       └─────────────┴────────────┴─────────────┘            │
│                         │                                    │
│                    14 API Blueprints (routes/)                │
└─────────────────────────┬───────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              │   Services Layer      │
              │  market_data (cache)  │
              │  bot_engine (state)   │
              │  indicators           │
              │  alpaca_service       │
              │  symbols / utils      │
              └───────────┬───────────┘
                          │
         ┌────────────────┼────────────────┐
         │                │                │
   ┌─────┴─────┐  ┌──────┴──────┐  ┌──────┴──────┐
   │  Scanners  │  │   Trading   │  │ Monitoring  │
   │ (13 modules)│  │ (6 modules) │  │ (6 modules) │
   └─────┬─────┘  └──────┬──────┘  └──────┬──────┘
         │                │                │
   ┌─────┴────────────────┴────────────────┘
   │         config/master_stock_list.py
   │         (single source of truth for symbols)
   └──────────────────────┬─────────────────
                          │
                    yfinance (market data)
```

### Key Design Decisions

- **No database** — All state stored in JSON files at project root for portability
- **No paid APIs** — All market data from yfinance (free)
- **Centralized caching** — `services/market_data.py` provides multi-level caching with rate limiting
- **Single symbol source** — `config/master_stock_list.py` is the only place stock universes are defined
- **Dual interface** — Every scanner works both as a CLI script (`python3 -m scanners.X`) and via the web dashboard
- **Fail-safe scanning** — Errors on individual tickers are caught silently; scanners never crash

### Data Flow

```
yfinance API  →  services/market_data.py (cache layer)
                        │
                  ┌─────┴──────┐
                  │            │
             routes/*.py    scanners/*.py
             (web API)      (CLI + web)
                  │            │
                  └─────┬──────┘
                        │
                  JSON state files
           (active_positions.json, top_picks.json, etc.)
```

---

## Web Dashboard Pages

| Page | URL | Description |
|------|-----|-------------|
| **Dashboard** | `/` | Market overview, indices, sectors, top movers |
| **Scanners** | `/scanners` | Run all scanners, view top picks |
| **Options** | `/options` | Options analysis, PCR, swing setups |
| **Monitoring** | `/monitoring` | Active positions with live P&L |
| **AI Trading** | `/ai-trading` | AI bot control, auto-cycle trading |
| **AI Analysis** | `/ai-analysis` | Candlestick patterns, price predictions |
| **Autonomous** | `/autonomous` | DeepSeek AI autonomous trader |
| **Crypto** | `/crypto` | Cryptocurrency quotes & movers |
| **Technical** | `/technical-analysis` | Interactive chart with indicators |
| **Alpaca** | `/alpaca` | Paper trading via Alpaca API |
| **Daily Agent** | `/daily-agent` | Daily performance analysis reports |
| **Chart Diagnostic** | `/chart-diagnostic` | Chart debugging tool |

---

## Scanners

| Scanner | Module | Output | Description |
|---------|--------|--------|-------------|
| Unified Top Picks | `scanners.unified_trading_system` | `top_picks.json` | Daily top 5 options, stocks, and ETFs |
| Short Squeeze | `scanners.short_squeeze_scanner` | `short_squeeze_candidates.csv` | Stocks with SI >20%, 20-30%+ move potential |
| Triple Confirmation | `scanners.triple_confirmation_scanner` | `triple_confirmation_picks.json` | SuperTrend + VWAP + MACD alignment (daily) |
| Triple Intraday | `scanners.triple_confirmation_intraday` | `triple_confirmation_intraday.json` | Same-day setups on 5-15 min timeframes |
| Triple Positional | `scanners.triple_confirmation_positional` | `triple_confirmation_positional.json` | 1-2 week holds on weekly timeframes |
| Options Predictor | `scanners.next_day_options_predictor` | Live display | 1DTE option setups with 1:3–1:5 R:R |
| AI Analysis | `scanners.ai_stock_analysis` | Live display | 25+ candlestick patterns, price predictions |
| Beaten Down | `scanners.beaten_down_quality_scanner` | `beaten_down_quality_stocks.csv` | Quality stocks at crash-level discounts |
| Market News | `scanners.market_news_scanner` | Live display | Pre/post-market news analysis |
| Golden Cross | `scanners.us_market_golden_cross_scanner` | Live display | 50/200 EMA golden cross scanner |
| Weekly Top 100 | `scanners.weekly_screener_top100` | Live display | Weekly screener ($50-$200, 3:1 R:R) |

### Scoring System

All scanners use a **0–15 point scoring system**:

```
12–15 points  →  🟢 Strong Buy
 8–11 points  →  🟡 Moderate Buy
 4–7  points  →  🟠 Weak / Hold
 0–3  points  →  🔴 Avoid
```

Signals: RSI, MACD, EMA crossovers, volume, ATR, and fundamentals. RSI >80 triggers a penalty.

---

## Trading Modules

| Module | Description |
|--------|-------------|
| `trading.autonomous_deepseek_trader` | DeepSeek AI-powered fully autonomous trader (paper/live) |
| `trading.autonomous_trading_agent` | Rule-based entry/exit with stop-loss and take-profit |
| `trading.spy_qqq_options_trader` | SPY/QQQ daily options targeting 1:10 R:R |
| `trading.auto_0dte_monitor` | Autonomous 0DTE PUT trader with monitoring until 2:50 PM CT |
| `trading.live_0dte_trader` | Live 0DTE options execution until 3:50 PM ET |
| `trading.execute_trade_setup` | Real-time options entry/exit setup generator |

### Risk Defaults

```python
stop_loss_options  = entry_premium * 0.50    # 50% stop
take_profit_targets = [entry * 2, entry * 3, entry * 4]  # 1:2, 1:3, 1:4
stop_loss_stock    = entry - (ATR * 1.5)
mandatory_exit     = "3:45 PM ET"            # Options close before expiry
```

---

## Monitoring

```bash
python3 -m monitoring.trade_monitor_alerts       # 5-min alerts on active positions
python3 -m monitoring.analyze_and_monitor         # Full pipeline: predict → setup → monitor
python3 -m monitoring.realtime_trade_monitor      # Real-time options position tracker
```

Monitors read from `active_positions.json` and check prices every 5 minutes against entry, target, and stop-loss levels.

---

## Configuration

### Symbol Universes (`config/master_stock_list.py`)

All stock/ETF symbol lists are defined in a single file:

| Function / Constant | Description |
|---------------------|-------------|
| `get_master_stock_list()` | Full universe of options-eligible + regular stocks |
| `get_intraday_core_list()` | High-volume subset for intraday scanners |
| `MASTER_ETF_UNIVERSE` | ETFs for options and swing trading |

### Alpaca Paper Trading (optional)

```bash
# Configure via the web UI at /alpaca, or manually:
# Edit alpaca_config.json with your API key and secret
```

---

## Technical Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Python 3.8+, Flask, Flask-SocketIO |
| **Market Data** | yfinance (free, no API key) |
| **Real-time** | WebSockets via SocketIO |
| **Frontend** | Jinja2 templates, vanilla JS, TradingView Lightweight Charts |
| **Broker API** | Alpaca Markets (optional, paper trading) |
| **AI Agent** | DeepSeek API (optional, for autonomous trading) |
| **Scheduling** | ThreadPoolExecutor, background threads |
| **State** | JSON files (no database required) |

---

## Development

### Adding a New Scanner

1. Create `scanners/my_scanner.py`
2. Add `sys.path.insert(0, ...)` and import from `config.master_stock_list`
3. Use the 0–15 scoring system
4. Output to a JSON/CSV file at `PROJECT_ROOT`
5. Register an endpoint in `routes/scanners.py`
6. Add a section in `templates/scanners.html`

### Adding a New Web Page

1. Create `routes/my_page.py` with a Flask Blueprint
2. Register in `routes/__init__.py`
3. Create `templates/my_page.html`
4. Add navigation link in the base template

### Import Patterns

```python
# In scanners/, trading/, monitoring/, scripts/:
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.master_stock_list import get_master_stock_list
from config import PROJECT_ROOT

# In routes/:
from scanners.unified_trading_system import UnifiedTradingSystem
from services.market_data import cached_get_price
```

### Running the Server

```bash
# Development
python3 run.py

# Production (with Gunicorn)
gunicorn -k eventlet -w 1 -b 0.0.0.0:5000 app.web_app:app
```

---

## Documentation

Detailed guides are in the `docs/` directory:

| Guide | Topic |
|-------|-------|
| [QUICKSTART.md](docs/QUICKSTART.md) | Getting started in 5 minutes |
| [README_WEB.md](docs/README_WEB.md) | Web dashboard features & setup |
| [README_MODULES.md](docs/README_MODULES.md) | Module architecture deep-dive |
| [MONITORING_GUIDE.md](docs/MONITORING_GUIDE.md) | Position monitoring setup |
| [0DTE_Trading_Guide.md](docs/0DTE_Trading_Guide.md) | Zero-day-to-expiry trading |
| [AUTO_STOP_LOSS_GUIDE.md](docs/AUTO_STOP_LOSS_GUIDE.md) | Automated stop-loss system |
| [REALTIME_TRADING_GUIDE.md](docs/REALTIME_TRADING_GUIDE.md) | Real-time trade execution |
| [README_PaperTrading.md](docs/README_PaperTrading.md) | Alpaca paper trading setup |
| [Weekly_Screener_Guide.md](docs/Weekly_Screener_Guide.md) | Weekly screening strategy |

---

## License

Private project. All rights reserved.
