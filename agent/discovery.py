"""Turn each service's OpenAPI document into a list of callable operations.

Nothing here knows about customers or orders. Point the registry at a 21st
FastAPI service and its endpoints show up as tools on the next start.
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from agent.config import ServiceConfig
from agent.http import ServiceClient, ServiceError

log = logging.getLogger(__name__)

SKIP_PATHS = {"/health", "/healthz", "/ready", "/metrics"}
METHODS = ("get", "post", "put", "patch", "delete")


@dataclass
class Operation:
    service: str
    name: str
    method: str
    path: str
    summary: str
    path_params: list[str]
    query_params: list[str]
    body_fields: list[str]
    args_schema: dict
    is_list: bool = False

    @property
    def destructive(self) -> bool:
        return self.method == "DELETE"

    @property
    def writes(self) -> bool:
        return self.method != "GET"


@dataclass
class Service:
    config: ServiceConfig
    client: ServiceClient
    title: str = ""
    description: str = ""
    version: str = ""
    operations: list[Operation] = field(default_factory=list)
    healthy: bool = False
    error: str | None = None

    @property
    def name(self) -> str:
        return self.config.name

    def summary_line(self) -> str:
        desc = self.config.description or self.description or self.title
        return f"{self.name}: {desc}"


def discover(configs: list[ServiceConfig], timeout: float = 10.0,
             transports: dict[str, Any] | None = None) -> list[Service]:
    transports = transports or {}
    services = []
    for cfg in configs:
        client = ServiceClient(cfg.name, cfg.url, timeout=timeout, transport=transports.get(cfg.name))
        svc = Service(config=cfg, client=client)
        try:
            spec = client.get("/openapi.json")
        except ServiceError as exc:
            svc.error = str(exc)
            log.warning("skipping %s: %s", cfg.name, exc)
        else:
            info = spec.get("info", {})
            svc.title = info.get("title", cfg.name)
            svc.description = info.get("description", "")
            svc.version = info.get("version", "")
            impostor = _claims_other_service(svc.title, cfg.name, [c.name for c in configs])
            if impostor:
                # Swapped ports in .env are easy to do and otherwise silent: the agent
                # would happily call the order API thinking it's the product API.
                svc.error = (f"{cfg.url} answers as '{svc.title}', which looks like the {impostor} service. "
                             f"Check {cfg.name.upper()}_SERVICE_URL.")
                log.error("skipping %s: %s", cfg.name, svc.error)
            else:
                svc.operations = parse_operations(cfg, spec)
                svc.healthy = True
        services.append(svc)

    _dedupe_names([op for s in services for op in s.operations])
    return services


def _claims_other_service(title: str, expected: str, known: list[str]) -> str | None:
    words = set(re.findall(r"[a-z0-9]+", title.lower()))
    if expected.lower() in words:
        return None
    return next((name for name in known if name != expected and name.lower() in words), None)


def parse_operations(cfg: ServiceConfig, spec: dict) -> list[Operation]:
    found: list[Operation] = []
    for path, item in spec.get("paths", {}).items():
        if path in SKIP_PATHS:
            continue
        methods = {m for m in METHODS if m in item}
        for method in METHODS:
            if method not in item:
                continue
            # PUT "replace everything" next to a PATCH is a footgun for an LLM:
            # it will happily null out fields it didn't mention.
            if method == "put" and "patch" in methods:
                continue
            op = _build(cfg.name, path, method, item[method], spec)
            if op.name in cfg.exclude:
                continue
            found.append(op)
    return found


def _build(service: str, path: str, method: str, raw: dict, spec: dict) -> Operation:
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params, query_params, body_fields = [], [], []

    for param in raw.get("parameters", []):
        where = param.get("in")
        if where not in ("path", "query"):
            continue
        schema = clean_schema(param.get("schema", {}), spec)
        if param.get("description"):
            schema.setdefault("description", param["description"])
        properties[param["name"]] = schema
        if param.get("required") or where == "path":
            required.append(param["name"])
        (path_params if where == "path" else query_params).append(param["name"])

    body = raw.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
    if body:
        body = clean_schema(body, spec)
        for key, prop in body.get("properties", {}).items():
            if key in properties:  # name clash with a path/query param; rare enough to just skip
                continue
            properties[key] = prop
            body_fields.append(key)
        if raw.get("requestBody", {}).get("required"):
            required.extend(r for r in body.get("required", []) if r in body_fields)

    response = (raw.get("responses", {}).get("200", {}) or raw.get("responses", {}).get("201", {}))
    resp_schema = response.get("content", {}).get("application/json", {}).get("schema", {})
    is_list = resp_schema.get("type") == "array"

    summary = raw.get("summary") or raw.get("description") or f"{method.upper()} {path}"
    return Operation(
        service=service,
        name=operation_name(raw.get("operationId"), path, method),
        method=method.upper(),
        path=path,
        summary=summary,
        path_params=path_params,
        query_params=query_params,
        body_fields=body_fields,
        args_schema={"type": "object", "properties": properties, "required": required},
        is_list=is_list,
    )


def operation_name(operation_id: str | None, path: str, method: str) -> str:
    """FastAPI builds operationIds as <func><sanitised path>_<method>, e.g.
    `get_order_api_v1_orders__order_id__get`. The handler name alone reads far
    better in a tool list, so strip the generated tail when it's there."""
    if operation_id:
        tail = re.sub(r"\W", "_", path) + "_" + method
        if operation_id.endswith(tail) and len(operation_id) > len(tail):
            return operation_id[: -len(tail)]
        name = operation_id
    else:
        name = f"{method}_{path}"
    return re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")[:64]


def clean_schema(schema: dict, root: dict, depth: int = 0) -> dict:
    """Inline $refs and simplify Pydantic's output into something small models
    handle: no titles, Optional[X] collapsed to X, int-or-str collapsed to str.

    `root` is the document local refs point into (the OpenAPI spec, or a
    Pydantic schema with $defs)."""
    if depth > 8:
        return {}
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        merged = {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
        return clean_schema(merged, root, depth + 1)

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "title":
            continue
        if key == "default" and value is None:
            continue
        if key in ("anyOf", "oneOf"):
            options = [clean_schema(v, root, depth + 1) for v in value]
            non_null = [o for o in options if o.get("type") != "null"]
            if len(non_null) == 1:
                out.update(non_null[0])
            elif {o.get("type") for o in non_null} == {"integer", "string"}:
                out["type"] = "string"
            else:
                out[key] = non_null
            continue
        if key == "$defs":
            continue
        if key == "properties":
            out[key] = {k: clean_schema(v, root, depth + 1) for k, v in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = clean_schema(value, root, depth + 1)
        else:
            out[key] = value
    return out


def _resolve_ref(ref: str, root: dict) -> dict:
    # only local refs: "#/components/schemas/OrderCreate", "#/$defs/OrderLine"
    node: Any = root
    for part in ref.lstrip("#/").split("/"):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    return node if isinstance(node, dict) else {}


def _dedupe_names(operations: list[Operation]) -> None:
    seen: dict[str, int] = {}
    for op in operations:
        seen[op.name] = seen.get(op.name, 0) + 1
    for op in operations:
        if seen[op.name] > 1:
            op.name = f"{op.service}_{op.name}"[:64]
