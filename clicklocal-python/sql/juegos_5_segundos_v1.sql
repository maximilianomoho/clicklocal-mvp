-- ClickJuegos: alta idempotente de "5 segundos".
-- Ejecutar manualmente en Supabase SQL Editor.
-- El score es el error absoluto respecto de 5.000 ms; menor es mejor.

begin;

insert into public.juegos (
    slug,
    nombre,
    activo,
    ranking_direction,
    score_unit,
    score_min,
    score_max
)
values (
    '5-segundos',
    '5 segundos',
    true,
    'lower',
    'ms',
    0,
    10000
)
on conflict (slug) do update
set nombre = excluded.nombre,
    activo = excluded.activo,
    ranking_direction = excluded.ranking_direction,
    score_unit = excluded.score_unit,
    score_min = excluded.score_min,
    score_max = excluded.score_max;

commit;
