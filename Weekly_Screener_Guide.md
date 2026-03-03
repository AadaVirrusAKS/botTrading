# Weekly Stock Screener - Top 100 Companies

## Overview
Automated stock screener that analyzes the top 100 US companies and identifies the best 5 weekly trading opportunities based on technical analysis.

## How It Works

### Stock Universe
Analyzes 100 major US stocks across sectors:
- **Technology**: AAPL, MSFT, NVDA, GOOGL, AMZN, META, etc.
- **Finance**: JPM, BAC, WFC, GS, MS, V, MA, etc.
- **Healthcare**: UNH, LLY, JNJ, ABBV, MRK, etc.
- **Consumer**: WMT, HD, PG, COST, KO, MCD, etc.
- **Industrial/Energy**: XOM, CVX, CAT, BA, UNP, etc.

### Scoring System (Max 13 points)
1. **Price above 50 EMA**: +2 points (or +1 if within 2%)
2. **Price above 200 EMA**: +2 points
3. **Golden Cross (50 EMA > 200 EMA)**: +3 points (or +1 if approaching)
4. **RSI 40-70**: +3 points (optimal range)
5. **RSI 30-40**: +2 points (recovering)
6. **High Volume**: +2 points (20% above average)
7. **5-day Momentum**: +1 point (positive price change)
8. **Risk/Reward ≥ 3:1**: +1 point

### Strategy
- **Entry**: When score ≥ 3 (typically 10-12 for top trades)
- **Stop Loss**: Entry - (1.5 × ATR)
- **Take Profit**: Entry + 3× (Entry - Stop Loss)
- **Position Sizing**: 2% risk per trade

## Usage

### Run Weekly (Every Monday):
```bash
cd /Users/akum221/Documents/TradingCode
python3 weekly_screener_top100.py
```

### What Gets Created:
1. **Console Output**: Top 5 trades with detailed analysis
2. **Excel File**: `weekly_stock_screener_YYYYMMDD.xlsx` with 3 sheets:
   - **Top 5 Trades**: Your best opportunities with full trade setup
   - **All Qualified Stocks**: All stocks that scored ≥ 3
   - **Summary**: Statistics and portfolio overview

## Latest Results (Dec 3, 2025)

### 🏆 TOP 5 WEEKLY TRADES

| # | Ticker | Company | Score | Entry | Stop Loss | Take Profit | Shares | Potential $ |
|---|--------|---------|-------|-------|-----------|-------------|--------|-------------|
| 1 | AAPL | Apple Inc. | 12/13 | $285.13 | $275.16 | $315.05 | 20 | $598.43 |
| 2 | TFC | Truist Financial | 12/13 | $47.31 | $45.74 | $52.01 | 127 | $598.01 |
| 3 | WFC | Wells Fargo | 12/13 | $88.79 | $86.43 | $95.87 | 84 | $594.60 |
| 4 | GS | Goldman Sachs | 12/13 | $835.51 | $803.68 | $930.97 | 6 | $572.77 |
| 5 | MS | Morgan Stanley | 12/13 | $173.03 | $166.08 | $193.88 | 28 | $583.84 |

**Portfolio Summary:**
- Total Capital Required: $29,026.57
- Total Potential Profit: $2,947.65 (10.2% portfolio return if all hit TP)
- Total Risk: $1,000 (10% of $10,000 account)
- Dominant Sector: Financial Services (4 out of 5)

### Honorable Mentions (6-10):
6. C (Citigroup) - $106.52
7. AXP (American Express) - $369.50
8. USB (U.S. Bancorp) - $50.61
9. AMGN (Amgen) - $345.93
10. JPM (JP Morgan) - $311.40

## Key Insights

### Current Market Observations
✅ **97 out of 100 stocks qualified** - Very healthy market conditions  
✅ **Financial sector dominance** - Banks showing strong technical setups  
✅ **High average scores** - Top 5 all scored 12/13 (excellent signals)  
✅ **Diversification opportunity** - Multiple sectors represented in top 10

### Why These Trades?
All top 5 stocks show:
- Price trading above both 50 and 200-day moving averages
- Golden Cross configuration (bullish long-term trend)
- RSI in optimal 40-70 range (not overbought/oversold)
- Positive 5-day momentum
- Clean 3:1 risk/reward setups

## Portfolio Allocation Options

### Conservative (Pick Top 3)
- AAPL + TFC + WFC
- Capital: ~$19,000
- Risk: $600 (6%)
- Potential: ~$1,791

### Balanced (Pick Top 5)
- All 5 recommended stocks
- Capital: ~$29,027
- Risk: $1,000 (10%)
- Potential: ~$2,948

### Aggressive (Pick Top 10)
- Include honorable mentions
- Capital: ~$58,000
- Risk: $2,000 (20%)
- Potential: ~$5,800

## Weekly Workflow

1. **Monday Morning**: Run the screener
2. **Review Results**: Check Excel file for top 5 trades
3. **Execute Trades**: Enter positions at market open
4. **Set Orders**: Place stop loss and take profit orders
5. **Monitor**: Check positions daily, let stops/targets work
6. **Weekend**: Close any remaining positions or hold through weekend

## Tips for Success

✅ **Run Weekly**: Market conditions change - fresh analysis is key  
✅ **Diversify Sectors**: Don't put all capital in one sector  
✅ **Respect Stop Losses**: Let them protect your capital  
✅ **Be Patient**: Wait for high-score setups (10+ points)  
✅ **Review Performance**: Track which setups work best  
✅ **Adjust Seasonally**: Markets behave differently in different months  

## Compared to Single-Stock Strategies

| Feature | Weekly Screener | SPY Paper Trading | NVDA Paper Trading |
|---------|----------------|-------------------|-------------------|
| Stocks Analyzed | 100 | 1 | 1 |
| Opportunities | 5 picks weekly | 1 position max | 1 position max |
| Diversification | High | None | None |
| Win Rate (expected) | 35-40% | 38% | 32% |
| Capital Required | Flexible | $8,884 | Variable |
| Update Frequency | Weekly | Daily | Daily |

### Advantages:
- **More Opportunities**: 5 trades vs 1
- **Better Diversification**: Multiple sectors
- **Flexibility**: Choose how many to trade
- **Weekly Focus**: Less time intensive than daily monitoring

### Best Use:
Combine both approaches:
- **Weekly Screener**: For active swing trading (5 positions)
- **SPY/NVDA Paper Trading**: For set-and-forget automation (1-2 positions)

## Files in Your Workspace

```
/Users/akum221/Documents/TradingCode/
├── weekly_screener_top100.py          # Main screener script
├── weekly_stock_screener_20251203.xlsx # Latest results
├── paper_trading_spy.py                # SPY automation
├── paper_trading_nvidia.py             # NVDA automation
├── nvda_backtest.py                    # Historical analysis
└── swing_trading_strategy.py           # Original backtest
```

## Next Steps

1. **This Week**: Review the top 5 trades in the Excel file
2. **Choose Your Plays**: Pick 3-5 stocks based on your capital
3. **Execute**: Enter trades Monday/Tuesday when signals are fresh
4. **Next Monday**: Run screener again for new opportunities
5. **Track Results**: Keep Excel files to review performance over time

---

**Pro Tip**: The screener works best in trending markets. If fewer than 50 stocks qualify, market may be choppy - consider reducing position sizes or waiting for better conditions.
