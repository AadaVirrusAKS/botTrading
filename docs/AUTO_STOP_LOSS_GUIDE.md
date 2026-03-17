# 🛑 Automatic Stop Loss System - User Guide

## Overview

The Trading UI App now features **automatic stop loss execution** that monitors your positions in real-time and automatically closes them when prices hit your predefined stop loss levels.

## Key Features

### 1. Automatic Position Closing 🔴
- **Real-time monitoring**: Every 30 seconds during auto-refresh
- **Instant execution**: Position is closed immediately when current price ≤ stop loss
- **Automatic notification**: Pop-up alert informs you when a stop loss is triggered
- **Audit trail**: Notes added to closed position indicating auto-close price

### 2. Early Warning System ⚠️
- **Visual alerts**: Ticker symbols display ⚠️ when within 10% of stop loss
- **Color coding**: Row turns orange when approaching stop loss
- **Console warnings**: Terminal displays distance to stop loss percentage

### 3. Toggle Control 🎛️
- **Enable/Disable**: Use "🛑 Auto Stop Loss" checkbox to turn feature on/off
- **Default state**: Enabled by default for maximum protection
- **Manual override**: Uncheck to disable auto-closing (not recommended)

## How It Works

### Position Monitoring Flow
```
1. Auto-refresh fetches live prices (every 30 seconds)
   ↓
2. System checks each ACTIVE position:
   - Is Auto Stop Loss enabled? ✅
   - Does position have a stop loss set? ✅
   - Is current price ≤ stop loss? ✅
   ↓
3. If ALL conditions met:
   → Close position immediately at current price
   → Update status to "CLOSED"
   → Add timestamp and note
   → Save to active_positions.json
   → Display warning notification
```

### Warning Indicators
- **Distance Calculation**: `((current_price - stop_loss) / stop_loss) * 100`
- **Warning Threshold**: 0% < distance ≤ 10%
- **Visual Markers**:
  - ⚠️ emoji added to ticker column
  - Row highlighted in orange
  - Console prints warning message

## Usage Examples

### Example 1: Options Position
```json
{
  "SPY_CALL_690": {
    "ticker": "SPY",
    "type": "option",
    "direction": "CALL",
    "strike": 690.0,
    "entry": 1.85,
    "stop_loss": 0.93,
    "status": "active"
  }
}
```

**Scenario A - Warning Phase**
- Current premium: $1.02
- Stop loss: $0.93
- Distance: 9.7% above stop
- **Result**: Row shows "SPY ⚠️" in orange

**Scenario B - Stop Loss Hit**
- Current premium: $0.91
- Stop loss: $0.93
- **Result**: Position auto-closed at $0.91
- **Notification**: "⚠️ STOP LOSS TRIGGERED - Position automatically closed at $0.91"

### Example 2: Stock Position
```json
{
  "AAPL_LONG": {
    "ticker": "AAPL",
    "type": "stock",
    "entry": 185.50,
    "stop_loss": 180.00,
    "quantity": 100,
    "status": "active"
  }
}
```

**Scenario**
- Current price: $179.95
- Stop loss: $180.00
- **Result**: Position auto-closed at $179.95
- **P&L**: ($185.50 - $179.95) × 100 = -$555.00
- **Notes**: "[AUTO-CLOSED: Stop Loss Hit @ $179.95]"

## Configuration

### Enable/Disable Auto Stop Loss
1. Navigate to **Monitor Positions** screen
2. Locate "🛑 Auto Stop Loss" checkbox (top control panel)
3. Check to enable (default) or uncheck to disable
4. No need to restart - takes effect immediately

### Setting Stop Loss Levels
When adding positions, always specify stop loss:
- **Options**: Typically 50% of entry premium (e.g., entry $2.00 → stop $1.00)
- **Stocks**: ATR-based (entry - 1.5 × ATR) or fixed percentage (5-10%)
- **Validation**: System will warn if stop loss is not set

## Best Practices

### ✅ DO
- **Always set stop loss** when adding positions
- **Keep auto stop loss enabled** for risk management
- **Monitor warnings** - act when ⚠️ appears
- **Review closed trades** to refine stop loss strategy
- **Use realistic stops** - too tight = frequent stops, too wide = large losses

### ❌ DON'T
- **Don't disable auto stop loss** unless backtesting/demo mode
- **Don't set stops too close** to entry (noise can trigger)
- **Don't ignore warnings** - ⚠️ means position is at risk
- **Don't manually close** if auto-close is about to trigger (let system work)

## Technical Details

### File Structure
Positions stored in `active_positions.json`:
```json
{
  "position_key": {
    "entry": 100.00,
    "stop_loss": 95.00,
    "current_price": 94.50,  // Fetched live
    "status": "active" → "closed",  // Auto-updated
    "exit": 94.50,  // Set when closed
    "date_closed": "2026-01-08 12:30:45",
    "notes": "[AUTO-CLOSED: Stop Loss Hit @ $94.50]"
  }
}
```

### Performance
- **Refresh interval**: 30 seconds (configurable)
- **Latency**: < 1 second from trigger to execution
- **Concurrency**: Thread-safe file operations
- **Error handling**: Fails gracefully, logs errors

### Calculation Formulas
```python
# Stop loss trigger condition
if current_price <= stop_loss:
    close_position()

# Warning threshold
distance_to_stop = ((current_price - stop_loss) / stop_loss) * 100
if 0 < distance_to_stop <= 10:
    show_warning()

# P&L calculation (options)
pl_amount = (current_price - entry) * quantity * 100

# P&L calculation (stocks)
pl_amount = (current_price - entry) * quantity
```

## Troubleshooting

### Issue: Position not closing despite hitting stop
**Solution**: 
1. Check "🛑 Auto Stop Loss" is checked
2. Verify `stop_loss` field exists in position
3. Ensure `status` is "active" not "closed"
4. Check console for error messages

### Issue: Warning not showing
**Solution**:
1. Refresh table manually
2. Check current price is within 10% of stop
3. Verify position status is "active"

### Issue: Notification spam
**Solution**:
1. Each position triggers notification once
2. Refresh removes closed positions from monitoring
3. Use "🗑️ DELETE CLOSED" to clear completed trades

## Safety Features

### Multi-layer Protection
1. **Condition checks**: Auto-close only if ALL conditions met
2. **Immediate save**: Position updated to JSON before notification
3. **Audit trail**: Every auto-close logged with timestamp
4. **User control**: Toggle can disable feature anytime
5. **Manual override**: Can still manually close if needed

### Risk Mitigation
- **Prevents holding losers**: Automatic exit limits downside
- **Emotional discipline**: Removes hesitation to cut losses
- **Consistent execution**: No manual intervention needed
- **Real-time response**: 30-second monitoring catches drops

## Advanced Usage

### Backtesting Mode
Disable auto stop loss to review historical performance:
1. Uncheck "🛑 Auto Stop Loss"
2. Manually adjust exit prices to simulate stops
3. Compare auto vs manual execution

### Demo Trading
Use `demo_monitoring.py` to test without live positions:
```bash
python3 demo_monitoring.py
```

### Custom Intervals
Modify refresh rate in code (not recommended < 10 seconds):
```python
# trading_ui_app.py line ~1285
self.refresh_timer = self.root.after(30000, self.start_auto_refresh)
# Change 30000 (30s) to desired milliseconds
```

## Related Features

- **Auto-refresh**: Updates prices every 30 seconds
- **Manual refresh**: Force update with "🔄 REFRESH NOW"
- **Position notes**: Add context to trades
- **Delete closed**: Clean up completed trades
- **Excel export**: Export P&L history (future feature)

## Support

For issues or feature requests:
1. Check console output for errors
2. Verify `active_positions.json` structure
3. Review this guide's troubleshooting section
4. Submit bug report with error logs

---

**Version**: 1.0  
**Last Updated**: January 8, 2026  
**Feature**: Automatic Stop Loss Execution  
**Status**: ✅ Production Ready
