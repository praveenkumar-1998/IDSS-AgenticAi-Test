"""Graph mechanics with a scripted model: no LLM, no network."""
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent.graph import prepare_history
from agent.runtime import Assistant

from .conftest import make_settings


class ScriptedModel(BaseChatModel):
    """Replays canned AIMessages and records what it was shown."""
    script: list
    seen: list = []
    bound: list = []
    route: Any = None

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])

    def bind_tools(self, tools, **kwargs):
        self.bound.append([t.name for t in tools])
        return self

    def with_structured_output(self, schema, **kwargs):
        route = self.route

        class Router:
            def invoke(self, messages):
                if isinstance(route, Exception):
                    raise route
                return schema(services=route)
        return Router()


def call(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture
def make_assistant(transports, tmp_path):
    def build(script, **settings):
        model = ScriptedModel(script=script, seen=[], bound=[])
        settings.setdefault("log_dir", tmp_path)
        return Assistant(make_settings(**settings), llm=model, transports=transports), model
    return build


def test_question_answered_from_tool_result(make_assistant):
    assistant, model = make_assistant([
        call("business_overview", {}),
        AIMessage(content="You have 3 customers."),
    ])
    turn = assistant.ask("how many customers?")
    assert turn.reply == "You have 3 customers."
    assert turn.tool_calls == [{"name": "business_overview", "args": {}}]
    tool_msg = model.seen[1][-1]
    assert isinstance(tool_msg, ToolMessage) and '"total": 3' in tool_msg.content


def test_delete_waits_for_approval_and_respects_no(make_assistant, backend):
    assistant, model = make_assistant([
        call("delete_customer", {"customer_id": 1}),
        AIMessage(content="Customer deleted."),  # what a confused model might say; must never be used
    ])
    turn = assistant.ask("delete jane")
    assert turn.approval == {"tool": "delete_customer", "args": {"customer_id": 1}}
    assert 1 in backend.tables["customers"]  # nothing ran yet

    turn = assistant.answer_approval(False)
    assert turn.reply == "Okay, I did not run delete_customer. Nothing was changed."
    assert 1 in backend.tables["customers"]
    assert len(model.seen) == 1  # the model never got to describe the outcome

    # the conversation carries on normally afterwards
    model.script[:] = [AIMessage(content="Sure.")]
    assert assistant.ask("thanks").reply == "Sure."


def test_approved_delete_runs_once(make_assistant, backend):
    assistant, _ = make_assistant([
        AIMessage(content="", tool_calls=[
            {"name": "get_customer", "args": {"customer_id": 1}, "id": "a"},
            {"name": "delete_customer", "args": {"customer_id": 1}, "id": "b"},
        ]),
        AIMessage(content="Deleted."),
    ])
    assistant.ask("look up and delete customer 1")
    before = list(backend.calls)
    assistant.answer_approval(True)
    after = backend.calls[len(before):]
    assert 1 not in backend.tables["customers"]
    # the safe call in the same batch must not be repeated on resume
    assert after.count(("GET", "/api/v1/customers/1")) == 1
    assert after.count(("DELETE", "/api/v1/customers/1")) == 1


def test_unknown_tool_gets_an_error_not_a_crash(make_assistant):
    assistant, model = make_assistant([call("drop_database", {}), AIMessage(content="Can't do that.")])
    assert assistant.ask("drop everything").reply == "Can't do that."
    assert "no tool called drop_database" in model.seen[1][-1].content


def test_tool_call_written_as_text_gets_one_retry(make_assistant):
    assistant, model = make_assistant([
        AIMessage(content='Here you go: {"name": "find_customers", "parameters": {"query": "zaphod"}}'),
        call("find_customers", {"query": "zaphod"}),
        AIMessage(content="No customer called Zaphod."),
    ])
    turn = assistant.ask("phone number of Zaphod?")
    assert turn.reply == "No customer called Zaphod."
    assert turn.tool_calls == [{"name": "find_customers", "args": {"query": "zaphod"}}]
    history = assistant.graph.get_state(assistant._config()).values["messages"]
    assert not any("plain text" in str(m.content) for m in history)


def test_stubborn_text_tool_call_is_parsed_and_run(make_assistant):
    text = '{"name": "find_customer", "parameters": {"query": "zaphod"}}'
    assistant, model = make_assistant([
        AIMessage(content=text), AIMessage(content=text),
        AIMessage(content="No customer called Zaphod."),
    ])
    turn = assistant.ask("phone number of Zaphod?")
    assert turn.tool_calls == [{"name": "find_customers", "args": {"query": "zaphod"}}]
    assert turn.reply == "No customer called Zaphod."
    assert "No records found" in model.seen[-1][-1].content


def test_step_limit_stops_runaway_loops(make_assistant):
    assistant, _ = make_assistant([call("list_orders", {}, f"c{i}") for i in range(20)], max_steps=3)
    assert "stopped after 3 tool rounds" in assistant.ask("loop forever").reply


def test_router_narrows_tools_when_there_are_many(make_assistant):
    assistant, model = make_assistant([AIMessage(content="ok")], router_threshold=5)
    model.route = ["product"]
    assistant.ask("what does the hub cost?")
    assert "list_products" in model.bound[0] and "list_customers" not in model.bound[0]


def test_router_always_includes_services_named_in_the_message(make_assistant):
    assistant, model = make_assistant([AIMessage(content="ok")], router_threshold=5)
    model.route = ["order"]
    assistant.ask("delete customer Chen Wei")
    assert "delete_customer" in model.bound[0] and "list_orders" in model.bound[0]


def test_router_failure_falls_back_to_all_tools(make_assistant):
    assistant, model = make_assistant([AIMessage(content="ok")], router_threshold=5)
    model.route = ValueError("model returned junk")
    assistant.ask("hello")
    assert len(model.bound[0]) == len(assistant.toolbox.entries)


def test_memory_carries_across_turns_and_reset_clears_it(make_assistant):
    assistant, model = make_assistant([AIMessage(content="hi Jane"), AIMessage(content="hi"),
                                       AIMessage(content="fresh")])
    assistant.ask("I'm Jane")
    assistant.ask("who am I?")
    assert any(getattr(m, "content", "") == "I'm Jane" for m in model.seen[1])
    assistant.reset()
    assistant.ask("who am I?")
    assert not any(getattr(m, "content", "") == "I'm Jane" for m in model.seen[2])


def test_history_drops_orphaned_tool_calls():
    history = [
        HumanMessage("a"),
        AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}]),
        HumanMessage("b"),
    ]
    cleaned = prepare_history(history, keep=30)
    assert [type(m).__name__ for m in cleaned] == ["HumanMessage", "HumanMessage"]


def test_history_trim_starts_on_a_human_turn():
    history = []
    for i in range(10):
        history += [HumanMessage(f"q{i}"), call("t", {}, str(i)),
                    ToolMessage("r", tool_call_id=str(i)), AIMessage(content=f"a{i}")]
    trimmed = prepare_history(history, keep=6)
    assert isinstance(trimmed[0], HumanMessage) and len(trimmed) <= 6
