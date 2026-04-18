## Что реализовано
### Архитектура (Задача 1)
- Добавлена схема решения в `Task2/Task2.drawio.xml` (+ экспорт `Task2/Task2.png`).
- Поток данных: **CRM (CSV)** + (демо) телеметрия → **Airflow ETL** → **ClickHouse витрина** → **reports-api** → **bionicpro-auth (BFF)** → **frontend**.

### ETL на Apache Airflow (Задача 2)
- Добавлен модуль `airflow/`:
    - DAG `reports_etl` по расписанию (`*/15 * * * *`).
    - Шаги DAG:
        1) `init_schema` — создаёт таблицы в ClickHouse
        2) `load_crm_csv` — загружает CRM из `airflow/data/crm.csv` в staging (`crm_users`)
        3) `generate_mock_telemetry` — генерирует телеметрию (заглушка вместо DB датчиков)
        4) `build_mart_and_watermark` — агрегирует в витрину (`report_mart`) и обновляет `etl_watermark`.

### OLAP ClickHouse (витрина)
- В `docker-compose.yaml` добавлен **ClickHouse**.
- Таблицы:
    - `crm_users` (staging CRM)
    - `telemetry_events` (staging телеметрия)
    - `report_mart` (витрина отчётов для быстрого чтения)
    - `etl_watermark` (метка “до какого времени данные готовы”)

### API отчётов (Задача 3)
- `reports-api` доработан: `GET /reports` читает отчёт **из ClickHouse витрины**, без тяжёлых вычислений в рантайме.

### Ограничение доступа (Задача 4)
- `reports-api` формирует ключ пользователя **из токена** (claim), а не принимает userId извне → отчёт доступен только “для себя”.
- Витрина привязана к ключу пользователя (в учебной версии — через email/ключ из токена, чтобы совпасть с CRM CSV).

### Контроль “период обработан ETL”
- `reports-api` проверяет наличие/актуальность `etl_watermark` и не отдаёт отчёт, если витрина ещё не подготовлена (возвращает ошибку вместо “пустых данных”).

### UI (Задача 5)
- Во фронтенде реализована кнопка получения отчёта (вызов `GET /api/reports` на BFF).
- Полученные данные отчёта отображаются на странице в JSON-формате.