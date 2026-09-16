# Text-to-SQL with a Clarification Engine — Knowledge Base

Technical log for this project, built incrementally as each step is completed —
same pattern as the MLflow/DVC/DagsHub knowledge base docs. Records what was
built, what broke, and why, not just the final working state.

---

## Project shape

Natural-language question → ambiguity check → (optional clarification
round-trip) → SQL generation → validation → safe execution against Postgres
→ natural-language answer. Full conceptual design was discussed before any
code — see the "How it works" section below for the distilled architecture.
Build order:

1. Postgres in Docker + seed schema/data
2. Project venv + core packages
3. Schema introspection + embedding-based table retrieval
4. Ambiguity classifier (structured output)
5. SQL generator (structured output)
6. SQL validation layer (sqlglot, allowlist, EXPLAIN)
7. Safe execution against Postgres (read-only role, timeout, limits)
8. Result formatting (rows → natural language)
9. Wire into LangGraph (conditional branch + clarification interrupt/resume)
10. LangSmith tracing over the full pipeline

---

## How it works (distilled)

- **Two-stage LLM reasoning, not one prompt doing everything**: an
  ambiguity classifier decides if the question has one dominant
  interpretation or several materially different ones; only if ambiguous
  does a clarification round-trip happen before SQL generation.
- **Schema is retrieved, not dumped whole** — table/column descriptions are
  embedded, and only the top-K relevant tables for a given question get
  injected into the SQL-gen prompt (RAG over the schema, same mechanic as
  the PDF embedding-search lab).
- **Every LLM decision point is a Pydantic model**, forced via structured
  output — makes the pipeline programmatically branchable
  (`if result.is_ambiguous:`) instead of string-parsing chat replies.
- **Validation is layered and happens before any real execution**: SQL
  parsed with `sqlglot` (not string matching) to confirm it's a single
  `SELECT`, referenced tables/columns checked against an explicit
  allowlist, then `EXPLAIN`ed against Postgres to catch syntax errors and
  sanity-check cost before it ever runs for real.
- **The actual security boundary is the database role**, not the prompt —
  a dedicated Postgres role with `SELECT`-only grants, so even a bypassed
  validation layer can't mutate data.
- **Conversation state across the clarification pause is a structured
  state object**, not a chat-history list — this is what LangGraph's
  checkpointer + `interrupt()` is for for: pause the graph, persist state,
  resume later (possibly on a different process) once the user answers.

---

## Log

### Setup

- Project originally scaffolded under `ML_OPS/Project_1_txt_to_SQL/`. Moved
  into a proper GitHub-backed repo at
  `ML_OPS/Text-to-SQL-with-a-Clarification-Engine/` (remote:
  `github.com/MZEE153/Text-to-SQL-with-a-Clarification-Engine`) once the
  user created and cloned it — all project files (`docker-compose.yml`,
  `db/`, `.gitignore`, `.env.example`, this file) relocated there. All
  paths below assume this location going forward.
- Postgres runs in Docker (`postgres:16`, port `5433` to avoid clashing with
  any local install), seeded via `db/init/*.sql` — these only execute on
  **first** container init (empty volume), so any schema/seed change needs
  `docker compose down -v && docker compose up -d` to actually take effect.
- Scaled the seed data from ~200 rows to **3,824** (60 customers, 1,236
  orders, 2,378 order_items, 150 systems) by adding `03_scale_up.sql` —
  52 procedurally-generated customers plus bounded random background
  orders across 2023-2026, layered on top of the original 8 hand-crafted
  "signature" customers without touching their numbers. Bulk-customer
  bounds (≤9 orders/year, ≤$4,500/order, cost fraction ≤0.80) were chosen
  specifically to stay under the signature customers' 2025 thresholds.
- **Verified empirically** (not just designed) that "best customer 2025"
  genuinely has three different correct answers depending on definition:
  revenue → Acme Corp ($50,000.01), order count → Globex Inc (12), profit
  → Stark Industries ($27,999.96). This is the actual data-level ambiguity
  the Clarification Engine needs to detect later.
- Project venv created; `GROQ_API_KEY`/`GOOGLE_API_KEY` still empty in
  `.env` (never got filled in back in `LangChain_Labs` either — the HF
  detour took over there). Installed packages for all three providers
  (Groq/Gemini/HF) so nothing blocks on this yet, but a real chat-model
  key is needed before Step 4 (ambiguity classifier) can run — flagged
  since this project leans on **structured output**, which Groq/Gemini
  handle more reliably than HF's routed inference for function-calling.

### Step 3 — Schema introspection + retrieval

- `schema_introspection.py` uses SQLAlchemy's `inspect()` (not raw
  `information_schema` queries) to pull tables/columns/types/PKs/FKs —
  verified output matches `01_schema.sql` exactly, including correctly
  identifying `systems.signed_off_at` as nullable.
- `schema_retrieval.py` embeds each table's description with the same
  local `sentence-transformers/all-MiniLM-L6-v2` model from the
  `LangChain_Labs` PDF lab, and ranks tables by cosine similarity per
  question — genuinely RAG over the schema, not a document.
- With only 4 tables this is overkill in practice (could just inject all
  4 descriptions every time for near-zero token cost) — built anyway
  since the mechanism is what matters, and it'd work unchanged at real
  scale (100s of tables).
- **Useful validation, not just a pass**: for "who was the best customer
  last year?", `customers` and `orders` came back nearly tied
  (0.28 vs 0.28) — correctly reflecting that this question genuinely
  needs *both* tables joined. This is why `top_k=2` (not 1) matters: at
  `top_k=1` the SQL generator would silently lose a table it needs.

### Provider decision: Gemini (not Groq)

- Went with `GOOGLE_API_KEY` / `ChatGoogleGenerativeAI` instead of Groq.
- `gemini-2.5-flash` is **deprecated for new accounts** — 404s with a
  message pointing at the replacement. Correct current model ID:
  `gemini-3.6-flash` (confirmed working via `gemini_smoke_test.py`).
- **Gotcha to remember for the result-formatting step later**:
  `result.content` on this model comes back as a **list of content
  blocks** (`[{'type': 'text', 'text': '...', 'extras': {...}}]`), not a
  plain string. Doesn't affect the structured-output steps (those return
  a parsed Pydantic object, not raw `.content`), but anything reading
  `.content` directly needs to extract the `text`-type block, not assume
  a string.

### Step 4 — Ambiguity classifier

- `AmbiguityCheck` (Pydantic) + `model.with_structured_output(...)` on
  Gemini. Correctly classified the systems question as unambiguous and
  the "best customer" question as ambiguous on the first real test.
- **Caught a genuine RAG failure mode, not a hypothetical one**: first
  run used `schema_retrieval.py`'s `top_k=2`, which for "best customer"
  only returned `customers` + `orders` — never `order_items`. Since
  `order_items` is the only table with `cost_price`, the classifier
  literally could not propose a "highest profit" interpretation; it
  wasn't shown the data that would make profit computable. It offered
  "average order value" instead as a 3rd option, which isn't one of the
  three definitions actually engineered into the seed data.
- **Fix**: since the schema is only 4 tables, skip retrieval-filtering
  for this step entirely and hand the classifier all 4 table
  descriptions unconditionally — trivial token cost at this scale, and
  removes the recall gap. Re-tested: "highest total profit" now appears,
  with a definition (`sum of (unit_price - cost_price) * quantity`)
  matching exactly the formula used to verify Stark Industries' profit
  lead back in Step 1.
- **Lesson**: retrieval recall directly bounds what a downstream LLM can
  even consider — a missed table isn't just a missing JOIN option, it's
  missing *knowledge* the model has no way to know it's missing. At real
  scale (100s of tables) this exact gap would be much harder to notice
  than it was here with only 4 tables to reason about.
- **Refactored** the fix: instead of bypassing `retrieve_relevant_tables`
  entirely (two different schema-context mechanisms in the codebase),
  call it with `top_k=4` — with only 4 tables that returns everything
  anyway (identical outcome, re-verified), but keeps one consistent
  retrieval code path that Step 5 will also use, and one that starts
  filtering for real automatically once the schema grows past 4 tables,
  with no code change needed later.
- Also hit, unrelated to the code: Docker Desktop wasn't running after a
  machine restart, so Postgres connections timed out. `restart:
  unless-stopped` in `docker-compose.yml` only takes effect once the
  Docker daemon itself is running — it auto-recovered the container the
  moment Docker Desktop was started again, no `docker compose up`
  needed.
