"""Step 6: validate generated SQL before it ever touches real data.
Three layered checks, cheapest first:
  1. Parse with sqlglot -- confirm it's a single SELECT statement, and
     that no data-modifying statement is hiding anywhere in the tree
     (e.g. a data-modifying CTE like `WITH x AS (DELETE ...) SELECT ...`
     -- syntactically the outer statement IS a SELECT, so checking only
     the top-level statement type would miss this).
  2. Check every referenced table against an explicit allowlist -- not a
     blocklist. A blocklist can only reject names you thought to list;
     an allowlist rejects everything except what you explicitly permit.
  3. EXPLAIN against real Postgres -- catches syntax errors and bad
     column references, without ever executing the query for real.
"""
import os  # standard library: read environment variables (DB credentials from .env)
import sqlglot  # SQL parser: turns SQL text into a syntax tree (AST) so we inspect structure, not raw strings
from sqlglot import exp  # sqlglot's expression node classes (exp.Select, exp.Delete, exp.Table, ...) used for type checks
from dotenv import load_dotenv  # reads KEY=VALUE lines from .env into environment variables
from sqlalchemy import create_engine, text  # create_engine = DB connection factory; text() = wrap a raw SQL string for execution

load_dotenv()  # load .env now so os.environ[...] below can find the Postgres settings

ALLOWED_TABLES = {"customers", "orders", "order_items", "systems"}  # the ONLY tables a query may touch (allowlist, not blocklist)

DISALLOWED_NODE_TYPES = (  # syntax-tree node types that mean "this statement changes data or structure"
    exp.Delete,  # DELETE FROM ...
    exp.Insert,  # INSERT INTO ...
    exp.Update,  # UPDATE ... SET ...
    exp.Drop,  # DROP TABLE / DROP DATABASE ...
    exp.Alter,  # ALTER TABLE ...
    exp.Create,  # CREATE TABLE / CREATE ROLE ...
    exp.TruncateTable,  # TRUNCATE TABLE ...
)

DB_URL = (  # builds the SQLAlchemy connection string from .env values (so no credentials are hard-coded in code)
    f"postgresql+psycopg://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"  # dialect+driver (psycopg 3) and user:password
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"  # @host:port/database (localhost:5433/txt2sql)
)
engine = create_engine(DB_URL)  # connection factory (lazy: doesn't actually connect until first use)


class SQLValidationError(Exception):  # our own exception type so callers can catch "validation failed" specifically
    """Raised when generated SQL fails validation, with a human-readable reason."""


def validate_sql(sql: str) -> None:  # main entry point: returns nothing if SQL is safe, raises SQLValidationError if not
    """Raises SQLValidationError on any failed check. Returns None on success."""

    # --- Layer 1a: must parse as exactly one statement ---
    try:  # sqlglot raises ParseError on malformed SQL, so wrap the parse
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]  # parse using Postgres grammar; one tree per ;-separated statement; drop empty ones
    except sqlglot.errors.ParseError as e:  # SQL is syntactically invalid
        raise SQLValidationError(f"SQL failed to parse: {e}") from e  # convert to our error type, keeping the original as the cause

    if len(statements) != 1:  # blocks stacked statements like "SELECT ...; DROP TABLE ..."
        raise SQLValidationError(  # reject with a clear reason
            f"Expected exactly one statement, got {len(statements)}. "  # says how many statements were found
            "Multiple statements are not allowed."  # says why that's rejected
        )
    stmt = statements[0]  # the single parsed statement (the root of its syntax tree)

    # --- Layer 1b: top-level must be a SELECT ---
    if not isinstance(stmt, exp.Select):  # root node must be a SELECT, not DELETE/INSERT/DROP/etc.
        raise SQLValidationError(  # reject non-SELECT statements
            f"Only SELECT statements are allowed, got: {type(stmt).__name__}"  # names the offending type, e.g. "Delete"
        )

    # --- Layer 1c: no data-modifying statement anywhere in the tree,
    # even nested inside a CTE (data-modifying CTEs are valid Postgres:
    # `WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x` parses as
    # a top-level SELECT despite deleting data) ---
    hidden_mutations = list(stmt.find_all(*DISALLOWED_NODE_TYPES))  # search the WHOLE tree (not just the root) for any forbidden node type
    if hidden_mutations:  # found a DELETE/INSERT/etc. hiding somewhere inside the SELECT
        kinds = {type(n).__name__ for n in hidden_mutations}  # unique names of the forbidden node types found, e.g. {'Delete'}
        raise SQLValidationError(  # reject the query
            f"Query contains a disallowed statement type nested inside it: {kinds} "  # says which forbidden type was found
            "(e.g. a data-modifying CTE)."  # gives the typical example of how this happens
        )

    # --- Layer 2: every referenced table must be on the allowlist ---
    referenced_tables = {t.name for t in stmt.find_all(exp.Table)}  # collect every table name the query mentions (FROM, JOIN, subqueries, CTEs)
    disallowed = referenced_tables - ALLOWED_TABLES  # set difference: tables used that are NOT on the allowlist
    if disallowed:  # at least one unapproved table (e.g. pg_shadow, the password-hash catalog)
        raise SQLValidationError(  # reject the query
            f"Query references table(s) not on the allowlist: {disallowed}. "  # lists the offending tables
            f"Allowed: {sorted(ALLOWED_TABLES)}"  # lists what IS allowed, sorted for stable output
        )
    if not referenced_tables:  # query touches no table at all (e.g. "SELECT 1")
        raise SQLValidationError("Query does not reference any table.")  # not a real data question, so reject

    # --- Layer 3: EXPLAIN against real Postgres. Plans the query without
    # executing it -- catches syntax errors and bad column references. ---
    with engine.connect() as conn:  # open a DB connection; the with-block closes it automatically
        try:  # Postgres raises an error if planning fails
            conn.execute(text(f"EXPLAIN {sql}"))  # EXPLAIN = "show the plan, don't run it"; fails on unknown columns or bad syntax
        except Exception as e:  # any planning error (e.g. UndefinedColumn)
            raise SQLValidationError(f"EXPLAIN failed: {e}") from e  # convert to our error type, keeping the original as the cause


if __name__ == "__main__":  # only runs when executed directly (python sql_validator.py), not when imported
    test_cases = [  # (sql, should_it_pass) pairs covering every layer
        ("SELECT COUNT(*) FROM systems WHERE status = 'signed_off'", True),  # clean, valid query -> must PASS
        ("DELETE FROM customers", False),  # not a SELECT -> rejected by layer 1b
        ("SELECT * FROM customers; DROP TABLE customers;", False),  # two statements -> rejected by layer 1a
        ("WITH deleted AS (DELETE FROM customers RETURNING *) SELECT * FROM deleted", False),  # SELECT wrapping a hidden DELETE -> rejected by layer 1c
        ("SELECT * FROM pg_shadow", False),  # valid SELECT but forbidden table -> rejected by layer 2 (allowlist)
        ("SELEC * FROM customers", False),  # typo in SELECT -> rejected by the parser (layer 1a)
        ("SELECT nonexistent_column FROM customers", False),  # column doesn't exist -> rejected by EXPLAIN (layer 3)
    ]

    for sql, should_pass in test_cases:  # run every test case
        print(f"SQL: {sql}")  # show which SQL is being tested
        try:  # validate_sql raises on failure, returns None on success
            validate_sql(sql)  # run all three layers
            outcome = "PASSED"  # no exception means it passed
        except SQLValidationError as e:  # validation rejected it
            outcome = f"REJECTED ({e})"  # record the rejection reason
        expectation = "expected PASS" if should_pass else "expected REJECT"  # what we hoped would happen
        match = "OK" if (outcome.startswith("PASSED") == should_pass) else "!! MISMATCH !!"  # OK when actual result equals expectation
        print(f"  -> {outcome}  [{expectation}]  {match}")  # one-line verdict per test
        print()  # blank line between tests
