"""Data models for the PayPal Guardrail Agent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Decision(Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    FLAG_FOR_REVIEW = "FLAG_FOR_REVIEW"


class RuleCategory(Enum):
    AMOUNT = "AMOUNT"
    VELOCITY = "VELOCITY"
    CURRENCY = "CURRENCY"
    ITEM = "ITEM"
    QUANTITY = "QUANTITY"
    DUPLICATE = "DUPLICATE"
    GEO = "GEO"
    CUSTOM = "CUSTOM"


@dataclass
class OrderItem:
    """A single item in an order."""
    name: str
    quantity: int
    unit_price: float
    currency: str = "USD"
    category: str = "general"
    sku: str | None = None

    @property
    def total_price(self) -> float:
        return round(self.unit_price * self.quantity, 2)


@dataclass
class OrderRequest:
    """An incoming order request to be validated by guardrails."""
    items: list[OrderItem]
    currency: str = "USD"
    buyer_email: str | None = None
    buyer_country: str | None = None
    shipping_country: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_amount(self) -> float:
        return round(sum(item.total_price for item in self.items), 2)


@dataclass
class RuleResult:
    """Result from a single guardrail rule evaluation."""
    rule_name: str
    category: RuleCategory
    passed: bool
    risk_level: RiskLevel
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardrailVerdict:
    """Aggregate verdict from all guardrail rules."""
    request_id: str
    decision: Decision
    overall_risk: RiskLevel
    risk_score: float  # 0.0 – 100.0
    rule_results: list[RuleResult] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def failed_rules(self) -> list[RuleResult]:
        return [r for r in self.rule_results if not r.passed]

    @property
    def summary(self) -> str:
        lines = [
            f"Decision: {self.decision.value}",
            f"Risk Level: {self.overall_risk.value}",
            f"Risk Score: {self.risk_score:.1f}/100",
            f"Rules Evaluated: {len(self.rule_results)}",
            f"Rules Failed: {len(self.failed_rules)}",
        ]
        if self.failed_rules:
            lines.append("Failed Rules:")
            for r in self.failed_rules:
                lines.append(f"  ✗ [{r.category.value}] {r.rule_name}: {r.message}")
        return "\n".join(lines)
