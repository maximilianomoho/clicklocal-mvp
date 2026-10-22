begin;

alter table public.gastronomia_pedidos
    add column if not exists confirmado_at timestamptz;

commit;
