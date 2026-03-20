"""Diagnose bot trade execution issues"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ai_bot_state.json')
d = json.load(open(STATE_FILE))

print('=== BOT STATE ===')
print(f'running: {d.get("running")}')
print(f'auto_trade: {d.get("auto_trade")}')
print(f'account_mode: {d.get("account_mode")}')
print(f'alpaca_execution: {d.get("alpaca_execution")}')
print(f'last_scan: {d.get("last_scan")}')
print(f'strategy: {d.get("strategy")}')

settings = d.get('settings', {})
print(f'\n=== SETTINGS ===')
for k in ['min_confidence', 'max_positions', 'position_size', 'instrument_type',
          'watchlist', 'scan_interval', 'max_daily_trades', 'market_regime_filter',
          'close_0dte_before_expiry', 'min_option_dte_days', 'reentry_cooldown_minutes']:
    print(f'  {k}: {settings.get(k)}')

acct_mode = d.get('account_mode', 'demo')
acct = d.get('demo_account' if acct_mode == 'demo' else 'real_account', {})
positions = acct.get('positions', [])
print(f'\n=== ACCOUNT ({acct_mode}) ===')
print(f'balance: ${acct.get("balance", 0):.2f}')
print(f'positions: {len(positions)}')
for p in positions:
    sym = p.get('contract', p.get('symbol', '?'))
    entry = p.get('entry_price', 0)
    current = p.get('current_price', 0)
    side = p.get('side', '?')
    inst = p.get('instrument_type', '?')
    print(f'  {sym}: entry=${entry:.2f}, current=${current:.2f}, side={side}, type={inst}')

print(f'\n=== SIGNALS ({len(d.get("signals", []))}) ===')
for s in d.get('signals', []):
    contract = s.get('contract', s.get('symbol', '?'))
    conf = s.get('confidence', '?')
    below = s.get('_below_threshold', False)
    score = s.get('score', '?')
    direction = s.get('direction', '?')
    inst = s.get('instrument_type', '?')
    ts = s.get('timestamp', 'NO_TS')
    print(f'  {contract}: conf={conf}%, below_thresh={below}, score={score}, dir={direction}, type={inst}, ts={ts}')

# Check today's trades
today_str = '2026-03-20'
trades = acct.get('trades', [])
today_trades = [t for t in trades if t.get('timestamp', '').startswith(today_str)]
print(f'\n=== TODAY TRADES ({len(today_trades)}) ===')
for t in today_trades[-15:]:
    ts = t.get('timestamp', '?')[:16]
    sym = t.get('symbol', '?')
    action = t.get('action', '?')
    conf = t.get('confidence', '')
    auto = t.get('auto_trade', False) or t.get('auto_exit', False)
    reason = t.get('reason', '')
    price = t.get('price', 0)
    pnl = t.get('pnl', '')
    print(f'  {ts} | {sym:12s} | {action:5s} | ${price:>8.2f} | conf={str(conf):>4s} | auto={str(auto):5s} | {reason}')

# Check if any signals would pass the confidence threshold
min_conf = settings.get('min_confidence', 75)
passing = [s for s in d.get('signals', []) if not s.get('_below_threshold') and s.get('confidence', 0) >= min_conf]
print(f'\n=== EXECUTION ANALYSIS ===')
print(f'min_confidence threshold: {min_conf}%')
print(f'Signals passing threshold: {len(passing)}/{len(d.get("signals", []))}')
print(f'Max positions: {settings.get("max_positions")}')
print(f'Current positions: {len(positions)}')
print(f'Available slots: {settings.get("max_positions", 5) - len(positions)}')
print(f'Balance: ${acct.get("balance", 0):.2f}')
print(f'Position size: ${settings.get("position_size", 1000)}')
print(f'Can afford 1 position: {acct.get("balance", 0) >= settings.get("position_size", 1000) * 0.5}')
