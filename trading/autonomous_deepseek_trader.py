#!/usr/bin/env python3
"""
AUTONOMOUS DEEPSEEK AI TRADING AGENT
Fully automated trading system using DeepSeek AI for decision making
⚠️ USE PAPER TRADING FIRST - Real money at risk!

Usage:
    # Set environment variables first:
    export DEEPSEEK_API_KEY='your-deepseek-api-key'
    export ALPACA_API_KEY='your-alpaca-key'
    export ALPACA_SECRET_KEY='your-alpaca-secret'
    
    # Run in paper trading mode (default):
    python3 autonomous_deepseek_trader.py
    
    # Run in live mode (⚠️ REAL MONEY):
    python3 autonomous_deepseek_trader.py --live
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time
import requests
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import warnings
warnings.filterwarnings('ignore')
from config import PROJECT_ROOT, DATA_DIR

# Import existing trading system
from scanners.unified_trading_system import UnifiedTradingSystem

# Optional: Alpaca for actual order execution
try:
    from alpaca.trading.client import TradingClient
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    print("⚠️  Alpaca not installed. Running in simulation mode only.")
    print("   Install with: pip install alpaca-py")


class DeepSeekAnalyzer:
    """DeepSeek AI integration for market analysis"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/v1/chat/completions"
        self.model = "deepseek-chat"
        self.decision_cache = {}
        self.cache_duration = 300  # 5 minutes
        
    def analyze_market(self, market_data: Dict) -> Dict:
        """Send market data to DeepSeek for AI analysis"""
        
        # Check cache first (avoid excessive API calls)
        cache_key = f"{market_data['ticker']}_{datetime.now().strftime('%Y%m%d%H%M')}"
        if cache_key in self.decision_cache:
            return self.decision_cache[cache_key]
        
        prompt = self._build_analysis_prompt(market_data)
        
        try:
            response = requests.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": """You are an expert quantitative trading analyst. 
                            Analyze market data and provide trading decisions.
                            Always respond with valid JSON only, no other text.
                            Be conservative - only recommend BUY with high confidence."""
                        },
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,  # Low temperature for consistent decisions
                    "max_tokens": 500
                },
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"⚠️  DeepSeek API error: {response.status_code}")
                return self._default_decision("API_ERROR")
            
            result = response.json()
            content = result['choices'][0]['message']['content']
            
            # Parse JSON from response
            decision = self._parse_decision(content)
            
            # Cache the decision
            self.decision_cache[cache_key] = decision
            
            return decision
            
        except Exception as e:
            print(f"⚠️  DeepSeek analysis error: {e}")
            return self._default_decision("EXCEPTION")
    
    def _build_analysis_prompt(self, data: Dict) -> str:
        """Build analysis prompt with market data"""
        return f"""Analyze this stock for a potential trade. Respond ONLY with JSON.

MARKET DATA:
- Ticker: {data.get('ticker', 'N/A')}
- Current Price: ${data.get('price', 0):.2f}
- Previous Close: ${data.get('prev_close', 0):.2f}
- Change: {data.get('change_pct', 0):.2f}%

TECHNICAL INDICATORS:
- RSI (14): {data.get('rsi', 50):.1f}
- MACD: {data.get('macd', 0):.4f}
- MACD Signal: {data.get('signal', 0):.4f}
- EMA9: ${data.get('ema9', 0):.2f}
- EMA21: ${data.get('ema21', 0):.2f}
- SMA20: ${data.get('sma20', 0):.2f}
- SMA50: ${data.get('sma50', 0):.2f}
- ATR: ${data.get('atr', 0):.2f}
- Volume Ratio (vs 20d avg): {data.get('volume_ratio', 1):.2f}x

TREND ANALYSIS:
- Price vs EMA9: {"ABOVE" if data.get('price', 0) > data.get('ema9', 0) else "BELOW"}
- Price vs SMA50: {"ABOVE" if data.get('price', 0) > data.get('sma50', 0) else "BELOW"}
- EMA Alignment: {"BULLISH (9>21)" if data.get('ema9', 0) > data.get('ema21', 0) else "BEARISH (9<21)"}
- MACD Crossover: {"BULLISH" if data.get('macd', 0) > data.get('signal', 0) else "BEARISH"}

DECISION RULES:
- RSI > 80 = OVERBOUGHT (avoid buying)
- RSI < 30 = OVERSOLD (potential bounce)
- RSI 40-60 = HEALTHY
- Volume Ratio > 1.5 = STRONG INTEREST
- Need at least 3 bullish signals for BUY

Respond with this exact JSON structure:
{{
    "action": "BUY" or "SELL" or "HOLD",
    "confidence": 0-100,
    "entry_price": suggested entry price,
    "stop_loss": suggested stop loss price,
    "target_price": suggested target price,
    "risk_reward": calculated risk/reward ratio,
    "signals": ["list", "of", "bullish/bearish", "signals"],
    "reason": "Brief explanation of decision"
}}"""

    def _parse_decision(self, content: str) -> Dict:
        """Parse AI response into decision dict"""
        try:
            # Try to extract JSON from response
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            
            decision = json.loads(content)
            
            # Validate required fields
            required = ['action', 'confidence', 'reason']
            for field in required:
                if field not in decision:
                    decision[field] = self._default_decision("PARSE_ERROR")[field]
            
            return decision
            
        except json.JSONDecodeError:
            print(f"⚠️  Could not parse AI response: {content[:100]}...")
            return self._default_decision("JSON_ERROR")
    
    def _default_decision(self, reason: str) -> Dict:
        """Return safe default decision"""
        return {
            "action": "HOLD",
            "confidence": 0,
            "entry_price": 0,
            "stop_loss": 0,
            "target_price": 0,
            "risk_reward": 0,
            "signals": [],
            "reason": f"Default HOLD due to: {reason}"
        }


class RiskManager:
    """Risk management and position sizing"""
    
    def __init__(self, 
                 max_daily_loss_pct: float = 0.05,
                 max_positions: int = 5,
                 risk_per_trade_pct: float = 0.02,
                 max_position_pct: float = 0.20):
        
        self.max_daily_loss_pct = max_daily_loss_pct  # 5% daily loss limit
        self.max_positions = max_positions
        self.risk_per_trade_pct = risk_per_trade_pct  # 2% risk per trade
        self.max_position_pct = max_position_pct  # 20% max in single position
        
        self.daily_pnl = 0.0
        self.starting_equity = 0.0
        self.current_positions = 0
        self.trade_log = []
        self.halted = False
        
    def set_starting_equity(self, equity: float):
        """Set starting equity for daily P&L tracking"""
        self.starting_equity = equity
        self.daily_pnl = 0.0
        
    def can_trade(self) -> Tuple[bool, str]:
        """Check if trading is allowed"""
        if self.halted:
            return False, "Trading manually halted"
        
        if self.starting_equity > 0:
            loss_pct = self.daily_pnl / self.starting_equity
            if loss_pct < -self.max_daily_loss_pct:
                self.halted = True
                return False, f"Daily loss limit hit: {loss_pct:.1%}"
        
        if self.current_positions >= self.max_positions:
            return False, f"Max positions reached: {self.current_positions}/{self.max_positions}"
        
        return True, "OK"
    
    def calculate_position_size(self, 
                                equity: float, 
                                entry_price: float, 
                                stop_loss: float) -> int:
        """Calculate position size based on risk"""
        if entry_price <= 0 or stop_loss <= 0:
            return 0
        
        risk_amount = equity * self.risk_per_trade_pct
        risk_per_share = abs(entry_price - stop_loss)
        
        if risk_per_share <= 0:
            return 0
        
        shares = int(risk_amount / risk_per_share)
        
        # Cap at max position size
        max_shares = int((equity * self.max_position_pct) / entry_price)
        shares = min(shares, max_shares)
        
        return max(0, shares)
    
    def validate_order(self, order: Dict) -> Tuple[bool, str]:
        """Validate order before execution"""
        checks = []
        
        # Check risk per trade
        if order.get('risk_pct', 0) > self.risk_per_trade_pct:
            checks.append(f"Risk too high: {order['risk_pct']:.1%} > {self.risk_per_trade_pct:.1%}")
        
        # Check position size
        if order.get('position_pct', 0) > self.max_position_pct:
            checks.append(f"Position too large: {order['position_pct']:.1%} > {self.max_position_pct:.1%}")
        
        # Check if we have capacity
        can_trade, reason = self.can_trade()
        if not can_trade:
            checks.append(reason)
        
        if checks:
            return False, "; ".join(checks)
        
        return True, "Order validated"
    
    def record_trade(self, trade: Dict):
        """Record trade for logging"""
        trade['timestamp'] = datetime.now().isoformat()
        self.trade_log.append(trade)
        
        if trade.get('action') == 'BUY':
            self.current_positions += 1
        elif trade.get('action') == 'SELL':
            self.current_positions = max(0, self.current_positions - 1)
            self.daily_pnl += trade.get('pnl', 0)
    
    def reset_daily(self):
        """Reset daily counters"""
        self.daily_pnl = 0.0
        self.halted = False


class AutonomousTrader:
    """Main autonomous trading agent"""
    
    def __init__(self, 
                 deepseek_api_key: str,
                 alpaca_key: str = None,
                 alpaca_secret: str = None,
                 paper_trading: bool = True):
        
        # AI Analyzer
        self.ai = DeepSeekAnalyzer(deepseek_api_key)
        
        # Market Scanner (from existing system)
        self.scanner = UnifiedTradingSystem(alert_interval=5)
        
        # Risk Manager
        self.risk = RiskManager(
            max_daily_loss_pct=0.05,
            max_positions=5,
            risk_per_trade_pct=0.02
        )
        
        # Broker connection
        self.paper_trading = paper_trading
        self.broker = None
        if ALPACA_AVAILABLE and alpaca_key and alpaca_secret:
            self.broker = TradingClient(alpaca_key, alpaca_secret, paper=paper_trading)
            print(f"✅ Connected to Alpaca ({'PAPER' if paper_trading else '⚠️ LIVE'} trading)")
        else:
            print("📝 Running in SIMULATION mode (no broker connected)")
        
        # State
        self.running = False
        self.positions = {}
        self.state_file = os.path.join(DATA_DIR, 'autonomous_trader_state.json')
        self.trade_log_file = os.path.join(DATA_DIR, 'autonomous_trade_log.json')
        
        # Timing
        self.scan_interval = 300  # 5 minutes
        self.min_confidence = 75  # Minimum AI confidence to trade
        
        self._load_state()
        self._print_banner()
    
    def _print_banner(self):
        """Print startup banner"""
        print("=" * 100)
        print("🤖 AUTONOMOUS DEEPSEEK AI TRADING AGENT")
        print("=" * 100)
        print(f"📅 Started: {datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}")
        print(f"🎯 Mode: {'PAPER TRADING' if self.paper_trading else '⚠️ LIVE TRADING ⚠️'}")
        print(f"🔄 Scan Interval: {self.scan_interval // 60} minutes")
        print(f"📊 Min Confidence: {self.min_confidence}%")
        print(f"💰 Risk per Trade: {self.risk.risk_per_trade_pct:.0%}")
        print(f"📈 Max Positions: {self.risk.max_positions}")
        print("=" * 100)
    
    def _load_state(self):
        """Load saved state"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.positions = state.get('positions', {})
                    self.risk.current_positions = len(self.positions)
                print(f"✅ Loaded {len(self.positions)} existing positions")
            except Exception as e:
                print(f"⚠️  Could not load state: {e}")
    
    def _save_state(self):
        """Save current state"""
        state = {
            'positions': self.positions,
            'last_update': datetime.now().isoformat(),
            'daily_pnl': self.risk.daily_pnl
        }
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def _log_trade(self, trade: Dict):
        """Append trade to log file"""
        trades = []
        if os.path.exists(self.trade_log_file):
            try:
                with open(self.trade_log_file, 'r') as f:
                    trades = json.load(f)
            except:
                pass
        
        trades.append(trade)
        
        with open(self.trade_log_file, 'w') as f:
            json.dump(trades, f, indent=2)
    
    def is_market_open(self) -> bool:
        """Check if market is open"""
        now = datetime.now()
        
        # Check weekday
        if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False
        
        # Check time (9:30 AM - 4:00 PM ET)
        # Adjust for your timezone if needed
        market_open = now.replace(hour=9, minute=30, second=0)
        market_close = now.replace(hour=16, minute=0, second=0)
        
        return market_open <= now <= market_close
    
    def get_account_equity(self) -> float:
        """Get current account equity"""
        if self.broker:
            try:
                account = self.broker.get_account()
                return float(account.equity)
            except Exception as e:
                print(f"⚠️  Could not get account: {e}")
                return 100000.0  # Default for simulation
        return 100000.0  # Simulation default
    
    def scan_opportunities(self) -> List[Dict]:
        """Scan market for opportunities"""
        print("\n🔍 Scanning market for opportunities...")
        
        opportunities = []
        
        # Use universe from existing system
        universe = self.scanner.options_universe + self.scanner.stock_universe[:10]
        universe = list(set(universe))  # Remove duplicates
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self.scanner.get_live_data, ticker): ticker 
                      for ticker in universe}
            
            for future in futures:
                try:
                    data = future.result(timeout=30)
                    if data:
                        opportunities.append(data)
                except Exception as e:
                    pass  # Skip failed tickers
        
        print(f"📊 Found {len(opportunities)} valid stocks to analyze")
        return opportunities
    
    def analyze_and_decide(self, market_data: Dict) -> Optional[Dict]:
        """Get AI decision for a stock"""
        decision = self.ai.analyze_market(market_data)
        
        ticker = market_data.get('ticker', 'N/A')
        action = decision.get('action', 'HOLD')
        confidence = decision.get('confidence', 0)
        
        if action != 'HOLD':
            print(f"   {ticker}: {action} (confidence: {confidence}%)")
        
        return decision
    
    def execute_buy(self, ticker: str, market_data: Dict, decision: Dict):
        """Execute a buy order"""
        equity = self.get_account_equity()
        entry_price = decision.get('entry_price') or market_data['price']
        stop_loss = decision.get('stop_loss') or (entry_price - market_data['atr'] * 1.5)
        target_price = decision.get('target_price') or (entry_price + (entry_price - stop_loss) * 3)
        
        # Calculate position size
        shares = self.risk.calculate_position_size(equity, entry_price, stop_loss)
        
        if shares <= 0:
            print(f"   ⚠️  {ticker}: Position size too small, skipping")
            return
        
        # Validate order
        order = {
            'ticker': ticker,
            'action': 'BUY',
            'shares': shares,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'target_price': target_price,
            'risk_pct': self.risk.risk_per_trade_pct,
            'position_pct': (shares * entry_price) / equity
        }
        
        valid, reason = self.risk.validate_order(order)
        if not valid:
            print(f"   ❌ {ticker}: Order rejected - {reason}")
            return
        
        # Execute order
        if self.broker:
            try:
                from alpaca.trading.requests import MarketOrderRequest
                from alpaca.trading.enums import OrderSide, TimeInForce
                order_data = MarketOrderRequest(
                    symbol=ticker,
                    qty=shares,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    take_profit={"limit_price": round(target_price, 2)},
                    stop_loss={"stop_price": round(stop_loss, 2)},
                )
                self.broker.submit_order(order_data)
                print(f"   ✅ {ticker}: BOUGHT {shares} shares @ ${entry_price:.2f}")
                print(f"      Stop: ${stop_loss:.2f} | Target: ${target_price:.2f}")
            except Exception as e:
                print(f"   ❌ {ticker}: Order failed - {e}")
                return
        else:
            # Simulation mode
            print(f"   📝 {ticker}: SIMULATED BUY {shares} shares @ ${entry_price:.2f}")
            print(f"      Stop: ${stop_loss:.2f} | Target: ${target_price:.2f}")
        
        # Record position
        self.positions[ticker] = {
            'shares': shares,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'target_price': target_price,
            'entry_time': datetime.now().isoformat(),
            'ai_confidence': decision.get('confidence', 0),
            'ai_reason': decision.get('reason', '')
        }
        
        # Update risk manager
        self.risk.record_trade(order)
        
        # Save state
        self._save_state()
        self._log_trade(order)
    
    def check_positions(self):
        """Monitor and manage existing positions"""
        if not self.positions:
            return
        
        print("\n📊 Checking existing positions...")
        
        for ticker, position in list(self.positions.items()):
            try:
                data = self.scanner.get_live_data(ticker)
                if not data:
                    continue
                
                current_price = data['price']
                entry_price = position['entry_price']
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
                
                print(f"   {ticker}: ${current_price:.2f} ({pnl_pct:+.1f}%)")
                
                # Check stop loss
                if current_price <= position['stop_loss']:
                    print(f"   🔴 {ticker}: STOP LOSS triggered!")
                    self._close_position(ticker, current_price, 'STOP_LOSS')
                
                # Check target
                elif current_price >= position['target_price']:
                    print(f"   🎯 {ticker}: TARGET reached!")
                    self._close_position(ticker, current_price, 'TARGET')
                    
            except Exception as e:
                print(f"   ⚠️  Error checking {ticker}: {e}")
    
    def _close_position(self, ticker: str, price: float, reason: str):
        """Close a position"""
        position = self.positions.get(ticker)
        if not position:
            return
        
        pnl = (price - position['entry_price']) * position['shares']
        
        if self.broker:
            try:
                self.broker.close_position(ticker)
                print(f"   ✅ Closed {ticker} - P&L: ${pnl:.2f}")
            except Exception as e:
                print(f"   ❌ Error closing {ticker}: {e}")
                return
        else:
            print(f"   📝 SIMULATED CLOSE {ticker} - P&L: ${pnl:.2f}")
        
        # Record trade
        trade = {
            'ticker': ticker,
            'action': 'SELL',
            'shares': position['shares'],
            'exit_price': price,
            'entry_price': position['entry_price'],
            'pnl': pnl,
            'reason': reason
        }
        self.risk.record_trade(trade)
        self._log_trade(trade)
        
        # Remove position
        del self.positions[ticker]
        self._save_state()
    
    def run_cycle(self):
        """Run one trading cycle"""
        print(f"\n{'=' * 60}")
        print(f"🔄 Trading Cycle: {datetime.now().strftime('%I:%M:%S %p')}")
        print(f"{'=' * 60}")
        
        # Check if we can trade
        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            print(f"⚠️  Trading paused: {reason}")
            self.check_positions()  # Still monitor positions
            return
        
        # Check existing positions first
        self.check_positions()
        
        # Scan for new opportunities
        opportunities = self.scan_opportunities()
        
        # Analyze top candidates with AI
        print("\n🤖 Analyzing with DeepSeek AI...")
        
        buy_signals = []
        
        for data in opportunities[:15]:  # Limit to top 15 to save API calls
            decision = self.analyze_and_decide(data)
            
            if (decision.get('action') == 'BUY' and 
                decision.get('confidence', 0) >= self.min_confidence and
                data['ticker'] not in self.positions):
                
                buy_signals.append((data, decision))
        
        # Execute top opportunities
        if buy_signals:
            print(f"\n🎯 Found {len(buy_signals)} BUY signals above {self.min_confidence}% confidence")
            
            # Sort by confidence
            buy_signals.sort(key=lambda x: x[1].get('confidence', 0), reverse=True)
            
            for data, decision in buy_signals[:3]:  # Max 3 new positions per cycle
                can_trade, reason = self.risk.can_trade()
                if not can_trade:
                    print(f"   ⚠️  Stopping: {reason}")
                    break
                
                self.execute_buy(data['ticker'], data, decision)
        else:
            print("\n📊 No high-confidence opportunities found")
        
        print(f"\n💼 Active Positions: {len(self.positions)}/{self.risk.max_positions}")
        print(f"📈 Daily P&L: ${self.risk.daily_pnl:.2f}")
    
    def start(self):
        """Start autonomous trading"""
        self.running = True
        
        # Set starting equity
        equity = self.get_account_equity()
        self.risk.set_starting_equity(equity)
        print(f"\n💰 Starting Equity: ${equity:,.2f}")
        
        print("\n🚀 Starting autonomous trading loop...")
        print("   Press Ctrl+C to stop\n")
        
        try:
            while self.running:
                if self.is_market_open():
                    self.run_cycle()
                else:
                    print(f"⏸️  Market closed - waiting... ({datetime.now().strftime('%I:%M %p')})")
                
                # Wait for next cycle
                time.sleep(self.scan_interval)
                
        except KeyboardInterrupt:
            print("\n\n🛑 Stopping autonomous trader...")
            self.stop()
    
    def stop(self):
        """Stop trading gracefully"""
        self.running = False
        self._save_state()
        
        print("\n" + "=" * 60)
        print("📊 TRADING SESSION SUMMARY")
        print("=" * 60)
        print(f"💼 Open Positions: {len(self.positions)}")
        print(f"📈 Daily P&L: ${self.risk.daily_pnl:.2f}")
        print(f"📝 Trades Today: {len(self.risk.trade_log)}")
        print("=" * 60)
        
        if self.positions:
            print("\n📋 Open Positions:")
            for ticker, pos in self.positions.items():
                print(f"   {ticker}: {pos['shares']} shares @ ${pos['entry_price']:.2f}")


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Autonomous DeepSeek AI Trading Agent')
    parser.add_argument('--live', action='store_true', help='Enable LIVE trading (⚠️ REAL MONEY)')
    parser.add_argument('--interval', type=int, default=5, help='Scan interval in minutes')
    parser.add_argument('--confidence', type=int, default=75, help='Minimum AI confidence (0-100)')
    args = parser.parse_args()
    
    # Get API keys from environment
    deepseek_key = os.environ.get('DEEPSEEK_API_KEY')
    alpaca_key = os.environ.get('ALPACA_API_KEY')
    alpaca_secret = os.environ.get('ALPACA_SECRET_KEY')
    
    if not deepseek_key:
        print("❌ Error: DEEPSEEK_API_KEY environment variable not set")
        print("   Get your API key from: https://platform.deepseek.com")
        print("   Then run: export DEEPSEEK_API_KEY='your-key-here'")
        return
    
    if args.live and not (alpaca_key and alpaca_secret):
        print("❌ Error: Live trading requires ALPACA_API_KEY and ALPACA_SECRET_KEY")
        return
    
    if args.live:
        print("\n" + "⚠️ " * 20)
        print("⚠️  WARNING: LIVE TRADING MODE - REAL MONEY AT RISK!")
        print("⚠️ " * 20)
        confirm = input("\nType 'YES' to confirm live trading: ")
        if confirm != 'YES':
            print("Aborted.")
            return
    
    # Create and start trader
    trader = AutonomousTrader(
        deepseek_api_key=deepseek_key,
        alpaca_key=alpaca_key,
        alpaca_secret=alpaca_secret,
        paper_trading=not args.live
    )
    
    trader.scan_interval = args.interval * 60
    trader.min_confidence = args.confidence
    
    trader.start()


if __name__ == '__main__':
    main()
