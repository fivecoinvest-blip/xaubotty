# PROJECT.md — Xaubotty

## 1. Goal
**Xaubotty** is an institutional-grade algorithmic trading signal bot engineered specifically for Gold (XAU/USD) using Smart Money Concepts (SMC), Market Structure (BOS / CHoCH), Liquidity Sweeps, and Fair Value Gaps (FVG) to dispatch high-probability trade setups directly to Telegram.

## 2. Stack
* **Language**: Python 3.9+
* **Market Data**: TwelveData REST API (`XAU/USD`, 15-min and 1-hour timeframes)
* **Alert Delivery**: Telegram Bot API (`sendMessage` with HTML formatting)
* **Deployment**: Railway / Heroku / Local runner via `Procfile` / `railway.json`

## 3. Status
* **Status**: Local folder renamed to `projects/xaubotty/`
* **Target Repository**: `https://github.com/fivecoinvest-blip/xaubotty`
* **Cleaned**: Removed all upstream fork references, foreign authors, and Codebuff attribution.

## 4. Key Decisions
* **Independent Ownership**: Rebuilt with clean Git history directly under `fivecoinvest-blip`.
* **Zero Hardcoded Secrets**: Uses environment variables (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `TWELVEDATA_API_KEY`) via `.env.example`.
* **Capital Protection**: Dynamic lot sizing and maximum daily trade limits (default: 3 trades/day, 5% max risk).
