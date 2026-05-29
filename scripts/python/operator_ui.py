#!/usr/bin/env python3
"""Read-only local operator UI for the research and paper-trading journals.

This is a local dashboard only. It never reads a wallet/private key, never calls
authenticated trading endpoints, and never records trades. It exposes the local
SQLite forecast journal and paper-trading ledger through read-only JSON
endpoints and a compact browser UI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

# Ensure the repo root is importable when run directly as a script.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

FORECAST_DB_PATH = "local_db_research.sqlite3"
PAPER_DB_PATH = "local_db_paper_trading.sqlite3"
PAPER_LABEL = "PAPER/SIMULATED"

if TYPE_CHECKING:
    from agents.research.journal import ForecastRecord
    from agents.research.paper_trading import PaperTrade, Position


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def forecast_to_dict(record: ForecastRecord) -> dict[str, Any]:
    """Serialize a forecast journal record for JSON responses."""
    return {
        "id": record.id,
        "market_id": record.market_id,
        "question": record.question,
        "outcome": record.outcome,
        "market_probability": _as_float(record.market_probability),
        "forecast_probability": float(record.forecast_probability),
        "confidence": record.confidence,
        "recommendation": record.recommendation,
        "notes": record.notes,
        "created_at": record.created_at,
    }


def position_to_dict(position: Position) -> dict[str, Any]:
    """Serialize a derived paper position for JSON responses."""
    return {
        "market_id": position.market_id,
        "question": position.question,
        "outcome": position.outcome,
        "shares": float(position.shares),
        "average_price": _as_float(position.average_price),
        "exposure": float(position.exposure),
        "realized_pnl": float(position.realized_pnl),
    }


def trade_to_dict(trade: PaperTrade) -> dict[str, Any]:
    """Serialize a paper-trade ledger row for JSON responses."""
    return {
        "id": trade.id,
        "market_id": trade.market_id,
        "question": trade.question,
        "outcome": trade.outcome,
        "side": trade.side,
        "price": float(trade.price),
        "shares": float(trade.shares),
        "size_usdc": float(trade.size_usdc),
        "realized_pnl": float(trade.realized_pnl),
        "notes": trade.notes,
        "simulated": bool(trade.simulated),
        "created_at": trade.created_at,
        "source": trade.source,
        "forecast_id": trade.forecast_id,
        "confidence": trade.confidence,
        "from_agent": trade.from_agent,
    }


def _parse_limit(query: dict[str, list[str]], default: int = 50) -> int:
    raw = query.get("limit", [str(default)])[0]
    try:
        limit = int(raw)
    except ValueError:
        return default
    return max(1, min(limit, 250))


def build_payload(path: str, query: dict[str, list[str]], app: "OperatorApp") -> Any:
    """Build a JSON-serializable response for an API path."""
    if path == "/api/health":
        return {
            "ok": True,
            "paper_label": PAPER_LABEL,
            "forecast_db_path": app.forecast_db_path,
            "paper_db_path": app.paper_db_path,
        }

    if path == "/api/risk-limits":
        from agents.research.risk import DEFAULT_LIMITS

        return {
            "label": PAPER_LABEL,
            "limits": DEFAULT_LIMITS.describe(),
            "values": {
                "max_trade_size_usdc": DEFAULT_LIMITS.max_trade_size_usdc,
                "max_exposure_per_market_usdc": (
                    DEFAULT_LIMITS.max_exposure_per_market_usdc
                ),
                "max_total_open_exposure_usdc": (
                    DEFAULT_LIMITS.max_total_open_exposure_usdc
                ),
                "min_price": DEFAULT_LIMITS.min_price,
                "max_price": DEFAULT_LIMITS.max_price,
            },
        }

    if path == "/api/forecasts":
        from agents.research.journal import ForecastJournal

        limit = _parse_limit(query)
        with ForecastJournal(app.forecast_db_path) as journal:
            return {
                "forecasts": [
                    forecast_to_dict(record)
                    for record in journal.list_forecasts(limit=limit)
                ]
            }

    if path == "/api/paper/portfolio":
        from agents.research.paper_trading import PaperTradingJournal

        with PaperTradingJournal(app.paper_db_path) as journal:
            positions = journal.get_open_positions()
            return {
                "paper_label": PAPER_LABEL,
                "positions": [position_to_dict(pos) for pos in positions],
                "total_open_exposure": float(journal.total_exposure()),
                "total_realized_pnl": float(journal.total_realized_pnl()),
            }

    if path == "/api/paper/history":
        from agents.research.paper_trading import PaperTradingJournal

        limit = _parse_limit(query)
        with PaperTradingJournal(app.paper_db_path) as journal:
            return {
                "paper_label": PAPER_LABEL,
                "trades": [
                    trade_to_dict(trade) for trade in journal.history(limit=limit)
                ],
            }

    if path == "/api/summary":
        from agents.research.journal import ForecastJournal
        from agents.research.paper_trading import PaperTradingJournal

        with ForecastJournal(app.forecast_db_path) as forecasts:
            forecast_rows = forecasts.list_forecasts(limit=250)
        with PaperTradingJournal(app.paper_db_path) as paper:
            positions = paper.get_open_positions()
            trades = paper.history(limit=250)
            return {
                "paper_label": PAPER_LABEL,
                "forecast_count": len(forecast_rows),
                "open_position_count": len(positions),
                "trade_count": len(trades),
                "total_open_exposure": float(paper.total_exposure()),
                "total_realized_pnl": float(paper.total_realized_pnl()),
                "agent_trade_count": sum(1 for trade in trades if trade.from_agent),
                "manual_override_count": sum(
                    1 for trade in trades if trade.source == "manual_override"
                ),
            }

    raise KeyError(path)


class OperatorApp:
    """Configuration holder for the local UI server."""

    def __init__(self, forecast_db_path: str, paper_db_path: str) -> None:
        self.forecast_db_path = forecast_db_path
        self.paper_db_path = paper_db_path


def make_handler(app: OperatorApp):
    """Create a request handler bound to an :class:`OperatorApp`."""

    class OperatorHandler(BaseHTTPRequestHandler):
        server_version = "PolybotOperatorUI/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
                return
            if parsed.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            if parsed.path.startswith("/api/"):
                try:
                    payload = build_payload(parsed.path, parse_qs(parsed.query), app)
                except KeyError:
                    self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                    return
                except Exception as err:  # Keep the UI responsive with clear errors.
                    self._send_json(
                        {"error": f"{type(err).__name__}: {err}"},
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._send_json(payload)
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            # Keep server output quiet except for explicit startup messages.
            return

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(
            self, payload: Any, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            body = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return OperatorHandler


def run_server(host: str, port: int, app: OperatorApp) -> None:
    """Run the local operator UI server until interrupted."""
    server = ThreadingHTTPServer((host, port), make_handler(app))
    url_host = "localhost" if host in {"127.0.0.1", "0.0.0.0"} else host
    print(f"Operator UI: http://{url_host}:{port}", flush=True)
    print("Mode: read-only research + PAPER/SIMULATED ledger", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a read-only local operator UI for forecasts and paper trades. "
            "No wallet, no private key, no live trading."
        )
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind.")
    parser.add_argument("--port", type=int, default=8765, help="Port to bind.")
    parser.add_argument(
        "--forecast-db-path",
        default=FORECAST_DB_PATH,
        help="Forecast journal SQLite path.",
    )
    parser.add_argument(
        "--paper-db-path",
        default=PAPER_DB_PATH,
        help="Paper-trading SQLite path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = OperatorApp(
        forecast_db_path=args.forecast_db_path,
        paper_db_path=args.paper_db_path,
    )
    run_server(args.host, args.port, app)
    return 0


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polybot Operator</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --surface: #ffffff;
      --surface-2: #eef2f6;
      --line: #d6dde7;
      --text: #18202b;
      --muted: #657285;
      --accent: #0f766e;
      --accent-2: #7c3aed;
      --warn: #b45309;
      --bad: #b91c1c;
      --good: #047857;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--surface);
    }
    .wrap {
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
    }
    .topbar {
      min-height: 72px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
    }
    h1 {
      font-size: 22px;
      line-height: 1.15;
      margin: 0;
      font-weight: 720;
    }
    .subtitle {
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
    }
    .pillrow {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .pill {
      min-height: 30px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    main {
      padding: 22px 0 40px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .stat {
      background: var(--surface);
      border: 1px solid var(--line);
      padding: 14px;
      min-height: 82px;
    }
    .stat label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }
    .stat strong {
      display: block;
      font-size: 24px;
      line-height: 1;
      font-weight: 760;
    }
    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 16px;
      align-items: start;
    }
    section {
      background: var(--surface);
      border: 1px solid var(--line);
      margin-bottom: 16px;
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      gap: 12px;
    }
    h2 {
      font-size: 15px;
      line-height: 1.2;
      margin: 0;
      font-weight: 720;
    }
    button {
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--text);
      min-height: 32px;
      padding: 0 11px;
      font: inherit;
      font-size: 13px;
      cursor: pointer;
    }
    button:hover { border-color: var(--accent); }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      text-align: left;
      vertical-align: top;
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    th {
      color: var(--muted);
      background: #fbfcfe;
      font-weight: 650;
      font-size: 12px;
    }
    tr:last-child td { border-bottom: 0; }
    .muted { color: var(--muted); }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    .tag {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    .tag.good { color: var(--good); border-color: #a7f3d0; background: #ecfdf5; }
    .tag.warn { color: var(--warn); border-color: #fed7aa; background: #fff7ed; }
    .tag.bad { color: var(--bad); border-color: #fecaca; background: #fef2f2; }
    .list {
      padding: 12px 14px;
      display: grid;
      gap: 10px;
    }
    .rowitem {
      border: 1px solid var(--line);
      padding: 11px;
      background: #fbfcfe;
    }
    .rowtop {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
    }
    .empty {
      padding: 18px 14px;
      color: var(--muted);
      font-size: 13px;
    }
    .error {
      margin-bottom: 16px;
      border: 1px solid #fecaca;
      background: #fef2f2;
      color: var(--bad);
      padding: 12px 14px;
      display: none;
      font-size: 13px;
    }
    @media (max-width: 920px) {
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .layout { grid-template-columns: 1fr; }
      .topbar { align-items: flex-start; flex-direction: column; padding: 14px 0; }
      .pillrow { justify-content: flex-start; }
    }
    @media (max-width: 560px) {
      .wrap { width: min(100vw - 20px, 1180px); }
      .stats { grid-template-columns: 1fr; }
      th, td { padding: 9px 8px; font-size: 12px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="wrap topbar">
      <div>
        <h1>Polybot Operator</h1>
        <div class="subtitle">Read-only forecasts and PAPER/SIMULATED ledger</div>
      </div>
      <div class="pillrow">
        <span class="pill">No wallet</span>
        <span class="pill">No live trading</span>
        <span class="pill" id="updated">Loading</span>
      </div>
    </div>
  </header>
  <main class="wrap">
    <div id="error" class="error"></div>
    <div class="stats">
      <div class="stat"><label>Forecasts</label><strong id="forecast-count">0</strong></div>
      <div class="stat"><label>Open Positions</label><strong id="position-count">0</strong></div>
      <div class="stat"><label>Paper Trades</label><strong id="trade-count">0</strong></div>
      <div class="stat"><label>Open Exposure</label><strong id="exposure">$0.00</strong></div>
      <div class="stat"><label>Realized P&L</label><strong id="pnl">$0.00</strong></div>
    </div>
    <div class="layout">
      <div>
        <section>
          <div class="section-head">
            <h2>Forecast Journal</h2>
            <button id="refresh">Refresh</button>
          </div>
          <div style="overflow:auto">
            <table>
              <thead>
                <tr>
                  <th style="width:70px">ID</th>
                  <th>Market</th>
                  <th style="width:95px">Forecast</th>
                  <th style="width:120px">Recommendation</th>
                  <th style="width:105px">Confidence</th>
                </tr>
              </thead>
              <tbody id="forecasts"></tbody>
            </table>
          </div>
        </section>
        <section>
          <div class="section-head">
            <h2>Paper Trade History</h2>
            <span class="tag">PAPER/SIMULATED</span>
          </div>
          <div id="history" class="list"></div>
        </section>
      </div>
      <aside>
        <section>
          <div class="section-head">
            <h2>Paper Portfolio</h2>
            <span class="tag">Simulated</span>
          </div>
          <div id="positions" class="list"></div>
        </section>
        <section>
          <div class="section-head">
            <h2>Risk Limits</h2>
          </div>
          <div id="limits" class="list"></div>
        </section>
      </aside>
    </div>
  </main>
  <script>
    const $ = (id) => document.getElementById(id);
    const money = (n) => `$${Number(n || 0).toFixed(2)}`;
    const pct = (n) => n === null || n === undefined ? "n/a" : `${Math.round(Number(n) * 100)}%`;
    const text = (value) => value === null || value === undefined || value === "" ? "n/a" : String(value);
    const esc = (value) => text(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
    const tagClass = (value) => {
      if (value === "PAPER_TRADE" || value === "agent_recommended") return "good";
      if (value === "WATCH" || value === "manual_override") return "warn";
      if (value === "NO_TRADE") return "bad";
      return "";
    };

    async function getJSON(path) {
      const response = await fetch(path, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || response.statusText);
      return body;
    }

    function renderForecasts(rows) {
      const target = $("forecasts");
      if (!rows.length) {
        target.innerHTML = `<tr><td colspan="5" class="muted">No forecasts saved yet.</td></tr>`;
        return;
      }
      target.innerHTML = rows.map((row) => `
        <tr>
          <td class="mono">#${row.id}</td>
          <td>
            <strong>${esc(row.question || row.market_id)}</strong>
            <div class="muted mono">${esc(row.market_id)}</div>
          </td>
          <td>${pct(row.forecast_probability)}</td>
          <td><span class="tag ${tagClass(row.recommendation)}">${esc(row.recommendation)}</span></td>
          <td>${esc(row.confidence)}</td>
        </tr>
      `).join("");
    }

    function renderPositions(rows) {
      const target = $("positions");
      if (!rows.length) {
        target.innerHTML = `<div class="empty">No open simulated positions.</div>`;
        return;
      }
      target.innerHTML = rows.map((row) => `
        <div class="rowitem">
          <div class="rowtop">
            <strong>${esc(row.market_id)}</strong>
            <span class="tag">${esc(row.outcome)}</span>
          </div>
          <div>${esc(row.question)}</div>
          <div class="muted">Shares ${Number(row.shares).toFixed(4)} · Avg ${pct(row.average_price)} · Exposure ${money(row.exposure)}</div>
        </div>
      `).join("");
    }

    function renderHistory(rows) {
      const target = $("history");
      if (!rows.length) {
        target.innerHTML = `<div class="empty">No simulated trades yet.</div>`;
        return;
      }
      target.innerHTML = rows.map((row) => `
        <div class="rowitem">
          <div class="rowtop">
            <strong>#${row.id} ${esc(row.side)} ${esc(row.market_id)}</strong>
            <span class="tag ${tagClass(row.source)}">${esc(row.source)}</span>
          </div>
          <div class="muted">
            ${esc(row.outcome)} · Price ${pct(row.price)} · Shares ${Number(row.shares).toFixed(4)} · Amount ${money(row.size_usdc)}
          </div>
          <div class="muted">
            P&L ${money(row.realized_pnl)} · Forecast ${row.forecast_id ? "#" + row.forecast_id : "n/a"} · ${esc(row.created_at)}
          </div>
        </div>
      `).join("");
    }

    function renderLimits(rows) {
      $("limits").innerHTML = rows.map((line) => `<div class="rowitem">${esc(line)}</div>`).join("");
    }

    async function refresh() {
      $("error").style.display = "none";
      try {
        const [summary, forecasts, portfolio, history, limits] = await Promise.all([
          getJSON("/api/summary"),
          getJSON("/api/forecasts?limit=50"),
          getJSON("/api/paper/portfolio"),
          getJSON("/api/paper/history?limit=50"),
          getJSON("/api/risk-limits"),
        ]);
        $("forecast-count").textContent = summary.forecast_count;
        $("position-count").textContent = summary.open_position_count;
        $("trade-count").textContent = summary.trade_count;
        $("exposure").textContent = money(summary.total_open_exposure);
        $("pnl").textContent = money(summary.total_realized_pnl);
        $("updated").textContent = new Date().toLocaleTimeString();
        renderForecasts(forecasts.forecasts);
        renderPositions(portfolio.positions);
        renderHistory(history.trades);
        renderLimits(limits.limits);
      } catch (error) {
        $("error").textContent = error.message;
        $("error").style.display = "block";
      }
    }

    $("refresh").addEventListener("click", refresh);
    refresh();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
