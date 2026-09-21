"""Integration check: does Step 6's validator actually accept what Step 5's
generator produces? sql_validator.py has only been tested against
hand-written SQL so far -- this closes that gap by chaining the two real
stages together on the same 4 resolved questions Step 5 already verified
against the live database.
"""
from sql_generator import generate_sql  # Step 5 (Gemini): resolved question -> generated SQL
from sql_validator import validate_sql, SQLValidationError  # Step 6: the safety check, plus its custom exception type

test_questions = [  # the same 4 regression questions used to verify Step 5
    "How many systems signed off last year?",  # expected answer: 12
    "Who was the best customer last year, defined as highest revenue?",  # expected: Acme Corp
    "Who was the best customer last year, defined as highest total profit?",  # expected: Stark Industries
    "What is the total sales in all years? Assume completed orders only, summed across all time.",  # expected: 3,077,465.82
]

for q in test_questions:  # run the full generate -> validate chain for each question
    print(f"Resolved question: {q}")  # show the input question
    result = generate_sql(q)  # Step 5: ask Gemini for SQL (uses one API request from the daily quota)
    print(f"  generated sql:\n{result.sql}")  # show exactly what the model produced
    try:  # validate_sql raises on failure and returns None on success
        validate_sql(result.sql)  # Step 6: parse check + table allowlist + EXPLAIN
        print("  -> validator: PASSED")  # no exception means the SQL is safe and plannable
    except SQLValidationError as e:  # the validator rejected the generated SQL
        print(f"  -> validator: REJECTED ({e})")  # show why it was rejected
    print()  # blank line between questions
