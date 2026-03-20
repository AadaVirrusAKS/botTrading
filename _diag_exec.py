"""Simulate bot auto_cycle trade execution to find exact blocker"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'ai_bot_state.json')
d = json.load(open(STATE_FILE))

settings = d.get('settings', {})
min_confidence = settings.get('min_confidence', 75)
max_positions = settings.get('max_positions', 5)
position_size = settings.get('position_size', 1000)
instrument_type = settings.get('instrument_type', 'stocks')
watchlist = settings.get('watchlist', 'top_50')

acct_mode = d.get('account_mode', 'demo')
acct = d.get('demo_account' if acct_mode == 'demo' else 'real_account', {})
positions = acct.get('positions', [])
balance = acct.get('balance', 0)

print(f"=== CURRENT STATE ===")
print(f"running={d.get('running')}, auto_trade={d.get('auto_trade')}, last_scan={d.get('last_scan')}")
print(f"min_confidence={min_confidence}, max_positions={max_positions}, position_size={position_size}")
print(f"instrument_type={instrument_type}, watchlist={watchlist}")
print(f"balance=${balance:.2f}, positions={len(positions)}/{max_positions}")
print(f"")

print(f"=== SIGNALS ({len(d.get('signals', []))}) ===")
for s in d.get('signals', []):
    contract = s.get('contract', s.get('symbol', '?'))
    conf = s.get('confidence', 0)
    below = s.get('_below_threshold', False)
    passes = not below and conf >= min_confidence
    print(f"  {contract}: conf={conf}%, below_thresh={below}, passes_threshold={passes}")

# Simulate execution checks
print(f"\n=== EXECUTION SIMULATION ===")
signals = d.get('signals', [])

# Check 1: auto_trade enabled?
if not d.get('auto_trade', False):
    print("BLOCKED: auto_trade is OFF")
    sys.exit()
print("OK: auto_trade is ON")

# Check 2: Any signals pass confidence?
passing = [s for s in signals if not s.get('_below_threshold') and s.get('confidence', 0) >= min_confidence]
print(f"Signals passing {min_confidence}% threshold: {len(passing)}/{len(signals)}")
if not passing:
    below_only = [s for s in signals if s.get('_below_threshold')]
    print(f"  ALL signals have _below_threshold=True ({len(below_only)}/{len(signals)})")
    print(f"  Max confidence: {max((s.get('confidence',0) for s in signals), default=0)}%")
    print(f"  BLOCKED: No signals pass min_confidence={min_confidence}%")

# Check 3: Position slots available?
if len(positions) >= max_positions:
    print(f"BLOCKED: Max positions reached ({len(positions)}/{max_positions})")
else:
    print(f"OK: Position slots available ({len(positions)}/{max_positions})")

# Check 4: Balance sufficient?
if balance < position_size * 0.5:
    print(f"BLOCKED: Balance ${balance:.2f} < 50% of position_size ${position_size}")
else:
    print(f"OK: Balance ${balance:.2f} sufficient")

# Check 5: Max daily trades?
today_str = datetime.now().strftime('%Y-%m-%d')
trades = acct.get('trades', [])
today_entries = [t for t in trades 
    if t.get('timestamp', '').startswith(today_str)
    and not t.get('auto_exit')
    and t.get('action') in ('BUY', 'SELL', 'SHORT')
    and (t.get('auto_trade') or t.get('source') == 'bot')]
max_daily = settings.get('max_daily_trades', 20)
print(f"Today auto-entries: {len(today_entries)}/{max_daily}")

# Check 6: Existing positions - directional conflict?
print(f"\n=== EXISTING POSITIONS ===")
for p in positions:
    contract = p.get('contract', p.get('symbol', '?'))
    expiry = p.get('expiry', '')
    opt_type = p.get('option_type', '?')
    side = p.get('side', '?')
    entry = p.get('entry_price', 0)
    current = p.get('current_price', 0)
    inst = p.get('instrument_type', '?')
    dte = 0
    if expiry:
        try:
            dte = (datetime.strptime(expiry, '%Y-%m-%d').date() - datetime.now().date()).days
        except: pass
    pnl = (current - entry) * p.get('quantity', 0) * (100 if inst == 'option' else 1)
    print(f"  {contract}: {opt_type} {side} | entry=${entry:.2f} curr=${current:.2f} | DTE={dte} | PnL=${pnl:.2f}")
    if dte <= 0:
        print(f"    *** 0DTE - should be auto-closed by expiry protection!")

# Check 7: Last scan freshness
last_scan = d.get('last_scan')
if last_scan:
    try:
        last_scan_time = datetime.fromisoformat(last_scan)
        age_sec = (datetime.now() - last_scan_time).total_seconds()
        print(f"\n=== SCAN TIMING ===")
        print(f"last_scan: {last_scan} (age: {age_sec:.0f}s = {age_sec/60:.1f}min = {age_sec/3600:.1f}hr)")
        if age_sec > 300:
            print(f"  Stale! Should trigger fresh scan on next auto_cycle")
    except:
        print(f"last_scan: {last_scan} (parse error)")
else:
    print(f"last_scan: None (will trigger fresh scan)")

# Check 8: Market regime
print(f"\n=== MARKET REGIME CHECK ===")
try:
    from services.market_data import cached_get_history
    spy_hist = cached_get_history('SPY', period='1mo', interval='1d')
    if spy_hist is not None and len(spy_hist) >= 3:
        spy_close = spy_hist['Close'].dropna()
        spy_latest = float(spy_close.iloc[-1])
        spy_prev = float(spy_close.iloc[-2])
        spy_sma3 = spy_close.rolling(3).mean().iloc[-1]
        spy_sma10 = spy_close.rolling(min(10, len(spy_close))).mean().iloc[-1]
        
        if spy_latest < spy_sma3 and spy_latest < spy_sma10 and spy_latest < spy_prev:
            regime = 'BEARISH'
            print(f"  SPY=${spy_latest:.2f}, SMA3=${spy_sma3:.2f}, SMA10=${spy_sma10:.2f} → BEARISH")
            print(f"  CALL entries will be BLOCKED by market regime filter")
            print(f"  PUT entries are ALLOWED")
        elif spy_latest > spy_sma3 and spy_latest > spy_sma10 and spy_latest > spy_prev:
            regime = 'BULLISH'
            print(f"  SPY=${spy_latest:.2f}, SMA3=${spy_sma3:.2f}, SMA10=${spy_sma10:.2f} → BULLISH")
            print(f"  PUT entries will be BLOCKED by market regime filter")
        else:
            regime = 'NEUTRAL'
            print(f"  SPY=${spy_latest:.2f}, SMA3=${spy_sma3:.2f}, SMA10=${spy_sma10:.2f} → NEUTRAL")
            print(f"  All directions ALLOWED")
except Exception as e:
    print(f"  Error: {e}")
