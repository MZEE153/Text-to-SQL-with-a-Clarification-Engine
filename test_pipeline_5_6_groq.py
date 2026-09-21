"""Integration check: Groq-generated SQL through the same validator used
for the Gemini path (sql_validator.py doesn't care which provider produced
the SQL -- it's provider-agnostic by design)."""
from sql_generator_groq import generate_sql  # Step 5 (Groq): resolved question -> generated SQL
from sql_validator import validate_sql, SQLValidationError  # Step 6: the same validator used for the Gemini path, plus its exception type

test_questions = [  # the same 4 regression questions used to verify Step 5
    "How many systems signed off last year?",  # expected answer: 12
    "Who was the best customer last year, defined as highest revenue?",  # expected: Acme Corp
    "Who was the best customer last year, defined as highest total profit?",  # expected: Stark Industries
    "What is the total sales in all years? Assume completed orders only, summed across all time.",  # expected: 3,077,465.82
]

for q in test_questions:  # run the full generate -> validate chain for each question
    print(f"Resolved question: {q}")  # show the input question
    result = generate_sql(q)  # Step 5: ask Groq for SQL
    print(f"  generated sql:\n{result.sql}")  # show exactly what the model produced
    try:  # validate_sql raises on failure and returns None on success
        validate_sql(result.sql)  # Step 6: parse check + table allowlist + EXPLAIN
        print("  -> validator: PASSED")  # no exception means the SQL is safe and plannable
    except SQLValidationError as e:  # the validator rejected the generated SQL
        print(f"  -> validator: REJECTED ({e})")  # show why it was rejected
    print()  # blank line between questions
