"""Step 7b: run validated SQL against Postgres safely.

Defence in depth -- each layer assumes the one before it failed:
  1. sql_validator.validate_sql   -- software check (structure, allowlist, EXPLAIN)
  2. read-only ROLE               -- the database itself refuses any write (the real boundary)
  3. read-only transaction        -- every transaction starts read-only
  4. statement timeout            -- runaway queries are cancelled by the server
  5. row cap                      -- at most MAX_ROWS rows are ever pulled into Python
"""
import os  # standard library: read environment variables (read-only role credentials)
import time  # standard library: measure how long a query took
from dataclasses import dataclass  # a lightweight typed container for the query result

import psycopg  # imported for its error classes (QueryCanceled = the timeout error)
from dotenv import load_dotenv  # reads KEY=VALUE lines from .env into environment variables
from sqlalchemy import create_engine, text  # create_engine = DB connection factory; text() = wrap a raw SQL string for execution
from sqlalchemy.exc import DBAPIError  # SQLAlchemy's wrapper around driver-level database errors

from sql_validator import validate_sql  # Step 6: the software safety check that always runs first

load_dotenv()  # load .env so the POSTGRES_RO_* variables exist

MAX_ROWS = 100  # never return more than this many rows, however large the query's real result is
TIMEOUT_MS = 5000  # cancel any query that runs longer than 5 seconds

RO_URL = (  # connection string for the READ-ONLY role (not the admin user the validator uses for EXPLAIN)
    f"postgresql+psycopg://{os.environ['POSTGRES_RO_USER']}:{os.environ['POSTGRES_RO_PASSWORD']}"  # dialect+driver, then read-only user:password
    f"@{os.environ['POSTGRES_HOST']}:{os.environ['POSTGRES_PORT']}/{os.environ['POSTGRES_DB']}"  # @host:port/database
)
ro_engine = create_engine(  # connection factory for the read-only role
    RO_URL,  # where and as whom to connect
    connect_args={"options": f"-c statement_timeout={TIMEOUT_MS} -c default_transaction_read_only=on"},  # per-connection Postgres settings sent at login: timeout + read-only transactions
)


class SQLExecutionError(Exception):  # raised when a validated query still fails at run time (timeout, permission denied, ...)
    """Raised when a validated query fails or is stopped while running."""


@dataclass  # auto-generates __init__ and __repr__ for a plain data holder
class QueryResult:  # what a successful execution returns
    columns: list[str]  # column names, in order
    rows: list[tuple]  # up to MAX_ROWS rows, each a tuple of Python values (Decimal, date, str, int, ...)
    truncated: bool  # True if the query had MORE than MAX_ROWS rows and the rest were dropped
    elapsed_ms: float  # wall-clock time the query took


def execute_sql(sql: str) -> QueryResult:  # main entry point: validate, then run under the read-only role
    validate_sql(sql)  # Step 6 first: raises SQLValidationError for anything unsafe, so it never reaches the database
    started = time.perf_counter()  # high-resolution start time
    try:  # database errors (timeout, permissions) surface as DBAPIError
        with ro_engine.connect() as conn:  # connect AS THE READ-ONLY ROLE; the with-block closes (and rolls back) automatically
            result = conn.execute(text(sql), execution_options={"stream_results": True})  # stream_results = server-side cursor, so rows are pulled in batches instead of the whole result being loaded into memory
            columns = list(result.keys())  # column names of the result
            fetched = result.fetchmany(MAX_ROWS + 1)  # pull at most MAX_ROWS + 1 rows; the extra one only tells us whether we truncated
    except DBAPIError as e:  # the database rejected or stopped the query
        if isinstance(e.orig, psycopg.errors.QueryCanceled):  # the server cancelled it because it hit statement_timeout
            raise SQLExecutionError(f"Query exceeded the {TIMEOUT_MS} ms time limit and was cancelled.") from e  # clear message for the caller
        raise SQLExecutionError(f"Database rejected the query: {type(e.orig).__name__}: {e.orig}") from e  # any other database-level refusal, e.g. permission denied
    elapsed_ms = (time.perf_counter() - started) * 1000  # elapsed time in milliseconds
    return QueryResult(  # package everything up
        columns=columns,  # column names
        rows=[tuple(r) for r in fetched[:MAX_ROWS]],  # keep at most MAX_ROWS rows, converted from SQLAlchemy Row objects to plain tuples
        truncated=len(fetched) > MAX_ROWS,  # more than MAX_ROWS came back, so some rows were dropped
        elapsed_ms=elapsed_ms,  # how long it took
    )
