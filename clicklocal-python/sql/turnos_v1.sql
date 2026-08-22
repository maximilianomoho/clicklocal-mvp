-- ============================================================
-- CLICKLOCAL TURNOS
-- ESTRUCTURA BASE V1
-- ============================================================
--
-- Módulo transversal.
-- Puede activarse para cualquier comercio de ClickLocal.
--
-- V1:
-- 1. Configuración general
-- 2. Servicios
-- 3. Profesionales
-- 4. Servicios por profesional
-- 5. Horarios habituales
-- 6. Bloqueos puntuales
-- 7. Reservas / turnos
--
-- Este archivo CREA estructura.
-- No inserta turnos.
-- No modifica comercios existentes.
-- ============================================================

begin;


-- ============================================================
-- 1. CONFIGURACION GENERAL
-- ============================================================

create table if not exists public.turnos_configuracion (

    comercio_id uuid primary key
        references public.comercios(id)
        on delete cascade,

    -- Hasta cuántos días hacia adelante se permite reservar.
    dias_anticipacion integer not null default 7
        check (dias_anticipacion >= 1),

    -- Mínimo de minutos entre el momento actual y el turno.
    minutos_anticipacion integer not null default 120
        check (minutos_anticipacion >= 0),

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now()
);


-- ============================================================
-- 2. SERVICIOS
-- ============================================================

create table if not exists public.turnos_servicios (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete cascade,

    nombre text not null,

    duracion_min integer not null
        check (duracion_min > 0),

    -- Cantidad máxima de clientes que pueden reservar
    -- el mismo servicio en el mismo horario.
    -- Ejemplo:
    -- corte = 1
    -- clase grupal = 8
    capacidad_max integer not null default 1
        check (capacidad_max >= 1),

    -- Cada cuántos minutos puede ofrecerse un nuevo inicio.
    -- Es independiente de la duración del servicio.
    -- Ejemplo:
    -- duración = 90 minutos
    -- intervalo de inicio = 5 minutos
    intervalo_inicio_min integer not null default 15
        check (intervalo_inicio_min >= 1),

    precio numeric(12,2)
        check (precio is null or precio >= 0),

    activo boolean not null default true,

    orden integer not null default 0,

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now(),

    constraint turnos_servicios_nombre_no_vacio
        check (btrim(nombre) <> '')
);


create index if not exists
    idx_turnos_servicios_comercio
on public.turnos_servicios (
    comercio_id,
    activo
);


-- ============================================================
-- 3. PROFESIONALES / PERSONAS QUE ATIENDEN
-- ============================================================

create table if not exists public.turnos_profesionales (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete cascade,

    nombre text not null,

    activo boolean not null default true,

    orden integer not null default 0,

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now(),

    constraint turnos_profesionales_nombre_no_vacio
        check (btrim(nombre) <> '')
);


create index if not exists
    idx_turnos_profesionales_comercio
on public.turnos_profesionales (
    comercio_id,
    activo
);


-- ============================================================
-- 4. QUE SERVICIOS PUEDE HACER CADA PROFESIONAL
-- ============================================================

create table if not exists public.turnos_profesional_servicios (

    profesional_id uuid not null
        references public.turnos_profesionales(id)
        on delete cascade,

    servicio_id uuid not null
        references public.turnos_servicios(id)
        on delete cascade,

    primary key (
        profesional_id,
        servicio_id
    )
);


-- ============================================================
-- 5. HORARIOS HABITUALES
-- ============================================================
--
-- dia_semana:
-- 0 = lunes
-- 1 = martes
-- 2 = miércoles
-- 3 = jueves
-- 4 = viernes
-- 5 = sábado
-- 6 = domingo
--
-- Se permiten varias franjas el mismo día.
-- Ejemplo:
-- 09:00 a 13:00
-- 15:00 a 19:00
-- ============================================================

create table if not exists public.turnos_horarios (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete cascade,

    profesional_id uuid not null
        references public.turnos_profesionales(id)
        on delete cascade,

    dia_semana smallint not null
        check (dia_semana between 0 and 6),

    hora_desde time not null,

    hora_hasta time not null,

    activo boolean not null default true,

    created_at timestamptz not null default now(),

    constraint turnos_horarios_rango_valido
        check (hora_hasta > hora_desde)
);


create index if not exists
    idx_turnos_horarios_profesional_dia
on public.turnos_horarios (
    profesional_id,
    dia_semana,
    activo
);


-- ============================================================
-- 6. BLOQUEOS / EXCEPCIONES PUNTUALES
-- ============================================================
--
-- profesional_id NULL:
-- bloquea al comercio completo.
--
-- hora_desde y hora_hasta NULL:
-- bloquea todo el día.
--
-- Con ambas horas:
-- bloquea solamente esa franja.
-- ============================================================

create table if not exists public.turnos_bloqueos (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete cascade,

    profesional_id uuid
        references public.turnos_profesionales(id)
        on delete cascade,

    fecha date not null,

    hora_desde time,

    hora_hasta time,

    motivo text,

    created_at timestamptz not null default now(),

    constraint turnos_bloqueos_horas_check
        check (
            (
                hora_desde is null
                and hora_hasta is null
            )
            or
            (
                hora_desde is not null
                and hora_hasta is not null
                and hora_hasta > hora_desde
            )
        )
);


create index if not exists
    idx_turnos_bloqueos_comercio_fecha
on public.turnos_bloqueos (
    comercio_id,
    fecha
);


-- ============================================================
-- 7. TURNOS / RESERVAS
-- ============================================================

create table if not exists public.turnos_reservas (

    id uuid primary key default gen_random_uuid(),

    comercio_id uuid not null
        references public.comercios(id)
        on delete restrict,

    servicio_id uuid not null
        references public.turnos_servicios(id)
        on delete restrict,

    profesional_id uuid not null
        references public.turnos_profesionales(id)
        on delete restrict,

    fecha date not null,

    hora_inicio time not null,

    hora_fin time not null,

    cliente_nombre text not null,

    cliente_whatsapp text not null,

    cliente_direccion text,

    observacion text,

    estado text not null default 'pendiente'
        check (
            estado in (
                'pendiente',
                'confirmado',
                'rechazado',
                'atendido',
                'no_vino'
            )
        ),

    created_at timestamptz not null default now(),

    updated_at timestamptz not null default now(),

    constraint turnos_reservas_horas_validas
        check (hora_fin > hora_inicio),

    constraint turnos_reservas_cliente_no_vacio
        check (btrim(cliente_nombre) <> ''),

    constraint turnos_reservas_whatsapp_no_vacio
        check (btrim(cliente_whatsapp) <> '')
);


create index if not exists
    idx_turnos_reservas_comercio_fecha
on public.turnos_reservas (
    comercio_id,
    fecha,
    hora_inicio
);


create index if not exists
    idx_turnos_reservas_profesional_fecha
on public.turnos_reservas (
    profesional_id,
    fecha,
    hora_inicio
);


-- ============================================================
-- 8. SEGURIDAD
-- ============================================================

alter table public.turnos_configuracion
    enable row level security;

alter table public.turnos_servicios
    enable row level security;

alter table public.turnos_profesionales
    enable row level security;

alter table public.turnos_profesional_servicios
    enable row level security;

alter table public.turnos_horarios
    enable row level security;

alter table public.turnos_bloqueos
    enable row level security;

alter table public.turnos_reservas
    enable row level security;

-- Sin políticas públicas.
-- Flask operará mediante supabase_admin.


commit;
