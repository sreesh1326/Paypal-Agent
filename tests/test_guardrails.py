"""Comprehensive tests for the PayPal Guardrail Agent.

Tests cover:
  • Individual guardrail rules
  • The GuardrailEngine aggregate evaluation
  • The PayPalGuardrailAgent tool dispatch
  • Edge cases and boundary conditions
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from guardrails.models import (
    Decision,
    OrderItem,
    OrderRequest,
    RiskLevel,
    RuleCategory,
)
from guardrails.rules import (
    GuardrailConfig,
    OrderLedger,
    check_amount_limits,
    check_blocked_items,
    check_currency,
    check_daily_spending,
    check_duplicate_order,
    check_geo_restrictions,
    check_quantity_limits,
    check_velocity,
)
from guardrails.purchase_validator import GuardrailEngine
from src.agent import PayPalGuardrailAgent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_order(
    items: list[OrderItem] | None = None,
    currency: str = "USD",
    buyer_email: str = "test@example.com",
    buyer_country: str | None = "US",
    shipping_country: str | None = "US",
    timestamp: datetime | None = None,
) -> OrderRequest:
    """Helper to build an OrderRequest."""
    if items is None:
        items = [OrderItem(name="Widget", quantity=1, unit_price=10.00)]
    return OrderRequest(
        items=items,
        currency=currency,
        buyer_email=buyer_email,
        buyer_country=buyer_country,
        shipping_country=shipping_country,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


def default_config() -> GuardrailConfig:
    return GuardrailConfig()


def fresh_ledger() -> OrderLedger:
    return OrderLedger()


# ===================================================================
# AMOUNT LIMIT TESTS
# ===================================================================

class TestAmountLimits:
    def test_normal_amount_passes(self):
        order = make_order(items=[OrderItem("Widget", 2, 25.00)])
        result = check_amount_limits(order, default_config())
        assert result.passed is True
        assert result.risk_level == RiskLevel.LOW

    def test_below_minimum_fails(self):
        order = make_order(items=[OrderItem("Tiny", 1, 0.001)])
        result = check_amount_limits(order, default_config())
        assert result.passed is False
        assert result.risk_level == RiskLevel.MEDIUM

    def test_exceeds_maximum_fails(self):
        order = make_order(items=[OrderItem("Expensive", 1, 15_000.00)])
        result = check_amount_limits(order, default_config())
        assert result.passed is False
        assert result.risk_level == RiskLevel.CRITICAL

    def test_high_value_flags_warning(self):
        config = default_config()
        order = make_order(items=[OrderItem("Pricey", 1, 6_000.00)])
        result = check_amount_limits(order, config)
        assert result.passed is True
        assert result.risk_level == RiskLevel.MEDIUM

    def test_exact_max_amount_passes(self):
        config = default_config()
        order = make_order(items=[OrderItem("Exact", 1, config.max_order_amount)])
        result = check_amount_limits(order, config)
        assert result.passed is True  # at the boundary, not exceeding


# ===================================================================
# DAILY SPENDING TESTS
# ===================================================================

class TestDailySpending:
    def test_within_limit_passes(self):
        ledger = fresh_ledger()
        order = make_order()
        result = check_daily_spending(order, default_config(), ledger)
        assert result.passed is True
        assert result.risk_level == RiskLevel.LOW

    def test_exceeds_daily_limit_fails(self):
        ledger = fresh_ledger()
        config = default_config()
        # Pre-fill ledger to near the limit
        pre_order = make_order(items=[OrderItem("Bulk", 1, 24_500.00)])
        ledger.record(pre_order)

        # This order should push over the limit
        order = make_order(items=[OrderItem("Extra", 1, 600.00)])
        result = check_daily_spending(order, config, ledger)
        assert result.passed is False
        assert result.risk_level == RiskLevel.HIGH

    def test_approaching_limit_warns(self):
        ledger = fresh_ledger()
        config = default_config()
        pre_order = make_order(items=[OrderItem("Bulk", 1, 14_500.00)])
        ledger.record(pre_order)

        order = make_order(items=[OrderItem("More", 1, 1_000.00)])
        result = check_daily_spending(order, config, ledger)
        assert result.passed is True
        assert result.risk_level == RiskLevel.MEDIUM


# ===================================================================
# VELOCITY TESTS
# ===================================================================

class TestVelocity:
    def test_normal_velocity_passes(self):
        ledger = fresh_ledger()
        order = make_order()
        result = check_velocity(order, default_config(), ledger)
        assert result.passed is True

    def test_per_minute_limit_exceeded(self):
        ledger = fresh_ledger()
        config = default_config()
        now = datetime.now(timezone.utc)

        # Record max_orders_per_minute orders
        for i in range(config.max_orders_per_minute):
            o = make_order(timestamp=now - timedelta(seconds=i * 10))
            ledger.record(o)

        # The next one should fail
        order = make_order(timestamp=now)
        result = check_velocity(order, config, ledger)
        assert result.passed is False
        assert result.risk_level == RiskLevel.CRITICAL

    def test_per_hour_limit_exceeded(self):
        ledger = fresh_ledger()
        config = default_config()
        now = datetime.now(timezone.utc)

        for i in range(config.max_orders_per_hour):
            o = make_order(timestamp=now - timedelta(minutes=i * 2))
            ledger.record(o)

        order = make_order(timestamp=now)
        result = check_velocity(order, config, ledger)
        assert result.passed is False


# ===================================================================
# CURRENCY TESTS
# ===================================================================

class TestCurrency:
    def test_allowed_currency_passes(self):
        order = make_order(currency="USD")
        result = check_currency(order, default_config())
        assert result.passed is True

    def test_blocked_currency_fails(self):
        order = make_order(currency="BTC")
        result = check_currency(order, default_config())
        assert result.passed is False
        assert result.risk_level == RiskLevel.HIGH

    def test_all_allowed_currencies(self):
        config = default_config()
        for curr in config.allowed_currencies:
            order = make_order(currency=curr)
            result = check_currency(order, config)
            assert result.passed is True, f"{curr} should be allowed"


# ===================================================================
# BLOCKED ITEMS TESTS
# ===================================================================

class TestBlockedItems:
    def test_normal_items_pass(self):
        order = make_order(items=[
            OrderItem("Mouse", 1, 29.99, category="electronics"),
        ])
        result = check_blocked_items(order, default_config())
        assert result.passed is True

    def test_blocked_category_fails(self):
        order = make_order(items=[
            OrderItem("Bad Item", 1, 100.00, category="weapons"),
        ])
        result = check_blocked_items(order, default_config())
        assert result.passed is False
        assert result.risk_level == RiskLevel.CRITICAL

    def test_mixed_items_with_blocked(self):
        order = make_order(items=[
            OrderItem("Mouse", 1, 29.99, category="electronics"),
            OrderItem("Bad", 1, 50.00, category="gambling"),
        ])
        result = check_blocked_items(order, default_config())
        assert result.passed is False

    def test_case_insensitive_blocking(self):
        order = make_order(items=[
            OrderItem("Item", 1, 10.00, category="WEAPONS"),
        ])
        result = check_blocked_items(order, default_config())
        assert result.passed is False


# ===================================================================
# QUANTITY TESTS
# ===================================================================

class TestQuantityLimits:
    def test_normal_quantity_passes(self):
        order = make_order(items=[OrderItem("Widget", 5, 10.00)])
        result = check_quantity_limits(order, default_config())
        assert result.passed is True

    def test_zero_quantity_fails(self):
        order = make_order(items=[OrderItem("Widget", 0, 10.00)])
        result = check_quantity_limits(order, default_config())
        assert result.passed is False

    def test_excessive_quantity_fails(self):
        order = make_order(items=[OrderItem("Widget", 1000, 10.00)])
        result = check_quantity_limits(order, default_config())
        assert result.passed is False

    def test_too_many_items_fails(self):
        items = [OrderItem(f"Item-{i}", 1, 1.00) for i in range(51)]
        order = make_order(items=items)
        result = check_quantity_limits(order, default_config())
        assert result.passed is False


# ===================================================================
# DUPLICATE DETECTION TESTS
# ===================================================================

class TestDuplicateDetection:
    def test_first_order_passes(self):
        ledger = fresh_ledger()
        order = make_order()
        result = check_duplicate_order(order, default_config(), ledger)
        assert result.passed is True

    def test_duplicate_within_window_fails(self):
        ledger = fresh_ledger()
        config = default_config()
        now = datetime.now(timezone.utc)

        # Record first order
        order1 = make_order(timestamp=now - timedelta(seconds=10))
        ledger.record(order1)

        # Same order again
        order2 = make_order(timestamp=now)
        result = check_duplicate_order(order2, config, ledger)
        assert result.passed is False
        assert result.risk_level == RiskLevel.HIGH

    def test_different_order_passes(self):
        ledger = fresh_ledger()
        config = default_config()
        now = datetime.now(timezone.utc)

        order1 = make_order(
            items=[OrderItem("Widget", 1, 10.00)],
            timestamp=now - timedelta(seconds=10),
        )
        ledger.record(order1)

        # Different items
        order2 = make_order(
            items=[OrderItem("Gadget", 2, 20.00)],
            timestamp=now,
        )
        result = check_duplicate_order(order2, config, ledger)
        assert result.passed is True


# ===================================================================
# GEO RESTRICTION TESTS
# ===================================================================

class TestGeoRestrictions:
    def test_allowed_country_passes(self):
        order = make_order(buyer_country="US", shipping_country="GB")
        result = check_geo_restrictions(order, default_config())
        assert result.passed is True

    def test_blocked_buyer_country_fails(self):
        order = make_order(buyer_country="KP")
        result = check_geo_restrictions(order, default_config())
        assert result.passed is False
        assert result.risk_level == RiskLevel.CRITICAL

    def test_blocked_shipping_country_fails(self):
        order = make_order(shipping_country="IR")
        result = check_geo_restrictions(order, default_config())
        assert result.passed is False

    def test_no_country_passes(self):
        order = make_order(buyer_country=None, shipping_country=None)
        result = check_geo_restrictions(order, default_config())
        assert result.passed is True


# ===================================================================
# GUARDRAIL ENGINE INTEGRATION TESTS
# ===================================================================

class TestGuardrailEngine:
    def test_clean_order_approved(self):
        engine = GuardrailEngine(ledger=fresh_ledger())
        order = make_order(items=[OrderItem("Widget", 2, 25.00)])
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.APPROVE
        assert verdict.overall_risk == RiskLevel.LOW
        assert len(verdict.failed_rules) == 0

    def test_over_limit_rejected(self):
        engine = GuardrailEngine(ledger=fresh_ledger())
        order = make_order(items=[OrderItem("Mega", 1, 15_000.00)])
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.REJECT
        assert len(verdict.failed_rules) > 0

    def test_blocked_country_rejected(self):
        engine = GuardrailEngine(ledger=fresh_ledger())
        order = make_order(shipping_country="KP")
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.REJECT

    def test_blocked_item_rejected(self):
        engine = GuardrailEngine(ledger=fresh_ledger())
        order = make_order(items=[OrderItem("Bad", 1, 10.00, category="drugs")])
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.REJECT

    def test_bad_currency_rejected(self):
        engine = GuardrailEngine(ledger=fresh_ledger())
        order = make_order(currency="XYZ")
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.REJECT

    def test_approved_order_recorded_in_ledger(self):
        ledger = fresh_ledger()
        engine = GuardrailEngine(ledger=ledger)
        order = make_order()
        engine.evaluate(order)
        assert len(ledger.orders_since(datetime.min.replace(tzinfo=timezone.utc))) == 1

    def test_rejected_order_not_recorded(self):
        ledger = fresh_ledger()
        engine = GuardrailEngine(ledger=ledger)
        order = make_order(items=[OrderItem("Bad", 1, 10.00, category="weapons")])
        engine.evaluate(order)
        assert len(ledger.orders_since(datetime.min.replace(tzinfo=timezone.utc))) == 0

    def test_duplicate_order_rejected(self):
        ledger = fresh_ledger()
        engine = GuardrailEngine(ledger=ledger)
        order1 = make_order()
        v1 = engine.evaluate(order1)
        assert v1.decision == Decision.APPROVE

        # Same order again
        order2 = make_order()
        v2 = engine.evaluate(order2)
        assert v2.decision == Decision.REJECT

    def test_verdict_summary_string(self):
        engine = GuardrailEngine(ledger=fresh_ledger())
        order = make_order()
        verdict = engine.evaluate(order)
        summary = verdict.summary
        assert "Decision:" in summary
        assert "Risk Level:" in summary

    def test_custom_config(self):
        config = GuardrailConfig(max_order_amount=50.00)
        engine = GuardrailEngine(config=config, ledger=fresh_ledger())
        order = make_order(items=[OrderItem("Over", 1, 75.00)])
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.REJECT

    def test_custom_rule_added(self):
        engine = GuardrailEngine(ledger=fresh_ledger())

        def custom_rule(order, config):
            from guardrails.models import RuleResult, RiskLevel, RuleCategory
            return RuleResult(
                rule_name="custom_test",
                category=RuleCategory.CUSTOM,
                passed=False,
                risk_level=RiskLevel.CRITICAL,
                message="Custom rule always fails.",
            )

        engine.add_rule(custom_rule)
        order = make_order()
        verdict = engine.evaluate(order)
        assert verdict.decision == Decision.REJECT
        assert any(r.rule_name == "custom_test" for r in verdict.rule_results)


# ===================================================================
# AGENT TOOL DISPATCH TESTS
# ===================================================================

class TestAgentTools:
    def test_list_catalog(self):
        agent = PayPalGuardrailAgent()
        result = agent.list_catalog()
        assert "products" in result
        assert result["count"] > 0

    def test_list_catalog_by_category(self):
        agent = PayPalGuardrailAgent()
        result = agent.list_catalog(category="electronics")
        assert all(p["category"] == "electronics" for p in result["products"])

    def test_search_product_by_name(self):
        agent = PayPalGuardrailAgent()
        result = agent.search_product("Wireless Mouse")
        assert result["found"] is True
        assert result["product"]["sku"] == "WIDGET-001"

    def test_search_product_by_sku(self):
        agent = PayPalGuardrailAgent()
        result = agent.search_product("WIDGET-002")
        assert result["found"] is True
        assert "Keyboard" in result["product"]["name"]

    def test_search_product_not_found(self):
        agent = PayPalGuardrailAgent()
        result = agent.search_product("Nonexistent Thing")
        assert result["found"] is False

    def test_validate_order_approve(self):
        agent = PayPalGuardrailAgent()
        result = agent.validate_order(
            items=[{"name": "Mouse", "quantity": 1, "unit_price": 29.99}],
        )
        assert result["decision"] == "APPROVE"
        assert "DRY RUN" in result.get("message", "")

    def test_validate_order_reject(self):
        agent = PayPalGuardrailAgent()
        result = agent.validate_order(
            items=[{"name": "Bad", "quantity": 1, "unit_price": 50.00, "category": "weapons"}],
        )
        assert result["decision"] == "REJECT"

    def test_get_guardrail_config(self):
        agent = PayPalGuardrailAgent()
        config = agent.get_guardrail_config()
        assert "amount_limits" in config
        assert "velocity_limits" in config
        assert "allowed_currencies" in config

    def test_dispatch_valid_tool(self):
        agent = PayPalGuardrailAgent()
        result = agent.dispatch("list_catalog", {})
        assert "products" in result

    def test_dispatch_unknown_tool(self):
        agent = PayPalGuardrailAgent()
        result = agent.dispatch("nonexistent_tool", {})
        assert "error" in result

    def test_tool_definitions_structure(self):
        defs = PayPalGuardrailAgent.tool_definitions()
        assert isinstance(defs, list)
        assert len(defs) >= 5
        for d in defs:
            assert d["type"] == "function"
            assert "name" in d["function"]
            assert "description" in d["function"]


# ===================================================================
# MODEL TESTS
# ===================================================================

class TestModels:
    def test_order_item_total_price(self):
        item = OrderItem("Widget", quantity=3, unit_price=10.00)
        assert item.total_price == 30.00

    def test_order_request_total_amount(self):
        order = make_order(items=[
            OrderItem("A", 2, 10.00),
            OrderItem("B", 1, 25.00),
        ])
        assert order.total_amount == 45.00

    def test_order_request_has_uuid(self):
        order = make_order()
        assert len(order.request_id) == 36  # UUID format


# ===================================================================
# CATALOG TESTS
# ===================================================================

class TestCatalog:
    def test_get_product(self):
        from catalogs.catalog import get_product
        p = get_product("WIDGET-001")
        assert p is not None
        assert p.name == "Wireless Mouse"

    def test_find_product_by_name(self):
        from catalogs.catalog import find_product_by_name
        p = find_product_by_name("keyboard")
        assert p is not None
        assert p.sku == "WIDGET-002"

    def test_validate_product_ok(self):
        from catalogs.catalog import validate_product
        ok, msg = validate_product("WIDGET-001", 5)
        assert ok is True

    def test_validate_product_not_found(self):
        from catalogs.catalog import validate_product
        ok, msg = validate_product("FAKE-999", 1)
        assert ok is False

    def test_validate_product_out_of_stock(self):
        from catalogs.catalog import validate_product
        ok, msg = validate_product("OOS-001", 1)
        assert ok is False

    def test_validate_product_bad_quantity(self):
        from catalogs.catalog import validate_product
        ok, msg = validate_product("WIDGET-001", 0)
        assert ok is False

    def test_list_products_all(self):
        from catalogs.catalog import list_products
        products = list_products()
        assert len(products) > 10

    def test_list_products_filtered(self):
        from catalogs.catalog import list_products
        products = list_products(category="electronics", in_stock_only=True)
        assert all(p.category == "electronics" and p.in_stock for p in products)
