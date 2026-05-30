# Local chat-agent shell (Milestone 5)

Polybot ships a small interactive chat shell that maps plain-English messages
to the existing safe research and paper-trading tools. There is **no LLM yet**:
intents are parsed by deterministic rules so behavior is fully testable and the
safety boundary is obvious.

The chat layer never reads a wallet, never authenticates to trading endpoints,
and never places a real order. Any trade it records is labeled
`PAPER/SIMULATED`.

## Run the REPL

```bash
python3 scripts/python/chat_agent.py
```

Optional flags (all paths default to the same files the CLI and operator UI
use):

```bash
python3 scripts/python/chat_agent.py \
  --forecast-db-path local_db_research.sqlite3 \
  --paper-db-path local_db_paper_trading.sqlite3 \
  --runs-db-path local_db_agent_runs.sqlite3 \
  --timeout 20
```

Type `help` to see supported commands, `exit` or `quit` to leave.

## Use chat in the operator UI

The operator UI exposes the same chat agent through a local endpoint and chat
panel:

```bash
python3 scripts/python/operator_ui.py
```

Open:

```text
http://localhost:8765
```

The browser only sends `{ "message": "..." }` to `POST /api/chat`. Python owns
the intent parsing and tool dispatch through `ChatAgent.handle(...)`; the UI
only displays the response. Chat history is kept in the browser for the current
page session only.

## Supported intents

| Intent             | Example message                                              | Needs network? |
| ------------------ | ------------------------------------------------------------ | -------------- |
| `SHOW_FORECASTS`   | `show forecasts`                                             | No             |
| `SHOW_PORTFOLIO`   | `show my paper portfolio`                                    | No             |
| `SHOW_HISTORY`     | `show paper history`                                         | No             |
| `SHOW_RUNS`        | `show agent runs`                                            | No             |
| `SCAN_MARKETS`     | `scan markets` (optionally `limit 20`)                       | Yes            |
| `RESEARCH_MARKET`  | `research market 540819`                                     | Yes            |
| `RUN_PAPER_AGENT`  | `run paper agent for market 540819 at price 0.57 size 10`    | Yes            |
| `HELP`             | `help`                                                       | No             |
| `UNKNOWN`          | anything else (responds with the help text)                  | No             |

Offline intents work with no third-party packages. Network intents import the
`httpx`-backed market client lazily and surface a clear error if `httpx` is
missing or the Polymarket public API is unreachable.

## Architecture

- `agents/chat/intents.py` — `Intent`, `ParsedIntent`, `parse_intent(...)`.
  Stdlib-only; conservative parsing. When a required value is missing the
  matching intent is returned with `missing` populated and a `reason` string,
  not silently filled in.
- `agents/chat/agent.py` — `ChatAgent.handle(message)` returns a
  `ChatResponse(text, intent, ok, data)`. The agent composes the existing
  `ForecastJournal`, `PaperTradingJournal`, `AgentRunsJournal`,
  `MarketDataClient`, `build_memo` / `render_memo`, `rank_markets`, and
  `run_paper_agent`. A `market_client_factory` can be injected for tests or a
  future non-`httpx` client.
- `scripts/python/chat_agent.py` — minimal REPL with injectable I/O so the
  loop is testable.

Both the parser and the agent are reusable: a future web chat UI can call
`ChatAgent.handle(...)` directly and render the structured `ChatResponse.data`.

## Safety

- No wallet, no private keys, no authenticated CLOB calls, no real orders.
- Network calls are read-only against the public Gamma API.
- Every paper-trade reply carries the `PAPER/SIMULATED` label.
- `UNKNOWN` and missing-value responses point the user back to `help` instead
  of guessing.
