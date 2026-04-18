-- crm_cdc_emit.sql
-- Цель: сгенерировать CDC-события Debezium по таблицам public.customers и public.prosthetics
-- Запуск: psql -h localhost -p 5434 -U crm_user -d crm -f crm_cdc_emit.sql

BEGIN;

-- 1) "Дёргаем" существующего user1: UPDATE создаст событие op='u'
UPDATE public.customers
SET
    first_name = first_name,      -- значение не меняем
    last_name  = last_name,       -- значение не меняем
    updated_at = now()
WHERE email = 'user1@example.com';

-- 2) Идемпотентно создаём тестового клиента (INSERT -> op='c', если ещё нет)
INSERT INTO public.customers (id, email, first_name, last_name, updated_at)
VALUES (
           '10000000-0000-0000-0000-000000000001'::uuid,
           'cdc_test_user@example.com',
           'CDC',
           'Test',
           now()
       )
    ON CONFLICT (id) DO UPDATE
                            SET
                                email      = EXCLUDED.email,
                            first_name = EXCLUDED.first_name,
                            last_name  = EXCLUDED.last_name,
                            updated_at = now();

-- 3) Идемпотентно создаём протез для тестового клиента (INSERT/UPDATE -> CDC в prosthetics)
INSERT INTO public.prosthetics (id, customer_id, model, updated_at)
VALUES (
           '20000000-0000-0000-0000-000000000001'::uuid,
           '10000000-0000-0000-0000-000000000001'::uuid,
           'P-CDC-1',
           now()
       )
    ON CONFLICT (id) DO UPDATE
                            SET
                                customer_id = EXCLUDED.customer_id,
                            model       = EXCLUDED.model,
                            updated_at  = now();

-- 4) Дополнительно можно "пнуть" протез user1, если он есть: UPDATE -> op='u'
UPDATE public.prosthetics
SET
    model = model,
    updated_at = now()
WHERE customer_id = '00000000-0000-0000-0000-000000000001'::uuid;

COMMIT;

-- Быстрая проверка в CRM:
SELECT id, email, first_name, last_name, updated_at
FROM public.customers
WHERE email IN ('user1@example.com', 'cdc_test_user@example.com')
ORDER BY email;

SELECT id, customer_id, model, updated_at
FROM public.prosthetics
WHERE customer_id IN (
                      '00000000-0000-0000-0000-000000000001'::uuid,
                      '10000000-0000-0000-0000-000000000001'::uuid
    )
ORDER BY customer_id, id;