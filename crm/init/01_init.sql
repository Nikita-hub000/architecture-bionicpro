ALTER ROLE crm_user WITH REPLICATION;

CREATE TABLE IF NOT EXISTS public.customers (
                                                id uuid PRIMARY KEY,
                                                email text NOT NULL,
                                                first_name text,
                                                last_name text,
                                                updated_at timestamptz DEFAULT now()
    );

CREATE TABLE IF NOT EXISTS public.prosthetics (
                                                  id uuid PRIMARY KEY,
                                                  customer_id uuid NOT NULL REFERENCES public.customers(id),
    model text,
    updated_at timestamptz DEFAULT now()
    );

INSERT INTO public.customers (id, email, first_name, last_name)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'user1@example.com', 'User', 'One')
    ON CONFLICT (id) DO NOTHING;

INSERT INTO public.prosthetics (id, customer_id, model)
VALUES
    ('00000000-0000-0000-0000-000000000101', '00000000-0000-0000-0000-000000000001', 'P-1001')
    ON CONFLICT (id) DO NOTHING;