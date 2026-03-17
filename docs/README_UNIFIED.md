# 🚀 UNIFIED TRADING SYSTEM

## Complete Platform for Options, Stocks & ETFs

**One system. All your trading needs. Top 5 picks from each category.**

---

## 📊 What It Does

### Generates Top 5 Picks:
✅ **Options** - Best option trades with strikes, premiums, targets  
✅ **Stocks** - Top growth stocks with technical signals  
✅ **ETFs** - Strongest ETFs for stable returns  

### Features:
🔍 **Smart Scanning** - Analyzes 50+ tickers across all categories  
📈 **Technical Analysis** - RSI, MACD, EMAs, Volume, ATR  
🎯 **Scoring System** - Ranks by strength (0-15 scale)  
💰 **Trade Setups** - Complete entry/exit for options  
🔔 **Position Monitoring** - Track trades with 5-min alerts  
💾 **Persistent Storage** - Saves picks and positions  

---

## 🎯 Asset Coverage

### OPTIONS Universe (10 tickers):
- **Indices**: SPY, QQQ, IWM, DIA
- **Mega Caps**: AAPL, TSLA, NVDA, MSFT, AMZN, META

### STOCKS Universe (20 tickers):
- **Tech**: AAPL, MSFT, GOOGL, NVDA, TSLA, META, NFLX, ADBE, CRM
- **Finance**: JPM, V, MA, BAC
- **Healthcare**: JNJ, UNH
- **Retail**: AMZN, WMT, HD
- **Other**: PG, DIS

### ETFs Universe (20 tickers):
- **Broad Market**: SPY, QQQ, IWM, DIA, VTI, VOO
- **International**: EEM, EFA
- **Bonds/Commodities**: TLT, GLD
- **Sectors**: XLF, XLE, XLK, XLV, XLI, XLP, XLY, XLU, XLRE, XLC

---

## 🚀 Quick Start

### Run The System:
```bash
python3 unified_trading_system.py
```

### Menu Options:

**1️⃣ Generate Top 5 Picks** (Recommended First)
- Scans all tickers
- Ranks by technical strength
- Shows top 5 for each category
- Saves to file

**2️⃣ Monitor Active Positions**
- Track your trades
- 5-minute alerts
- P&L tracking
- Target/stop loss monitoring

**3️⃣ Add New Position**
- Enter option, stock, or ETF position
- Automatic target calculation
- Start monitoring immediately

**4️⃣ View Saved Picks**
- See previously generated picks
- Review without re-scanning

---

## 📊 Example Output

### Top 5 Options:
```
#1. TSLA - Score: 9/15
   💹 Price: $474.75 | RSI: 70.0
   🎯 PUT $478 @ $7.12
   💵 Cost: $712.12 | Target: $28.48 (1:4)
   ✅ Signals: Bullish EMAs, Above SMA50, RSI overbought

#2. META - Score: 8/15
   💹 Price: $657.43 | RSI: 60.4
   🎯 PUT $662 @ $9.86
   💵 Cost: $986.15 | Target: $39.45 (1:4)
   ✅ Signals: Bullish EMAs, RSI neutral, MACD bullish
```

### Top 5 Stocks:
```
#1. MA - Score: 10/15
   💹 Price: $569.03 | Change: +0.53%
   📊 RSI: 63.7 | Volume: 0.33x avg
   🏢 Sector: Financial Services
   ✅ Signals: Bullish EMAs, Above SMA50, RSI neutral

#2. JNJ - Score: 10/15
   💹 Price: $210.05 | Change: +0.36%
   📊 RSI: 54.5 | Volume: 0.33x avg
   🏢 Sector: Healthcare
   ✅ Signals: Bullish EMAs, Above SMA50, RSI neutral
```

### Top 5 ETFs:
```
#1. IWM - Score: 8/15
   💹 Price: $248.25 | Change: -0.66%
   📊 RSI: 53.1 | ATR: $3.49
   ✅ Signals: Bullish EMAs, Above SMA50, RSI neutral

#2. DIA - Score: 8/15
   💹 Price: $480.49 | Change: -0.31%
   📊 RSI: 58.4 | ATR: $4.49
   ✅ Signals: Bullish EMAs, Above SMA50, RSI neutral
```

---

## 🎯 Scoring System

### How Assets Are Scored (0-15 points):

#### Common Indicators (All Assets):
- **Trend** (4 pts): EMA alignment, price vs SMAs
- **Momentum** (4 pts): RSI levels, MACD signals
- **Volume** (1 pt): Above average activity

#### Asset-Specific:

**OPTIONS** (Extra 6 pts):
- High volatility (good for options)
- Strong momentum (large moves)

**STOCKS** (Extra 6 pts):
- Steady positive trend
- Large cap stability
- Sector strength

**ETFs** (Extra 6 pts):
- Low volatility (stable)
- Good liquidity
- Consistent performance

---

## 💰 Options Trade Setup

For each option pick, you get:

### Entry Details:
- **Type**: CALL or PUT
- **Strike**: Calculated based on ATR
- **Premium**: Affordable (0.3-1.5% of stock price)
- **Contract Cost**: Premium × 100

### Targets (1:2, 1:3, 1:4):
- **Target 1**: +100% (Close 33%)
- **Target 2**: +200% (Close 33%)
- **Target 3**: +300% (Close 34%)
- **Stop Loss**: -50% (Max loss)

### Example:
```
AAPL CALL $273 @ $1.62
Cost: $162/contract
Target 1: $3.24 (+100%)
Target 2: $4.86 (+200%)
Target 3: $6.48 (+300%)
Stop: $0.81 (-50%)
```

---

## 🔔 Position Monitoring

### Once You Enter a Trade:

1. **Add Position** (Option 3)
   - Enter ticker, type, price, quantity
   - System calculates targets

2. **Start Monitoring** (Option 2)
   - Alerts every 5 minutes
   - Real-time P&L updates
   - Target hit notifications
   - Stop loss warnings

### Alert Example:
```
🔔 ALERT - 11:15:00 AM
====================================

📊 AAPL (OPTION)
   Entry: $1.62 | Current: $2.10
   P&L: $96.00 (+29.6%)
   
🎯 Targets:
   1:2: $3.24 - ⏳ PENDING
   1:3: $4.86 - ⏳ PENDING
   1:4: $6.48 - ⏳ PENDING

⏰ TIME REMAINING: 3h 35m until exit
```

---

## 📁 Files Created

### Main File:
- **unified_trading_system.py** - Complete system (one file!)

### Data Files (Auto-Generated):
- **top_picks.json** - Saved top 5 picks
- **active_positions.json** - Your open positions
- **trade_alerts.log** - Alert history

---

## 🎓 Typical Workflow

### Morning Routine:
```bash
# 1. Generate picks
python3 unified_trading_system.py
> Select: 1 (Generate Top 5 Picks)

# 2. Review picks for all categories
# Options: Best volatility plays
# Stocks: Growth opportunities  
# ETFs: Stable investments

# 3. Enter trades in your broker
# - Options: Follow exact setup (strike, premium)
# - Stocks: Buy at current price or limit
# - ETFs: Build core positions
```

### After Entry:
```bash
# 4. Add position to system
python3 unified_trading_system.py
> Select: 3 (Add New Position)
> Enter details

# 5. Start monitoring
> Select: 2 (Monitor Active Positions)
> Get alerts every 5 minutes
```

### Throughout Day:
- Monitor alerts
- Take profits at targets
- Respect stop losses
- Exit options by 2:50 PM CT

---

## ⚙️ Customization

### Change Alert Frequency:
Edit line 15 in file:
```python
system = UnifiedTradingSystem(alert_interval=5)  # Change to 2, 10, etc.
```

### Add More Tickers:
Edit lines 21-26:
```python
self.options_universe = ['SPY', 'QQQ', ...]  # Add more
self.stock_universe = ['AAPL', 'MSFT', ...]  # Add more
self.etf_universe = ['SPY', 'QQQ', ...]      # Add more
```

### Adjust Scoring:
Edit `score_asset()` method (lines 145-200) to change criteria.

---

## 🔍 Technical Indicators Used

### Trend:
- **EMA 9/21**: Short-term momentum
- **SMA 50/200**: Long-term trend
- **Golden/Death Cross**: Major signals

### Momentum:
- **RSI (14)**: Overbought/oversold
- **MACD**: Trend strength
- **ROC**: Rate of change

### Volatility:
- **ATR (14)**: Average True Range
- **Bollinger Bands**: Price extremes

### Volume:
- **Volume Ratio**: Current vs 20-day avg
- **Accumulation**: Buying pressure

---

## 📊 Performance Tips

### For Options:
✅ Trade highest volatility picks (TSLA, NVDA)  
✅ Use 1DTE for next-day trades  
✅ Take profits at 1:2 minimum  
✅ Never hold past 2:50 PM CT  

### For Stocks:
✅ Focus on high-score picks (9-10)  
✅ Buy on dips when RSI < 40  
✅ Hold for trend continuation  
✅ Use wider stops (5-10%)  

### For ETFs:
✅ Build core positions  
✅ Dollar-cost average  
✅ Hold through volatility  
✅ Diversify across sectors  

---

## ⚠️ Important Notes

### Options Trading:
1. **Exit Time**: MUST close by 2:50 PM CT (3:50 PM ET)
2. **Premium Cap**: Automatically kept < 1.5% of stock price
3. **OTM Strikes**: Used for affordability
4. **Risk**: Never risk more than you can afford to lose

### Data Accuracy:
1. **Live Prices**: From Yahoo Finance (slight delay)
2. **Premium Estimates**: Simplified calculations
3. **Verify**: Always check broker for actual prices
4. **Internet**: Required for real-time data

### Position Limits:
1. **Options**: Start with 1-2 contracts
2. **Stocks**: Size based on account
3. **ETFs**: Build gradually
4. **Diversification**: Don't go all-in on one pick

---

## 🛠️ Requirements

### Python Packages:
```bash
pip install yfinance pandas numpy
```

### System:
- Python 3.7+
- Internet connection
- Terminal access

---

## 📈 Success Metrics

Track your performance:

### Win Rates (Target):
- **Options**: 55-60%
- **Stocks**: 60-70%
- **ETFs**: 70-80%

### Average Returns:
- **Options**: 1:3 to 1:4 ratio on winners
- **Stocks**: 5-15% per position
- **ETFs**: 3-10% per quarter

### Risk Management:
- Max loss per trade: 2% of account
- Max positions: 3-5 at once
- Stop loss: Always use them!

---

## 🔄 Update Frequency

### Generate New Picks:
- **Options**: Daily (market conditions change)
- **Stocks**: Weekly (trends develop slower)
- **ETFs**: Monthly (longer-term plays)

### Best Times:
- **Morning**: 9:30-10:30 AM (opening volatility)
- **Midday**: 12:00-1:00 PM (reassessment)
- **Afternoon**: 2:00-3:00 PM (final setups)

---

## 💡 Pro Tips

1. **Start Small**: Test with 1 contract/100 shares
2. **Follow Signals**: Trust the top-scored picks
3. **Partial Profits**: Take money off table at targets
4. **Review Daily**: Learn from what works
5. **Stay Disciplined**: Rules exist for a reason

---

## 📞 Quick Reference

```bash
# Generate picks
python3 unified_trading_system.py
> 1

# Monitor positions
python3 unified_trading_system.py
> 2

# Add position
python3 unified_trading_system.py  
> 3

# View saved picks
python3 unified_trading_system.py
> 4
```

---

## ✅ System Status

| Component | Status | Description |
|-----------|--------|-------------|
| Options Scanner | ✅ | Top 5 from 10 tickers |
| Stock Scanner | ✅ | Top 5 from 20 tickers |
| ETF Scanner | ✅ | Top 5 from 20 tickers |
| Scoring Engine | ✅ | 15-point scale |
| Trade Setup | ✅ | Auto-calculated |
| Position Monitor | ✅ | 5-min alerts |
| Data Storage | ✅ | JSON persistence |
| Alert Logging | ✅ | Complete history |

---

## 🎯 Bottom Line

**One Python file. Three asset classes. Top 5 picks each. Simple.**

- No complex configuration
- No multiple scripts
- No confusion
- Just results.

Run it. Get picks. Trade smart. Monitor positions. Profit.

---

**Last Updated**: December 17, 2025  
**Version**: 2.0 - Unified Platform  
**File**: unified_trading_system.py (18KB)  

🚀 **Ready to use NOW!**
