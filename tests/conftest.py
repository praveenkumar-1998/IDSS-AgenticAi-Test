"""An in-memory stand-in for the three services.

It serves the real OpenAPI documents (captured from the running services into
tests/fixtures) and implements just enough CRUD behind them, so discovery,
tools, workflows and the graph can be tested without starting uvicorn.
"""
import json
import re
from pathlib import Path

import httpx
import pytest

from agent.config import ServiceConfig, Settings
from agent.discovery import discover

FIXTURES = Path(__file__).parent / "fixtures"


class FakeBackend:
    def __init__(self):
        self.tables: dict[str, dict[int, dict]] = {"customers": {}, "products": {}, "orders": {}}
        self.next_id = {name: 1 for name in self.tables}
        self.fail: dict[tuple[str, str], int] = {}  # (method, resource) -> status to return
        self.calls: list[tuple[str, str]] = []

    def add(self, table: str, **row) -> dict:
        row_id = self.next_id[table]
        self.next_id[table] += 1
        row = {"id": row_id, **row}
        if table == "orders":
            items = []
            for n, item in enumerate(row.get("items", []), start=1):
                items.append({"id": row_id * 100 + n, **item, "subtotal": item["quantity"] * item["unit_price"]})
            row["items"] = items
            row.setdefault("status", "pending")
            row["total_amount"] = sum(i["subtotal"] for i in items)
        if table == "customers":
            row.setdefault("is_active", True)
        if table == "products":
            row.setdefault("is_available", True)
            row.setdefault("stock_quantity", 0)
        self.tables[table][row_id] = row
        return row

    def transport(self, service: str) -> httpx.MockTransport:
        spec = json.loads((FIXTURES / f"{service}_openapi.json").read_text())

        def handle(request: httpx.Request) -> httpx.Response:
            path, method = request.url.path, request.method
            self.calls.append((method, path))
            if path == "/openapi.json":
                return httpx.Response(200, json=spec)

            m = re.match(r"^/api/v1/(customers|products|orders)/(?:customer/(\d+)|(\d+))?$", path)
            if not m:
                return httpx.Response(404, json={"detail": "Not Found"})
            table, by_customer, row_id = m.group(1), m.group(2), m.group(3)
            if (method, table) in self.fail:
                return httpx.Response(self.fail[(method, table)], json={"detail": "injected failure"})

            rows = self.tables[table]
            if by_customer:
                found = [o for o in rows.values() if o["customer_id"] == int(by_customer)]
                return httpx.Response(200, json=self._page(found, request))
            if row_id is None:
                if method == "GET":
                    return httpx.Response(200, json=self._page(list(rows.values()), request))
                if method == "POST":
                    return httpx.Response(201, json=self.add(table, **json.loads(request.content)))
            row = rows.get(int(row_id)) if row_id else None
            if row is None:
                return httpx.Response(404, json={"detail": f"{table[:-1].title()} #{row_id} not found."})
            if method == "GET":
                return httpx.Response(200, json=row)
            if method == "PATCH":
                row.update(json.loads(request.content))
                return httpx.Response(200, json=row)
            if method == "DELETE":
                return httpx.Response(200, json=rows.pop(int(row_id)))
            return httpx.Response(405)

        return httpx.MockTransport(handle)

    @staticmethod
    def _page(rows: list, request: httpx.Request) -> list:
        skip = int(request.url.params.get("skip", 0))
        limit = int(request.url.params.get("limit", 100))
        return rows[skip: skip + limit]


def make_settings(**overrides) -> Settings:
    base = dict(
        provider="fake", model=None, temperature=0.0,
        services=[
            ServiceConfig("customer", "http://customer.test", "customer accounts"),
            ServiceConfig("product", "http://product.test", "product catalogue"),
            ServiceConfig("order", "http://order.test", "orders"),
        ],
        read_only=False, confirm_writes=False, router_threshold=100, max_steps=6,
        history_messages=30, max_tool_chars=12000, http_timeout=5.0, log_dir=Path("logs/test"),
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def backend() -> FakeBackend:
    b = FakeBackend()
    b.add("customers", name="Jane Doe", email="jane@example.com", address="5 Oak Ave")
    b.add("customers", name="John Smith", email="john@example.com", address="9 Elm St")
    b.add("customers", name="Johnny Bravo", email="johnny@example.com")
    b.add("products", name="USB-C Hub", sku="HUB-01", price=29.99, stock_quantity=10)
    b.add("products", name="Wireless Keyboard", sku="KB-200", price=49.99, stock_quantity=2)
    return b


@pytest.fixture
def transports(backend):
    return {name: backend.transport(name) for name in ("customer", "product", "order")}


@pytest.fixture
def services(transports):
    return discover(make_settings().services, transports=transports)
