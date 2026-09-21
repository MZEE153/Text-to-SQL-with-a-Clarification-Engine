-- Sample business schema for the Text-to-SQL project.
-- Deliberately small (4 tables) but with enough structure that
-- "best customer" genuinely has multiple different valid answers
-- depending on how "best" is defined.

CREATE TABLE customers (  -- one row per customer company
    customer_id   SERIAL PRIMARY KEY,  -- auto-incrementing integer id; PRIMARY KEY = unique + not null + automatically indexed
    name          TEXT NOT NULL,  -- customer display name; required (not unique, which is why the seed data makes names unique by hand)
    email         TEXT NOT NULL UNIQUE,  -- billing contact; UNIQUE stops two customers sharing one email
    created_at    DATE NOT NULL  -- date the customer signed up; required
);

CREATE TABLE orders (  -- one row per order placed by a customer
    order_id      SERIAL PRIMARY KEY,  -- auto-incrementing order id
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),  -- FOREIGN KEY: every order must belong to an existing customer
    order_date    DATE NOT NULL,  -- when the order was placed; this is what "last year" questions filter on
    status        TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),  -- CHECK restricts values to these two; "sales" should only count 'completed'
    total_amount  NUMERIC(12, 2) NOT NULL  -- order value: exact decimal, up to 12 digits with 2 after the point (exact, so money never drifts like floats do)
);

CREATE TABLE order_items (  -- one row per product line inside an order (an order has 1..n items)
    order_item_id SERIAL PRIMARY KEY,  -- auto-incrementing line-item id
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),  -- FOREIGN KEY: the order this line belongs to
    product_name  TEXT NOT NULL,  -- what was sold
    quantity      INTEGER NOT NULL,  -- how many units
    unit_price    NUMERIC(12, 2) NOT NULL,  -- price charged per unit (revenue side)
    cost_price    NUMERIC(12, 2) NOT NULL  -- what it cost us; unit_price - cost_price = profit per unit
);

-- Deliberately unrelated to customers/orders — this is the table the
-- "How many systems signed off last year?" example question targets.
CREATE TABLE systems (  -- standalone table with no foreign keys to the sales tables
    system_id     SERIAL PRIMARY KEY,  -- auto-incrementing system id
    name          TEXT NOT NULL,  -- system name, e.g. 'System-001'
    status        TEXT NOT NULL CHECK (status IN ('signed_off', 'pending', 'in_progress', 'cancelled')),  -- lifecycle state; CHECK limits it to these four values
    signed_off_at DATE,          -- NULL unless status = 'signed_off'
    created_at    DATE NOT NULL  -- when the system record was created; required
);

CREATE INDEX idx_orders_customer_id ON orders(customer_id);  -- speeds up joining orders to customers and per-customer lookups
CREATE INDEX idx_orders_order_date ON orders(order_date);  -- speeds up date-range filters such as "orders in 2025"
CREATE INDEX idx_order_items_order_id ON order_items(order_id);  -- speeds up joining line items to their order
CREATE INDEX idx_systems_status ON systems(status);  -- speeds up filtering systems by status
