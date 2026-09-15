-- Seed data designed so "best customer in 2025" has THREE different,
-- genuinely correct answers depending on definition:
--   highest revenue      -> Acme Corp      (~$50,000)
--   highest order count  -> Globex Inc     (12 orders)
--   highest profit       -> Stark Industries (~$28,000, thin revenue but high margin)
-- This is what makes the ambiguity real to query against, not theoretical.

INSERT INTO customers (name, email, created_at) VALUES
    ('Acme Corp',         'billing@acme.example',      '2023-01-15'),
    ('Globex Inc',        'ap@globex.example',         '2023-02-20'),
    ('Initech',           'finance@initech.example',   '2023-03-05'),
    ('Umbrella Corp',     'orders@umbrella.example',   '2023-04-10'),
    ('Wayne Enterprises', 'procurement@wayne.example', '2023-05-01'),
    ('Stark Industries',  'billing@stark.example',     '2023-06-18'),
    ('Wonka Industries',  'orders@wonka.example',      '2023-07-22'),
    ('Hooli LLC',         'ap@hooli.example',          '2023-08-30');

-- Cost fraction (cost_price / unit_price) per customer's typical product mix.
-- Stark's products are high-margin (0.20 cost fraction = 80% margin), which
-- is what lets it win on profit despite modest revenue and order count.
--   Acme 0.70 | Globex 0.75 | Initech 0.65 | Umbrella 0.70
--   Wayne 0.60 | Stark 0.20 | Wonka 0.50 | Hooli 0.68

-- 2025 orders: the primary "last year" test data. order_count and
-- total_revenue below are the ground truth for validating any generated SQL.
DO $$
DECLARE
    cust RECORD;
    i INTEGER;
    per_order NUMERIC;
    new_order_id INTEGER;
    cost_fraction NUMERIC;
BEGIN
    FOR cust IN
        SELECT * FROM (VALUES
            ('Acme Corp',          3,  50000.00, 0.70),
            ('Globex Inc',        12,  30000.00, 0.75),
            ('Initech',            2,  45000.00, 0.65),
            ('Umbrella Corp',      9,  25000.00, 0.70),
            ('Wayne Enterprises',  5,  20000.00, 0.60),
            ('Stark Industries',   6,  35000.00, 0.20),
            ('Wonka Industries',   1,  10000.00, 0.50),
            ('Hooli LLC',          7,  28000.00, 0.68)
        ) AS t(name, order_count, total_revenue, cost_fraction)
    LOOP
        per_order := ROUND(cust.total_revenue / cust.order_count, 2);
        FOR i IN 1..cust.order_count LOOP
            INSERT INTO orders (customer_id, order_date, status, total_amount)
            SELECT customer_id,
                   DATE '2025-01-01' + ((i * 29) % 330) * INTERVAL '1 day',
                   'completed',
                   per_order
            FROM customers WHERE name = cust.name
            RETURNING order_id INTO new_order_id;

            INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)
            VALUES (new_order_id, 'Standard Package', 1, per_order,
                    ROUND(per_order * cust.cost_fraction, 2));
        END LOOP;
    END LOOP;
END $$;

-- A couple of CANCELLED 2025 orders — these must be excluded from revenue.
-- A naive query that forgets `WHERE status = 'completed'` will overcount.
DO $$
DECLARE
    new_order_id INTEGER;
BEGIN
    INSERT INTO orders (customer_id, order_date, status, total_amount)
    SELECT customer_id, DATE '2025-03-14', 'cancelled', 8000.00
    FROM customers WHERE name = 'Acme Corp'
    RETURNING order_id INTO new_order_id;
    INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)
    VALUES (new_order_id, 'Standard Package', 1, 8000.00, 5600.00);

    INSERT INTO orders (customer_id, order_date, status, total_amount)
    SELECT customer_id, DATE '2025-09-02', 'cancelled', 4000.00
    FROM customers WHERE name = 'Globex Inc'
    RETURNING order_id INTO new_order_id;
    INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)
    VALUES (new_order_id, 'Standard Package', 1, 4000.00, 3000.00);
END $$;

-- Noise orders in adjacent years (2024, 2026) so date filtering is actually
-- tested, not trivially "all the data there is". Amounts are arbitrary.
DO $$
DECLARE
    cust RECORD;
    yr INTEGER;
    i INTEGER;
    new_order_id INTEGER;
BEGIN
    FOR cust IN SELECT customer_id, name FROM customers LOOP
        FOREACH yr IN ARRAY ARRAY[2024, 2026] LOOP
            FOR i IN 1..2 LOOP
                INSERT INTO orders (customer_id, order_date, status, total_amount)
                VALUES (cust.customer_id,
                        MAKE_DATE(yr, 1 + ((i * 5) % 11), 1 + ((i * 7) % 27)),
                        'completed', 3000.00 + (i * 500))
                RETURNING order_id INTO new_order_id;
                INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)
                VALUES (new_order_id, 'Standard Package', 1, 3000.00 + (i * 500),
                        ROUND((3000.00 + (i * 500)) * 0.65, 2));
            END LOOP;
        END LOOP;
    END LOOP;
END $$;

-- systems table: ground truth for "how many systems signed off last year (2025)?" -> 12
DO $$
DECLARE
    i INTEGER;
BEGIN
    FOR i IN 1..12 LOOP  -- signed off in 2025 (the answer to the example question)
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'signed_off',
                DATE '2025-01-01' + ((i * 23) % 330) * INTERVAL '1 day',
                DATE '2024-11-01' + (i * 3) * INTERVAL '1 day');
    END LOOP;

    FOR i IN 13..17 LOOP  -- signed off in 2024 (noise year)
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'signed_off',
                DATE '2024-01-01' + ((i * 23) % 330) * INTERVAL '1 day',
                DATE '2023-11-01' + (i * 3) * INTERVAL '1 day');
    END LOOP;

    FOR i IN 18..21 LOOP  -- signed off in 2026 (noise year)
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'signed_off',
                DATE '2026-01-01' + ((i * 23) % 200) * INTERVAL '1 day',
                DATE '2025-11-01' + (i * 3) * INTERVAL '1 day');
    END LOOP;

    FOR i IN 22..27 LOOP  -- still pending, never signed off
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'pending', NULL,
                DATE '2025-06-01' + (i * 3) * INTERVAL '1 day');
    END LOOP;

    FOR i IN 28..32 LOOP  -- in progress
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'in_progress', NULL,
                DATE '2025-08-01' + (i * 3) * INTERVAL '1 day');
    END LOOP;

    FOR i IN 33..35 LOOP  -- cancelled before sign-off
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'), 'cancelled', NULL,
                DATE '2025-02-01' + (i * 3) * INTERVAL '1 day');
    END LOOP;
END $$;
