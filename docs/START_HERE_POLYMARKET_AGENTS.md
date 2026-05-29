# Start Here: Building Polymarket Research Agents

This repo is useful as a learning/reference implementation, but it is archived and should not be treated as production-ready trading infrastructure. Start with a research-only agent, then add paper trading, then add tightly controlled execution only if you are legally eligible and understand the risks.

## What Polymarket Is

Polymarket is a prediction market. A market is usually framed as a question with outcomes such as Yes/No. Outcome prices can be read as rough market-implied probabilities, before accounting for spread, fees, liquidity, and market structure.

Example: if the Yes outcome trades around 0.62, the market is roughly pricing a 62% chance for Yes. Your edge only exists if your true probability estimate is better than the market after costs and uncertainty.

## Current API Map

Use the current official documentation as the source of truth:

- Gamma API: public market and event discovery at `https://gamma-api.polymarket.com`.
- Data API: public analytics such as positions, activity, holders, and trades at `https://data-api.polymarket.com`.
- CLOB API: order books, prices, spreads, price history, and authenticated trading at `https://clob.polymarket.com`.

The archived repo mostly maps onto this split:

- `agents/polymarket/gamma.py`: market and event metadata.
- `agents/polymarket/polymarket.py`: Gamma reads, CLOB reads, wallet auth, and order execution.
- `agents/application/executor.py`: LLM forecasting and trade sizing logic.
- `agents/application/trade.py`: end-to-end autonomous trader flow.
- `scripts/python/cli.py`: CLI wrapper for discovery, news, RAG, and agent commands.

## Safety Rules For This Project

1. Do not put a funded private key in `.env` while learning.
2. Keep execution disabled until research and paper-trading metrics are stable.
3. Never let an LLM directly decide position size from total wallet balance.
4. Treat every model output as untrusted text. Parse it strictly and cap risk in code.
5. Track every recommendation, skipped trade, simulated fill, and outcome.
6. Check the current Terms of Service and your local rules before trading. Some jurisdictions can view market data but cannot trade through certain Polymarket products.

## Beginner Build Roadmap

### Phase 1: Research Dashboard

Goal: read markets, rank opportunities, and produce a short evidence memo. No wallet. No private key. No trades.

Build:

- Fetch active markets from Gamma.
- Filter by liquidity, volume, spread, closing date, and category.
- Pull order book or midpoint from CLOB public endpoints.
- Generate a research memo with:
  - market question
  - current implied probability
  - resolution criteria
  - major evidence for/against
  - base rate
  - uncertainty level
  - final probability estimate
  - "no trade" by default

Success metric:

- You can explain why the agent selected each market and what evidence it used.

### Phase 2: Forecast Journal

Goal: prove the agent can forecast before it can trade.

Build:

- Store every forecast in SQLite or a CSV.
- Re-check markets after resolution.
- Score forecasts using Brier score and calibration buckets.
- Compare the agent forecast against the market price at forecast time.

Success metric:

- The agent is at least calibrated and does not only produce confident-sounding guesses.

### Phase 3: Paper Trading

Goal: simulate fills and P&L without touching funds.

Build:

- Simulated wallet balance.
- Simulated orders using current best bid/ask and spread assumptions.
- Position limits per market and per category.
- Full paper P&L, drawdown, and exposure tracking.

Success metric:

- Positive paper performance after realistic spread/slippage assumptions, across many markets, with controlled drawdown.

### Phase 4: Human-Approved Execution

Goal: keep the model out of the final control loop.

Build:

- The agent proposes a trade.
- Code enforces hard limits.
- A human approves.
- Only then call authenticated CLOB trading.

Required guardrails:

- max dollars per trade
- max open exposure
- max daily loss
- minimum liquidity
- maximum spread
- denylist for ambiguous resolution criteria
- manual approval for every order

### Phase 5: Limited Automation

Goal: automate only strategies that survived paper trading and human review.

Build:

- Strategy-specific execution rules.
- Kill switch.
- Monitoring and alerting.
- Post-trade reconciliation.
- Automated cancellation of stale orders.

## What I Would Build First

Start with a narrow research agent:

1. `fetch_markets`: get active markets.
2. `rank_markets`: filter for liquid, narrow-spread, near-term markets.
3. `research_market`: collect public context and resolution rules.
4. `forecast_market`: produce probability plus uncertainty.
5. `journal_forecast`: persist the forecast.

Avoid autonomous execution until the journal proves the forecasts are useful.

## Useful Sources

- Polymarket API docs: `https://docs.polymarket.com/api-reference`
- Gamma API docs: `https://docs.polymarket.com/developers/gamma-markets-api/overview`
- Geographic restrictions help page: `https://help.polymarket.com/en/articles/13364163-geographic-restrictions`
- Archived agents repo: `https://github.com/Polymarket/agents`
