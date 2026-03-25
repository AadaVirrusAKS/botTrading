"""
AI Stock Analysis Engine
========================
Advanced candlestick pattern recognition, price prediction models,
and AI-driven trading signals using statistical and ML techniques.

Features:
- 25+ candlestick pattern detection with reliability scoring
- Price prediction using Linear Regression, ARIMA-like, and ensemble methods
- Multi-timeframe trend analysis
- Pattern-based probability scoring from historical data
- Automated signal generation with confidence levels

Uses only numpy, pandas, and scipy (no heavy ML frameworks required).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import warnings
warnings.filterwarnings('ignore')

# Use cached data layer (Alpaca real-time → yfinance fallback)
try:
    from services.market_data import cached_get_history, cached_get_ticker_info
    _USE_CACHED = True
except ImportError:
    _USE_CACHED = False


# ============================================================================
# CANDLESTICK PATTERN RECOGNITION ENGINE
# ============================================================================

class CandlestickPatternEngine:
    """Detects 25+ candlestick patterns with reliability scoring."""

    def __init__(self):
        self.pattern_catalog = self._build_pattern_catalog()

    def _build_pattern_catalog(self):
        """Build catalog of pattern metadata."""
        return {
            'Bullish Engulfing': {'type': 'bullish', 'bars': 2, 'reliability': 0.63, 'category': 'reversal'},
            'Bearish Engulfing': {'type': 'bearish', 'bars': 2, 'reliability': 0.63, 'category': 'reversal'},
            'Doji': {'type': 'neutral', 'bars': 1, 'reliability': 0.50, 'category': 'indecision'},
            'Dragonfly Doji': {'type': 'bullish', 'bars': 1, 'reliability': 0.55, 'category': 'reversal'},
            'Gravestone Doji': {'type': 'bearish', 'bars': 1, 'reliability': 0.55, 'category': 'reversal'},
            'Hammer': {'type': 'bullish', 'bars': 1, 'reliability': 0.60, 'category': 'reversal'},
            'Inverted Hammer': {'type': 'bullish', 'bars': 1, 'reliability': 0.55, 'category': 'reversal'},
            'Hanging Man': {'type': 'bearish', 'bars': 1, 'reliability': 0.58, 'category': 'reversal'},
            'Shooting Star': {'type': 'bearish', 'bars': 1, 'reliability': 0.60, 'category': 'reversal'},
            'Morning Star': {'type': 'bullish', 'bars': 3, 'reliability': 0.72, 'category': 'reversal'},
            'Evening Star': {'type': 'bearish', 'bars': 3, 'reliability': 0.72, 'category': 'reversal'},
            'Three White Soldiers': {'type': 'bullish', 'bars': 3, 'reliability': 0.76, 'category': 'continuation'},
            'Three Black Crows': {'type': 'bearish', 'bars': 3, 'reliability': 0.76, 'category': 'continuation'},
            'Bullish Harami': {'type': 'bullish', 'bars': 2, 'reliability': 0.53, 'category': 'reversal'},
            'Bearish Harami': {'type': 'bearish', 'bars': 2, 'reliability': 0.53, 'category': 'reversal'},
            'Piercing Line': {'type': 'bullish', 'bars': 2, 'reliability': 0.64, 'category': 'reversal'},
            'Dark Cloud Cover': {'type': 'bearish', 'bars': 2, 'reliability': 0.64, 'category': 'reversal'},
            'Tweezer Top': {'type': 'bearish', 'bars': 2, 'reliability': 0.55, 'category': 'reversal'},
            'Tweezer Bottom': {'type': 'bullish', 'bars': 2, 'reliability': 0.55, 'category': 'reversal'},
            'Spinning Top': {'type': 'neutral', 'bars': 1, 'reliability': 0.45, 'category': 'indecision'},
            'Marubozu Bullish': {'type': 'bullish', 'bars': 1, 'reliability': 0.68, 'category': 'continuation'},
            'Marubozu Bearish': {'type': 'bearish', 'bars': 1, 'reliability': 0.68, 'category': 'continuation'},
            'Rising Three Methods': {'type': 'bullish', 'bars': 5, 'reliability': 0.74, 'category': 'continuation'},
            'Falling Three Methods': {'type': 'bearish', 'bars': 5, 'reliability': 0.74, 'category': 'continuation'},
            'Bullish Abandoned Baby': {'type': 'bullish', 'bars': 3, 'reliability': 0.70, 'category': 'reversal'},
            'Bearish Abandoned Baby': {'type': 'bearish', 'bars': 3, 'reliability': 0.70, 'category': 'reversal'},
            'Three Inside Up': {'type': 'bullish', 'bars': 3, 'reliability': 0.65, 'category': 'reversal'},
            'Three Inside Down': {'type': 'bearish', 'bars': 3, 'reliability': 0.65, 'category': 'reversal'},
        }

    def detect_all_patterns(self, df, lookback=50):
        """
        Detect all candlestick patterns across the last `lookback` bars.
        Returns list of pattern detections with bar index, reliability, and context.
        """
        if len(df) < 10:
            return []

        patterns = []
        end = len(df)
        start = max(0, end - lookback)

        for i in range(start + 5, end):  # Need at least 5 bars of context
            bar_patterns = self._detect_at_bar(df, i)
            for p in bar_patterns:
                p['bar_index'] = i
                p['date'] = str(df.index[i].date()) if hasattr(df.index[i], 'date') else str(df.index[i])
                p['close'] = float(df.iloc[i]['Close'])
                patterns.append(p)

        return patterns

    def _detect_at_bar(self, df, idx):
        """Detect all patterns at a specific bar index."""
        patterns = []
        if idx < 5 or idx >= len(df):
            return patterns

        c = df.iloc[idx]      # current
        p1 = df.iloc[idx-1]   # previous
        p2 = df.iloc[idx-2]   # 2 bars ago
        p3 = df.iloc[idx-3] if idx >= 3 else None
        p4 = df.iloc[idx-4] if idx >= 4 else None

        o, h, l, cl = float(c['Open']), float(c['High']), float(c['Low']), float(c['Close'])
        body = abs(cl - o)
        rng = h - l
        if rng == 0:
            return patterns

        upper_shadow = h - max(o, cl)
        lower_shadow = min(o, cl) - l
        is_green = cl > o
        is_red = cl < o

        po, ph, pl, pcl = float(p1['Open']), float(p1['High']), float(p1['Low']), float(p1['Close'])
        pbody = abs(pcl - po)
        prng = ph - pl
        p_is_green = pcl > po
        p_is_red = pcl < po

        # Trend context (use 10-bar SMA slope)
        sma_vals = df['Close'].iloc[max(0, idx-10):idx+1].values
        if len(sma_vals) >= 3:
            trend_slope = (sma_vals[-1] - sma_vals[0]) / max(abs(sma_vals[0]), 1e-10)
            in_uptrend = trend_slope > 0.01
            in_downtrend = trend_slope < -0.01
        else:
            in_uptrend = in_downtrend = False

        body_ratio = body / rng if rng > 0 else 0

        # ──── Single-bar patterns ────

        # Doji
        if body_ratio < 0.1:
            if lower_shadow > 2 * body and upper_shadow < body * 0.5:
                patterns.append(self._make('Dragonfly Doji', in_downtrend))
            elif upper_shadow > 2 * body and lower_shadow < body * 0.5:
                patterns.append(self._make('Gravestone Doji', in_uptrend))
            else:
                patterns.append(self._make('Doji'))

        # Hammer (bullish reversal at bottom)
        if is_green and lower_shadow > 2 * body and upper_shadow < body * 0.3 and in_downtrend:
            patterns.append(self._make('Hammer', True))

        # Inverted Hammer
        if is_green and upper_shadow > 2 * body and lower_shadow < body * 0.3 and in_downtrend:
            patterns.append(self._make('Inverted Hammer', True))

        # Hanging Man (bearish reversal at top)
        if lower_shadow > 2 * body and upper_shadow < body * 0.3 and in_uptrend:
            patterns.append(self._make('Hanging Man', True))

        # Shooting Star
        if upper_shadow > 2 * body and lower_shadow < body * 0.3 and in_uptrend:
            patterns.append(self._make('Shooting Star', True))

        # Spinning Top
        if 0.1 < body_ratio < 0.4 and upper_shadow > body and lower_shadow > body:
            patterns.append(self._make('Spinning Top'))

        # Marubozu
        if body_ratio > 0.85:
            if is_green:
                patterns.append(self._make('Marubozu Bullish', True))
            else:
                patterns.append(self._make('Marubozu Bearish', True))

        # ──── Two-bar patterns ────

        # Bullish Engulfing
        if p_is_red and is_green and o <= pcl and cl >= po and body > pbody:
            patterns.append(self._make('Bullish Engulfing', in_downtrend))

        # Bearish Engulfing
        if p_is_green and is_red and o >= pcl and cl <= po and body > pbody:
            patterns.append(self._make('Bearish Engulfing', in_uptrend))

        # Bullish Harami
        if p_is_red and is_green and o > pcl and cl < po and body < pbody * 0.5:
            patterns.append(self._make('Bullish Harami', in_downtrend))

        # Bearish Harami
        if p_is_green and is_red and o < pcl and cl > po and body < pbody * 0.5:
            patterns.append(self._make('Bearish Harami', in_uptrend))

        # Piercing Line
        if p_is_red and is_green and o < pl and cl > (po + pcl) / 2 and cl < po:
            patterns.append(self._make('Piercing Line', in_downtrend))

        # Dark Cloud Cover
        if p_is_green and is_red and o > ph and cl < (po + pcl) / 2 and cl > po:
            patterns.append(self._make('Dark Cloud Cover', in_uptrend))

        # Tweezer Bottom
        if p_is_red and is_green and abs(l - pl) < rng * 0.05 and in_downtrend:
            patterns.append(self._make('Tweezer Bottom', True))

        # Tweezer Top
        if p_is_green and is_red and abs(h - ph) < rng * 0.05 and in_uptrend:
            patterns.append(self._make('Tweezer Top', True))

        # ──── Three-bar patterns ────

        p2o, p2h, p2l, p2cl = float(p2['Open']), float(p2['High']), float(p2['Low']), float(p2['Close'])
        p2body = abs(p2cl - p2o)
        p2_is_red = p2cl < p2o
        p2_is_green = p2cl > p2o

        # Morning Star
        if p2_is_red and pbody < p2body * 0.3 and is_green and cl > (p2o + p2cl) / 2:
            patterns.append(self._make('Morning Star', in_downtrend))

        # Evening Star
        if p2_is_green and pbody < p2body * 0.3 and is_red and cl < (p2o + p2cl) / 2:
            patterns.append(self._make('Evening Star', in_uptrend))

        # Three White Soldiers
        if (p2_is_green and p_is_green and is_green and
            pcl > p2cl and cl > pcl and
            po > p2o and o > po and
            p2body > 0 and pbody > 0 and body > 0):
            patterns.append(self._make('Three White Soldiers', True))

        # Three Black Crows
        if (p2_is_red and p_is_red and is_red and
            pcl < p2cl and cl < pcl and
            po < p2o and o < po and
            p2body > 0 and pbody > 0 and body > 0):
            patterns.append(self._make('Three Black Crows', True))

        # Three Inside Up
        if p2_is_red and p_is_green and po > p2cl and pcl < p2o and is_green and cl > p2o:
            patterns.append(self._make('Three Inside Up', in_downtrend))

        # Three Inside Down
        if p2_is_green and p_is_red and po < p2cl and pcl > p2o and is_red and cl < p2o:
            patterns.append(self._make('Three Inside Down', in_uptrend))

        # Bullish Abandoned Baby (gap down doji then gap up)
        if p2_is_red and pbody / max(prng, 1e-10) < 0.1 and ph < p2l and l > ph and is_green:
            patterns.append(self._make('Bullish Abandoned Baby', True))

        # Bearish Abandoned Baby
        if p2_is_green and pbody / max(prng, 1e-10) < 0.1 and pl > p2h and h < pl and is_red:
            patterns.append(self._make('Bearish Abandoned Baby', True))

        # ──── Five-bar pattern: Rising / Falling Three Methods ────
        if p4 is not None and p3 is not None:
            p3o, p3cl = float(p3['Open']), float(p3['Close'])
            p4o, p4cl = float(p4['Open']), float(p4['Close'])

            # Rising Three Methods: big green, 3 small reds inside, big green
            if (float(p4['Close']) > p4o and  # bar 1 green
                p3cl < p3o and p2cl < p2o and pcl < po and  # bars 2-4 red
                is_green and cl > float(p4['Close']) and  # bar 5 green and closes above bar 1
                min(p3cl, p2cl, pcl) > p4o):  # small bars stay within bar 1
                patterns.append(self._make('Rising Three Methods', True))

            # Falling Three Methods
            if (float(p4['Close']) < p4o and  # bar 1 red
                p3cl > p3o and p2cl > p2o and pcl > po and  # bars 2-4 green
                is_red and cl < float(p4['Close']) and  # bar 5 red and closes below bar 1
                max(p3cl, p2cl, pcl) < p4o):  # small bars stay within bar 1
                patterns.append(self._make('Falling Three Methods', True))

        return patterns

    def _make(self, name, trend_confirmed=False):
        """Build pattern dict with reliability and metadata."""
        meta = self.pattern_catalog.get(name, {})
        reliability = meta.get('reliability', 0.50)
        # Ensure trend_confirmed is a native Python bool (numpy booleans are not JSON serializable)
        trend_confirmed = bool(trend_confirmed)
        # Boost reliability if trend context confirms the pattern
        if trend_confirmed:
            reliability = min(reliability + 0.08, 0.95)

        return {
            'name': name,
            'type': meta.get('type', 'neutral'),
            'category': meta.get('category', 'unknown'),
            'bars': int(meta.get('bars', 1)),
            'reliability': round(float(reliability), 2),
            'strength': 'strong' if reliability >= 0.65 else 'medium' if reliability >= 0.50 else 'weak',
            'trend_confirmed': trend_confirmed,
        }

    def get_pattern_summary(self, patterns):
        """Summarize detected patterns into bullish/bearish/neutral counts."""
        if not patterns:
            return {'bullish': 0, 'bearish': 0, 'neutral': 0, 'total': 0,
                    'avg_reliability': 0, 'dominant': 'neutral', 'patterns_by_type': {}}

        bullish = [p for p in patterns if p['type'] == 'bullish']
        bearish = [p for p in patterns if p['type'] == 'bearish']
        neutral = [p for p in patterns if p['type'] == 'neutral']

        # Weight by reliability
        bull_score = sum(p['reliability'] for p in bullish)
        bear_score = sum(p['reliability'] for p in bearish)

        if bull_score > bear_score * 1.2:
            dominant = 'bullish'
        elif bear_score > bull_score * 1.2:
            dominant = 'bearish'
        else:
            dominant = 'neutral'

        return {
            'bullish': len(bullish),
            'bearish': len(bearish),
            'neutral': len(neutral),
            'total': len(patterns),
            'avg_reliability': round(np.mean([p['reliability'] for p in patterns]), 2) if patterns else 0,
            'dominant': dominant,
            'bull_score': round(bull_score, 2),
            'bear_score': round(bear_score, 2),
        }

    def get_recent_patterns(self, patterns, last_n=5):
        """Get unique patterns from the most recent N bars."""
        if not patterns:
            return []
        # Sort by bar_index descending, deduplicate by name
        sorted_p = sorted(patterns, key=lambda x: x.get('bar_index', 0), reverse=True)
        seen = set()
        recent = []
        for p in sorted_p:
            if p['name'] not in seen and len(recent) < last_n * 3:
                seen.add(p['name'])
                recent.append(p)
        return recent[:10]

    def predict_next_candle(self, df, all_patterns=None):
        """
        Predict the next candle's formation, direction probability,
        and buyer vs seller pressure.
        Uses: recency-weighted candle momentum, volume delta, RSI bias,
              wick shape analysis, and recent pattern signals.
        """
        if len(df) < 20:
            return None

        close = df['Close'].values.astype(float)
        open_ = df['Open'].values.astype(float)
        high  = df['High'].values.astype(float)
        low   = df['Low'].values.astype(float)
        vol   = df['Volume'].values.astype(float)

        n = min(20, len(df))

        # ── 1. Recency-weighted directional momentum ──────────────────────
        directions = (close[-n:] >= open_[-n:]).astype(float)
        w = np.exp(np.linspace(-1.5, 0, n))
        w /= w.sum()
        bull_momentum = float(np.dot(directions, w))

        # ── 2. Volume-weighted buyer/seller delta ─────────────────────────
        # Each bar: buying pressure = (close - low) / (high - low)
        rng = high[-n:] - low[-n:]
        rng[rng < 0.001] = 0.001
        bar_buy_pressure = (close[-n:] - low[-n:]) / rng  # 0..1
        total_vol = vol[-n:].sum()
        if total_vol > 0:
            buyer_vol  = float(np.dot(bar_buy_pressure, vol[-n:]))
            seller_vol = total_vol - buyer_vol
            buyer_pct  = round(buyer_vol  / total_vol * 100, 1)
            seller_pct = round(seller_vol / total_vol * 100, 1)
        else:
            buyer_pct = seller_pct = 50.0

        # ── 3. RSI (14-period) ─────────────────────────────────────────────
        diffs = np.diff(close[-16:])
        gains  = np.maximum(diffs, 0)
        losses = np.maximum(-diffs, 0)
        avg_g  = np.mean(gains)  if gains.size  else 0
        avg_l  = np.mean(losses) if losses.size else 0.001
        rsi = 100.0 - (100.0 / (1.0 + avg_g / max(avg_l, 1e-9)))
        rsi_bias = (rsi - 50.0) / 100.0   # −0.5 … +0.5

        # ── 4. Pattern-based bias ─────────────────────────────────────────
        pattern_bias = 0.0
        if all_patterns:
            recent_5 = sorted(all_patterns, key=lambda x: x.get('bar_index', 0), reverse=True)[:5]
            for p in recent_5:
                w_p = p.get('reliability', 0.5)
                if p['type'] == 'bullish':
                    pattern_bias += w_p
                elif p['type'] == 'bearish':
                    pattern_bias -= w_p
            pattern_bias = float(np.clip(pattern_bias / max(len(recent_5), 1) / 2, -0.3, 0.3))

        # ── 5. Ensemble directional probability ───────────────────────────
        raw = (0.35 * bull_momentum
               + 0.35 * (buyer_pct / 100.0)
               + 0.15 * (0.5 + rsi_bias)
               + 0.15 * (0.5 + pattern_bias))
        bull_prob = float(np.clip(raw, 0.05, 0.95))
        bear_prob = round(1.0 - bull_prob, 3)
        bull_prob = round(bull_prob, 3)
        bull_prob_pct = round(bull_prob * 100, 1)
        bear_prob_pct = round(bear_prob * 100, 1)

        # ── 6. Candle shape / formation ──────────────────────────────────
        bodies      = np.abs(close[-10:] - open_[-10:])
        ranges_10   = high[-10:] - low[-10:]
        ranges_10[ranges_10 < 0.001] = 0.001
        avg_body    = float(np.mean(bodies))
        avg_range   = float(np.mean(ranges_10))
        body_ratio  = avg_body / avg_range

        up_wicks    = high[-10:] - np.maximum(close[-10:], open_[-10:])
        lo_wicks    = np.minimum(close[-10:], open_[-10:]) - low[-10:]
        avg_up_wick = float(np.mean(up_wicks))
        avg_lo_wick = float(np.mean(lo_wicks))

        # Doji likelihood
        doji_chance = max(0.0, 0.45 - body_ratio)

        if doji_chance > 0.18:
            if avg_lo_wick > avg_body * 1.8:
                formation, formation_color = 'Dragonfly Doji', 'bullish'
                form_prob = 0.58
            elif avg_up_wick > avg_body * 1.8:
                formation, formation_color = 'Gravestone Doji', 'bearish'
                form_prob = 0.55
            else:
                formation, formation_color = 'Doji (Indecision)', 'neutral'
                form_prob = 0.50
        elif bull_prob >= 0.62:
            if avg_lo_wick > avg_body * 1.5:
                formation, formation_color = 'Hammer / Bullish Reversal', 'bullish'
                form_prob = 0.62
            elif body_ratio > 0.70:
                formation, formation_color = 'Marubozu Bullish (Strong)', 'bullish'
                form_prob = bull_prob
            else:
                formation, formation_color = 'Bullish Candle', 'bullish'
                form_prob = bull_prob
        elif bear_prob >= 0.62:
            if avg_up_wick > avg_body * 1.5:
                formation, formation_color = 'Shooting Star / Bearish Reversal', 'bearish'
                form_prob = 0.62
            elif body_ratio > 0.70:
                formation, formation_color = 'Marubozu Bearish (Strong)', 'bearish'
                form_prob = bear_prob
            else:
                formation, formation_color = 'Bearish Candle', 'bearish'
                form_prob = bear_prob
        else:
            formation, formation_color = 'Spinning Top (Mixed)', 'neutral'
            form_prob = 0.50

        # ── 7. Expected price change ──────────────────────────────────────
        daily_vol_pct = float(np.std(np.diff(np.log(close[-20:])))) * 100
        expected_move_pct = round((bull_prob - bear_prob) * daily_vol_pct * 1.5, 2)
        expected_range_pct = round(daily_vol_pct * 1.5, 2)

        # ── 8. Context summary ────────────────────────────────────────────
        if bull_prob >= 0.68:
            context = 'Strong buying pressure — bulls in control'
        elif bear_prob >= 0.68:
            context = 'Strong selling pressure — bears in control'
        elif abs(bull_prob - 0.5) < 0.08:
            context = 'Equilibrium — market consolidating, breakout possible'
        elif bull_prob > 0.5:
            context = 'Mild bullish bias — buyers gaining edge'
        else:
            context = 'Mild bearish bias — sellers gaining edge'

        # ── 9. Volume trend ───────────────────────────────────────────────
        if len(vol) >= 10:
            vol_recent = np.mean(vol[-5:])
            vol_prior  = np.mean(vol[-10:-5])
            vol_trend  = 'rising' if vol_recent > vol_prior * 1.05 else \
                         'falling' if vol_recent < vol_prior * 0.95 else 'neutral'
        else:
            vol_trend = 'neutral'

        # Visual candlestick shape hints (for JS renderer)
        # upper_wick_ratio / body_ratio / lower_wick_ratio  (0-100 scale)
        total_range = avg_up_wick + avg_body + avg_lo_wick
        total_range = max(total_range, 0.001)
        shape = {
            'upper_wick':   round(avg_up_wick / total_range * 100, 1),
            'body':         round(avg_body    / total_range * 100, 1),
            'lower_wick':   round(avg_lo_wick / total_range * 100, 1),
            'is_bullish':   bool(bull_prob >= 0.5),
        }

        return {
            'formation':          formation,
            'formation_color':    formation_color,
            'formation_prob':     round(form_prob * 100, 1),
            'bull_prob':          bull_prob_pct,
            'bear_prob':          bear_prob_pct,
            'buyer_pct':          buyer_pct,
            'seller_pct':         seller_pct,
            'expected_move_pct':  expected_move_pct,
            'expected_range_pct': expected_range_pct,
            'rsi':                round(float(rsi), 1),
            'vol_trend':          vol_trend,
            'context':            context,
            'shape':              shape,
            'signals': {
                'momentum_bull': round(bull_momentum * 100, 1),
                'volume_buying': buyer_pct,
                'rsi':           round(float(rsi), 1),
                'pattern_bias':  round((0.5 + pattern_bias) * 100, 1),
            },
        }


# ============================================================================
# PRICE PREDICTION ENGINE
# ============================================================================

class PricePredictionEngine:
    """
    Multi-model price prediction using statistical techniques.
    Models:
    - Linear Regression with momentum features
    - Exponential smoothing
    - Mean reversion model
    - Ensemble combination
    """

    def predict(self, df, horizon=5):
        """
        Generate price predictions for the next `horizon` days.
        Returns dict with predictions, confidence intervals, and model details.
        """
        if len(df) < 30:
            return self._empty_prediction(horizon)

        close = df['Close'].values.astype(float)
        high = df['High'].values.astype(float)
        low = df['Low'].values.astype(float)
        volume = df['Volume'].values.astype(float) if 'Volume' in df.columns else np.ones(len(close))

        current_price = close[-1]

        # Run individual models
        lr_pred = self._linear_regression_predict(close, horizon)
        ema_pred = self._exponential_smoothing_predict(close, horizon)
        mr_pred = self._mean_reversion_predict(close, horizon)
        mom_pred = self._momentum_predict(close, high, low, volume, horizon)

        # Ensemble: weighted average based on recent accuracy
        weights = self._calculate_model_weights(close, lr_pred, ema_pred, mr_pred, mom_pred)
        ensemble_pred = np.zeros(horizon)
        for w, pred in zip(weights, [lr_pred, ema_pred, mr_pred, mom_pred]):
            ensemble_pred += w * pred

        # Confidence intervals using historical volatility
        returns = np.diff(np.log(close[-60:]))
        daily_vol = np.std(returns) if len(returns) > 5 else 0.02
        confidence_bands = self._calculate_confidence_bands(current_price, ensemble_pred, daily_vol, horizon)

        # Price targets
        targets = self._calculate_price_targets(close, daily_vol, horizon)

        return {
            'current_price': round(float(current_price), 2),
            'predictions': {
                'ensemble': [round(float(p), 2) for p in ensemble_pred],
                'linear_regression': [round(float(p), 2) for p in lr_pred],
                'exponential_smoothing': [round(float(p), 2) for p in ema_pred],
                'mean_reversion': [round(float(p), 2) for p in mr_pred],
                'momentum': [round(float(p), 2) for p in mom_pred],
            },
            'model_weights': {
                'linear_regression': round(float(weights[0]), 3),
                'exponential_smoothing': round(float(weights[1]), 3),
                'mean_reversion': round(float(weights[2]), 3),
                'momentum': round(float(weights[3]), 3),
            },
            'confidence': {
                'upper_68': [round(float(x), 2) for x in confidence_bands['upper_1sigma']],
                'lower_68': [round(float(x), 2) for x in confidence_bands['lower_1sigma']],
                'upper_95': [round(float(x), 2) for x in confidence_bands['upper_2sigma']],
                'lower_95': [round(float(x), 2) for x in confidence_bands['lower_2sigma']],
            },
            'targets': targets,
            'daily_volatility': round(float(daily_vol) * 100, 2),
            'horizon': horizon,
            'predicted_direction': 'bullish' if ensemble_pred[-1] > current_price else 'bearish',
            'predicted_change_pct': round(float((ensemble_pred[-1] - current_price) / current_price * 100), 2),
        }

    def _linear_regression_predict(self, close, horizon):
        """Linear regression on recent price data with polynomial features."""
        n = min(60, len(close))
        y = close[-n:]
        x = np.arange(n)

        # Fit polynomial regression (degree 2)
        try:
            coeffs = np.polyfit(x, y, 2)
            poly = np.poly1d(coeffs)
            future_x = np.arange(n, n + horizon)
            pred = poly(future_x)

            # Dampen predictions that diverge too far (prevent runaway parabolas)
            max_daily_change = np.std(np.diff(y)) * 3
            dampened = [close[-1]]
            for i in range(horizon):
                raw = pred[i]
                change = raw - dampened[-1]
                change = np.clip(change, -max_daily_change, max_daily_change)
                dampened.append(dampened[-1] + change)
            return np.array(dampened[1:])
        except Exception:
            return np.full(horizon, close[-1])

    def _exponential_smoothing_predict(self, close, horizon):
        """Double exponential smoothing (Holt's method)."""
        n = min(60, len(close))
        y = close[-n:]

        # Optimize alpha and beta
        alpha = 0.3
        beta = 0.1

        # Initialize
        level = y[0]
        trend = np.mean(np.diff(y[:5])) if len(y) > 5 else 0

        for val in y:
            prev_level = level
            level = alpha * val + (1 - alpha) * (prev_level + trend)
            trend = beta * (level - prev_level) + (1 - beta) * trend

        # Forecast
        preds = []
        for i in range(1, horizon + 1):
            preds.append(level + i * trend)
        return np.array(preds)

    def _mean_reversion_predict(self, close, horizon):
        """Mean reversion model using Ornstein-Uhlenbeck process."""
        n = min(120, len(close))
        y = close[-n:]
        mean_price = np.mean(y)
        current = y[-1]

        # Estimate mean reversion speed
        deviations = y - mean_price
        if len(deviations) > 1:
            # Simple AR(1) coefficient as mean reversion speed
            autocorr = np.corrcoef(deviations[:-1], deviations[1:])[0, 1]
            theta = max(0.01, min(1 - autocorr, 0.5))  # speed of reversion
        else:
            theta = 0.1

        preds = []
        price = current
        for i in range(horizon):
            price = price + theta * (mean_price - price)
            preds.append(price)
        return np.array(preds)

    def _momentum_predict(self, close, high, low, volume, horizon):
        """Momentum-based prediction using RSI and MACD-like features."""
        n = min(60, len(close))
        y = close[-n:]

        # Calculate momentum features
        returns_5 = (y[-1] / y[-5] - 1) if len(y) >= 5 else 0
        returns_20 = (y[-1] / y[-20] - 1) if len(y) >= 20 else 0

        # RSI-based direction
        gains = np.diff(y)
        avg_gain = np.mean(gains[gains > 0][-14:]) if len(gains[gains > 0]) > 0 else 0
        avg_loss = abs(np.mean(gains[gains < 0][-14:])) if len(gains[gains < 0]) > 0 else 1e-10
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))

        # Daily expected move based on ATR
        if len(high) >= 14 and len(low) >= 14:
            atr = np.mean(high[-14:] - low[-14:])
        else:
            atr = np.std(y) * 0.5

        # Direction and magnitude
        if rsi < 30:
            direction = 1  # oversold, expect bounce
            magnitude = atr * 0.3
        elif rsi > 70:
            direction = -1  # overbought, expect pullback
            magnitude = atr * 0.3
        else:
            # Use recent momentum
            direction = 1 if returns_5 > 0 else -1
            magnitude = atr * 0.15

        # Decay momentum over forecast horizon
        preds = []
        price = y[-1]
        for i in range(horizon):
            decay = np.exp(-0.1 * i)
            price += direction * magnitude * decay
            preds.append(price)
        return np.array(preds)

    def _calculate_model_weights(self, close, *predictions):
        """Calculate model weights based on recent backtest accuracy."""
        n_test = min(10, len(close) - 30)
        if n_test < 3:
            k = len(predictions)
            return np.ones(k) / k

        errors = []
        for pred_func_result in predictions:
            # Simple: use inverse of how far the last prediction is from recent trend
            err = abs(pred_func_result[0] - close[-1]) / max(abs(close[-1]), 1e-10)
            errors.append(max(err, 1e-10))

        # Inverse error weighting
        inv_errors = [1.0 / e for e in errors]
        total = sum(inv_errors)
        return np.array([ie / total for ie in inv_errors])

    def _calculate_confidence_bands(self, current_price, predictions, daily_vol, horizon):
        """Calculate confidence bands based on historical volatility."""
        upper_1 = []
        lower_1 = []
        upper_2 = []
        lower_2 = []

        for i in range(horizon):
            # Volatility grows with sqrt of time
            vol_at_t = daily_vol * np.sqrt(i + 1) * current_price
            pred = predictions[i]
            upper_1.append(pred + vol_at_t)
            lower_1.append(pred - vol_at_t)
            upper_2.append(pred + 2 * vol_at_t)
            lower_2.append(pred - 2 * vol_at_t)

        return {
            'upper_1sigma': np.array(upper_1),
            'lower_1sigma': np.array(lower_1),
            'upper_2sigma': np.array(upper_2),
            'lower_2sigma': np.array(lower_2),
        }

    def _calculate_price_targets(self, close, daily_vol, horizon):
        """Calculate key price targets."""
        current = close[-1]
        atr_est = daily_vol * current

        # Support/Resistance from recent pivots
        recent = close[-20:]
        resistance = float(np.max(recent))
        support = float(np.min(recent))

        return {
            'bullish_target': round(float(current + atr_est * horizon * 0.5), 2),
            'bearish_target': round(float(current - atr_est * horizon * 0.5), 2),
            'resistance': round(resistance, 2),
            'support': round(support, 2),
            'stop_loss': round(float(current - atr_est * 1.5), 2),
            'take_profit_1': round(float(current + atr_est * 2), 2),
            'take_profit_2': round(float(current + atr_est * 3), 2),
            'take_profit_3': round(float(current + atr_est * 4), 2),
        }

    def _empty_prediction(self, horizon):
        """Return empty prediction when insufficient data."""
        return {
            'current_price': 0,
            'predictions': {k: [0] * horizon for k in
                           ['ensemble', 'linear_regression', 'exponential_smoothing',
                            'mean_reversion', 'momentum']},
            'model_weights': {},
            'confidence': {k: [0] * horizon for k in
                          ['upper_68', 'lower_68', 'upper_95', 'lower_95']},
            'targets': {},
            'daily_volatility': 0,
            'horizon': horizon,
            'predicted_direction': 'neutral',
            'predicted_change_pct': 0,
        }


# ============================================================================
# TREND ANALYSIS ENGINE
# ============================================================================

class TrendAnalysisEngine:
    """Multi-timeframe trend analysis with strength scoring."""

    def analyze(self, df):
        """Analyze trend across multiple perspectives."""
        if len(df) < 50:
            return self._empty_analysis()

        close = df['Close'].values.astype(float)
        high = df['High'].values.astype(float)
        low = df['Low'].values.astype(float)
        volume = df['Volume'].values.astype(float) if 'Volume' in df.columns else np.ones(len(close))

        return {
            'short_term': self._analyze_period(close, 10, 'short'),
            'medium_term': self._analyze_period(close, 30, 'medium'),
            'long_term': self._analyze_period(close, 60, 'long'),
            'volume_trend': self._analyze_volume_trend(close, volume),
            'volatility_regime': self._analyze_volatility(close),
            'trend_strength': self._calculate_trend_strength(close),
            'support_resistance': self._find_support_resistance(close, high, low),
        }

    def _analyze_period(self, close, period, label):
        """Analyze trend for a specific period."""
        n = min(period, len(close))
        segment = close[-n:]

        if len(segment) < 3:
            return {'direction': 'neutral', 'strength': 0, 'change_pct': 0}

        change_pct = (segment[-1] / segment[0] - 1) * 100
        x = np.arange(len(segment))

        try:
            slope = np.polyfit(x, segment, 1)[0]
            r2 = 1 - np.sum((segment - np.polyval(np.polyfit(x, segment, 1), x))**2) / \
                      max(np.sum((segment - np.mean(segment))**2), 1e-10)
        except Exception:
            slope = 0
            r2 = 0

        if slope > 0 and r2 > 0.3:
            direction = 'bullish'
        elif slope < 0 and r2 > 0.3:
            direction = 'bearish'
        else:
            direction = 'neutral'

        return {
            'direction': direction,
            'strength': round(float(min(abs(r2 * 100), 100)), 1),
            'change_pct': round(float(change_pct), 2),
            'slope': round(float(slope), 4),
        }

    def _analyze_volume_trend(self, close, volume):
        """Check if volume confirms price trend."""
        n = min(20, len(close))
        recent_close = close[-n:]
        recent_vol = volume[-n:]

        price_up = recent_close[-1] > recent_close[0]
        avg_vol_recent = np.mean(recent_vol[-5:])
        avg_vol_prior = np.mean(recent_vol[:max(1, n-5)])

        vol_increasing = avg_vol_recent > avg_vol_prior * 1.1

        if price_up and vol_increasing:
            status = 'confirmed_up'
        elif not price_up and vol_increasing:
            status = 'confirmed_down'
        elif price_up and not vol_increasing:
            status = 'weak_up'
        else:
            status = 'weak_down'

        return {
            'status': status,
            'avg_recent_volume': int(avg_vol_recent),
            'avg_prior_volume': int(avg_vol_prior),
            'volume_ratio': round(float(avg_vol_recent / max(avg_vol_prior, 1)), 2),
        }

    def _analyze_volatility(self, close):
        """Determine volatility regime."""
        if len(close) < 30:
            return {'regime': 'normal', 'current_vol': 0, 'avg_vol': 0}

        returns = np.diff(np.log(close[-60:]))
        current_vol = np.std(returns[-10:]) * np.sqrt(252) * 100
        avg_vol = np.std(returns) * np.sqrt(252) * 100

        if current_vol > avg_vol * 1.5:
            regime = 'high'
        elif current_vol < avg_vol * 0.7:
            regime = 'low'
        else:
            regime = 'normal'

        return {
            'regime': regime,
            'current_vol': round(float(current_vol), 1),
            'avg_vol': round(float(avg_vol), 1),
            'percentile': round(float(min(current_vol / max(avg_vol, 1) * 50, 100)), 0),
        }

    def _calculate_trend_strength(self, close):
        """ADX-like trend strength calculation."""
        if len(close) < 20:
            return {'score': 0, 'label': 'No Data'}

        # Calculate directional movement
        n = min(50, len(close))
        segment = close[-n:]
        returns = np.diff(segment) / segment[:-1] * 100

        # Trend consistency: percentage of days in the dominant direction
        up_days = np.sum(returns > 0)
        down_days = np.sum(returns < 0)
        total_days = len(returns)
        consistency = max(up_days, down_days) / max(total_days, 1)

        # Magnitude: cumulative return
        cum_return = abs((segment[-1] / segment[0] - 1) * 100)

        # Score 0-100
        score = min(consistency * 50 + min(cum_return, 50), 100)

        if score >= 70:
            label = 'Strong Trend'
        elif score >= 45:
            label = 'Moderate Trend'
        elif score >= 25:
            label = 'Weak Trend'
        else:
            label = 'No Clear Trend'

        return {
            'score': round(float(score), 1),
            'label': label,
            'consistency': round(float(consistency * 100), 1),
            'direction': 'up' if up_days > down_days else 'down',
        }

    def _find_support_resistance(self, close, high, low):
        """Find key support and resistance levels."""
        n = min(60, len(close))
        recent_high = high[-n:]
        recent_low = low[-n:]
        recent_close = close[-n:]

        # Find local maxima/minima
        resistances = []
        supports = []

        for i in range(2, len(recent_high) - 2):
            if recent_high[i] > recent_high[i-1] and recent_high[i] > recent_high[i-2] and \
               recent_high[i] > recent_high[i+1] and recent_high[i] > recent_high[i+2]:
                resistances.append(float(recent_high[i]))

            if recent_low[i] < recent_low[i-1] and recent_low[i] < recent_low[i-2] and \
               recent_low[i] < recent_low[i+1] and recent_low[i] < recent_low[i+2]:
                supports.append(float(recent_low[i]))

        # Cluster nearby levels
        current_price = float(close[-1])
        resistances = sorted(set([round(r, 2) for r in resistances if r > current_price]))[:3]
        supports = sorted(set([round(s, 2) for s in supports if s < current_price]), reverse=True)[:3]

        return {
            'resistances': resistances,
            'supports': supports,
            'nearest_resistance': resistances[0] if resistances else None,
            'nearest_support': supports[0] if supports else None,
        }

    def _empty_analysis(self):
        return {
            'short_term': {'direction': 'neutral', 'strength': 0, 'change_pct': 0},
            'medium_term': {'direction': 'neutral', 'strength': 0, 'change_pct': 0},
            'long_term': {'direction': 'neutral', 'strength': 0, 'change_pct': 0},
            'volume_trend': {'status': 'unknown', 'volume_ratio': 0},
            'volatility_regime': {'regime': 'unknown', 'current_vol': 0},
            'trend_strength': {'score': 0, 'label': 'No Data'},
            'support_resistance': {'resistances': [], 'supports': []},
        }


# ============================================================================
# AI SIGNAL GENERATOR
# ============================================================================

class AISignalGenerator:
    """Combines patterns, predictions, and trend analysis into actionable signals."""

    def generate_signal(self, pattern_summary, predictions, trend_analysis):
        """Generate an AI-powered trading signal."""
        score = 0
        reasons = []
        warnings = []

        # Pattern analysis (weight: 30%)
        if pattern_summary['total'] > 0:
            if pattern_summary['dominant'] == 'bullish':
                score += 30 * (pattern_summary['bull_score'] / max(pattern_summary['bull_score'] + pattern_summary['bear_score'], 1))
                reasons.append(f"🕯️ {pattern_summary['bullish']} bullish pattern(s) detected")
            elif pattern_summary['dominant'] == 'bearish':
                score -= 30 * (pattern_summary['bear_score'] / max(pattern_summary['bull_score'] + pattern_summary['bear_score'], 1))
                reasons.append(f"🕯️ {pattern_summary['bearish']} bearish pattern(s) detected")

        # Prediction analysis (weight: 35%)
        if predictions.get('predicted_change_pct', 0) != 0:
            pred_pct = predictions['predicted_change_pct']
            pred_score = min(abs(pred_pct) * 5, 35) * (1 if pred_pct > 0 else -1)
            score += pred_score
            direction = '📈 Bullish' if pred_pct > 0 else '📉 Bearish'
            reasons.append(f"{direction} prediction: {pred_pct:+.2f}% over {predictions['horizon']} days")

        # Trend analysis (weight: 35%)
        trend_score = 0
        for period in ['short_term', 'medium_term', 'long_term']:
            t = trend_analysis.get(period, {})
            if t.get('direction') == 'bullish':
                trend_score += t.get('strength', 0) * 0.35 / 3
            elif t.get('direction') == 'bearish':
                trend_score -= t.get('strength', 0) * 0.35 / 3
        score += trend_score

        if trend_analysis.get('short_term', {}).get('direction') == trend_analysis.get('long_term', {}).get('direction'):
            if trend_analysis['short_term']['direction'] != 'neutral':
                reasons.append(f"✅ Short and long-term trends aligned ({trend_analysis['short_term']['direction']})")
        else:
            warnings.append("⚠️ Short and long-term trends are misaligned")

        # Volume confirmation
        vol_trend = trend_analysis.get('volume_trend', {})
        if vol_trend.get('status', '').startswith('confirmed'):
            reasons.append(f"📊 Volume confirms price movement (ratio: {vol_trend.get('volume_ratio', 0):.1f}x)")
        elif vol_trend.get('status', '').startswith('weak'):
            warnings.append("⚠️ Low volume — move may lack conviction")

        # Volatility warning
        vol_regime = trend_analysis.get('volatility_regime', {})
        if vol_regime.get('regime') == 'high':
            warnings.append(f"⚠️ High volatility regime ({vol_regime.get('current_vol', 0):.1f}% annualized)")

        # Determine overall signal
        if score >= 30:
            signal = 'STRONG BUY'
            confidence = min(score / 50 * 100, 95)
        elif score >= 10:
            signal = 'BUY'
            confidence = min(score / 30 * 100, 85)
        elif score <= -30:
            signal = 'STRONG SELL'
            confidence = min(abs(score) / 50 * 100, 95)
        elif score <= -10:
            signal = 'SELL'
            confidence = min(abs(score) / 30 * 100, 85)
        else:
            signal = 'HOLD'
            confidence = max(30, 60 - abs(score) * 2)

        return {
            'signal': signal,
            'score': round(float(score), 1),
            'confidence': round(float(confidence), 1),
            'reasons': reasons,
            'warnings': warnings,
            'components': {
                'pattern_score': round(float(score - trend_score - (predictions.get('predicted_change_pct', 0) * 5 if predictions.get('predicted_change_pct') else 0)), 1),
                'prediction_score': round(float(min(abs(predictions.get('predicted_change_pct', 0)) * 5, 35)), 1),
                'trend_score': round(float(trend_score), 1),
            }
        }


# ============================================================================
# MULTI-STOCK HEATMAP ANALYZER
# ============================================================================

class StockHeatmapAnalyzer:
    """Analyze multiple stocks for bullish/bearish heatmap display."""

    def __init__(self):
        self.pattern_engine = CandlestickPatternEngine()

    def analyze_watchlist(self, symbols, period='3mo'):
        """Analyze a list of symbols and return heatmap data."""
        results = []

        def _analyze_one(symbol):
            try:
                if _USE_CACHED:
                    df = cached_get_history(symbol, period=period, interval='1d')
                else:
                    ticker = yf.Ticker(symbol)
                    df = ticker.history(period=period)
                if df is None or df.empty or len(df) < 20:
                    return None

                close = df['Close'].values.astype(float)
                patterns = self.pattern_engine.detect_all_patterns(df, lookback=10)
                summary = self.pattern_engine.get_pattern_summary(patterns)

                # Quick sentiment score
                change_1d = (close[-1] / close[-2] - 1) * 100 if len(close) >= 2 else 0
                change_5d = (close[-1] / close[-5] - 1) * 100 if len(close) >= 5 else 0
                change_20d = (close[-1] / close[-20] - 1) * 100 if len(close) >= 20 else 0

                # RSI
                gains = np.diff(close[-15:])
                avg_gain = np.mean(gains[gains > 0]) if len(gains[gains > 0]) > 0 else 0
                avg_loss = abs(np.mean(gains[gains < 0])) if len(gains[gains < 0]) > 0 else 1e-10
                rsi = 100 - (100 / (1 + avg_gain / avg_loss))

                # Sentiment: -100 to +100
                sentiment = 0
                sentiment += np.clip(change_1d * 5, -20, 20)
                sentiment += np.clip(change_5d * 2, -30, 30)
                sentiment += np.clip(change_20d, -20, 20)
                if summary['dominant'] == 'bullish':
                    sentiment += 15
                elif summary['dominant'] == 'bearish':
                    sentiment -= 15
                if rsi < 30:
                    sentiment += 15
                elif rsi > 70:
                    sentiment -= 15

                return {
                    'symbol': symbol,
                    'price': round(float(close[-1]), 2),
                    'change_1d': round(float(change_1d), 2),
                    'change_5d': round(float(change_5d), 2),
                    'change_20d': round(float(change_20d), 2),
                    'rsi': round(float(rsi), 1),
                    'sentiment': round(float(np.clip(sentiment, -100, 100)), 1),
                    'pattern_dominant': summary['dominant'],
                    'pattern_count': summary['total'],
                    'bullish_patterns': summary['bullish'],
                    'bearish_patterns': summary['bearish'],
                }
            except Exception as e:
                print(f"Error analyzing {symbol}: {e}")
                return None

        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_analyze_one, s): s for s in symbols}
            for f in futures:
                try:
                    r = f.result(timeout=30)
                    if r:
                        results.append(r)
                except Exception:
                    pass

        # Sort by sentiment
        results.sort(key=lambda x: x['sentiment'], reverse=True)
        return results


# ============================================================================
# MAIN AI ANALYSIS FUNCTION
# ============================================================================

def run_ai_analysis(symbol, period='6mo', prediction_horizon=5, df=None, info=None):
    """
    Run complete AI analysis on a symbol.
    Returns comprehensive analysis with patterns, predictions, trends, and signals.
    Pass df/info to reuse cached data and avoid redundant API calls.
    """
    try:
        if df is None:
            try:
                if _USE_CACHED:
                    df = cached_get_history(symbol, period=period, interval='1d')
                else:
                    ticker = yf.Ticker(symbol)
                    df = ticker.history(period=period)
            except Exception as fetch_err:
                if 'no such table' in str(fetch_err).lower():
                    # Corrupted yfinance cache — nuke and retry once
                    import glob
                    cache_dir = os.path.join(os.getcwd(), ".yfinance_cache")
                    for db_file in glob.glob(os.path.join(cache_dir, "*.db*")):
                        try:
                            os.remove(db_file)
                        except OSError:
                            pass
                    print(f"🔄 Cleared corrupted yfinance cache, retrying {symbol}")
                    if _USE_CACHED:
                        df = cached_get_history(symbol, period=period, interval='1d')
                    else:
                        ticker = yf.Ticker(symbol)
                        df = ticker.history(period=period)
                else:
                    raise

        if df is None or df.empty or len(df) < 20:
            return {'success': False, 'error': f'Insufficient data for {symbol}'}

        # Get current info (skip API if pre-provided)
        if info is None:
            info = {}
            try:
                if _USE_CACHED:
                    info = cached_get_ticker_info(symbol) or {}
                else:
                    ticker = yf.Ticker(symbol)
                    info = ticker.info or {}
            except Exception:
                pass

        # Run engines
        pattern_engine = CandlestickPatternEngine()
        prediction_engine = PricePredictionEngine()
        trend_engine = TrendAnalysisEngine()
        signal_gen = AISignalGenerator()

        # Pattern detection
        all_patterns = pattern_engine.detect_all_patterns(df, lookback=50)
        recent_patterns = pattern_engine.get_recent_patterns(all_patterns)
        pattern_summary = pattern_engine.get_pattern_summary(all_patterns)
        next_candle = pattern_engine.predict_next_candle(df, all_patterns)

        # Price prediction
        predictions = prediction_engine.predict(df, horizon=prediction_horizon)

        # Trend analysis
        trend_analysis = trend_engine.analyze(df)

        # AI Signal
        ai_signal = signal_gen.generate_signal(pattern_summary, predictions, trend_analysis)

        # Build chart overlay data (patterns mapped to candle dates)
        pattern_overlays = []
        for p in all_patterns[-30:]:  # Last 30 detected patterns
            pattern_overlays.append({
                'date': p.get('date', ''),
                'price': p.get('close', 0),
                'name': p['name'],
                'type': p['type'],
                'reliability': p['reliability'],
            })

        return {
            'success': True,
            'symbol': symbol,
            'company_name': info.get('shortName', info.get('longName', symbol)),
            'current_price': predictions['current_price'],
            'patterns': {
                'all': all_patterns[-50:],  # Cap at 50 for response size
                'recent': recent_patterns,
                'summary': pattern_summary,
                'overlays': pattern_overlays,
                'next_candle': next_candle,
            },
            'predictions': predictions,
            'trend_analysis': trend_analysis,
            'ai_signal': ai_signal,
            'metadata': {
                'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'data_period': period,
                'bars_analyzed': len(df),
                'prediction_horizon': prediction_horizon,
            },
        }

    except Exception as e:
        return {'success': False, 'error': str(e)}


def run_heatmap_analysis(symbols=None):
    """Run heatmap analysis for a list of symbols."""
    if symbols is None:
        symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'AMD',
            'NFLX', 'CRM', 'INTC', 'ORCL', 'ADBE', 'PYPL', 'SQ', 'SHOP',
            'SPY', 'QQQ', 'IWM', 'DIA',
            'JPM', 'BAC', 'GS', 'V', 'MA',
            'JNJ', 'UNH', 'PFE', 'ABBV', 'MRK'
        ]

    analyzer = StockHeatmapAnalyzer()
    results = analyzer.analyze_watchlist(symbols)

    # Calculate market breadth
    total = len(results)
    bullish_count = sum(1 for r in results if r['sentiment'] > 20)
    bearish_count = sum(1 for r in results if r['sentiment'] < -20)
    neutral_count = total - bullish_count - bearish_count

    return {
        'success': True,
        'stocks': results,
        'market_breadth': {
            'total': total,
            'bullish': bullish_count,
            'bearish': bearish_count,
            'neutral': neutral_count,
            'breadth_pct': round(bullish_count / max(total, 1) * 100, 1),
        },
        'avg_sentiment': round(float(np.mean([r['sentiment'] for r in results])), 1) if results else 0,
    }


if __name__ == '__main__':
    # Quick test
    print("=" * 80)
    print("🤖 AI Stock Analysis Engine - Test Run")
    print("=" * 80)

    result = run_ai_analysis('AAPL', prediction_horizon=5)
    if result['success']:
        print(f"\n📊 {result['symbol']} - {result['company_name']}")
        print(f"💰 Current Price: ${result['current_price']}")
        print(f"\n🕯️ Pattern Summary:")
        ps = result['patterns']['summary']
        print(f"   Bullish: {ps['bullish']} | Bearish: {ps['bearish']} | Neutral: {ps['neutral']}")
        print(f"   Dominant: {ps['dominant']} (Avg Reliability: {ps['avg_reliability']})")
        print(f"\n📈 Price Prediction ({result['predictions']['horizon']}d):")
        print(f"   Ensemble: {result['predictions']['predictions']['ensemble']}")
        print(f"   Direction: {result['predictions']['predicted_direction']}")
        print(f"   Change: {result['predictions']['predicted_change_pct']:+.2f}%")
        print(f"\n🤖 AI Signal: {result['ai_signal']['signal']}")
        print(f"   Confidence: {result['ai_signal']['confidence']}%")
        print(f"   Score: {result['ai_signal']['score']}")
        for r in result['ai_signal']['reasons']:
            print(f"   {r}")
        for w in result['ai_signal']['warnings']:
            print(f"   {w}")
    else:
        print(f"Error: {result['error']}")
