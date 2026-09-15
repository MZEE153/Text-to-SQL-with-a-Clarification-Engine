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
