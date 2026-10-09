import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from agent.config import Settings, load_settings
from agent.discovery import Service, discover
from agent.graph import build_graph, reply_text
from agent.http import new_request_id, request_id
from agent.llm import build_chat_model, model_name
from agent.tools import Toolbox
from agent.trace import Tracer

log = logging.getLogger(__name__)


@dataclass
class Turn:
    reply: str = ""
    # set when the graph is paused waiting for a yes/no: {"tool": ..., "args": ...}
    approval: dict | None = None
    tool_calls: list[dict] = field(default_factory=list)


class Assistant:
    """One chat session. Front-ends (terminal, Streamlit) only talk to this."""

    def __init__(self, settings: Settings | None = None, llm: BaseChatModel | None = None,
                 transports: dict[str, Any] | None = None,
                 on_step: Callable[[str, dict], None] | None = None):
        self.settings = settings or load_settings()
        self.llm = llm or build_chat_model(self.settings.provider, self.settings.model, self.settings.temperature)
        self.model_label = (f"{self.settings.provider} / {model_name(self.settings.provider, self.settings.model)}"
                            if llm is None else type(llm).__name__)
        self.transports = transports
        self.on_step = on_step or (lambda kind, data: None)
        self.session_id = uuid.uuid4().hex[:8]
        self.trace = Tracer(self.settings.log_dir, self.session_id)
        self.checkpointer = InMemorySaver()
        self.thread_id = self.session_id
        self.reload()

    def reload(self) -> list[Service]:
        """Re-read every service's OpenAPI spec. Conversation memory survives."""
        self.services = discover(self.settings.services, self.settings.http_timeout, self.transports)
        self.toolbox = Toolbox(self.services, self.settings, on_call=self._tool_finished)
        self.graph = build_graph(self.llm, self.toolbox, self.services, self.settings,
                                 self.checkpointer, trace=self.trace)
        self.trace("discovery", services={s.name: len(s.operations) if s.healthy else s.error
                                          for s in self.services},
                   tools=len(self.toolbox.entries))
        return self.services

    def reset(self) -> None:
        self.thread_id = uuid.uuid4().hex[:8]

    def ask(self, text: str) -> Turn:
        self._request_id = new_request_id()
        self.trace("user", text=text)
        return self._run({"messages": [HumanMessage(text)]})

    def answer_approval(self, approved: bool) -> Turn:
        # same turn, same id, even if the answer comes from another thread (Streamlit)
        request_id.set(getattr(self, "_request_id", "-"))
        return self._run(Command(resume=approved))

    @property
    def pending_approval(self) -> dict | None:
        state = self.graph.get_state(self._config())
        return state.interrupts[0].value if state.interrupts else None

    def _config(self) -> dict:
        # each agent<->tools round is two graph steps, plus the router
        return {"configurable": {"thread_id": self.thread_id},
                "recursion_limit": self.settings.max_steps * 2 + 2}

    def _run(self, payload: Any) -> Turn:
        turn = Turn()
        started = time.perf_counter()
        try:
            for chunk in self.graph.stream(payload, self._config(), stream_mode="updates"):
                if "__interrupt__" in chunk:
                    turn.approval = chunk["__interrupt__"][0].value
                    self.on_step("approval", turn.approval)
                    continue
                for update in chunk.values():
                    for msg in (update or {}).get("messages", []):
                        self._report(msg, turn)
        except GraphRecursionError:
            turn.reply = (f"I stopped after {self.settings.max_steps} tool rounds without finishing. "
                          "Try splitting the request into smaller steps.")
            self.trace("step_limit")
            return turn

        if turn.approval is None:
            messages = self.graph.get_state(self._config()).values.get("messages", [])
            last = messages[-1] if messages else None
            turn.reply = reply_text(last) if isinstance(last, AIMessage) else ""
            self.trace("reply", text=turn.reply, tool_calls=len(turn.tool_calls),
                       ms=round((time.perf_counter() - started) * 1000))
        return turn

    def _report(self, msg: Any, turn: Turn) -> None:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls:
                turn.tool_calls.append({"name": call["name"], "args": call["args"]})
                self.on_step("call", {"name": call["name"], "args": call["args"]})
        elif isinstance(msg, ToolMessage) and str(msg.content).startswith("ERROR"):
            self.on_step("error", {"name": msg.name, "error": str(msg.content)})

    def _tool_finished(self, event: dict) -> None:
        self.trace("tool", **event)
