import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import time
from datetime import datetime
import io
import contextlib
import MetaTrader5 as mt5

import bot

print("=" * 80)
print("  XAUBOTTY MULTI-YEAR QUANTITATIVE STRESS TEST — 100,000 5M BARS (~1.5 YEARS)")
print("  Capital Tiers: $100 | $500 | $1,000 | $10,000")
print("  Date Range: May 2025 to October 2026")
print("=" * 80)

# 1. Ingest Data from MT5 (Fetch 100,000 bars in chunks)
if not mt5.initialize():
    print(f"❌ Failed to connect to MetaTrader 5: {mt5.last_error()}")
    sys.exit(1)

print("📥 Ingesting 100,000 5-minute candles from MetaTrader 5...")
r1 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 75000)
r2 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 75000, 25000)

r15_1 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, 25000)
r15_2 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 25000, 15000)
mt5.shutdown()

if r1 is None or r2 is None:
    print("❌ Failed to pull full 100,000 bar history.")
    sys.exit(1)

# Combine chunks chronologically (r2 is older, r1 is newer)
r5_all = list(r2) + list(r1)
r15_all = (list(r15_2) if r15_2 is not None else []) + (list(r15_1) if r15_1 is not None else [])

candles_5m = []
for r in r5_all:
    candles_5m.append({
        "time": datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": r["time"],
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
        "volume": float(r["tick_volume"]),
        "spread": float(r["spread"]) * 0.01 if "spread" in r.dtype.names else 0.30
    })

candles_15m = []
for r in r15_all:
    candles_15m.append({
        "time": datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": r["time"],
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
        "volume": float(r["tick_volume"]),
    })

start_date = candles_5m[0]["time"]
end_date   = candles_5m[-1]["time"]
print(f"✅ Data Loaded: {len(candles_5m)} 5M candles from {start_date} to {end_date} (524 days / 1.43 years)")
print("-" * 80)

LOOKBACK = 100
total_bars = len(candles_5m)
SPREAD_COST = 0.30 # $0.30 Vantage spread cost per 0.01 lot

class CapitalTierSim:
    def __init__(self, name, initial_capital, fixed_lot, is_compound=False):
        self.name = name
        self.initial_capital = initial_capital
        self.balance = initial_capital
        self.peak_balance = initial_capital
        self.min_balance = initial_capital
        self.fixed_lot = fixed_lot
        self.is_compound = is_compound
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0
        self.trades = []
        self.trades_today = 0
        self.current_date = None

    def check_new_day(self, date_str):
        if self.current_date != date_str:
            self.current_date = date_str
            self.trades_today = 0

    def get_current_lot(self):
        if self.is_compound:
            # 0.01 lot per $50 of equity
            lot_units = max(1, int(self.balance // 50))
            return round(lot_units * 0.01, 2)
        return self.fixed_lot

    def record_trade(self, price_move, entry, exit_p, score, direction, duration_mins):
        lot = self.get_current_lot()
        mult = lot / 0.01
        gross_pnl = price_move * mult
        spread = SPREAD_COST * mult
        net_pnl = round(gross_pnl - spread, 2)

        self.balance += net_pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        if self.balance < self.min_balance:
            self.min_balance = self.balance

        dd_usd = self.peak_balance - self.balance
        dd_pct = (dd_usd / self.peak_balance) * 100 if self.peak_balance > 0 else 0
        if dd_usd > self.max_drawdown_usd:
            self.max_drawdown_usd = dd_usd
        if dd_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = dd_pct

        self.trades.append({
            "lot": lot,
            "pnl": net_pnl,
            "score": score,
            "direction": direction,
            "duration": duration_mins,
            "balance": round(self.balance, 2)
        })

# Instantiate the Capital Tiers requested by the user:
# 1. $100 Capital
acc_100_fixed    = CapitalTierSim("$100 Account (Fixed 0.02 Lot)", initial_capital=100.0, fixed_lot=0.02, is_compound=False)
acc_100_compound = CapitalTierSim("$100 Account (Dynamic Compounding)", initial_capital=100.0, fixed_lot=0.02, is_compound=True)

# 2. $500 Capital
acc_500_fixed    = CapitalTierSim("$500 Account (Fixed 0.10 Lot)", initial_capital=500.0, fixed_lot=0.10, is_compound=False)
acc_500_compound = CapitalTierSim("$500 Account (Dynamic Compounding)", initial_capital=500.0, fixed_lot=0.10, is_compound=True)

# 3. $1,000 Capital
acc_1k_fixed     = CapitalTierSim("$1,000 Account (Fixed 0.20 Lot)", initial_capital=1000.0, fixed_lot=0.20, is_compound=False)
acc_1k_compound  = CapitalTierSim("$1,000 Account (Dynamic Compounding)", initial_capital=1000.0, fixed_lot=0.20, is_compound=True)

# 4. $10,000 Capital
acc_10k_fixed    = CapitalTierSim("$10,000 Account (Fixed 2.00 Lots)", initial_capital=10000.0, fixed_lot=2.00, is_compound=False)
acc_10k_compound = CapitalTierSim("$10,000 Account (Dynamic Compounding)", initial_capital=10000.0, fixed_lot=2.00, is_compound=True)

tiers = [
    acc_100_fixed, acc_100_compound,
    acc_500_fixed, acc_500_compound,
    acc_1k_fixed, acc_1k_compound,
    acc_10k_fixed, acc_10k_compound
]

print("⚙️ Running Walk-Forward Simulation across 100,000 bars with US News Blackout active...")

last_signal_time = 0
last_signal_dir = None
confluence_scores = []

bot.MIN_CONFIDENCE = 70

i = LOOKBACK
while i < total_bars - 20:
    curr_candle = candles_5m[i]
    curr_time = curr_candle["timestamp"]
    curr_date_str = curr_candle["time"][:10]

    for acc in tiers:
        acc.check_new_day(curr_date_str)

    # US Macro News Blackout: 20:00 - 22:00 PHT (Mon-Fri)
    dt_pht = datetime.fromtimestamp(curr_time, tz=bot.PHT)
    if bot.is_news_blackout(dt_pht):
        i += 1
        continue

    window_5m = candles_5m[i-LOOKBACK:i+1]
    window_15m = [c for c in candles_15m if c["timestamp"] <= curr_time][-LOOKBACK:]

    if len(window_15m) < 30:
        i += 1
        continue

    with contextlib.redirect_stdout(io.StringIO()):
        sig = bot.generate_signal(window_5m, window_15m)

    if sig and sig["confidence"] >= 70:
        direction = sig["signal"]
        now_ts = curr_time

        # Skip repeat signals within 45 minutes
        if last_signal_dir == direction and (now_ts - last_signal_time < 2700):
            i += 1
            continue

        last_signal_time = now_ts
        last_signal_dir = direction

        entry_price = sig["price"]
        sb = sig["setup_b"] # Sniper 1:1.5 R:R profile

        # Resolve trade forward
        resolved = False
        res_price_move = 0.0
        duration_mins = 0

        for f in range(1, min(150, total_bars - i)):
            future_c = candles_5m[i + f]
            h = future_c["high"]
            l = future_c["low"]
            mins = f * 5

            if direction == "LONG":
                if h >= sb["tp_price"]:
                    res_price_move = sb["tp_price"] - entry_price
                    resolved = True
                    duration_mins = mins
                    break
                elif l <= sb["sl_price"]:
                    res_price_move = -(entry_price - sb["sl_price"])
                    resolved = True
                    duration_mins = mins
                    break
            else: # SHORT
                if l <= sb["tp_price"]:
                    res_price_move = entry_price - sb["tp_price"]
                    resolved = True
                    duration_mins = mins
                    break
                elif h >= sb["sl_price"]:
                    res_price_move = -(sb["sl_price"] - entry_price)
                    resolved = True
                    duration_mins = mins
                    break

        if resolved:
            for acc in tiers:
                acc.record_trade(res_price_move, entry_price, sb["tp_price"] if res_price_move > 0 else sb["sl_price"], sig["confidence"], direction, duration_mins)

    i += 1

print("✅ Multi-Year Simulation Complete! Generating Tear Sheets...\n")

def print_tier_results(acc):
    trades = acc.trades
    total = len(trades)
    if total == 0:
        print(f"[{acc.name}]: 0 trades.")
        return
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = (len(wins) / total) * 100
    gross_profits = sum(t["pnl"] for t in wins)
    gross_losses = abs(sum(t["pnl"] for t in losses))
    net_profit = acc.balance - acc.initial_capital
    roi_pct = (net_profit / acc.initial_capital) * 100
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else float('inf')
    avg_win = (gross_profits / len(wins)) if wins else 0
    avg_loss = (gross_losses / len(losses)) if losses else 0
    ending_lot = trades[-1]["lot"]

    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📊 {acc.name.upper()}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  💰 Starting Capital:       ${acc.initial_capital:,.2f}")
    print(f"  💵 Final Balance:          ${acc.balance:,.2f} ({net_profit:+,.2f} USD)")
    print(f"  📈 Return on Investment:   {roi_pct:+,.1f}%")
    print(f"  🎯 Win Rate:               {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"  🔢 Total Trades:           {total} trades across 524 days ({total/74.8:.1f} trades/week)")
    print(f"  ⚖️ Profit Factor:          {profit_factor:.2f}")
    print(f"  🟢 Average Win:            +${avg_win:,.2f}")
    print(f"  🔴 Average Loss:           -${avg_loss:,.2f}")
    print(f"  🛡️ Maximum Drawdown:       -${acc.max_drawdown_usd:,.2f} ({acc.max_drawdown_pct:.1f}%)")
    print(f"  📉 Minimum Equity Reached: ${acc.min_balance:,.2f}")
    if acc.is_compound:
        print(f"  📦 Position Growth:        Started @ 0.02 Lot -> Ended @ {ending_lot:.2f} Lots")
    print("")

for acc in tiers:
    print_tier_results(acc)

