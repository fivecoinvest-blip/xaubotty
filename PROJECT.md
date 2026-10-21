# PROJECT.md — Xaubotty

## 1. Goal
**Xaubotty** is an institutional-grade algorithmic trading signal bot engineered specifically for Gold (XAU/USD) using Smart Money Concepts (SMC), Market Structure (BOS / CHoCH), Liquidity Sweeps, and Fair Value Gaps (FVG) to dispatch high-probability trade setups directly to Telegram.

## 2. Stack
* **Language**: Python 3.11+
* **Primary Market Data**: MetaTrader 5 (100% Free Live Direct Broker Feed, zero latency, live bid/ask/spread, zero API keys)
* **Alert Delivery**: Telegram Bot API (`sendMessage` with HTML formatting)
* **Execution Engine**: Dual Paper Mode (Zero real capital risk, paper tracking only)

## 3. Status
* **Status**: Fully calibrated for Philippines (PHT, UTC+8) and $40.00 fund with 1:500 leverage on Vantage.
* **Target Repository**: `https://github.com/fivecoinvest-blip/xaubotty` (Clean commit history, zero forks).
* **Execution**: Dual Paper Mode running simultaneously on 5-minute candle closes with automatic TP/SL outcome resolution alerts and real-time MT5 spread telemetry.

## 4. Key Decisions & Mathematical Calibration
* **Data Provider Choice (Option 1)**: Integrated MetaTrader 5 official Python library for 100% free, unlimited, zero-delay tick and candle feed directly from the user's broker terminal. Completely eliminates the need for any paid TwelveData plan in the long term.
* **Rejection of External Crypto Proxies (Binance PAXG)**: Benchmark testing revealed a ~$5.76 price basis discrepancy between Binance PAXG/USDT and Vantage XAU/USD spot prices. For tight scalping ($3 TP / $10 SL), this basis gap completely distorts execution. Strict architectural rule: MetaTrader 5 on Vantage is the single, non-negotiable source of truth.
* **Independent Ownership**: Clean Git history under `fivecoinvest-blip/xaubotty`.
* **Zero Hardcoded Secrets**: Strictly local `.env` (gitignored); never committed to repository.
* **Capital Sizing ($40 Fund @ 1:500 Leverage)**:
  * Margin requirement per 0.01 micro lot = ~$8.26 (20.6% of account).
  * Free margin = $31.74. Vantage 50% liquidation level = $4.13.
  * Gold liquidation cushion = $35.87 adverse move.
  * Fixed micro lot size (0.01) replaces old dynamic lot calculation that failed under $40 accounts.
* **Dual Paper Mode**:
  * **Setup A [Institutional SMC 1:2 R:R]**: Dynamic SL based on order blocks/swing levels (capped at $10 max), TP = 2x SL. Breakeven win rate: 33.3%.
  * **Setup B [Quick Scalp]**: Fixed $10.00 SL, $3.00 TP. Breakeven win rate: 76.9%.
* **Automated Outcome Engine**:
  * Evaluates open trades against incoming 5M candle highs/lows.
  * Dispatches victory/loss alerts to Telegram and logs to `paper_trades_history.json`.
