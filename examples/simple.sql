-- Simple: basic SELECT with column alias
SELECT
    id,
    name,
    email
FROM customers;

-- With transformation: expression in SELECT
SELECT
    product_id,
    quantity,
    price,
    quantity * price AS total_price
FROM order_items;

-- Aggregation: simple GROUP BY
SELECT
    category,
    COUNT(*) AS product_count,
    AVG(price) AS avg_price
FROM products
GROUP BY category;

-- JOIN: two tables
SELECT
    o.id AS order_id,
    o.order_date,
    c.name AS customer_name,
    c.email AS customer_email
FROM orders o
JOIN customers c ON o.customer_id = c.id;

-- CTE: simple WITH clause
WITH active_customers AS (
    SELECT id, name, email
    FROM customers
    WHERE status = 'active'
)
SELECT
    id,
    name,
    email
FROM active_customers;
