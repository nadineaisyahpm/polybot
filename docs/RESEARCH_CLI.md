# Research CLI (public data only) + paper trading (simulated only)

A beginner-safe workflow for Polymarket. It fetches **public** market data,
ranks markets transparently, prints deterministic research memos, journals
forecasts to local SQLite, and supports **simulated** paper trading with risk
guardrails. It never reads a wallet/private key, never authenticates to trading
endpoints, and never places a real trade.

## Dependency model (important)

The package is split so that offline work needs **zero third-party packages**:

| Module                         | Requires `httpx`? | Purpose                                  |
| ------------------------------ | ----------------- | ---------------------------------------- |
| `agents/research/models.py`    | No                | Pure data models + parsing (stdlib only) |
| `agents/research/ranking.py`   | No                | Market scoring with reasons              |
| `agents/research/memo.py`      | No                | Deterministic research memos             |
| `agents/research/journal.py`   | No                | SQLite forecast journal (stdlib only)    |
| `agents/research/risk.py`      | No                | Paper-trading risk guardrails (stdlib)   |
| `agents/research/paper_trading.py` | No            | SQLite paper-trade ledger (stdlib only)  |
| `agents/research/connector.py` | No                | Forecast -> risk -> paper-trade bridge   |
| `scripts/python/operator_ui.py` | No               | Read-only local operator UI              |
| `agents/research/market_data.py` | **Yes**         | Public Gamma API client (network)        |

`agents/research/__init__.py` uses lazy imports (PEP 562), so
`import agents.research` does **not** import `httpx`. Only accessing
`MarketDataClient` triggers the network module. In the CLI, `httpx` is imported
lazily inside the `scan` and `research` commands (and the optional metadata
enrichment in `journal`). As a result:

- `journal`, `list-journal`, and **all `paper-*` commands** run with **no
  third-party packages installed**.
- `scan` and `research` require `httpx`. If it is missing, you get a clear
  message ("The 'httpx' package is required...") instead of a traceback.

## Exact interpreter / venv setup used

This milestone was developed and verified with:

- **Interpreter:** CPython **3.12.6** (`/usr/local/bin/python3` on macOS).
- **Standard library only** for `models`, `ranking`, `memo`, `journal`, and the
  `journal` / `list-journal` CLI commands. No venv is strictly required to run
  those.
- **`httpx`** (already in `requirements.txt`, observed `0.28.1`) for the network
  commands `scan` and `research`.
- **Tests** run with the standard-library `unittest` (no dependencies) and also
  pass under `pytest` if you have it.

> The repo's main `README.md` suggests `python3.9`. The research CLI is written
> to run on the local interpreter (3.12 here) and does not require 3.9-specific
> tooling, per the build brief.

### Option A — minimal, no venv (offline commands only)

If you only want to journal and review forecasts, any Python 3.9+ works with no
installs:

```bash
export PYTHONPATH="."          # so 'agents' is importable
python3 scripts/python/research_cli.py journal --market-id 253123 --probability 0.57 --notes "first read"
python3 scripts/python/research_cli.py list-journal
```

(The CLI also injects the repo root into `sys.path` itself, so `PYTHONPATH` is
optional when invoking it directly. Set it if you import the package elsewhere.)

### Option B — virtual environment (recommended, enables scan/research)

```bash
# 1. Create and activate a venv with the local Python 3 interpreter
python3 -m venv .venv
source .venv/bin/activate            # macOS/Linux
# .venv\Scripts\activate            # Windows

# 2. Install only what the research CLI needs for the network commands
pip install httpx                    # minimal
# or install the full repo stack:
# pip install -r requirements.txt

# 3. (optional) install pytest to run tests with pytest
pip install pytest
```

Confirm the interpreter:

```bash
python -V        # expect Python 3.12.x (or your local 3.9+)
python -c "import httpx; print(httpx.__version__)"
```

## Commands

```bash
# List and rank active public markets (needs httpx)
python scripts/python/research_cli.py scan --limit 10
python scripts/python/research_cli.py scan --limit 10 --reasons

# Optional: stricter filter that drops trading-restricted markets
python scripts/python/research_cli.py scan --limit 10 --exclude-restricted

# Print a deterministic research memo for one market (needs httpx)
python scripts/python/research_cli.py research --market-id <id>

# Save a forecast locally (works offline, no httpx required)
python scripts/python/research_cli.py journal --market-id <id> --probability 0.57 --notes "..."

# Review saved forecasts (works offline, no httpx required)
python scripts/python/research_cli.py list-journal
```

The SQLite database defaults to `local_db_research.sqlite3` (matching the
`local_db*` entry in `.gitignore`). Override with `--db-path`.

> Note on restricted markets: on Polymarket, nearly every active market is
> flagged `restricted: true`, which reflects **trading** jurisdiction limits
> (e.g. the US), not market quality. Because this tool is read-only research and
> never trades, `scan` includes restricted markets by default and adds a
> transparency note. Pass `--exclude-restricted` for the stricter behavior.
>
> Use `--timeout <seconds>` on `scan`/`research` to bound slow network calls.

## Paper trading (Milestone 2 — SIMULATED only, no live trading)

The `paper-*` commands record **simulated** trades to a separate local SQLite
ledger (`local_db_paper_trading.sqlite3`). They never place a real order, never
touch a wallet/private key, and never call authenticated CLOB endpoints. Every
output is labeled `PAPER/SIMULATED`.

Risk guardrails are enforced **before** any simulated trade is recorded. A
rejected trade is never written to the ledger. Default limits:

- max paper trade size: **10 USDC**
- max exposure per market: **25 USDC**
- max total open exposure: **100 USDC**
- price must be within **0.01..0.99**
- no short selling (you cannot sell more shares than you hold)

```bash
# Open a simulated position (risk-checked)
python scripts/python/research_cli.py paper-buy --market-id <id> --price 0.55 --size 10 --notes "thesis"

# Reduce/close a simulated position (realizes P&L)
python scripts/python/research_cli.py paper-sell --market-id <id> --price 0.60 --shares 5

# View open simulated positions, exposure, and realized P&L
python scripts/python/research_cli.py paper-portfolio

# View the simulated trade ledger (most recent first)
python scripts/python/research_cli.py paper-history
```

### Connecting research decisions to paper trades (Milestone 2.5)

`paper-buy`/`paper-sell` are *manual* paper trades. To close the loop from
research, use `paper-from-forecast`, which records a simulated buy directly from
a saved forecast and stamps it with provenance:

```bash
# Requires the forecast's recommendation to be PAPER_TRADE
python scripts/python/research_cli.py paper-from-forecast --forecast-id <id> --price 0.57 --size 10

# Force a paper trade from a non-PAPER_TRADE forecast (logged as a manual override)
python scripts/python/research_cli.py paper-from-forecast --forecast-id <id> --price 0.57 --size 10 --override
```

Rules enforced by the connector:

- The forecast must exist in the research journal (`--forecast-db-path`,
  defaults to `local_db_research.sqlite3`).
- The forecast recommendation must be `PAPER_TRADE`, or you must pass
  `--override`. An override is recorded honestly as `manual_override`.
- Question, market id, outcome, and confidence are copied from the forecast into
  the paper ledger; notes are merged.
- The paper trade carries a `source` (`agent_recommended`, `manual_override`, or
  `manual`) and a `forecast_id`, so the full chain is visible in `paper-history`:

```text
Market -> Memo -> Forecast -> Risk Decision -> Paper Trade -> P&L
```

Existing Milestone 2 paper databases are migrated automatically (the
`source`/`forecast_id`/`confidence` columns are added in place); legacy rows
default to `manual` provenance.

## Read-only operator UI (Milestone 3)

Run a local browser UI over the forecast journal and paper-trading ledger:

```bash
python3 scripts/python/operator_ui.py
```

Then open:

```text
http://localhost:8765
```

The UI shows saved forecasts, open simulated positions, paper trade history,
aggregate exposure, realized paper P&L, and risk limits. It is read-only: it
does not create forecasts, record trades, read wallet fields, or call trading
APIs.

Optional paths:

```bash
python3 scripts/python/operator_ui.py \
  --forecast-db-path local_db_research.sqlite3 \
  --paper-db-path local_db_paper_trading.sqlite3 \
  --port 8765
```

How it works:

- The ledger is **append-only**. Positions, exposure, average cost, and realized
  P&L are *derived* by replaying the ledger, so the stored history is the single
  source of truth and is fully auditable.
- Cost basis uses the average-cost method; "open exposure" is the remaining cost
  basis of an open position (USDC committed and not yet sold).
- The recommendation surface from research stays `NO_TRADE` by default;
  `PAPER_TRADE` is a practice-only label and never triggers a real order.

## Verification

```bash
# Tests (no third-party deps required)
python3 -m unittest tests.test_research_ranking tests.test_research_journal \
    tests.test_research_risk tests.test_research_paper_trading \
    tests.test_research_connector tests.test_operator_ui

# Or discover all research tests at once
python3 -m unittest discover -s tests -p "test_research_*.py"

# Tests under pytest (if installed), matching the build brief
python3 -m pytest tests/test_research_journal.py tests/test_research_ranking.py \
    tests/test_research_risk.py tests/test_research_paper_trading.py \
    tests/test_research_connector.py tests/test_operator_ui.py

# Smoke test the live scan (requires network + httpx)
python3 scripts/python/research_cli.py scan --limit 5

# Smoke test paper trading (offline, no httpx)
python3 scripts/python/research_cli.py paper-buy --market-id demo --price 0.5 --size 10
python3 scripts/python/research_cli.py paper-portfolio
python3 scripts/python/research_cli.py paper-history
```

## Safety

- No `POLYGON_WALLET_PRIVATE_KEY` is read, stored, or required.
- No authenticated CLOB trading endpoints are called.
- `execute_market_order` is never invoked.
- No real order placement, wallet support, or private key handling exists in any
  research or paper-trading module.
- Paper trading is **simulated only** and clearly labeled `PAPER/SIMULATED`;
  risk guardrails are enforced before any simulated trade is recorded.
- Every recommendation defaults to `NO_TRADE` unless there is enough structured
  evidence to justify `WATCH` or `PAPER_TRADE` (a practice-only label).
