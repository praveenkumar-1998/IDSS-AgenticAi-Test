import pytest

from agent.http import ServiceClient, ServiceError
from agent.workflows import (Context, WorkflowError, business_overview, cancel_order, customer_order_summary,
                             find_customers, place_order, resolve_customer, sales_by_product, stock_report)


@pytest.fixture
def ctx(transports):
    return Context({name: ServiceClient(name, f"http://{name}.test", transport=t, retries=0)
                    for name, t in transports.items()})


def stock(backend, product_id):
    return backend.tables["products"][product_id]["stock_quantity"]


def test_place_order_uses_catalogue_price_and_takes_stock(ctx, backend):
    order = place_order(ctx, "jane@example.com", [{"product": "HUB-01", "quantity": 3}])
    assert order["customer_id"] == 1
    assert order["items"][0]["unit_price"] == 29.99
    assert order["shipping_address"] == "5 Oak Ave"
    assert stock(backend, 1) == 7


def test_place_order_refuses_when_stock_is_short(ctx, backend):
    with pytest.raises(WorkflowError, match="Not enough stock"):
        place_order(ctx, "jane", [{"product": "HUB-01", "quantity": 1}, {"product": "KB-200", "quantity": 5}])
    assert stock(backend, 1) == 10 and stock(backend, 2) == 2
    assert not backend.tables["orders"]


def test_place_order_rolls_back_stock_if_order_service_fails(ctx, backend):
    backend.fail[("POST", "orders")] = 500
    with pytest.raises(ServiceError, match="500"):
        place_order(ctx, 1, [{"product": 1, "quantity": 2}, {"product": 2, "quantity": 1}])
    assert stock(backend, 1) == 10 and stock(backend, 2) == 2


def test_inactive_customer_cannot_order(ctx, backend):
    backend.tables["customers"][1]["is_active"] = False
    with pytest.raises(WorkflowError, match="inactive"):
        place_order(ctx, 1, [{"product": 1, "quantity": 1}])


def test_ambiguous_name_asks_instead_of_guessing(ctx):
    with pytest.raises(WorkflowError, match="matches 2 customers"):
        resolve_customer(ctx, "john")
    # an exact match beats partial ones
    assert resolve_customer(ctx, "John Smith")["id"] == 2


def test_cancel_returns_stock(ctx, backend):
    order = place_order(ctx, 1, [{"product": 1, "quantity": 4}])
    assert stock(backend, 1) == 6
    cancelled = cancel_order(ctx, order["id"])
    assert cancelled["status"] == "cancelled"
    assert stock(backend, 1) == 10


def test_cancel_refuses_shipped_orders(ctx, backend):
    order = place_order(ctx, 1, [{"product": 1, "quantity": 1}])
    backend.tables["orders"][order["id"]]["status"] = "shipped"
    with pytest.raises(WorkflowError, match="shipped"):
        cancel_order(ctx, order["id"])
    assert stock(backend, 1) == 9


def test_cancel_survives_deleted_product(ctx, backend):
    order = place_order(ctx, 1, [{"product": 1, "quantity": 1}])
    del backend.tables["products"][1]
    assert "Not restocked" in cancel_order(ctx, order["id"])["note"]


def test_reports_ignore_cancelled_orders(ctx, backend):
    place_order(ctx, 1, [{"product": 1, "quantity": 2}])
    keep = place_order(ctx, 2, [{"product": 1, "quantity": 1}, {"product": 2, "quantity": 1}])
    gone = place_order(ctx, 1, [{"product": 2, "quantity": 1}])
    cancel_order(ctx, gone["id"])

    sales = sales_by_product(ctx)
    assert sales["ranking"][0] == {"product_id": 1, "product_name": "USB-C Hub", "units_sold": 3,
                                   "revenue": 89.97, "orders": 2}
    assert sales["total_units_sold"] == 4

    overview = business_overview(ctx)
    assert overview["orders"]["by_status"] == {"pending": 2, "cancelled": 1}
    assert overview["orders"]["revenue_excluding_cancelled"] == round(59.98 + keep["total_amount"], 2)

    summary = customer_order_summary(ctx, "jane@example.com")
    assert summary["order_count"] == 2
    assert summary["total_spent_excluding_cancelled"] == 59.98


def test_counts_walk_every_page(ctx, backend):
    for i in range(450):
        backend.add("customers", name=f"Bulk {i}", email=f"bulk{i}@example.com")
    assert business_overview(ctx)["customers"]["total"] == 453
    assert len(find_customers(ctx, "bulk")) == 450


def test_stock_report(ctx, backend):
    backend.add("products", name="Cable", sku="C-1", price=5, stock_quantity=0)
    report = stock_report(ctx, low_stock_threshold=5)
    assert [p["name"] for p in report["out_of_stock"]] == ["Cable"]
    assert [p["name"] for p in report["low_stock"]] == ["Wireless Keyboard"]
