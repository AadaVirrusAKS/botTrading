"""Quick test to verify crypto download with MultiIndex handling."""
import yfinance as yf

syms = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'XRP-USD', 'ADA-USD']
data = yf.download(syms, period='5d', interval='1d', group_by='ticker',
                   threads=True, progress=False, timeout=60, auto_adjust=True)

if data is None or data.empty:
    print("FAIL: yf.download returned empty")
    exit(1)

print(f"shape: {data.shape}")
print(f"columns type: {type(data.columns).__name__}")

if hasattr(data.columns, 'get_level_values'):
    l0 = list(data.columns.get_level_values(0).unique())
    print(f"level 0 sample: {l0[:6]}")
    price_fields = {'Close', 'Open', 'High', 'Low', 'Volume'}
    field_first = bool(price_fields.intersection(set(str(x) for x in l0)))
    print(f"field_first: {field_first}")

    for s in syms:
        try:
            if field_first:
                c = float(data['Close'][s].dropna().iloc[-1])
            else:
                c = float(data[s]['Close'].dropna().iloc[-1])
            print(f"  {s}: ${c:,.2f}")
        except Exception as e:
            print(f"  {s}: ERROR {e}")
else:
    print("No MultiIndex - single ticker mode")

print("\nOK - crypto download working")
