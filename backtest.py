import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import time
from datetime import datetime
import pytz
import MetaTrader5 as mt5

# Import strategy functions from bot.py
import bot

print("=" * 70)
print("  XAUBOTTY QUANTITATIVE BACKTESTING ENGINE")
print("  Symbol: XAU/USD (Vantage) | 10,000 Consecutive 5M Candles (~1.7 Months)")
print("=" * 70)

# 1. Ingest Data from MT5
if not mt5.initialize():
    print(f"❌ Failed to connect to MetaTrader 5: {mt5.last_error()}")
    sys.exit(1)

CANDLE_COUNT = 10000
print(f"📥 Pulling {CANDLE_COUNT} 5-minute and 15-minute candles from MetaTrader 5...")

r5 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, CANDLE_COUNT)
r15 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, CANDLE_COUNT // 3 + 100)
mt5.shutdown()

if r5 is None or len(r5) < 500:
    print("❌ Failed to retrieve sufficient 5M candle data.")
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
print(f"✅ Data Loaded: {len(candles_5m)} 5M candles from {start_date} to {end_date}")
print("-" * 70)

import io
import contextlib

# Simulation State
INITIAL_BALANCE = 40.0
FIXED_LOT = 0.01
SPREAD_COST = 0.30 # $0.30 Vantage spread cost per 0.01 lot

class AccountSim:
    def __init__(self, name, max_daily=3, is_unlimited=False):
        self.name = name
        self.max_daily = max_daily
        self.is_unlimited = is_unlimited
        self.balance = INITIAL_BALANCE
        self.peak_balance = INITIAL_BALANCE
        self.max_drawdown_usd = 0.0
        self.max_drawdown_pct = 0.0
        self.trades = []
        self.trades_today = 0
        self.current_date = None

    def check_new_day(self, date_str):
        if self.current_date != date_str:
            self.current_date = date_str
            self.trades_today = 0

    def can_trade(self):
        if self.is_unlimited:
            return True
        return self.trades_today < self.max_daily

    def record_trade(self, pnl, score, direction, entry, exit_p, duration_mins):
        self.trades_today += 1
        net_pnl = pnl - SPREAD_COST
        self.balance += net_pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd_usd = self.peak_balance - self.balance
        dd_pct = (dd_usd / self.peak_balance) * 100 if self.peak_balance > 0 else 0
        if dd_usd > self.max_drawdown_usd:
            self.max_drawdown_usd = dd_usd
        if dd_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = dd_pct

        self.trades.append({
            "pnl": round(net_pnl, 2),
            "gross_pnl": round(pnl, 2),
            "score": score,
            "direction": direction,
            "entry": entry,
            "exit": exit_p,
            "duration": duration_mins,
            "balance_after": round(self.balance, 2)
        })

acc_a = AccountSim("Setup A (Institutional 1:2 R:R - Max 3/day)", max_daily=3)
acc_b = AccountSim("Setup B (Quick Scalp $10 SL / $3 TP - Max 3/day)", max_daily=3)
acc_c = AccountSim("Setup C (Unlimited SMC 1:2 - No Daily Limit)", is_unlimited=True)

# 2. Walk-Forward Simulation Loop
LOOKBACK = 100
total_bars = len(candles_5m)
print(f"⚙️ Running Walk-Forward Simulation across {total_bars - LOOKBACK} bars...")

last_signal_time = 0
last_signal_dir = None
confluence_scores = []

i = LOOKBACK
while i < total_bars - 20:
    curr_candle = candles_5m[i]
    curr_time = curr_candle["timestamp"]
    curr_date_str = curr_candle["time"][:10]

    acc_a.check_new_day(curr_date_str)
    acc_b.check_new_day(curr_date_str)
    acc_c.check_new_day(curr_date_str)

    # Window of past 100 5M candles
    window_5m = candles_5m[i-LOOKBACK:i+1]

    # Find matching 15M candles strictly before current time
    window_15m = [c for c in candles_15m if c["timestamp"] <= curr_time][-LOOKBACK:]

    if len(window_15m) < 30:
        i += 1
        continue

    with contextlib.redirect_stdout(io.StringIO()):
        sig = bot.generate_signal(window_5m, window_15m)

    if sig:
        confluence_scores.append(sig["confidence"])
        direction = sig["signal"]
        now_ts = curr_time

        # Skip repeat signals within 45 minutes
        if last_signal_dir == direction and (now_ts - last_signal_time < 2700):
            i += 1
            continue

        last_signal_time = now_ts
        last_signal_dir = direction

        entry_price = sig["price"]
        sa = sig["setup_a"]
        sb = sig["setup_b"]

        can_a = acc_a.can_trade()
        can_b = acc_b.can_trade()
        can_c = acc_c.can_trade()

        # Step forward bar-by-bar to resolve outcomes
        a_done = not can_a
        b_done = not can_b
        c_done = not can_c

        pnl_a, pnl_b, pnl_c = 0.0, 0.0, 0.0
        dur_a, dur_b, dur_c = 0, 0, 0

        # Scan up to 150 bars forward (~12.5 hours max hold)
        for f in range(1, min(150, total_bars - i)):
            future_c = candles_5m[i + f]
            h = future_c["high"]
            l = future_c["low"]
            mins = f * 5

            # Setup A & C (1:2 Institutional)
            if not a_done or not c_done:
                if direction == "LONG":
                    if h >= sa["tp_price"]:
                        res_pnl = sa["tp_price"] - entry_price
                        if not a_done: pnl_a = res_pnl; dur_a = mins; a_done = True
                        if not c_done: pnl_c = res_pnl; dur_c = mins; c_done = True
                    elif l <= sa["sl_price"]:
                        res_pnl = -(entry_price - sa["sl_price"])
                        if not a_done: pnl_a = res_pnl; dur_a = mins; a_done = True
                        if not c_done: pnl_c = res_pnl; dur_c = mins; c_done = True
                else: # SHORT
                    if l <= sa["tp_price"]:
                        res_pnl = entry_price - sa["tp_price"]
                        if not a_done: pnl_a = res_pnl; dur_a = mins; a_done = True
                        if not c_done: pnl_c = res_pnl; dur_c = mins; c_done = True
                    elif h >= sa["sl_price"]:
                        res_pnl = -(sa["sl_price"] - entry_price)
                        if not a_done: pnl_a = res_pnl; dur_a = mins; a_done = True
                        if not c_done: pnl_c = res_pnl; dur_c = mins; c_done = True

            # Setup B ($10 SL / $3 TP Quick Scalp)
            if not b_done:
                if direction == "LONG":
                    if h >= sb["tp_price"]:
                        pnl_b = 3.00; dur_b = mins; b_done = True
                    elif l <= sb["sl_price"]:
                        pnl_b = -10.00; dur_b = mins; b_done = True
                else: # SHORT
                    if l <= sb["tp_price"]:
                        pnl_b = 3.00; dur_b = mins; b_done = True
                    elif h >= sb["sl_price"]:
                        pnl_b = -10.00; dur_b = mins; b_done = True

            if a_done and b_done and c_done:
                break

        # Record completed trades
        if can_a and a_done:
            acc_a.record_trade(pnl_a, sig["confidence"], direction, entry_price, sa["tp_price"] if pnl_a > 0 else sa["sl_price"], dur_a)
        if can_b and b_done:
            acc_b.record_trade(pnl_b, sig["confidence"], direction, entry_price, sb["tp_price"] if pnl_b > 0 else sb["sl_price"], dur_b)
        if can_c and c_done:
            acc_c.record_trade(pnl_c, sig["confidence"], direction, entry_price, sa["tp_price"] if pnl_c > 0 else sa["sl_price"], dur_c)

    i += 1

# 3. Print Institutional Tear Sheet
def print_stats(acc):
    trades = acc.trades
    total = len(trades)
    if total == 0:
        print(f"\n{acc.name}: No trades generated.")
        return
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    win_rate = (len(wins) / total) * 100
    gross_profits = sum(t["pnl"] for t in wins)
    gross_losses = abs(sum(t["pnl"] for t in losses))
    net_profit = acc.balance - INITIAL_BALANCE
    roi_pct = (net_profit / INITIAL_BALANCE) * 100
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else float('inf')
    avg_win = (gross_profits / len(wins)) if wins else 0
    avg_loss = (gross_losses / len(losses)) if losses else 0
    avg_duration = sum(t["duration"] for t in trades) / total

    print(f"\n{'=' * 70}")
    print(f"  📊 PERFORMANCE RESULTS: {acc.name.upper()}")
    print(f"{'=' * 70}")
    print(f"  💰 Initial Capital:        ${INITIAL_BALANCE:.2f}")
    print(f"  💵 Final Balance:          ${acc.balance:.2f} ({net_profit:+.2f} USD)")
    print(f"  📈 Return on Investment:   {roi_pct:+.1f}%")
    print(f"  🎯 Win Rate:               {win_rate:.1f}% ({len(wins)} Wins / {len(losses)} Losses)")
    print(f"  🔢 Total Trades Executed:  {total} trades")
    print(f"  ⚖️ Profit Factor:          {profit_factor:.2f}")
    print(f"  🟢 Average Win:            +${avg_win:.2f}")
    print(f"  🔴 Average Loss:           -${avg_loss:.2f}")
    print(f"  🛡️ Maximum Drawdown:       -${acc.max_drawdown_usd:.2f} ({acc.max_drawdown_pct:.1f}%)")
    print(f"  ⏱️ Average Trade Duration:  {int(avg_duration)} minutes ({avg_duration/60:.1f} hrs)")

print_stats(acc_a)
print_stats(acc_b)
print_stats(acc_c)

# 4. Confluence Score Analysis: Did higher scores win more?
print(f"\n{'=' * 70}")
print("  🎯 CONFLUENCE SCORE ACCURACY BREAKDOWN (Setup C Trades)")
print(f"{'=' * 70}")

for min_s, max_s in [(65, 70), (70, 79), (80, 100)]:
    bucket = [t for t in acc_c.trades if min_s <= t["score"] <= max_s]
    if bucket:
        b_wins = [t for t in bucket if t["pnl"] > 0]
        b_wr = (len(b_wins) / len(bucket)) * 100
        b_pnl = sum(t["pnl"] for t in bucket)
        print(f"  Score {min_s}% - {max_s}%: {len(bucket):3d} trades | Win Rate: {b_wr:.1f}% | Net P/L: ${b_pnl:+.2f}")
print("=" * 70)
