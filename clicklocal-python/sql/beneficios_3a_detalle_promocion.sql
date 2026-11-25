begin;

alter table public.beneficios
  add column if not exists detalle_promocion text;

do $bloque$
begin
  if not exists (
    select 1
    from pg_constraint
    where conrelid = 'public.beneficios'::regclass
      and conname = 'beneficios_detalle_promocion_longitud_check'
  ) then
    alter table public.beneficios
      add constraint beneficios_detalle_promocion_longitud_check
      check (
        detalle_promocion is null
        or char_length(detalle_promocion) between 1 and 200
      );
  end if;
end
$bloque$;

comment on column public.beneficios.detalle_promocion is
  'Texto plano opcional, visible en el modal público. Máximo 200 caracteres.';

commit;
