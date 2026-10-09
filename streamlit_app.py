"""Browser front-end for the same agent the terminal uses.

    uv run streamlit run streamlit_app.py
"""
import json

import streamlit as st

from agent.runtime import Assistant
from agent.trace import setup_logging

st.set_page_config(page_title="Store assistant")


@st.cache_resource
def _logging_once() -> bool:
    from agent.config import load_settings
    setup_logging(load_settings().log_dir)
    return True


def get_assistant() -> Assistant:
    if "assistant" not in st.session_state:
        steps: list[str] = []
        st.session_state.steps = steps
        st.session_state.assistant = Assistant(
            on_step=lambda kind, data: steps.append(
                f"{data['name']} {json.dumps(data['args'])}" if kind == "call" else
                f"error in {data['name']}: {data['error'][:160]}" if kind == "error" else ""))
        st.session_state.history = []
    return st.session_state.assistant


def finish(turn) -> None:
    calls = [s for s in st.session_state.steps if s]
    st.session_state.steps.clear()
    if turn.approval:
        st.session_state.pending = turn.approval
        st.session_state.pending_calls = calls
        return
    st.session_state.pending = None
    calls = st.session_state.pop("pending_calls", []) + calls
    st.session_state.history.append({"role": "assistant", "content": turn.reply or "(no answer)", "calls": calls})


def run(fn, *args) -> None:
    try:
        with st.spinner("Working..."):
            finish(fn(*args))
    except Exception as exc:
        st.session_state.history.append(
            {"role": "assistant", "content": f"The model call failed: {type(exc).__name__}: {str(exc)[:300]}"})


_logging_once()
try:
    assistant = get_assistant()
except (RuntimeError, ValueError) as exc:
    st.error(f"Can't start the assistant: {exc}")
    st.stop()

with st.sidebar:
    st.subheader("Services")
    for svc in assistant.services:
        state = "up" if svc.healthy else "down"
        st.markdown(f"**{svc.name}** - {state} - [{svc.config.url}]({svc.config.url}/docs)")
    st.caption(f"Model: {assistant.model_label}, {len(assistant.toolbox.entries)} tools")
    if st.button("Reload services", use_container_width=True):
        assistant.reload()
        st.rerun()
    if st.button("New conversation", use_container_width=True):
        assistant.reset()
        st.session_state.history = []
        st.session_state.pending = None
        st.rerun()

st.title("Store assistant")
st.caption("Answers come from the live customer, product and order services.")

for msg in st.session_state.history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("calls"):
            with st.expander(f"{len(msg['calls'])} service call(s)"):
                st.code("\n".join(msg["calls"]), language=None)

pending = st.session_state.get("pending")
if pending:
    with st.chat_message("assistant"):
        st.warning(f"I need your OK to run **{pending['tool']}** with `{json.dumps(pending['args'])}`")
        yes, no = st.columns(2)
        if yes.button("Approve", type="primary", use_container_width=True):
            run(assistant.answer_approval, True)
            st.rerun()
        if no.button("Reject", use_container_width=True):
            run(assistant.answer_approval, False)
            st.rerun()

prompt = st.chat_input("Ask about customers, products or orders", disabled=bool(pending))
if prompt:
    st.session_state.history.append({"role": "user", "content": prompt})
    run(assistant.ask, prompt)
    st.rerun()
