## Что было сделано (итог по задаче 4)

### 1) Разделили потоки OLTP (CRM) и выгрузок/аналитики
- Вместо “массовой выгрузки CRM в CSV” для отчётности используем **CDC**:
    - **Debezium** слушает изменения в `crm_db` (Postgres logical replication).
    - Публикует события в **Kafka** (топики по таблицам).
    - **ClickHouse** читает топики через **KafkaEngine** и раскладывает данные в измерения (`dim_*`) через **Materialized View**.

Это снимает нагрузку с CRM: никаких full-scan/экспортов “для отчётов” больше не нужно.

### 2) Подготовили ClickHouse слой CDC + витрину
- В ClickHouse есть:
    - KafkaEngine-таблицы `cdc_*_kafka` (читают Kafka-топики Debezium)
    - `dim_customers`, `dim_prosthetics` (слой измерений в OLAP)
    - `telemetry_events` (факт телеметрии)
    - итоговая витрина `report_mart_cdc` + MV, которая собирает витрину из `telemetry_events` + `dim_*`

### 3) Перевели Reports API на новую витрину
- `reports-api` читает **`report_mart_cdc`** (а не старую `report_mart`) и генерирует JSON-отчёт.
- Для снижения нагрузки реализовано сохранение в S3 (MinIO) и выдача через CDN (Nginx).

### 4) Починили автосоздание бакета MinIO (критично для скачивания отчёта)
Была ошибка `NoSuchBucket`, потому что `minio-init` стартовал слишком рано и не успевал создать bucket.
- `minio-init` теперь **ждёт готовности MinIO** и только потом делает:
    - `mc mb local/reports`
    - `mc anonymous set download local/reports`

---

## Инструкция: последовательность действий “с нуля” до скачивания отчёта

### Шаг 0. Поднять окружение
Из корня проекта:

```shell script
docker compose up -d --build
```


Подождите 1–2 минуты (Keycloak/ClickHouse/Airflow стартуют не мгновенно).

---

### Шаг 1. Проверить, что MinIO создал bucket (иначе отчёт не сохранится)
Проверьте логи:

```shell script
docker logs bionicpro-minio-init --tail 200
```


Должны быть строки про:
- ожидание готовности MinIO
- успешное создание bucket `reports`
- установка anonymous download

Также можно зайти в консоль MinIO: `http://localhost:9001` и увидеть bucket `reports`.

---

### Шаг 2. Проверить, что Debezium connector создан и RUNNING
Проверки (с хоста):

```shell script
curl http://localhost:8083/
curl http://localhost:8083/connectors
curl http://localhost:8083/connectors/crm-postgres-connector/status
```


В статусе коннектора должно быть `state: RUNNING`.

---

### Шаг 3. Прогнать “события” CRM (чтобы CDC точно сгенерировал сообщения)
Выполните ваш SQL-скрипт в CRM (Postgres `crm_db`) — **он создаёт INSERT/UPDATE**, которые Debezium подхватит:

Вариант через `docker exec`:

```shell script
docker exec -i crm_db psql -U crm_user -d crm -f /docker-entrypoint-initdb.d/../init/../crm_cdc_emit.sql
```


Если путь неудобный, самый простой вариант — выполнить содержимое `crm/crm_cdc_emit.sql` через любой клиент Postgres, подключившись к `localhost:5434`, БД `crm`.

---

### Шаг 4. Проверить, что CDC данные реально попали в ClickHouse dims
В ClickHouse:

```sql
USE reports;

SELECT * FROM dim_customers WHERE email = 'user1@example.com' LIMIT 5;
SELECT * FROM dim_prosthetics LIMIT 5;
```


Если `user1@example.com` в `dim_customers` есть — CDC часть работает.

---

### Шаг 5. Запустить DAG2 (генерация телеметрии) через UI Airflow
1) Откройте Airflow UI: `http://localhost:8081`
2) Логин: `admin`, пароль: `admin`
3) Найдите DAG **`reports_etl_cdc`**
4) Включите (unpause)
5) Нажмите **Trigger DAG** и дождитесь, пока задачи станут зелёными.

Что делает DAG:
- создаёт (если нет) `telemetry_events`
- читает пользователей из `dim_customers`
- пишет события телеметрии в `telemetry_events`

---

### Шаг 6. Проверить, что витрина `report_mart_cdc` наполнилась
В ClickHouse:

```sql
USE reports;

SELECT count() FROM telemetry_events WHERE user_sub = 'user1@example.com';

SELECT *
FROM report_mart_cdc
WHERE user_key = 'user1@example.com'
ORDER BY mart_updated_at DESC
LIMIT 1;
```


Если есть строка в `report_mart_cdc` — API больше не должен отдавать “не найдено”.

---

### Шаг 7. Скачать отчёт в UI
1) Откройте фронтенд: `http://localhost:3000`
2) Войдите под пользователем `user1` (пароль тот, который у вас задан в realm)
3) Нажмите кнопку скачивания отчёта

Ожидаемый результат:
- API найдёт строку в `report_mart_cdc`
- Сгенерирует JSON и положит в MinIO bucket `reports`
- Вернёт `cdnUrl`, по которому файл доступен через `http://localhost:8082/...`

---
