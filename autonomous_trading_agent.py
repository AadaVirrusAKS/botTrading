import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import time
warnings.filterwarnings('ignore')

# -----------------------------
# AUTONOMOUS TRADING AGENT
# Handles: Analysis, Entry, Stop Loss, Take Profit, Risk/Reward Management
# -----------------------------

class AutonomousTradingAgent:
    def __init__(self, capital=10000, risk_per_trade=0.02, min_risk_reward=5.0):
        """
        Initialize Trading Agent
        
        Parameters:
        - capital: Starting capital ($10,000 default)
        - risk_per_trade: Risk percentage per trade (2% default)
        - min_risk_reward: Minimum risk/reward ratio required (5:1 default)
        """
        self.initial_capital = capital
        self.available_capital = capital
        self.risk_per_trade = risk_per_trade
        self.min_risk_reward = min_risk_reward
        
        # Trading state
        self.open_positions = {}
        self.closed_trades = []
        self.trade_history = []
        
        print("=" * 100)
        print("🤖 AUTONOMOUS TRADING AGENT INITIALIZED")
        print("=" * 100)
        print(f"Initial Capital: ${self.initial_capital:,.2f}")
        print(f"Risk per Trade: {self.risk_per_trade * 100}%")
        print(f"Minimum Risk/Reward: 1:{self.min_risk_reward}")
        print(f"Max Risk per Trade: ${self.initial_capital * self.risk_per_trade:,.2f}")
        print("=" * 100)
    
    def calculate_indicators(self, data):
        """Calculate technical indicators"""
        if len(data) < 200:
            return None
            
        # EMAs
        data['50EMA'] = data['Close'].ewm(span=50, adjust=False).mean()
        data['200EMA'] = data['Close'].ewm(span=200, adjust=False).mean()
        
        # RSI
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        data['RSI'] = 100 - (100 / (1 + rs))
        
        # ATR for stop loss calculation
        data['ATR'] = data['Close'].rolling(window=14).std()
        
        # Volume MA
        data['Volume_MA'] = data['Volume'].rolling(window=20).mean()
        
        return data
    
    def analyze_stock(self, ticker):
        """
        Comprehensive stock analysis
        Returns: Analysis result with entry signal, stop loss, take profit
        """
        try:
            stock = yf.Ticker(ticker)
            data = stock.history(period='1y', interval='1d')
            
            if len(data) < 200:
                return None
            
            # Calculate indicators
            data = self.calculate_indicators(data)
            if data is None:
                return None
            
            latest = data.iloc[-1]
            prev = data.iloc[-2] if len(data) >= 2 else latest
            
            # Check Golden Cross
            golden_cross = latest['50EMA'] > latest['200EMA']
            recent_cross = golden_cross and (prev['50EMA'] <= prev['200EMA'] or 
                                            latest['50EMA'] < latest['200EMA'] * 1.02)
            
            # Price above both EMAs
            above_both = latest['Close'] > latest['50EMA'] and latest['Close'] > latest['200EMA']
            
            # RSI in good range (not overbought/oversold)
            rsi_good = 40 <= latest['RSI'] <= 75
            
            # Volume confirmation
            volume_good = latest['Volume'] > latest['Volume_MA']
            
            # Calculate entry, stop loss, and take profit
            entry_price = latest['Close']
            atr = latest['ATR']
            
            # Stop loss: 1.5 ATR below entry
            stop_loss = entry_price - (atr * 1.5)
            
            # Risk per share
            risk_per_share = entry_price - stop_loss
            
            # Take profit: Based on risk/reward ratio
            take_profit = entry_price + (risk_per_share * self.min_risk_reward)
            
            # Calculate position size based on risk management
            max_risk_amount = self.available_capital * self.risk_per_trade
            shares = int(max_risk_amount / risk_per_share) if risk_per_share > 0 else 0
            
            # Verify sufficient capital
            position_cost = shares * entry_price
            if position_cost > self.available_capital:
                shares = int(self.available_capital / entry_price)
                position_cost = shares * entry_price
            
            # Calculate actual risk/reward
            potential_profit = (take_profit - entry_price) * shares
            potential_loss = (entry_price - stop_loss) * shares
            actual_rr = potential_profit / potential_loss if potential_loss > 0 else 0
            
            # Score the trade
            score = 0
            if golden_cross: score += 5
            if above_both: score += 3
            if rsi_good: score += 3
            if volume_good: score += 2
            if recent_cross: score += 2
            
            # Entry signal: Must meet minimum criteria
            entry_signal = (golden_cross and above_both and 
                          actual_rr >= self.min_risk_reward and 
                          shares > 0 and score >= 10)
            
            return {
                'ticker': ticker,
                'entry_signal': entry_signal,
                'score': score,
                'entry_price': entry_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'shares': shares,
                'position_cost': position_cost,
                'risk_per_share': risk_per_share,
                'potential_profit': potential_profit,
                'potential_loss': potential_loss,
                'risk_reward_ratio': actual_rr,
                'rsi': latest['RSI'],
                'ema_50': latest['50EMA'],
                'ema_200': latest['200EMA'],
                'atr': atr,
                'golden_cross': golden_cross,
                'above_both': above_both,
                'rsi_good': rsi_good,
                'volume_good': volume_good
            }
            
        except Exception as e:
            print(f"  ❌ Error analyzing {ticker}: {str(e)}")
            return None
    
    def execute_buy(self, analysis):
        """Execute buy order based on analysis"""
        ticker = analysis['ticker']
        
        if not analysis['entry_signal']:
            print(f"  ⚠️  {ticker}: No entry signal")
            return False
        
        if analysis['position_cost'] > self.available_capital:
            print(f"  ⚠️  {ticker}: Insufficient capital")
            return False
        
        # Execute buy
        self.open_positions[ticker] = {
            'ticker': ticker,
            'entry_date': datetime.now(),
            'entry_price': analysis['entry_price'],
            'shares': analysis['shares'],
            'stop_loss': analysis['stop_loss'],
            'take_profit': analysis['take_profit'],
            'position_cost': analysis['position_cost'],
            'potential_profit': analysis['potential_profit'],
            'potential_loss': analysis['potential_loss'],
            'risk_reward_ratio': analysis['risk_reward_ratio'],
            'score': analysis['score']
        }
        
        # Update capital
        self.available_capital -= analysis['position_cost']
        
        # Log trade
        self.trade_history.append({
            'action': 'BUY',
            'date': datetime.now(),
            'ticker': ticker,
            'shares': analysis['shares'],
            'price': analysis['entry_price'],
            'cost': analysis['position_cost']
        })
        
        print(f"\n  ✅ BUY EXECUTED: {ticker}")
        print(f"     Shares: {analysis['shares']}")
        print(f"     Entry: ${analysis['entry_price']:.2f}")
        print(f"     Stop Loss: ${analysis['stop_loss']:.2f} (-{((analysis['stop_loss']/analysis['entry_price']-1)*-100):.1f}%)")
        print(f"     Take Profit: ${analysis['take_profit']:.2f} (+{((analysis['take_profit']/analysis['entry_price']-1)*100):.1f}%)")
        print(f"     Position Size: ${analysis['position_cost']:.2f}")
        print(f"     Risk/Reward: 1:{analysis['risk_reward_ratio']:.1f}")
        print(f"     Remaining Capital: ${self.available_capital:,.2f}")
        
        return True
    
    def monitor_positions(self):
        """Monitor open positions and execute sell based on stop loss or take profit"""
        if not self.open_positions:
            return
        
        print(f"\n{'=' * 100}")
        print(f"📊 MONITORING {len(self.open_positions)} OPEN POSITIONS")
        print(f"{'=' * 100}")
        
        positions_to_close = []
        
        for ticker, position in self.open_positions.items():
            try:
                # Get current price
                stock = yf.Ticker(ticker)
                current_data = stock.history(period='1d', interval='1m')
                
                if len(current_data) == 0:
                    continue
                
                current_price = current_data['Close'].iloc[-1]
                
                # Calculate P&L
                unrealized_pl = (current_price - position['entry_price']) * position['shares']
                unrealized_pl_pct = ((current_price / position['entry_price']) - 1) * 100
                
                print(f"\n{ticker}:")
                print(f"  Entry: ${position['entry_price']:.2f} | Current: ${current_price:.2f}")
                print(f"  P&L: ${unrealized_pl:,.2f} ({unrealized_pl_pct:+.2f}%)")
                print(f"  Stop Loss: ${position['stop_loss']:.2f} | Take Profit: ${position['take_profit']:.2f}")
                
                # Check stop loss
                if current_price <= position['stop_loss']:
                    print(f"  🛑 STOP LOSS TRIGGERED!")
                    positions_to_close.append((ticker, current_price, 'STOP_LOSS'))
                
                # Check take profit
                elif current_price >= position['take_profit']:
                    print(f"  🎯 TAKE PROFIT TRIGGERED!")
                    positions_to_close.append((ticker, current_price, 'TAKE_PROFIT'))
                
                else:
                    print(f"  ⏳ Position active - waiting for exit signal")
                    
            except Exception as e:
                print(f"  ❌ Error monitoring {ticker}: {str(e)}")
        
        # Execute sells
        for ticker, exit_price, exit_reason in positions_to_close:
            self.execute_sell(ticker, exit_price, exit_reason)
    
    def execute_sell(self, ticker, exit_price, exit_reason):
        """Execute sell order"""
        if ticker not in self.open_positions:
            return
        
        position = self.open_positions[ticker]
        
        # Calculate actual P&L
        sell_proceeds = exit_price * position['shares']
        realized_pl = sell_proceeds - position['position_cost']
        realized_pl_pct = ((exit_price / position['entry_price']) - 1) * 100
        
        # Update capital
        self.available_capital += sell_proceeds
        
        # Record closed trade
        closed_trade = {
            'ticker': ticker,
            'entry_date': position['entry_date'],
            'exit_date': datetime.now(),
            'entry_price': position['entry_price'],
            'exit_price': exit_price,
            'shares': position['shares'],
            'position_cost': position['position_cost'],
            'sell_proceeds': sell_proceeds,
            'realized_pl': realized_pl,
            'realized_pl_pct': realized_pl_pct,
            'exit_reason': exit_reason,
            'hold_days': (datetime.now() - position['entry_date']).days
        }
        
        self.closed_trades.append(closed_trade)
        
        # Log trade
        self.trade_history.append({
            'action': 'SELL',
            'date': datetime.now(),
            'ticker': ticker,
            'shares': position['shares'],
            'price': exit_price,
            'proceeds': sell_proceeds,
            'pl': realized_pl,
            'reason': exit_reason
        })
        
        # Remove from open positions
        del self.open_positions[ticker]
        
        print(f"\n  {'🟢' if realized_pl > 0 else '🔴'} SELL EXECUTED: {ticker}")
        print(f"     Exit Reason: {exit_reason}")
        print(f"     Exit Price: ${exit_price:.2f}")
        print(f"     Shares: {position['shares']}")
        print(f"     P&L: ${realized_pl:,.2f} ({realized_pl_pct:+.2f}%)")
        print(f"     Hold Period: {closed_trade['hold_days']} days")
        print(f"     New Capital: ${self.available_capital:,.2f}")
    
    def scan_and_trade(self, tickers):
        """
        Scan list of tickers and automatically execute trades
        """
        print(f"\n{'=' * 100}")
        print(f"🔍 SCANNING {len(tickers)} STOCKS FOR TRADING OPPORTUNITIES")
        print(f"{'=' * 100}")
        
        opportunities = []
        
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] Analyzing {ticker}...", end='\r')
            
            analysis = self.analyze_stock(ticker)
            
            if analysis and analysis['entry_signal']:
                opportunities.append(analysis)
        
        print(f"\n\n✅ Found {len(opportunities)} trading opportunities")
        
        # Sort by score
        opportunities.sort(key=lambda x: x['score'], reverse=True)
        
        # Execute trades
        executed = 0
        for opp in opportunities:
            if self.available_capital < opp['position_cost']:
                print(f"\n⚠️  Insufficient capital for {opp['ticker']} - skipping")
                continue
            
            if self.execute_buy(opp):
                executed += 1
        
        print(f"\n{'=' * 100}")
        print(f"✅ Executed {executed} trades")
        print(f"{'=' * 100}")
    
    def get_portfolio_summary(self):
        """Get current portfolio summary"""
        total_position_value = sum(pos['position_cost'] for pos in self.open_positions.values())
        total_capital = self.available_capital + total_position_value
        
        # Calculate unrealized P&L
        total_unrealized_pl = 0
        for ticker, position in self.open_positions.items():
            try:
                stock = yf.Ticker(ticker)
                current_data = stock.history(period='1d', interval='1m')
                if len(current_data) > 0:
                    current_price = current_data['Close'].iloc[-1]
                    unrealized_pl = (current_price - position['entry_price']) * position['shares']
                    total_unrealized_pl += unrealized_pl
            except:
                pass
        
        # Calculate realized P&L
        total_realized_pl = sum(trade['realized_pl'] for trade in self.closed_trades)
        
        # Win rate
        winning_trades = len([t for t in self.closed_trades if t['realized_pl'] > 0])
        total_trades = len(self.closed_trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'initial_capital': self.initial_capital,
            'available_capital': self.available_capital,
            'position_value': total_position_value,
            'total_capital': total_capital + total_unrealized_pl,
            'total_pl': total_realized_pl + total_unrealized_pl,
            'realized_pl': total_realized_pl,
            'unrealized_pl': total_unrealized_pl,
            'open_positions': len(self.open_positions),
            'closed_trades': total_trades,
            'win_rate': win_rate,
            'winning_trades': winning_trades,
            'losing_trades': total_trades - winning_trades
        }
    
    def display_summary(self):
        """Display portfolio summary"""
        summary = self.get_portfolio_summary()
        
        print(f"\n{'=' * 100}")
        print(f"📊 PORTFOLIO SUMMARY")
        print(f"{'=' * 100}")
        print(f"Initial Capital:        ${summary['initial_capital']:>12,.2f}")
        print(f"Available Capital:      ${summary['available_capital']:>12,.2f}")
        print(f"Position Value:         ${summary['position_value']:>12,.2f}")
        print(f"Total Capital:          ${summary['total_capital']:>12,.2f}")
        print(f"Total P&L:              ${summary['total_pl']:>12,.2f} ({(summary['total_pl']/summary['initial_capital']*100):+.2f}%)")
        print(f"  Realized P&L:         ${summary['realized_pl']:>12,.2f}")
        print(f"  Unrealized P&L:       ${summary['unrealized_pl']:>12,.2f}")
        print(f"\nOpen Positions:         {summary['open_positions']:>12}")
        print(f"Closed Trades:          {summary['closed_trades']:>12}")
        print(f"Win Rate:               {summary['win_rate']:>11.1f}%")
        print(f"  Winning Trades:       {summary['winning_trades']:>12}")
        print(f"  Losing Trades:        {summary['losing_trades']:>12}")
        print(f"{'=' * 100}")
    
    def save_report(self):
        """Save trading report to Excel"""
        filename = f"trading_agent_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            # Portfolio summary
            summary = self.get_portfolio_summary()
            summary_df = pd.DataFrame([summary])
            summary_df.to_excel(writer, sheet_name='Portfolio Summary', index=False)
            
            # Open positions
            if self.open_positions:
                positions_df = pd.DataFrame(self.open_positions.values())
                positions_df.to_excel(writer, sheet_name='Open Positions', index=False)
            
            # Closed trades
            if self.closed_trades:
                trades_df = pd.DataFrame(self.closed_trades)
                trades_df.to_excel(writer, sheet_name='Closed Trades', index=False)
            
            # Trade history
            if self.trade_history:
                history_df = pd.DataFrame(self.trade_history)
                history_df.to_excel(writer, sheet_name='Trade History', index=False)
        
        print(f"\n✅ Report saved to: {filename}")
        return filename


# -----------------------------
# MAIN EXECUTION
# -----------------------------

if __name__ == "__main__":
    # Initialize agent with $10,000 capital, 2% risk per trade, 5:1 minimum R/R
    agent = AutonomousTradingAgent(capital=10000, risk_per_trade=0.02, min_risk_reward=5.0)
    
    # Stock universe to scan (from previous scanner results)
    stock_universe = [
        # Technology
        'AAPL', 'AMD', 'CRWD', 'NTAP', 'AVGO',
        # Healthcare
        'EW', 'MDT', 'STE', 'HCA', 'IDXX',
        # Financial
        'BAC', 'BLK', 'CB', 'SCHW', 'RJF',
        # Consumer Cyclical
        'AMZN', 'F', 'PHM', 'PLNT', 'TSLA',
        # Consumer Defensive
        'KO', 'PEP', 'MNST', 'BG', 'CVS',
        # Energy
        'XOM', 'OVV', 'AM', 'EPD', 'PAA',
        # Industrials
        'HUBB', 'RTX', 'UNP', 'LMT', 'MMM',
        # Basic Materials
        'FCX', 'BTG', 'CLF', 'OR', 'FNV',
        # Real Estate
        'WELL', 'CBRE', 'SPG', 'HST',
        # Utilities
        'SWX', 'POR', 'OTTR', 'NWE',
        # Communication
        'EA', 'TTWO', 'GTN', 'MSGS', 'NXST'
    ]
    
    # Phase 1: Initial scan and trade execution
    print("\n" + "=" * 100)
    print("PHASE 1: INITIAL MARKET SCAN & TRADE EXECUTION")
    print("=" * 100)
    agent.scan_and_trade(stock_universe)
    
    # Display initial portfolio
    agent.display_summary()
    
    # Phase 2: Monitor positions (simulate periodic monitoring)
    print("\n" + "=" * 100)
    print("PHASE 2: POSITION MONITORING")
    print("=" * 100)
    agent.monitor_positions()
    
    # Final summary
    agent.display_summary()
    
    # Save report
    agent.save_report()
    
    print("\n" + "=" * 100)
    print("🤖 AUTONOMOUS TRADING AGENT SESSION COMPLETE")
    print("=" * 100)
