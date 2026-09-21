-- Seed data designed so "best customer in 2025" has THREE different,
-- genuinely correct answers depending on definition:
--   highest revenue      -> Acme Corp      (~$50,000)
--   highest order count  -> Globex Inc     (12 orders)
--   highest profit       -> Stark Industries (~$28,000, thin revenue but high margin)
-- This is what makes the ambiguity real to query against, not theoretical.

INSERT INTO customers (name, email, created_at) VALUES  -- the 8 hand-crafted "signature" customers (one multi-row INSERT)
    ('Acme Corp',         'billing@acme.example',      '2023-01-15'),  -- will win on REVENUE in 2025
    ('Globex Inc',        'ap@globex.example',         '2023-02-20'),  -- will win on ORDER COUNT in 2025
    ('Initech',           'finance@initech.example',   '2023-03-05'),  -- 2nd-highest 2025 revenue ($45,000) from only 2 orders
    ('Umbrella Corp',     'orders@umbrella.example',   '2023-04-10'),  -- 2nd-highest 2025 order count (9), modest revenue
    ('Wayne Enterprises', 'procurement@wayne.example', '2023-05-01'),  -- 5 orders / $20,000 in 2025; never leads a metric
    ('Stark Industries',  'billing@stark.example',     '2023-06-18'),  -- will win on PROFIT in 2025 (high-margin products)
    ('Wonka Industries',  'orders@wonka.example',      '2023-07-22'),  -- one small order in 2025 ($10,000)
    ('Hooli LLC',         'ap@hooli.example',          '2023-08-30');  -- 7 orders / $28,000 in 2025; never leads a metric

-- Cost fraction (cost_price / unit_price) per customer's typical product mix.
-- Stark's products are high-margin (0.20 cost fraction = 80% margin), which
-- is what lets it win on profit despite modest revenue and order count.
--   Acme 0.70 | Globex 0.75 | Initech 0.65 | Umbrella 0.70
--   Wayne 0.60 | Stark 0.20 | Wonka 0.50 | Hooli 0.68

-- 2025 orders: the primary "last year" test data. order_count and
-- total_revenue below are the ground truth for validating any generated SQL.
DO $$  -- anonymous procedural (PL/pgSQL) block: lets us use loops and variables inside plain SQL
DECLARE  -- variable declarations for this block
    cust RECORD;  -- holds one row of the driving table below (name, order_count, total_revenue, cost_fraction)
    i INTEGER;  -- loop counter: which order of this customer we are on
    per_order NUMERIC;  -- amount of each order for the current customer
    new_order_id INTEGER;  -- id of the order just inserted, needed to attach its line item
    cost_fraction NUMERIC;  -- declared but unused; the loop reads cust.cost_fraction instead
BEGIN  -- start of the executable part
    FOR cust IN  -- outer loop: once per signature customer
        SELECT * FROM (VALUES  -- an inline table of the per-customer 2025 targets
            ('Acme Corp',          3,  50000.00, 0.70),  -- 3 orders totalling $50,000 -> highest revenue
            ('Globex Inc',        12,  30000.00, 0.75),  -- 12 orders totalling $30,000 -> highest order count
            ('Initech',            2,  45000.00, 0.65),  -- 2 orders totalling $45,000
            ('Umbrella Corp',      9,  25000.00, 0.70),  -- 9 orders totalling $25,000
            ('Wayne Enterprises',  5,  20000.00, 0.60),  -- 5 orders totalling $20,000
            ('Stark Industries',   6,  35000.00, 0.20),  -- 6 orders totalling $35,000 at 20% cost -> highest profit
            ('Wonka Industries',   1,  10000.00, 0.50),  -- 1 order of $10,000
            ('Hooli LLC',          7,  28000.00, 0.68)  -- 7 orders totalling $28,000
        ) AS t(name, order_count, total_revenue, cost_fraction)  -- gives the VALUES table its column names
    LOOP  -- body runs once per customer row above
        per_order := ROUND(cust.total_revenue / cust.order_count, 2);  -- split the target revenue evenly across the orders, rounded to cents
        FOR i IN 1..cust.order_count LOOP  -- inner loop: create each of this customer's orders
            INSERT INTO orders (customer_id, order_date, status, total_amount)  -- insert one order row ...
            SELECT customer_id,  -- ... looking up the customer's id by name
                   DATE '2025-01-01' + ((i * 29) % 330) * INTERVAL '1 day',  -- spread order dates across 2025 (deterministic, not random)
                   'completed',  -- all of these count as real sales
                   per_order  -- the amount computed above
            FROM customers WHERE name = cust.name  -- match the current customer
            RETURNING order_id INTO new_order_id;  -- capture the new order's id into the variable

            INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)  -- one line item per order
            VALUES (new_order_id, 'Standard Package', 1, per_order,  -- the whole order amount as a single unit
                    ROUND(per_order * cust.cost_fraction, 2));  -- cost = amount x this customer's cost fraction, so profit = amount - cost
        END LOOP;  -- end inner loop (orders)
    END LOOP;  -- end outer loop (customers)
END $$;  -- end of block; this closes the dollar-quoted body opened at DO

-- A couple of CANCELLED 2025 orders — these must be excluded from revenue.
-- A naive query that forgets `WHERE status = 'completed'` will overcount.
DO $$  -- second procedural block
DECLARE  -- variable declarations
    new_order_id INTEGER;  -- id of the order just inserted
BEGIN  -- start of the executable part
    INSERT INTO orders (customer_id, order_date, status, total_amount)  -- a cancelled $8,000 order for Acme ...
    SELECT customer_id, DATE '2025-03-14', 'cancelled', 8000.00  -- fixed date, status 'cancelled'
    FROM customers WHERE name = 'Acme Corp'  -- look up Acme's id
    RETURNING order_id INTO new_order_id;  -- capture the new order's id
    INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)  -- its line item
    VALUES (new_order_id, 'Standard Package', 1, 8000.00, 5600.00);  -- cost 5,600 = 70% of 8,000

    INSERT INTO orders (customer_id, order_date, status, total_amount)  -- a cancelled $4,000 order for Globex ...
    SELECT customer_id, DATE '2025-09-02', 'cancelled', 4000.00  -- fixed date, status 'cancelled'
    FROM customers WHERE name = 'Globex Inc'  -- look up Globex's id
    RETURNING order_id INTO new_order_id;  -- capture the new order's id
    INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)  -- its line item
    VALUES (new_order_id, 'Standard Package', 1, 4000.00, 3000.00);  -- cost 3,000 = 75% of 4,000
END $$;  -- end of block

-- Noise orders in adjacent years (2024, 2026) so date filtering is actually
-- tested, not trivially "all the data there is". Amounts are arbitrary.
DO $$  -- third procedural block
DECLARE  -- variable declarations
    cust RECORD;  -- one customer row (id and name)
    yr INTEGER;  -- the noise year being generated (2024 or 2026)
    i INTEGER;  -- loop counter (1 or 2): which noise order this is
    new_order_id INTEGER;  -- id of the order just inserted
BEGIN  -- start of the executable part
    FOR cust IN SELECT customer_id, name FROM customers LOOP  -- every existing customer (the 8 signature ones at this point)
        FOREACH yr IN ARRAY ARRAY[2024, 2026] LOOP  -- once for each noise year
            FOR i IN 1..2 LOOP  -- two noise orders per customer per year
                INSERT INTO orders (customer_id, order_date, status, total_amount)  -- insert one noise order ...
                VALUES (cust.customer_id,  -- for this customer
                        MAKE_DATE(yr, 1 + ((i * 5) % 11), 1 + ((i * 7) % 27)),  -- build a valid date from year, month and day formulas (deterministic)
                        'completed', 3000.00 + (i * 500))  -- completed, $3,500 or $4,000
                RETURNING order_id INTO new_order_id;  -- capture the new order's id
                INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)  -- its line item
                VALUES (new_order_id, 'Standard Package', 1, 3000.00 + (i * 500),  -- same amount as the order
                        ROUND((3000.00 + (i * 500)) * 0.65, 2));  -- cost fixed at 65% of the amount
            END LOOP;  -- end noise-order loop
        END LOOP;  -- end year loop
    END LOOP;  -- end customer loop
END $$;  -- end of block

-- systems table: ground truth for "how many systems signed off last year (2025)?" -> 12
DO $$  -- fourth procedural block
DECLARE  -- variable declarations
    i INTEGER;  -- loop counter, also used to build unique names and spread dates
BEGIN  -- start of the executable part
    FOR i IN 1..12 LOOP  -- signed off in 2025 (the answer to the example question)
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'signed_off',  -- name like 'System-001' (LPAD pads with zeros to 3 digits), status signed_off
                DATE '2025-01-01' + ((i * 23) % 330) * INTERVAL '1 day',  -- sign-off date spread across 2025
                DATE '2024-11-01' + (i * 3) * INTERVAL '1 day');  -- created shortly before sign-off
    END LOOP;  -- exactly 12 rows: the ground-truth answer

    FOR i IN 13..17 LOOP  -- signed off in 2024 (noise year)
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'signed_off',  -- zero-padded name, status signed_off
                DATE '2024-01-01' + ((i * 23) % 330) * INTERVAL '1 day',  -- sign-off date in 2024
                DATE '2023-11-01' + (i * 3) * INTERVAL '1 day');  -- created in late 2023
    END LOOP;  -- 5 rows that must NOT count towards 2025

    FOR i IN 18..21 LOOP  -- signed off in 2026 (noise year)
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'signed_off',  -- zero-padded name, status signed_off
                DATE '2026-01-01' + ((i * 23) % 200) * INTERVAL '1 day',  -- sign-off date in 2026 (mod 200 keeps it inside the year)
                DATE '2025-11-01' + (i * 3) * INTERVAL '1 day');  -- created in late 2025
    END LOOP;  -- 4 rows that must NOT count towards 2025

    FOR i IN 22..27 LOOP  -- still pending, never signed off
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'pending', NULL,  -- status pending, no sign-off date
                DATE '2025-06-01' + (i * 3) * INTERVAL '1 day');  -- created mid-2025
    END LOOP;  -- 6 pending rows

    FOR i IN 28..32 LOOP  -- in progress
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'in_progress', NULL,  -- status in_progress, no sign-off date
                DATE '2025-08-01' + (i * 3) * INTERVAL '1 day');  -- created in 2025
    END LOOP;  -- 5 in-progress rows

    FOR i IN 33..35 LOOP  -- cancelled before sign-off
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'cancelled', NULL,  -- status cancelled, no sign-off date
                DATE '2025-02-01' + (i * 3) * INTERVAL '1 day');  -- created early 2025
    END LOOP;  -- 3 cancelled rows
END $$;  -- end of block; 35 systems total from this file
