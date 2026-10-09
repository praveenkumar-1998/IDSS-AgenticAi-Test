"""The agent loop.

    START -> route -> agent <-> tools -> END

route   picks which services the question needs. With 3 services every tool
        fits in the prompt, so it's skipped; past AGENT_ROUTER_THRESHOLD tools it
        keeps the model from drowning in 200 schemas.
agent   one LLM call with only the routed tools bound.
tools   runs the calls. Destructive ones pause the graph with interrupt() until
        a human answers; the checkpointer holds the state while it waits.
"""
import difflib
import json
import logging
import re
import uuid
from datetime import date

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.config import Settings
from agent.discovery import Service
from agent.tools import Toolbox

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the operations assistant for an online store. You answer questions and \
carry out tasks by calling tools that talk to the live backend services. Today is {today}.

Services you can use for this request:
{services}

Rules:
- Every name, id, count, total, price or status in your answer must come from a \
tool result in this conversation. If you haven't looked it up, look it up. If a \
tool returns no records, say so plainly. Never invent example data.
- For counts, totals, revenue or "best selling" questions use the summary tools \
(business_overview, sales_by_product, stock_report, customer_order_summary). They cover every \
record; list endpoints return one page only.
- When the user names a customer or product, find it first. If more than one \
record matches, ask which one before changing anything.
- Use place_order to create orders and cancel_order to cancel them; they keep \
stock correct.
- Leave optional tool arguments out unless the user asked for something \
specific; the defaults are sensible.
- If a tool returns ERROR, fix the arguments or explain the problem. Don't repeat \
the same failing call.
- If the user declines an action, don't try it another way.
- Finish every step of a multi-step request, then say what was done, with ids.
- Keep answers short. Use a small table or bullets for several records.\
"""

ROUTER_PROMPT = """\
Pick the backend services needed to handle the user's latest message. \
Use the conversation for context ("cancel it" refers to whatever was discussed). \
Pick every service the task touches, e.g. placing an order needs customer, product and order. \
Return an empty list only for small talk.

Services:
{services}\
"""


class Route(BaseModel):
    services: list[str] = Field(description="service names from the list")


class AgentState(MessagesState):
    services: list[str]


def build_graph(llm: BaseChatModel, toolbox: Toolbox, services: list[Service], settings: Settings,
                checkpointer: BaseCheckpointSaver, trace=lambda event, **kw: None):
    live = [s for s in services if s.healthy]
    names = [s.name for s in live]
    use_router = len(toolbox.entries) > settings.router_threshold and len(names) > 1
    catalogue = "\n".join(f"- {s.summary_line()}" for s in live)

    def route(state: AgentState) -> dict:
        if not use_router:
            return {"services": names}
        recent = [m for m in state["messages"] if isinstance(m, (HumanMessage, AIMessage)) and m.text][-6:]
        try:
            picked = llm.with_structured_output(Route).invoke(
                [SystemMessage(ROUTER_PROMPT.format(services=catalogue)), *recent])
            chosen = [n for n in picked.services if n in names]
        except Exception:
            # A router that fails closed would make the agent useless; fall back to everything.
            log.warning("router failed, using all services", exc_info=True)
            chosen = list(names)
        # Small models misroute ("delete customer X" right after talking about
        # orders came back as just ["order"]). A service named in the message is
        # always included, whatever the model said.
        last = next((m.text for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")
        chosen += [n for n in names if n not in chosen and re.search(rf"\b{re.escape(n)}s?\b", last, re.I)]
        if not chosen:
            chosen = state.get("services") or names
        trace("route", services=chosen)
        return {"services": chosen}

    def agent(state: AgentState) -> dict:
        selected = state.get("services") or names
        tools = toolbox.for_services(selected)
        prompt = SYSTEM_PROMPT.format(
            today=date.today().isoformat(),
            services="\n".join(f"- {s.summary_line()}" for s in live if s.name in selected),
        )
        model = llm.bind_tools(tools) if tools else llm
        messages = [SystemMessage(prompt), *prepare_history(state["messages"], settings.history_messages)]
        reply = model.invoke(messages)
        if tools and looks_like_text_tool_call(reply):
            # 3-4B models sometimes print the call as JSON instead of making it.
            # One nudge fixes most of these; the nudge itself isn't kept in history.
            trace("tool_call_repair", text=reply.text[:200])
            reply = model.invoke([*messages, reply, HumanMessage(
                "You wrote a tool call as plain text. Make the actual tool call instead, using one of "
                "the available tools, or answer without tools.")])
            if looks_like_text_tool_call(reply):
                # Still text. Better to run it (bad arguments come back as an error the
                # model can fix) than to show the user a JSON blob.
                reply = parse_text_tool_call(reply, [t.name for t in tools]) or reply
        return {"messages": [reply]}

    def run_tools(state: AgentState) -> dict:
        calls = state["messages"][-1].tool_calls

        # Ask for every approval before running anything. interrupt() re-runs this
        # node from the top on resume, so running safe calls first would run them twice.
        approved = {}
        for call in calls:
            if toolbox.needs_approval(call["name"]):
                approved[call["id"]] = bool(interrupt({"tool": call["name"], "args": call["args"]}))
                trace("approval", tool=call["name"], args=call["args"], approved=approved[call["id"]])

        results = []
        for call in calls:
            tool = toolbox.get(call["name"])
            if tool is None:
                content = (f"ERROR: there is no tool called {call['name']}. "
                           f"Available: {', '.join(t.name for t in toolbox.all())}")
            elif approved.get(call["id"]) is False:
                content = "The user declined this action. It was not performed."
            else:
                content = tool.invoke(call["args"])
            results.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))

        declined = [c["name"] for c in calls if approved.get(c["id"]) is False]
        if declined:
            # Don't let the model narrate this one. In testing, a 3B model answered a
            # rejected cancel_order with "Order 19 was cancelled."
            results.append(AIMessage(content=f"Okay, I did not run {', '.join(declined)}. Nothing was changed."))
        return {"messages": results}

    def next_step(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else END

    def after_tools(state: AgentState) -> str:
        return END if isinstance(state["messages"][-1], AIMessage) else "agent"

    graph = StateGraph(AgentState)
    graph.add_node("route", route)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "route")
    graph.add_edge("route", "agent")
    graph.add_conditional_edges("agent", next_step, ["tools", END])
    graph.add_conditional_edges("tools", after_tools, ["agent", END])
    return graph.compile(checkpointer=checkpointer)


def prepare_history(messages: list[AnyMessage], keep: int) -> list[AnyMessage]:
    """Last `keep` messages, starting on a human turn, with no orphaned tool calls.

    Orphans happen when a run is cut off by the step limit or an exception
    between the model asking for a tool and the tool answering. Every provider
    rejects that history, so the dangling calls are dropped."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    cleaned: list[AnyMessage] = []
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            missing = [c for c in m.tool_calls if c["id"] not in answered]
            if missing:
                kept = [c for c in m.tool_calls if c["id"] in answered]
                m = AIMessage(content=m.content, tool_calls=kept, id=m.id)
                if not kept and not m.content:
                    continue
        cleaned.append(m)
    if len(cleaned) <= keep:
        return cleaned
    return trim_messages(cleaned, max_tokens=keep, token_counter=len, strategy="last",
                         start_on="human", allow_partial=False)


TEXT_CALL = re.compile(r'\{\s*"name"\s*:\s*"\w+"\s*,\s*"(parameters|arguments|args)"\s*:')


def looks_like_text_tool_call(message: AIMessage) -> bool:
    return not message.tool_calls and bool(TEXT_CALL.search(message.text or ""))


def parse_text_tool_call(message: AIMessage, tool_names: list[str]) -> AIMessage | None:
    match = TEXT_CALL.search(message.text or "")
    if not match:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(message.text[match.start():])
    except ValueError:
        return None
    # "find_customer" for find_customers is the usual slip
    name = difflib.get_close_matches(str(payload.get("name")), tool_names, n=1, cutoff=0.8)
    if not name:
        return None
    args = payload.get("parameters") or payload.get("arguments") or payload.get("args") or {}
    if not isinstance(args, dict):
        return None
    return AIMessage(content="", tool_calls=[{"name": name[0], "args": args, "id": f"text-{uuid.uuid4().hex[:8]}"}])


def reply_text(message: AnyMessage) -> str:
    text = message.text if hasattr(message, "text") else str(message.content)
    # some local models leak their reasoning even with thinking switched off
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
