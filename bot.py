import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import json
import requests
import time
from datetime import datetime, date
import pytz

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ============================================
# CONFIGURATION — CALIBRATED FOR PHILIPPINES & $40 ACCOUNT
# ============================================
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY")

SYMBOL       = "XAU/USD"
CAPITAL      = 40.0             # Exact user account fund
LEVERAGE     = 500              # Vantage 1:500 high leverage
FIXED_LOT    = 0.01             # Micro lot (1 oz gold)

MAX_TRADES_PER_DAY = 3
MIN_CONFIDENCE     = 65
MIN_SL_DOLLARS     = 2.50
MAX_SL_DOLLARS     = 10.0           # Max stop loss cap ($10 on $40 fund)
MIN_FVG_SIZE       = 1.50           # Minimum FVG gap in dollars
EQUAL_LEVEL_TOL    = 0.30           # Dollars — swing high/low liquidity proximity
PHT                = pytz.timezone('Asia/Manila')  # Philippines Local Time (UTC+8)

PAPER_MODE = True

# Gold contract on Vantage: 1.00 lot = 100oz -> $1 move = $100/lot -> $1 move = $1.00 per 0.01 lot
USD_PER_DOLLAR_MOVE_PER_001_LOT = 1.0

# ============================================
# STATE & PAPER TRADE LEDGER
# ============================================
trades_today          = 0
last_trade_date       = None
last_signal_direction = None
last_signal_time      = 0
active_paper_trades   = []
PAPER_HISTORY_FILE    = os.path.join(os.path.dirname(__file__), "paper_trades_history.json")


def load_paper_history():
    if os.path.exists(PAPER_HISTORY_FILE):
        try:
            with open(PAPER_HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_paper_history(history):
    try:
        with open(PAPER_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"Error saving paper history: {e}")

# ============================================
# TELEGRAM
# ============================================
def send_telegram(message):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        print(f"Telegram status: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ============================================
# FETCH CANDLES
# ============================================
def get_candles(interval="5min", outputsize=100):
    url    = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL, "interval": interval,
        "outputsize": outputsize, "apikey": TWELVEDATA_API_KEY, "format": "JSON"
    }
    try:
        r    = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"API Error: {data}")
            return None
        candles = []
        for c in reversed(data["values"]):
            candles.append({
                "time": c["datetime"], "open": float(c["open"]),
                "high": float(c["high"]), "low": float(c["low"]),
                "close": float(c["close"])
            })
        return candles
    except Exception as e:
        print(f"Candle fetch error: {e}")
        return None

# ============================================
# SWING HIGH / LOW DETECTION
# ============================================
def find_swing_highs(candles, lookback=3):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        if all(candles[i]["high"] >= candles[i-j]["high"] and
               candles[i]["high"] >= candles[i+j]["high"]
               for j in range(1, lookback+1)):
            swings.append((i, candles[i]["high"]))
    return swings

def find_swing_lows(candles, lookback=3):
    swings = []
    for i in range(lookback, len(candles) - lookback):
        if all(candles[i]["low"] <= candles[i-j]["low"] and
               candles[i]["low"] <= candles[i+j]["low"]
               for j in range(1, lookback+1)):
            swings.append((i, candles[i]["low"]))
    return swings

# ============================================
# MARKET STRUCTURE — BOS & CHoCH
# ============================================
def analyze_structure_bos(candles):
    if len(candles) < 20:
        return "NEUTRAL", None, False

    swing_highs = find_swing_highs(candles[-60:], lookback=3)
    swing_lows  = find_swing_lows(candles[-60:],  lookback=3)

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL", None, False

    curr_price = candles[-1]["close"]
    last_sh    = swing_highs[-1][1]
    last_sl    = swing_lows[-1][1]
    prev_sh    = swing_highs[-2][1]
    prev_sl    = swing_lows[-2][1]

    bos_type = None
    choch    = False
    bias     = "NEUTRAL"

    if curr_price > last_sh:
        bos_type = "BOS"
        bias     = "BULLISH"
        if prev_sl > last_sl:
            choch = True
            bias  = "BULLISH"
    elif curr_price < last_sl:
        bos_type = "BOS"
        bias     = "BEARISH"
        if prev_sh < last_sh:
            choch = True
            bias  = "BEARISH"
    else:
        if last_sh > prev_sh and last_sl > prev_sl:
            bias = "BULLISH"
        elif last_sh < prev_sh and last_sl < prev_sl:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

    return bias, bos_type, choch

# ============================================
# ORDER BLOCKS
# ============================================
def detect_order_block(candles, bias):
    if len(candles) < 15:
        return None, None, False

    recent = candles[-12:]
    curr_price = candles[-1]["close"]

    if bias == "BULLISH":
        for i in range(len(recent) - 3, 0, -1):
            c = recent[i]
            if c["close"] < c["open"]:
                next_c = recent[i+1]
                if next_c["close"] > next_c["open"]:
                    impulse = next_c["close"] - next_c["open"]
                    ob_body = c["open"] - c["close"]
                    if impulse >= ob_body * 1.5:
                        ob_high = c["high"]
                        ob_low  = c["low"]
                        in_ob   = ob_low <= curr_price <= ob_high
                        return ob_high, ob_low, in_ob

    elif bias == "BEARISH":
        for i in range(len(recent) - 3, 0, -1):
            c = recent[i]
            if c["close"] > c["open"]:
                next_c = recent[i+1]
                if next_c["close"] < next_c["open"]:
                    impulse = next_c["open"] - next_c["close"]
                    ob_body = c["close"] - c["open"]
                    if impulse >= ob_body * 1.5:
                        ob_high = c["high"]
                        ob_low  = c["low"]
                        in_ob   = ob_low <= curr_price <= ob_high
                        return ob_high, ob_low, in_ob

    return None, None, False

# ============================================
# LIQUIDITY ZONES — EQUAL HIGHS / LOWS
# ============================================
def detect_liquidity_zones(candles):
    if len(candles) < 30:
        return [], [], False, False

    sh = [p[1] for p in find_swing_highs(candles[-40:], lookback=2)]
    sl = [p[1] for p in find_swing_lows(candles[-40:],  lookback=2)]

    curr_price = candles[-1]["close"]
    high_now   = candles[-1]["high"]
    low_now    = candles[-1]["low"]

    equal_highs = []
    for i in range(len(sh)):
        for j in range(i+1, len(sh)):
            if abs(sh[i] - sh[j]) <= EQUAL_LEVEL_TOL:
                equal_highs.append(round((sh[i] + sh[j]) / 2, 2))

    equal_lows = []
    for i in range(len(sl)):
        for j in range(i+1, len(sl)):
            if abs(sl[i] - sl[j]) <= EQUAL_LEVEL_TOL:
                equal_lows.append(round((sl[i] + sl[j]) / 2, 2))

    swept_high = False
    for eq_h in equal_highs:
        if high_now > eq_h and curr_price < eq_h:
            swept_high = True
            break

    swept_low = False
    for eq_l in equal_lows:
        if low_now < eq_l and curr_price > eq_l:
            swept_low = True
            break

    return equal_highs, equal_lows, swept_high, swept_low

# ============================================
# FAIR VALUE GAPS (FVG)
# ============================================
def detect_fvg(candles):
    if len(candles) < 4:
        return None

    c1 = candles[-3]
    c3 = candles[-1]

    if c3["low"] > c1["high"]:
        gap = c3["low"] - c1["high"]
        if gap >= MIN_FVG_SIZE:
            return "BULLISH_FVG"

    if c3["high"] < c1["low"]:
        gap = c1["low"] - c3["high"]
        if gap >= MIN_FVG_SIZE:
            return "BEARISH_FVG"

    return None

# ============================================
# LIQUIDITY GRAB (WICK SWEEP)
# ============================================
def detect_liquidity_grab(candles):
    if len(candles) < 15:
        return None

    curr = candles[-1]
    prev_high = max(c["high"] for c in candles[-10:-1])
    prev_low  = min(c["low"]  for c in candles[-10:-1])

    body_top    = max(curr["open"], curr["close"])
    body_bottom = min(curr["open"], curr["close"])
    total_range = curr["high"] - curr["low"]

    if total_range == 0:
        return None

    upper_wick = curr["high"] - body_top
    lower_wick = body_bottom - curr["low"]

    if curr["high"] > prev_high and curr["close"] < prev_high:
        if upper_wick / total_range >= 0.50:
            return "BEARISH_GRAB"

    if curr["low"] < prev_low and curr["close"] > prev_low:
        if lower_wick / total_range >= 0.50:
            return "BULLISH_GRAB"

    return None

# ============================================
# CANDLE PATTERNS
# ============================================
def check_candle_pattern(candles):
    if len(candles) < 3:
        return None, 0

    curr = candles[-1]
    prev = candles[-2]

    curr_body = abs(curr["close"] - curr["open"])
    prev_body = abs(prev["close"] - prev["open"])
    total_range = curr["high"] - curr["low"]

    if total_range == 0:
        return None, 0

    body_top    = max(curr["open"], curr["close"])
    body_bottom = min(curr["open"], curr["close"])
    lower_wick  = body_bottom - curr["low"]
    upper_wick  = curr["high"] - body_top

    if (curr["close"] > curr["open"] and prev["close"] < prev["open"] and
            curr["close"] > prev["open"] and curr["open"] < prev["close"]):
        return "BULLISH_ENGULF", 3

    if (curr["close"] < curr["open"] and prev["close"] > prev["open"] and
            curr["open"] > prev["close"] and curr["close"] < prev["open"]):
        return "BEARISH_ENGULF", 3

    if lower_wick >= 2 * curr_body and upper_wick <= 0.3 * curr_body:
        return "HAMMER", 2

    if upper_wick >= 2 * curr_body and lower_wick <= 0.3 * curr_body:
        return "SHOOTING_STAR", 2

    return None, 0

# ============================================
# RSI CALCULATION
# ============================================
def calculate_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50.0

    closes = [c["close"] for c in candles[-(period+1):]]
    gains, losses = [], []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

# ============================================
# DYNAMIC SL DISTANCE CALCULATION
# ============================================
def calculate_sl_distance(candles, bias, ob_high, ob_low):
    curr = candles[-1]["close"]

    if bias == "LONG":
        if ob_low is not None:
            sl_level = ob_low - 0.50
        else:
            swing_lows = find_swing_lows(candles[-30:])
            sl_level = swing_lows[-1][1] - 0.50 if swing_lows else curr - 5.0
        return max(curr - sl_level, MIN_SL_DOLLARS)

    elif bias == "SHORT":
        if ob_high is not None:
            sl_level = ob_high + 0.50
        else:
            swing_highs = find_swing_highs(candles[-30:])
            sl_level = swing_highs[-1][1] + 0.50 if swing_highs else curr + 5.0
        return max(sl_level - curr, MIN_SL_DOLLARS)

    return 5.0

# ============================================
# CALCULATE LOTS & RISK
# ============================================
def calculate_lots(sl_dollars):
    lots = FIXED_LOT # 0.01 micro lot
    actual_risk = round(sl_dollars * USD_PER_DOLLAR_MOVE_PER_001_LOT, 2)
    # On $40 fund, risk is safe if SL distance <= MAX_SL_DOLLARS ($10.00)
    is_safe = sl_dollars <= (MAX_SL_DOLLARS + 0.50)
    return lots, actual_risk, is_safe

# ============================================
# MAIN SIGNAL ENGINE — FULL SMC STRATEGY
# ============================================
def generate_signal(candles_5m, candles_15m):
    if not candles_5m or not candles_15m:
        return None

    curr_price = candles_5m[-1]["close"]

    # 15M HIGHER TIMEFRAME BIAS
    tf_bias, bos_type, choch = analyze_structure_bos(candles_15m)

    if tf_bias == "NEUTRAL":
        print(f"Price: {curr_price:.2f} | 15M = NEUTRAL — holding fire (no trade)")
        return None

    # 5M CONFLUENCE ANALYSIS
    ob_high, ob_low, price_in_ob                  = detect_order_block(candles_5m, tf_bias)
    eq_highs, eq_lows, swept_high, swept_low      = detect_liquidity_zones(candles_5m)
    fvg                                           = detect_fvg(candles_5m)
    liquidity                                     = detect_liquidity_grab(candles_5m)
    pattern, pat_score                            = check_candle_pattern(candles_5m)
    rsi                                           = calculate_rsi(candles_5m)

    long_score, short_score = 0, 0
    long_reasons, short_reasons = [], []

    # Market structure score
    if tf_bias == "BULLISH":
        long_score += 25
        long_reasons.append(f"15M Bullish Structure ({bos_type}{'+ CHoCH' if choch else ''})")
    elif tf_bias == "BEARISH":
        short_score += 25
        short_reasons.append(f"15M Bearish Structure ({bos_type}{'+ CHoCH' if choch else ''})")

    # Order block score
    if ob_high is not None:
        if tf_bias == "BULLISH":
            long_score += 15
            long_reasons.append(f"Bullish Order Block ({ob_low:.2f} - {ob_high:.2f})")
            if price_in_ob:
                long_score += 10
                long_reasons.append("Price inside Order Block (Discount zone)")
        elif tf_bias == "BEARISH":
            short_score += 15
            short_reasons.append(f"Bearish Order Block ({ob_low:.2f} - {ob_high:.2f})")
            if price_in_ob:
                short_score += 10
                short_reasons.append("Price inside Order Block (Premium zone)")

    # Liquidity grab
    if swept_low and tf_bias == "BULLISH":
        long_score += 15
        long_reasons.append("Equal Lows Swept (Buy-side liquidity engineered)")
    if swept_high and tf_bias == "BEARISH":
        short_score += 15
        short_reasons.append("Equal Highs Swept (Sell-side liquidity engineered)")

    if liquidity == "BULLISH_GRAB":
        long_score += 10
        long_reasons.append("Bullish Liquidity Grab (Wick sweep)")
    elif liquidity == "BEARISH_GRAB":
        short_score += 10
        short_reasons.append("Bearish Liquidity Grab (Wick sweep)")

    # FVG
    if fvg == "BULLISH_FVG":
        long_score += 10
        long_reasons.append(f"Bullish FVG (Imbalance ≥${MIN_FVG_SIZE})")
    elif fvg == "BEARISH_FVG":
        short_score += 10
        short_reasons.append(f"Bearish FVG (Imbalance ≥${MIN_FVG_SIZE})")

    # Candlestick pattern
    if pattern in ["BULLISH_ENGULF", "HAMMER"]:
        long_score += pat_score * 5
        long_reasons.append(f"Candle Pattern: {pattern}")
    elif pattern in ["BEARISH_ENGULF", "SHOOTING_STAR"]:
        short_score += pat_score * 5
        short_reasons.append(f"Candle Pattern: {pattern}")

    # RSI Momentum
    if rsi < 35:
        long_score += 8
        long_reasons.append(f"RSI Oversold ({rsi})")
    elif rsi > 65:
        short_score += 8
        short_reasons.append(f"RSI Overbought ({rsi})")

    print(f"Confluence: Long={long_score} | Short={short_score} | Need: {MIN_CONFIDENCE}+")

    signal, score, reasons = None, 0, []
    if long_score > short_score and long_score >= MIN_CONFIDENCE and tf_bias == "BULLISH":
        signal  = "LONG"
        score   = min(long_score, 99)
        reasons = long_reasons
    elif short_score > long_score and short_score >= MIN_CONFIDENCE and tf_bias == "BEARISH":
        signal  = "SHORT"
        score   = min(short_score, 99)
        reasons = short_reasons

    if not signal:
        return None

    # Calculate smart technical SL
    sl_dollars_raw = calculate_sl_distance(candles_5m, signal, ob_high, ob_low)
    sl_dollars_a = min(round(sl_dollars_raw, 2), MAX_SL_DOLLARS)
    tp_dollars_a = round(sl_dollars_a * 2.0, 2)

    # Profile B: User's Quick Scalp ($10 SL / $3 TP test)
    sl_dollars_b = 10.00
    tp_dollars_b = 3.00

    if signal == "LONG":
        sl_price_a = round(curr_price - sl_dollars_a, 2)
        tp_price_a = round(curr_price + tp_dollars_a, 2)
        sl_price_b = round(curr_price - sl_dollars_b, 2)
        tp_price_b = round(curr_price + tp_dollars_b, 2)
    else:
        sl_price_a = round(curr_price + sl_dollars_a, 2)
        tp_price_a = round(curr_price - tp_dollars_a, 2)
        sl_price_b = round(curr_price + sl_dollars_b, 2)
        tp_price_b = round(curr_price - tp_dollars_b, 2)

    return {
        "signal":        signal,
        "price":         curr_price,
        "lots":          FIXED_LOT,
        "confidence":    score,
        "reasons":       reasons,
        "rsi":           rsi,
        "tf_bias":       tf_bias,
        "bos_type":      bos_type or "None",
        "choch":         choch,
        "ob_high":       ob_high,
        "ob_low":        ob_low,
        "price_in_ob":   price_in_ob,
        "fvg":           fvg or "None",
        "pattern":       pattern or "None",
        # Profile A (Institutional 1:2)
        "setup_a": {
            "name": "Institutional SMC (1:2 R:R)",
            "sl_dollars": sl_dollars_a,
            "tp_dollars": tp_dollars_a,
            "sl_price": sl_price_a,
            "tp_price": tp_price_a,
            "rr": "1:2.0",
            "be_winrate": "33.3%",
            "max_risk_usd": sl_dollars_a,
            "target_win_usd": tp_dollars_a,
        },
        # Profile B (Quick Scalp User Test)
        "setup_b": {
            "name": "Quick Scalp ($10 SL / $3 TP)",
            "sl_dollars": sl_dollars_b,
            "tp_dollars": tp_dollars_b,
            "sl_price": sl_price_b,
            "tp_price": tp_price_b,
            "rr": "1:0.3",
            "be_winrate": "76.9%",
            "max_risk_usd": sl_dollars_b,
            "target_win_usd": tp_dollars_b,
        }
    }

# ============================================
# FORMAT & DISPATCH DUAL PAPER SIGNAL
# ============================================
def send_signal(sig):
    direction = "🟢 LONG (BUY)" if sig["signal"] == "LONG" else "🔴 SHORT (SELL)"
    emoji     = "📈" if sig["signal"] == "LONG" else "📉"

    reasons_text = "\n".join([f"  ✅ {r}" for r in sig["reasons"]])
    now_pht      = datetime.now(PHT).strftime('%d %b %Y %I:%M %p PHT')
    session      = get_session()

    # Exact margin calculation for Vantage 1:500 on 0.01 micro lot
    notional = sig["price"] * 100 * FIXED_LOT
    req_margin = round(notional / LEVERAGE, 2)
    free_margin = round(CAPITAL - req_margin, 2)

    sa = sig["setup_a"]
    sb = sig["setup_b"]

    msg = f"""🧪 <b>XAUBOTTY DUAL-PAPER SIGNAL</b> {emoji}
━━━━━━━━━━━━━━━━━━━━
📊 <b>Direction:</b> {direction}
💰 <b>Market Entry:</b> {sig["price"]:.2f}
📦 <b>Volume:</b> {sig["lots"]} Micro Lot (Fixed)
🇵🇭 <b>Time:</b> {now_pht}
🌏 <b>Session:</b> {session}
💼 <b>Account:</b> ${CAPITAL:.2f} Fund | 1:{LEVERAGE} Leverage
🔒 <b>Margin Used:</b> ${req_margin:.2f} | <b>Free Margin:</b> ${free_margin:.2f}
━━━━━━━━━━━━━━━━━━━━
📊 <b>DUAL PAPER COMPARISON MODES:</b>

🔹 <b>[SETUP A] INSTITUTIONAL SMC (1:2 R:R)</b>
🛑 <b>Stop Loss:</b> {sa["sl_price"]:.2f} (-${sa["max_risk_usd"]:.2f})
🎯 <b>Take Profit:</b> {sa["tp_price"]:.2f} (+${sa["target_win_usd"]:.2f})
⚖️ <b>Risk/Reward:</b> {sa["rr"]} | <b>Needed Win Rate:</b> {sa["be_winrate"]}

🔸 <b>[SETUP B] QUICK SCALP TEST ($10 SL / $3 TP)</b>
🛑 <b>Stop Loss:</b> {sb["sl_price"]:.2f} (-${sb["max_risk_usd"]:.2f})
🎯 <b>Take Profit:</b> {sb["tp_price"]:.2f} (+${sb["target_win_usd"]:.2f})
⚖️ <b>Risk/Reward:</b> {sb["rr"]} | <b>Needed Win Rate:</b> {sb["be_winrate"]}
━━━━━━━━━━━━━━━━━━━━
📋 <b>SMC CONFLUENCE (Score: {sig['confidence']}%):</b>
{reasons_text}

📈 <b>15M Structure:</b> {sig["tf_bias"]} | {sig["bos_type"]}{'+ CHoCH🔄' if sig["choch"] else ''}
📊 <b>FVG:</b> {sig["fvg"]}
🕯 <b>Pattern:</b> {sig["pattern"]}
📉 <b>RSI:</b> {sig["rsi"]}
━━━━━━━━━━━━━━━━━━━━
⚠️ <i>Paper Mode Active. Tracking real outcomes for Setup A vs Setup B.</i>"""

    send_telegram(msg)
    print(f"✅ Dispatched Dual Signal: {sig['signal']} @ {sig['price']:.2f} | Setup A SL: {sa['sl_price']} TP: {sa['tp_price']} | Setup B SL: {sb['sl_price']} TP: {sb['tp_price']}")

    # Register open paper trade for live tracking
    paper_trade = {
        "id": int(time.time()),
        "time_pht": now_pht,
        "signal": sig["signal"],
        "entry": sig["price"],
        "setup_a": {
            "sl": sa["sl_price"],
            "tp": sa["tp_price"],
            "status": "OPEN",
            "pnl": 0.0
        },
        "setup_b": {
            "sl": sb["sl_price"],
            "tp": sb["tp_price"],
            "status": "OPEN",
            "pnl": 0.0
        }
    }
    active_paper_trades.append(paper_trade)


def evaluate_open_paper_trades(current_candle):
    """Monitors active paper trades and sends resolution alerts when SL or TP is touched."""
    high = current_candle["high"]
    low  = current_candle["low"]
    time_str = current_candle["time"]

    resolved_any = False
    for trade in active_paper_trades:
        # Check Setup A
        if trade["setup_a"]["status"] == "OPEN":
            if trade["signal"] == "LONG":
                if high >= trade["setup_a"]["tp"]:
                    trade["setup_a"]["status"] = "TAKE_PROFIT_HIT"
                    trade["setup_a"]["pnl"] = round(trade["setup_a"]["tp"] - trade["entry"], 2)
                    resolved_any = True
                elif low <= trade["setup_a"]["sl"]:
                    trade["setup_a"]["status"] = "STOP_LOSS_HIT"
                    trade["setup_a"]["pnl"] = -round(trade["entry"] - trade["setup_a"]["sl"], 2)
                    resolved_any = True
            else: # SHORT
                if low <= trade["setup_a"]["tp"]:
                    trade["setup_a"]["status"] = "TAKE_PROFIT_HIT"
                    trade["setup_a"]["pnl"] = round(trade["entry"] - trade["setup_a"]["tp"], 2)
                    resolved_any = True
                elif high >= trade["setup_a"]["sl"]:
                    trade["setup_a"]["status"] = "STOP_LOSS_HIT"
                    trade["setup_a"]["pnl"] = -round(trade["setup_a"]["sl"] - trade["entry"], 2)
                    resolved_any = True

        # Check Setup B
        if trade["setup_b"]["status"] == "OPEN":
            if trade["signal"] == "LONG":
                if high >= trade["setup_b"]["tp"]:
                    trade["setup_b"]["status"] = "TAKE_PROFIT_HIT"
                    trade["setup_b"]["pnl"] = 3.00
                    resolved_any = True
                elif low <= trade["setup_b"]["sl"]:
                    trade["setup_b"]["status"] = "STOP_LOSS_HIT"
                    trade["setup_b"]["pnl"] = -10.00
                    resolved_any = True
            else: # SHORT
                if low <= trade["setup_b"]["tp"]:
                    trade["setup_b"]["status"] = "TAKE_PROFIT_HIT"
                    trade["setup_b"]["pnl"] = 3.00
                    resolved_any = True
                elif high >= trade["setup_b"]["sl"]:
                    trade["setup_b"]["status"] = "STOP_LOSS_HIT"
                    trade["setup_b"]["pnl"] = -10.00
                    resolved_any = True

        if resolved_any:
            msg = f"""🔔 <b>XAUBOTTY PAPER OUTCOME UPDATE</b>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Signal:</b> {trade['signal']} from {trade['entry']:.2f}
🕐 <b>Trigger Candle:</b> {time_str}

🔹 <b>[Setup A Institutional 1:2]:</b> {trade['setup_a']['status']} (P/L: ${trade['setup_a']['pnl']:+.2f})
🔸 <b>[Setup B Quick Scalp]:</b> {trade['setup_b']['status']} (P/L: ${trade['setup_b']['pnl']:+.2f})
━━━━━━━━━━━━━━━━━━━━
<i>Paper ledger updated in local memory.</i>"""
            send_telegram(msg)
            history = load_paper_history()
            history.append(trade)
            save_paper_history(history)

    # Keep only open trades
    active_paper_trades[:] = [t for t in active_paper_trades if t["setup_a"]["status"] == "OPEN" or t["setup_b"]["status"] == "OPEN"]

# ============================================
# PHILIPPINES SESSION LABEL
# ============================================
def get_session():
    h = datetime.now(PHT).hour
    if 8 <= h < 15:
        return "Asian Session (Tokyo/HK) 🌏"
    elif 15 <= h < 20:
        return "London Session 🇬🇧"
    elif 20 <= h < 24:
        return "London/NY Overlap (Peak Volatility) ⚡"
    elif 0 <= h < 5:
        return "New York Afternoon 🗽"
    else:
        return "Off Hours (Pacific) 🌙"

# ============================================
# MAIN LOOP
# ============================================
def main():
    global trades_today, last_trade_date, last_signal_direction, last_signal_time

    print("🚀 XAUBOTTY Starting...")
    print(f"Location: Philippines (PHT / UTC+8)")
    print(f"Capital: ${CAPITAL:.2f} | Leverage: 1:{LEVERAGE} | Lot: {FIXED_LOT}")
    print(f"Dual Paper Mode: [Setup A: Institutional 1:2] vs [Setup B: Quick Scalp $10 SL / $3 TP]")
    print(f"Paper mode: {PAPER_MODE}")

    send_telegram(
        f"🚀 <b>XAUBOTTY LIVE IN PHILIPPINES 🇵🇭</b>\n\n"
        f"🧪 <b>DUAL PAPER TESTING ACTIVE</b>\n\n"
        f"💼 <b>Account:</b> ${CAPITAL:.2f} Fund | 1:{LEVERAGE} Leverage\n"
        f"📦 <b>Size:</b> {FIXED_LOT} Micro Lot (Fixed)\n"
        f"🕒 <b>Session:</b> {get_session()}\n\n"
        f"📊 <b>Testing 2 Modes Simultaneously:</b>\n"
        f"  🔹 <b>Setup A:</b> Institutional SMC (1:2 R:R)\n"
        f"  🔸 <b>Setup B:</b> Quick Scalp ($10 SL / $3 TP)\n\n"
        f"Scanning on 5-Minute Candle Closes ⚔️"
    )

    while True:
        try:
            now   = datetime.now(PHT)
            today = now.date()

            if last_trade_date != today:
                trades_today = 0
                last_trade_date = today
                last_signal_direction = None
                print(f"\n📅 New day in Philippines: {today}")
                send_telegram(f"📅 <b>New Trading Day: {today} (PHT)</b>\nSignals remaining: {MAX_TRADES_PER_DAY}")

            session = get_session()
            print(f"\n[{now.strftime('%I:%M %p PHT')}] {session} | Signals: {trades_today}/{MAX_TRADES_PER_DAY}")

            if trades_today >= MAX_TRADES_PER_DAY:
                print("Daily limit reached (3/3). Sleeping 1hr...")
                time.sleep(3600)
                continue

            if now.weekday() >= 5: # Saturday/Sunday
                print("Weekend — Gold market closed. Sleeping 1hr...")
                time.sleep(3600)
                continue

            print("Fetching candles from TwelveData...")
            candles_5m = get_candles("5min", 100)
            time.sleep(2)
            candles_15m = get_candles("15min", 100)

            if not candles_5m or not candles_15m:
                print("Fetch failed. Retry in 2min...")
                time.sleep(120)
                continue

            # Check open paper trades against the latest candle
            if active_paper_trades:
                evaluate_open_paper_trades(candles_5m[-1])

            # Generate new signal
            sig = generate_signal(candles_5m, candles_15m)

            if sig:
                now_ts = time.time()
                if (last_signal_direction == sig["signal"] and
                        now_ts - last_signal_time < 2700):
                    print("Same direction within 45min — skipping repeat entry.")
                else:
                    send_signal(sig)
                    trades_today         += 1
                    last_signal_time      = now_ts
                    last_signal_direction = sig["signal"]
                    if trades_today >= MAX_TRADES_PER_DAY:
                        send_telegram(
                            f"🔴 <b>Daily Limit Reached</b>\n"
                            f"{MAX_TRADES_PER_DAY}/{MAX_TRADES_PER_DAY} signals sent.\n"
                            f"Resuming tomorrow."
                        )
            else:
                print("No signal on this candle. Waiting for next close...")

            # Clock-sync: sleep until next 5M candle close
            ts         = time.time()
            sleep_time = (300 - (ts % 300)) + 3
            print(f"Sleeping {int(sleep_time)}s until next 5M candle close...")
            time.sleep(sleep_time)

        except KeyboardInterrupt:
            send_telegram("🔴 <b>XAUBOTTY stopped.</b>")
            break
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(120)

if __name__ == "__main__":
    main()
