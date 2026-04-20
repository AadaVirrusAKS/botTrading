import json, os
from datetime import datetime

DATA_DIR = '/Users/akum221/Documents/TradingCode/data'
today = '2026-04-17'

FILES = [
    ('bot_state_user_1.json', ['demo_account', 'real_account']),
    ('bot_state_user_2.json', ['demo_account', 'real_account']),
    ('ai_bot_state.json',     ['demo_account', 'real_account']),
    ('paper_trading_state.json', ['demo_account', 'real_account']),
]

for fname, sub_keys in FILES:
    fpath = os.path.join(DATA_DIR, fname)
    if not os.path.exists(fpath):
        continue
    with open(fpath) as f:
        state = json.load(f)

    for sub_key in sub_keys:
        acc = state.get(sub_key)
        if not acc:
            continue
        trades = acc.get('trades', [])
        today_trades = [t for t in trades if t.get('timestamp','').startswith(today)]
        if not today_trades:
            continue

        print(f"\n{'='*80}")
        print(f"FILE: {fname}  |  ACCOUNT: {sub_key}")
        print(f"{'='*80}")

        total_pnl = sum(t.get('pnl', t.get('profit_loss', 0)) or 0 for t in today_trades)
        wins   = [t for t in today_trades if (t.get('pnl', t.get('profit_loss', 0)) or 0) > 0]
        losses = [t for t in today_trades if (t.get('pnl', t.get('profit_loss', 0)) or 0) < 0]
        neutral= [t for t in today_trades if (t.get('pnl', t.get('profit_loss', 0)) or 0) == 0]

        print(f"Total trades today : {len(today_trades)}")
        print(f"Wins/Losses/Neutral: {len(wins)} / {len(losses)} / {len(neutral)}")
        print(f"Total P&L          : ${total_pnl:.2f}")
        if wins:
            print(f"Avg win            : ${sum((t.get('pnl', t.get('profit_loss',0)) or 0) for t in wins)/len(wins):.2f}  |  Best: ${max((t.get('pnl', t.get('profit_loss',0)) or 0) for t in wins):.2f}")
        if losses:
            print(f"Avg loss           : ${sum((t.get('pnl', t.get('profit_loss',0)) or 0) for t in losses)/len(losses):.2f}  |  Worst: ${min((t.get('pnl', t.get('profit_loss',0)) or 0) for t in losses):.2f}")

        print(f"\n{'Symbol/Contract':<22} {'Action':<10} {'Type':<7} {'Opt':<5} {'Price':>8} {'Qty':>5} {'P&L':>9}  {'Time':<20} Reason/Exit")
        print('-'*110)
        for t in sorted(today_trades, key=lambda x: x.get('timestamp','')):
            sym    = t.get('contract', t.get('symbol','?'))
            action = t.get('action','?')
            itype  = t.get('instrument_type','stock')[:6]
            otype  = (t.get('option_type','') or '').upper()
            price  = t.get('price', t.get('entry_price', t.get('exit_price', 0))) or 0
            qty    = t.get('quantity', t.get('shares', 0)) or 0
            pnl    = t.get('pnl', t.get('profit_loss', 0)) or 0
            ts     = t.get('timestamp','')[:19]
            reason = t.get('exit_reason', t.get('reason', t.get('signal', t.get('source',''))))
            pnl_str = f"${pnl:+.2f}" if pnl != 0 else "     -"
            print(f"{sym:<22} {action:<10} {itype:<7} {otype:<5} ${price:>7.2f} {qty:>5} {pnl_str:>9}  {ts:<20} {reason}")

        # P&L by symbol
        by_sym = {}
        for t in today_trades:
            s = t.get('symbol', '?')
            pnl = t.get('pnl', t.get('profit_loss', 0)) or 0
            by_sym.setdefault(s, {'pnl': 0, 'count': 0})
            by_sym[s]['pnl'] += pnl
            by_sym[s]['count'] += 1
        print(f"\nP&L BY SYMBOL:")
        for sym, d in sorted(by_sym.items(), key=lambda x: x[1]['pnl'], reverse=True):
            bar = '█' * int(abs(d['pnl']) / 50) if d['pnl'] != 0 else ''
            sign = '+' if d['pnl'] >= 0 else ''
            print(f"  {sym:<10} {sign}${d['pnl']:>8.2f}  ({d['count']} trades) {bar}")

        # Open positions
        open_pos = acc.get('positions', [])
        if open_pos:
            print(f"\nOPEN POSITIONS ({len(open_pos)}):")
            for p in open_pos:
                sym   = p.get('contract', p.get('symbol','?'))
                entry = p.get('entry_price', p.get('price', 0)) or 0
                qty   = p.get('quantity', p.get('shares', 0)) or 0
                otype = (p.get('option_type','') or '').upper()
                itype = p.get('instrument_type','stock')
                target= p.get('target_price', p.get('take_profit', 0)) or 0
                stop  = p.get('stop_loss', 0) or 0
                print(f"  {sym:<20} {itype:<7} {otype:<5} entry=${entry:.2f}  qty={qty}  target=${target:.2f}  stop=${stop:.2f}")
        else:
            print("\nNo open positions.")

        bal = acc.get('balance', acc.get('cash', None))
        if bal is not None:
            print(f"Current Balance: ${bal:.2f}")
