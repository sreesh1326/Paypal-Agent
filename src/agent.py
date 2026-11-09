"""PayPal Guardrail Agent – an agentic tool-use interface for safe PayPal order management.

This agent exposes tools that can be called by an LLM (or directly) to:
  1. Browse the product catalog
  2. Validate an order through guardrails (dry run)
  3. Create a PayPal order (with guardrail enforcement)
  4. Check order status
  5. Capture an approved order
  6. View guardrail configuration

Each tool returns structured JSON so any LLM can parse and present results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from catalogs.catalog import (
    find_product_by_name,
    get_product,
    list_products,
    validate_product,
)
from guardrails import GuardrailConfig, GuardrailEngine
from src.order_service import OrderService

logger = logging.getLogger(__name__)


class PayPalGuardrailAgent:

    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self.config = config or GuardrailConfig()
        self.engine = GuardrailEngine(config=self.config)
        self.order_service = OrderService(guardrail_engine=self.engine)
        self._order_history: list[dict] = []

    @staticmethod
    def tool_definitions() -> list[dict]:
        """Return Anthropic-compatible tool/function definitions for LLM integration."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_catalog",
                    "description": "List available products in the catalog. Optionally filter by category or in-stock status.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "description": "Filter by product category (e.g., 'electronics', 'accessories').",
                            },
                            "in_stock_only": {
                                "type": "boolean",
                                "description": "If true, only return in-stock items.",
                                "default": False,
                            },
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "validate_order",
                    "description": "Run an order through all guardrail checks WITHOUT creating it on PayPal (dry run). Returns the guardrail verdict with risk assessment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "description": "Items to order. Each item needs name, quantity, unit_price.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                        "unit_price": {"type": "number"},
                                        "category": {"type": "string", "default": "general"},
                                    },
                                    "required": ["name", "quantity", "unit_price"],
                                },
                            },
                            "currency": {"type": "string", "default": "INR"},
                            "buyer_email": {"type": "string"},
                            "buyer_country": {"type": "string"},
                            "shipping_country": {"type": "string"},
                        },
                        "required": ["items"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_order",
                    "description": "Validate an order through guardrails and, if approved, create it on PayPal sandbox. Returns the guardrail verdict AND the PayPal order details.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": "array",
                                "description": "Items to order.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "quantity": {"type": "integer"},
                                        "unit_price": {"type": "number"},
                                        "category": {"type": "string", "default": "general"},
                                    },
                                    "required": ["name", "quantity", "unit_price"],
                                },
                            },
                            "currency": {"type": "string", "default": "INR"},
                            "buyer_email": {"type": "string"},
                            "buyer_country": {"type": "string"},
                            "shipping_country": {"type": "string"},
                        },
                        "required": ["items"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_order_status",
                    "description": "Check the current status of a PayPal order by its order ID.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "string", "description": "The PayPal order ID."},
                        },
                        "required": ["order_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "capture_order",
                    "description": "Capture (finalize payment for) an approved PayPal order.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "string", "description": "The PayPal order ID to capture."},
                        },
                        "required": ["order_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_guardrail_config",
                    "description": "Show the current guardrail configuration (limits, thresholds, blocked items, etc.).",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_product",
                    "description": "Search for a product by name or SKU in the catalog.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Product name or SKU to search for."},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]


    def list_catalog(
        self, category: str | None = None, in_stock_only: bool = False
    ) -> dict:
        """List available products."""
        products = list_products(category=category, in_stock_only=in_stock_only)
        return {
            "count": len(products),
            "products": [
                {
                    "sku": p.sku,
                    "name": p.name,
                    "price": p.price,
                    "currency": p.currency,
                    "category": p.category,
                    "in_stock": p.in_stock,
                    "description": p.description,
                }
                for p in products
            ],
        }

    def search_product(self, query: str) -> dict:
        """Search for a product by name or SKU."""
        # Try SKU first
        product = get_product(query.upper())
        if not product:
            product = find_product_by_name(query)

        if product:
            return {
                "found": True,
                "product": {
                    "sku": product.sku,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "category": product.category,
                    "in_stock": product.in_stock,
                    "description": product.description,
                    "max_quantity": product.max_quantity,
                },
            }
        return {"found": False, "message": f"No product found for '{query}'."}

    def validate_order(
        self,
        items: list[dict],
        currency: str = "USD",
        buyer_email: str | None = None,
        buyer_country: str | None = None,
        shipping_country: str | None = None,
    ) -> dict:
        """Dry-run order validation through guardrails."""
        return self.order_service.process_order(
            items=items,
            currency=currency,
            buyer_email=buyer_email,
            buyer_country=buyer_country,
            shipping_country=shipping_country,
            dry_run=True,
        )

    def create_order(
        self,
        items: list[dict],
        currency: str = "INR",
        buyer_email: str | None = None,
        buyer_country: str | None = None,
        shipping_country: str | None = None,
    ) -> dict:
        """Create a guardrail-protected PayPal order."""
        result = self.order_service.process_order(
            items=items,
            currency=currency,
            buyer_email=buyer_email,
            buyer_country=buyer_country,
            shipping_country=shipping_country,
            dry_run=False,
        )
        self._order_history.append(result)
        return result

    def get_order_status(self, order_id: str) -> dict:
        """Check PayPal order status."""
        try:
            return self.order_service.get_order(order_id)
        except Exception as exc:
            return {"error": str(exc)}

    def capture_order(self, order_id: str) -> dict:
        """Capture an approved PayPal order."""
        try:
            return self.order_service.capture_order(order_id)
        except Exception as exc:
            return {"error": str(exc)}

    def get_guardrail_config(self) -> dict:
        """Return current guardrail configuration."""
        c = self.config
        return {
            "amount_limits": {
                "min_order_amount": c.min_order_amount,
                "max_order_amount": c.max_order_amount,
                "high_amount_threshold": c.high_amount_threshold,
                "daily_spending_limit": c.daily_spending_limit,
            },
            "velocity_limits": {
                "max_per_minute": c.max_orders_per_minute,
                "max_per_hour": c.max_orders_per_hour,
                "max_per_day": c.max_orders_per_day,
            },
            "allowed_currencies": c.allowed_currencies,
            "blocked_categories": c.blocked_categories,
            "quantity_limits": {
                "max_per_item": c.max_quantity_per_item,
                "max_items_per_order": c.max_items_per_order,
            },
            "geo_restrictions": {
                "blocked_countries": c.blocked_countries,
            },
            "duplicate_window_seconds": c.duplicate_window_seconds,
        }

    def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> dict:
        """Dispatch a tool call by name. Used by LLM agent loops."""
        tool_map = {
            "list_catalog": self.list_catalog,
            "search_product": self.search_product,
            "validate_order": self.validate_order,
            "create_order": self.create_order,
            "get_order_status": self.get_order_status,
            "capture_order": self.capture_order,
            "get_guardrail_config": self.get_guardrail_config,
        }
        fn = tool_map.get(tool_name)
        if fn is None:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return fn(**arguments)
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return {"error": str(exc)}
