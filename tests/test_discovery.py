import httpx

from agent.config import ServiceConfig
from agent.discovery import _dedupe_names, clean_schema, discover, operation_name, parse_operations

from .conftest import make_settings


def names(services, service):
    return {op.name for s in services if s.name == service for op in s.operations}


def test_operation_names_drop_fastapi_suffix():
    assert operation_name("get_order_api_v1_orders__order_id__get", "/api/v1/orders/{order_id}", "get") == "get_order"
    assert operation_name("weird-id", "/x", "get") == "weird_id"
    assert operation_name(None, "/api/v1/things/", "post") == "post_api_v1_things"


def test_all_three_services_discovered(services):
    assert all(s.healthy for s in services)
    assert names(services, "order") == {
        "create_order", "list_orders", "list_orders_by_customer", "get_order", "patch_order", "delete_order",
    }


def test_put_hidden_when_patch_exists(services):
    assert "update_customer" not in names(services, "customer")
    assert "patch_customer" in names(services, "customer")


def test_order_body_refs_are_inlined(services):
    order = next(s for s in services if s.name == "order")
    create = next(op for op in order.operations if op.name == "create_order")
    items = create.args_schema["properties"]["items"]
    assert items["type"] == "array"
    assert set(items["items"]["properties"]) == {"product_id", "product_name", "quantity", "unit_price"}
    assert set(create.args_schema["required"]) >= {"customer_id", "items"}
    # Optional[str] collapsed, no titles left behind
    assert create.args_schema["properties"]["notes"] == {"type": "string"}


def test_path_params_required_and_delete_flagged(services):
    customer = next(s for s in services if s.name == "customer")
    delete = next(op for op in customer.operations if op.name == "delete_customer")
    assert delete.args_schema["required"] == ["customer_id"]
    assert delete.destructive


def test_unreachable_service_is_marked_down_not_fatal(transports):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    settings = make_settings()
    transports = {**transports, "product": httpx.MockTransport(refuse)}
    services = discover(settings.services, transports=transports)
    product = next(s for s in services if s.name == "product")
    assert not product.healthy and "unreachable" in product.error
    assert all(s.healthy for s in services if s.name != "product")


def test_swapped_urls_are_caught(transports):
    swapped = {**transports, "product": transports["order"], "order": transports["product"]}
    services = discover(make_settings().services, transports=swapped)
    product = next(s for s in services if s.name == "product")
    assert not product.healthy and "looks like the order service" in product.error
    assert next(s for s in services if s.name == "customer").healthy


def test_excluded_operations_are_skipped(transports, backend):
    import json
    from .conftest import FIXTURES
    spec = json.loads((FIXTURES / "order_openapi.json").read_text())
    ops = parse_operations(ServiceConfig("order", "http://x", exclude=["delete_order"]), spec)
    assert "delete_order" not in {op.name for op in ops}


def test_name_clash_across_services_gets_prefixed(services):
    ops = [op for s in services for op in s.operations]
    for op in ops:
        if op.name == "list_orders":
            op.name = "list_customers"  # pretend two services expose the same handler name
    _dedupe_names(ops)
    assert {"customer_list_customers", "order_list_customers"} <= {op.name for op in ops}


def test_clean_schema_collapses_int_or_str():
    schema = {"anyOf": [{"type": "integer"}, {"type": "string"}], "title": "Customer"}
    assert clean_schema(schema, {}) == {"type": "string"}
