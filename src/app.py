"""Interactive CLI application for the PayPal Guardrail Agent.

Run with:  python -m src.app
"""

from __future__ import annotations

import json
import sys
from textwrap import dedent

from src.agent import PayPalGuardrailAgent


BANNER = r"""
╔══════════════════════════════════════════════════════════════════╗
║              🛡️  PayPal Guardrail Agent  🛡️                     ║
║         Intelligent Order Protection & Risk Management           ║
╚══════════════════════════════════════════════════════════════════╝
"""

HELP_TEXT = dedent("""\
    Commands:
      catalog [category]         — Browse the product catalog
      search <query>             — Search products by name or SKU
      validate                   — Dry-run order through guardrails
      order                      — Create a guardrail-protected PayPal order
      status <order_id>          — Check PayPal order status
      capture <order_id>         — Capture an approved order
      config                     — View guardrail configuration
      demo                       — Run demo scenarios
      help                       — Show this help
      quit                       — Exit
""")


def _print_json(data: dict) -> None:
    """Pretty-print a dict as coloured JSON."""
    print(json.dumps(data, indent=2, default=str))


def _prompt_items() -> list[dict]:
    """Interactively prompt for order items."""
    items: list[dict] = []
    print("  Enter items (empty name to finish):")
    while True:
        name = input("    Item name: ").strip()
        if not name:
            break
        try:
            qty = int(input("    Quantity: ").strip())
            price = float(input("    Unit price ($): ").strip())
        except ValueError:
            print("    ⚠  Invalid number, skipping item.")
            continue
        category = input("    Category [general]: ").strip() or "general"
        items.append({
            "name": name,
            "quantity": qty,
            "unit_price": price,
            "category": category,
        })
        print(f"    ✓ Added: {name} × {qty} @ ${price:.2f}")
        user_response = input(" Do you want to add another item? [Yes/No] ").strip().lower()
        if user_response not in ("y", "yes", "YeS"):
           break
        else:
            continue
    return items 


def _prompt_order_details() -> dict:
    """Prompt for optional order metadata."""
    currency = input("  Currency [USD]: ").strip() or "USD"
    email = input("  Buyer email (optional): ").strip() or None
    buyer_co = input("  Buyer country code (optional, e.g. US): ").strip() or None
    ship_co = input("  Shipping country code (optional): ").strip() or None
    return {
        "currency": currency,
        "buyer_email": email,
        "buyer_country": buyer_co,
        "shipping_country": ship_co,
    }


def run_demo(agent: PayPalGuardrailAgent) -> None:
    """Run a set of demo scenarios to showcase guardrail capabilities."""
    scenarios = [
        {
            "title": "✅ Normal Order (should APPROVE)",
            "items": [
                {"name": "Wireless Mouse", "quantity": 2, "unit_price": 29.99, "category": "electronics"},
                {"name": "USB-C Hub", "quantity": 1, "unit_price": 49.99, "category": "electronics"},
            ],
            "currency": "USD",
            "buyer_email": "buyer@example.com",
            "buyer_country": "US",
        },
        {
            "title": "❌ Exceeds Max Amount (should REJECT)",
            "items": [
                {"name": "Enterprise Server", "quantity": 3, "unit_price": 4999.99, "category": "enterprise"},
            ],
            "currency": "USD",
        },
        {
            "title": "❌ Blocked Category (should REJECT)",
            "items": [
                {"name": "Suspicious Item", "quantity": 1, "unit_price": 50.00, "category": "weapons"},
            ],
            "currency": "USD",
        },
        {
            "title": "❌ Blocked Country (should REJECT)",
            "items": [
                {"name": "Laptop Stand", "quantity": 1, "unit_price": 39.99, "category": "accessories"},
            ],
            "currency": "USD",
            "shipping_country": "KP",
        },
        {
            "title": "❌ Unsupported Currency (should REJECT)",
            "items": [
                {"name": "Widget", "quantity": 1, "unit_price": 10.00},
            ],
            "currency": "BTC",
        },
        {
            "title": "⚠️  High-Value Order (should FLAG or APPROVE with warning)",
            "items": [
                {"name": "4K Ultrawide Monitor", "quantity": 4, "unit_price": 1299.99, "category": "electronics"},
            ],
            "currency": "USD",
            "buyer_email": "vip@example.com",
        },
    ]

    print("\n" + "=" * 60)
    print("  🎬  GUARDRAIL DEMO SCENARIOS")
    print("=" * 60)

    for i, scenario in enumerate(scenarios, 1):
        title = scenario.pop("title")
        print(f"\n{'─' * 60}")
        print(f"  Scenario {i}: {title}")
        print(f"{'─' * 60}")
        result = agent.validate_order(**scenario)
        decision = result["decision"]
        risk = result["risk_level"]
        score = result["risk_score"]

        # Decision icon
        icon = {"APPROVE": "✅", "REJECT": "❌", "FLAG_FOR_REVIEW": "⚠️"}.get(decision, "❓")

        print(f"  {icon}  Decision: {decision}  |  Risk: {risk}  |  Score: {score}/100")
        print(f"  Total: ${result['total_amount']:.2f} {result['currency']}")

        for r in result["rule_results"]:
            status = "✓" if r["passed"] else "✗"
            print(f"    {status} [{r['risk']}] {r['rule']}: {r['message']}")

    print(f"\n{'=' * 60}")
    print("  Demo complete! All guardrail rules demonstrated.\n")


def main() -> None:
    agent = PayPalGuardrailAgent()
    print(BANNER)
    print(HELP_TEXT)

    while True:
        try:
            raw = input("\n🛡️  guardrail> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not raw:
            continue

        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        elif cmd == "help":
            print(HELP_TEXT)

        elif cmd == "catalog":
            category = arg or None
            _print_json(agent.list_catalog(category=category, in_stock_only=False))

        elif cmd == "search":
            if not arg:
                print("Usage: search <product name or SKU>")
            else:
                _print_json(agent.search_product(arg))

        elif cmd == "validate":
            items = _prompt_items()
            if not items:
                print("No items entered.")
                continue
            details = _prompt_order_details()
            result = agent.validate_order(items=items, **details)
            _print_json(result)

        elif cmd == "order":
            items = _prompt_items()
            if not items:
                print("No items entered.")
                continue
            details = _prompt_order_details()
            result = agent.create_order(items=items, **details)
            _print_json(result)

        elif cmd == "status":
            if not arg:
                print("Usage: status <order_id>")
            else:
                _print_json(agent.get_order_status(arg))

        elif cmd == "capture":
            if not arg:
                print("Usage: capture <order_id>")
            else:
                _print_json(agent.capture_order(arg))

        elif cmd == "config":
            _print_json(agent.get_guardrail_config())

        elif cmd == "demo":
            run_demo(agent)

        else:
            print(f"Unknown command: {cmd}. Type 'help' for available commands.")


if __name__ == "__main__":
    main()
