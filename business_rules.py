"""Domain rules that BOTH the ambiguity classifier and the SQL generator must
follow. They live in one place so the two stages cannot drift apart.

Why this exists: the schema text only says what the columns ARE, not what
counts as a sale. In the first live end-to-end run, the classifier wrote
interpretations like "sum of total_amount from all orders placed in 2025", and
the SQL generator obeyed that literal wording over its own "sales means
completed" default -- so Acme Corp's revenue came out as 58000.01 instead of
50000.01 and Globex Inc's order count as 13 instead of 12 (cancelled orders
were counted).
"""

BUSINESS_RULES = """Business rules for this database:
- An order with status 'cancelled' is not a sale. Revenue, sales, order counts, profit and any "best customer" ranking count ONLY orders with status = 'completed'.
- This holds even if a question or a stated interpretation says "all orders". Cancelled orders are included only if the question explicitly asks for them.
- When you write an interpretation definition, it must say that only completed orders are counted.

"""  # the trailing blank line separates this block from the prompt that follows it
