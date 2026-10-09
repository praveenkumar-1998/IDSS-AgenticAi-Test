"""Hand-written tools for things a single endpoint can't do safely or exactly.

The generated tools cover plain CRUD for every service. These exist for:
  * multi-service writes that need ordering and compensation (placing an order
    has to check stock, decrement it, and put it back if anything fails);
  * numbers. LLMs are bad at summing 300 order lines, so reports are computed
    here and the model only has to read them out.

Each workflow declares the services it needs; it's only offered to the model
when all of them were discovered, so a missing service degrades the agent
instead of breaking it.
"""
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field

from agent.http import ServiceClient, ServiceError

log = logging.getLogger(__name__)

CUSTOMERS = "/api/v1/customers/"
PRODUCTS = "/api/v1/products/"
ORDERS = "/api/v1/orders/"

OPEN_STATUSES = ("pending", "confirmed")


class WorkflowError(Exception):
    """A business-rule failure the user should see verbatim."""


@dataclass
class Workflow:
    fn: Callable
    name: str
    requires: tuple[str, ...]
    destructive: bool = False
    writes: bool = False
    replaces: tuple[str, ...] = ()
    # services whose questions this answers, for the router; defaults to `requires`
    topics: tuple[str, ...] = ()


@dataclass
class Context:
    clients: dict[str, ServiceClient] = field(default_factory=dict)

    def __getattr__(self, name: str) -> ServiceClient:
        try:
            return self.clients[name]
        except KeyError:
            raise AttributeError(name) from None


REGISTRY: list[Workflow] = []


def workflow(*, requires: tuple[str, ...], destructive: bool = False, writes: bool = False,
             replaces: tuple[str, ...] = (), topics: tuple[str, ...] = ()):
    def register(fn: Callable) -> Callable:
        REGISTRY.append(Workflow(fn, fn.__name__, requires, destructive, writes or destructive,
                                 replaces, topics or requires))
        return fn
    return register


# lookups

def _match(rows: list[dict], query: str, keys: tuple[str, ...]) -> list[dict]:
    q = query.strip().lower()
    exact = [r for r in rows if any(str(r.get(k) or "").lower() == q for k in keys)]
    if exact:
        return exact
    return [r for r in rows if any(q in str(r.get(k) or "").lower() for k in keys)]


def resolve_customer(ctx: Context, ref: int | str) -> dict:
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return ctx.customer.get(f"{CUSTOMERS}{int(ref)}")
    matches = _match(ctx.customer.get_all(CUSTOMERS), str(ref), ("email", "name"))
    return _one(matches, "customer", ref, lambda c: f"#{c['id']} {c['name']} <{c['email']}>")


def resolve_product(ctx: Context, ref: int | str) -> dict:
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return ctx.product.get(f"{PRODUCTS}{int(ref)}")
    matches = _match(ctx.product.get_all(PRODUCTS), str(ref), ("sku", "name"))
    return _one(matches, "product", ref, lambda p: f"#{p['id']} {p['name']} (SKU {p['sku']})")


def _one(matches: list[dict], kind: str, ref: Any, label: Callable[[dict], str]) -> dict:
    if not matches:
        raise WorkflowError(f"No {kind} matches '{ref}'.")
    if len(matches) > 1:
        options = "; ".join(label(m) for m in matches[:10])
        raise WorkflowError(f"'{ref}' matches {len(matches)} {kind}s: {options}. Ask the user which one.")
    return matches[0]


@workflow(requires=("customer",))
def find_customers(ctx: Context, query: str) -> list[dict]:
    """Search customers by name or email (case-insensitive, partial match).

    Args:
        query: part of a name or email, e.g. "jane" or "@acme.com"
    """
    rows = _match(ctx.customer.get_all(CUSTOMERS), query, ("name", "email"))
    return [{k: c.get(k) for k in ("id", "name", "email", "phone", "is_active")} for c in rows]


@workflow(requires=("product",))
def find_products(ctx: Context, query: str) -> list[dict]:
    """Search products by name, SKU or category (case-insensitive, partial match).

    Args:
        query: part of a product name, SKU or category
    """
    rows = _match(ctx.product.get_all(PRODUCTS), query, ("name", "sku", "category"))
    return [{k: p.get(k) for k in ("id", "name", "sku", "price", "stock_quantity", "category", "is_available")}
            for p in rows]


# reports

@workflow(requires=("customer", "product", "order"))
def business_overview(ctx: Context) -> dict:
    """Exact headline numbers across all services: customer, product and order
    counts, orders by status, revenue, and low-stock products. Use this for
    "how many ..." questions instead of counting list results."""
    customers = ctx.customer.get_all(CUSTOMERS)
    products = ctx.product.get_all(PRODUCTS)
    orders = ctx.order.get_all(ORDERS)

    by_status = Counter(o["status"] for o in orders)
    billable = [o for o in orders if o["status"] != "cancelled"]
    return {
        "customers": {"total": len(customers), "active": sum(c["is_active"] for c in customers)},
        "products": {
            "total": len(products),
            "available": sum(p["is_available"] for p in products),
            "out_of_stock": [p["name"] for p in products if p["stock_quantity"] == 0],
            "low_stock_under_10": [f"{p['name']} ({p['stock_quantity']})" for p in products
                                   if 0 < p["stock_quantity"] < 10],
        },
        "orders": {
            "total": len(orders),
            "by_status": dict(by_status),
            "revenue_excluding_cancelled": round(sum(o["total_amount"] for o in billable), 2),
            "average_order_value": round(sum(o["total_amount"] for o in billable) / len(billable), 2)
            if billable else 0,
        },
    }


@workflow(requires=("product",))
def stock_report(ctx: Context, low_stock_threshold: int = 10) -> dict:
    """Current stock levels: products that are out of stock, products running low,
    and products marked unavailable. Use for "low stock", "inventory" or "what
    do we need to reorder" questions.

    Args:
        low_stock_threshold: low means fewer than this many in stock. Leave it
            out unless the user gave a number; the default is 10.
    """
    products = ctx.product.get_all(PRODUCTS)

    def row(p: dict) -> dict:
        return {"product_id": p["id"], "name": p["name"], "sku": p["sku"], "stock_quantity": p["stock_quantity"]}

    return {
        "threshold": low_stock_threshold,
        "out_of_stock": [row(p) for p in products if p["stock_quantity"] == 0],
        "low_stock": sorted((row(p) for p in products if 0 < p["stock_quantity"] < low_stock_threshold),
                            key=lambda r: r["stock_quantity"]),
        "unavailable": [row(p) for p in products if not p["is_available"]],
        "products_checked": len(products),
    }


@workflow(requires=("order",), topics=("order", "product"))
def sales_by_product(ctx: Context, top: int = 10, include_cancelled: bool = False) -> dict:
    """Units sold and revenue per product, best sellers first, computed from
    every order line. Answers "what products are sold / best sellers". This is
    sales history, not stock levels.

    Args:
        top: how many products to return (0 = all)
        include_cancelled: count lines from cancelled orders too
    """
    units: Counter = Counter()
    revenue: defaultdict = defaultdict(float)
    names: dict[int, str] = {}
    orders_with: defaultdict = defaultdict(set)
    for order in ctx.order.get_all(ORDERS):
        if order["status"] == "cancelled" and not include_cancelled:
            continue
        for line in order["items"]:
            pid = line["product_id"]
            units[pid] += line["quantity"]
            revenue[pid] += line["subtotal"]
            names[pid] = line["product_name"]
            orders_with[pid].add(order["id"])

    ranked = units.most_common(top or None)
    return {
        "products_sold": len(units),
        "total_units_sold": sum(units.values()),
        "total_revenue": round(sum(revenue.values()), 2),
        "ranking": [
            {"product_id": pid, "product_name": names[pid], "units_sold": qty,
             "revenue": round(revenue[pid], 2), "orders": len(orders_with[pid])}
            for pid, qty in ranked
        ],
    }


@workflow(requires=("customer", "order"))
def customer_order_summary(ctx: Context, customer: int | str) -> dict:
    """How many orders a customer has, what they spent, statuses and their
    recent orders.

    Args:
        customer: customer id, email or name
    """
    cust = resolve_customer(ctx, customer)
    orders = ctx.order.get_all(f"{ORDERS}customer/{cust['id']}")
    billable = [o for o in orders if o["status"] != "cancelled"]
    recent = sorted(orders, key=lambda o: o.get("created_at") or "", reverse=True)[:5]
    return {
        "customer": {"id": cust["id"], "name": cust["name"], "email": cust["email"]},
        "order_count": len(orders),
        "by_status": dict(Counter(o["status"] for o in orders)),
        "total_spent_excluding_cancelled": round(sum(o["total_amount"] for o in billable), 2),
        "recent_orders": [
            {"order_id": o["id"], "status": o["status"], "total": o["total_amount"],
             "created_at": o.get("created_at"),
             "items": [f"{i['quantity']} x {i['product_name']}" for i in o["items"]]}
            for o in recent
        ],
    }


@workflow(requires=("customer", "order"))
def order_details(ctx: Context, order_id: int) -> dict:
    """Full detail for one order, joined with the customer's name and email.

    Args:
        order_id: the order id
    """
    order = ctx.order.get(f"{ORDERS}{order_id}")
    try:
        cust = ctx.customer.get(f"{CUSTOMERS}{order['customer_id']}")
        order["customer"] = {"name": cust["name"], "email": cust["email"]}
    except ServiceError as exc:
        if exc.status != 404:
            raise
        order["customer"] = None  # customer was deleted; the order still stands
    return order


# writes

class OrderLine(BaseModel):
    product: int | str = Field(description="product id, SKU or exact name")
    quantity: int = Field(gt=0)


@workflow(requires=("customer", "product", "order"), writes=True, replaces=("create_order",))
def place_order(ctx: Context, customer: int | str, items: list[OrderLine],
                shipping_address: str | None = None, notes: str | None = None) -> dict:
    """Place an order. Looks up the customer and products, checks availability
    and stock, uses catalogue prices, reserves stock and creates the order.
    Rolls the stock back if any step fails.

    Args:
        customer: customer id, email or name
        items: products and quantities to order
        shipping_address: defaults to the customer's address
        notes: optional note on the order
    """
    if not items:
        raise WorkflowError("An order needs at least one item.")
    cust = resolve_customer(ctx, customer)
    if not cust["is_active"]:
        raise WorkflowError(f"Customer #{cust['id']} {cust['name']} is inactive.")

    lines: list[tuple[dict, int]] = []
    for item in items:
        item = OrderLine.model_validate(item)
        product = resolve_product(ctx, item.product)
        if not product["is_available"]:
            raise WorkflowError(f"{product['name']} is not available for sale.")
        if product["stock_quantity"] < item.quantity:
            raise WorkflowError(f"Not enough stock for {product['name']}: "
                                f"asked {item.quantity}, have {product['stock_quantity']}.")
        lines.append((product, item.quantity))

    # The product service has no reserve/commit endpoint, so this is a small saga:
    # take stock first, create the order, undo the stock on failure.
    # Two agents racing on the last unit can still oversell; fixing that needs an
    # atomic decrement in the product service, not more code here.
    reserved: list[tuple[dict, int]] = []
    try:
        for product, qty in lines:
            ctx.product.request("PATCH", f"{PRODUCTS}{product['id']}",
                                json={"stock_quantity": product["stock_quantity"] - qty})
            reserved.append((product, qty))
        return ctx.order.request("POST", ORDERS, json={
            "customer_id": cust["id"],
            "shipping_address": shipping_address or cust.get("address"),
            "notes": notes,
            "items": [{"product_id": p["id"], "product_name": p["name"],
                       "quantity": q, "unit_price": p["price"]} for p, q in lines],
        })
    except Exception:
        for product, _ in reserved:
            try:
                ctx.product.request("PATCH", f"{PRODUCTS}{product['id']}",
                                    json={"stock_quantity": product["stock_quantity"]})
            except ServiceError:
                log.error("stock rollback failed for product %s", product["id"], exc_info=True)
        raise


@workflow(requires=("product", "order"), destructive=True)
def cancel_order(ctx: Context, order_id: int) -> dict:
    """Cancel a pending or confirmed order and return its items to stock.

    Args:
        order_id: the order id
    """
    order = ctx.order.get(f"{ORDERS}{order_id}")
    if order["status"] not in OPEN_STATUSES:
        raise WorkflowError(f"Order #{order_id} is {order['status']}; only pending or confirmed orders can be cancelled.")

    # Flip the status first: a cancelled order with stock not yet returned is
    # easy to spot and fix, stock returned for a live order is not.
    updated = ctx.order.request("PATCH", f"{ORDERS}{order_id}", json={"status": "cancelled"})
    skipped = []
    for line in order["items"]:
        try:
            product = ctx.product.get(f"{PRODUCTS}{line['product_id']}")
        except ServiceError as exc:
            if exc.status != 404:
                raise
            skipped.append(line["product_name"])
            continue
        ctx.product.request("PATCH", f"{PRODUCTS}{product['id']}",
                            json={"stock_quantity": product["stock_quantity"] + line["quantity"]})
    if skipped:
        updated["note"] = f"Not restocked (product no longer exists): {', '.join(skipped)}"
    return updated
