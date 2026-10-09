# Design notes

Notes on how the agent works and why, plus the estimate that was asked for.

## The services as given

Three FastAPI apps, each with its own SQLite file: customer (8001), product
(8002), order (8003). They're plain CRUD and don't talk to each other.

What mattered for the agent:

- The order service stores customer_id and product_id but never checks them,
  and it takes unit_price from whoever calls it. So you can order for a customer
  that doesn't exist, at any price, without stock. Cancelling doesn't return
  stock either. If those rules matter (they do), the caller has to enforce them.
- There's no search and no count endpoint. "How many orders did Jane place" =
  find Jane in the customer list, then list orders for her id. "How many
  customers" = page through skip/limit, since one page is 100 rows.
- Every service serves /openapi.json with typed bodies. That's what the whole
  design leans on.

## How it fits together

```
services.toml -> discovery (reads /openapi.json) -> Toolbox -> LangGraph agent -> CLI / Streamlit
                                                       |
                                     generated CRUD tools + workflows.py
```

The graph itself is small: `route -> agent <-> tools`. Code is in `agent/`.

## Tools are generated from OpenAPI

I didn't want a hand-written tool per endpoint, because that's exactly the part
that doesn't survive going from 3 services to 20. `discovery.py` reads each spec
and turns every operation into a tool. Path params, query params and body fields
get flattened into one argument schema, $refs are inlined, Optional[X] becomes X.
Small models handle flat schemas a lot better than nested Pydantic output.

New service = new block in services.toml. `/reload` in the CLI re-reads the specs
without restarting.

Two generic rules:

- PUT is skipped when the path also has PATCH. A full replace is an easy way for
  a model to wipe a field it didn't mention.
- DELETE always asks a human first. Per service you can add more operations to
  that list (`confirm = [...]`), or turn it on for all writes with
  AGENT_CONFIRM_WRITES=true.

Discovery also checks the spec's title against the configured name. My own .env
had product and order on swapped ports at one point and the agent happily called
the order API as "product". Now it refuses that URL with a message.

## Workflows

Generated CRUD isn't enough in two cases, so `workflows.py` has a handful of
hand-written tools:

- Writes across services. `place_order` looks up the customer and products (by
  id, email, SKU or name), checks stock, takes prices from the catalogue,
  decrements stock, creates the order, and puts the stock back if anything
  fails. `cancel_order` only cancels pending/confirmed orders and restocks. Since
  place_order exists, the raw create_order endpoint is hidden (it would let the
  model choose the price).
- Numbers. business_overview, sales_by_product, stock_report and
  customer_order_summary go through every page and do the counting in Python.
  The model just reads the result. A 3B model adding up 18 order totals is a coin
  flip.

Each workflow says which services it needs. If one of them is down the workflow
isn't offered, and in place_order's case create_order comes back.

## Routing

With 3 services there are 24 tools, which is fine for any model. With 20+
services it would be a couple of hundred, and models start picking the wrong one.
So above AGENT_ROUTER_THRESHOLD tools (30 by default, so it's off right now) a
route step asks the model which services the message is about and only those
tools get bound.

Small models get this wrong sometimes. "delete customer Chen Wei" straight after
talking about orders got routed to order only. So a service that's named in the
message is always included, and if the router errors out it falls back to all
tools.

Past ~50 services I'd swap the LLM router for embedding search over tool
descriptions, or one sub-agent per domain. Either would plug in at
`Toolbox.for_services`.

## Keeping it from making things up

Most of this came from running it against llama3.2:3b and fixing what broke:

- Prompt rule: every fact has to come from a tool result.
- Empty results come back as "No records found (0 results) ... do not make any
  up" rather than [], which small models liked to fill with invented rows.
- A full page gets a note saying there's probably more, so nobody reports
  "100 customers" off one page.
- Arguments are validated before any HTTP call (jsonschema for generated tools,
  Pydantic for workflows). "4", 4.0, "false" and nulls get coerced first.
  Errors go back to the model as text so it can retry.
- Passing a name where an id is expected gets a hint to look it up first.
- If a name matches more than one record, the tool returns the options and tells
  the model to ask.
- Approvals use LangGraph's interrupt(). All approvals for a step are collected
  before anything runs, because a resumed node re-runs from the top and would
  otherwise repeat the safe calls. There's a test that counts the HTTP calls.
- After a "no", the reply is fixed text, not the model's. llama3.2 once answered
  a rejected cancel with "Order 19 was cancelled."
- If the model prints a tool call as JSON instead of calling it, it gets one
  nudge. If it does it again and the name is close to a real tool, that call is
  run.
- Limits on steps, tool output size and history length. Read-only mode drops
  every write tool. Only GETs are retried (a retried POST can create a duplicate
  order).

## Logs

`logs/agent-trace.jsonl` gets a line per message, route, tool call (with timing),
approval and reply. Each turn has a request id that's also sent to the services
as X-Request-ID. LANGSMITH_TRACING=true works too if you want full traces.

## Models

LLM_PROVIDER can be ollama, gemini, openai or anthropic. I tested on Ollama with
llama3.2:3b. It gets the answers right but it's slow on a laptop CPU (roughly
5-60 s per answer), partly because its chat template puts the tool list in the
last user message, so Ollama can't reuse its prompt cache between questions. A
hosted model answers in a couple of seconds and is what I'd use for real.

## Tests

- `uv run pytest`: 48 tests, about 3 s, no services or model needed. A fake
  backend serves the real OpenAPI specs (saved in tests/fixtures) and a scripted
  chat model drives the graph.
- `evals/run_evals.py`: real model, live services, 9 questions. Answers are
  checked against numbers computed from the APIs. Two of them ask about things
  that don't exist, to catch invented answers. Last run on llama3.2:3b passed
  all 9 after the fixes above.
- `test_services.py`: the original smoke test.

## Not done yet

- Memory is in-process (InMemorySaver). For real use, swap in LangGraph's
  Postgres or Redis checkpointer.
- No auth. The agent should pass the user's token through rather than use its
  own account.
- Two sessions ordering the last unit at the same time can oversell. The real
  fix is an atomic "decrement if enough stock" endpoint in the product service.
- 9 eval questions is a start; it should grow from real questions.

## Estimate

Roughly 6 working days for one dev who knows Python/FastAPI and has used
LangChain:

| | days |
|---|---|
| going through the services, figuring out the gaps | 0.5 |
| OpenAPI discovery + HTTP client | 1 |
| workflows (order/cancel, reports, lookups) | 1 |
| LangGraph agent: routing, approvals, memory | 1 |
| CLI + Streamlit | 0.5 |
| tests, evals, tuning on a couple of models | 1.5 |
| docs, logging, cleanup | 0.5 |

Getting it production ready (auth, persistent memory, Docker/CI, more evals) is
another 1.5-2 weeks, mostly depending on the auth setup.
