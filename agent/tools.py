import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator
from langchain_core.tools import BaseTool, StructuredTool
from langchain_core.tools.base import create_schema_from_function
from pydantic import ValidationError

from agent.config import Settings
from agent.discovery import Operation, Service, clean_schema
from agent.http import ServiceError
from agent.workflows import REGISTRY, Context, Workflow, WorkflowError

log = logging.getLogger(__name__)

DEFAULT_PAGE = 100


@dataclass
class ToolEntry:
    tool: BaseTool
    services: tuple[str, ...]
    writes: bool
    needs_approval: bool
    kind: str  # "api" or "workflow"


class Toolbox:
    """Every tool the agent can use, plus the policy around them (read-only
    mode, which calls need a human yes, how results are shaped)."""

    def __init__(self, services: list[Service], settings: Settings,
                 workflows: list[Workflow] | None = None,
                 on_call: Callable[[dict], None] | None = None):
        self.settings = settings
        self.services = {s.name: s for s in services if s.healthy}
        self.ctx = Context({name: s.client for name, s in self.services.items()})
        self.on_call = on_call or (lambda event: None)
        self.entries: dict[str, ToolEntry] = {}

        replaced: set[str] = set()
        for wf in workflows if workflows is not None else REGISTRY:
            if not set(wf.requires) <= self.services.keys():
                log.info("workflow %s disabled, missing one of %s", wf.name, wf.requires)
                continue
            if wf.writes and settings.read_only:
                continue
            self._add_workflow(wf)
            replaced.update(wf.replaces)

        for svc in self.services.values():
            for op in svc.operations:
                if op.name in replaced or op.name in self.entries:
                    continue
                if op.writes and settings.read_only:
                    continue
                self._add_operation(svc, op)

    def all(self) -> list[BaseTool]:
        return [e.tool for e in self.entries.values()]

    def for_services(self, names: list[str]) -> list[BaseTool]:
        wanted = set(names)
        return [e.tool for e in self.entries.values() if wanted & set(e.services)]

    def needs_approval(self, tool_name: str) -> bool:
        entry = self.entries.get(tool_name)
        return bool(entry and entry.needs_approval)

    def get(self, tool_name: str) -> BaseTool | None:
        entry = self.entries.get(tool_name)
        return entry.tool if entry else None

    def _approval(self, writes: bool, destructive: bool, name: str, svc_cfg=None) -> bool:
        if destructive:
            return True
        if svc_cfg and name in svc_cfg.confirm:
            return True
        return writes and self.settings.confirm_writes

    def _add_operation(self, svc: Service, op: Operation) -> None:
        validator = Draft202012Validator(op.args_schema)

        def run(**kwargs: Any) -> str:
            args = _coerce(_drop_empty(kwargs), op.args_schema)
            errors = sorted(validator.iter_errors(args), key=lambda e: list(e.path))
            if errors:
                msgs = "; ".join(f"{'/'.join(map(str, e.path)) or 'arguments'}: {e.message}" for e in errors[:5])
                hint = ""
                if any(e.validator == "type" and e.validator_value == "integer" for e in errors):
                    # the classic: get_customer(customer_id="Jane Doe")
                    hint = " If you only have a name, look the record up with a find_ tool first to get its id."
                return f"ERROR: invalid arguments for {op.name}: {msgs}.{hint}"
            path = op.path
            for p in op.path_params:
                path = path.replace("{" + p + "}", str(args[p]))
            query = {k: args[k] for k in op.query_params if k in args}
            body = {k: args[k] for k in op.body_fields if k in args}
            return self._execute(op.name, args, lambda: svc.client.request(
                op.method, path, params=query or None, json=body if op.method != "GET" else None),
                page_limit=query.get("limit", DEFAULT_PAGE) if op.is_list else None)

        desc = op.summary.rstrip(".") + f". [{op.service} service, {op.method} {op.path}]"
        if op.destructive:
            desc += " Permanent; the user will be asked to confirm."
        tool = StructuredTool(name=op.name, description=desc, args_schema=op.args_schema, func=run)
        self.entries[op.name] = ToolEntry(
            tool=tool, services=(op.service,), writes=op.writes,
            needs_approval=self._approval(op.writes, op.destructive, op.name, svc.config), kind="api")

    def _add_workflow(self, wf: Workflow) -> None:
        model = create_schema_from_function(wf.name, wf.fn, filter_args=["ctx"], parse_docstring=True)
        raw = model.model_json_schema()
        # The model sees the flattened schema; Pydantic still does the real validation.
        schema = clean_schema(raw, raw)

        def run(**kwargs: Any) -> str:
            try:
                args = model.model_validate(_coerce(_drop_empty(kwargs), schema)).model_dump(exclude_unset=True)
            except ValidationError as exc:
                problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
                                     for e in exc.errors()[:5])
                return f"ERROR: invalid arguments for {wf.name}: {problems}"
            return self._execute(wf.name, kwargs, lambda: wf.fn(self.ctx, **args))

        description = (wf.fn.__doc__ or wf.name).split("\n\n")[0]
        description = " ".join(description.split())
        if wf.destructive:
            description += " The user will be asked to confirm."
        tool = StructuredTool(name=wf.name, description=description, args_schema=schema, func=run)
        self.entries[wf.name] = ToolEntry(
            tool=tool, services=wf.topics or wf.requires, writes=wf.writes,
            needs_approval=self._approval(wf.writes, wf.destructive, wf.name), kind="workflow")

    def _execute(self, name: str, args: dict, call: Callable[[], Any],
                 page_limit: int | None = None) -> str:
        started = time.perf_counter()
        status = "ok"
        try:
            result = call()
        except (ServiceError, WorkflowError) as exc:
            status = "error"
            return f"ERROR: {exc}"
        except Exception as exc:  # a bug in a workflow shouldn't kill the chat session
            status = "crash"
            log.exception("tool %s crashed", name)
            return f"ERROR: {name} failed unexpectedly ({type(exc).__name__}). Tell the user it failed."
        finally:
            self.on_call({"tool": name, "args": args, "status": status,
                          "ms": round((time.perf_counter() - started) * 1000)})
        return self._shape(result, page_limit)

    def _shape(self, result: Any, page_limit: int | None) -> str:
        if isinstance(result, list):
            if not result:
                return "No records found (0 results). Tell the user there are none; do not make any up."
            payload: Any = {"count": len(result), "items": result}
            if page_limit is not None and len(result) >= page_limit:
                payload["note"] = (f"This is one page of {page_limit}; more records probably exist. "
                                   "Use skip to page, or a summary tool for totals.")
            result = payload
        text = json.dumps(result, default=str, ensure_ascii=False)
        limit = self.settings.max_tool_chars
        if len(text) > limit:
            text = text[:limit] + (f"... [truncated, {len(text) - limit} more characters. "
                                   "Narrow the request or use a summary tool.]")
        return text


def _drop_empty(args: dict) -> dict:
    # Small models like sending every optional field as null or "".
    return {k: v for k, v in args.items() if v is not None and v != ""}


def _coerce(value: Any, schema: dict) -> Any:
    """Gemini and some Ollama models send integers as 1.0 and booleans as "true"."""
    kind = schema.get("type")
    if kind == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    if kind == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    if kind == "number" and isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    if kind == "boolean" and isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    if kind == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        return {k: _coerce(v, props.get(k, {})) for k, v in value.items()}
    if kind == "array" and isinstance(value, list):
        return [_coerce(v, schema.get("items", {})) for v in value]
    return value
