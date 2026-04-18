CREATE DATABASE IF NOT EXISTS reports;
USE reports;

-- CDC customers
CREATE TABLE IF NOT EXISTS cdc_customers_kafka
(
    `value` String
)
    ENGINE = Kafka
    SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'crm.public.customers',
    kafka_group_name = 'ch_cdc_customers',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1;

CREATE TABLE IF NOT EXISTS dim_customers
(
    customer_id String,
    email String,
    first_name String,
    last_name String,
    updated_at DateTime
)
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY customer_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_customers_to_dim
TO dim_customers
AS
SELECT
    JSONExtractString(value, 'payload', 'after', 'id') AS customer_id,
    JSONExtractString(value, 'payload', 'after', 'email') AS email,
    JSONExtractString(value, 'payload', 'after', 'first_name') AS first_name,
    JSONExtractString(value, 'payload', 'after', 'last_name') AS last_name,
    now() AS updated_at
FROM cdc_customers_kafka
WHERE JSONExtractString(value, 'payload', 'op') IN ('c','u');

-- CDC prosthetics
CREATE TABLE IF NOT EXISTS cdc_prosthetics_kafka
(
    `value` String
)
    ENGINE = Kafka
    SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'crm.public.prosthetics',
    kafka_group_name = 'ch_cdc_prosthetics',
    kafka_format = 'JSONAsString',
    kafka_num_consumers = 1;

CREATE TABLE IF NOT EXISTS dim_prosthetics
(
    prosthetic_id String,
    customer_id String,
    model String,
    updated_at DateTime
)
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY prosthetic_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_prosthetics_to_dim
TO dim_prosthetics
AS
SELECT
    JSONExtractString(value, 'payload', 'after', 'id') AS prosthetic_id,
    JSONExtractString(value, 'payload', 'after', 'customer_id') AS customer_id,
    JSONExtractString(value, 'payload', 'after', 'model') AS model,
    now() AS updated_at
FROM cdc_prosthetics_kafka
WHERE JSONExtractString(value, 'payload', 'op') IN ('c','u');