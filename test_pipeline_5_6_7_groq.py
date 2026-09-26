"""End-to-end check of Steps 5 -> 6 -> 7 with Groq: resolved question in,
real rows out. generate_sql (LLM) -> validate_sql (software check, run inside
execute_sql) -> execute under the read-only role -> compare with ground truth.
"""
from decimal import Decimal  # exact decimal type that Postgres NUMERIC values arrive as

from sql_generator_groq import generate_sql  # Step 5 (Groq): resolved question -> generated SQL
from sql_validator import SQLValidationError  # Step 6's error type, so a rejection can be reported instead of crashing
from sql_executor import execute_sql, SQLExecutionError  # Step 7: validate + run under the read-only role, plus its error type

cases = [  # (resolved question, a value that must appear in the first result row)
    ("How many systems signed off last year?", 12),  # expected count
    ("Who was the best customer last year, defined as highest revenue?", "Acme Corp"),  # expected revenue winner
    ("Who was the best customer last year, defined as highest total profit?", "Stark Industries"),  # expected profit winner
    ("What is the total sales in all years? Assume completed orders only, summed across all time.", Decimal("3077465.82")),  # expected total
]

passed = 0  # how many questions came back with the expected answer
for question, expected in cases:  # run the full chain for each question
    print(f"Question: {question}")  # show the input
    generated = generate_sql(question)  # Step 5: ask Groq for SQL
    print(f"  sql: {' '.join(generated.sql.split())}")  # the generated SQL, collapsed onto one line
    try:  # validation or execution can still refuse
        result = execute_sql(generated.sql)  # Steps 6 + 7: validate, then execute as the read-only role
    except (SQLValidationError, SQLExecutionError) as e:  # refused before or during execution
        print(f"  -> REFUSED: {e}")  # report why
        continue  # move on to the next question
    ok = expected in result.rows[0]  # is the ground-truth value in the first row?
    passed += ok  # True counts as 1
    print(f"  -> columns={result.columns} first_row={result.rows[0]} ({result.elapsed_ms:.0f} ms)  [{'OK' if ok else '!! WRONG ANSWER !!'}]")  # actual rows returned
    print()  # blank line between questions

print(f"{passed}/{len(cases)} questions answered correctly end to end")  # overall score
