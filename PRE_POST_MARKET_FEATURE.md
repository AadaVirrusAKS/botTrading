# Pre-Market & Post-Market Analysis Feature

## Overview
Added comprehensive pre-market and after-hours analysis to the web dashboard showing top 20 movers and losers during extended trading hours.

## Features

### 🌅 Pre-Market Analysis
- **Active**: 4:00 AM - 9:30 AM ET
- Scans 89+ high-volume stocks across all major sectors
- Top 20 gainers and losers with real-time price changes
- Volume comparison with average trading volume

### 🌆 After-Hours Analysis
- **Active**: 4:00 PM - 8:00 PM ET
- Same comprehensive stock coverage as pre-market
- Tracks price movements after regular market close
- Identifies significant post-earnings or news movers

### 📊 Extended Hours Watchlist
Monitors:
- **Mega caps**: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA
- **Tech & semiconductors**: AMD, INTC, QCOM, AVGO, ASML, TSM
- **Financials**: JPM, BAC, WFC, GS, MS, V, MA
- **Healthcare & biotech**: PFE, JNJ, UNH, MRNA, BNTX
- **Consumer**: WMT, HD, NKE, SBUX, MCD
- **Energy**: XOM, CVX, COP, SLB
- **E-commerce & fintech**: SHOP, SQ, PYPL, COIN, SOFI
- **Growth stocks**: PLTR, UBER, LYFT, ABNB, RIVN
- **Major ETFs**: SPY, QQQ, IWM, XLK, XLF, XLE

## Technical Implementation

### Backend (web_app.py)

#### New API Endpoints
```python
GET /api/market/premarket
GET /api/market/afterhours
```

#### Response Format
```json
{
  "success": true,
  "timestamp": "2026-01-15T07:30:00",
  "marketState": "PRE_MARKET",
  "marketDescription": "Pre-Market",
  "gainers": [
    {
      "symbol": "ASML",
      "price": 1345.43,
      "prevClose": 1270.00,
      "change": 75.43,
      "changePct": 5.93,
      "volume": 234567,
      "avgVolume": 1234567,
      "sessionType": "pre-market",
      "marketState": "PRE_MARKET"
    }
  ],
  "losers": [...],
  "totalScanned": 89
}
```

#### Key Functions
- `get_extended_hours_data(symbol)` - Fetches pre/post market prices from Yahoo Finance
- `get_premarket_movers(limit=20)` - Returns top gainers/losers for pre-market
- `get_afterhours_movers(limit=20)` - Returns top gainers/losers for after-hours

### Frontend (dashboard.js)

#### New Methods
- `loadExtendedHoursAnalysis()` - Auto-detects market state and loads appropriate data
- `renderExtendedHoursAnalysis(data, sessionLabel)` - Displays market status banner
- `renderExtendedMoversTable(movers, direction)` - Renders top 20 table

#### Auto-Refresh
- Extended hours data refreshes every 2 minutes
- Regular market data still refreshes every 30 seconds

### UI Components (index.html)

#### New Section
```html
<!-- Pre-Market & After-Hours Analysis -->
<div class="card">
  <div class="card-header">
    <h2>🌅 Pre-Market & After-Hours Analysis</h2>
    <button data-refresh="extended">🔄 Refresh</button>
  </div>
  <div class="card-body">
    <!-- Market status banner -->
    <!-- Top 20 Gainers -->
    <!-- Top 20 Losers -->
  </div>
</div>
```

## Usage

### Web Dashboard
1. Navigate to `http://localhost:5000`
2. Scroll to "Pre-Market & After-Hours Analysis" section
3. View automatically detected market state:
   - 🌅 Pre-Market (4:00 AM - 9:30 AM ET)
   - 🌆 After-Hours (4:00 PM - 8:00 PM ET)
   - 📊 Regular Hours (shows pre-market data)
4. Review top 20 gainers and losers ranked by % change

### Manual Refresh
Click the 🔄 Refresh button to manually update extended hours data

### API Access
```bash
# Pre-market movers
curl http://localhost:5000/api/market/premarket

# After-hours movers
curl http://localhost:5000/api/market/afterhours
```

## Market Status Detection

The system automatically detects:
- **Weekend**: Market closed
- **Pre-Market**: 4:00 AM - 9:30 AM ET (weekdays)
- **Regular Trading**: 9:30 AM - 4:00 PM ET
- **After-Hours**: 4:00 PM - 8:00 PM ET
- **Closed**: Outside trading hours

## Data Sources

- **Yahoo Finance API** (via yfinance library)
- No external API keys required
- Real-time pre/post market pricing
- Historical volume comparison

## Performance

- Scans 89 symbols in parallel using ThreadPoolExecutor
- 15 concurrent workers for optimal speed
- Typical response time: 3-5 seconds
- Graceful error handling for rate limits

## Example Output

```
🌅 Pre-Market Analysis
Pre-Market • Scanned 89 symbols

🚀 Top Gainers (Top 20)
#  Symbol  Price     Change
1  ASML    $1345.43  +75.43 (+5.93%)
2  NVDA    $145.20   +6.80 (+4.92%)
3  META    $612.50   +18.30 (+3.08%)
...

📉 Top Losers (Top 20)
#  Symbol  Price    Change
1  RIVN    $17.53   -1.31 (-7.01%)
2  LCID    $3.45    -0.18 (-4.96%)
3  SNAP    $12.80   -0.52 (-3.90%)
...
```

## Notes

- BRK.B may show "delisted" warnings (ignore - ticker format issue)
- Some stocks may not have pre/post market pricing available
- Volume data reflects most recent trading session
- Changes calculated from previous day's close

## Future Enhancements

- [ ] Add news catalyst detection for large movers
- [ ] Filter by minimum volume threshold
- [ ] Add price alerts for significant pre-market changes
- [ ] Historical pre-market performance tracking
- [ ] Sector breakdown of extended hours activity
