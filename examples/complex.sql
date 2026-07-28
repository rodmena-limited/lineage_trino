-- ============================================================================
-- Complex SQL: Multi-stage ETL with CTEs, window functions, CASE, UNION
-- Simulates a real-world analytics pipeline
-- ============================================================================

-- 1. CREATE TABLE AS SELECT: daily order summary
CREATE TABLE analytics.daily_order_summary AS
WITH order_base AS (
    SELECT
        o.id AS order_id,
        o.customer_id,
        o.order_date,
        o.status,
        o.total_amount,
        o.discount,
        o.tax,
        c.name AS customer_name,
        c.segment AS customer_segment,
        c.acquisition_date,
        a.city,
        a.state,
        a.country
    FROM raw.orders o
    JOIN raw.customers c ON o.customer_id = c.id
    LEFT JOIN raw.addresses a ON c.id = a.customer_id AND a.is_primary = TRUE
    WHERE o.order_date >= DATE '2024-01-01'
),
line_aggregates AS (
    SELECT
        ol.order_id,
        COUNT(ol.id) AS line_items_count,
        SUM(ol.quantity) AS total_quantity,
        SUM(ol.extended_price) AS gross_amount,
        SUM(ol.extended_price * (1 - COALESCE(ol.discount, 0))) AS net_amount,
        COUNT(DISTINCT ol.product_id) AS unique_products,
        AVG(ol.quantity) AS avg_quantity_per_item
    FROM raw.order_lines ol
    WHERE ol.order_id IN (SELECT order_id FROM order_base)
    GROUP BY ol.order_id
),
payment_summary AS (
    SELECT
        p.order_id,
        COUNT(p.id) AS payment_count,
        SUM(p.amount) AS total_paid,
        MAX(p.payment_date) AS last_payment_date,
        MIN(p.payment_date) AS first_payment_date,
        COUNT(DISTINCT p.payment_method) AS payment_methods_used,
        SUM(CASE WHEN p.status = 'failed' THEN 1 ELSE 0 END) AS failed_payments
    FROM raw.payments p
    WHERE p.order_id IN (SELECT order_id FROM order_base)
    GROUP BY p.order_id
),
order_ranking AS (
    SELECT
        ob.order_id,
        ob.customer_id,
        ob.total_amount,
        ROW_NUMBER() OVER (
            PARTITION BY ob.customer_id
            ORDER BY ob.total_amount DESC
        ) AS order_rank_by_amount,
        RANK() OVER (
            PARTITION BY ob.customer_id
            ORDER BY ob.order_date DESC
        ) AS order_recency_rank,
        SUM(ob.total_amount) OVER (
            PARTITION BY ob.customer_id
            ORDER BY ob.order_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cumulative_customer_spend,
        LAG(ob.total_amount, 1) OVER (
            PARTITION BY ob.customer_id
            ORDER BY ob.order_date
        ) AS prev_order_amount,
        LEAD(ob.order_date, 1) OVER (
            PARTITION BY ob.customer_id
            ORDER BY ob.order_date
        ) AS next_order_date
    FROM order_base ob
)
SELECT
    -- Primary keys and identifiers
    ob.order_id,
    ob.customer_id,

    -- Customer info
    ob.customer_name,
    ob.customer_segment,
    ob.acquisition_date,

    -- Order details
    ob.order_date,
    ob.status,
    ob.total_amount AS order_total,
    ob.discount,
    ob.tax,
    ob.total_amount - ob.tax + COALESCE(ob.discount, 0) AS taxable_base,

    -- Line item aggregates
    COALESCE(la.line_items_count, 0) AS line_items_count,
    COALESCE(la.total_quantity, 0) AS total_quantity,
    la.gross_amount,
    la.net_amount,
    la.unique_products,
    la.avg_quantity_per_item,

    -- Payment info
    COALESCE(ps.payment_count, 0) AS payment_count,
    COALESCE(ps.total_paid, 0) AS total_paid,
    COALESCE(ps.failed_payments, 0) AS failed_payments,
    ps.first_payment_date,
    ps.last_payment_date,

    -- Ranking & analytics
    or2.order_rank_by_amount,
    or2.order_recency_rank,
    or2.cumulative_customer_spend,
    or2.prev_order_amount,
    or2.next_order_date,

    -- Derived metrics
    ob.total_amount - COALESCE(ps.total_paid, 0) AS balance_due,
    CASE
        WHEN ob.status = 'shipped' AND ps.total_paid >= ob.total_amount THEN 'paid'
        WHEN ob.status = 'shipped' AND ps.total_paid < ob.total_amount THEN 'partially_paid'
        WHEN ob.status = 'cancelled' THEN 'cancelled'
        WHEN ob.status = 'returned' THEN 'returned'
        ELSE 'pending'
    END AS payment_status,
    DENSE_RANK() OVER (ORDER BY ob.total_amount DESC) AS global_sales_rank,

    -- Geography
    ob.city,
    ob.state,
    ob.country,

    -- Audit
    CURRENT_TIMESTAMP AS processed_at

FROM order_base ob
LEFT JOIN line_aggregates la ON ob.order_id = la.order_id
LEFT JOIN payment_summary ps ON ob.order_id = ps.order_id
LEFT JOIN order_ranking or2 ON ob.order_id = or2.order_id;

-- 2. Union of two sources
CREATE TABLE analytics.unified_customer_view AS
SELECT
    id,
    name,
    email,
    'current' AS source_system,
    created_at
FROM raw.customers
WHERE status = 'active'
UNION ALL
SELECT
    id,
    name,
    email,
    'legacy' AS source_system,
    created_at
FROM archive.customers_backup
WHERE status = 'active';

-- 3. INSERT with complex SELECT
INSERT INTO analytics.monthly_kpi (metric_name, metric_value, measurement_date)
SELECT
    'total_revenue' AS metric_name,
    SUM(total_amount) AS metric_value,
    DATE_TRUNC('month', order_date) AS measurement_date
FROM raw.orders
WHERE status IN ('completed', 'shipped')
GROUP BY DATE_TRUNC('month', order_date)
UNION ALL
SELECT
    'active_customers' AS metric_name,
    COUNT(DISTINCT customer_id) AS metric_value,
    DATE_TRUNC('month', order_date) AS measurement_date
FROM raw.orders
WHERE order_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '12' MONTH
GROUP BY DATE_TRUNC('month', order_date);
