import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
import os

# -----------------------------
# PAPER TRADING SYSTEM FOR SPY
# -----------------------------

class PaperTradingSPY:
    def __init__(self, initial_capital=10000):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.position = None  # Current open position
        self.trades_file = 'spy_paper_trades.xlsx'
        self.positions_file = 'spy_current_position.xlsx'
        self.risk_reward_ratio = 3
        self.risk_per_trade = 0.02
        
    def calculate_indicators(self, data):
        """Calculate technical indicators"""
        # EMAs
        data['50EMA'] = data['Close'].ewm(span=50, adjust=False).mean()
        data['200EMA'] = data['Close'].ewm(span=200, adjust=False).mean()
        
        # RSI
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        data['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR
        data['ATR'] = data['Close'].rolling(window=14).std()
        
        return data
    
    def check_entry_signal(self, row):
        """Check if current conditions meet entry criteria"""
        if pd.isna(row['50EMA']) or pd.isna(row['200EMA']) or pd.isna(row['RSI']):
            return False
            
        # Entry conditions
        if (row['Close'] > row['50EMA'] and 
            row['Close'] > row['200EMA'] and 
            row['50EMA'] > row['200EMA'] and
            30 < row['RSI'] < 80):
            return True
        return False
    
    def check_exit_signal(self, current_price, position):
        """Check if stop loss or take profit hit"""
        if current_price <= position['Stop Loss']:
            return 'SL Hit', position['Stop Loss']
        elif current_price >= position['Take Profit']:
            return 'TP Hit', position['Take Profit']
        return None, None
    
    def load_trades_history(self):
        """Load existing trades from Excel"""
        if os.path.exists(self.trades_file):
            return pd.read_excel(self.trades_file)
        return pd.DataFrame(columns=[
            'Trade #', 'Entry Date', 'Entry Price', 'Stop Loss', 'Take Profit',
            'Exit Date', 'Exit Price', 'Shares', 'Profit/Loss', 'Profit %', 
            'Outcome', 'Account Balance'
        ])
    
    def load_current_position(self):
        """Load current open position if exists"""
        if os.path.exists(self.positions_file):
            df = pd.read_excel(self.positions_file)
            if len(df) > 0:
                return df.iloc[0].to_dict()
        return None
    
    def save_current_position(self, position, current_price=None):
        """Save current position to Excel with current P/L"""
        if position is None:
            # Clear position file
            pd.DataFrame().to_excel(self.positions_file, index=False)
        else:
            df = pd.DataFrame([position])
            
            # Add current P/L if price provided
            if current_price is not None:
                df['Current Price'] = current_price
                df['Unrealized P/L ($)'] = (current_price - position['Entry Price']) * position['Shares']
                df['Unrealized P/L (%)'] = ((current_price - position['Entry Price']) / position['Entry Price']) * 100
            
            df.to_excel(self.positions_file, index=False)
    
    def save_trade(self, trade):
        """Save completed trade to Excel with summary statistics"""
        trades_df = self.load_trades_history()
        new_trade = pd.DataFrame([trade])
        trades_df = pd.concat([trades_df, new_trade], ignore_index=True)
        
        # Create Excel writer for multiple sheets
        with pd.ExcelWriter(self.trades_file, engine='openpyxl') as writer:
            # Write trades to first sheet
            trades_df.to_excel(writer, sheet_name='Trades', index=False)
            
            # Calculate summary statistics
            if len(trades_df) > 0:
                total_trades = len(trades_df)
                winning_trades = len(trades_df[trades_df['Profit/Loss'] > 0])
                losing_trades = len(trades_df[trades_df['Profit/Loss'] <= 0])
                win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
                
                total_profit = trades_df['Profit/Loss'].sum()
                avg_profit = trades_df['Profit/Loss'].mean()
                avg_win = trades_df[trades_df['Profit/Loss'] > 0]['Profit/Loss'].mean() if winning_trades > 0 else 0
                avg_loss = trades_df[trades_df['Profit/Loss'] <= 0]['Profit/Loss'].mean() if losing_trades > 0 else 0
                
                largest_win = trades_df['Profit/Loss'].max()
                largest_loss = trades_df['Profit/Loss'].min()
                
                current_balance = trades_df['Account Balance'].iloc[-1]
                total_return = current_balance - self.initial_capital
                total_return_pct = (total_return / self.initial_capital) * 100
                
                # Create summary dataframe
                summary_data = {
                    'Metric': [
                        'Initial Capital',
                        'Current Balance',
                        'Total Return ($)',
                        'Total Return (%)',
                        '',
                        'Total Trades',
                        'Winning Trades',
                        'Losing Trades',
                        'Win Rate (%)',
                        '',
                        'Total Profit/Loss',
                        'Average P/L per Trade',
                        'Average Win',
                        'Average Loss',
                        'Largest Win',
                        'Largest Loss',
                        '',
                        'Profit Factor',
                    ],
                    'Value': [
                        f'${self.initial_capital:.2f}',
                        f'${current_balance:.2f}',
                        f'${total_return:.2f}',
                        f'{total_return_pct:.2f}%',
                        '',
                        total_trades,
                        winning_trades,
                        losing_trades,
                        f'{win_rate:.2f}%',
                        '',
                        f'${total_profit:.2f}',
                        f'${avg_profit:.2f}',
                        f'${avg_win:.2f}',
                        f'${avg_loss:.2f}',
                        f'${largest_win:.2f}',
                        f'${largest_loss:.2f}',
                        '',
                        f'{abs(avg_win / avg_loss):.2f}' if avg_loss != 0 else 'N/A',
                    ]
                }
                
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
        
        print(f"✓ Trade saved to {self.trades_file}")
    
    def run_daily_check(self):
        """Run daily paper trading check"""
        print("=" * 60)
        print(f"SPY PAPER TRADING - Daily Check")
        print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        # Download recent SPY data
        print("\n📊 Downloading SPY data...")
        ticker = yf.Ticker('SPY')
        data = ticker.history(period='1y')
        data = data[['Close', 'Volume']]
        
        # Calculate indicators
        data = self.calculate_indicators(data)
        
        # Get today's data
        latest = data.iloc[-1]
        current_price = latest['Close']
        current_date = data.index[-1]
        
        print(f"Current SPY Price: ${current_price:.2f}")
        print(f"50 EMA: ${latest['50EMA']:.2f}")
        print(f"200 EMA: ${latest['200EMA']:.2f}")
        print(f"RSI: {latest['RSI']:.2f}")
        
        # Load current position
        self.position = self.load_current_position()
        
        # Check if we have an open position
        if self.position is not None:
            print(f"\n📍 OPEN POSITION:")
            print(f"   Entry Date: {self.position['Entry Date']}")
            print(f"   Entry Price: ${self.position['Entry Price']:.2f}")
            print(f"   Stop Loss: ${self.position['Stop Loss']:.2f}")
            print(f"   Take Profit: ${self.position['Take Profit']:.2f}")
            print(f"   Shares: {self.position['Shares']:.0f}")
            
            current_pl = (current_price - self.position['Entry Price']) * self.position['Shares']
            current_pl_pct = ((current_price - self.position['Entry Price']) / self.position['Entry Price']) * 100
            print(f"   Current P/L: ${current_pl:.2f} ({current_pl_pct:.2f}%)")
            
            # Update position file with current P/L
            self.save_current_position(self.position, current_price)
            
            # Check for exit
            outcome, exit_price = self.check_exit_signal(current_price, self.position)
            
            if outcome:
                print(f"\n🚨 EXIT SIGNAL: {outcome}")
                # Close position
                trades_df = self.load_trades_history()
                trade_num = len(trades_df) + 1
                
                profit = (exit_price - self.position['Entry Price']) * self.position['Shares']
                profit_pct = ((exit_price - self.position['Entry Price']) / self.position['Entry Price']) * 100
                self.current_capital += profit
                
                trade = {
                    'Trade #': trade_num,
                    'Entry Date': self.position['Entry Date'],
                    'Entry Price': self.position['Entry Price'],
                    'Stop Loss': self.position['Stop Loss'],
                    'Take Profit': self.position['Take Profit'],
                    'Exit Date': current_date.strftime('%Y-%m-%d'),
                    'Exit Price': exit_price,
                    'Shares': self.position['Shares'],
                    'Profit/Loss': profit,
                    'Profit %': profit_pct,
                    'Outcome': outcome,
                    'Account Balance': self.current_capital
                }
                
                self.save_trade(trade)
                self.position = None
                self.save_current_position(None)
                
                print(f"✓ Position closed!")
                print(f"   Exit Price: ${exit_price:.2f}")
                print(f"   Profit/Loss: ${profit:.2f} ({profit_pct:.2f}%)")
                print(f"   New Account Balance: ${self.current_capital:.2f}")
        
        # If no position, check for entry signal
        if self.position is None:
            print(f"\n💼 Account Balance: ${self.current_capital:.2f}")
            
            if self.check_entry_signal(latest):
                print(f"\n🎯 ENTRY SIGNAL DETECTED!")
                
                # Calculate position size
                stop_loss = current_price - latest['ATR'] * 1.5
                take_profit = current_price + (current_price - stop_loss) * self.risk_reward_ratio
                
                # Position sizing based on risk
                risk_amount = self.current_capital * self.risk_per_trade
                risk_per_share = current_price - stop_loss
                shares = int(risk_amount / risk_per_share) if risk_per_share > 0 else 0
                
                if shares > 0:
                    position_value = shares * current_price
                    
                    # Create new position
                    self.position = {
                        'Entry Date': current_date.strftime('%Y-%m-%d'),
                        'Entry Price': current_price,
                        'Stop Loss': stop_loss,
                        'Take Profit': take_profit,
                        'Shares': shares,
                        'Position Value': position_value
                    }
                    
                    self.save_current_position(self.position, current_price)
                    
                    print(f"✓ New position opened!")
                    print(f"   Entry Price: ${current_price:.2f}")
                    print(f"   Stop Loss: ${stop_loss:.2f}")
                    print(f"   Take Profit: ${take_profit:.2f}")
                    print(f"   Shares: {shares}")
                    print(f"   Position Value: ${position_value:.2f}")
                    print(f"   Risk Amount: ${risk_amount:.2f}")
                else:
                    print("⚠️ Position size too small, skipping trade")
            else:
                print("\n⏳ No entry signal - waiting for conditions")
        
        print("\n" + "=" * 60)
        print("Daily check complete!")
        print("=" * 60)

# Run the paper trading system
if __name__ == "__main__":
    trader = PaperTradingSPY(initial_capital=10000)
    trader.run_daily_check()
