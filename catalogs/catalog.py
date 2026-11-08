"""In-memory product catalog used by the PayPal guardrail agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    """A product that can be offered for purchase."""

    sku: str
    name: str
    price: float
    currency: str = "USD"
    category: str = "general"
    in_stock: bool = True
    description: str = ""
    max_quantity: int = 99


_PRODUCTS: tuple[Product, ...] = (
    Product("WIDGET-001", "Wireless Mouse", 29.99, category="electronics", description="Ergonomic wireless mouse."),
    Product("WIDGET-002", "Mechanical Keyboard", 79.99, category="electronics", description="Mechanical keyboard with tactile switches."),
    Product("WIDGET-003", "USB-C Hub", 49.99, category="electronics", description="Multiport USB-C hub."),
    Product("WIDGET-004", "4K Ultrawide Monitor", 1299.99, category="electronics", description="4K ultrawide display."),
    Product("WIDGET-005", "Webcam", 89.99, category="electronics", description="Full HD USB webcam."),
    Product("WIDGET-006", "Noise-Cancelling Headphones", 199.99, category="electronics", description="Over-ear wireless headphones."),
    Product("WIDGET-007", "Portable SSD", 119.99, category="electronics", description="Portable USB-C solid-state drive."),
    Product("WIDGET-008", "USB-C Charger", 24.99, category="electronics", description="Compact USB-C wall charger."),
    Product("ACC-001", "Laptop Stand", 39.99, category="accessories", description="Adjustable aluminum laptop stand."),
    Product("ACC-002", "Mouse Pad", 14.99, category="accessories", description="Large non-slip desk mouse pad."),
    Product("ACC-003", "Laptop Sleeve", 29.99, category="accessories", description="Protective padded laptop sleeve."),
    Product("ACC-004", "USB-C Cable", 12.99, category="accessories", description="Durable one-meter USB-C cable."),
    Product("ACC-005", "Desk Organizer", 22.50, category="accessories", description="Compact desktop organizer."),
    Product("ACC-006", "Laptop Riser", 34.99, category="accessories", description="Foldable laptop riser."),
    Product("HOME-001", "Desk Lamp", 44.99, category="home", description="Dimmable LED desk lamp."),
    Product("HOME-002", "Cable Organizer", 9.99, category="home", description="Reusable cable clips for a tidy desk."),
    Product("OOS-001", "Smart Speaker", 59.99, category="electronics", in_stock=False, description="Smart speaker; currently out of stock."),
)

_PRODUCTS_BY_SKU = {product.sku: product for product in _PRODUCTS}


def get_product(sku: str) -> Product | None:
    """Return a product by SKU, ignoring letter case and surrounding spaces."""
    return _PRODUCTS_BY_SKU.get(sku.strip().upper()) if isinstance(sku, str) else None


def find_product_by_name(name: str) -> Product | None:
    """Find a product whose name contains the supplied text, case-insensitively."""
    if not isinstance(name, str) or not name.strip():
        return None
    query = name.strip().casefold()
    return next((p for p in _PRODUCTS if query in p.name.casefold()), None)


def list_products(
    category: str | None = None, in_stock_only: bool = False
) -> list[Product]:
    """Return catalog products, optionally filtered by category and stock status."""
    products = _PRODUCTS
    if category:
        category_key = category.strip().casefold()
        products = tuple(p for p in products if p.category.casefold() == category_key)
    if in_stock_only:
        products = tuple(p for p in products if p.in_stock)
    return list(products)


def validate_product(sku: str, quantity: int) -> tuple[bool, str]:
    """Check that an SKU exists, is available, and has the requested quantity."""
    product = get_product(sku)
    if product is None:
        return False, f"Product '{sku}' was not found."
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return False, "Quantity must be a positive integer."
    if not product.in_stock:
        return False, f"{product.name} is out of stock."
    if quantity > product.max_quantity:
        return False, f"Quantity exceeds the maximum of {product.max_quantity} for {product.name}."
    return True, f"{product.name} is available."
