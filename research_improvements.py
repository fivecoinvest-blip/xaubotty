import bot
import MetaTrader5 as mt5
from datetime import datetime
import io, contextlib

if not mt5.initialize():
    print("MT5 Init Failed")
    exit()

r5 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 35000)
r15 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, 35000 // 3 + 200)
mt5.shutdown()

candles_5m = [{"time": datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S"), "timestamp": r["time"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"])} for r in r5]
candles_15m = [{"time": datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S"), "timestamp": r["time"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]), "close": float(r["close"])} for r in r15]

LOOKBACK = 100
total_bars = len(candles_5m)
trades = []

i = LOOKBACK
last_t = 0
last_d = None

bot.MIN_CONFIDENCE = 70

while i < total_bars - 20:
    c = candles_5m[i]
    ts = c["timestamp"]
    w5 = candles_5m[i-LOOKBACK:i+1]
    w15 = [x for x in candles_15m if x["timestamp"] <= ts][-LOOKBACK:]
    if len(w15) < 30:
        i += 1
        continue
    
    with contextlib.redirect_stdout(io.StringIO()):
        sig = bot.generate_signal(w5, w15)
    
    if sig and sig["confidence"] >= 70:
        d = sig["signal"]
        if last_d == d and (ts - last_t < 2700):
            i += 1
            continue
        last_t = ts
        last_d = d
        
        sb = sig["setup_b"]
        ep = sig["price"]
        h_pht = (datetime.fromtimestamp(ts).hour + 8) % 24
        
        sl_dist = sb["sl_dollars"]
        tp_dist = sb["tp_dollars"]
        
        max_favorable = 0.0
        outcome = None
        dur = 0
        pnl = 0.0
        
        # Test Break-Even logic: if profit reached >= +1.0R (sl_dist), move SL to entry (+0.10)
        be_outcome = None
        be_pnl = 0.0
        hit_be_trigger = False
        
        for f in range(1, min(150, total_bars - i)):
            fc = candles_5m[i+f]
            h = fc["high"]
            l = fc["low"]
            dur = f * 5
            
            if d == "LONG":
                fav = h - ep
                if fav > max_favorable:
                    max_favorable = fav
                if fav >= sl_dist:
                    hit_be_trigger = True
                
                # Standard
                if outcome is None:
                    if h >= sb["tp_price"]:
                        outcome = "WIN"
                        pnl = sb["tp_dollars"]
                    elif l <= sb["sl_price"]:
                        outcome = "LOSS"
                        pnl = -sb["sl_dollars"]
                
                # Breakeven protected
                if be_outcome is None:
                    if h >= sb["tp_price"]:
                        be_outcome = "WIN"
                        be_pnl = sb["tp_dollars"]
                    elif hit_be_trigger and l <= ep + 0.10:
                        be_outcome = "BE"
                        be_pnl = 0.0
                    elif l <= sb["sl_price"]:
                        be_outcome = "LOSS"
                        be_pnl = -sb["sl_dollars"]
            else: # SHORT
                fav = ep - l
                if fav > max_favorable:
                    max_favorable = fav
                if fav >= sl_dist:
                    hit_be_trigger = True
                
                if outcome is None:
                    if l <= sb["tp_price"]:
                        outcome = "WIN"
                        pnl = sb["tp_dollars"]
                    elif h >= sb["sl_price"]:
                        outcome = "LOSS"
                        pnl = -sb["sl_dollars"]
                
                if be_outcome is None:
                    if l <= sb["tp_price"]:
                        be_outcome = "WIN"
                        be_pnl = sb["tp_dollars"]
                    elif hit_be_trigger and h >= ep - 0.10:
                        be_outcome = "BE"
                        be_pnl = 0.0
                    elif h >= sb["sl_price"]:
                        be_outcome = "LOSS"
                        be_pnl = -sb["sl_dollars"]
            
            if outcome is not None and be_outcome is not None:
                break
            
        if outcome:
            trades.append({
                "time": c["time"],
                "hour_pht": h_pht,
                "outcome": outcome,
                "pnl": pnl - 0.30,
                "be_outcome": be_outcome,
                "be_pnl": be_pnl - 0.30 if be_outcome != "BE" else 0.0,
                "max_fav": max_favorable,
                "sl_dist": sl_dist,
                "tp_dist": tp_dist,
                "dur": dur
            })
    i += 1

print(f"Total 70%+ trades across 6 months: {len(trades)}")
std_wins = [t for t in trades if t["outcome"] == "WIN"]
std_pnl = sum(t["pnl"] for t in trades)
print(f"STANDARD SETUP B (Fixed SL/TP): {len(std_wins)} Wins / {len(trades)-len(std_wins)} Losses | Total Net PnL: ${std_pnl:.2f}")

be_wins = [t for t in trades if t["be_outcome"] == "WIN"]
be_bes = [t for t in trades if t["be_outcome"] == "BE"]
be_losses = [t for t in trades if t["be_outcome"] == "LOSS"]
be_pnl_tot = sum(t["be_pnl"] for t in trades)
print(f"IMPROVEMENT 1 [BREAK-EVEN AT +1.0R]: {len(be_wins)} Wins / {len(be_bes)} Scratch(BE) / {len(be_losses)} Losses | Total Net PnL: ${be_pnl_tot:.2f}")

# Session breakdown
asian = [t for t in trades if 0 <= t["hour_pht"] < 15]
london_ny = [t for t in trades if 15 <= t["hour_pht"] <= 23]
a_wins = [t for t in asian if t["outcome"] == "WIN"]
l_wins = [t for t in london_ny if t["outcome"] == "WIN"]

print("\n--- SESSION PERFORMANCE BREAKDOWN (70%+ Confluence) ---")
print(f"Asian Session (00:00 - 15:00 PHT): {len(asian)} trades | {len(a_wins)}W - {len(asian)-len(a_wins)}L ({len(a_wins)/len(asian)*100:.1f}%) | Net PnL: ${sum(t['pnl'] for t in asian):.2f}")
print(f"London & NY Peak (15:00 - 24:00 PHT): {len(london_ny)} trades | {len(l_wins)}W - {len(london_ny)-len(l_wins)}L ({len(l_wins)/len(london_ny)*100:.1f}%) | Net PnL: ${sum(t['pnl'] for t in london_ny):.2f}")

# Trailing Stop test
print("\n--- LOSS ROOT-CAUSE ANALYSIS ---")
losses = [t for t in trades if t["outcome"] == "LOSS"]
got_green = [t for t in losses if t["max_fav"] >= 2.0]
got_half = [t for t in losses if t["max_fav"] >= t["tp_dist"] * 0.5]
print(f"Total Losses: {len(losses)}")
print(f"Losses that were up at least +$2.00 before reversing: {len(got_green)} ({len(got_green)/len(losses)*100:.1f}%)")
print(f"Losses that reached at least 50% of Take Profit before reversing: {len(got_half)} ({len(got_half)/len(losses)*100:.1f}%)")

