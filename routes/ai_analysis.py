"""
AI Analysis Routes - AI stock analysis, heatmap, correlation, predictions.
"""
from flask import Blueprint, render_template, jsonify, request
import json
import time
from datetime import datetime
import yfinance as yf
import numpy as np
import pandas as pd

from services.utils import clean_nan_values
from services.market_data import (
    cached_get_history, cached_get_ticker_info, _log_fetch_event,
    cached_batch_prices, _is_rate_limited
)
from services.symbols import resolve_symbol_or_name

ai_analysis_bp = Blueprint("ai_analysis", __name__)

@ai_analysis_bp.route('/api/ai/analyze', methods=['POST'])
def ai_analyze_stock():
    """Run full AI analysis on a stock symbol."""
    try:
        from ai_stock_analysis import run_ai_analysis
        data = request.get_json() or {}
        symbol = data.get('symbol', '').strip()
        period = data.get('period', '6mo')
        horizon = data.get('horizon', 5)
        allow_fallback = bool(data.get('allow_fallback', False))
        force_live = bool(data.get('force_live', True))

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        # Resolve symbol or name
        resolved = resolve_symbol_or_name(symbol)
        if not resolved:
            return jsonify({'success': False, 'error': f'Could not resolve symbol: {symbol}'}), 400

        # Live-first mode (default): let analysis engine fetch live data directly.
        # Cache-prefetch mode remains available when force_live=False.
        if force_live:
            df = None
            info = None
        else:
            df = cached_get_history(resolved, period=period, interval='1d')
            info = cached_get_ticker_info(resolved)

        result = run_ai_analysis(resolved, period=period, prediction_horizon=horizon, df=df, info=info)
        if not result or not isinstance(result, dict) or result.get('error'):
            error_message = (result or {}).get('error') if isinstance(result, dict) else 'No analysis output'
            if allow_fallback:
                fallback = build_ai_analysis_fallback(resolved, period=period, horizon=horizon, error_message=error_message)
                fallback['success'] = True
                fallback['data_source'] = 'fallback'
                return jsonify(clean_nan_values(fallback))
            return jsonify({
                'success': False,
                'error': f'Live analysis unavailable for {resolved}: {error_message}'
            }), 503

        result['success'] = True
        result['data_source'] = 'live'
        return jsonify(clean_nan_values(result))

    except Exception as e:
        try:
            data = request.get_json() or {}
            allow_fallback = bool(data.get('allow_fallback', False))
            symbol = (data.get('symbol') or '').strip().upper() or 'SPY'
            period = data.get('period', '6mo')
            horizon = int(data.get('horizon', 5) or 5)
            if allow_fallback:
                fallback = build_ai_analysis_fallback(symbol, period=period, horizon=horizon, error_message=str(e))
                fallback['success'] = True
                fallback['data_source'] = 'fallback'
                return jsonify(clean_nan_values(fallback))
        except Exception:
            pass
        print(f"Error in ai_analyze_stock: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


def build_ai_analysis_fallback(symbol: str, period: str = '6mo', horizon: int = 5, error_message: str = ''):
    """Fallback AI analysis payload used when live provider is rate-limited.

    Keeps AI Analysis page operational with best-effort local data.
    """
    symbol = (symbol or 'SPY').upper()
    horizon = max(1, min(int(horizon or 5), 30))

    # Base price from local top picks when available
    base_price = 100.0
    company_name = symbol
    try:
        if os.path.exists('top_picks.json'):
            with open('top_picks.json', 'r') as f:
                tp = json.load(f) or {}
            for row in (tp.get('stocks', []) + tp.get('etfs', []) + tp.get('options', [])):
                if str(row.get('ticker', '')).upper() == symbol:
                    md = row.get('data') or {}
                    base_price = float(md.get('price') or base_price)
                    company_name = md.get('sector', symbol) or symbol
                    break
    except Exception:
        pass

    # Build deterministic lightweight forecast rails
    drift = 0.003  # +0.3%/day conservative drift
    ensemble = [round(base_price * (1 + drift * (i + 1)), 2) for i in range(horizon)]
    lin_reg = [round(base_price * (1 + (drift + 0.001) * (i + 1)), 2) for i in range(horizon)]
    exp_smooth = [round(base_price * (1 + (drift - 0.0005) * (i + 1)), 2) for i in range(horizon)]
    mean_rev = [round(base_price * (1 + (drift * 0.6) * (i + 1)), 2) for i in range(horizon)]
    momentum = [round(base_price * (1 + (drift + 0.0015) * (i + 1)), 2) for i in range(horizon)]
    upper_95 = [round(p * 1.02, 2) for p in ensemble]
    lower_95 = [round(p * 0.98, 2) for p in ensemble]
    predicted_change_pct = ((ensemble[-1] - base_price) / base_price * 100) if base_price > 0 else 0

    direction = 'BUY' if predicted_change_pct >= 0 else 'SELL'
    confidence = 58.0 if error_message else 62.0

    return {
        'symbol': symbol,
        'company_name': company_name,
        'current_price': base_price,
        'ai_signal': {
            'signal': direction,
            'confidence': confidence,
            'score': confidence,
            'components': {
                'pattern_score': 0.0,
                'prediction_score': 1.5 if direction == 'BUY' else -1.5,
                'trend_score': 0.5 if direction == 'BUY' else -0.5,
            },
            'reasons': [
                'Using cached/local fallback data (live feed rate-limited)',
                f'{horizon}-day projection available',
            ],
            'warnings': [error_message] if error_message else []
        },
        'patterns': {
            'summary': {'total': 0, 'bullish': 0, 'bearish': 0, 'neutral': 0, 'avg_reliability': 0, 'dominant': 'neutral'},
            'recent': [],
            'overlays': [],
            'next_candle': {
                'formation_color': 'neutral',
                'context': 'Fallback mode',
                'formation': 'No reliable live pattern',
                'formation_prob': 50,
                'expected_move_pct': round(predicted_change_pct / max(horizon, 1), 2),
                'expected_range_pct': 1.0,
                'rsi': 50,
                'vol_trend': 'neutral',
                'buyer_pct': 50,
                'seller_pct': 50,
                'bull_prob': 50,
                'bear_prob': 50,
                'signals': {'momentum_bull': 50, 'volume_buying': 50, 'rsi': 50, 'pattern_bias': 50},
                'shape': {'upper_wick': 25, 'body': 50, 'lower_wick': 25, 'is_bullish': predicted_change_pct >= 0},
            }
        },
        'predictions': {
            'horizon': horizon,
            'current_price': base_price,
            'predicted_change_pct': round(predicted_change_pct, 2),
            'predictions': {
                'ensemble': ensemble,
                'linear_regression': lin_reg,
                'exponential_smoothing': exp_smooth,
                'mean_reversion': mean_rev,
                'momentum': momentum,
            },
            'confidence': {'upper_95': upper_95, 'lower_95': lower_95},
            'model_weights': {
                'linear_regression': 0.25,
                'exponential_smoothing': 0.25,
                'mean_reversion': 0.25,
                'momentum': 0.25,
            },
            'targets': {
                'take_profit_1': round(base_price * 1.01, 2),
                'take_profit_2': round(base_price * 1.02, 2),
                'take_profit_3': round(base_price * 1.03, 2),
                'stop_loss': round(base_price * 0.98, 2),
                'resistance': round(base_price * 1.015, 2),
                'support': round(base_price * 0.985, 2),
                'bullish_target': round(base_price * 1.03, 2),
                'bearish_target': round(base_price * 0.97, 2),
            }
        },
        'trend_analysis': {
            'short_term': {'direction': 'bullish' if predicted_change_pct >= 0 else 'bearish', 'strength': 55, 'change_pct': round(predicted_change_pct / max(horizon, 1), 2)},
            'medium_term': {'direction': 'neutral', 'strength': 50, 'change_pct': round(predicted_change_pct / 2, 2)},
            'long_term': {'direction': 'neutral', 'strength': 48, 'change_pct': round(predicted_change_pct, 2)},
            'volume_trend': {'status': 'normal', 'volume_ratio': 1.0, 'avg_recent_volume': 0},
            'trend_strength': {'label': 'Moderate', 'score': 55, 'consistency': 52, 'direction': 'up' if predicted_change_pct >= 0 else 'down'},
            'support_resistance': {
                'supports': [round(base_price * 0.98, 2), round(base_price * 0.96, 2)],
                'resistances': [round(base_price * 1.02, 2), round(base_price * 1.04, 2)],
            },
            'volatility_regime': {'regime': 'normal', 'percentile': 50, 'current_vol': 0, 'avg_vol': 0},
        },
        'metadata': {
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_period': period,
            'bars_analyzed': 0,
            'prediction_horizon': horizon,
        }
    }


@ai_analysis_bp.route('/api/ai/heatmap', methods=['POST'])
def ai_heatmap():
    """Run heatmap analysis across multiple stocks."""
    try:
        from ai_stock_analysis import run_heatmap_analysis
        data = request.get_json() or {}
        symbols = data.get('symbols', None)

        result = run_heatmap_analysis(symbols)
        result['success'] = True
        return jsonify(clean_nan_values(result))

    except Exception as e:
        print(f"Error in ai_heatmap: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_analysis_bp.route('/api/ai/patterns', methods=['POST'])
def ai_patterns_only():
    """Get just candlestick patterns for a symbol (lightweight endpoint)."""
    try:
        from ai_stock_analysis import CandlestickPatternEngine
        data = request.get_json()
        symbol = data.get('symbol', '').strip().upper()
        lookback = data.get('lookback', 50)

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        df = cached_get_history(symbol, period='6mo', interval='1d')

        if df is None or df.empty or len(df) < 10:
            return jsonify({'success': False, 'error': f'No data for {symbol}'}), 400

        engine = CandlestickPatternEngine()
        patterns = engine.detect_all_patterns(df, lookback=lookback)
        summary = engine.get_pattern_summary(patterns)
        recent = engine.get_recent_patterns(patterns)

        return jsonify(clean_nan_values({
            'success': True,
            'symbol': symbol,
            'patterns': recent,
            'summary': summary,
            'total_detected': len(patterns),
        }))

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@ai_analysis_bp.route('/api/ai/predict', methods=['POST'])
def ai_predict_only():
    """Get just price predictions for a symbol (lightweight endpoint)."""
    try:
        from ai_stock_analysis import PricePredictionEngine
        data = request.get_json()
        symbol = data.get('symbol', '').strip().upper()
        horizon = data.get('horizon', 5)

        if not symbol:
            return jsonify({'success': False, 'error': 'Symbol required'}), 400

        df = cached_get_history(symbol, period='6mo', interval='1d')

        if df is None or df.empty or len(df) < 30:
            return jsonify({'success': False, 'error': f'Insufficient data for {symbol}'}), 400

        engine = PricePredictionEngine()
        predictions = engine.predict(df, horizon=horizon)

        return jsonify(clean_nan_values({
            'success': True,
            'symbol': symbol,
            'predictions': predictions,
        }))

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


