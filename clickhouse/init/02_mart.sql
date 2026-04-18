CREATE DATABASE IF NOT EXISTS reports;
USE reports;

-- Факт телеметрии (нужен для MV витрины).
-- Даже если ETL будет наполнять эту таблицу позже, схема должна существовать при старте.
CREATE TABLE IF NOT EXISTS telemetry_events
(
    user_sub String,
    ts DateTime,
    latency_ms UInt32,
    alert UInt8
)
    ENGINE = MergeTree
    ORDER BY (user_sub, ts);

-- Витрина для отчётов (итоговая таблица)
CREATE TABLE IF NOT EXISTS report_mart_cdc
(
    user_key String,
    customer_id String,
    email String,
    first_name String,
    last_name String,
    prosthetic_id String,
    period_start Date,
    period_end Date,
    sessions UInt32,
    avg_latency_ms Float64,
    alerts UInt32,
    mart_updated_at DateTime
)
    ENGINE = ReplacingMergeTree(mart_updated_at)
    ORDER BY (user_key, period_start, period_end);

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_telemetry_to_report_mart_cdc
TO report_mart_cdc
AS
SELECT
    c.email AS user_key,
    c.customer_id AS customer_id,
    c.email AS email,
    c.first_name AS first_name,
    c.last_name AS last_name,
    p.prosthetic_id AS prosthetic_id,
    toDate(t.ts) AS period_start,
    toDate(t.ts) AS period_end,
    toUInt32(count()) AS sessions,
    avg(toFloat64(t.latency_ms)) AS avg_latency_ms,
    sum(toUInt32(t.alert)) AS alerts,
    now() AS mart_updated_at
FROM telemetry_events AS t
         INNER JOIN dim_customers AS c ON c.email = t.user_sub
         LEFT JOIN dim_prosthetics AS p ON p.customer_id = c.customer_id
GROUP BY
    user_key,
    customer_id,
    email,
    first_name,
    last_name,
    prosthetic_id,
    period_start,
    period_end;