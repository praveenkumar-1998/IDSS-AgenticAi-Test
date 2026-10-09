import json

from agent.tools import Toolbox
from agent.workflows import REGISTRY

from .conftest import make_settings


def toolbox(services, **settings):
    return Toolbox(services, make_settings(**settings))


def test_place_order_replaces_raw_create_order(services):
    tb = toolbox(services)
    assert "place_order" in tb.entries
    assert "create_order" not in tb.entries  # the raw endpoint lets the model invent prices


def test_deletes_and_cancel_need_approval(services):
    tb = toolbox(services)
    assert tb.needs_approval("delete_customer")
    assert tb.needs_approval("cancel_order")
    assert not tb.needs_approval("list_orders")
    assert not tb.needs_approval("place_order")


def test_confirm_writes_mode(services):
    tb = toolbox(services, confirm_writes=True)
    assert tb.needs_approval("place_order")
    assert tb.needs_approval("patch_customer")
    assert not tb.needs_approval("get_customer")


def test_read_only_hides_every_write(services):
    tb = toolbox(services, read_only=True)
    for name, entry in tb.entries.items():
        assert not entry.writes, name
    assert "business_overview" in tb.entries


def test_workflow_dropped_when_its_service_is_down(services):
    healthy = [s for s in services if s.name != "product"]
    tb = Toolbox(healthy, make_settings())
    assert "place_order" not in tb.entries
    assert "customer_order_summary" in tb.entries
    # with place_order gone the raw endpoint is the only way left, so it comes back
    assert "create_order" in tb.entries


def test_string_ids_are_coerced(services):
    tb = toolbox(services)
    out = json.loads(tb.get("get_customer").invoke({"customer_id": "1"}))
    assert out["name"] == "Jane Doe"
    out = json.loads(tb.get("get_customer").invoke({"customer_id": 2.0}))
    assert out["name"] == "John Smith"


def test_bad_arguments_come_back_as_text(services):
    tb = toolbox(services)
    assert tb.get("get_order").invoke({"order_id": "four"}).startswith("ERROR: invalid arguments")
    assert "items" in tb.get("place_order").invoke({"customer": "jane"})


def test_http_errors_come_back_as_text(services):
    tb = toolbox(services)
    out = tb.get("get_customer").invoke({"customer_id": 99})
    assert out.startswith("ERROR") and "404" in out


def test_empty_list_is_spelled_out(services):
    out = toolbox(services).get("list_orders").invoke({})
    assert "0 results" in out and "do not make any up" in out


def test_full_page_warns_that_more_exist(services, backend):
    for i in range(5):
        backend.add("customers", name=f"Extra {i}", email=f"x{i}@example.com")
    out = json.loads(toolbox(services).get("list_customers").invoke({"limit": 3}))
    assert out["count"] == 3 and "more records probably exist" in out["note"]


def test_large_results_are_truncated(services, backend):
    for i in range(50):
        backend.add("customers", name="A" * 50, email=f"long{i}@example.com")
    out = toolbox(services, max_tool_chars=500).get("list_customers").invoke({})
    assert len(out) < 700 and "truncated" in out


def test_tool_calls_are_reported(services):
    events = []
    tb = Toolbox(services, make_settings(), on_call=events.append)
    tb.get("get_customer").invoke({"customer_id": 1})
    tb.get("get_customer").invoke({"customer_id": 42})
    assert [e["status"] for e in events] == ["ok", "error"]
    assert all("ms" in e for e in events)


def test_crashing_workflow_does_not_raise(services):
    from agent.workflows import Workflow

    def broken(ctx):
        """Always explodes."""
        raise KeyError("boom")

    tb = Toolbox(services, make_settings(), workflows=[*REGISTRY, Workflow(broken, "broken", ("order",))])
    assert "failed unexpectedly" in tb.get("broken").invoke({})


def test_router_scoping(services):
    tb = toolbox(services)
    product_tools = {t.name for t in tb.for_services(["product"])}
    assert "list_products" in product_tools
    assert "list_customers" not in product_tools
    assert "place_order" in product_tools  # cross-service workflows show up for each service they touch
