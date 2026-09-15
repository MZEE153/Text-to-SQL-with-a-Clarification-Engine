-- Sample business schema for the Text-to-SQL project.
-- Deliberately small (4 tables) but with enough structure that
-- "best customer" genuinely has multiple different valid answers
-- depending on how "best" is defined.

CREATE TABLE customers (
    customer_id   SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    created_at    DATE NOT NULL
);

CREATE TABLE orders (
    order_id      SERIAL PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date    DATE NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('completed', 'cancelled')),
    total_amount  NUMERIC(12, 2) NOT NULL
);

CREATE TABLE order_items (
    order_item_id SERIAL PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_name  TEXT NOT NULL,
    quantity      INTEGER NOT NULL,
    unit_price    NUMERIC(12, 2) NOT NULL,
    cost_price    NUMERIC(12, 2) NOT NULL  -- what it cost us; unit_price - cost_price = profit per unit
);

-- Deliberately unrelated to customers/orders — this is the table the
-- "How many systems signed off last year?" example question targets.
CREATE TABLE systems (
    system_id     SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('signed_off', 'pending', 'in_progress', 'cancelled')),
    signed_off_at DATE,          -- NULL unless status = 'signed_off'
    created_at    DATE NOT NULL
);

CREATE INDEX idx_orders_customer_id ON orders(customer_id);
CREATE INDEX idx_orders_order_date ON orders(order_date);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_systems_status ON systems(status);
