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

    # ВАЖНО: CRM таблицы больше не создаём/не используем (crm_users, report_mart, etl_watermark)
    # Для CDC dims и новой витрины схемы создаются init-скриптами ClickHouse при старте контейнера.

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


def generate_mock_telemetry():
    """
    Демо-генерация телеметрии.
    Теперь источник пользователей = CDC-измерение dim_customers (email),
    чтобы данные автоматически попадали в report_mart_cdc через MV.
    """
    client = _ch_client()

    # Берём email из dim_customers — это тот же ключ, что ожидает MV (JOIN c.email = t.user_sub)
    users = client.query_df("SELECT DISTINCT email FROM dim_customers").to_dict("records")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for u in users:
        sub = u["email"]
        for i in range(60):
            ts = now - timedelta(minutes=i)
            latency = 50 + (i % 50)
            alert = 1 if (i % 37 == 0) else 0
            rows.append((sub, ts, latency, alert))

    if rows:
        client.insert("telemetry_events", rows, column_names=["user_sub", "ts", "latency_ms", "alert"])


with DAG(
    dag_id="reports_etl_cdc",
    start_date=datetime(2026, 1, 1),
    schedule="*/15 * * * *",
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=2)},
    tags=["reports", "clickhouse", "cdc"],
) as dag:
    t1 = PythonOperator(task_id="init_schema", python_callable=init_schema)
    t2 = PythonOperator(task_id="generate_mock_telemetry", python_callable=generate_mock_telemetry)

    t1 >> t2