import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import plotly.graph_objects as go
import yfinance as yf
from config import PROJECT_ROOT, DATA_DIR

# -----------------------------
# 1. Download real SPY historical data for backtesting
# -----------------------------
print('Downloading SPY data...')
ticker = yf.Ticker('SPY')
data = ticker.history(period='2y')  # Get 2 years of data
data = data[['Close', 'Volume']]
print(f'Downloaded {len(data)} days of SPY data from {data.index[0].date()} to {data.index[-1].date()}')

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
# 2. Swing Trading Strategy Logic
# -----------------------------
entry_signals = []
positions = []
stop_losses = []
take_profits = []

risk_reward_ratio = 3  # 1:3 for swing trading
risk_per_trade = 0.02  # 2% risk
account_size = 10000

for i in range(len(data)):
    if i < 200:  # Ensure enough data for 200 EMA
        continue
    price = data['Close'].iloc[i]
    # Entry conditions: Price above both EMAs, RSI between 40-70 (bullish but not overbought)
    if (price > data['50EMA'].iloc[i] and 
        price > data['200EMA'].iloc[i] and 
        data['50EMA'].iloc[i] > data['200EMA'].iloc[i] and  # Golden cross confirmation
        40 < data['RSI'].iloc[i] < 70):  # Expanded RSI range
        entry_signals.append(i)
        stop_loss = price - data['ATR'].iloc[i] * 1.5
        take_profit = price + (price - stop_loss) * risk_reward_ratio
        stop_losses.append(stop_loss)
        take_profits.append(take_profit)
        positions.append(price)

# -----------------------------
# 3. Backtesting Performance Metrics (Using Actual Price Movement)
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
    
    for future_idx in range(entry_idx + 1, min(entry_idx + 30, len(data))):  # Look 30 days forward max
        low = data['Close'].iloc[future_idx] * 0.995  # Approximate intraday low
        high = data['Close'].iloc[future_idx] * 1.005  # Approximate intraday high
        
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
        'Entry Date': entry_date,
        'Entry Price': entry_price,
        'Exit Date': exit_date,
        'Exit Price': exit_price,
        'Profit': profit,
        'Profit %': profit_pct,
        'Outcome': 'TP Hit' if hit_tp else ('SL Hit' if hit_sl else 'Time Exit')
    })

if len(profits) > 0:
    win_rate = (np.sum(np.array(profits) > 0) / len(profits)) * 100
    total_return = sum(profits)
    avg_profit = np.mean(profits)
    sharpe_ratio = avg_profit / np.std(profits) if np.std(profits) > 0 else 0
    
    print(f'\n=== BACKTEST RESULTS FOR SPY ===')
    print(f'Total Trades: {len(profits)}')
    print(f'Win Rate: {win_rate:.2f}%')
    print(f'Total Return: ${total_return:.2f}')
    print(f'Average Profit per Trade: ${avg_profit:.2f}')
    print(f'Sharpe Ratio: {sharpe_ratio:.2f}')
    print(f'\nTrade Details:')
    trades_df = pd.DataFrame(trades)
    print(trades_df.to_string())
else:
    win_rate = 0
    sharpe_ratio = 0
    print('\nNo trades were generated with the current strategy parameters.')

# -----------------------------
# 4. Stock Screener Logic
# -----------------------------
screener_candidates = data[(data['Close'] > data['50EMA']) & (data['Close'] > data['200EMA']) & (data['RSI'].between(40, 60)) & (data['Volume'] > data['Volume'].rolling(20).mean())]
print('Stock Screener Candidates:')
print(screener_candidates.tail(10))

# -----------------------------
# 5. Visual Roadmap Chart
# -----------------------------
fig = go.Figure()
fig.add_trace(go.Scatter(x=data.index, y=data['Close'], mode='lines', name='Price'))
fig.add_trace(go.Scatter(x=data.index, y=data['50EMA'], mode='lines', name='50 EMA'))
fig.add_trace(go.Scatter(x=data.index, y=data['200EMA'], mode='lines', name='200 EMA'))

for idx, entry in enumerate(entry_signals):
    fig.add_shape(type='line', x0=data.index[entry], y0=positions[idx], x1=data.index[entry], y1=take_profits[idx], line=dict(color='green', dash='dot'))
    fig.add_shape(type='line', x0=data.index[entry], y0=positions[idx], x1=data.index[entry], y1=stop_losses[idx], line=dict(color='red', dash='dot'))

fig.update_layout(title='Swing Trading Plan Roadmap', xaxis_title='Date', yaxis_title='Price')
fig.write_json(os.path.join(DATA_DIR, 'swing_trading_roadmap.json'))
try:
    fig.write_image(os.path.join(DATA_DIR, 'swing_trading_roadmap.png'))
except Exception as e:
    print(f'Note: Could not save image file. Install kaleido if needed: pip install kaleido')
