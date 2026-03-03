import pandas as pd
import numpy as np
import plotly.graph_objects as go
import yfinance as yf

# -----------------------------
# NVIDIA (NVDA) BACKTEST ANALYSIS
# -----------------------------

print('=' * 70)
print('NVIDIA (NVDA) SWING TRADING BACKTEST')
print('=' * 70)

# Download NVDA historical data
print('\n📊 Downloading NVDA data...')
ticker = yf.Ticker('NVDA')
data = ticker.history(period='2y')
data = data[['Close', 'Volume']]
print(f'Downloaded {len(data)} days of NVDA data from {data.index[0].date()} to {data.index[-1].date()}')

# Calculate indicators
short_ema = data['Close'].ewm(span=50, adjust=False).mean()
long_ema = data['Close'].ewm(span=200, adjust=False).mean()

# RSI calculation
delta = data['Close'].diff()
gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
rs = gain / loss
rsi = 100 - (100 / (1 + rs))

# ATR approximation using close price standard deviation
atr = data['Close'].rolling(window=14).std()

data['50EMA'] = short_ema
data['200EMA'] = long_ema
data['RSI'] = rsi
data['ATR'] = atr

# -----------------------------
# Swing Trading Strategy Logic
# -----------------------------
entry_signals = []
positions = []
stop_losses = []
take_profits = []

risk_reward_ratio = 3
risk_per_trade = 0.02
account_size = 10000

for i in range(len(data)):
    if i < 200:  # Ensure enough data for 200 EMA
        continue
    price = data['Close'].iloc[i]
    # Entry conditions
    if (price > data['50EMA'].iloc[i] and 
        price > data['200EMA'].iloc[i] and 
        data['50EMA'].iloc[i] > data['200EMA'].iloc[i] and
        40 < data['RSI'].iloc[i] < 70):
        entry_signals.append(i)
        stop_loss = price - data['ATR'].iloc[i] * 1.5
        take_profit = price + (price - stop_loss) * risk_reward_ratio
        stop_losses.append(stop_loss)
        take_profits.append(take_profit)
        positions.append(price)

# -----------------------------
# Backtesting Performance Metrics
# -----------------------------
profits = []
trades = []

for idx, entry_idx in enumerate(entry_signals):
    entry_price = positions[idx]
    sl = stop_losses[idx]
    tp = take_profits[idx]
    entry_date = data.index[entry_idx]
    
    # Look forward from entry to see if SL or TP was hit first
    hit_tp = False
    hit_sl = False
    exit_price = entry_price
    exit_date = entry_date
    
    for future_idx in range(entry_idx + 1, min(entry_idx + 30, len(data))):
        low = data['Close'].iloc[future_idx] * 0.995
        high = data['Close'].iloc[future_idx] * 1.005
        
        if low <= sl:
            hit_sl = True
            exit_price = sl
            exit_date = data.index[future_idx]
            break
        elif high >= tp:
            hit_tp = True
            exit_price = tp
            exit_date = data.index[future_idx]
            break
    
    # If neither hit, use closing price after 30 days or last available price
    if not hit_sl and not hit_tp:
        exit_idx = min(entry_idx + 30, len(data) - 1)
        exit_price = data['Close'].iloc[exit_idx]
        exit_date = data.index[exit_idx]
    
    profit = exit_price - entry_price
    profit_pct = (profit / entry_price) * 100
    profits.append(profit)
    trades.append({
        'Entry Date': entry_date.strftime('%Y-%m-%d'),
        'Entry Price': entry_price,
        'Exit Date': exit_date.strftime('%Y-%m-%d'),
        'Exit Price': exit_price,
        'Profit': profit,
        'Profit %': profit_pct,
        'Outcome': 'TP Hit' if hit_tp else ('SL Hit' if hit_sl else 'Time Exit')
    })

if len(profits) > 0:
    win_rate = (np.sum(np.array(profits) > 0) / len(profits)) * 100
    total_return = sum(profits)
    avg_profit = np.mean(profits)
    avg_win = np.mean([p for p in profits if p > 0]) if any(p > 0 for p in profits) else 0
    avg_loss = np.mean([p for p in profits if p <= 0]) if any(p <= 0 for p in profits) else 0
    sharpe_ratio = avg_profit / np.std(profits) if np.std(profits) > 0 else 0
    
    print(f'\n{"=" * 70}')
    print(f'BACKTEST RESULTS FOR NVIDIA (NVDA)')
    print(f'{"=" * 70}')
    print(f'Total Trades: {len(profits)}')
    print(f'Winning Trades: {sum(1 for p in profits if p > 0)}')
    print(f'Losing Trades: {sum(1 for p in profits if p <= 0)}')
    print(f'Win Rate: {win_rate:.2f}%')
    print(f'Total Return: ${total_return:.2f}')
    print(f'Average Profit per Trade: ${avg_profit:.2f}')
    print(f'Average Win: ${avg_win:.2f}')
    print(f'Average Loss: ${avg_loss:.2f}')
    print(f'Sharpe Ratio: {sharpe_ratio:.2f}')
    print(f'Profit Factor: {abs(avg_win / avg_loss):.2f}' if avg_loss != 0 else 'N/A')
    
    # Save detailed trades to Excel
    trades_df = pd.DataFrame(trades)
    trades_df.to_excel('nvda_backtest_results.xlsx', index=False)
    print(f'\n✓ Detailed backtest results saved to nvda_backtest_results.xlsx')
    
    print(f'\nRecent Trades (last 10):')
    print(trades_df.tail(10).to_string(index=False))
else:
    print('\nNo trades were generated with the current strategy parameters.')

# -----------------------------
# Stock Screener Logic
# -----------------------------
print(f'\n{"=" * 70}')
print('CURRENT MARKET CONDITIONS')
print(f'{"=" * 70}')
screener_candidates = data[(data['Close'] > data['50EMA']) & 
                          (data['Close'] > data['200EMA']) & 
                          (data['RSI'].between(40, 70)) & 
                          (data['Volume'] > data['Volume'].rolling(20).mean())]

if len(screener_candidates) > 0:
    print(f'\nDays meeting entry criteria (last 10):')
    print(screener_candidates[['Close', '50EMA', '200EMA', 'RSI']].tail(10).to_string())
else:
    print('\nNo recent days meeting entry criteria')

# Current status
latest = data.iloc[-1]
print(f'\nCurrent NVDA Status:')
print(f'  Price: ${latest["Close"]:.2f}')
print(f'  50 EMA: ${latest["50EMA"]:.2f}')
print(f'  200 EMA: ${latest["200EMA"]:.2f}')
print(f'  RSI: {latest["RSI"]:.2f}')
print(f'  Volume: {latest["Volume"]:,.0f}')

# Check current signal
if (latest['Close'] > latest['50EMA'] and 
    latest['Close'] > latest['200EMA'] and 
    latest['50EMA'] > latest['200EMA'] and
    40 < latest['RSI'] < 70):
    print(f'\n🎯 ENTRY SIGNAL ACTIVE!')
else:
    print(f'\n⏳ No entry signal currently')
    if latest['Close'] <= latest['50EMA']:
        print(f'  - Price below 50 EMA')
    if latest['Close'] <= latest['200EMA']:
        print(f'  - Price below 200 EMA')
    if latest['50EMA'] <= latest['200EMA']:
        print(f'  - 50 EMA below 200 EMA (Death Cross)')
    if not (40 < latest['RSI'] < 70):
        print(f'  - RSI outside 40-70 range (current: {latest["RSI"]:.2f})')

# -----------------------------
# Visual Roadmap Chart
# -----------------------------
print(f'\n📊 Generating chart...')
fig = go.Figure()
fig.add_trace(go.Scatter(x=data.index, y=data['Close'], mode='lines', name='NVDA Price', line=dict(color='blue')))
fig.add_trace(go.Scatter(x=data.index, y=data['50EMA'], mode='lines', name='50 EMA', line=dict(color='orange')))
fig.add_trace(go.Scatter(x=data.index, y=data['200EMA'], mode='lines', name='200 EMA', line=dict(color='red')))

# Add entry points
for idx, entry in enumerate(entry_signals):
    fig.add_trace(go.Scatter(
        x=[data.index[entry]], 
        y=[positions[idx]], 
        mode='markers', 
        marker=dict(color='green', size=10, symbol='triangle-up'),
        name='Entry' if idx == 0 else '',
        showlegend=(idx == 0)
    ))

fig.update_layout(
    title='NVIDIA (NVDA) Swing Trading Analysis - 2 Year Backtest',
    xaxis_title='Date',
    yaxis_title='Price ($)',
    hovermode='x unified',
    height=600
)

fig.write_html('nvda_trading_chart.html')
print(f'✓ Interactive chart saved to nvda_trading_chart.html')

print(f'\n{"=" * 70}')
print('Analysis Complete!')
print(f'{"=" * 70}')
