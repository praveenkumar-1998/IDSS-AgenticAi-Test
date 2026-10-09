import httpx
import time

BASE = {
    "customer": "http://localhost:8001",
    "product": "http://localhost:8002",
    "order": "http://localhost:8003",
}

TS = int(time.time())
PASS = "[PASS]"
FAIL = "[FAIL]"


def check(label: str, response: httpx.Response, expected: int = None) -> httpx.Response:
    ok = response.status_code < 300 if expected is None else response.status_code == expected
    icon = PASS if ok else FAIL
    print(f"  {icon} [{response.status_code}] {label}")
    if not ok:
        print(f"       {response.text[:300]}")
    return response


def section(title: str):
    print()
    print("=" * 48)
    print(f"  {title}")
    print("=" * 48)


section("Health checks")
for svc, base in BASE.items():
    r = check(f"{svc} /health -> {httpx.get(f'{base}/health').json()}", httpx.get(f"{base}/health"))

section("Customer Service (port 8001)")
base = BASE["customer"]

r = check("POST /customers/ (create Alice)", httpx.post(f"{base}/customers/", json={
    "name": "Alice Johnson",
    "email": f"alice_{TS}@example.com",
    "phone": "+1-555-0101",
    "address": "123 Main St",
}), 201)
cust_id = r.json()["id"]

check("GET  /customers/ (list)", httpx.get(f"{base}/customers/"))
check(f"GET  /customers/{cust_id}", httpx.get(f"{base}/customers/{cust_id}"))
check(f"PATCH /customers/{cust_id} (change phone)", httpx.patch(
    f"{base}/customers/{cust_id}", json={"phone": "+1-555-9999"}
))
check(f"PUT   /customers/{cust_id} (full update)", httpx.put(
    f"{base}/customers/{cust_id}", json={
        "name": "Alice Johnson-Smith",
        "email": f"alice_{TS}@example.com",
        "phone": "+1-555-9999",
    }
))

section("Product Service (port 8002)")
base = BASE["product"]

r = check("POST /products/ (create Keyboard)", httpx.post(f"{base}/products/", json={
    "name": "Wireless Keyboard",
    "sku": f"KB-{TS}",
    "price": 49.99,
    "stock_quantity": 200,
    "category": "Electronics",
}), 201)
prod_id = r.json()["id"]

check("GET  /products/ (list)", httpx.get(f"{base}/products/"))
check(f"GET  /products/{prod_id}", httpx.get(f"{base}/products/{prod_id}"))
check(f"PATCH /products/{prod_id} (lower price)", httpx.patch(
    f"{base}/products/{prod_id}", json={"price": 44.99, "stock_quantity": 185}
))

section("Order Service (port 8003)")
base = BASE["order"]

r = check("POST /orders/ (place order with 2 items)", httpx.post(f"{base}/orders/", json={
    "customer_id": cust_id,
    "shipping_address": "123 Main St",
    "notes": "Please leave at door",
    "items": [
        {
            "product_id": prod_id,
            "product_name": "Wireless Keyboard",
            "quantity": 2,
            "unit_price": 44.99,
        }
    ],
}), 201)
order = r.json()
order_id = order["id"]

check("GET  /orders/ (list)", httpx.get(f"{base}/orders/"))
check(f"GET  /orders/{order_id} (with items)", httpx.get(f"{base}/orders/{order_id}"))
check(f"GET  /orders/customer/{cust_id}", httpx.get(f"{base}/orders/customer/{cust_id}"))
check(f"PATCH /orders/{order_id} (status -> confirmed)", httpx.patch(
    f"{base}/orders/{order_id}", json={"status": "confirmed"}
))
check(f"PATCH /orders/{order_id} (status -> shipped)", httpx.patch(
    f"{base}/orders/{order_id}", json={"status": "shipped"}
))

section("Delete (cascade cleanup)")
check(f"DELETE /orders/{order_id}", httpx.delete(f"{BASE['order']}/orders/{order_id}"))
check(f"DELETE /products/{prod_id}", httpx.delete(f"{BASE['product']}/products/{prod_id}"))
check(f"DELETE /customers/{cust_id}", httpx.delete(f"{BASE['customer']}/customers/{cust_id}"))

section("404 guard checks")
check("GET /customers/999 (expect 404)", httpx.get(f"{BASE['customer']}/customers/999"), 404)
check("GET /products/999  (expect 404)", httpx.get(f"{BASE['product']}/products/999"), 404)
check("GET /orders/999    (expect 404)", httpx.get(f"{BASE['order']}/orders/999"), 404)

print("\nAll tests complete.")
