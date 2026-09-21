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
DO $$  -- anonymous procedural (PL/pgSQL) block
DECLARE  -- variable declarations
    adjectives TEXT[] := ARRAY['Nova','Vertex','Quantum','Silver','Blue','Rapid','Summit','Cedar',  -- first half of the name: a 30-item word list ...
                                'Falcon','Orion','Crimson','Golden','Northern','Pacific','Atlas',  -- ... continued
                                'Titan','Echo','Lunar','Solar','Amber','Ivory','Onyx','Sapphire',  -- ... continued
                                'Emerald','Granite','Copper','Zenith','Vector','Nimbus','Cobalt'];  -- ... end of the adjective list
    nouns TEXT[] := ARRAY['Systems','Logistics','Holdings','Industries','Solutions','Partners',  -- second half of the name: a 20-item word list ...
                           'Group','Technologies','Traders','Works','Dynamics','Ventures',  -- ... continued
                           'Networks','Enterprises','Analytics','Robotics','Freight','Foods',  -- ... continued
                           'Media','Capital'];  -- ... end of the noun list
    i INTEGER;  -- loop counter 1..52; also the uniqueness suffix
    cname TEXT;  -- the generated customer name
BEGIN  -- start of the executable part
    FOR i IN 1..52 LOOP  -- one iteration per new customer
        cname := adjectives[1 + (i % array_length(adjectives, 1))] || ' ' ||  -- pick an adjective by i modulo list length (SQL arrays are 1-indexed, hence 1 +) ...
                 nouns[1 + (i % array_length(nouns, 1))] || ' ' || i;  -- ... plus a noun and the number i, e.g. 'Nova Systems 1'; the number keeps names unique
        INSERT INTO customers (name, email, created_at)  -- insert the customer ...
        VALUES (cname, 'contact' || i || '@bulkcustomer.example',  -- unique email built from i, satisfying the UNIQUE constraint
                DATE '2022-01-01' + (i * 11) * INTERVAL '1 day');  -- sign-up dates spread from 2022 into mid-2023 (11 days apart)
    END LOOP;  -- end of customer loop
END $$;  -- end of block

-- Bulk background orders + order_items for ALL 60 customers.
--   - The original 8 signature customers only get 2023 added here
--     (their 2025 figures and existing 2024/2026 noise are untouched).
--   - The 52 new customers get orders across 2023-2026, INCLUDING
--     2025 -- but bounded (<=9 orders/year, <=$4,500/order, cost
--     fraction <=0.80) so none of them can beat the signature
--     customers on 2025 revenue (max ~$40,500 < Acme's $50,000),
--     order count (max 9 < Globex's 12), or profit (max ~$20,250 <
--     Stark's ~$28,000).
DO $$  -- second procedural block
DECLARE  -- variable declarations
    cust RECORD;  -- one customer row (id and name)
    yr INTEGER;  -- the year currently being generated
    order_count INTEGER;  -- how many orders this customer gets in this year
    i INTEGER;  -- order loop counter
    item_count INTEGER;  -- how many line items this order has
    j INTEGER;  -- line-item loop counter
    new_order_id INTEGER;  -- id of the order just inserted
    order_amount NUMERIC;  -- total value of the current order
    item_amount NUMERIC;  -- value of each line item (order amount split evenly)
    cost_fraction NUMERIC;  -- this line item's cost as a fraction of its price (bounded so profit stays modest)
    products TEXT[] := ARRAY['Standard Package','Premium Package','Enterprise License',  -- catalogue of product names to pick from randomly ...
                              'Support Plan','Consulting Hours','Hardware Bundle',  -- ... continued
                              'Software Add-on','Training Session','Maintenance Contract',  -- ... continued
                              'Custom Integration'];  -- ... end of the product list
    years_for_this_customer INTEGER[];  -- which years to generate for the current customer
BEGIN  -- start of the executable part
    FOR cust IN SELECT customer_id, name FROM customers LOOP  -- every customer, all 60
        IF cust.name IN ('Acme Corp','Globex Inc','Initech','Umbrella Corp',  -- is this one of the 8 signature customers? ...
                          'Wayne Enterprises','Stark Industries','Wonka Industries','Hooli LLC') THEN  -- ... (list continued)
            years_for_this_customer := ARRAY[2023];  -- signature customers: 2023 only, so their 2024/2025/2026 figures stay untouched
        ELSE  -- otherwise it is one of the 52 generated customers
            years_for_this_customer := ARRAY[2023, 2024, 2025, 2026];  -- generated customers get all four years
        END IF;  -- end of the branch

        FOREACH yr IN ARRAY years_for_this_customer LOOP  -- once per year in the chosen list
            order_count := 2 + floor(random() * 8)::int;  -- 2 to 9 orders
            FOR i IN 1..order_count LOOP  -- create each order
                order_amount := ROUND((300 + random() * 4200)::numeric, 2);  -- $300-$4,500
                INSERT INTO orders (customer_id, order_date, status, total_amount)  -- insert one order ...
                VALUES (cust.customer_id,  -- for this customer
                        MAKE_DATE(yr, 1 + floor(random() * 12)::int, 1 + floor(random() * 27)::int),  -- random valid date in that year (day capped at 27 so every month works)
                        CASE WHEN random() < 0.05 THEN 'cancelled' ELSE 'completed' END,  -- about 5% of orders are cancelled
                        order_amount)  -- the amount computed above
                RETURNING order_id INTO new_order_id;  -- capture the new order's id

                item_count := 1 + floor(random() * 3)::int;  -- 1 to 3 items
                FOR j IN 1..item_count LOOP  -- create each line item
                    item_amount := ROUND((order_amount / item_count)::numeric, 2);  -- split the order amount evenly across its items
                    cost_fraction := ROUND((0.45 + random() * 0.35)::numeric, 2);  -- 0.45-0.80
                    INSERT INTO order_items (order_id, product_name, quantity, unit_price, cost_price)  -- insert the line item ...
                    VALUES (new_order_id,  -- attached to the order just created
                            products[1 + floor(random() * array_length(products, 1))::int],  -- random product name from the catalogue
                            1, item_amount, ROUND(item_amount * cost_fraction, 2));  -- quantity 1, price = item share, cost = price x cost fraction
                END LOOP;  -- end of line-item loop
            END LOOP;  -- end of order loop
        END LOOP;  -- end of year loop
    END LOOP;  -- end of customer loop
END $$;  -- end of block

-- Bulk background systems rows. The original "12 signed off in
-- 2025" ground truth is untouched -- these deliberately never land
-- in 2025, only 2023/2024/2026.
DO $$  -- third procedural block
DECLARE  -- variable declarations
    i INTEGER;  -- loop counter, also part of the system name
    st TEXT;  -- the randomly chosen status
    yr INTEGER;  -- the randomly chosen year (never 2025)
BEGIN  -- start of the executable part
    FOR i IN 36..150 LOOP  -- ids 36..150 continue after the 35 systems from 02_seed.sql (115 new rows, 150 in total)
        st := (ARRAY['signed_off','signed_off','pending','in_progress','cancelled'])[1 + floor(random() * 5)::int];  -- random status; 'signed_off' listed twice so it is twice as likely
        yr := (ARRAY[2023, 2024, 2026])[1 + floor(random() * 3)::int];  -- random year from a list that deliberately omits 2025
        INSERT INTO systems (name, status, signed_off_at, created_at)  -- insert one system ...
        VALUES ('System-' || LPAD(i::TEXT, 3, '0'),  -- zero-padded unique name, e.g. 'System-036'
                st,  -- the random status
                CASE WHEN st = 'signed_off'  -- only signed-off systems get a sign-off date ...
                     THEN MAKE_DATE(yr, 1 + floor(random() * 12)::int, 1 + floor(random() * 27)::int)  -- ... a random valid date in the chosen year
                     ELSE NULL END,  -- ... everything else stays NULL
                MAKE_DATE(yr - 1, 1 + floor(random() * 12)::int, 1 + floor(random() * 27)::int));  -- created the year before, so created_at is earlier than any sign-off
    END LOOP;  -- end of loop
END $$;  -- end of block
