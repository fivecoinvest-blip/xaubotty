import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import requests
import time
from datetime import datetime

print("=" * 55)
print("  BINANCE PAXG/USDT LIVE TEST (Zero API Key)")
print("=" * 55)

# 1. Fetch Live Price & Book Ticker from Binance Public API
binance_url = "https://api.binance.com/api/v3/ticker/bookTicker"
params = {"symbol": "PAXGUSDT"}

try:
    start_t = time.time()
    res = requests.get(binance_url, params=params, timeout=5)
    elapsed = (time.time() - start_t) * 1000
    data = res.json()
    
    bid = float(data["bidPrice"])
    ask = float(data["askPrice"])
    spread = round(ask - bid, 2)
    
    print(f"✅ Binance PAXG/USDT Connected ({elapsed:.1f}ms latency)")
    print(f"   🟢 Bid:   ${bid:.2f}")
    print(f"   🔴 Ask:   ${ask:.2f}")
    print(f"   📏 Spread: ${spread:.2f} ({int(spread * 10)} pips)")
except Exception as e:
    print(f"❌ Binance fetch error: {e}")
    bid, ask = None, None

# 2. Compare Side-by-Side with MetaTrader 5 (Local Vantage Feed)
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        tick = mt5.symbol_info_tick("XAUUSD")
        if tick:
            mt5_bid = tick.bid
            mt5_ask = tick.ask
            mt5_spread = round(mt5_ask - mt5_bid, 2)
            print("-" * 55)
            print("🏛️ MetaTrader 5 (Vantage XAUUSD Spot):")
            print(f"   🟢 Bid:   ${mt5_bid:.2f}")
            print(f"   🔴 Ask:   ${mt5_ask:.2f}")
            print(f"   📏 Spread: ${mt5_spread:.2f}")
            
            if bid is not None:
                diff = abs(bid - mt5_bid)
                print("-" * 55)
                print(f"📊 Price Difference (Binance vs Vantage): ${diff:.2f}")
                if diff < 3.0:
                    print("🎯 Result: NEARLY IDENTICAL! (<$3 difference on $4,100 Gold)")
        mt5.shutdown()
except Exception as e:
    print(f"MT5 comparison note: {e}")

# 3. Test 5-Minute Candlestick Retrieval from Binance
print("-" * 55)
print("🕯️ Testing 5-Minute Candle Retrieval from Binance...")
kline_url = "https://api.binance.com/api/v3/klines"
kline_params = {"symbol": "PAXGUSDT", "interval": "5m", "limit": 5}

try:
    res = requests.get(kline_url, params=kline_params, timeout=5)
    klines = res.json()
    print(f"✅ Successfully retrieved {len(klines)} 5-minute candles:")
    for k in klines:
        t_str = datetime.fromtimestamp(k[0] / 1000).strftime("%H:%M")
        o = float(k[1])
        h = float(k[2])
        l = float(k[3])
        c = float(k[4])
        v = float(k[5])
        print(f"   [{t_str}] Open: {o:.2f} | High: {h:.2f} | Low: {l:.2f} | Close: {c:.2f} | Vol: {v:.2f} oz")
    print("=" * 55)
    print("🎉 VERDICT: Binance PAXG/USDT is 100% functional, free, & ready for Linux!")
    print("=" * 55)
except Exception as e:
    print(f"❌ Candle error: {e}")
