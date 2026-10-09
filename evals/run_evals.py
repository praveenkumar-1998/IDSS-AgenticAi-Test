"""Ask the real model a fixed set of questions and check the answers against
numbers computed directly from the services.

The unit tests prove the plumbing; this checks the model actually uses it.
Run it after changing the prompt, the model or a tool description.

    uv run python evals/run_evals.py
    uv run python evals/run_evals.py --provider ollama --model qwen3:4b
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.runtime import Assistant  # noqa: E402
from agent.workflows import business_overview, customer_order_summary, sales_by_product, stock_report  # noqa: E402


def money(value: float) -> list[str]:
    # accept 7736.98, 7,736.98
    return [f"{value:.2f}", f"{value:,.2f}"]


def build_cases(assistant: Assistant) -> list[dict]:
    ctx = assistant.toolbox.ctx
    overview = business_overview(ctx)
    sales = sales_by_product(ctx, top=3)
    # the customer with the most orders, so the count is worth checking
    orders = ctx.order.get_all("/api/v1/orders/")
    busiest = max(set(o["customer_id"] for o in orders), key=[o["customer_id"] for o in orders].count)
    summary = customer_order_summary(ctx, busiest)
    name = summary["customer"]["name"]
    pending = overview["orders"]["by_status"].get("pending", 0)
    low = [p["name"] for p in stock_report(ctx)["low_stock"]]

    return [
        {"q": "How many customers are there?", "any": [str(overview["customers"]["total"])]},
        {"q": "How many orders are pending right now?", "any": [str(pending)]},
        {"q": "What is our total revenue, not counting cancelled orders?",
         "any": money(overview["orders"]["revenue_excluding_cancelled"])},
        {"q": "Which product sells the most units?", "any": [sales["ranking"][0]["product_name"]]},
        {"q": f"How many orders has {name} placed?", "any": [str(summary["order_count"])]},
        # this one used to be answered with units sold instead of stock
        {"q": "Which products are low on stock?", "all": low, "any": low[:1] or ["none"]},
        {"q": "Is anything out of stock?",
         "any": overview["products"]["out_of_stock"] or ["no", "none"]},
        {"q": "What is the status of order 999999?", "any": ["not found", "doesn't exist", "does not exist",
                                                             "no order", "couldn't find", "could not find"]},
        # the model must not invent data the services don't have
        {"q": "What is the phone number of customer Zaphod Beeblebrox?",
         "any": ["no customer", "not found", "couldn't find", "could not find", "no match", "doesn't exist",
                 "does not exist", "no record"]},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--only", help="run only questions containing this text")
    opts = parser.parse_args()
    if opts.provider:
        os.environ["LLM_PROVIDER"] = opts.provider
    if opts.model:
        os.environ["LLM_MODEL"] = opts.model

    assistant = Assistant()
    if not all(s.healthy for s in assistant.services):
        print("All services must be running for the evals.")
        return 2

    cases = [c for c in build_cases(assistant) if not opts.only or opts.only.lower() in c["q"].lower()]
    passed = 0
    for case in cases:
        assistant.reset()
        started = time.perf_counter()
        turn = assistant.ask(case["q"])
        while turn.approval:  # nothing here should need one; refuse if it asks
            turn = assistant.answer_approval(False)
        took = time.perf_counter() - started
        reply = turn.reply.lower()
        ok = (any(e.lower() in reply for e in case["any"])
              and all(e.lower() in reply for e in case.get("all", [])))
        passed += ok
        tools = ", ".join(c["name"] for c in turn.tool_calls) or "no tools"
        print(f"{'PASS' if ok else 'FAIL'}  {took:5.1f}s  {case['q']}")
        print(f"      tools: {tools}")
        if not ok:
            print(f"      expected one of {case['any']}")
            print(f"      got: {turn.reply[:300]}")

    print(f"\n{passed}/{len(cases)} passed with {assistant.model_label}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
