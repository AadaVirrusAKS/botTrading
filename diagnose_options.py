#!/usr/bin/env python3
"""Diagnostic: check why intraday options scanner isn't finding high-confidence trades."""
import sys
sys.path.insert(0, '.')

from services.market_data import cached_get_history, cached_batch_prices
import pandas as pd

symbols = ['SPY', 'QQQ', 'AAPL', 'TSLA', 'NVDA', 'AMD', 'MSFT', 'AMZN', 'GOOGL', 'META', 'IWM']

print("=" * 90)
print("INTRADAY OPTIONS SCANNER DIAGNOSTIC")
print("=" * 90)

for sym in symbols:
    df = cached_get_history(sym, period='5d', interval='5m')
    if df is None or df.empty or len(df) < 50:
        print(f"{sym:6s}: NO DATA")
        continue

    df['Typical'] = (df['High'] + df['Low'] + df['Close']) / 3
    df['DateCol'] = df.index.date
    df['Cum_TV'] = df.groupby('DateCol').apply(lambda g: (g['Typical'] * g['Volume']).cumsum()).droplevel(0)
    df['Cum_V'] = df.groupby('DateCol')['Volume'].cumsum()
    df['VWAP'] = df['Cum_TV'] / df['Cum_V']

    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['MACD'] = df['EMA9'] - df['EMA21']
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    latest_date = df.index.date[-1]
    today = df[df.index.date == latest_date]
    if today.empty or len(today) < 5:
        print(f"{sym:6s}: Not enough bars today ({len(today)})")
        continue

    bar = today.iloc[-1]
    price = float(bar['Close'])
    vwap = float(bar['VWAP']) if not pd.isna(bar['VWAP']) else price
    rsi = float(bar['RSI']) if not pd.isna(bar['RSI']) else 50
    macd_hist = float(bar['MACD_Hist']) if not pd.isna(bar['MACD_Hist']) else 0
    ema9 = float(bar['EMA9'])
    ema21 = float(bar['EMA21'])

    above_vwap = price > vwap
    ema_bull = ema9 > ema21
    macd_bull = macd_hist > 0

    bull_pts = sum([above_vwap, ema_bull, macd_bull, rsi < 65])
    bear_pts = sum([not above_vwap, not ema_bull, not macd_bull, rsi > 35])

    # Determine what would happen
    status = ""
    if ema_bull and macd_hist < 0:
        status = "BLOCKED: EMA bull + MACD bear conflict"
    elif not ema_bull and macd_hist > 0:
        status = "BLOCKED: EMA bear + MACD bull conflict"
    elif bull_pts >= 3:
        status = "BULLISH -> CALL"
    elif bear_pts >= 3:
        status = "BEARISH -> PUT"
    else:
        status = f"BLOCKED: no direction (bull={bull_pts}, bear={bear_pts})"

    # Estimate max confidence score if it passed
    # Just simulate max achievable
    score = 0
    # vol_ratio
    avg_vol = float(today['Volume'].mean())
    cur_vol = float(bar['Volume'])
    vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1
    if vol_ratio > 2.0: score += 3
    elif vol_ratio > 1.3: score += 2
    else: score += 1
    # MACD momentum
    if abs(macd_hist) > 0.1: score += 3
    elif abs(macd_hist) > 0.03: score += 2
    else: score += 1
    # directional alignment
    dir_pts = bull_pts if "BULLISH" in status else bear_pts
    if dir_pts >= 4: score += 2
    elif dir_pts >= 3: score += 1
    # RSI sweet spot (assume best case for direction)
    if "BULLISH" in status:
        if 25 <= rsi <= 45: score += 2
        elif 45 < rsi <= 55: score += 1
    else:
        if 55 <= rsi <= 75: score += 2
        elif 45 <= rsi < 55: score += 1

    # confidence (without IV/OI/opt_vol which need chain data)
    # These could add +6 more (IV 0-2, OI 0-2, OptVol 0-2)
    min_conf = int(35 + score * 3.5)
    max_conf_with_chain = int(35 + (score + 6) * 3.5)
    
    print(f"{sym:6s} | ${price:>8.2f} | VWAP ${vwap:>8.2f} | RSI {rsi:5.1f} | "
          f"MACD_H {macd_hist:>8.4f} | EMA9>{ema21 if ema_bull else 'NO':>6} | "
          f"Bull={bull_pts} Bear={bear_pts} | score_base={score} conf={min_conf}-{max_conf_with_chain}% | {status}")

print()
print("=" * 90)
print("KEY OBSERVATIONS:")
print(f"  min_confidence required = 95%")
print(f"  Confidence formula: min(98, max(50, int(35 + score * 3.5)))")
print(f"  Max score = 18 -> confidence = min(98, 35 + 18*3.5) = min(98, 98) = 98%")
print(f"  Score >= 18 needed for 95%+ confidence -> 35 + 18*3.5 = 98")
print(f"  Actually: (95 - 35) / 3.5 = 17.14 -> need score >= 18 for 95%+")
print(f"  Score 17 -> conf = 35 + 17*3.5 = 94.5 -> 94% (FAILS)")
print(f"  Score 18 -> conf = 35 + 18*3.5 = 98% (MAX POSSIBLE = 98%)")
print(f"")
print(f"  PROBLEM: Need a PERFECT score of 18/18 to reach 95% confidence!")
print(f"  That means ALL of: vol_spike(3), strong_momentum(3), high_IV(2), high_OI(2),")
print(f"  high_opt_vol(2), full_alignment(2), RSI_sweet_spot(2), tight_spread(0)")
print(f"  AND no bid-ask penalty. This is nearly impossible to achieve consistently.")
