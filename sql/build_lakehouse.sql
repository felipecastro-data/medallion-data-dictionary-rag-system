-- Builds the lakehouse_demo Bronze, Silver and Gold tables from the public samples.tpch catalog,
-- and creates the empty data_dictionary table. `medallion-describe` fills in the dictionary rows.
--
-- Run this once on a new workspace, in the Databricks SQL editor.
--
-- WARNING: this is the one place in the project that scans whole tables. It copies about 38 million
-- rows, and each CREATE OR REPLACE replaces the table and removes its column comments. To restore
-- comments afterwards, run `medallion-comments`. Existing data_dictionary rows are kept.
--
-- Expected row counts:
--   raw_customers, customers    750,000
--   raw_orders, orders          7,500,000
--   raw_lineitem, order_items   29,999,795
--   customer_ltv                499,989
--   daily_sales_summary         2,406

CREATE CATALOG IF NOT EXISTS lakehouse_demo;
CREATE SCHEMA IF NOT EXISTS lakehouse_demo.`1_bronze`;
CREATE SCHEMA IF NOT EXISTS lakehouse_demo.`2_silver`;
CREATE SCHEMA IF NOT EXISTS lakehouse_demo.`3_gold`;

-- Bronze: unchanged source rows, plus when and where each one was loaded from.

CREATE OR REPLACE TABLE lakehouse_demo.`1_bronze`.raw_customers AS
SELECT *, current_timestamp() AS _ingested_at, 'samples.tpch.customer' AS _source
FROM samples.tpch.customer;

CREATE OR REPLACE TABLE lakehouse_demo.`1_bronze`.raw_orders AS
SELECT *, current_timestamp() AS _ingested_at, 'samples.tpch.orders' AS _source
FROM samples.tpch.orders;

CREATE OR REPLACE TABLE lakehouse_demo.`1_bronze`.raw_lineitem AS
SELECT *, current_timestamp() AS _ingested_at, 'samples.tpch.lineitem' AS _source
FROM samples.tpch.lineitem;

-- Silver: one row per key, readable names, trimmed text and fixed types.

CREATE OR REPLACE TABLE lakehouse_demo.`2_silver`.customers AS
SELECT
  CAST(c_custkey AS BIGINT) AS customer_id,
  TRIM(c_name) AS customer_name,
  TRIM(c_address) AS address,
  CAST(c_nationkey AS INT) AS nation_key,
  TRIM(c_phone) AS phone,
  CAST(c_acctbal AS DECIMAL(12, 2)) AS account_balance,
  TRIM(c_mktsegment) AS market_segment
FROM lakehouse_demo.`1_bronze`.raw_customers
WHERE c_custkey IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY c_custkey ORDER BY c_custkey) = 1;

CREATE OR REPLACE TABLE lakehouse_demo.`2_silver`.orders AS
WITH unique_orders AS (
  SELECT
    CAST(o_orderkey AS BIGINT) AS order_id,
    CAST(o_custkey AS BIGINT) AS customer_id,
    TRIM(o_orderstatus) AS order_status,
    CAST(o_totalprice AS DECIMAL(12, 2)) AS total_price,
    CAST(o_orderdate AS DATE) AS order_date,
    TRIM(o_orderpriority) AS order_priority,
    TRIM(o_clerk) AS clerk
  FROM lakehouse_demo.`1_bronze`.raw_orders
  WHERE o_orderkey IS NOT NULL AND o_orderdate IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY o_orderkey ORDER BY o_orderkey) = 1
)
SELECT
  o.customer_id,
  o.order_id,
  o.order_status,
  o.total_price,
  o.order_date,
  o.order_priority,
  o.clerk,
  c.customer_name,
  c.market_segment
FROM unique_orders AS o
LEFT JOIN lakehouse_demo.`2_silver`.customers AS c ON o.customer_id = c.customer_id;

CREATE OR REPLACE TABLE lakehouse_demo.`2_silver`.order_items AS
WITH unique_lines AS (
  SELECT
    CAST(l_orderkey AS BIGINT) AS order_id,
    CAST(l_linenumber AS INT) AS line_number,
    CAST(l_partkey AS BIGINT) AS part_id,
    CAST(l_quantity AS DECIMAL(10, 2)) AS quantity,
    CAST(l_extendedprice AS DECIMAL(12, 2)) AS extended_price,
    CAST(l_discount AS DECIMAL(5, 2)) AS discount,
    CAST(l_tax AS DECIMAL(5, 2)) AS tax,
    CAST(l_shipdate AS DATE) AS ship_date
  FROM lakehouse_demo.`1_bronze`.raw_lineitem
  QUALIFY ROW_NUMBER() OVER (PARTITION BY l_orderkey, l_linenumber ORDER BY l_orderkey) = 1
)
SELECT
  l.order_id,
  l.line_number,
  l.part_id,
  l.quantity,
  l.extended_price,
  l.discount,
  l.tax,
  l.ship_date,
  l.extended_price * (1 - l.discount) AS net_amount,
  o.order_date,
  o.customer_id
FROM unique_lines AS l
LEFT JOIN lakehouse_demo.`2_silver`.orders AS o ON l.order_id = o.order_id;

-- Gold: business aggregates.

CREATE OR REPLACE TABLE lakehouse_demo.`3_gold`.daily_sales_summary AS
SELECT
  order_date,
  COUNT(DISTINCT order_id) AS order_count,
  SUM(net_amount) AS total_revenue,
  SUM(quantity) AS total_quantity,
  ROUND(SUM(net_amount) / COUNT(DISTINCT order_id), 2) AS avg_order_value
FROM lakehouse_demo.`2_silver`.order_items
GROUP BY order_date
ORDER BY order_date;

CREATE OR REPLACE TABLE lakehouse_demo.`3_gold`.customer_ltv AS
WITH customer_orders AS (
  SELECT
    customer_id,
    COUNT(DISTINCT order_id) AS total_orders,
    SUM(net_amount) AS lifetime_revenue,
    MIN(order_date) AS first_order_date,
    MAX(order_date) AS last_order_date
  FROM lakehouse_demo.`2_silver`.order_items
  GROUP BY customer_id
)
SELECT
  o.customer_id,
  o.total_orders,
  o.lifetime_revenue,
  o.first_order_date,
  o.last_order_date,
  DATEDIFF(o.last_order_date, o.first_order_date) AS customer_tenure_days,
  ROUND(o.lifetime_revenue / o.total_orders, 2) AS avg_order_value,
  c.customer_name,
  c.market_segment
FROM customer_orders AS o
LEFT JOIN lakehouse_demo.`2_silver`.customers AS c ON o.customer_id = c.customer_id;

-- IF NOT EXISTS, so running this file again never removes reviewed descriptions.

CREATE TABLE IF NOT EXISTS lakehouse_demo.`3_gold`.data_dictionary (
  table_name STRING,
  column_name STRING,
  description STRING,
  source STRING,
  generated_at TIMESTAMP,
  embedding_id STRING
);
