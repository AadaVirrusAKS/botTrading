"""
Daily Trading Analysis Agent
Automated daily analysis of bot performance with actionable improvement suggestions.

Runs analysis on:
  - Trade P&L, win rate, risk/reward
  - Rapid re-entry loops and overtrading patterns
  - Signal-position conflicts
  - Market regime alignment
  - Confidence score distribution
  - Volume confirmation quality
  - Position health check

Generates a JSON report saved to daily_analysis_reports/ and served via API.
Can run standalone (python3 services/daily_analysis_agent.py) or via the web dashboard.
"""
import os
import json
import threading
from datetime import datetime, timedelta
from collections import Counter
from zoneinfo import ZoneInfo

from config import DATA_DIR
REPORT_DIR = os.path.join(DATA_DIR, 'daily_analysis_reports')
BOT_STATE_FILE = os.path.join(DATA_DIR, 'ai_bot_state.json')

_agent_lock = threading.Lock()


def _load_bot_state():
    """Load the bot state from disk."""
    if not os.path.exists(BOT_STATE_FILE):
        return None
    with open(BOT_STATE_FILE) as f:
        return json.load(f)


def _get_trades(state, date_str=None):
    """Get trades from bot state, optionally filtered by date."""
    account = state.get('demo_account', {})
    trades = account.get('trades', [])
    if date_str:
        trades = [t for t in trades if t.get('timestamp', '').startswith(date_str)]
    return trades


def _get_positions(state):
    """Get open positions from bot state."""
    return state.get('demo_account', {}).get('positions', [])


def _get_signals(state):
    """Get latest scanner signals from bot state."""
    return state.get('signals', [])


# ============================================================
# ANALYSIS MODULES
# ============================================================

def analyze_pnl(trades, date_str):
    """Analyze P&L for the given date."""
    entries = [t for t in trades if t.get('action') in ('BUY', 'SELL', 'SHORT') and not t.get('auto_exit')]
    exits = [t for t in trades if t.get('pnl') is not None and t.get('action') in (
        'SELL', 'BUY_TO_COVER', 'CLOSE', 'PARTIAL_SELL', 'PARTIAL_COVER')]

    wins = [t for t in exits if float(t.get('pnl', 0) or 0) > 0]
    losses = [t for t in exits if float(t.get('pnl', 0) or 0) < 0]
    breakeven = [t for t in exits if float(t.get('pnl', 0) or 0) == 0]

    total_pnl = sum(float(t.get('pnl', 0) or 0) for t in exits)
    avg_win = sum(float(t.get('pnl', 0)) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(float(t.get('pnl', 0)) for t in losses) / len(losses) if losses else 0

    # By instrument type
    stock_exits = [t for t in exits if t.get('instrument_type', 'stock') != 'option']
    option_exits = [t for t in exits if t.get('instrument_type') == 'option']

    # By exit reason
    reason_stats = {}
    for t in exits:
        reason = t.get('reason', 'UNKNOWN')
        if reason not in reason_stats:
            reason_stats[reason] = {'count': 0, 'pnl': 0}
        reason_stats[reason]['count'] += 1
        reason_stats[reason]['pnl'] += float(t.get('pnl', 0) or 0)
    for v in reason_stats.values():
        v['pnl'] = round(v['pnl'], 2)

    return {
        'date': date_str,
        'total_entries': len(entries),
        'total_exits': len(exits),
        'wins': len(wins),
        'losses': len(losses),
        'breakeven': len(breakeven),
        'win_rate': round(len(wins) / (len(wins) + len(losses)) * 100, 1) if (len(wins) + len(losses)) > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'risk_reward': round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 0,
        'stock_exits': len(stock_exits),
        'option_exits': len(option_exits),
        'stock_pnl': round(sum(float(t.get('pnl', 0) or 0) for t in stock_exits), 2),
        'option_pnl': round(sum(float(t.get('pnl', 0) or 0) for t in option_exits), 2),
        'exit_reasons': reason_stats,
        'largest_win': round(max((float(t.get('pnl', 0) or 0) for t in exits), default=0), 2),
        'largest_loss': round(min((float(t.get('pnl', 0) or 0) for t in exits), default=0), 2),
    }


def analyze_rapid_reentries(trades, date_str):
    """Detect rapid re-entry loops (same symbol entered/exited within 2 minutes)."""
    day_trades = [t for t in trades if t.get('timestamp', '').startswith(date_str)]
    day_trades.sort(key=lambda t: t.get('timestamp', ''))

    rapid_events = []
    symbol_rapid_counts = Counter()

    for i in range(1, len(day_trades)):
        prev = day_trades[i - 1]
        curr = day_trades[i]
        if prev.get('symbol') == curr.get('symbol'):
            try:
                t1 = datetime.fromisoformat(prev.get('timestamp', ''))
                t2 = datetime.fromisoformat(curr.get('timestamp', ''))
                diff = (t2 - t1).total_seconds()
                if diff < 120:
                    symbol_rapid_counts[curr['symbol']] += 1
                    if len(rapid_events) < 20:
                        rapid_events.append({
                            'symbol': curr['symbol'],
                            'from_action': prev.get('action', ''),
                            'to_action': curr.get('action', ''),
                            'seconds': int(diff),
                            'time': t2.strftime('%H:%M:%S'),
                        })
            except Exception:
                pass

    total_rapid = sum(symbol_rapid_counts.values())
    return {
        'total_rapid_reentries': total_rapid,
        'by_symbol': dict(symbol_rapid_counts.most_common(10)),
        'events': rapid_events,
    }


def analyze_overtrading(trades, date_str):
    """Check for overtrading patterns — too many trades on same symbol."""
    day_entries = [t for t in trades
                   if t.get('timestamp', '').startswith(date_str)
                   and t.get('action') in ('BUY', 'SELL', 'SHORT')
                   and not t.get('auto_exit')]

    symbol_counts = Counter(t.get('symbol') for t in day_entries)
    overtrade_symbols = {sym: cnt for sym, cnt in symbol_counts.items() if cnt > 3}

    return {
        'total_entries_today': len(day_entries),
        'unique_symbols': len(symbol_counts),
        'entries_per_symbol': dict(symbol_counts.most_common(10)),
        'overtraded_symbols': overtrade_symbols,
    }


def analyze_signal_conflicts(positions, signals):
    """Detect conflicts between open positions and current scanner signals."""
    conflicts = []
    for pos in positions:
        sym = pos.get('symbol', '')
        pos_type = pos.get('option_type', '').lower()
        is_option = pos.get('instrument_type') == 'option'
        pos_side = pos.get('side', 'LONG')

        for sig in signals:
            if sig.get('symbol') != sym:
                continue

            sig_direction = sig.get('direction', '').upper()

            if is_option:
                if pos_type == 'call' and sig_direction == 'BEARISH':
                    conflicts.append({
                        'symbol': sym,
                        'position': f"LONG {pos_type.upper()} {pos.get('contract', '')}",
                        'signal_direction': sig_direction,
                        'signal_score': sig.get('score', 0),
                        'severity': 'HIGH',
                        'suggestion': f'Close {sym} CALL — scanner is now BEARISH (score {sig.get("score", 0)})',
                    })
                elif pos_type == 'put' and sig_direction == 'BULLISH':
                    conflicts.append({
                        'symbol': sym,
                        'position': f"LONG {pos_type.upper()} {pos.get('contract', '')}",
                        'signal_direction': sig_direction,
                        'signal_score': sig.get('score', 0),
                        'severity': 'HIGH',
                        'suggestion': f'Close {sym} PUT — scanner is now BULLISH (score {sig.get("score", 0)})',
                    })
            else:
                if pos_side == 'LONG' and sig_direction == 'BEARISH':
                    conflicts.append({
                        'symbol': sym,
                        'position': f'LONG STOCK',
                        'signal_direction': sig_direction,
                        'signal_score': sig.get('score', 0),
                        'severity': 'MEDIUM',
                        'suggestion': f'Consider reducing {sym} LONG — scanner turning bearish',
                    })
                elif pos_side == 'SHORT' and sig_direction == 'BULLISH':
                    conflicts.append({
                        'symbol': sym,
                        'position': f'SHORT STOCK',
                        'signal_direction': sig_direction,
                        'signal_score': sig.get('score', 0),
                        'severity': 'MEDIUM',
                        'suggestion': f'Consider covering {sym} SHORT — scanner turning bullish',
                    })

    return {'conflicts': conflicts, 'count': len(conflicts)}


def analyze_confidence_distribution(signals):
    """Analyze the spread of confidence scores — flag if all are the same."""
    confidences = [s.get('confidence', 0) for s in signals]
    if not confidences:
        return {'distribution': {}, 'issue': 'No signals', 'unique_values': 0}

    dist = Counter(confidences)
    unique = len(set(confidences))
    spread = max(confidences) - min(confidences)

    issue = None
    if unique <= 2:
        issue = f'Only {unique} unique confidence values — scoring not differentiating signals'
    elif spread < 10:
        issue = f'Confidence spread is only {spread}% — signals not well differentiated'

    return {
        'distribution': dict(sorted(dist.items())),
        'min': min(confidences),
        'max': max(confidences),
        'spread': spread,
        'unique_values': unique,
        'issue': issue,
    }


def analyze_volume_quality(signals):
    """Check volume ratio quality across signals."""
    ratios = [s.get('volume_ratio', 0) for s in signals]
    if not ratios:
        return {'issue': 'No signals', 'signals_count': 0}

    zero_vol = sum(1 for r in ratios if r == 0)
    low_vol = sum(1 for r in ratios if 0 < r < 0.3)
    good_vol = sum(1 for r in ratios if r >= 0.3)
    high_vol = sum(1 for r in ratios if r >= 1.5)

    issues = []
    if zero_vol > 0:
        issues.append(f'{zero_vol} signals with volume_ratio=0 (data missing)')
    if low_vol > 0:
        issues.append(f'{low_vol} signals with low volume (<0.3x avg)')

    return {
        'signals_count': len(ratios),
        'zero_volume': zero_vol,
        'low_volume': low_vol,
        'good_volume': good_vol,
        'high_volume': high_vol,
        'avg_ratio': round(sum(ratios) / len(ratios), 2) if ratios else 0,
        'issues': issues,
    }


def analyze_position_health(positions):
    """Check health of open positions — underwater, near stop, expiring."""
    health_issues = []
    total_unrealized = 0

    for pos in positions:
        sym = pos.get('symbol', '')
        entry = pos.get('entry_price', 0) or 0
        current = pos.get('current_price', 0) or 0
        stop = pos.get('stop_loss', 0) or 0
        target = pos.get('target', 0) or 0
        qty = pos.get('quantity', 0) or 0
        is_option = pos.get('instrument_type') == 'option'
        multiplier = 100 if is_option else 1
        expiry = pos.get('expiry', '')

        if entry <= 0:
            continue

        pnl = (current - entry) * qty * multiplier
        pnl_pct = ((current - entry) / entry) * 100
        total_unrealized += pnl

        issues = []

        # Deep underwater
        if pnl_pct < -15:
            issues.append(f'Deep loss: {pnl_pct:.1f}% (${pnl:.2f})')

        # Near stop loss
        if stop > 0 and current > 0:
            dist_to_stop_pct = ((current - stop) / current) * 100
            if 0 < dist_to_stop_pct < 3:
                issues.append(f'Near stop loss ({dist_to_stop_pct:.1f}% away)')

        # Expiring soon (options)
        if is_option and expiry:
            try:
                exp_dt = datetime.strptime(expiry, '%Y-%m-%d')
                dte = (exp_dt.date() - datetime.now().date()).days
                if dte <= 0:
                    issues.append('EXPIRED — close immediately')
                elif dte == 1:
                    issues.append('Expires TOMORROW — theta decay acute')
                elif dte <= 3 and pnl_pct < 0:
                    issues.append(f'{dte} DTE and losing — theta risk')
            except Exception:
                pass

        # Far from target
        if target > 0 and current > 0 and entry > 0:
            remaining_to_target = ((target - current) / current) * 100
            if remaining_to_target > 50:
                issues.append(f'Target very far ({remaining_to_target:.0f}% away)')

        health_issues.append({
            'symbol': sym,
            'contract': pos.get('contract', sym),
            'entry': entry,
            'current': current,
            'pnl': round(pnl, 2),
            'pnl_pct': round(pnl_pct, 1),
            'stop_loss': stop,
            'target': target,
            'issues': issues,
            'status': 'CRITICAL' if any('Deep loss' in i or 'EXPIRED' in i for i in issues)
                      else 'WARNING' if issues
                      else 'HEALTHY',
        })

    return {
        'positions': health_issues,
        'total_unrealized': round(total_unrealized, 2),
        'critical_count': sum(1 for p in health_issues if p['status'] == 'CRITICAL'),
        'warning_count': sum(1 for p in health_issues if p['status'] == 'WARNING'),
        'healthy_count': sum(1 for p in health_issues if p['status'] == 'HEALTHY'),
    }


def generate_suggestions(pnl_data, rapid_data, overtrade_data, conflict_data,
                         confidence_data, volume_data, health_data, settings):
    """Generate prioritized improvement suggestions based on all analysis modules."""
    suggestions = []
    priority = 0  # Lower = more urgent

    # --- CRITICAL: Signal conflicts ---
    for c in conflict_data.get('conflicts', []):
        if c['severity'] == 'HIGH':
            priority += 1
            suggestions.append({
                'priority': priority,
                'severity': 'CRITICAL',
                'category': 'Signal Conflict',
                'title': f"Close {c['symbol']} — Scanner Reversed",
                'detail': c['suggestion'],
                'action': 'close_position',
                'symbol': c['symbol'],
            })

    # --- CRITICAL: Position health ---
    for p in health_data.get('positions', []):
        if p['status'] == 'CRITICAL':
            priority += 1
            suggestions.append({
                'priority': priority,
                'severity': 'CRITICAL',
                'category': 'Position Health',
                'title': f"{p['contract']} — {', '.join(p['issues'])}",
                'detail': f"Unrealized: ${p['pnl']:.2f} ({p['pnl_pct']:.1f}%). Consider immediate action.",
                'action': 'review_position',
                'symbol': p['symbol'],
            })

    # --- HIGH: Rapid re-entries ---
    if rapid_data.get('total_rapid_reentries', 0) > 3:
        priority += 1
        worst = list(rapid_data.get('by_symbol', {}).items())[:3]
        suggestions.append({
            'priority': priority,
            'severity': 'HIGH',
            'category': 'Overtrading',
            'title': f"Rapid re-entry detected ({rapid_data['total_rapid_reentries']} events)",
            'detail': f"Worst offenders: {', '.join(f'{s} ({c}x)' for s, c in worst)}. "
                      f"Increase reentry_cooldown_minutes (currently {settings.get('reentry_cooldown_minutes', 10)}min).",
            'action': 'adjust_setting',
            'setting': 'reentry_cooldown_minutes',
            'recommended_value': max(15, settings.get('reentry_cooldown_minutes', 10) + 5),
        })

    # --- HIGH: Overtrading on single symbol ---
    for sym, cnt in overtrade_data.get('overtraded_symbols', {}).items():
        priority += 1
        suggestions.append({
            'priority': priority,
            'severity': 'HIGH',
            'category': 'Overtrading',
            'title': f"{sym}: {cnt} entries today (excessive)",
            'detail': f"Reduce max_per_symbol_daily (currently {settings.get('max_per_symbol_daily', 4)}) "
                      f"or increase cooldown to prevent repeated losses on the same symbol.",
            'action': 'adjust_setting',
            'setting': 'max_per_symbol_daily',
            'recommended_value': max(2, settings.get('max_per_symbol_daily', 4) - 1),
        })

    # --- MEDIUM: Poor win rate ---
    if pnl_data.get('win_rate', 100) < 40 and pnl_data.get('total_exits', 0) >= 5:
        priority += 1
        suggestions.append({
            'priority': priority,
            'severity': 'MEDIUM',
            'category': 'Performance',
            'title': f"Low win rate: {pnl_data['win_rate']}%",
            'detail': f"Out of {pnl_data['total_exits']} exits, only {pnl_data['wins']} wins. "
                      f"Consider raising min_confidence (currently {settings.get('min_confidence', 75)}) "
                      f"to filter weaker setups.",
            'action': 'adjust_setting',
            'setting': 'min_confidence',
            'recommended_value': min(95, settings.get('min_confidence', 75) + 5),
        })

    # --- MEDIUM: Bad risk/reward ---
    if pnl_data.get('risk_reward', 0) > 0 and pnl_data['risk_reward'] < 1.0 and pnl_data.get('total_exits', 0) >= 3:
        priority += 1
        suggestions.append({
            'priority': priority,
            'severity': 'MEDIUM',
            'category': 'Risk Management',
            'title': f"Poor risk/reward ratio: {pnl_data['risk_reward']}:1",
            'detail': f"Avg win: ${pnl_data['avg_win']:.2f}, Avg loss: ${pnl_data['avg_loss']:.2f}. "
                      f"Winners not big enough to offset losers. Consider wider targets or tighter stops.",
            'action': 'review_strategy',
        })

    # --- MEDIUM: Stop losses dominating ---
    sl_count = pnl_data.get('exit_reasons', {}).get('STOP_LOSS', {}).get('count', 0)
    target_count = pnl_data.get('exit_reasons', {}).get('TARGET_HIT', {}).get('count', 0)
    if sl_count > target_count and sl_count >= 3:
        sl_pnl = pnl_data.get('exit_reasons', {}).get('STOP_LOSS', {}).get('pnl', 0)
        priority += 1
        suggestions.append({
            'priority': priority,
            'severity': 'MEDIUM',
            'category': 'Risk Management',
            'title': f"Stop losses outnumber targets ({sl_count} SL vs {target_count} targets)",
            'detail': f"Stop loss exits cost ${abs(sl_pnl):.2f} today. "
                      f"Consider: (1) widen stops with ATR-based trailing, (2) add grace period, "
                      f"(3) increase min_confidence to avoid marginal setups.",
            'action': 'review_strategy',
        })

    # --- LOW: Confidence not varied ---
    if confidence_data.get('issue'):
        priority += 1
        suggestions.append({
            'priority': priority,
            'severity': 'LOW',
            'category': 'Signal Quality',
            'title': f"Confidence scoring issue",
            'detail': confidence_data['issue'],
            'action': 'review_code',
        })

    # --- LOW: Volume quality issues ---
    for issue in volume_data.get('issues', []):
        priority += 1
        suggestions.append({
            'priority': priority,
            'severity': 'LOW',
            'category': 'Signal Quality',
            'title': f"Volume data issue",
            'detail': issue,
            'action': 'review_code',
        })

    # --- INFO: Healthy day ---
    if not suggestions:
        suggestions.append({
            'priority': 999,
            'severity': 'INFO',
            'category': 'Status',
            'title': 'No issues detected',
            'detail': f"PnL: ${pnl_data.get('total_pnl', 0):.2f}, Win rate: {pnl_data.get('win_rate', 0):.1f}%, "
                      f"{len(health_data.get('positions', []))} positions healthy.",
            'action': 'none',
        })

    return sorted(suggestions, key=lambda s: s['priority'])


# ============================================================
# MAIN AGENT ENTRY POINT
# ============================================================

def run_daily_analysis(date_str=None):
    """
    Run the full daily analysis agent.
    Returns a comprehensive report dict and saves it to disk.
    """
    with _agent_lock:
        state = _load_bot_state()
        if not state:
            return {'error': 'Bot state file not found', 'success': False}

        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')

        all_trades = _get_trades(state)
        day_trades = _get_trades(state, date_str)
        positions = _get_positions(state)
        signals = _get_signals(state)
        settings = state.get('settings', {})
        account = state.get('demo_account', {})

        # Run all analysis modules
        pnl_data = analyze_pnl(day_trades, date_str)
        rapid_data = analyze_rapid_reentries(all_trades, date_str)
        overtrade_data = analyze_overtrading(all_trades, date_str)
        conflict_data = analyze_signal_conflicts(positions, signals)
        confidence_data = analyze_confidence_distribution(signals)
        volume_data = analyze_volume_quality(signals)
        health_data = analyze_position_health(positions)

        # Generate improvement suggestions
        suggestions = generate_suggestions(
            pnl_data, rapid_data, overtrade_data, conflict_data,
            confidence_data, volume_data, health_data, settings
        )

        # Historical trend (last 7 days)
        history = []
        for i in range(7):
            d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            dt = _get_trades(state, d)
            exits = [t for t in dt if t.get('pnl') is not None and t.get('action') in (
                'SELL', 'BUY_TO_COVER', 'CLOSE', 'PARTIAL_SELL', 'PARTIAL_COVER')]
            day_pnl = sum(float(t.get('pnl', 0) or 0) for t in exits)
            wins = sum(1 for t in exits if float(t.get('pnl', 0) or 0) > 0)
            losses = sum(1 for t in exits if float(t.get('pnl', 0) or 0) < 0)
            history.append({
                'date': d,
                'exits': len(exits),
                'pnl': round(day_pnl, 2),
                'wins': wins,
                'losses': losses,
                'win_rate': round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0,
            })

        report = {
            'success': True,
            'generated_at': datetime.now().isoformat(),
            'analysis_date': date_str,
            'account': {
                'balance': account.get('balance', 0),
                'initial_balance': account.get('initial_balance', 0),
                'total_return_pct': round(((account.get('balance', 0) / max(account.get('initial_balance', 1), 1)) - 1) * 100, 1),
                'open_positions': len(positions),
            },
            'pnl': pnl_data,
            'rapid_reentries': rapid_data,
            'overtrading': overtrade_data,
            'signal_conflicts': conflict_data,
            'confidence_quality': confidence_data,
            'volume_quality': volume_data,
            'position_health': health_data,
            'suggestions': suggestions,
            'history': history,
            'settings_snapshot': {
                'min_confidence': settings.get('min_confidence', 75),
                'max_positions': settings.get('max_positions', 5),
                'max_daily_trades': settings.get('max_daily_trades', 20),
                'max_per_symbol_daily': settings.get('max_per_symbol_daily', 4),
                'reentry_cooldown_minutes': settings.get('reentry_cooldown_minutes', 10),
                'position_size': settings.get('position_size', 4000),
                'stop_loss': settings.get('stop_loss', 2),
                'take_profit': settings.get('take_profit', 4),
                'instrument_type': settings.get('instrument_type', 'options'),
            },
        }

        # Save report to disk
        os.makedirs(REPORT_DIR, exist_ok=True)
        report_file = os.path.join(REPORT_DIR, f'analysis_{date_str}.json')
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        return report


def get_latest_report():
    """Load the most recent analysis report from disk."""
    if not os.path.exists(REPORT_DIR):
        return None
    files = sorted([f for f in os.listdir(REPORT_DIR) if f.startswith('analysis_') and f.endswith('.json')], reverse=True)
    if not files:
        return None
    with open(os.path.join(REPORT_DIR, files[0])) as f:
        return json.load(f)


def list_reports():
    """List all available analysis reports."""
    if not os.path.exists(REPORT_DIR):
        return []
    files = sorted([f for f in os.listdir(REPORT_DIR) if f.startswith('analysis_') and f.endswith('.json')], reverse=True)
    reports = []
    for fname in files[:30]:
        date_str = fname.replace('analysis_', '').replace('.json', '')
        reports.append({'date': date_str, 'filename': fname})
    return reports


# ============================================================
# STANDALONE CLI
# ============================================================
if __name__ == '__main__':
    import sys

    date = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"\n{'='*80}")
    print(f"  📊 Daily Trading Analysis Agent")
    print(f"  Date: {date or datetime.now().strftime('%Y-%m-%d')}")
    print(f"{'='*80}\n")

    report = run_daily_analysis(date)
    if not report.get('success'):
        print(f"❌ Error: {report.get('error')}")
        sys.exit(1)

    # Print summary
    pnl = report['pnl']
    print(f"💰 P&L: ${pnl['total_pnl']:.2f} | Win Rate: {pnl['win_rate']}% | "
          f"Wins: {pnl['wins']} | Losses: {pnl['losses']} | R:R: {pnl['risk_reward']}")

    acct = report['account']
    print(f"📈 Account: ${acct['balance']:.2f} ({acct['total_return_pct']:.1f}% total return) | "
          f"Open: {acct['open_positions']} positions")

    health = report['position_health']
    print(f"🏥 Positions: {health['healthy_count']} healthy, {health['warning_count']} warning, "
          f"{health['critical_count']} critical | Unrealized: ${health['total_unrealized']:.2f}")

    conflicts = report['signal_conflicts']
    if conflicts['count'] > 0:
        print(f"\n⚠️  Signal Conflicts ({conflicts['count']}):")
        for c in conflicts['conflicts']:
            print(f"  🔴 {c['symbol']}: {c['position']} vs {c['signal_direction']} signal — {c['suggestion']}")

    print(f"\n📋 Suggestions ({len(report['suggestions'])}):")
    for s in report['suggestions']:
        icon = {'CRITICAL': '🔴', 'HIGH': '🟠', 'MEDIUM': '🟡', 'LOW': '🔵', 'INFO': '✅'}.get(s['severity'], '⚪')
        print(f"  {icon} [{s['severity']}] {s['title']}")
        print(f"     {s['detail']}")

    print(f"\n📅 7-Day History:")
    for h in report['history']:
        bar = '█' * max(0, int(h['pnl'] / 10)) if h['pnl'] > 0 else '▒' * max(0, int(abs(h['pnl']) / 10))
        sign = '+' if h['pnl'] >= 0 else ''
        print(f"  {h['date']}: {sign}${h['pnl']:.2f} ({h['exits']} exits, {h['win_rate']}% WR) {bar}")

    print(f"\n✅ Report saved to daily_analysis_reports/analysis_{report['analysis_date']}.json")
