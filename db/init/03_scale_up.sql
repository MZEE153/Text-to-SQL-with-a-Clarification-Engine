-- ============================================================
-- SCALE-UP: additional customers + bulk background transaction
-- history, layered on top of 02_seed.sql to make the dataset feel
-- like a real business (targeting 1000+ total rows) WITHOUT
-- disturbing the hand-crafted 2025 "best customer" ground truth
-- for the original 8 signature customers.
-- ============================================================

-- 52 additional customers (60 total). Names are procedurally
-- generated; a numeric suffix guarantees uniqueness -- only
-- `email` has a DB-level UNIQUE constraint, but duplicate `name`
-- values would silently merge two different customers together
-- under any GROUP BY name query, so uniqueness matters here too.
DO $$
DECLARE
    adjectives TEXT[] := ARRAY['Nova','Vertex','Quantum','Silver','Blue','Rapid','Summit','Cedar',
                                'Falcon','Orion','Crimson','Golden','Northern','Pacific','Atlas',
                                'Titan','Echo','Lunar','Solar','Amber','Ivory','Onyx','Sapphire',
                                'Emerald','Granite','Copper','Zenith','Vector','Nimbus','Cobalt'];
    nouns TEXT[] := ARRAY['Systems','Logistics','Holdings','Industries','Solutions','Partners',
                           'Group','Technologies','Traders','Works','Dynamics','Ventures',
                           'Networks','Enterprises','Analytics','Robotics','Freight','Foods',
                           'Media','Capital'];
    i INTEGER;
    cname TEXT;
BEGIN
    FOR i IN 1..52 LOOP
        cname := adjectives[1 + (i % array_length(adjectives, 1))] || ' ' ||
                 nouns[1 + (i % array_length(nouns, 1))] || ' ' || i;
        INSERT INTO customers (name, email, created_at)
        VALUES (cname, 'contact' || i || '@bulkcustomer.example',
                DATE '2022-01-01' + (i * 11) * INTERVAL '1 day');
    END LOOP;
END $$;

-- Bulk background orders + order_items for ALL 60 customers.
--   - The original 8 signature customers only get 2023 added here
--     (their 2025 figures and existing 2024/2026 noise are untouched).
--   - The 52 new customers get orders across 2023-2026, INCLUDING
--     2025 -- but bounded (<=9 orders/year, <=$4,500/order, cost
--     fraction <=0.80) so none of them can beat the signature
--     customers on 2025 revenue (max ~$40,500 < Acme's $50,000),
--     order count (max 9 < Globex's 12), or profit (max ~$20,250 <
--     Stark's ~$28,000).
DO $$
DECLARE
    cust RECORD;
    yr INTEGER;
    order_count INTEGER;
    i INTEGER;
    item_count INTEGER;
    j INTEGER;
    new_order_id INTEGER;
    order_amount NUMERIC;
    item_amount NUMERIC;
    cost_fraction NUMERIC;
    products TEXT[] := ARRAY['Standard Package','Premium Package','Enterprise License',
                              'Support Plan','Consulting Hours','Hardware Bundle',
                              'Software Add-on','Training Session','Maintenance Contract',
                              'Custom Integration'];
    years_for_this_customer INTEGER[];
BEGIN
    FOR cust IN SELECT customer_id, name FROM customers LOOP
        IF cust.name IN ('Acme Corp','Globex Inc','Initech','Umbrella Corp',
                          'Wayne Enterprises','Stark Industries','Wonka Industries','Hooli LLC') THEN
            years_for_this_customer := ARRAY[2023];
        ELSE
            years_for_this_customer := ARRAY[2023, 2024, 2025, 2026];
        END IF;

        FOREACH yr IN ARRAY years_for_this_customer LOOP
            order_count := 2 + floor(random() * 8)::int;  -- 2 to 9 orders
            FOR i IN 1..order_count LOOP
                order_amount := ROUND((300 + random() * 4200)::numeric, 2);  -- $300-$4,500
                INSERT INTO orders (customer_id, order_date, status, total_amount)
                VALUES (cust.customer_id,
                        MAKE_DATE(yr, 1 + floor(random() * 12)::int, 1 + floor(random() * 27)::int),
                        CASE WHEN random() < 0.05 THEN 'cancelled' ELSE 'completed' END,
                        order_amount)
                RETURNING order_id INTO new_order_id;

                item_count := 1 + floor(random() * 3)::int;  -- 1 to 3 items
                FOR j IN 1..item_count LOOP
                    item_amount := ROUND((order_amount / item_count)::numeric, 2);
                    cost_fraction := ROUND((0.45 + random() * 0.35)::numeric, 2);  -- 0.45-0.80
                    INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)
                    VALUES (new_order_id,
                            products[1 + floor(random() * array_length(products, 1))::int],
                            1, item_amount, ROUND(item_amount * cost_fraction, 2));
                END LOOP;
            END LOOP;
        END LOOP;
    END LOOP;
END $$;

-- Bulk background systems rows. The original "12 signed off in
-- 2025" ground truth is untouched -- these deliberately never land
-- in 2025, only 2023/2024/2026.
DO $$
DECLARE
    i INTEGER;
    st TEXT;
    yr INTEGER;
BEGIN
    FOR i IN 36..150 LOOP
        st := (ARRAY['signed_off','signed_off','pending','in_progress','cancelled'])[1 + floor(random() * 5)::int];
        yr := (ARRAY[2023, 2024, 2026])[1 + floor(random() * 3)::int];
        INSERT INTO systems (name, status, signed_off_at, created_at)
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'),
                st,
                CASE WHEN st = 'signed_off'
                     THEN MAKE_DATE(yr, 1 + floor(random() * 12)::int, 1 + floor(random() * 27)::int)
                     ELSE NULL END,
                MAKE_DATE(yr - 1, 1 + floor(random() * 12)::int, 1 + floor(random() * 27)::int));
    END LOOP;
END $$;
