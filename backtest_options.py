import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import time
from datetime import datetime
import pytz
import math
import io
import contextlib
import MetaTrader5 as mt5

import bot
# Allow generating signals down to 65% so individual account filters can test different thresholds
bot.MIN_CONFIDENCE = 65

print("=" * 75)
print("  XAUBOTTY STRATEGIC OPTIMIZATION SUITE — 3-OPTION QUANTITATIVE LAB")
print("  Dataset: 35,000 Vantage MT5 5M Candles (~6.2 Months)")
print("=" * 75)

# 1. Ingest Data from MT5
if not mt5.initialize():
    print(f"❌ Failed to connect to MetaTrader 5: {mt5.last_error()}")
    sys.exit(1)

CANDLE_COUNT = 35000
print(f"📥 Pulling {CANDLE_COUNT} 5M & 15M candles from MetaTrader 5...")
r5 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, CANDLE_COUNT)
r15 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, CANDLE_COUNT // 3 + 200)
mt5.shutdown()

if r5 is None or len(r5) < 500:
    print("❌ Failed to retrieve candle data.")
    sys.exit(1)

candles_5m = []
for r in r5:
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
for r in r15:
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
print(f"✅ Data Ready: 35,000 candles from {start_date} to {end_date}")
print("-" * 75)

LOOKBACK = 100
total_bars = len(candles_5m)

# Helper Account Simulation Class
class SimAccount:
    def __init__(self, name, initial_capital, sizing_mode="fixed", fixed_lot=0.01, min_conf=70, session_filter=False):
        self.name = name
        self.initial_capital = initial_capital
        self.balance = initial_capital
        self.peak_balance = initial_capital
        self.min_balance = initial_capital
        self.sizing_mode = sizing_mode # "fixed" or "compound"
        self.fixed_lot = fixed_lot
        self.min_conf = min_conf
        self.session_filter = session_filter # True = only London & NY (15:00 - 24:00 PHT)
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0
        self.trades = []
        self.trades_today = 0
        self.current_date = None

    def check_new_day(self, date_str):
        if self.current_date != date_str:
            self.current_date = date_str
            self.trades_today = 0

    def get_lot_size(self):
        if self.sizing_mode == "compound":
            # Compound: 0.01 lot per $40 of balance
            lot_units = max(1, int(self.balance // 40))
            return round(lot_units * 0.01, 2)
        return self.fixed_lot

    def record_trade(self, price_move, entry, exit_p, score, direction, duration_mins):
        lot = self.get_lot_size()
        # Price move in dollars * 100 * lot - spread cost
        # Gold 0.01 lot = $1/point. $0.30 spread cost per 0.01 lot
        gross_pnl = price_move * (lot / 0.01)
        spread_cost = 0.30 * (lot / 0.01)
        net_pnl = round(gross_pnl - spread_cost, 2)

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

# Instantiate Accounts across the 3 Options:

# Baseline: Fixed 0.01 on $40 (Setup B Sniper 1:1.5)
acc_baseline = SimAccount("Baseline ($40 Capital, Fixed 0.01 Lot)", initial_capital=40.0, sizing_mode="fixed", fixed_lot=0.01, min_conf=70)

# Option 1: Dynamic Compounding Model on $40
acc_compound = SimAccount("Option 1: Dynamic Compounding (0.01 lot per $40 equity)", initial_capital=40.0, sizing_mode="compound", min_conf=70)

# Option 2: Session Momentum / Higher Frequency Mode (London & NY overlap 15:00 - 24:00 PHT with 65% conf)
acc_momentum = SimAccount("Option 2: London/NY Session Momentum (65% Conf during Peak Hours)", initial_capital=40.0, sizing_mode="fixed", fixed_lot=0.01, min_conf=65, session_filter=True)

# Option 3: Capital Scaling Matrix
acc_cap_100 = SimAccount("Option 3A: $100 Capital (0.02 Lot)", initial_capital=100.0, sizing_mode="fixed", fixed_lot=0.02, min_conf=70)
acc_cap_200 = SimAccount("Option 3B: $200 Capital (0.05 Lot)", initial_capital=200.0, sizing_mode="fixed", fixed_lot=0.05, min_conf=70)
acc_cap_500 = SimAccount("Option 3C: $500 Capital (0.10 Lot)", initial_capital=500.0, sizing_mode="fixed", fixed_lot=0.10, min_conf=70)

all_accounts = [acc_baseline, acc_compound, acc_momentum, acc_cap_100, acc_cap_200, acc_cap_500]

print("⚙️ Simulating Walk-Forward Execution across all 6 models...")

last_signal_time = 0
last_signal_dir = None

i = LOOKBACK
while i < total_bars - 20:
    curr_candle = candles_5m[i]
    curr_time = curr_candle["timestamp"]
    curr_date_str = curr_candle["time"][:10]
    curr_hour_utc = datetime.fromtimestamp(curr_time).hour
    curr_hour_pht = (curr_hour_utc + 8) % 24 # Convert to Philippines Time

    window_5m = candles_5m[i-LOOKBACK:i+1]
    window_15m = [c for c in candles_15m if c["timestamp"] <= curr_time][-LOOKBACK:]

    if len(window_15m) < 30:
        i += 1
        continue

    with contextlib.redirect_stdout(io.StringIO()):
        # Pass 65 as base generation so we can filter individually per account
        sig = bot.generate_signal(window_5m, window_15m)

    if sig:
        direction = sig["signal"]
        score = sig["confidence"]
        now_ts = curr_time

        if last_signal_dir == direction and (now_ts - last_signal_time < 2700):
            i += 1
            continue

        last_signal_time = now_ts
        last_signal_dir = direction

        entry_price = sig["price"]
        sb = sig["setup_b"]

        # Check which accounts can accept this signal
        qualifying_accs = []
        is_peak_session = (15 <= curr_hour_pht <= 23)

        for acc in all_accounts:
            if acc.session_filter:
                if is_peak_session and score >= acc.min_conf:
                    qualifying_accs.append(acc)
            else:
                if score >= acc.min_conf:
                    qualifying_accs.append(acc)

        if not qualifying_accs:
            i += 1
            continue

        # Step forward bar-by-bar to resolve outcome for Setup B (1:1.5 Sniper)
        res_price_move = 0.0
        resolved = False
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
            for acc in qualifying_accs:
                acc.record_trade(res_price_move, entry_price, sb["tp_price"] if res_price_move > 0 else sb["sl_price"], score, direction, duration_mins)

    i += 1

print("✅ Walk-Forward Simulation Complete! Generating Tear Sheets...\n")

def print_account_summary(acc):
    trades = acc.trades
    total = len(trades)
    if total == 0:
        print(f"[{acc.name}]: No trades.")
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

    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📊 {acc.name.upper()}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  💰 Initial Capital:        ${acc.initial_capital:.2f}")
    print(f"  💵 Final Balance:          ${acc.balance:.2f} ({net_profit:+.2f} USD)")
    print(f"  📈 Return on Investment:   {roi_pct:+.1f}%")
    print(f"  🎯 Win Rate:               {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"  🔢 Total Trades:           {total} trades ({total / 26.5:.1f} trades/week)")
    print(f"  ⚖️ Profit Factor:          {profit_factor:.2f}")
    print(f"  🟢 Average Win:            +${avg_win:.2f}")
    print(f"  🔴 Average Loss:           -${avg_loss:.2f}")
    print(f"  🛡️ Maximum Drawdown:       -${acc.max_drawdown_usd:.2f} ({acc.max_drawdown_pct:.1f}%)")
    print(f"  📉 Minimum Equity Reached: ${acc.min_balance:.2f}")
    print("")

for acc in all_accounts:
    print_account_summary(acc)
