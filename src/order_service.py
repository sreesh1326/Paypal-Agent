"""PayPal Order Service – wraps the PayPal Server SDK for order lifecycle.

Handles:  create → (approve) → capture  with guardrail checks at each step.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.item import Item as PayPalItem
from paypalserversdk.models.money import Money
from paypalserversdk.models.order_request import OrderRequest as PayPalOrderRequest
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient

from catalogs.catalog import Product, get_product
from guardrails import (
    Decision,
    GuardrailEngine,
    GuardrailVerdict,
    OrderItem,
    OrderRequest,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OrderService:
    """High-level service that validates orders through guardrails then
    creates them on the PayPal sandbox."""

    def __init__(
        self,
        guardrail_engine: GuardrailEngine | None = None,
    ) -> None:
        self.engine = guardrail_engine or GuardrailEngine()
        self._client: PaypalServersdkClient | None = None

    # ------------------------------------------------------------------
    # PayPal client (lazy init)
    # ------------------------------------------------------------------

    @property
    def paypal(self) -> PaypalServersdkClient:
        if self._client is None:
            self._client = self._create_paypal_client()
        return self._client

    @staticmethod
    def _create_paypal_client() -> PaypalServersdkClient:
        load_dotenv(PROJECT_ROOT / ".env")
        client_id = os.getenv("PAYPAL_CLIENT_ID")
        client_secret = os.getenv("PAYPAL_SECRET") or os.getenv("PAYPAL_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env"
            )
        return PaypalServersdkClient(
            client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
                o_auth_client_id=client_id,
                o_auth_client_secret=client_secret,
            ),
            environment=Environment.SANDBOX,
        )

    # ------------------------------------------------------------------
    # Core flow
    # ------------------------------------------------------------------

    def process_order(
        self,
        items: list[dict],
        currency: str = "USD",
        buyer_email: str | None = None,
        buyer_country: str | None = None,
        shipping_country: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """End-to-end: validate → create PayPal order (unless dry_run).

        Args:
            items: List of dicts with keys: name, quantity, unit_price,
                   and optionally category, sku.
            currency: ISO 4217 currency code.
            buyer_email: Buyer email for velocity tracking.
            buyer_country: ISO 3166-1 alpha-2 buyer country.
            shipping_country: ISO 3166-1 alpha-2 shipping country.
            dry_run: If True, only validate — don't call PayPal.

        Returns:
            Dict with keys: verdict, paypal_order (if created), approval_url.
        """
        # Build internal order request
        order_items = [
            OrderItem(
                name=i["name"],
                quantity=i["quantity"],
                unit_price=i["unit_price"],
                currency=currency,
                category=i.get("category", "general"),
                sku=i.get("sku"),
            )
            for i in items
        ]

        order_request = OrderRequest(
            items=order_items,
            currency=currency,
            buyer_email=buyer_email,
            buyer_country=buyer_country,
            shipping_country=shipping_country,
        )

        # Run guardrails
        verdict: GuardrailVerdict = self.engine.evaluate(order_request)

        result: dict = {
            "request_id": order_request.request_id,
            "total_amount": order_request.total_amount,
            "currency": currency,
            "decision": verdict.decision.value,
            "risk_level": verdict.overall_risk.value,
            "risk_score": verdict.risk_score,
            "rule_results": [
                {
                    "rule": r.rule_name,
                    "passed": r.passed,
                    "risk": r.risk_level.value,
                    "message": r.message,
                }
                for r in verdict.rule_results
            ],
        }

        if verdict.decision == Decision.REJECT:
            result["paypal_order"] = None
            result["approval_url"] = None
            result["message"] = (
                f"Order REJECTED by guardrails. "
                f"{len(verdict.failed_rules)} rule(s) failed."
            )
            logger.warning("Order %s REJECTED: %s", order_request.request_id, verdict.summary)
            return result

        if verdict.decision == Decision.FLAG_FOR_REVIEW:
            result["message"] = (
                "Order FLAGGED for manual review due to elevated risk. "
                "Proceeding with PayPal order creation."
            )

        if dry_run:
            result["paypal_order"] = None
            result["approval_url"] = None
            result["message"] = (
                result.get("message", "") +
                " [DRY RUN — PayPal order not created]"
            ).strip()
            return result

        # Create PayPal order
        try:
            paypal_result = self._create_paypal_order(order_items, currency)
            result.update(paypal_result)
        except Exception as exc:
            logger.exception("PayPal order creation failed")
            result["paypal_order"] = None
            result["approval_url"] = None
            result["error"] = str(exc)
            result["message"] = f"Guardrails passed but PayPal creation failed: {exc}"

        return result

    def get_order(self, order_id: str) -> dict:
        """Retrieve a PayPal order by ID."""
        res = self.paypal.orders.get_order({"id": order_id})
        if res.is_error():
            raise RuntimeError(f"Failed to get order {order_id}: {res.errors}")
        order = res.body
        return {
            "id": order.id,
            "status": order.status,
            "links": [{"rel": l.rel, "href": l.href} for l in (order.links or [])],
        }

    def capture_order(self, order_id: str) -> dict:
        """Capture a previously approved order."""
        res = self.paypal.orders.capture_order(
            {"id": order_id, "prefer": "return=representation"}
        )
        if res.is_error():
            raise RuntimeError(f"Capture failed for {order_id}: {res.errors}")
        body = res.body
        return {
            "id": body.id,
            "status": body.status,
        }

    def _create_paypal_order(
        self, items: list[OrderItem], currency: str
    ) -> dict:
        """Call PayPal to create an order."""
        total = sum(i.total_price for i in items)

        paypal_items = [
            PayPalItem(
                name=item.name[:127],
                quantity=str(item.quantity),
                unit_amount=Money(
                    currency_code=currency,
                    value=f"{item.unit_price:.2f}",
                ),
            )
            for item in items
        ]

        body = PayPalOrderRequest(
            intent=CheckoutPaymentIntent.CAPTURE,
            purchase_units=[
                PurchaseUnitRequest(
                    items=paypal_items,
                    amount=AmountWithBreakdown(
                        currency_code=currency,
                        value=f"{total:.2f}",
                        breakdown={
                            "item_total": Money(
                                currency_code=currency,
                                value=f"{total:.2f}",
                            ),
                        },
                    ),
                )
            ],
        )

        res = self.paypal.orders.create_order(
            {"body": body, "prefer": "return=representation"}
        )

        if res.is_error():
            raise RuntimeError(f"PayPal API error: {res.errors}")

        order = res.body
        approval_url = next(
            (link.href for link in (order.links or []) if link.rel == "approve"),
            None,
        )

        return {
            "paypal_order": {
                "id": order.id,
                "status": order.status,
                "links": [{"rel": l.rel, "href": l.href} for l in (order.links or [])],
            },
            "approval_url": approval_url,
            "message": f"PayPal order {order.id} created (status: {order.status}).",
        }
