"""Risk guardrails for paper (simulated) trading.

This module is pure and dependency-light: standard library only, no ``httpx``,
no database, no wallet, no network. It exists to answer one question before any
*simulated* trade is recorded: "Is this within the configured safety limits?"

These guardrails exist to protect a beginner learning the mechanics of a
prediction market. Nothing here can move real money — it only governs entries in
a local SQLite paper-trading journal. Even so, the limits are intentionally
conservative and every rejection comes with a plain-English reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Small tolerance so floating-point dust (e.g. 10.000000001) does not trip a
# limit that the user clearly intended to hit exactly.
_EPS = 1e-9


@dataclass(frozen=True)
class RiskLimits:
    """Configurable safety limits for paper trading.

    Defaults match the Milestone 2 specification. All monetary values are in
    USDC (simulated — no real currency is ever involved).
    """

    max_trade_size_usdc: float = 10.0
    max_exposure_per_market_usdc: float = 25.0
    max_total_open_exposure_usdc: float = 100.0
    min_price: float = 0.01
    max_price: float = 0.99

    def describe(self) -> list[str]:
        """Return human-readable limit descriptions for display in the CLI."""
        return [
            f"max paper trade size: {self.max_trade_size_usdc:g} USDC",
            f"max exposure per market: {self.max_exposure_per_market_usdc:g} USDC",
            f"max total open exposure: {self.max_total_open_exposure_usdc:g} USDC",
            f"allowed price range: {self.min_price:g}..{self.max_price:g}",
        ]


# A shared default instance so callers can rely on stable defaults.
DEFAULT_LIMITS = RiskLimits()


@dataclass
class RiskDecision:
    """The outcome of a guardrail check.

    ``approved`` is ``True`` only when there are no violations. ``reasons`` always
    explains the decision, whether approved or rejected.
    """

    approved: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return not self.approved


def _check_price(price: float, limits: RiskLimits, violations: list[str]) -> None:
    if price is None:
        violations.append("price is required.")
        return
    if not (limits.min_price - _EPS <= price <= limits.max_price + _EPS):
        violations.append(
            f"price {price:g} is outside the allowed range "
            f"{limits.min_price:g}..{limits.max_price:g}."
        )


def evaluate_paper_buy(
    price: float,
    size_usdc: float,
    current_market_exposure: float,
    current_total_exposure: float,
    limits: RiskLimits = DEFAULT_LIMITS,
) -> RiskDecision:
    """Decide whether a simulated BUY is within the risk limits.

    Parameters
    ----------
    price:
        Entry price per share, must be within ``[min_price, max_price]``.
    size_usdc:
        USDC notional the user wants to commit on this single trade.
    current_market_exposure:
        Existing open exposure (USDC cost basis) for this market, before the trade.
    current_total_exposure:
        Existing open exposure (USDC cost basis) across all markets, before the trade.
    limits:
        The :class:`RiskLimits` to enforce.

    Returns
    -------
    RiskDecision
        Approved only if every check passes. All breached limits are reported,
        not just the first, so the user sees the full picture.
    """
    violations: list[str] = []

    _check_price(price, limits, violations)

    if size_usdc is None or size_usdc <= 0:
        violations.append("trade size must be a positive USDC amount.")
    else:
        if size_usdc > limits.max_trade_size_usdc + _EPS:
            violations.append(
                f"trade size {size_usdc:g} USDC exceeds the max paper trade size "
                f"of {limits.max_trade_size_usdc:g} USDC."
            )

        projected_market = current_market_exposure + size_usdc
        if projected_market > limits.max_exposure_per_market_usdc + _EPS:
            violations.append(
                f"this trade would raise market exposure to {projected_market:g} USDC, "
                f"above the per-market limit of "
                f"{limits.max_exposure_per_market_usdc:g} USDC."
            )

        projected_total = current_total_exposure + size_usdc
        if projected_total > limits.max_total_open_exposure_usdc + _EPS:
            violations.append(
                f"this trade would raise total open exposure to {projected_total:g} "
                f"USDC, above the overall limit of "
                f"{limits.max_total_open_exposure_usdc:g} USDC."
            )

    if violations:
        return RiskDecision(approved=False, reasons=violations)
    return RiskDecision(
        approved=True,
        reasons=["within all paper-trading risk limits."],
    )


def evaluate_paper_sell(
    price: float,
    shares_to_sell: float,
    shares_held: float,
    limits: RiskLimits = DEFAULT_LIMITS,
) -> RiskDecision:
    """Decide whether a simulated SELL is allowed.

    Selling reduces risk, so the exposure and trade-size limits do not apply.
    We only require a valid price, a positive amount, and that the user is not
    selling more shares than they hold (no short selling in this foundation).
    """
    violations: list[str] = []

    _check_price(price, limits, violations)

    if shares_to_sell is None or shares_to_sell <= 0:
        violations.append("shares to sell must be a positive amount.")
    elif shares_to_sell > shares_held + _EPS:
        violations.append(
            f"cannot sell {shares_to_sell:g} shares; only {shares_held:g} held "
            "(short selling is not supported in paper trading)."
        )

    if violations:
        return RiskDecision(approved=False, reasons=violations)
    return RiskDecision(approved=True, reasons=["sell is within paper-trading rules."])
