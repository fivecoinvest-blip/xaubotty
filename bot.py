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
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

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

MT5_SYMBOL         = "XAUUSD"
CAPITAL            = 40.0             # Exact user account fund
LEVERAGE           = 500              # Vantage 1:500 high leverage
FIXED_LOT          = 0.01             # Micro lot (1 oz gold)

# Data Provider Configuration (MetaTrader 5 Direct Free Broker Feed)
MT5_INITIALIZED    = False

MAX_TRADES_PER_DAY = 3
MIN_CONFIDENCE     = 65
MIN_SL_DOLLARS     = 2.50
MAX_SL_DOLLARS     = 10.0           # Max stop loss cap ($10 on $40 fund)
MIN_FVG_SIZE       = 1.50           # Minimum FVG gap in dollars
EQUAL_LEVEL_TOL    = 0.30           # Dollars — swing high/low liquidity proximity
PHT                = pytz.timezone('Asia/Manila')  # Philippines Local Time (UTC+8)

PAPER_MODE = True                   # Strictly Paper Mode Only!

# Gold contract on Vantage: 1.00 lot = 100oz -> $1 move = $100/lot -> $1 move = $1.00 per 0.01 lot
USD_PER_DOLLAR_MOVE_PER_001_LOT = 1.0

# ============================================
# PERSISTENT LOCAL LEDGER (CRASH-PROOF & RESTART-SAFE)
# ============================================
DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")
LEDGER_FILE = os.path.join(DATA_DIR, "ledger.json")

DEFAULT_LEDGER = {
    "accounts": {
        "setup_a": {
            "name": "Disciplined Institutional (1:2 R:R)",
            "badge": "🔹 Setup A",
            "rule": "Disciplined (Max 3/day)",
            "initial_balance": CAPITAL,
            "balance": CAPITAL,
            "total_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "trades_today": 0,
            "last_trade_date": None
        },
        "setup_b": {
            "name": "Disciplined Quick Scalp ($10 SL / $3 TP)",
            "badge": "🔸 Setup B",
            "rule": "Disciplined (Max 3/day)",
            "initial_balance": CAPITAL,
            "balance": CAPITAL,
            "total_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "trades_today": 0,
            "last_trade_date": None
        },
        "setup_c": {
            "name": "Unlimited High-Frequency SMC",
            "badge": "⚡ Setup C",
            "rule": "UNLIMITED (No Daily Limit)",
            "initial_balance": CAPITAL,
            "balance": CAPITAL,
            "total_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "trades_today": 0,
            "last_trade_date": None
        }
    },
    "open_trades": [],
    "closed_trades": []
}


def load_ledger():
    """Loads ledger from disk, auto-initializing fresh accounts if not present."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(LEDGER_FILE):
        try:
            with open(LEDGER_FILE, "r") as f:
                data = json.load(f)
                if "accounts" not in data:
                    data["accounts"] = {}
                for k, v in DEFAULT_LEDGER["accounts"].items():
                    if k not in data["accounts"]:
                        data["accounts"][k] = v.copy()
                if "open_trades" not in data:
                    data["open_trades"] = []
                if "closed_trades" not in data:
                    data["closed_trades"] = []
                return data
        except Exception as e:
            print(f"Error loading ledger: {e}")
    # Return fresh ledger
    import copy
    fresh = copy.deepcopy(DEFAULT_LEDGER)
    save_ledger(fresh)
    return fresh


def save_ledger(data):
    """Atomically writes ledger to disk to prevent corruptions during power-cuts or restarts."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        tmp_file = LEDGER_FILE + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, LEDGER_FILE)
    except Exception as e:
        print(f"Error saving ledger: {e}")


def check_daily_reset(ledger, current_date_str):
    """Resets daily trade counters for each account when a new calendar day arrives in Philippines."""
    updated = False
    for acc in ledger["accounts"].values():
        if acc.get("last_trade_date") != current_date_str:
            acc["trades_today"] = 0
            acc["last_trade_date"] = current_date_str
            updated = True
    if updated:
        save_ledger(ledger)


last_signal_direction = None
last_signal_time      = 0

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
# METATRADER 5 DEDICATED LIVE MARKET DATA
# ============================================
def init_mt5():
    """Initializes MetaTrader 5 connection to fetch 100% free live broker candles & spreads."""
    global MT5_INITIALIZED, MT5_SYMBOL
    if not MT5_AVAILABLE:
        print("❌ MetaTrader5 library not found. Please install: pip install MetaTrader5")
        return False
    try:
        if not mt5.initialize():
            print(f"❌ MetaTrader 5 initialize failed: {mt5.last_error()}. Make sure MetaTrader 5 is running.")
            return False

        # Detect the exact Gold symbol available on the broker
        available = [s.name for s in mt5.symbols_get() if s.name in ["XAUUSD", "GOLD", "XAUUSD+", "XAUUSD.", "XAUUSDm", "XAUUSD#"]]
        if available:
            MT5_SYMBOL = available[0]
        else:
            MT5_SYMBOL = "XAUUSD"

        mt5.symbol_select(MT5_SYMBOL, True)
        term_info = mt5.terminal_info()
        company = term_info.company if term_info else "Vantage/MT5"
        print(f"✅ MetaTrader 5 Connected: {company} | Symbol: {MT5_SYMBOL} (100% Free Live Feed)")
        MT5_INITIALIZED = True
        return True
    except Exception as e:
        print(f"Error initializing MT5: {e}")
        return False


def get_live_market_info():
    """Returns real-time bid, ask, and spread from MT5."""
    if MT5_INITIALIZED:
        try:
            tick = mt5.symbol_info_tick(MT5_SYMBOL)
            if tick:
                spread = round(tick.ask - tick.bid, 2)
                return {
                    "source": "MetaTrader 5 (Vantage Direct)",
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "spread": spread
                }
        except Exception:
            pass
    return {
        "source": "MetaTrader 5 (Connecting...)",
        "bid": None,
        "ask": None,
        "spread": None
    }


def get_candles(interval="5min", outputsize=100):
    """
    Fetches candles directly from MetaTrader 5 (100% free, 0 delay, exact broker price).
    Zero third-party API dependencies.
    """
    global MT5_INITIALIZED
    if not MT5_INITIALIZED:
        init_mt5()
    if not MT5_INITIALIZED:
        print("MetaTrader 5 not connected. Please ensure your MetaTrader 5 terminal is open.")
        return None

    tf = mt5.TIMEFRAME_M5 if interval == "5min" else (mt5.TIMEFRAME_M15 if interval == "15min" else mt5.TIMEFRAME_H1)
    rates = mt5.copy_rates_from_pos(MT5_SYMBOL, tf, 0, outputsize)
    if rates is not None and len(rates) > 0:
        candles = []
        for r in rates:
            candles.append({
                "time": datetime.fromtimestamp(r["time"]).strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["tick_volume"])
            })
        return candles
    else:
        print(f"MT5 rates unavailable for {interval}: {mt5.last_error()}")
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
        },
        # Profile C (Unlimited High-Frequency SMC)
        "setup_c": {
            "name": "Unlimited High-Frequency SMC",
            "sl_dollars": sl_dollars_a,
            "tp_dollars": tp_dollars_a,
            "sl_price": sl_price_a,
            "tp_price": tp_price_a,
            "rr": "1:2.0",
            "be_winrate": "33.3%",
            "max_risk_usd": sl_dollars_a,
            "target_win_usd": tp_dollars_a,
        }
    }

# ============================================
# FORMAT & DISPATCH TRIPLE PAPER SIGNAL
# ============================================
def send_signal(sig):
    direction = "🟢 LONG (BUY)" if sig["signal"] == "LONG" else "🔴 SHORT (SELL)"
    emoji     = "📈" if sig["signal"] == "LONG" else "📉"

    reasons_text = "\n".join([f"  ✅ {r}" for r in sig["reasons"]])
    now_pht      = datetime.now(PHT).strftime('%d %b %Y %I:%M %p PHT')
    today_str    = datetime.now(PHT).strftime('%Y-%m-%d')
    session      = get_session()

    # Load persistent ledger & update daily limits
    ledger = load_ledger()
    check_daily_reset(ledger, today_str)

    acc_a = ledger["accounts"]["setup_a"]
    acc_b = ledger["accounts"]["setup_b"]
    acc_c = ledger["accounts"]["setup_c"]

    can_take_a = acc_a["trades_today"] < MAX_TRADES_PER_DAY
    can_take_b = acc_b["trades_today"] < MAX_TRADES_PER_DAY
    can_take_c = True # Always unlimited!

    if can_take_a:
        acc_a["trades_today"] += 1
    if can_take_b:
        acc_b["trades_today"] += 1
    acc_c["trades_today"] += 1

    # Exact margin calculation for Vantage 1:500 on 0.01 micro lot
    notional = sig["price"] * 100 * FIXED_LOT
    req_margin = round(notional / LEVERAGE, 2)
    free_margin = round(CAPITAL - req_margin, 2)

    sa = sig["setup_a"]
    sb = sig["setup_b"]
    sc = sig["setup_c"]

    market_info = get_live_market_info()
    spread_str  = f"${market_info['spread']:.2f} ({int(market_info['spread']*10)} pips)" if market_info["spread"] is not None else "N/A"
    feed_badge  = f"🏛️ <b>Data Feed:</b> {market_info['source']}\n📏 <b>Live Spread:</b> {spread_str}"

    status_a = f"✅ Active ({acc_a['trades_today']}/3 Today)" if can_take_a else "⏸️ Paused (3/3 Limit Reached)"
    status_b = f"✅ Active ({acc_b['trades_today']}/3 Today)" if can_take_b else "⏸️ Paused (3/3 Limit Reached)"
    status_c = f"⚡ Active ({acc_c['trades_today']} Trades Today — UNLIMITED)"

    msg = f"""🧪 <b>XAUBOTTY TRIPLE-PAPER SIGNAL</b> {emoji}
━━━━━━━━━━━━━━━━━━━━
📊 <b>Direction:</b> {direction}
💰 <b>Market Entry:</b> {sig["price"]:.2f}
📦 <b>Volume:</b> {sig["lots"]} Micro Lot (Fixed)
{feed_badge}
🇵🇭 <b>Time:</b> {now_pht}
🌏 <b>Session:</b> {session}
💼 <b>Account:</b> ${CAPITAL:.2f} Starting Fund | 1:{LEVERAGE} Leverage
🔒 <b>Margin Used:</b> ${req_margin:.2f} | <b>Free Margin:</b> ${free_margin:.2f}
━━━━━━━━━━━━━━━━━━━━
📊 <b>3 ACCOUNT EXECUTION COMPARISON:</b>

🔹 <b>[SETUP A] INSTITUTIONAL SMC (1:2 R:R)</b>
🛑 <b>Stop Loss:</b> {sa["sl_price"]:.2f} (-${sa["max_risk_usd"]:.2f})
🎯 <b>Take Profit:</b> {sa["tp_price"]:.2f} (+${sa["target_win_usd"]:.2f})
⚖️ <b>R:R:</b> {sa["rr"]} | <b>Status:</b> {status_a}
💰 <b>Balance:</b> ${acc_a['balance']:.2f}

🔸 <b>[SETUP B] QUICK SCALP TEST ($10 SL / $3 TP)</b>
🛑 <b>Stop Loss:</b> {sb["sl_price"]:.2f} (-${sb["max_risk_usd"]:.2f})
🎯 <b>Take Profit:</b> {sb["tp_price"]:.2f} (+${sb["target_win_usd"]:.2f})
⚖️ <b>R:R:</b> {sb["rr"]} | <b>Status:</b> {status_b}
💰 <b>Balance:</b> ${acc_b['balance']:.2f}

⚡ <b>[SETUP C] UNLIMITED SMC (NO DAILY LIMIT)</b>
🛑 <b>Stop Loss:</b> {sc["sl_price"]:.2f} (-${sc["max_risk_usd"]:.2f})
🎯 <b>Take Profit:</b> {sc["tp_price"]:.2f} (+${sc["target_win_usd"]:.2f})
⚖️ <b>R:R:</b> {sc["rr"]} | <b>Status:</b> {status_c}
💰 <b>Balance:</b> ${acc_c['balance']:.2f}
━━━━━━━━━━━━━━━━━━━━
📋 <b>SMC CONFLUENCE (Score: {sig['confidence']}%):</b>
{reasons_text}

📈 <b>15M Structure:</b> {sig["tf_bias"]} | {sig["bos_type"]}{'+ CHoCH🔄' if sig["choch"] else ''}
📊 <b>FVG:</b> {sig["fvg"]}
🕯 <b>Pattern:</b> {sig["pattern"]}
📉 <b>RSI:</b> {sig["rsi"]}
━━━━━━━━━━━━━━━━━━━━
💾 <i>Persisted to local ledger: projects/xaubotty/data/ledger.json</i>"""

    send_telegram(msg)
    print(f"✅ Dispatched Signal: {sig['signal']} @ {sig['price']:.2f} | Setup A: {status_a} | Setup B: {status_b} | Setup C: {status_c}")

    # Register open paper trade for live tracking in persistent ledger
    paper_trade = {
        "id": f"trade_{int(time.time())}",
        "time_pht": now_pht,
        "signal": sig["signal"],
        "entry": sig["price"],
        "feed": market_info["source"],
        "spread": market_info["spread"],
        "setup_a": {
            "active": can_take_a,
            "sl": sa["sl_price"],
            "tp": sa["tp_price"],
            "status": "OPEN" if can_take_a else "SKIPPED_LIMIT_REACHED",
            "pnl": 0.0
        },
        "setup_b": {
            "active": can_take_b,
            "sl": sb["sl_price"],
            "tp": sb["tp_price"],
            "status": "OPEN" if can_take_b else "SKIPPED_LIMIT_REACHED",
            "pnl": 0.0
        },
        "setup_c": {
            "active": True,
            "sl": sc["sl_price"],
            "tp": sc["tp_price"],
            "status": "OPEN",
            "pnl": 0.0
        }
    }
    ledger.setdefault("open_trades", []).append(paper_trade)
    save_ledger(ledger)


def evaluate_open_paper_trades(current_candle):
    """Monitors active paper trades from the persistent ledger and records outcomes when SL or TP is touched."""
    ledger = load_ledger()
    open_trades = ledger.get("open_trades", [])
    if not open_trades:
        return

    high = current_candle["high"]
    low  = current_candle["low"]
    time_str = current_candle["time"]

    accounts = ledger["accounts"]
    resolved_any = False
    newly_resolved = []

    for trade in open_trades:
        trade_resolved = False

        # 1. Setup A
        sa = trade["setup_a"]
        if sa["active"] and sa["status"] == "OPEN":
            if trade["signal"] == "LONG":
                if high >= sa["tp"]:
                    sa["status"] = "TAKE_PROFIT_HIT"
                    sa["pnl"] = round(sa["tp"] - trade["entry"], 2)
                    trade_resolved = True
                elif low <= sa["sl"]:
                    sa["status"] = "STOP_LOSS_HIT"
                    sa["pnl"] = -round(trade["entry"] - sa["sl"], 2)
                    trade_resolved = True
            else: # SHORT
                if low <= sa["tp"]:
                    sa["status"] = "TAKE_PROFIT_HIT"
                    sa["pnl"] = round(trade["entry"] - sa["tp"], 2)
                    trade_resolved = True
                elif high >= sa["sl"]:
                    sa["status"] = "STOP_LOSS_HIT"
                    sa["pnl"] = -round(sa["sl"] - trade["entry"], 2)
                    trade_resolved = True

            if sa["status"] != "OPEN":
                accounts["setup_a"]["balance"] += sa["pnl"]
                accounts["setup_a"]["total_pnl"] += sa["pnl"]
                if sa["pnl"] > 0:
                    accounts["setup_a"]["wins"] += 1
                else:
                    accounts["setup_a"]["losses"] += 1

        # 2. Setup B
        sb = trade["setup_b"]
        if sb["active"] and sb["status"] == "OPEN":
            if trade["signal"] == "LONG":
                if high >= sb["tp"]:
                    sb["status"] = "TAKE_PROFIT_HIT"
                    sb["pnl"] = 3.00
                    trade_resolved = True
                elif low <= sb["sl"]:
                    sb["status"] = "STOP_LOSS_HIT"
                    sb["pnl"] = -10.00
                    trade_resolved = True
            else: # SHORT
                if low <= sb["tp"]:
                    sb["status"] = "TAKE_PROFIT_HIT"
                    sb["pnl"] = 3.00
                    trade_resolved = True
                elif high >= sb["sl"]:
                    sb["status"] = "STOP_LOSS_HIT"
                    sb["pnl"] = -10.00
                    trade_resolved = True

            if sb["status"] != "OPEN":
                accounts["setup_b"]["balance"] += sb["pnl"]
                accounts["setup_b"]["total_pnl"] += sb["pnl"]
                if sb["pnl"] > 0:
                    accounts["setup_b"]["wins"] += 1
                else:
                    accounts["setup_b"]["losses"] += 1

        # 3. Setup C (Unlimited)
        sc = trade["setup_c"]
        if sc["active"] and sc["status"] == "OPEN":
            if trade["signal"] == "LONG":
                if high >= sc["tp"]:
                    sc["status"] = "TAKE_PROFIT_HIT"
                    sc["pnl"] = round(sc["tp"] - trade["entry"], 2)
                    trade_resolved = True
                elif low <= sc["sl"]:
                    sc["status"] = "STOP_LOSS_HIT"
                    sc["pnl"] = -round(trade["entry"] - sc["sl"], 2)
                    trade_resolved = True
            else: # SHORT
                if low <= sc["tp"]:
                    sc["status"] = "TAKE_PROFIT_HIT"
                    sc["pnl"] = round(trade["entry"] - sc["tp"], 2)
                    trade_resolved = True
                elif high >= sc["sl"]:
                    sc["status"] = "STOP_LOSS_HIT"
                    sc["pnl"] = -round(sc["sl"] - trade["entry"], 2)
                    trade_resolved = True

            if sc["status"] != "OPEN":
                accounts["setup_c"]["balance"] += sc["pnl"]
                accounts["setup_c"]["total_pnl"] += sc["pnl"]
                if sc["pnl"] > 0:
                    accounts["setup_c"]["wins"] += 1
                else:
                    accounts["setup_c"]["losses"] += 1

        if trade_resolved:
            resolved_any = True
            newly_resolved.append(trade)

    if resolved_any:
        still_open = []
        for t in open_trades:
            # Trade is fully done if all active setups are closed
            all_done = True
            for s_key in ["setup_a", "setup_b", "setup_c"]:
                if t[s_key]["active"] and t[s_key]["status"] == "OPEN":
                    all_done = False
                    break
            if all_done:
                ledger.setdefault("closed_trades", []).append(t)
            else:
                still_open.append(t)

        ledger["open_trades"] = still_open
        save_ledger(ledger)

        for trade in newly_resolved:
            def fmt_stats(acc_key):
                acc = accounts[acc_key]
                tot = acc["wins"] + acc["losses"]
                wr = round((acc["wins"] / tot * 100), 1) if tot > 0 else 0.0
                return f"${acc['balance']:.2f} (P/L: ${acc['total_pnl']:+.2f}) | {wr}% WR ({acc['wins']}W - {acc['losses']}L)"

            msg = f"""🔔 <b>XAUBOTTY PAPER OUTCOME RESOLUTION</b>
━━━━━━━━━━━━━━━━━━━━
📊 <b>Trade:</b> {trade['signal']} from {trade['entry']:.2f}
🕐 <b>Candle:</b> {time_str}

🔹 <b>[Setup A - Disciplined 3/day]:</b> {trade['setup_a']['status']} (P/L: ${trade['setup_a']['pnl']:+.2f})
   💰 <b>Account:</b> {fmt_stats('setup_a')}

🔸 <b>[Setup B - Quick Scalp 3/day]:</b> {trade['setup_b']['status']} (P/L: ${trade['setup_b']['pnl']:+.2f})
   💰 <b>Account:</b> {fmt_stats('setup_b')}

⚡ <b>[Setup C - Unlimited SMC]:</b> {trade['setup_c']['status']} (P/L: ${trade['setup_c']['pnl']:+.2f})
   💰 <b>Account:</b> {fmt_stats('setup_c')}
━━━━━━━━━━━━━━━━━━━━
💾 <i>Persisted to local ledger: projects/xaubotty/data/ledger.json</i>"""
            send_telegram(msg)
            print(f"🎯 Paper Outcome Recorded: Trade {trade['id']} resolved.")

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
    global last_signal_direction, last_signal_time

    print("🚀 XAUBOTTY Starting...")
    print(f"Location: Philippines (PHT / UTC+8)")
    print(f"Capital: ${CAPITAL:.2f} | Leverage: 1:{LEVERAGE} | Lot: {FIXED_LOT}")
    print(f"Triple Paper Mode: [Setup A (3/day)] vs [Setup B (3/day)] vs [Setup C (Unlimited)]")
    print(f"Paper mode: {PAPER_MODE} (Strictly No Real Trades)")

    # Initialize MetaTrader 5 for 100% Free Live Vantage Data
    init_mt5()

    # Rehydrate persistent local ledger
    ledger = load_ledger()
    open_count = len(ledger.get("open_trades", []))
    closed_count = len(ledger.get("closed_trades", []))
    print(f"💾 Persistent Ledger Loaded: {open_count} open trades, {closed_count} closed historical trades.")

    market_info = get_live_market_info()
    spread_str  = f"${market_info['spread']:.2f} ({int(market_info['spread']*10)} pips)" if market_info["spread"] is not None else "Connecting..."

    acc_a = ledger["accounts"]["setup_a"]
    acc_b = ledger["accounts"]["setup_b"]
    acc_c = ledger["accounts"]["setup_c"]

    send_telegram(
        f"🚀 <b>XAUBOTTY LIVE IN PHILIPPINES 🇵🇭</b>\n\n"
        f"🧪 <b>TRIPLE PAPER TESTING ACTIVE (METATRADER 5)</b>\n\n"
        f"🏛️ <b>Data Feed:</b> {market_info['source']}\n"
        f"📏 <b>Live Spread:</b> {spread_str}\n"
        f"💼 <b>Starting Fund:</b> ${CAPITAL:.2f} | 1:{LEVERAGE} Leverage\n"
        f"📦 <b>Size:</b> {FIXED_LOT} Micro Lot (Fixed)\n"
        f"🕒 <b>Session:</b> {get_session()}\n\n"
        f"📊 <b>Testing 3 Accounts Simultaneously:</b>\n"
        f"  🔹 <b>Setup A:</b> Institutional SMC 1:2 (Max 3/day) | Bal: ${acc_a['balance']:.2f}\n"
        f"  🔸 <b>Setup B:</b> Quick Scalp $10 SL / $3 TP (Max 3/day) | Bal: ${acc_b['balance']:.2f}\n"
        f"  ⚡ <b>Setup C:</b> Unlimited SMC (No Daily Limit) | Bal: ${acc_c['balance']:.2f}\n\n"
        f"💾 <i>Persistent Ledger: Active in local storage ({open_count} rehydrated open trades)</i>\n"
        f"Scanning on 5-Minute Candle Closes ⚔️"
    )

    last_day_announced = None

    while True:
        try:
            now       = datetime.now(PHT)
            today_str = now.strftime('%Y-%m-%d')

            # Ensure accounts reset their daily counters at Philippine midnight
            check_daily_reset(ledger, today_str)

            if last_day_announced != today_str:
                last_day_announced    = today_str
                last_signal_direction = None
                print(f"\n📅 New trading day in Philippines: {today_str}")
                send_telegram(f"📅 <b>New Trading Day: {today_str} (PHT)</b>\nSetups A & B reset to 3 signals. Setup C running UNLIMITED ⚡")

            session = get_session()
            acc_a = ledger["accounts"]["setup_a"]
            acc_b = ledger["accounts"]["setup_b"]
            acc_c = ledger["accounts"]["setup_c"]

            print(f"\n[{now.strftime('%I:%M %p PHT')}] {session} | Trades Today: A({acc_a['trades_today']}/3), B({acc_b['trades_today']}/3), C({acc_c['trades_today']} Unlimited)")

            if now.weekday() >= 5: # Saturday/Sunday
                print("Weekend — Gold market closed. Sleeping 1hr...")
                time.sleep(3600)
                continue

            print("Fetching live candles from MetaTrader 5...")
            candles_5m = get_candles("5min", 100)
            time.sleep(1)
            candles_15m = get_candles("15min", 100)

            if not candles_5m or not candles_15m:
                print("Fetch failed. Retry in 2min...")
                time.sleep(120)
                continue

            # Check open paper trades from persistent ledger against the latest candle
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
                    last_signal_direction = sig["signal"]
                    last_signal_time      = now_ts
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
