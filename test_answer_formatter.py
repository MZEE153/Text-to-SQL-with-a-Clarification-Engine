"""Tests for Step 8 (answer_formatter.py).
  A. deterministic parts -- number-grounding check, text assembly, empty result -- no model call
  B. live -- real query results (Step 7 executor) turned into answers by Groq, checked against known values
"""
from sql_executor import QueryResult, execute_sql  # Step 7: the result type, and the executor that produces real results
from answer_formatter import find_ungrounded_numbers, compose_text, format_answer  # Step 8 pieces under test

failures = 0  # running count of checks that did not go as expected


def check(label: str, ok: bool, detail: str = "") -> None:  # print one PASS/FAIL line and remember failures
    global failures  # we update the module-level counter
    if not ok:  # the expectation was not met
        failures += 1  # count it
    print(f"  [{'OK' if ok else '!! FAIL !!'}] {label}" + (f"  -- {detail}" if detail else ""))  # one line per check


print("A. Deterministic parts (no model)")  # section header
check("thousands separator matches the raw value", find_ungrounded_numbers("Acme Corp earned 50,000.01.", '{"rows": [["Acme Corp", "50000.01"]]}') == [])  # 50,000.01 and 50000.01 are the same number
check("fabricated figure is caught", find_ungrounded_numbers("Acme Corp earned 51,000.", '{"rows": [["Acme Corp", "50000.01"]]}') == ["51000"])  # 51000 appears nowhere in the data
check("trailing zeros are equivalent", find_ungrounded_numbers("Total was 50000.10.", '{"rows": [["50000.1"]]}') == [])  # 50000.10 == 50000.1 numerically
check("dates are grounded by the data", find_ungrounded_numbers("Signed off on 2025-01-05.", '{"rows": [["2025-01-05"]]}') == [])  # the year, month and day tokens all occur in the data
check("a year guessed by the model is caught", find_ungrounded_numbers("It happened in 2025.", '{"rows": [["12"]]}') == ["2025"])  # 2025 is not in the data, question or SQL
check("several ungrounded numbers sort numerically", find_ungrounded_numbers("10 and 2 and 300", "") == ["2", "10", "300"])  # numeric order, not text order ('10' < '2' as text)
check("no numbers -> nothing to flag", find_ungrounded_numbers("Acme Corp was the top customer.", "anything") == [])  # a sentence without digits is trivially grounded
swapped_data = '{"rows": [["Acme Corp", "50000.01"], ["Initech", "45000.00"]]}'  # two customers and their revenue
check("KNOWN LIMITATION: swapped values are NOT detected", find_ungrounded_numbers("Acme Corp earned 45000.00 and Initech earned 50000.01.", swapped_data) == [], "both numbers exist in the data, so the check passes even though the pairing is wrong")  # documents that the check proves a number EXISTS in the data, not that it belongs to the name next to it

full = compose_text("Answer.", "Assuming X.", True, ["51000"])  # every optional part switched on
check("all parts present", "Warning:" in full and "cut off at 100 rows" in full and "Assumption: Assuming X." in full, repr(full[:60]))  # the warning, the cut-off note and the assumption all appear
check("assumption comes last, answer first", full.startswith("Answer.") and full.endswith("Assumption: Assuming X."))  # fixed order: answer, then warnings, then the assumption
check("plain answer is left alone", compose_text("Answer.", None, False, []) == "Answer.")  # nothing to append -> unchanged

empty = format_answer("Show customer Nobody Inc.", "SELECT 1", QueryResult(columns=["name"], rows=[], truncated=False, elapsed_ms=1.0), "Assuming X.")  # an empty result plus an assumption
check("empty result skips the model", not empty.used_llm and "No matching records were found." in empty.text and "Assumption: Assuming X." in empty.text, repr(empty.text))  # fixed wording, assumption still appended, no API call

print("B. Live: real query results -> Groq -> answer")  # section header
REVENUE_SQL = "SELECT c.name, SUM(o.total_amount) AS revenue FROM customers c JOIN orders o ON o.customer_id = c.customer_id WHERE o.status = 'completed' AND o.order_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY c.name ORDER BY revenue DESC"  # revenue per customer in 2025, highest first (a LIMIT is added per case)
live_cases = [  # (label, question, SQL, assumed_default, substrings the final text must contain, or None to require every row's first column)
    ("single count", "How many systems signed off in 2025?", "SELECT COUNT(*) AS n FROM systems WHERE status = 'signed_off' AND signed_off_at >= '2025-01-01' AND signed_off_at < '2026-01-01'", None, ["12"]),  # expected count: 12
    ("revenue winner", "Who was the best customer in 2025 by revenue?", REVENUE_SQL + " LIMIT 1", None, ["Acme Corp", "50000.01"]),  # expected: Acme Corp
    ("profit winner", "Who was the best customer in 2025 by profit?", "SELECT c.name, SUM((oi.unit_price - oi.cost_price) * oi.quantity) AS profit FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN order_items oi ON oi.order_id = o.order_id WHERE o.status = 'completed' AND o.order_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY c.name ORDER BY profit DESC LIMIT 1", None, ["Stark Industries", "27999.96"]),  # expected: Stark Industries
    ("total with assumption", "What is the total sales in all years?", "SELECT SUM(total_amount) AS total_sales FROM orders WHERE status = 'completed'", "Assuming completed orders only, summed across all time.", ["3077465.82", "Assumption: Assuming completed orders only"]),  # the assumption line must be appended
    ("multi-row list", "Who were the top 5 customers by revenue in 2025?", REVENUE_SQL + " LIMIT 5", None, None),  # None = every one of the 5 names must be mentioned
    ("truncated result", "List every order paired with every order item.", "SELECT o.order_id, oi.order_item_id FROM orders o CROSS JOIN order_items oi", None, ["cut off at 100 rows"]),  # the cut-off note must be appended
]
for label, question, sql, assumed, expected in live_cases:  # run each case
    result = execute_sql(sql)  # Step 7: run the SQL for real (validated, read-only)
    final = format_answer(question, sql, result, assumed)  # Step 8: word the answer
    flat = final.text.replace(",", "")  # compare with thousands separators removed, so 50,000.01 matches 50000.01
    needed = expected if expected is not None else [str(row[0]) for row in result.rows]  # fixed expectations, or every row's first column (the names) for the list case
    missing = [s for s in needed if s.replace(",", "") not in flat]  # anything the final text failed to include
    check(label, not missing and final.used_llm and not final.ungrounded_numbers, f"missing={missing} ungrounded={final.ungrounded_numbers}")  # all expected content present, model was used, no unmatched numbers
    print("      " + final.text.replace("\n", "\n      "))  # show the actual answer, indented under its check line

print()  # blank line before the verdict
print("ALL CHECKS PASSED" if failures == 0 else f"{failures} CHECK(S) FAILED")  # overall verdict
