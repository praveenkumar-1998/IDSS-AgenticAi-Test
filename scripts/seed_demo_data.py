"""Load demo customers, products and orders through the public APIs.

Safe to run twice: customers and products are matched on email / SKU, and
orders are only created when the order service is empty.

    uv run python scripts/seed_demo_data.py
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import load_services  # noqa: E402
from agent.http import ServiceClient, ServiceError  # noqa: E402
from agent.workflows import CUSTOMERS, ORDERS, PRODUCTS, Context, cancel_order, place_order  # noqa: E402

CUSTOMER_ROWS = [
    ("Jane Doe", "jane@example.com", "+1-555-0101", "5 Oak Ave, Springfield"),
    ("Ravi Kumar", "ravi.kumar@example.com", "+91-98400-11223", "12 Anna Salai, Chennai"),
    ("Maria Garcia", "maria.garcia@example.com", "+34-600-123-456", "Calle Mayor 8, Madrid"),
    ("Tom Becker", "tom.becker@example.com", "+49-151-2345678", "Hauptstr. 3, Berlin"),
    ("Aisha Khan", "aisha.khan@example.com", "+44-7700-900123", "22 Baker St, London"),
    ("Chen Wei", "chen.wei@example.com", "+86-138-0013-8000", "88 Nanjing Rd, Shanghai"),
    ("Priya Nair", "priya.nair@example.com", "+91-99001-22334", "4 MG Road, Bengaluru"),
    ("Liam O'Brien", "liam.obrien@example.com", "+353-85-123-4567", "7 Grafton St, Dublin"),
]

PRODUCT_ROWS = [
    ("USB-C Hub 7-in-1", "HUB-01", 29.99, 50, "Accessories"),
    ("Wireless Keyboard", "KB-200", 49.99, 120, "Peripherals"),
    ("Ergonomic Mouse", "MS-310", 34.50, 80, "Peripherals"),
    ("27in 4K Monitor", "MON-27K", 329.00, 15, "Displays"),
    ("Laptop Stand", "STD-AL", 39.00, 60, "Accessories"),
    ("Noise Cancelling Headphones", "HP-NC9", 199.00, 25, "Audio"),
    ("1080p Webcam", "CAM-1080", 59.90, 40, "Peripherals"),
    ("Mechanical Keyboard", "KB-MX", 119.00, 8, "Peripherals"),
    ("Portable SSD 1TB", "SSD-1T", 89.99, 30, "Storage"),
    ("HDMI Cable 2m", "CBL-HD2", 9.99, 0, "Accessories"),
]


def main() -> None:
    services = {s.name: s for s in load_services()}
    ctx = Context({name: ServiceClient(name, s.url) for name, s in services.items()})

    existing = {c["email"] for c in ctx.customer.get_all(CUSTOMERS)}
    for name, email, phone, address in CUSTOMER_ROWS:
        if email not in existing:
            ctx.customer.request("POST", CUSTOMERS, json={"name": name, "email": email,
                                                          "phone": phone, "address": address})
    existing = {p["sku"] for p in ctx.product.get_all(PRODUCTS)}
    for name, sku, price, stock, category in PRODUCT_ROWS:
        if sku not in existing:
            ctx.product.request("POST", PRODUCTS, json={"name": name, "sku": sku, "price": price,
                                                        "stock_quantity": stock, "category": category})

    if ctx.order.get_all(ORDERS):
        print("orders already present, leaving them alone")
    else:
        rng = random.Random(7)
        emails = [row[1] for row in CUSTOMER_ROWS]
        skus = [row[1] for row in PRODUCT_ROWS[:-1]]  # the HDMI cable is out of stock
        statuses = ["pending", "confirmed", "confirmed", "shipped", "delivered", "delivered", "cancelled"]
        for _ in range(18):
            lines = [{"product": sku, "quantity": rng.randint(1, 3)} for sku in rng.sample(skus, rng.randint(1, 3))]
            try:
                order = place_order(ctx, rng.choice(emails), lines)
            except Exception as exc:  # out of stock etc.; skip that one
                print(f"  skipped an order: {exc}")
                continue
            status = rng.choice(statuses)
            if status == "cancelled":
                cancel_order(ctx, order["id"])  # so the stock goes back
            elif status != "pending":
                ctx.order.request("PATCH", f"{ORDERS}{order['id']}", json={"status": status})

    print(f"customers: {len(ctx.customer.get_all(CUSTOMERS))}, "
          f"products: {len(ctx.product.get_all(PRODUCTS))}, "
          f"orders: {len(ctx.order.get_all(ORDERS))}")


if __name__ == "__main__":
    try:
        main()
    except ServiceError as exc:
        sys.exit(f"Seeding failed: {exc}\nAre all three services running?")
