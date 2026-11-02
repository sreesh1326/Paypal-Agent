import os
import webbrowser
from pathlib import Path

from dotenv import load_dotenv
from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def create_client() -> PaypalServersdkClient:
    load_dotenv(PROJECT_ROOT / ".env")

    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_SECRET") or os.getenv("PAYPAL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Set PAYPAL_CLIENT_ID and PAYPAL_SECRET in the project .env file "
            "(PAYPAL_CLIENT_SECRET is also accepted)."
        )

    return PaypalServersdkClient(
        client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
            o_auth_client_id=client_id,
            o_auth_client_secret=client_secret,
        ),
        environment=Environment.SANDBOX,
    )


def main() -> None:
    client = create_client()
    orders = client.orders

    create_result = orders.create_order(
        {
            "body": OrderRequest(
                intent=CheckoutPaymentIntent.CAPTURE,
                purchase_units=[
                    PurchaseUnitRequest(
                        amount=AmountWithBreakdown(
                            currency_code="USD",
                            value="1.00",
                        )
                    )
                ],
            ),
            "prefer": "return=representation",
        }
    )
    if create_result.is_error():
        raise RuntimeError(f"PayPal order creation failed: {create_result.errors}")

    order = create_result.body
    approval_link = next(
        (link.href for link in order.links if link.rel == "approve"),
        None,
    )
    if not order.id or not approval_link:
        raise RuntimeError("PayPal created an order without an ID or approval link.")

    print(f"Created sandbox order {order.id} (status: {order.status}).")
    print(f"Approve this order with a PayPal sandbox buyer: {approval_link}")
    webbrowser.open(approval_link)
    input("After approving the order in the browser, press Enter to capture it...")

    get_result = orders.get_order({"id": order.id})
    if get_result.is_error():
        raise RuntimeError(f"Could not verify the PayPal order: {get_result.errors}")
    if get_result.body.status != "APPROVED":
        raise RuntimeError(
            f"Order {order.id} is {get_result.body.status}, not APPROVED. "
            "Approve it with a sandbox buyer before capturing."
        )

    capture_result = orders.capture_order(
        {"id": order.id, "prefer": "return=representation"}
    )
    if capture_result.is_error():
        raise RuntimeError(f"PayPal order capture failed: {capture_result.errors}")

    print(
        f"Captured sandbox order {order.id} "
        f"(status: {capture_result.body.status})."
    )


if __name__ == "__main__":
    main()