# Claude Agent Build Brief

You are helping build a beginner-safe Polymarket research agent inside this repository.

The user is new to Polymarket and trading. Prioritize learning, observability, and risk control over speed or autonomous trading. Do not add live trading in the first milestone.

## Non-Negotiable Constraints

- Do not request, store, or use `POLYGON_WALLET_PRIVATE_KEY`.
- Do not call authenticated CLOB trading endpoints.
- Do not uncomment or invoke `execute_market_order`.
- Do not add autonomous real-money execution.
- Use public market data only.
- Every recommendation must default to `NO_TRADE` unless the system has enough structured evidence.
- Treat LLM output as untrusted. Validate and parse strictly.

## First Milestone

Build a research-only CLI workflow:

```bash
python scripts/python/research_cli.py scan --limit 10
python scripts/python/research_cli.py research --market-id <id>
python scripts/python/research_cli.py journal --market-id <id> --probability 0.57 --notes "..."
python scripts/python/research_cli.py list-journal
```

The workflow should:

1. Fetch active markets from the public Gamma API.
2. Rank markets by basic quality filters.
3. Produce a plain-English research memo for a selected market.
4. Store forecasts locally in SQLite.
5. Never place trades.

## Suggested Files

Create new files rather than modifying the archived trading path heavily:

- `agents/research/__init__.py`
- `agents/research/market_data.py`
- `agents/research/ranking.py`
- `agents/research/journal.py`
- `agents/research/memo.py`
- `scripts/python/research_cli.py`
- `tests/test_research_journal.py`
- `tests/test_research_ranking.py`

Keep the existing archived files mostly intact unless a small import fix is required.

## Data Model

SQLite table: `forecasts`

Fields:

- `id`: integer primary key
- `market_id`: text
- `question`: text
- `outcome`: text, default `Yes`
- `market_probability`: real nullable
- `forecast_probability`: real
- `confidence`: text, one of `low`, `medium`, `high`
- `recommendation`: text, one of `NO_TRADE`, `WATCH`, `PAPER_TRADE`
- `notes`: text
- `created_at`: ISO datetime

## Market Ranking

Start simple and transparent:

- include active markets only
- exclude closed/archived/restricted markets when those fields are present
- prefer markets with:
  - non-empty question
  - non-empty outcomes
  - non-empty outcome prices
  - higher liquidity/volume if available
  - lower spread if available
  - future end date if available

Return a score with reason strings, not just a number.

## Research Memo Format

Output a readable memo:

```text
Market: ...
Market ID: ...
Resolution / Description: ...
Current Implied Probability: ...

Evidence For:
- ...

Evidence Against:
- ...

Unknowns:
- ...

Forecast:
- Probability: ...
- Confidence: low|medium|high
- Recommendation: NO_TRADE|WATCH|PAPER_TRADE

Reasoning:
...
```

If no LLM key is present, still generate a deterministic memo from available market metadata.

## Implementation Notes

- Prefer `httpx` for API reads because the repo already uses it.
- Prefer standard-library `sqlite3` for the first journal.
- Keep functions small and testable.
- Add type hints where practical.
- Avoid broad dependency upgrades in the first pass.
- Avoid requiring Python 3.9-specific tooling for this first milestone if the code can run on the local Python.

## Verification

Run:

```bash
python -m pytest tests/test_research_journal.py tests/test_research_ranking.py
python scripts/python/research_cli.py scan --limit 5
```

If live API access is unavailable, mock the API calls in tests and make the CLI fail gracefully with a useful error.

## Definition Of Done

The milestone is done when a beginner can:

1. List candidate markets.
2. Read a research memo.
3. Save a forecast.
4. Review saved forecasts.
5. Confirm that no wallet/private key/live trade path is involved.
