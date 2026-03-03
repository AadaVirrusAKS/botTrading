# SPY Paper Trading System

## Overview
Automated paper trading system for SPY stock using swing trading strategy.

## Features
- **Daily monitoring** of SPY stock
- **Entry signals** based on: Price > 50EMA & 200EMA, Golden Cross, RSI 40-70
- **Risk management**: 2% risk per trade, 1:3 risk/reward ratio
- **Automatic position tracking** with stop loss and take profit
- **Excel logging** of all trades and current positions

## Files Created
1. **paper_trading_spy.py** - Main paper trading script
2. **spy_paper_trades.xlsx** - Complete trade history log
3. **spy_current_position.xlsx** - Current open position (if any)

## How to Use

### Run Daily Check (Manual)
```bash
cd /Users/akum221/Documents/TradingCode
python3 paper_trading_spy.py
```

### Set Up Automated Daily Runs (macOS)
Create a cron job to run daily at market close (4:30 PM ET):

```bash
crontab -e
```

Add this line (runs at 4:30 PM every weekday):
```
30 16 * * 1-5 cd /Users/akum221/Documents/TradingCode && /usr/local/bin/python3 paper_trading_spy.py >> trading_log.txt 2>&1
```

Or create a simple daily reminder to run manually.

## Trading Logic

### Entry Conditions (ALL must be true)
- Current price > 50-day EMA
- Current price > 200-day EMA  
- 50-day EMA > 200-day EMA (Golden Cross)
- RSI between 40-70 (bullish but not overbought)

### Exit Conditions (Either triggers)
- **Stop Loss**: Entry price - (1.5 × ATR)
- **Take Profit**: Entry price + 3 × (Entry - Stop Loss)

### Position Sizing
- Risk per trade: 2% of account balance
- Shares = (Account × 2%) / (Entry Price - Stop Loss)

## Current Status
- **Initial Capital**: $10,000
- **Current Position**: Check `spy_current_position.xlsx`
- **Trade History**: Check `spy_paper_trades.xlsx`

## Excel Files Structure

### spy_paper_trades.xlsx
| Column | Description |
|--------|-------------|
| Trade # | Sequential trade number |
| Entry Date | Date position was opened |
| Entry Price | SPY price at entry |
| Stop Loss | Stop loss price level |
| Take Profit | Take profit price level |
| Exit Date | Date position was closed |
| Exit Price | SPY price at exit |
| Shares | Number of shares traded |
| Profit/Loss | Dollar profit/loss |
| Profit % | Percentage profit/loss |
| Outcome | SL Hit / TP Hit |
| Account Balance | Balance after trade |

### spy_current_position.xlsx
Shows active position with entry details and targets (empty if no position).

## Monitoring Your Trades
1. Run the script daily after market close
2. Check Excel files for trade updates
3. Monitor account balance growth/decline
4. Review win rate and average profit over time

## Notes
- This is **paper trading only** - no real money involved
- Data downloaded from Yahoo Finance (free)
- Designed for swing trading (multi-day holds)
- One position at a time (no pyramiding)

## Tips
- Let the system run for at least 3-6 months to evaluate performance
- Don't modify positions manually - let stop loss/take profit work
- Review trades monthly to understand what's working
- Compare results to buy-and-hold SPY performance
