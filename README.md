# Microservices – UV Workspace

A **monolithic folder structure, microservices-ready** Python backend built with:

| Tool | Purpose |
|------|---------|
| [uv](https://docs.astral.sh/uv/) | Fast package manager & workspace orchestrator |
| [FastAPI](https://fastapi.tiangolo.com/) | High-performance REST framework |
| [SQLAlchemy 2](https://docs.sqlalchemy.org/) | ORM with typed models |
| [SQLite (in-memory)](https://www.sqlite.org/) | Per-service isolated DB with shared-cache URI |
| [Pydantic v2](https://docs.pydantic.dev/) | Request validation & response schemas |

---

## 🤖 AI Agent Developer Candidate Assessment

If you are completing the **AI Agent Developer Assessment**:
- 💻 Implement your CLI Chatbot agent in: [agent_cli.py](agent_cli.py)
- 🎯 Your goal: Build an interactive terminal assistant that orchestrates multi-step actions across Customer, Product, and Order microservices using LLM tool calling.

---

## Project Layout

Each service follows a **scalable layered architecture** with API versioning (`v1`), pure route handlers, dedicated schemas, ORM models, and CRUD encapsulation:

```
IDSSTest/
├── pyproject.toml                  ← uv workspace root
├── agent_cli.py                    ← terminal chatbot
├── streamlit_app.py                ← browser chatbot
├── services.toml                   ← service registry for the agent
├── agent/                          ← discovery, tools, workflows, LangGraph flow
├── tests/                          ← offline agent tests (pytest)
├── evals/run_evals.py              ← answer-accuracy checks against live services
├── scripts/seed_demo_data.py       ← demo customers / products / orders
├── docs/DESIGN.md                  ← architecture, decisions, time estimate
├── test_services.py                ← 27-check end-to-end smoke test
├── .vscode/                        ← Run & Debug configurations
│   ├── launch.json                 ← F5 debug for individual or all services
│   ├── settings.json               ← Virtualenv & Python path resolution
│   └── tasks.json                  ← One-click tasks for sync & servers
├── shared/                         ← Installable shared library
│   ├── pyproject.toml
│   └── shared/
│       ├── database.py             ← get_engine / init_db / get_db
│       ├── crud_base.py            ← Generic CRUDBase[Model, Create, Update]
│       └── schemas_base.py         ← Pydantic BaseSchema (ORM mode on)
└── services/
    ├── customer_service/           ← Port 8001
    │   ├── pyproject.toml
    │   └── app/
    │       ├── core/config.py      ← Service settings & env vars
    │       ├── models/customer.py  ← ORM table: customers
    │       ├── schemas/customer.py ← Pydantic Create/Update/Response
    │       ├── crud/customer.py    ← customer_crud singleton
    │       ├── api/v1/
    │       │   ├── router.py       ← v1 aggregator
    │       │   └── endpoints/customers.py
    │       └── main.py             ← create_app() factory
    ├── product_service/            ← Port 8002
    │   ├── pyproject.toml
    │   └── app/
    │       ├── core/config.py
    │       ├── models/product.py   ← ORM table: products
    │       ├── schemas/product.py
    │       ├── crud/product.py     ← product_crud singleton
    │       ├── api/v1/
    │       │   ├── router.py
    │       │   └── endpoints/products.py
    │       └── main.py
    └── order_service/              ← Port 8003
        ├── pyproject.toml
        └── app/
            ├── core/config.py
            ├── models/order.py     ← ORM tables: orders, order_items
            ├── schemas/order.py
            ├── crud/order.py       ← OrderCRUD (atomic multi-item order creation)
            ├── api/v1/
            │   ├── router.py
            │   └── endpoints/orders.py
            └── main.py
```

---

## Quick Start

### 1. Install & Sync Workspace

From the project root:

```powershell
uv sync --all-packages
```

Or from inside any individual service folder:

```powershell
cd services\customer_service
uv sync
```

Both work seamlessly because the root `pyproject.toml` defines the workspace members.

---

### 2. Run & Debug in VS Code

Open the project in VS Code / Antigravity IDE:
1. Press `Ctrl+Shift+D` (or open the **Run and Debug** panel).
2. Select any configuration from the dropdown:
   - **`Debug: Customer Service (8001)`**
   - **`Debug: Product Service (8002)`**
   - **`Debug: Order Service (8003)`**
   - **`Debug: All 3 Services`** (launches all 3 simultaneously with compound debugging)
   - **`Debug: E2E Test Suite`**
3. Press `F5` to start debugging with full breakpoint support!

Alternatively, run tasks via **Terminal -> Run Task...**:
- `uv: Sync Workspace`
- `Start: Customer Service (8001)`
- `Start: Product Service (8002)`
- `Start: Order Service (8003)`
- `Test: Run E2E Test Suite`

---

### 3. Run Manually via CLI

```powershell
# Terminal 1 – Customer Service  :8001
uv run uvicorn app.main:app --port 8001 --app-dir services/customer_service --reload

# Terminal 2 – Product Service   :8002
uv run uvicorn app.main:app --port 8002 --app-dir services/product_service --reload

# Terminal 3 – Order Service     :8003
uv run uvicorn app.main:app --port 8003 --app-dir services/order_service --reload
```

---

### 4. Interactive Swagger UI Docs

- **Customer Service**: http://localhost:8001/docs
- **Product Service**: http://localhost:8002/docs
- **Order Service**: http://localhost:8003/docs

---

### 5. Run the Automated Test Suite

```powershell
uv run python test_services.py
```

All 27 assertion checks across health, CRUD operations, relationships, cascade deletes, and 404 guards will run and report status.

---

### 6. Run the AI Agent Chatbot

The agent is in `agent/` (LangChain + LangGraph). How it works and the time
estimate are in [docs/DESIGN.md](docs/DESIGN.md).

1. Start the three services as in section 3 (customer 8001, product 8002, order 8003).
2. Copy `.env.example` to `.env` and pick a model. For a free local one install
   Ollama and run `ollama pull llama3.2:3b`. Otherwise set `LLM_PROVIDER` to
   `gemini`, `openai` or `anthropic` and add the key.
3. Some demo data, if the databases are empty: `uv run python scripts/seed_demo_data.py`
4. Then:

```powershell
uv run python agent_cli.py              # terminal, add -v to see the tool calls
uv run streamlit run streamlit_app.py   # browser on http://localhost:8501
```

Some things to ask:

- how many customers and orders do we have?
- what are the top 3 selling products?
- which products are low on stock?
- summarise Jane Doe's orders
- order 2 USB-C hubs for Ravi Kumar, then: cancel that order (it asks y/N first)

In the terminal, `/tools` lists what the agent can call, `/reload` picks up
changed services, `/reset` clears the chat and `/exit` quits.

Adding a service: add it to [services.toml](services.toml). Its tools are built
from its `/openapi.json`, no agent code needed. Anything that spans services
(ordering, cancelling, reports) is in [agent/workflows.py](agent/workflows.py).

Tests:

```powershell
uv run pytest                     # offline, ~3 s
uv run python evals/run_evals.py  # asks the real model, checks answers against the APIs
uv run python test_services.py    # original service smoke test
```

Logs go to `logs/` (`agent-trace.jsonl` has every tool call with timings).
