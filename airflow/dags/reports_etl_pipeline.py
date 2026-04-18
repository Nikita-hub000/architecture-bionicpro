from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import clickhouse_connect
from airflow import DAG
from airflow.operators.python import PythonOperator


def _ch_client():
    return clickhouse_connect.get_client(
        host=os.environ["CH_HOST"],
        port=int(os.environ.get("CH_PORT", "8123")),
        username=os.environ["CH_USER"],
        password=os.environ["CH_PASSWORD"],
        database=os.environ["CH_DB"],
    )


def init_schema():
    client = _ch_client()

    client.command("""
    CREATE TABLE IF NOT EXISTS crm_users (
      user_sub String,
      email String,
      first_name String,
      last_name String,
      prosthetic_id String,
      updated_at DateTime
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (user_sub)
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS telemetry_events (
      user_sub String,
      ts DateTime,
      latency_ms UInt32,
      alert UInt8
    )
    ENGINE = MergeTree
    ORDER BY (user_sub, ts)
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS report_mart (
      user_sub String,
      period_start Date,
      period_end Date,
      sessions UInt32,
      avg_latency_ms Float64,
      alerts UInt32,
      etl_loaded_at DateTime
    )
    ENGINE = ReplacingMergeTree(etl_loaded_at)
    ORDER BY (user_sub, period_start, period_end)
    """)

    client.command("""
    CREATE TABLE IF NOT EXISTS etl_watermark (
      name String,
      value DateTime,
      updated_at DateTime
    )
    ENGINE = ReplacingMergeTree(updated_at)
    ORDER BY (name)
    """)


def load_crm_csv():
    csv_path = os.environ["CRM_CSV_PATH"]
    df = pd.read_csv(csv_path)

    # КЛЮЧ ДЛЯ ВИТРИНЫ = email (чтобы совпадать с Keycloak claim `email`)
    # Оставляем колонку user_sub в ClickHouse, но заполняем её email-ом.
    df["user_sub"] = df["email"]

    df["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

    client = _ch_client()
    client.insert_df("crm_users", df)


def generate_mock_telemetry():
    # Демо-генерация телеметрии: в реальном проекте тут Extract из DB датчиков.
    # Генерируем события за последний час для всех пользователей из crm_users.
    client = _ch_client()
    users = client.query_df("SELECT DISTINCT user_sub FROM crm_users").to_dict("records")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for u in users:
        sub = u["user_sub"]
        for i in range(60):
            ts = now - timedelta(minutes=i)
            latency = 50 + (i % 50)
            alert = 1 if (i % 37 == 0) else 0
            rows.append((sub, ts, latency, alert))

    if rows:
        client.insert("telemetry_events", rows, column_names=["user_sub", "ts", "latency_ms", "alert"])


def build_mart_and_watermark():
    client = _ch_client()

    # watermark = максимум ts из telemetry_events
    wm_df = client.query_df("SELECT max(ts) AS max_ts FROM telemetry_events")
    max_ts = wm_df.iloc[0]["max_ts"]
    if pd.isna(max_ts):
        return

    # период витрины: сутки, в которые попадает max_ts
    period_start = max_ts.date()
    period_end = period_start  # для простоты: дневной отчёт

    # агрегируем телеметрию по user_sub за этот день
    client.command(f"""
    INSERT INTO report_mart
    SELECT
      user_sub,
      toDate('{period_start}') AS period_start,
      toDate('{period_end}') AS period_end,
      count() AS sessions,
      avg(toFloat64(latency_ms)) AS avg_latency_ms,
      sum(toUInt32(alert)) AS alerts,
      now() AS etl_loaded_at
    FROM telemetry_events
    WHERE toDate(ts) = toDate('{period_start}')
    GROUP BY user_sub
    """)

    client.command("""
    INSERT INTO etl_watermark
    VALUES ('reports', (SELECT max(ts) FROM telemetry_events), now())
    """)


with DAG(
    dag_id="reports_etl",
    start_date=datetime(2026, 1, 1),
    schedule="*/15 * * * *",
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    tags=["reports", "clickhouse"],
) as dag:
    t1 = PythonOperator(task_id="init_schema", python_callable=init_schema)
    t2 = PythonOperator(task_id="load_crm_csv", python_callable=load_crm_csv)
    t3 = PythonOperator(task_id="generate_mock_telemetry", python_callable=generate_mock_telemetry)
    t4 = PythonOperator(task_id="build_mart_and_watermark", python_callable=build_mart_and_watermark)

    t1 >> t2 >> t3 >> t4
