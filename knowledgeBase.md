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

### Step 4 — Calibration fix: binary ambiguous flag was over-triggering

- Manual testing surfaced a real UX problem: "what is the total sales in
  all years?" was flagged `is_ambiguous: True` even though it has an
  obvious default (completed orders, grand total) most people would
  expect without being asked. The binary `is_ambiguous` model had no way
  to express "technically underspecified, but proceed with a stated
  default" as distinct from "genuinely no default exists, must ask."
- **Fix**: added a third outcome via `assumed_default: str | None` on
  `AmbiguityCheck`, and rewrote the system prompt to teach the actual
  distinction — ask only when no single interpretation is what most
  people would assume by default; otherwise proceed and state the
  assumption for transparency. Re-tested all 5 questions.
- **"Total sales" now correctly proceeds without asking** — the primary
  fix worked. But its stated assumption is itself debatable: it says
  "regardless of status," meaning **cancelled orders are included** in
  "total sales." Most people would want completed-only. Because the
  assumption is now surfaced explicitly instead of computed silently,
  this bad judgment call is visible and catchable rather than a silent
  wrong answer — that's the actual point of `assumed_default`, not just
  UX politeness.
- **Second-order effect, not obviously good**: "what is the name of the
  computer?" flipped from `is_ambiguous: True` (offering "all systems"
  vs. "most recent system") to `is_ambiguous: False` with a silent
  default of "return all system names." Arguably worse for this specific
  case — the question is grammatically singular ("**the** computer"),
  and picking silently between "list everything" vs. "pick one" feels
  like it deserved a question. Reinforces the still-open gap: there's no
  way for the model to say "this doesn't map to my schema at all" — it's
  forced into either "ambiguous" or "confident default," and neither
  fits a genuinely out-of-scope question well. Left as a known
  limitation, not fixed — revisit if it causes real problems once SQL
  generation exists downstream.

### Step 5 — SQL generator

- `SQLGenerationResult` (Pydantic: `sql`, `tables_used`, `explanation`) +
  structured output on Gemini, given a fully-resolved question (Step 4
  already settled) plus the same `retrieve_relevant_tables` schema
  context Step 4 uses.
- Tested with 4 hand-written "resolved" questions standing in for what
  Step 9 (LangGraph) will eventually build automatically by merging the
  original question with a clarification answer or `assumed_default`.
- **Verified by actually executing the generated SQL against the real
  database**, not just reading it: all 4 results matched previously
  verified ground truth exactly — 12 signed-off systems, Acme Corp
  $50,000.01 revenue, Stark Industries $27,999.96 profit. The profit
  query correctly pulled in `order_items` on its own (confirming the
  Step 4 RAG-recall fix carries through), and the "total sales" query
  correctly respected the `status = 'completed'` instruction we fed it —
  directly validating the fix path for the cancelled-orders concern
  raised in Step 4's calibration work.
- Not yet validated: this stage will happily generate SQL for *any*
  resolved question handed to it, correct or not — that's exactly what
  Step 6 (validation layer) exists to check before anything runs for
  real. No safety net yet at this point in the pipeline.

### Step 6 — SQL validation layer

- `sql_validator.py`: `sqlglot.parse(sql, read="postgres")` checks for
  exactly one statement whose top-level type is `exp.Select`; every
  referenced table (`stmt.find_all(exp.Table)`) checked against an
  explicit allowlist (not a blocklist — rejects everything not
  permitted, rather than only things thought to list); then `EXPLAIN`
  against live Postgres to catch syntax errors and bad column references
  without executing anything for real.
- **A blocklist-shaped gap caught during design, not by accident**: a
  top-level-statement-type check alone would miss a data-modifying CTE —
  `WITH deleted AS (DELETE FROM customers RETURNING *) SELECT * FROM
  deleted` is syntactically a top-level `SELECT` in Postgres despite
  deleting data. Fixed by walking the *entire* parsed tree
  (`stmt.find_all(exp.Delete, exp.Insert, exp.Update, exp.Drop,
  exp.Alter, exp.Create, exp.TruncateTable)`), not just checking the
  root node's type. Verified: this exact query is correctly rejected.
- **7/7 test cases landed as expected**, including a real illustration of
  why the allowlist matters, not just structural validity: `SELECT *
  FROM pg_shadow` parses as a perfectly valid `SELECT` — `pg_shadow` is
  Postgres's actual password-hash system catalog. Only the allowlist
  check stops it; nothing about "is this a SELECT" would.
- **Integration-tested against real generator output**, not just
  hand-written SQL (`test_pipeline_5_6.py`, chaining `sql_generator.py`
  → `sql_validator.py`) — this is what surfaced the Gemini quota issue
  below, before it could complete.

### Step 6 detour — Gemini free-tier quota, and why the HF fallback didn't work out

- Mid integration-test, Gemini returned `429 RESOURCE_EXHAUSTED` —
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, quota value `20`
  requests/day for `gemini-3.6-flash`. Cumulative calls across this
  whole session (smoke test + every classifier/generator run) used it
  up. Confirms this is a real, low daily ceiling worth planning around,
  not just a burst throttle — the `retryDelay: 52s` in the error is
  misleading; a retry a minute later still failed with the same error.
- **Tried Hugging Face as a fallback provider, and hit two separate,
  genuine dead ends worth remembering** (not a config mistake on our
  part — actual current platform behavior):
  1. `ChatHuggingFace.with_structured_output(SQLGenerationResult)`
     raises `NotImplementedError: Pydantic schema is not supported for
     function calling`. HF's routed inference has no tool-calling
     support to hang structured output off of — unlike Gemini/Groq, this
     isn't a "sometimes unreliable" gap, it's unimplemented. Worked
     around with `PydanticOutputParser` (inject `get_format_instructions()`
     into the prompt, parse the raw text response) — the correct fallback
     specifically because it needs nothing from the provider except
     "can follow instructions and return text."
  2. Even after that fix, `Qwen/Qwen2.5-7B-Instruct` (the model
     confirmed working in `LangChain_Labs/Hf.py` earlier this session)
     failed with `model_not_supported`: *"not supported by any provider
     you have enabled."* Probed HF's actual current catalog via
     `huggingface_hub.list_models(...)` directly rather than guessing
     model names one at a time (already burned two guesses this
     session: TinyLlama earlier, Qwen2.5-7B-Instruct now) — found that
     the free default provider (`hf-inference`) currently serves only
     embedding/small-NLP models (`sentence-transformers/all-MiniLM-L6-v2`,
     BERT, T5-small, rerankers — exactly what this project already uses
     it for in `schema_retrieval.py`), **not** any chat/instruct
     generation models. Chat-capable small models exist
     (`Qwen/Qwen2.5-0.5B-Instruct`, `meta-llama/Llama-3.2-1B-Instruct`,
     etc.) but only via third-party providers (Together, Fireworks,
     Novita...) that must be explicitly enabled per-account under HF
     Settings → Inference Providers — not something a code fix can route
     around.
  3. `list_models()`'s actual signature didn't match assumed kwargs
     either (`task=` doesn't exist, should be `pipeline_tag=`;
     `direction=` doesn't exist at all) — had to introspect
     `inspect.signature(HfApi.list_models)` directly rather than guess
     a second and third time. Small confirmation of the broader lesson:
     check the installed version's real signature before assuming API
     shape, especially for a fast-moving library.
- **Decision**: didn't pursue enabling a third-party HF provider or
  switching to Groq — chose to just wait out Gemini's daily quota reset
  instead, since the validator itself (the actual Step 6 deliverable)
  was already fully verified against hand-written SQL and didn't
  actually need this integration test to be "done." `sql_generator_hf.py`
  and `hf_model_probe.py` are left in the repo as working reference for
  this detour, not wired into the main pipeline.
- **Lesson for later steps (7-10)**: don't assume a provider that worked
  once stays working — availability (models, rate limits, enabled
  providers) is live external state, not a fixed fact to remember. Worth
  deciding *before* Step 9 (LangGraph orchestration) whether the
  pipeline should support a provider-fallback pattern, given how many
  times free-tier limits have already interrupted work this session.
- **Resolved**: came back later and reran the integration test — the
  daily quota had reset (confirmed empirically: multiple calls succeeded
  where the same calls 429'd before). Hit two more *transient* `503
  UNAVAILABLE` ("high demand... temporary") errors along the way,
  distinct from the 429 — those cleared on a plain retry with no code
  change, confirming they were genuinely transient rather than another
  hidden limit. **Final result: 4/4 resolved questions passed
  `generate_sql()` → `validate_sql()` end to end**, all matching
  previously-verified ground truth. Step 6 is now fully done, not just
  unit-tested in isolation: 12 signed-off systems, Acme Corp $50,000.01
  revenue, Stark Industries $27,999.96 profit, $3,077,465.82 total
  completed sales — all generated fresh and all passed validation
  without modification.

### Process incident — Hugging Face token exposed in chat, twice, via `.env.example`

- While adding `HUGGINGFACEHUB_API_TOKEN` for the Step 6 HF detour
  above, the token was added to `.env.example` instead of `.env` —
  twice, by two different tokens in a row. `.env.example` is not
  gitignored (only `.env` is), and because I had already `Read()` that
  file earlier in the session, the file-watcher surfaced every
  subsequent edit to it as a diff automatically — meaning both tokens
  became visible in this conversation the moment they were saved to
  disk, with no explicit paste needed.
- Neither token reached git (`git status` confirmed `.env.example` was
  only modified in the working tree, never staged or committed), but
  both were treated as compromised anyway per this project's standing
  rule: exposure in chat is enough to require revocation, regardless of
  whether it reached a remote.
- **Fix that actually broke the loop**: stopped asking the user to "add
  it to `.env`" as a bare instruction (too easy to confuse with the
  similarly-named, already-open `.env.example`) and instead gave the
  full absolute path to the correct file, plus an explicit instruction
  not to paste the value or print the file's contents in any terminal
  command. Also stopped reading `.env.example` further this session so
  edits to it no longer auto-surface.
- **Lesson**: a `.env` / `.env.example` pair is a real, recurring
  footgun for exactly this kind of mistake, independent of which model
  or provider is involved — worth naming the two files as differently
  as possible in future projects (e.g. `.env.template`) rather than
  assuming the naming convention is self-evident under time pressure.

### Provider detour, part 2 — trying local (Ollama), landing on Groq

- Considered `bash <(curl setup_adc.sh)` (Google's Application Default
  Credentials setup) as a way around Gemini's quota — correctly identified
  as the wrong tool before running it: ADC authenticates against Vertex
  AI, a different product/package (`langchain-google-vertexai`, not
  `langchain-google-genai`) with its own (generally billed, not the same
  always-free AI Studio tier) quota system. Not pursued.
- **Tried Ollama locally instead.** Machine specs checked first (13th
  Gen i7-13620H, 10c/16t, 15.7GB RAM, Intel UHD integrated graphics only
  — no dedicated GPU, 281GB free disk): good enough to *run* a quantized
  7-8B model, but CPU-only inference on a 7B model proved too slow for
  iterative testing — a 4-question test run didn't finish in 3+ minutes
  and was abandoned mid-run. Installed via `winget install Ollama.Ollama`
  (silent, worked cleanly) and `ollama pull qwen2.5:7b-instruct` (~4.7GB,
  completed fine) — the infra works, the speed just doesn't fit a
  tight iterate-and-verify workflow. `sql_generator_ollama.py` is left
  in the repo, untested end-to-end, as a reference if a faster machine
  or GPU is available later.
- **Landed on Groq**, which is what actually unblocked this. Two real
  gotchas on the way, not just "add the key and go":
  1. **Docker Desktop wasn't running again** — same failure mode as the
     Step 4 finding, recurring because the machine went through another
     restart/sleep cycle (`docker ps -a` failed outright, not just the
     container). Started Docker Desktop, `txt2sql_postgres` came back
     healthy automatically via `restart: unless-stopped` — no
     `docker compose up` needed, consistent with the earlier finding.
  2. **`llama-3.1-8b-instant` (the obvious/commonly-referenced Groq
     model ID) returned `404 model_not_found`** — Groq's available
     model lineup had shifted; querying `Groq().models.list()` directly
     showed the Llama family isn't in this account's current catalog at
     all anymore, replaced by OpenAI's open-weight GPT-OSS models, Qwen,
     and a few specialty models (Whisper, TTS, prompt-guard). Checking
     the live catalog beat guessing a second model name blind — same
     lesson as the HF model-availability issue earlier, different
     provider. Switched to `openai/gpt-oss-20b`.
- **`sql_generator_groq.py` worked cleanly on the first real attempt**
  after that: native `with_structured_output` (no `PydanticOutputParser`
  fallback needed, unlike HF), fast responses, and **4/4 generated
  queries matched previously-verified ground truth exactly** when run
  directly against Postgres (12 signed-off systems, Acme Corp $50,000.01
  revenue, Stark Industries $27,999.96 profit, $3,077,465.82 total
  completed sales) — then **4/4 also passed `sql_validator.py`**
  unmodified (`test_pipeline_5_6_groq.py`), confirming the validator is
  genuinely provider-agnostic: it never cared which model produced the
  SQL, only whether the SQL itself is safe.
- **Overall lesson for this whole detour (Gemini quota → HF dead ends →
  Ollama too slow → Groq)**: every free-tier LLM provider in this
  project has now failed at least once for a reason that had nothing to
  do with our code — a quota, a missing account-level provider
  enablement, a renamed/removed model, a slow local machine. None of
  these were bugs to fix once; they're standing operational risk. This
  makes the earlier open question from Step 6 concrete rather than
  hypothetical: a provider-fallback pattern (try Groq, fall back to
  Gemini, or vice versa) is worth designing into Step 9's LangGraph
  orchestration, not bolted on later.

### Steps 5–6 code walkthrough (line-by-line `#` annotations added)

Every code line in the working Step 5/6 files now carries a `#` comment
saying what it does and what it uses. Files annotated:
`sql_generator.py`, `sql_generator_groq.py`, `sql_validator.py`,
`test_pipeline_5_6.py`, `test_pipeline_5_6_groq.py`. Left un-annotated on
purpose: `sql_generator_hf.py`, `sql_generator_ollama.py`,
`hf_model_probe.py` — dead-end/diagnostic references, already explained in
their own docstrings and in the detour sections above.

- **Verified the annotation changed nothing**: snapshotted the originals
  first, then compared Python syntax trees (`ast.dump`) of old vs new for
  all 5 files — identical. Comments don't appear in a syntax tree, but
  every string does, so this also proves the LLM prompt and Pydantic
  field descriptions weren't accidentally altered. Re-ran
  `sql_validator.py` live afterward: 7/7 verdicts OK, 0 mismatches.
- **Gotcha that shaped how the prompt is annotated**: a `#` comment
  *inside* the triple-quoted `SYSTEM_PROMPT` string would not be a
  comment — it would be literal text sent to the model, silently
  changing its instructions. The same applies to the `description=`
  strings in the Pydantic `Field(...)` calls (those are sent to the LLM
  as per-field instructions). So the prompt's line-by-line explanation
  lives in a comment block *above* the string, and Field descriptions
  are annotated with trailing comments *outside* the quotes.

**What each file uses, and why**

| File | Library / call | Used for |
|---|---|---|
| `sql_generator.py` | `python-dotenv` `load_dotenv()` | Load `GOOGLE_API_KEY` from `.env` |
| | `langchain_google_genai.ChatGoogleGenerativeAI` | Gemini client (`gemini-3.6-flash`) |
| | `pydantic.BaseModel` / `Field` | Define `SQLGenerationResult` (`sql`, `tables_used`, `explanation`); `Field(description=...)` doubles as per-field LLM instructions |
| | `model.with_structured_output(...)` | Force the reply into that typed shape (tool-calling under the hood) |
| | `schema_retrieval.retrieve_relevant_tables(top_k=4)` | Embedding search (Step 3b) picks which table descriptions go into the prompt |
| | `SYSTEM_PROMPT.format(schema=...)` | Fill the `{schema}` placeholder |
| | `generator.invoke([("system", ...), ("human", ...)])` | The actual LLM call |
| `sql_generator_groq.py` | `langchain_groq.ChatGroq` (`openai/gpt-oss-20b`, `temperature=0.1`) | Same job, different provider; reuses `SYSTEM_PROMPT` and `SQLGenerationResult` from `sql_generator.py` so only the model differs |
| `sql_validator.py` | `sqlglot.parse(sql, read="postgres")` | Parse SQL into a syntax tree (structure, not string matching); one tree per `;`-separated statement |
| | `sqlglot.expressions` (`exp.Select`, `exp.Delete`, `exp.Table`, ...) | Type-check the root node; `stmt.find_all(...)` walks the whole tree to catch nested DML and list every referenced table |
| | `ALLOWED_TABLES` (set) + set subtraction | Allowlist check: `referenced_tables - ALLOWED_TABLES` |
| | `sqlalchemy.create_engine` + `text()` | Connect to Postgres and run `EXPLAIN <sql>` (plans without executing) |
| | `os.environ[...]` + `python-dotenv` | Build the DB URL from `.env` so no credentials are hard-coded |
| | `SQLValidationError` (custom `Exception`) | Let callers catch "validation failed" specifically, with a human-readable reason |
| `test_pipeline_5_6*.py` | `generate_sql` → `validate_sql` chain | Integration test: does the validator accept what a real generator produces? (`_groq` variant swaps only the import) |

**Reading order for the validator** (cheapest check first, so most bad
SQL is rejected without touching the database): parse → single statement
→ root is `SELECT` → no nested DML → table allowlist → `EXPLAIN`. Only
the last layer needs a live DB connection.

### Annotation pass extended to Steps 1–4 (and the convention going forward)

The same per-line commenting is now applied to every earlier file, and is
the standing convention for all future steps (7–10 included):

- **Step 1 (infra)**: `docker-compose.yml`, `.gitignore`, `.env.example`,
  `db/init/01_schema.sql`, `02_seed.sql`, `03_scale_up.sql`
- **Step 3**: `schema_introspection.py`, `schema_retrieval.py`
- **Provider smoke test**: `gemini_smoke_test.py`
- **Step 4**: `ambiguity_classifier.py`
- Not annotated: `sql_generator_hf.py`, `sql_generator_ollama.py`,
  `hf_model_probe.py` (dead-end references), and the separate
  `LangChain_Labs` / MLflow / DVC / DagsHub folders (different tracks).

**Comment syntax is not the same everywhere — this bit us once.**

| Format | Comment syntax | Gotcha |
|---|---|---|
| Python | `# ...` (trailing is fine) | Never inside a string sent to a model (prompts, `Field(description=...)`): it would become literal prompt text |
| YAML (`docker-compose.yml`) | `# ...` with a space before it | None found; `docker compose config` output unchanged |
| SQL (`db/init/*.sql`) | `-- ...` | `#` is a real operator in Postgres (bitwise XOR), so it can't be used as a comment |
| `.env` / `.env.example` | `# ...` on its **own line** | Kept on separate lines so a value can never absorb the comment |
| `.gitignore` | `# ...` on its **own line only** | A trailing `# ...` becomes part of the pattern |

- **Mistake made and caught**: the first pass wrote trailing comments in
  `.gitignore` (`.env  # the REAL secrets file`). Git only treats `#` as a
  comment at the start of a line, so that line became the literal pattern
  `.env  # the REAL secrets file...` and stopped ignoring `.env` — which
  would have let the real secrets file be committed. Noticed by
  re-reading the file against gitignore syntax rules right after writing
  it (nothing had been committed; I did not run `git check-ignore` on
  the broken version). Fixed by moving comments onto their own lines,
  then verified the *fix* with `git check-ignore -v`: `.env`, `venv/`,
  `__pycache__/` and `*.pyc` are ignored again while `.env.example`
  stays tracked.
- **How each format was proven unchanged** (no behavior change from
  annotation): Python via identical `ast.dump` trees (also proves prompt
  and description strings untouched); compose via identical
  `docker compose config` output; `.env.example` via identical parsed
  values from `dotenv_values`; SQL by stripping `--` comments and
  comparing the remaining code (identical, and line counts unchanged at
  43/152/116). SQL was additionally loaded into a throwaway scratch
  database with `ON_ERROR_STOP` to confirm the server accepts comments
  inside the `DO $$ ... $$` PL/pgSQL bodies — it did, and the seeded 2025
  ground truth still came out right (12 systems, Acme $50,000.01, Globex
  12 orders, Stark $27,999.96). Order/item counts there (1,258 / 2,424)
  differ slightly from the live database (1,236 / 2,378) because
  `03_scale_up.sql` uses `random()`; the live database was never touched.
- **Factual accuracy over decoration**: a first draft of the seed-data
  comments called Initech a "decoy" and Umbrella "mid-range" without
  checking. Corrected against the real numbers (revenue order: Acme 50k,
  Initech 45k, Stark 35k, Globex 30k, Hooli 28k, Umbrella 25k, Wayne 20k,
  Wonka 10k; order-count order: Globex 12, Umbrella 9, Hooli 7, Stark 6,
  Wayne 5, Acme 3, Initech 2, Wonka 1).
- **Small pre-existing issues surfaced by reading every line, noted but
  deliberately not changed** (annotation must not alter behavior):
  `retrieve_relevant_tables()` is typed `-> list[str]` but really returns
  a list of `(name, description, score)` tuples; and `02_seed.sql`'s first
  `DO` block declares a `cost_fraction` variable that is never used (the
  loop reads `cust.cost_fraction` from the record instead). Both are
  flagged in comments where they occur.
- `.env.example` also gained a header warning that it is committed to git
  and must never hold real values — a direct response to the earlier
  token-exposure incidents — plus an entry for `HUGGINGFACEHUB_API_TOKEN`
  and a note on which file uses each key.

**What the Step 1–4 files use, and why**

| File | Uses | For |
|---|---|---|
| `docker-compose.yml` | `postgres:16` image, `restart: unless-stopped`, port `5433:5432`, named volume `pgdata`, `./db/init` mount, `pg_isready` healthcheck | A reproducible local Postgres; init SQL runs only on first start of an empty volume |
| `01_schema.sql` | `SERIAL PRIMARY KEY`, `REFERENCES` (foreign keys), `CHECK`, `UNIQUE`, `NUMERIC(12,2)`, indexes | 4 tables; `NUMERIC` keeps money exact; `CHECK` limits `status` values |
| `02_seed.sql` | PL/pgSQL `DO $$` blocks, `FOR` loops, inline `VALUES` table, `RETURNING ... INTO`, `MAKE_DATE`, `LPAD` | Deterministic ground-truth data: 3 different "best customer" winners, 12 systems signed off in 2025 |
| `03_scale_up.sql` | `random()`, arrays, `CASE`, `FOREACH` | ~1,000+ rows of bounded random background data that can't disturb the ground truth |
| `schema_introspection.py` | `sqlalchemy.inspect`, `create_engine`, `os.environ` + `python-dotenv` | Read the live schema and render each table as plain English |
| `schema_retrieval.py` | `HuggingFaceEmbeddings` (`all-MiniLM-L6-v2`, local), `sklearn` `cosine_similarity` | RAG over the schema: rank tables by similarity to the question |
| `gemini_smoke_test.py` | `ChatGoogleGenerativeAI`, `model.invoke` | Minimal proof the Gemini key and model name work |
| `ambiguity_classifier.py` | `pydantic` models, `with_structured_output`, `retrieve_relevant_tables(top_k=4)` | 3-way classification: clear / default-with-stated-assumption / ask the user |
