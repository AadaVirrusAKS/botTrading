# US Market Pulse - Web Trading Dashboard

A modern, real-time trading dashboard for USA markets similar to IntradayPulse.in.

## 🚀 Features

### Live Market Data
- **Real-time indices tracking** (S&P 500, Dow Jones, NASDAQ, Russell 2000, VIX)
- **Sector performance heatmap** using sector ETFs
- **Top gainers/losers** scanner with live updates
- **Market status indicator** (Pre-market, Regular, After-hours, Closed)

### Integrated Trading Scanners
- **Unified Trading System** - Top daily picks for options, stocks, and ETFs
- **Short Squeeze Scanner** - High-probability squeeze candidates
- **Quality Stocks Scanner** - Beaten-down quality opportunities
- All scanners use existing Python modules with proven algorithms

### Position Monitoring
- **Live P&L tracking** for active positions
- **WebSocket real-time updates** every 5-10 seconds
- **Stop-loss and target alerts**
- **Portfolio statistics** (total P&L, win rate, active trades)

### Technical Architecture
- **Backend**: Flask + Flask-SocketIO for WebSocket support
- **Frontend**: Modern responsive design with vanilla JavaScript
- **Data Source**: Yahoo Finance via yfinance (no API keys required)
- **Real-time**: Socket.IO for bidirectional communication
- **Responsive**: Mobile-friendly grid layouts

## 📦 Installation

### 1. Install Dependencies
```bash
# Install web application dependencies
pip install -r requirements_web.txt

# Or install individually
pip install flask flask-socketio flask-cors python-socketio eventlet
```

### 2. Verify Existing Dependencies
Make sure you have the trading system dependencies already installed:
```bash
pip install yfinance pandas numpy
```

## 🎯 Quick Start

### Run the Web Server
```bash
# Start the Flask development server
python3 web_app.py
```

The dashboard will be available at: **http://localhost:5000**

### Production Deployment
For production, use a WSGI server like Gunicorn:
```bash
# Install production server
pip install gunicorn eventlet

# Run with Gunicorn
gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:5000 web_app:app
```

## 🖥️ Usage

### Main Dashboard (`/`)
- View live market indices with automatic updates
- See sector performance heatmap
- Browse top gainers and losers
- Quick access to all scanners

### Scanners Page (`/scanners`)
Run any of the integrated trading scanners:
- **Unified System**: `?type=unified`
- **Short Squeeze**: `?type=short-squeeze`  
- **Quality Stocks**: `?type=quality-stocks`

Results show scored opportunities with signals and technical indicators.

### Live Monitoring (`/monitoring`)
- View all active positions from `active_positions.json`
- Real-time P&L updates via WebSocket
- Portfolio statistics dashboard
- Auto-refresh every 10 seconds

### Options Analysis (`/options`)
Placeholder page for future options flow integration.

## 🔧 How It Works

### Data Flow
1. **Server** fetches market data via yfinance
2. **API endpoints** expose data as JSON
3. **WebSocket** pushes real-time updates to clients
4. **Frontend** renders data with live updates

### File Structure
```
TradingCode/
├── web_app.py              # Flask server with all routes
├── templates/              # HTML pages
│   ├── index.html         # Main dashboard
│   ├── scanners.html      # Scanner results
│   ├── monitoring.html    # Live positions
│   └── options.html       # Options analysis
├── static/
│   ├── css/
│   │   └── style.css      # Modern responsive design
│   └── js/
│       └── dashboard.js   # Frontend logic + WebSocket
├── requirements_web.txt    # Web dependencies
└── README_WEB.md          # This file
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/market/overview` | GET | Market status + indices |
| `/api/market/sectors` | GET | Sector ETF performance |
| `/api/market/movers/<direction>` | GET | Top gainers/losers |
| `/api/scanner/unified` | GET | Unified system picks |
| `/api/scanner/short-squeeze` | GET | Short squeeze candidates |
| `/api/scanner/quality-stocks` | GET | Quality stock opportunities |
| `/api/positions/active` | GET | Active positions |
| `/api/quote/<symbol>` | GET | Single stock quote |

### WebSocket Events

| Event | Direction | Description |
|-------|-----------|-------------|
| `connect` | Server → Client | Connection established |
| `subscribe_quotes` | Client → Server | Subscribe to symbols |
| `quote_update` | Server → Client | Real-time price updates |

## 🎨 Customization

### Change Color Scheme
Edit `static/css/style.css` and modify the CSS variables:
```css
:root {
    --bg-dark: #0a0e27;
    --accent-blue: #4a9eff;
    --accent-purple: #8b5cf6;
    /* ... */
}
```

### Add More Scanners
1. Create scanner module (see `short_squeeze_scanner.py` pattern)
2. Import in `web_app.py`
3. Add route in API section:
```python
@app.route('/api/scanner/my-scanner')
def my_scanner():
    scanner = MyScanner()
    results = scanner.scan()
    return jsonify({'success': True, 'results': results})
```
4. Add button in `scanners.html`

### Modify Watchlist for Movers
Edit `get_top_movers()` function in `web_app.py`:
```python
watchlist = [
    'AAPL', 'MSFT', 'GOOGL', # Add your symbols
    # ...
]
```

### Change Refresh Intervals
In `static/js/dashboard.js`:
```javascript
// Market overview auto-refresh (default: 30 seconds)
this.refreshInterval = setInterval(() => {
    this.loadMarketOverview();
}, 30000); // Change to desired milliseconds
```

## 📊 Data Sources

- **Market Data**: Yahoo Finance (via yfinance library)
- **No API keys required** - completely free
- **Delayed quotes**: ~15-20 minute delay for free tier
- **Real-time**: Consider upgrading to paid providers for true real-time

### For Real-Time Data (Future)
To add true real-time data:
1. Sign up for Polygon.io, Finnhub, or Alpha Vantage
2. Add API key to environment variables
3. Modify data fetching functions in `web_app.py`
4. Update WebSocket handlers for streaming

Example with Polygon:
```python
import os
POLYGON_KEY = os.getenv('POLYGON_API_KEY')

def get_live_quote_polygon(symbol):
    url = f'https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}'
    params = {'apiKey': POLYGON_KEY}
    # ... fetch and parse
```

## 🚨 Important Notes

### Compliance
- Market data shown is for **informational purposes only**
- **Not investment advice** - disclaimers included on all pages
- Respect data provider terms of service
- Consider licensing requirements for commercial use

### Performance
- Dashboard uses threading for concurrent symbol fetching
- WebSocket updates are throttled to prevent overload
- Consider Redis caching for high-traffic scenarios

### Production Considerations
- Use HTTPS in production
- Implement rate limiting on API endpoints
- Add authentication if exposing publicly
- Monitor server resources (CPU/memory)
- Consider CDN for static assets

## 🐛 Troubleshooting

### WebSocket not connecting
```bash
# Check if eventlet is installed
pip install eventlet

# Restart server
python3 web_app.py
```

### Scanner results not showing
```bash
# Verify scanner modules are working
python3 unified_trading_system.py
python3 short_squeeze_scanner.py

# Check if JSON files are being generated
ls -la *.json
```

### Slow page loads
- Reduce watchlist size in `get_top_movers()`
- Increase ThreadPoolExecutor workers if CPU allows
- Enable caching for frequently accessed data

### Module import errors
```bash
# Ensure all dependencies are installed
pip install -r requirements_web.txt

# Check Python path includes current directory
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

## 🔮 Future Enhancements

- [ ] Options flow integration (Put/Call Ratio, unusual activity)
- [ ] Historical backtesting visualization
- [ ] Custom watchlist management
- [ ] Email/SMS alerts for signals
- [ ] Chart integration (TradingView widgets)
- [ ] Dark/light theme toggle
- [ ] User authentication and portfolios
- [ ] Export scanner results to CSV/Excel
- [ ] Mobile app (React Native)

## 📄 License

This web dashboard integrates with the existing TradingCode system and follows the same usage terms.

## 🤝 Support

For issues or questions:
1. Check existing scanner logs
2. Verify Flask server console output
3. Inspect browser console for JavaScript errors
4. Test API endpoints directly: `curl http://localhost:5000/api/market/overview`

---

**Built for USA market traders** 🇺🇸 | **Real-time insights** 📊 | **No API keys required** 🔓
