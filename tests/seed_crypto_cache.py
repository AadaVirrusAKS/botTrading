"""Seed the crypto disk cache using Ticker.history() (v8 API) which is more reliable."""
import yfinance as yf
import json
import os
import time

CRYPTO_SYMBOLS = [
    'BTC-USD', 'ETH-USD', 'USDT-USD', 'BNB-USD', 'SOL-USD',
    'XRP-USD', 'USDC-USD', 'ADA-USD', 'DOGE-USD',
    'AVAX-USD', 'TRX-USD', 'LINK-USD', 'SHIB-USD',
    'DOT-USD', 'BCH-USD', 'NEAR-USD', 'MATIC-USD', 'LTC-USD',
    'ATOM-USD', 'FIL-USD', 'XLM-USD', 'ALGO-USD', 'AAVE-USD',
    'ICP-USD', 'HBAR-USD', 'ETC-USD', 'RNDR-USD', 'CRO-USD',
    'VET-USD', 'THETA-USD', 'FTM-USD', 'FLOW-USD', 'MKR-USD',
    'INJ-USD', 'TON11419-USD', 'SHIB-USD', 'UNI7083-USD',
    'PEPE24478-USD', 'DAI-USD', 'APT21794-USD', 'SUI20947-USD',
]

results = {}
failed = []

for sym in CRYPTO_SYMBOLS:
    try:
        t = yf.Ticker(sym)
        hist = t.history(period='5d', interval='1d')
        if hist is None or hist.empty or len(hist) < 2:
            failed.append(sym)
            continue
        cv = hist['Close'].dropna()
        if len(cv) < 2:
            failed.append(sym)
            continue
        cur = float(cv.iloc[-1])
        prev = float(cv.iloc[-2])
        change = cur - prev
        results[sym] = {
            'symbol': sym,
            'price': round(cur, 2),
            'change': round(change, 2),
            'changePct': round((change / prev * 100), 2) if prev else 0,
            'volume': int(hist['Volume'].iloc[-1]) if 'Volume' in hist.columns else 0,
            'high': round(float(hist['High'].iloc[-1]), 2) if 'High' in hist.columns else 0,
            'low': round(float(hist['Low'].iloc[-1]), 2) if 'Low' in hist.columns else 0,
        }
    except Exception as e:
        failed.append(sym)

print(f"Got {len(results)}/{len(CRYPTO_SYMBOLS)} symbols, {len(failed)} failed")

if results:
    cache_dir = '.dashboard_cache'
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, 'crypto_cache.json')
    with open(cache_file, 'w') as f:
        json.dump({'ts': time.time(), 'data': results}, f)
    print(f"Saved to {cache_file}")
    for s in ['BTC-USD', 'ETH-USD', 'SOL-USD', 'XRP-USD', 'DOGE-USD']:
        q = results.get(s)
        if q:
            print(f"  {s}: ${q['price']:.2f} ({q['changePct']:+.2f}%)")
    if failed:
        print(f"Failed: {failed[:10]}")
else:
    print("FAILED: no data fetched")
