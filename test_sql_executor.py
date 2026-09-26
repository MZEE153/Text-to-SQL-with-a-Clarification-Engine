"""Tests for Step 7 (sql_executor.py + the read-only role). No LLM is involved,
so this uses no API quota. Four sections:
  A. correct answers come back through the executor (known ground truth)
  B. the row cap and the statement timeout actually trigger
  C. the Step 6 validator still rejects bad SQL before it reaches the database
  D. the read-only ROLE stops writes even when the validator is bypassed entirely
"""
import time  # standard library: measure how long the timeout tests really take
from decimal import Decimal  # exact decimal type that Postgres NUMERIC values arrive as

from sqlalchemy import text  # wrap raw SQL strings for execution
from sqlalchemy.exc import DBAPIError  # SQLAlchemy's wrapper around driver-level database errors

from sql_executor import execute_sql, ro_engine, SQLExecutionError, MAX_ROWS  # the Step 7 executor, the read-only engine, its error type and the row cap
from sql_validator import engine as admin_engine, SQLValidationError  # the ADMIN engine (only used to count rows before/after) and the Step 6 error type

failures = 0  # running count of checks that did not go as expected


def check(label: str, ok: bool, detail: str = "") -> None:  # print one PASS/FAIL line and remember failures
    global failures  # we update the module-level counter
    if not ok:  # the expectation was not met
        failures += 1  # count it
    print(f"  [{'OK' if ok else '!! FAIL !!'}] {label}" + (f"  -- {detail}" if detail else ""))  # one line per check


print("A. Ground-truth answers through the executor")  # section header
ground_truth = [  # (label, SQL, a value that must appear in the first result row)
    ("systems signed off in 2025", "SELECT COUNT(*) FROM systems WHERE status = 'signed_off' AND signed_off_at >= '2025-01-01' AND signed_off_at < '2026-01-01'", 12),  # expected count: 12
    ("best customer 2025 by revenue", "SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.customer_id WHERE o.status = 'completed' AND o.order_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY c.name ORDER BY revenue DESC LIMIT 1", "Acme Corp"),  # expected winner: Acme Corp
    ("best customer 2025 by profit", "SELECT c.name, SUM((oi.unit_price - oi.cost_price) * oi.quantity) AS profit FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' AND o.order_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY c.name ORDER BY profit DESC LIMIT 1", "Stark Industries"),  # expected winner: Stark Industries
    ("total completed sales", "SELECT SUM(total_amount) FROM orders WHERE status = 'completed'", Decimal("3077465.82")),  # expected total from earlier verification
]
for label, sql, expected in ground_truth:  # run each known query
    r = execute_sql(sql)  # validate + execute under the read-only role
    check(label, expected in r.rows[0], f"row={r.rows[0]}, {r.elapsed_ms:.0f} ms")  # the expected value must be in the first row

r = execute_sql("SELECT name, created_at::text, '12:30:00' AS t FROM customers WHERE name LIKE 'A%' LIMIT 3")  # edge syntax: a :: cast, a time literal with colons, and a LIKE with a % sign
check("edge syntax (::cast, '12:30:00', LIKE 'A%') executes", len(r.rows) > 0, f"{len(r.rows)} row(s)")  # SQLAlchemy text() must not mistake any of these for bind parameters

print("B. Row cap and timeout")  # section header
r = execute_sql("SELECT customer_id FROM customers")  # 60 rows: fewer than the cap
check("under the cap: all rows, not truncated", len(r.rows) == 60 and not r.truncated, f"{len(r.rows)} rows")  # everything returned

r = execute_sql("SELECT o.order_id, oi.order_item_id FROM orders o CROSS JOIN order_items oi")  # a cross join: ~3 million rows
check("cross join is capped", len(r.rows) == MAX_ROWS and r.truncated, f"{len(r.rows)} rows returned, truncated={r.truncated}, {r.elapsed_ms:.0f} ms")  # only MAX_ROWS come back, flagged as truncated
check("cap is fast (streamed, not fully loaded)", r.elapsed_ms < 3000, f"{r.elapsed_ms:.0f} ms")  # a server-side cursor means we never materialise 3M rows

for label, sql in [  # two queries that would run far longer than the 5 s limit
    ("pg_sleep(10)", "SELECT pg_sleep(10) FROM customers LIMIT 1"),  # deterministic slow query; NOTE the validator does not restrict function calls
    ("runaway 3-way cross-join count", "SELECT COUNT(*) FROM orders a CROSS JOIN orders b CROSS JOIN orders c"),  # ~2 billion row combinations: a realistic accidental runaway
]:
    started = time.perf_counter()  # start the clock
    try:  # we expect a SQLExecutionError
        execute_sql(sql)  # run it
        check(f"timeout: {label}", False, "finished without being cancelled")  # it should never complete
    except SQLExecutionError as e:  # the server cancelled it
        took = (time.perf_counter() - started) * 1000  # real elapsed time
        check(f"timeout: {label}", "time limit" in str(e) and 4500 < took < 9000, f"cancelled after {took:.0f} ms")  # cancelled near the 5000 ms limit

print("C. Validator still rejects before the database is reached")  # section header
for label, sql in [  # SQL the Step 6 validator must refuse
    ("DELETE", "DELETE FROM customers"),  # not a SELECT
    ("pg_shadow (password hashes)", "SELECT * FROM pg_shadow"),  # table not on the allowlist
    ("data-modifying CTE", "WITH d AS (DELETE FROM customers RETURNING *) SELECT * FROM d"),  # DELETE hidden inside a SELECT
]:
    try:  # we expect a SQLValidationError
        execute_sql(sql)  # run it
        check(f"validator rejects: {label}", False, "was executed!")  # it must never reach the database
    except SQLValidationError:  # rejected by Step 6
        check(f"validator rejects: {label}", True)  # as designed

print("D. The read-only ROLE, with the validator bypassed entirely")  # section header
COUNT_SQL = "SELECT (SELECT count(*) FROM customers), (SELECT count(*) FROM orders), (SELECT count(*) FROM order_items), (SELECT count(*) FROM systems)"  # row counts of all 4 tables in one query
with admin_engine.connect() as admin:  # connect as admin, only to count rows
    before = tuple(admin.execute(text(COUNT_SQL)).one())  # counts before any attack

ATTACKS = [  # statements that would damage data if they ever succeeded
    "DELETE FROM customers",  # delete rows
    "INSERT INTO customers (name, email, created_at) VALUES ('evil', 'evil@x.example', '2025-01-01')",  # add a row
    "UPDATE orders SET total_amount = 0",  # rewrite data
    "DROP TABLE systems",  # destroy a table
    "TRUNCATE order_items",  # wipe a table
    "CREATE TABLE evil (id int)",  # create an object
    "WITH d AS (DELETE FROM customers RETURNING *) SELECT * FROM d",  # the data-modifying-CTE trick
]


def attempt(statement: str, force_read_write: bool) -> str:  # run one statement as the read-only role and report what happened
    with ro_engine.connect() as conn:  # connect AS THE READ-ONLY ROLE, straight to the engine (no validator involved)
        try:  # we expect the database to refuse
            if force_read_write:  # layer-isolation mode: switch off the read-only-transaction layer for this transaction
                conn.execute(text("SET TRANSACTION READ WRITE"))  # allowed for any role; now ONLY the missing privileges stand in the way
            conn.execute(text(statement))  # try the attack
            return "SUCCEEDED (!!)"  # the database let it through
        except DBAPIError as e:  # the database refused
            return type(e.orig).__name__  # which kind of refusal, e.g. InsufficientPrivilege
        finally:  # always, even on success
            conn.rollback()  # never commit: if a statement ever did succeed, this undoes it


for statement in ATTACKS:  # each attack, in two modes
    ro_mode = attempt(statement, force_read_write=False)  # default: read-only transaction + no privileges
    priv_mode = attempt(statement, force_read_write=True)  # read-only transaction switched OFF: only the missing privileges remain
    check(f"{statement[:52]!r}", ro_mode != "SUCCEEDED (!!)" and priv_mode == "InsufficientPrivilege", f"read-only txn -> {ro_mode}; privileges alone -> {priv_mode}")  # both layers must block it, and privileges alone must be enough

for label, statement in [  # reads that must also be refused by privileges
    ("pg_shadow (password hashes)", "SELECT * FROM pg_shadow"),  # superuser-only system view
    ("pg_read_file (read server files)", "SELECT pg_read_file('/etc/passwd')"),  # superuser-only function
]:
    outcome = attempt(statement, force_read_write=False)  # try it
    check(f"role cannot: {label}", outcome == "InsufficientPrivilege", outcome)  # must be a privilege refusal

with admin_engine.connect() as admin:  # connect as admin again, only to count rows
    after = tuple(admin.execute(text(COUNT_SQL)).one())  # counts after all the attacks
check("row counts unchanged after every attack", before == after, f"before={before} after={after}")  # proof that nothing was modified

print()  # blank line before the verdict
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")  # overall verdict
