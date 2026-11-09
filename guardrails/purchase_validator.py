"""PayPal Guardrail Engine – validates orders against configurable rules.

Usage:
    from guardrails import GuardrailEngine
    engine = GuardrailEngine()
    verdict = engine.evaluate(order_request)
"""

from __future__ import annotations

import inspect
import logging
from typing import Callable

from guardrails.models import (
    Decision,
    GuardrailVerdict,
    OrderRequest,
    RiskLevel,
    RuleResult,
)
from guardrails.rules import (
    ALL_RULES,
    GuardrailConfig,
    OrderLedger,
    get_ledger,
)

logger = logging.getLogger(__name__)

# Map risk levels to numeric scores for aggregation
_RISK_SCORES: dict[RiskLevel, float] = {
    RiskLevel.LOW: 10.0,
    RiskLevel.MEDIUM: 40.0,
    RiskLevel.HIGH: 70.0,
    RiskLevel.CRITICAL: 95.0,
}


class GuardrailEngine:
    """Evaluates an OrderRequest against a pipeline of guardrail rules.

    Attributes:
        config: Guardrail configuration (thresholds, limits, etc.).
        ledger: In-memory order ledger for velocity/duplicate checks.
    """

    def __init__(
        self,
        config: GuardrailConfig | None = None,
        ledger: OrderLedger | None = None,
    ) -> None:
        self.config = config or GuardrailConfig()
        self.ledger = ledger or get_ledger()
        self._rules: list[Callable] = list(ALL_RULES)

    def evaluate(self, order: OrderRequest) -> GuardrailVerdict:
        """Run all rules and return an aggregate verdict."""
        results: list[RuleResult] = []

        for rule_fn in self._rules:
            try:
                result = self._invoke_rule(rule_fn, order)
                results.append(result)
            except Exception as exc:
                logger.exception("Rule %s raised an error", rule_fn.__name__)
                results.append(
                    RuleResult(
                        rule_name=rule_fn.__name__,
                        category=getattr(rule_fn, "_category", None)
                        or results[-1].category if results else __import__("guardrails.models", fromlist=["RuleCategory"]).RuleCategory.CUSTOM,
                        passed=False,
                        risk_level=RiskLevel.HIGH,
                        message=f"Rule error: {exc}",
                    )
                )

        risk_score = self._compute_risk_score(results)
        overall_risk = self._score_to_risk_level(risk_score)
        decision = self._make_decision(results, risk_score)

        verdict = GuardrailVerdict(
            request_id=order.request_id,
            decision=decision,
            overall_risk=overall_risk,
            risk_score=risk_score,
            rule_results=results,
            timestamp=order.timestamp,
        )

        # Record the order in the ledger (for future velocity/duplicate checks)
        # only if it was approved.
        if decision == Decision.APPROVE:
            self.ledger.record(order)

        logger.info(
            "Order %s → %s (risk=%.1f, level=%s)",
            order.request_id,
            decision.value,
            risk_score,
            overall_risk.value,
        )

        return verdict

    def add_rule(self, rule_fn: Callable) -> None:
        """Register an additional custom rule."""
        self._rules.append(rule_fn)

    def remove_rule(self, rule_name: str) -> None:
        """Remove a rule by function name."""
        self._rules = [r for r in self._rules if r.__name__ != rule_name]


    def _invoke_rule(self, rule_fn: Callable, order: OrderRequest) -> RuleResult:
        """Call a rule, injecting config/ledger as needed."""
        sig = inspect.signature(rule_fn)
        kwargs: dict = {"order": order, "config": self.config}
        if "ledger" in sig.parameters:
            kwargs["ledger"] = self.ledger
        return rule_fn(**kwargs)

    def _compute_risk_score(self, results: list[RuleResult]) -> float:
        """Weighted average of individual rule risk scores (0–100)."""
        if not results:
            return 0.0

        weights = self.config.risk_weights
        total_weight = 0.0
        weighted_sum = 0.0

        for r in results:
            # Look up weight by category, default to equal share
            cat_key = r.category.value.lower()
            w = weights.get(cat_key, 1.0 / len(results))
            score = _RISK_SCORES[r.risk_level]
            # Failed rules get a heavier penalty
            if not r.passed:
                score = min(100.0, score * 1.5)
            weighted_sum += score * w
            total_weight += w

        return round(weighted_sum / total_weight, 1) if total_weight else 0.0

    @staticmethod
    def _score_to_risk_level(score: float) -> RiskLevel:
        if score >= 80:
            return RiskLevel.CRITICAL
        if score >= 55:
            return RiskLevel.HIGH
        if score >= 30:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _make_decision(results: list[RuleResult], risk_score: float) -> Decision:
        """Determine final decision based on rule outcomes and risk score."""
        critical_failures = [
            r for r in results if not r.passed and r.risk_level == RiskLevel.CRITICAL
        ]
        if critical_failures:
            return Decision.REJECT

        any_failure = any(not r.passed for r in results)
        if any_failure:
            return Decision.REJECT

        if risk_score >= 50:
            return Decision.FLAG_FOR_REVIEW

        return Decision.APPROVE
