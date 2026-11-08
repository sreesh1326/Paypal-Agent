"""Individual guardrail rules for PayPal order validation.

Each rule is a callable that takes an OrderRequest (and context) and returns a RuleResult.
Rules are designed to be composable, configurable, and independently testable.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from guardrails.models import (
    Decision,
    OrderItem,
    OrderRequest,
    RiskLevel,
    RuleCategory,
    RuleResult,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GuardrailConfig:
    """Central configuration for all guardrail rules."""

    # Amount limits
    max_order_amount: float = 10_000.00
    high_amount_threshold: float = 5_000.00
    min_order_amount: float = 0.01

    # Daily spending limits
    daily_spending_limit: float = 25_000.00
    daily_spending_warning: float = 15_000.00

    # Velocity limits
    max_orders_per_minute: int = 3
    max_orders_per_hour: int = 20
    max_orders_per_day: int = 100

    # Currency
    allowed_currencies: list[str] = field(
        default_factory=lambda: ["USD", "EUR", "GBP", "CAD", "AUD", "JPY"]
    )

    # Items
    blocked_categories: list[str] = field(
        default_factory=lambda: ["weapons", "drugs", "counterfeit", "gambling"]
    )
    max_quantity_per_item: int = 999
    max_items_per_order: int = 50

    # Duplicate detection window (seconds)
    duplicate_window_seconds: int = 300  # 5 minutes

    # Geo restrictions
    blocked_countries: list[str] = field(
        default_factory=lambda: ["KP", "IR", "SY", "CU"]  # OFAC-sanctioned
    )

    # Risk score weights (must sum to 1.0 conceptually)
    risk_weights: dict[str, float] = field(
        default_factory=lambda: {
            "amount": 0.25,
            "velocity": 0.25,
            "currency": 0.10,
            "item": 0.15,
            "duplicate": 0.15,
            "geo": 0.10,
        }
    )


# ---------------------------------------------------------------------------
# In-memory ledger (for velocity / duplicate / daily-spend tracking)
# ---------------------------------------------------------------------------

class OrderLedger:
    """Tracks recent orders for velocity & duplicate checks.

    In production this would be backed by Redis or a database.
    For the hackathon demo it lives in-memory.
    """

    def __init__(self) -> None:
        self._orders: list[dict[str, Any]] = []
        self._daily_spend: defaultdict[str, float] = defaultdict(float)  # date-key → $

    def record(self, order: OrderRequest) -> None:
        fingerprint = self._fingerprint(order)
        self._orders.append(
            {
                "request_id": order.request_id,
                "timestamp": order.timestamp,
                "amount": order.total_amount,
                "currency": order.currency,
                "buyer_email": order.buyer_email,
                "fingerprint": fingerprint,
            }
        )
        day_key = order.timestamp.strftime("%Y-%m-%d")
        self._daily_spend[day_key] += order.total_amount

    def orders_since(self, since: datetime, buyer_email: str | None = None) -> list[dict]:
        results = [o for o in self._orders if o["timestamp"] >= since]
        if buyer_email:
            results = [o for o in results if o["buyer_email"] == buyer_email]
        return results

    def daily_spend(self, date: datetime | None = None) -> float:
        day_key = (date or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        return self._daily_spend.get(day_key, 0.0)

    def has_duplicate(self, order: OrderRequest, window_seconds: int) -> bool:
        fp = self._fingerprint(order)
        cutoff = order.timestamp - timedelta(seconds=window_seconds)
        return any(
            o["fingerprint"] == fp and o["timestamp"] >= cutoff
            for o in self._orders
        )

    @staticmethod
    def _fingerprint(order: OrderRequest) -> str:
        """Hash of buyer + items + amount to detect duplicates."""
        raw = f"{order.buyer_email}|{order.currency}|{order.total_amount}"
        for item in sorted(order.items, key=lambda i: i.name):
            raw += f"|{item.name}:{item.quantity}:{item.unit_price}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def reset(self) -> None:
        self._orders.clear()
        self._daily_spend.clear()


# Singleton ledger for the demo
_ledger = OrderLedger()


def get_ledger() -> OrderLedger:
    return _ledger


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def check_amount_limits(order: OrderRequest, config: GuardrailConfig) -> RuleResult:
    """Validates per-transaction amount limits."""
    amount = order.total_amount

    if amount < config.min_order_amount:
        return RuleResult(
            rule_name="amount_limits",
            category=RuleCategory.AMOUNT,
            passed=False,
            risk_level=RiskLevel.MEDIUM,
            message=f"Order amount ${amount:.2f} is below minimum ${config.min_order_amount:.2f}.",
            details={"amount": amount, "min": config.min_order_amount},
        )

    if amount > config.max_order_amount:
        return RuleResult(
            rule_name="amount_limits",
            category=RuleCategory.AMOUNT,
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            message=f"Order amount ${amount:.2f} exceeds maximum ${config.max_order_amount:.2f}.",
            details={"amount": amount, "max": config.max_order_amount},
        )

    if amount > config.high_amount_threshold:
        return RuleResult(
            rule_name="amount_limits",
            category=RuleCategory.AMOUNT,
            passed=True,
            risk_level=RiskLevel.MEDIUM,
            message=f"High-value order: ${amount:.2f} exceeds review threshold ${config.high_amount_threshold:.2f}.",
            details={"amount": amount, "threshold": config.high_amount_threshold},
        )

    return RuleResult(
        rule_name="amount_limits",
        category=RuleCategory.AMOUNT,
        passed=True,
        risk_level=RiskLevel.LOW,
        message=f"Order amount ${amount:.2f} is within limits.",
        details={"amount": amount},
    )


def check_daily_spending(
    order: OrderRequest, config: GuardrailConfig, ledger: OrderLedger | None = None
) -> RuleResult:
    """Validates daily aggregate spending limit."""
    ledger = ledger or get_ledger()
    spent = ledger.daily_spend(order.timestamp)
    projected = spent + order.total_amount

    if projected > config.daily_spending_limit:
        return RuleResult(
            rule_name="daily_spending",
            category=RuleCategory.AMOUNT,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=(
                f"Projected daily spend ${projected:.2f} would exceed "
                f"limit ${config.daily_spending_limit:.2f}."
            ),
            details={"spent": spent, "projected": projected, "limit": config.daily_spending_limit},
        )

    if projected > config.daily_spending_warning:
        return RuleResult(
            rule_name="daily_spending",
            category=RuleCategory.AMOUNT,
            passed=True,
            risk_level=RiskLevel.MEDIUM,
            message=f"Daily spend approaching limit: ${projected:.2f} / ${config.daily_spending_limit:.2f}.",
            details={"spent": spent, "projected": projected},
        )

    return RuleResult(
        rule_name="daily_spending",
        category=RuleCategory.AMOUNT,
        passed=True,
        risk_level=RiskLevel.LOW,
        message=f"Daily spend is within limits (${projected:.2f} / ${config.daily_spending_limit:.2f}).",
        details={"spent": spent, "projected": projected},
    )


def check_velocity(
    order: OrderRequest, config: GuardrailConfig, ledger: OrderLedger | None = None
) -> RuleResult:
    """Detects rapid-fire order submission (potential bot / fraud)."""
    ledger = ledger or get_ledger()
    now = order.timestamp

    last_minute = ledger.orders_since(now - timedelta(minutes=1), order.buyer_email)
    last_hour = ledger.orders_since(now - timedelta(hours=1), order.buyer_email)
    last_day = ledger.orders_since(now - timedelta(days=1), order.buyer_email)

    if len(last_minute) >= config.max_orders_per_minute:
        return RuleResult(
            rule_name="velocity",
            category=RuleCategory.VELOCITY,
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            message=f"Rate limit exceeded: {len(last_minute)} orders in the last minute (max {config.max_orders_per_minute}).",
            details={"window": "1min", "count": len(last_minute), "limit": config.max_orders_per_minute},
        )

    if len(last_hour) >= config.max_orders_per_hour:
        return RuleResult(
            rule_name="velocity",
            category=RuleCategory.VELOCITY,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=f"Rate limit exceeded: {len(last_hour)} orders in the last hour (max {config.max_orders_per_hour}).",
            details={"window": "1hr", "count": len(last_hour), "limit": config.max_orders_per_hour},
        )

    if len(last_day) >= config.max_orders_per_day:
        return RuleResult(
            rule_name="velocity",
            category=RuleCategory.VELOCITY,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=f"Rate limit exceeded: {len(last_day)} orders today (max {config.max_orders_per_day}).",
            details={"window": "1day", "count": len(last_day), "limit": config.max_orders_per_day},
        )

    return RuleResult(
        rule_name="velocity",
        category=RuleCategory.VELOCITY,
        passed=True,
        risk_level=RiskLevel.LOW,
        message="Order velocity is within normal limits.",
        details={
            "last_minute": len(last_minute),
            "last_hour": len(last_hour),
            "last_day": len(last_day),
        },
    )


def check_currency(order: OrderRequest, config: GuardrailConfig) -> RuleResult:
    """Validates that the currency is allowed."""
    if order.currency not in config.allowed_currencies:
        return RuleResult(
            rule_name="currency",
            category=RuleCategory.CURRENCY,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=f"Currency '{order.currency}' is not in the allowed list: {config.allowed_currencies}.",
            details={"currency": order.currency, "allowed": config.allowed_currencies},
        )

    return RuleResult(
        rule_name="currency",
        category=RuleCategory.CURRENCY,
        passed=True,
        risk_level=RiskLevel.LOW,
        message=f"Currency '{order.currency}' is allowed.",
        details={"currency": order.currency},
    )


def check_blocked_items(order: OrderRequest, config: GuardrailConfig) -> RuleResult:
    """Rejects orders containing items in blocked categories."""
    blocked_found: list[str] = []
    for item in order.items:
        if item.category.lower() in [c.lower() for c in config.blocked_categories]:
            blocked_found.append(f"{item.name} (category: {item.category})")

    if blocked_found:
        return RuleResult(
            rule_name="blocked_items",
            category=RuleCategory.ITEM,
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            message=f"Order contains blocked items: {', '.join(blocked_found)}.",
            details={"blocked_items": blocked_found},
        )

    return RuleResult(
        rule_name="blocked_items",
        category=RuleCategory.ITEM,
        passed=True,
        risk_level=RiskLevel.LOW,
        message="No blocked items found.",
    )


def check_quantity_limits(order: OrderRequest, config: GuardrailConfig) -> RuleResult:
    """Validates item counts and quantities."""
    if len(order.items) > config.max_items_per_order:
        return RuleResult(
            rule_name="quantity_limits",
            category=RuleCategory.QUANTITY,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=f"Too many items in order: {len(order.items)} (max {config.max_items_per_order}).",
            details={"item_count": len(order.items), "max": config.max_items_per_order},
        )

    excessive: list[str] = []
    for item in order.items:
        if item.quantity > config.max_quantity_per_item:
            excessive.append(f"{item.name}: {item.quantity}")
        if item.quantity < 1:
            return RuleResult(
                rule_name="quantity_limits",
                category=RuleCategory.QUANTITY,
                passed=False,
                risk_level=RiskLevel.MEDIUM,
                message=f"Invalid quantity for '{item.name}': {item.quantity}.",
                details={"item": item.name, "quantity": item.quantity},
            )

    if excessive:
        return RuleResult(
            rule_name="quantity_limits",
            category=RuleCategory.QUANTITY,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=f"Excessive quantities: {', '.join(excessive)} (max per item: {config.max_quantity_per_item}).",
            details={"excessive_items": excessive},
        )

    return RuleResult(
        rule_name="quantity_limits",
        category=RuleCategory.QUANTITY,
        passed=True,
        risk_level=RiskLevel.LOW,
        message="Item quantities are within limits.",
    )


def check_duplicate_order(
    order: OrderRequest, config: GuardrailConfig, ledger: OrderLedger | None = None
) -> RuleResult:
    """Catches accidental duplicate submissions."""
    ledger = ledger or get_ledger()

    if ledger.has_duplicate(order, config.duplicate_window_seconds):
        return RuleResult(
            rule_name="duplicate_order",
            category=RuleCategory.DUPLICATE,
            passed=False,
            risk_level=RiskLevel.HIGH,
            message=(
                f"Duplicate order detected within {config.duplicate_window_seconds}s window. "
                "This looks like an accidental re-submission."
            ),
            details={"window_seconds": config.duplicate_window_seconds},
        )

    return RuleResult(
        rule_name="duplicate_order",
        category=RuleCategory.DUPLICATE,
        passed=True,
        risk_level=RiskLevel.LOW,
        message="No duplicate detected.",
    )


def check_geo_restrictions(order: OrderRequest, config: GuardrailConfig) -> RuleResult:
    """Validates buyer/shipping countries against sanctions list."""
    blocked = []
    if order.buyer_country and order.buyer_country.upper() in config.blocked_countries:
        blocked.append(f"buyer country: {order.buyer_country}")
    if order.shipping_country and order.shipping_country.upper() in config.blocked_countries:
        blocked.append(f"shipping country: {order.shipping_country}")

    if blocked:
        return RuleResult(
            rule_name="geo_restrictions",
            category=RuleCategory.GEO,
            passed=False,
            risk_level=RiskLevel.CRITICAL,
            message=f"Blocked geography detected: {', '.join(blocked)}.",
            details={"blocked": blocked, "blocked_countries": config.blocked_countries},
        )

    return RuleResult(
        rule_name="geo_restrictions",
        category=RuleCategory.GEO,
        passed=True,
        risk_level=RiskLevel.LOW,
        message="No geographic restrictions triggered.",
    )


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

ALL_RULES = [
    check_amount_limits,
    check_daily_spending,
    check_velocity,
    check_currency,
    check_blocked_items,
    check_quantity_limits,
    check_duplicate_order,
    check_geo_restrictions,
]
