"""Terminal chatbot for the customer / product / order services.

    uv run python agent_cli.py              # uses LLM_PROVIDER from .env
    uv run python agent_cli.py -v           # also print tool arguments
    uv run python agent_cli.py --provider gemini --model gemini-flash-latest

Commands inside the chat: /tools  /services  /reload  /reset  /help  /exit
"""
import argparse
import json
import os
import sys

from agent.config import load_settings
from agent.runtime import Assistant
from agent.trace import setup_logging

HELP = """\
Ask in plain English, for example:
  how many customers do we have?
  what are our best selling products?
  summarise the orders for jane@example.com
  order 2 USB-C hubs for Jane Doe and confirm it
  cancel order 4

  /tools     list the tools the agent can call
  /services  show discovered services
  /reload    re-read the services' OpenAPI specs (after a deploy)
  /reset     forget the conversation
  /exit      quit
"""


def short(args: dict, limit: int = 90) -> str:
    text = json.dumps(args, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def print_services(assistant: Assistant) -> None:
    for svc in assistant.services:
        if svc.healthy:
            print(f"  up    {svc.name:<10} {svc.config.url}  ({len(svc.operations)} endpoints)")
        else:
            print(f"  DOWN  {svc.name:<10} {svc.config.url}  - {svc.error}")


def print_tools(assistant: Assistant) -> None:
    for name, entry in assistant.toolbox.entries.items():
        flag = " [asks first]" if entry.needs_approval else ""
        print(f"  {name:<28} {entry.kind:<8} {','.join(entry.services)}{flag}")


def ask_yes_no(request: dict) -> bool:
    print(f"\n  The agent wants to run {request['tool']} {short(request['args'], 200)}")
    try:
        return input("  Allow it? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", help="ollama, gemini, openai or anthropic")
    parser.add_argument("--model")
    parser.add_argument("-v", "--verbose", action="store_true")
    opts = parser.parse_args()

    if opts.provider:
        os.environ["LLM_PROVIDER"] = opts.provider
    if opts.model:
        os.environ["LLM_MODEL"] = opts.model

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    settings = load_settings()
    setup_logging(settings.log_dir, opts.verbose)

    def on_step(kind: str, data: dict) -> None:
        if kind == "call":
            print(f"  .. {data['name']} {short(data['args']) if opts.verbose else ''}".rstrip())
        elif kind == "error" and opts.verbose:
            print(f"  !! {data['name']}: {data['error'][:200]}")

    try:
        assistant = Assistant(settings, on_step=on_step)
    except (RuntimeError, ValueError) as exc:
        print(f"Can't start: {exc}")
        return 1

    print(f"Store assistant  -  model: {assistant.model_label}")
    print_services(assistant)
    if not any(s.healthy for s in assistant.services):
        print("\nNo service is reachable. Start them first (see README), then type /reload.")
    mode = "read-only" if settings.read_only else "read/write"
    print(f"{len(assistant.toolbox.entries)} tools, {mode}. Type /help for examples.\n")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue

        cmd = text.lower()
        if cmd in ("/exit", "/quit", "exit", "quit"):
            break
        if cmd == "/help":
            print(HELP)
            continue
        if cmd == "/reset":
            assistant.reset()
            print("Conversation cleared.\n")
            continue
        if cmd == "/services":
            print_services(assistant)
            continue
        if cmd == "/tools":
            print_tools(assistant)
            continue
        if cmd == "/reload":
            assistant.reload()
            print_services(assistant)
            print(f"{len(assistant.toolbox.entries)} tools.\n")
            continue

        try:
            turn = assistant.ask(text)
            while turn.approval:
                turn = assistant.answer_approval(ask_yes_no(turn.approval))
        except KeyboardInterrupt:
            print("\n(cancelled)\n")
            continue
        except Exception as exc:
            # Provider outages, bad keys, rate limits. Keep the session alive.
            print(f"\nagent> Sorry, the model call failed: {type(exc).__name__}: {str(exc)[:300]}\n")
            continue

        print(f"\nagent> {turn.reply or '(no answer)'}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
