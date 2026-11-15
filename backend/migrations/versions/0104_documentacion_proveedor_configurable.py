"""RF50 configurable (respuestas del propietario del 15-11-2026 a P1-P5 de `0103`). Diseño:
`doc/diseno/RF50_Configurable.md` del repositorio CEM, con la lista de parámetros para la futura
aplicación de instalación y mantenimiento.

Palabras del propietario: «Todos estos comportamientos deben ser configurables mediante variables
según lo desee el consejo (cliente) en su instalación del producto». Por eso cada valor vive en la
base, por consorcio, con un valor por defecto; hoy lo cambia el equipo con
`scripts/configurar_proveedores.py` (como las monedas de `0100`) y no hay pantalla en CEM.

**La configuración** (`nucleo.configuracion_proveedores`). Una fila por cambio, que no se modifica
ni se borra; vale la última del consorcio. Sin filas valen los valores por defecto
(`nucleo.configuracion_proveedores_vigente`):

- `dias_por_vencer` (`DIAS_POR_VENCER`, 30; P3): cuántos días antes del vencimiento se marca «por
  vencer»;
- `modo_exigencia` (`MODO_EXIGENCIA`, `bloquear`; P4): qué hace la documentación que exige la ley
  cuando falta o venció. `avisar` la marca y nada más; `bloquear` además niega **preparar o
  modificar una orden nueva** a ese proveedor. Nunca toca una orden que ya existe: aprobar, pagar
  y conciliar siguen igual;
- `admite_no_vence` (`ADMITE_NO_VENCE`, sí; P5): si una constancia se puede cargar con «No vence»
  y la fecha en blanco;
- `roles_que_ven` (`ROLES_QUE_VEN`; P1 y P2): los roles que consultan la ficha del proveedor y sus
  seguros (`proveedores.ver`): tipos de mandato (`administrador`, `administrador_externo`,
  `miembro_consejo`) y de vínculo (`propietario`, `inquilino`, `ocupante`). Por defecto, todo el
  Consejo y la administración. «Habitantes autorizados» se agrega por tipo de vínculo; dar el
  permiso a una persona sola queda como pregunta abierta;
- `requisitos_agregados` y `requisitos_omitidos`: qué filas de la matriz legal exige este
  consorcio además o en lugar de las verificadas.

**La matriz legal** (`nucleo.requisito_legal`, global). Cada fila es un requisito que la ley pide
a un rubro (`rubro_legal`): su tipo (`seguro` o `habilitacion`), qué es, la fuente y si la fuente
está verificada. Se exigen las verificadas, más las agregadas y menos las omitidas por el
consorcio. Las que dicen «SIN VERIFICAR» no se exigen salvo que el consorcio las agregue. Hoy la
cambia una migración; la futura aplicación de mantenimiento la va a editar.

**Los rubros del proveedor** (`nucleo.rubro_legal_proveedor`). Quien prepara órdenes le asigna o
le retira un rubro legal al proveedor (`asignar_rubro_legal_proveedor`, `egresos.preparar_orden`
sin el gesto). Cada cambio es una fila que no se borra; vale la última por rubro.

**La constancia** suma `requisito_codigo` (qué requisito cubre, opcional) y `no_vence`. La base
exige una fecha o «No vence», nunca las dos ni ninguna (`vencimiento_definido`). Una constancia
«No vence» vale siempre: su estado es `vigente` mientras no se reemplace.

**Lo que falta** (`nucleo.requisitos_faltantes`): cada requisito exigido de los rubros del
proveedor sin una constancia de ese requisito vigente o por vencer. La barrera
(`exigir_documentacion_legal`, `before insert` en `nucleo.orden_pago_version`) niega con
`documentacion_legal_faltante` la versión 1 de una orden, o una versión que cambia de proveedor,
si el modo es `bloquear` y al proveedor le falta algo.

**Quién lo ve.** `proveedores.ver` (`exigir_lectura_proveedores`): la persona tiene, hoy, un
mandato o un vínculo de un tipo que está en `roles_que_ven`, y la sesión vale; no pide la
identidad humana ni el modo de egresos: es solo leer. La API repite la regla con la concesión por
configuración del evaluador (`nucleo.roles_configurados`). `proveedores_consulta` no devuelve la
cuenta ni su huella. Las lecturas de documentación, rubros y faltantes aceptan también las de
egresos.

**Exportación (`0069`):** `exportar_proveedores` suma `rubro_legal_proveedor` y
`configuracion_proveedores`, y la constancia suma `no_vence` y `requisito_codigo`.

Downgrade: se niega (`downgrade_con_datos`) si hay configuración, rubros asignados o alguna
constancia con «No vence» o con requisito.

Revisión: 0104
Anterior: 0103
"""

from alembic import op
from sqlalchemy import text

revision = "0104"
down_revision = "0103"
branch_labels = None
depends_on = None

DIAS_POR_VENCER = 30
MODO_EXIGENCIA = "bloquear"
MODOS_EXIGENCIA = ("avisar", "bloquear")
ADMITE_NO_VENCE = True
ROLES_QUE_VEN = ("administrador", "administrador_externo", "miembro_consejo")
"""P1: todo el Consejo y la administración."""
ROLES_POSIBLES = (
    "administrador",
    "administrador_externo",
    "miembro_consejo",
    "propietario",
    "inquilino",
    "ocupante",
)
"""Los tipos de `nucleo.tipo_mandato` y de `nucleo.tipo_vinculo`."""
ACCION_VER = "proveedores.ver"
ACCION_AUDITADA = "consorcio.proveedor.rubro_legal"

REQUISITOS: tuple[tuple[str, str, str, str, str, bool], ...] = (
    (
        "general.seguro_rc",
        "general",
        "seguro",
        (
            "Seguro de responsabilidad civil del prestador; si es autónomo, endosado a favor del "
            "consorcio"
        ),
        (
            "Ley 941 (CABA), art. 11 inc. g y art. 15 inc. b; texto consolidado en "
            "https://juristeca.jusbaires.gob.ar/?p=6723"
        ),
        True,
    ),
    (
        "con_personal.art",
        "con_personal",
        "seguro",
        ("Afiliación a una ART del personal en relación de dependencia (o autoseguro)"),
        (
            "Ley 24.557, arts. 2 y 3; Ley 941 (CABA), art. 11 inc. g; "
            "https://www.argentina.gob.ar/normativa/nacional/ley-24557-27971/actualizacion"
        ),
        True,
    ),
    (
        "ascensores.permiso_conservador",
        "ascensores",
        "habilitacion",
        (
            "Permiso de conservador en el Registro de Empresas Conservadoras de Elevadores (AGC), "
            "anual"
        ),
        (
            "Código de Edificación (Ley 6100, mod. Ley 6438), art. 5.1.5; RT-050105-020601-05 v2 "
            "(IF-2021-33941298-GCABA-DGFYCO); "
            "https://buenosaires.gob.ar/tramites/registro-de-empresas-conservadoras-de-elevadores,"
            " punto 3.1"
        ),
        True,
    ),
    (
        "ascensores.seguro_rc",
        "ascensores",
        "seguro",
        (
            "Responsabilidad civil de la empresa conservadora, mínimo 350.000 UF y vigencia de al "
            "menos 1 año"
        ),
        (
            "Código de Edificación (Ley 6100, mod. Ley 6438), art. 5.1.5; RT-050105-020601-05 v2 "
            "(IF-2021-33941298-GCABA-DGFYCO); "
            "https://buenosaires.gob.ar/tramites/registro-de-empresas-conservadoras-de-elevadores,"
            " punto 3.1 in fine"
        ),
        True,
    ),
    (
        "ascensores.representante_tecnico",
        "ascensores",
        "habilitacion",
        ("Representante técnico matriculado con incumbencia"),
        (
            "Código de Edificación (Ley 6100, mod. Ley 6438), art. 5.1.5; RT-050105-020601-05 v2 "
            "(IF-2021-33941298-GCABA-DGFYCO); "
            "https://buenosaires.gob.ar/tramites/registro-de-empresas-conservadoras-de-elevadores,"
            " puntos 3.1 j y 4"
        ),
        True,
    ),
    (
        "incendio.registro_rifci",
        "incendio",
        "habilitacion",
        (
            "Inscripción en el registro de instaladores y mantenedores de instalaciones fijas "
            "contra incendio (RIFCI), anual"
        ),
        (
            "Res. 405-AGC-19; RT IF-2022-06441323-GCABA-DGHP; https://buenosaires.gob.ar/tramites/"
            "inscripcion-en-el-registro-de-fabricantes-reparadores-e-instaladores-de"
        ),
        True,
    ),
    (
        "incendio.seguro_rc",
        "incendio",
        "seguro",
        ("Responsabilidad civil contra terceros por la actividad, vigente y pagada"),
        (
            "Res. 405-AGC-19; RT IF-2022-06441323-GCABA-DGHP; https://buenosaires.gob.ar/tramites/"
            "inscripcion-en-el-registro-de-fabricantes-reparadores-e-instaladores-de"
        ),
        True,
    ),
    (
        "incendio.director_tecnico",
        "incendio",
        "habilitacion",
        ("Director técnico matriculado que firma el anexo técnico"),
        (
            "Res. 405-AGC-19; RT IF-2022-06441323-GCABA-DGHP; https://buenosaires.gob.ar/tramites/"
            "inscripcion-en-el-registro-de-fabricantes-reparadores-e-instaladores-de"
        ),
        True,
    ),
    (
        "matafuegos.registro_reeci",
        "matafuegos",
        "habilitacion",
        (
            "Inscripción en el registro de recargadores e instaladores de extintores (R.E.E.C.I., "
            "AGC), anual"
        ),
        (
            "Código de Edificación (Leyes 6100 y 6438); RT-050107-02601-09 v04; Disposición "
            "Conjunta 48/DGFYCO/26, arts. 1 y 2 (BOCBA 7333); https://documentosboletinoficial.bue"
            "nosaires.gob.ar/publico/ck_PE-DIS-MJGC-MJGC-DGFYCO-48-26-7333.pdf"
        ),
        True,
    ),
    (
        "matafuegos.seguro_rc",
        "matafuegos",
        "seguro",
        ("Responsabilidad civil por la actividad, vigente y pagada"),
        (
            "SIN VERIFICAR: solo el resumen de https://buenosaires.gob.ar/tramites/inscripcion-en-"
            "el-registro-de-fabricantes-reparadores-y-recargadores-de-extintores (la página no "
            "abrió)"
        ),
        False,
    ),
    (
        "gas.matricula_gasista",
        "gas",
        "habilitacion",
        ("Instalador matriculado por la distribuidora de la zona (Metrogas o Naturgy)"),
        (
            "Res. ENARGAS I/902/09; Res. ENARGAS 144/2024, art. 2; NAG-200; "
            "https://www.boletinoficial.gob.ar/detalleAviso/primera/305929/20240417 (periodicidad "
            "SIN VERIFICAR)"
        ),
        True,
    ),
    (
        "electricidad.matricula",
        "electricidad",
        "habilitacion",
        ("Electricista matriculado"),
        (
            "SIN VERIFICAR: no se halló norma de CABA que la exija para todo trabajo en propiedad "
            "horizontal; Ley 941, art. 11 inc. a («cuando la normativa lo exija»)"
        ),
        False,
    ),
    (
        "desratizacion.nomina_apra",
        "desratizacion",
        "habilitacion",
        (
            "Inscripción en la Nómina de empresas de desratización (APRA), con responsable técnico"
            " matriculado; cada 2 años"
        ),
        (
            "Res. 281/APRA/2026, arts. 1, 4, 5 y 6 (BOCBA 17-09-2026); "
            "https://boletinoficial.buenosaires.gob.ar/normativaba/norma/877383"
        ),
        True,
    ),
    (
        "fachadas.profesional_matriculado",
        "fachadas",
        "habilitacion",
        (
            "Profesional matriculado con incumbencia que firma el informe y el certificado de "
            "conservación de fachada"
        ),
        (
            "Código de Edificación, art. 5.1.2; RT-020601-01 v1; https://buenosaires.gob.ar/sites/"
            "default/files/2026-07/RT-020601-01-V1-CONSERVACION%20DE%20FACHADAS_0.pdf"
        ),
        True,
    ),
    (
        "fachadas.art_programa_seguridad",
        "fachadas",
        "seguro",
        ("ART del personal con aviso de obra y programa de seguridad aprobado"),
        (
            "SIN VERIFICAR: Res. SRT 51/97, 35/98 y 319/99; Decreto 911/96 (texto vigente no "
            "confirmado en fuente oficial)"
        ),
        False,
    ),
    (
        "calderas.certificador_matriculado",
        "calderas",
        "habilitacion",
        ("Profesional certificador matriculado del Registro de Instalaciones Térmicas (AGC)"),
        (
            "SIN VERIFICAR la norma y la periodicidad; "
            "https://buenosaires.gob.ar/gcaba_historico/agc/registro-de-instalaciones"
        ),
        False,
    ),
)
"""La matriz legal inicial (CABA; investigación del 15-11-2026 con la fuente de cada fila). Cada
fila: (codigo, rubro_legal, tipo, descripcion, fuente, verificado). Las «SIN VERIFICAR» no se
exigen salvo que el consorcio las agregue. Lo que la ley pide al consorcio y no al proveedor
(oblea de ascensores, plan de tanques, registro IFCI) no va acá. Desinfección y tanques no tienen
registro vigente en CABA (Ley 6136, art. 19; Res. 35/APRA/2019; Ley 6040): no hay fila."""

TABLA_CONFIG = "nucleo.configuracion_proveedores"
TABLA_REQUISITO = "nucleo.requisito_legal"
TABLA_RUBRO = "nucleo.rubro_legal_proveedor"
TABLA_CONSTANCIA = "nucleo.documentacion_proveedor"

CONFIG_VIGENTE = "nucleo.configuracion_proveedores_vigente(uuid)"
ROLES_CONFIGURADOS = "nucleo.roles_configurados(uuid)"
CONFIGURAR = (
    "nucleo.configurar_proveedores(integer, text, boolean, text[], text[], text[], uuid, text)"
)
EXIGIDOS = "nucleo.requisitos_exigidos(uuid)"
RUBROS_DE = "nucleo.rubros_legales_de(uuid, uuid)"
FALTANTES = "nucleo.requisitos_faltantes(uuid, uuid)"
EXIGIR_LEGAL = "nucleo.exigir_documentacion_legal()"
PUEDE_VER = "nucleo.puede_ver_proveedores(uuid, uuid)"
EXIGIR_LECTURA = "nucleo.exigir_lectura_proveedores(uuid, text, uuid, text, uuid)"
ASIGNAR = "nucleo.asignar_rubro_legal_proveedor(uuid, text, boolean, uuid, text, uuid)"
CONSULTA = "nucleo.proveedores_consulta(uuid, uuid, text, uuid)"
RUBROS_LECTURA = "nucleo.rubros_legales_lectura(uuid, text, uuid, text, uuid)"
FALTANTES_LECTURA = "nucleo.requisitos_faltantes_lectura(uuid, text, uuid, text, uuid)"
REQUISITOS_LECTURA = "nucleo.requisitos_legales_lectura(text, uuid, text, uuid)"
CONFIG_LECTURA = "nucleo.configuracion_proveedores_lectura(text, uuid, text, uuid)"
CONFIG_SOPORTE = "nucleo.configuracion_proveedores_soporte()"

CARGAR_ANTES = (
    "nucleo.cargar_documentacion_proveedor(uuid, text, text, date, uuid, text, text, integer, text,"
    " uuid, text, uuid)"
)
CARGAR = (
    "nucleo.cargar_documentacion_proveedor(uuid, text, text, date, uuid, text, text, integer, text,"
    " uuid, text, uuid, text, boolean)"
)
LEER = "nucleo.documentacion_proveedor_lectura(uuid, text, uuid, text, uuid)"
ESTADO = "nucleo.estado_documentacion_proveedor(uuid, uuid)"
EXPORTAR_PROVEEDORES = "nucleo.exportar_proveedores(uuid, uuid)"

RUTAS = (
    ROLES_CONFIGURADOS,
    CONFIGURAR,
    ASIGNAR,
    CONSULTA,
    RUBROS_LECTURA,
    FALTANTES_LECTURA,
    REQUISITOS_LECTURA,
    CONFIG_LECTURA,
    CONFIG_SOPORTE,
)
"""Las que ejecuta `cem_app`: las rutas, `roles_configurados` (el contexto de cada pedido) y la
configuración de soporte, que corre como `cem_app` sin persona (como
`habilitar_moneda_consorcio`)."""
INTERNAS = (CONFIG_VIGENTE, EXIGIDOS, RUBROS_DE, FALTANTES, EXIGIR_LEGAL, PUEDE_VER, EXIGIR_LECTURA)

_lista = lambda valores: ", ".join(f"'{v}'" for v in valores)  # noqa: E731
_roles_posibles = _lista(ROLES_POSIBLES)
_roles_defecto = _lista(ROLES_QUE_VEN)
_modos = _lista(MODOS_EXIGENCIA)

TABLAS = f"""
create table {TABLA_REQUISITO} (
  codigo       text primary key check (codigo ~ '^[a-z_]+\\.[a-z_]+$'),
  rubro_legal  text not null check (rubro_legal ~ '^[a-z_]+$'),
  tipo         text not null check (tipo in ('seguro', 'habilitacion')),
  descripcion  text not null check (length(btrim(descripcion)) between 1 and 200),
  fuente       text not null check (length(btrim(fuente)) > 0),
  verificado   boolean not null,
  check (split_part(codigo, '.', 1) = rubro_legal)
);
revoke all on {TABLA_REQUISITO} from public, cem_app;

create table {TABLA_CONFIG} (
  consorcio_id          uuid not null references nucleo.consorcio (id),
  numero                bigint generated always as identity primary key,
  dias_por_vencer       smallint not null check (dias_por_vencer between 1 and 365),
  modo_exigencia        text not null check (modo_exigencia in ({_modos})),
  admite_no_vence       boolean not null,
  roles_que_ven         text[] not null
    check (roles_que_ven <@ array[{_roles_posibles}]::text[]),
  requisitos_agregados  text[] not null,
  requisitos_omitidos   text[] not null,
  -- Quién la cambió: el equipo, sin persona. La futura aplicación de mantenimiento sumará el suyo.
  origen                text not null check (origen = 'soporte'),
  registrada_en         timestamptz not null default now()
);
create index configuracion_proveedores_consorcio on {TABLA_CONFIG} (consorcio_id, numero);
alter table {TABLA_CONFIG} enable row level security;
alter table {TABLA_CONFIG} force row level security;
create policy por_consorcio on {TABLA_CONFIG} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA_CONFIG} from public, cem_app;
create trigger historia_configuracion_proveedores before update or delete on {TABLA_CONFIG}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_configuracion_proveedores_truncate before truncate on {TABLA_CONFIG}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

create table {TABLA_RUBRO} (
  consorcio_id    uuid not null,
  numero          bigint generated always as identity primary key,
  proveedor_id    uuid not null,
  rubro_legal     text not null check (rubro_legal ~ '^[a-z_]+$'),
  accion          text not null check (accion in ('asignar', 'retirar')),
  registrado_por  uuid not null references nucleo.persona (id),
  identidad       uuid not null,
  registrado_en   timestamptz not null default now(),
  foreign key (consorcio_id, proveedor_id) references nucleo.proveedor (consorcio_id, id),
  foreign key (consorcio_id, identidad) references nucleo.identidad_humana (consorcio_id, id)
);
create index rubro_legal_proveedor_proveedor
  on {TABLA_RUBRO} (consorcio_id, proveedor_id, rubro_legal, numero);
alter table {TABLA_RUBRO} enable row level security;
alter table {TABLA_RUBRO} force row level security;
create policy por_consorcio on {TABLA_RUBRO} for select
  using (consorcio_id = plataforma.consorcio_actual());
revoke all on {TABLA_RUBRO} from public, cem_app;
create trigger historia_rubro_legal_proveedor before update or delete on {TABLA_RUBRO}
  for each row execute function nucleo.negar_cambio_historia_orden_pago();
create trigger historia_rubro_legal_proveedor_truncate before truncate on {TABLA_RUBRO}
  for each statement execute function nucleo.negar_cambio_historia_orden_pago();

alter table {TABLA_CONSTANCIA}
  alter column vence_el drop not null,
  add column no_vence boolean not null default false,
  add column requisito_codigo text references {TABLA_REQUISITO} (codigo),
  add constraint vencimiento_definido check ((vence_el is null) = no_vence);
"""

FUNCIONES = f"""
-- La configuración que vale hoy en el consorcio: la última fila o los valores por defecto.
create function {CONFIG_VIGENTE}
  returns table (dias_por_vencer integer, modo_exigencia text, admite_no_vence boolean,
                 roles_que_ven text[], requisitos_agregados text[],
                 requisitos_omitidos text[], registrada_en timestamptz)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    (select k.dias_por_vencer::integer, k.modo_exigencia, k.admite_no_vence, k.roles_que_ven,
            k.requisitos_agregados, k.requisitos_omitidos, k.registrada_en
       from {TABLA_CONFIG} k where k.consorcio_id = $1
      order by k.numero desc limit 1)
    union all
    select {DIAS_POR_VENCER}, '{MODO_EXIGENCIA}', {str(ADMITE_NO_VENCE).lower()},
           array[{_roles_defecto}]::text[], '{{}}'::text[], '{{}}'::text[], null::timestamptz
     where not exists (select 1 from {TABLA_CONFIG} k where k.consorcio_id = $1)
  $$;

-- Para el contexto de cada pedido (la concesión por configuración del evaluador): solo los roles.
create function {ROLES_CONFIGURADOS}
  returns table (accion text, roles text[])
  language sql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
    select '{ACCION_VER}'::text, k.roles_que_ven
      from nucleo.configuracion_proveedores_vigente($1) k
     where $1 = plataforma.consorcio_actual()
  $$;

-- Los requisitos que este consorcio exige: los verificados, más los agregados, menos los omitidos.
create function {EXIGIDOS}
  returns setof {TABLA_REQUISITO}
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select r.* from {TABLA_REQUISITO} r, nucleo.configuracion_proveedores_vigente($1) k
     where (r.verificado or r.codigo = any (k.requisitos_agregados))
       and r.codigo <> all (k.requisitos_omitidos)
  $$;

-- Los rubros legales asignados hoy al proveedor: el último cambio de cada rubro es «asignar».
create function {RUBROS_DE}
  returns setof text
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select x.rubro_legal from (
      select distinct on (e.rubro_legal) e.rubro_legal, e.accion
        from {TABLA_RUBRO} e
       where e.consorcio_id = $1 and e.proveedor_id = $2
       order by e.rubro_legal, e.numero desc) x
     where x.accion = 'asignar'
     order by x.rubro_legal
  $$;

-- Lo que le falta: cada requisito exigido de sus rubros sin una constancia de ese requisito que
-- esté vigente o por vencer (una vencida cuenta como faltante).
create function {FALTANTES}
  returns table (codigo text, rubro_legal text, tipo text, descripcion text)
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select r.codigo, r.rubro_legal, r.tipo, r.descripcion
      from nucleo.requisitos_exigidos($1) r
     where r.rubro_legal in (select nucleo.rubros_legales_de($1, $2))
       and not exists (
             select 1 from {TABLA_CONSTANCIA} a
              where a.consorcio_id = $1 and a.proveedor_id = $2
                and a.requisito_codigo = r.codigo
                and nucleo.estado_documentacion_proveedor($1, a.id) in ('vigente', 'por_vencer'))
     order by r.rubro_legal, r.codigo
  $$;

-- La barrera: en modo `bloquear`, una orden nueva (versión 1) o una versión que cambia de
-- proveedor no se prepara si al proveedor le falta documentación que exige la ley.
create function {EXIGIR_LEGAL}
  returns trigger
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    anterior uuid;
  begin
    if new.proveedor_id is null
       or (select k.modo_exigencia
             from nucleo.configuracion_proveedores_vigente(new.consorcio_id) k) <> 'bloquear'
    then
      return new;
    end if;
    if new.numero > 1 then
      select v.proveedor_id into anterior from nucleo.orden_pago_version v
       where v.consorcio_id = new.consorcio_id and v.orden_id = new.orden_id
         and v.numero = new.numero - 1;
      if anterior is not distinct from new.proveedor_id then
        return new;
      end if;
    end if;
    if exists (select 1 from nucleo.requisitos_faltantes(new.consorcio_id, new.proveedor_id)) then
      raise exception 'al proveedor le falta documentación que exige la ley'
        using errcode = 'P0001', hint = 'documentacion_legal_faltante';
    end if;
    return new;
  end
  $$;
create trigger documentacion_legal before insert on nucleo.orden_pago_version
  for each row execute function {EXIGIR_LEGAL};

-- Si la persona tiene hoy un mandato o un vínculo de un tipo que la configuración lista.
create function {PUEDE_VER}
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
             select 1 from nucleo.mandato m
              where m.consorcio_id = $1 and m.persona_id = $2
                and nucleo.mandato_vigente(m.id, nucleo.hoy_del_consorcio($1))
                and m.tipo::text = any (k.roles_que_ven))
        or exists (
             select 1 from nucleo.vinculo v
              where v.consorcio_id = $1 and v.persona_id = $2
                and daterange(v.vigente_desde, v.vigente_hasta) @> nucleo.hoy_del_consorcio($1)
                and v.id not in (select nucleo.vinculos_importados_pendientes())
                and v.tipo::text = any (k.roles_que_ven))
      from nucleo.configuracion_proveedores_vigente($1) k
  $$;

-- Leer proveedores: `proveedores.ver` (por la configuración, sesión válida, sin identidad ni modo
-- de egresos) o las lecturas de egresos.
create function {EXIGIR_LECTURA}
  returns void
  language plpgsql
  set search_path = pg_catalog, pg_temp
  as $$
  begin
    if facultad is distinct from '{ACCION_VER}' then
      perform nucleo.exigir_lectura_egresos(c, facultad, actor, origen_pedido, sesion);
      return;
    end if;
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'api' then
      raise exception 'origen inválido' using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if actor is null or actor is distinct from plataforma.persona_actual()
       or not nucleo.puede_ver_proveedores(c, actor) then
      raise exception 'sin {ACCION_VER} en este consorcio' using errcode = '42501';
    end if;
    if not exists (select 1 from nucleo.sesion x
                    where x.id = sesion and x.persona_id = actor and x.revocada_en is null
                      and x.vence_en > now()) then
      raise exception 'la sesión no es válida' using errcode = 'P0001', hint = 'sesion_invalida';
    end if;
  end
  $$;
"""
# Los parámetros de `exigir_lectura_proveedores` se nombran como los de `exigir_lectura_egresos`.
FUNCIONES = FUNCIONES.replace(
    f"create function {EXIGIR_LECTURA}",
    "create function nucleo.exigir_lectura_proveedores(c uuid, facultad text, actor uuid,"
    " origen_pedido text, sesion uuid)",
)

RUTAS_SQL = f"""
-- El equipo cambia la configuración: corre como `cem_app` sin persona, origen `soporte`. Un valor
-- nulo deja el que vale hoy. Devuelve cuándo quedó registrada.
create function nucleo.configurar_proveedores(dias integer, modo text, no_vence boolean,
                                              roles text[], agregados text[],
                                              omitidos text[], actor uuid, origen_pedido text)
  returns timestamptz
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    k record;
    nueva record;
    cuando timestamptz;
  begin
    if c is null then
      raise exception 'sin consorcio en el ámbito' using errcode = '42501';
    end if;
    if origen_pedido is distinct from 'soporte' then
      raise exception 'la configuración de proveedores la cambia solo el equipo (soporte)'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    if plataforma.persona_actual() is not null or actor is not null then
      raise exception 'el origen soporte no admite persona en la sesión ni actor'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
    select * into k from nucleo.configuracion_proveedores_vigente(c);
    select coalesce(dias, k.dias_por_vencer) as dias, coalesce(modo, k.modo_exigencia) as modo,
           coalesce(no_vence, k.admite_no_vence) as no_vence,
           coalesce((select array_agg(distinct x order by x) from unnest(roles) x),
                    case when roles is null then k.roles_que_ven else '{{}}'::text[] end) as roles,
           coalesce((select array_agg(distinct x order by x) from unnest(agregados) x),
                    case when agregados is null then k.requisitos_agregados
                         else '{{}}'::text[] end) as agregados,
           coalesce((select array_agg(distinct x order by x) from unnest(omitidos) x),
                    case when omitidos is null then k.requisitos_omitidos
                         else '{{}}'::text[] end) as omitidos
      into nueva;
    if nueva.dias not between 1 and 365 then
      raise exception 'los días van de 1 a 365' using errcode = 'P0001', hint = 'dias_invalidos';
    end if;
    if nueva.modo not in ({_modos}) then
      raise exception 'el modo es avisar o bloquear' using errcode = 'P0001',
        hint = 'modo_invalido';
    end if;
    if not nueva.roles <@ array[{_roles_posibles}]::text[] then
      raise exception 'rol desconocido: los roles son %', array[{_roles_posibles}]
        using errcode = 'P0001', hint = 'rol_invalido';
    end if;
    if exists (select 1 from unnest(nueva.agregados || nueva.omitidos) x
                where x not in (select r.codigo from {TABLA_REQUISITO} r)) then
      raise exception 'requisito desconocido' using errcode = 'P0001',
        hint = 'requisito_invalido';
    end if;
    if (nueva.dias, nueva.modo, nueva.no_vence, nueva.roles, nueva.agregados, nueva.omitidos)
       is not distinct from (k.dias_por_vencer, k.modo_exigencia, k.admite_no_vence,
                             k.roles_que_ven, k.requisitos_agregados, k.requisitos_omitidos)
       and k.registrada_en is not null then
      raise exception 'la configuración ya es esa' using errcode = 'P0001',
        hint = 'configuracion_sin_cambios';
    end if;
    insert into {TABLA_CONFIG}
      (consorcio_id, dias_por_vencer, modo_exigencia, admite_no_vence, roles_que_ven,
       requisitos_agregados, requisitos_omitidos, origen)
    values (c, nueva.dias, nueva.modo, nueva.no_vence, nueva.roles, nueva.agregados,
            nueva.omitidos, 'soporte')
    returning registrada_en into cuando;
    return cuando;
  end
  $$;

-- Lo que lee el script del equipo: toda la configuración que vale, sin persona en la sesión.
create function nucleo.configuracion_proveedores_soporte()
  returns table (dias_por_vencer integer, modo_exigencia text, admite_no_vence boolean,
                 roles_que_ven text[], requisitos_agregados text[],
                 requisitos_omitidos text[], registrada_en timestamptz)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    if c is null or plataforma.persona_actual() is not null then
      raise exception 'solo el equipo, con el consorcio en el ámbito y sin persona'
        using errcode = '42501';
    end if;
    return query select * from nucleo.configuracion_proveedores_vigente(c);
  end
  $$;

-- Quien prepara órdenes asigna o retira un rubro legal del proveedor.
create function nucleo.asignar_rubro_legal_proveedor(proveedor uuid, rubro text,
                                                     asignar boolean, actor uuid,
                                                     origen_pedido text, sesion uuid)
  returns bigint
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    identidad uuid;
    tiene boolean;
    numero_nuevo bigint;
  begin
    identidad := nucleo.exigir_acto_sin_gesto(c, actor, sesion, 'egresos.preparar_orden',
                                              origen_pedido);
    perform 1 from nucleo.proveedor p where p.consorcio_id = c and p.id = proveedor for update;
    if not found then
      raise exception 'el proveedor no existe en este consorcio' using errcode = 'P0001',
        hint = 'proveedor_inexistente';
    end if;
    if rubro is null
       or not exists (select 1 from {TABLA_REQUISITO} r where r.rubro_legal = rubro) then
      raise exception 'rubro legal desconocido' using errcode = 'P0001',
        hint = 'rubro_legal_invalido';
    end if;
    tiene := rubro in (select nucleo.rubros_legales_de(c, proveedor));
    if asignar is null or tiene = asignar then
      raise exception 'el rubro ya está así' using errcode = 'P0001',
        hint = 'rubro_legal_sin_cambios';
    end if;
    insert into {TABLA_RUBRO}
      (consorcio_id, proveedor_id, rubro_legal, accion, registrado_por, identidad)
    values (c, proveedor, rubro, case when asignar then 'asignar' else 'retirar' end, actor,
            identidad)
    returning numero into numero_nuevo;
    perform nucleo.auditar_proveedor(c, '{ACCION_AUDITADA}', proveedor, actor,
      jsonb_build_object('rubro_legal', rubro, 'asignado', tiene),
      jsonb_build_object('rubro_legal', rubro, 'asignado', asignar));
    return numero_nuevo;
  end
  $$;

-- La ficha del proveedor para `proveedores.ver`: sin la cuenta, su huella ni el documento.
create function nucleo.proveedores_consulta(proveedor uuid, actor uuid, origen_pedido text,
                                            sesion uuid)
  returns table (id uuid, cuit text, razon_social text, rubro text, contacto text,
                 telefono text, whatsapp text, correo text, domicilio text,
                 atiende_emergencias boolean, estado text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, '{ACCION_VER}', actor, origen_pedido, sesion);
    return query
      select p.id, p.cuit, pv.razon_social, pv.rubro, pv.contacto, pv.telefono, pv.whatsapp,
             pv.correo, pv.domicilio, pv.atiende_emergencias, pv.estado::text
        from nucleo.proveedor p
        join nucleo.proveedor_version pv
          on pv.consorcio_id = p.consorcio_id and pv.proveedor_id = p.id
         and pv.numero = (select max(x.numero) from nucleo.proveedor_version x
                           where x.consorcio_id = p.consorcio_id and x.proveedor_id = p.id)
       where p.consorcio_id = c and (proveedor is null or p.id = proveedor)
       order by pv.razon_social, p.id;
  end
  $$;

create function nucleo.rubros_legales_lectura(proveedor uuid, facultad text, actor uuid,
                                              origen_pedido text, sesion uuid)
  returns table (proveedor_id uuid, rubro_legal text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return query
      select p.id, r.r
        from nucleo.proveedor p
        cross join lateral nucleo.rubros_legales_de(c, p.id) as r(r)
       where p.consorcio_id = c and (proveedor is null or p.id = proveedor)
       order by p.id, r.r;
  end
  $$;

create function nucleo.requisitos_faltantes_lectura(proveedor uuid, facultad text, actor uuid,
                                                    origen_pedido text, sesion uuid)
  returns table (proveedor_id uuid, codigo text, rubro_legal text, tipo text,
                 descripcion text)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return query
      select p.id, f.codigo, f.rubro_legal, f.tipo, f.descripcion
        from nucleo.proveedor p
        cross join lateral nucleo.requisitos_faltantes(c, p.id) f
       where p.consorcio_id = c and (proveedor is null or p.id = proveedor)
       order by p.id, f.rubro_legal, f.codigo;
  end
  $$;

-- La matriz, con lo que este consorcio exige.
create function nucleo.requisitos_legales_lectura(facultad text, actor uuid,
                                                  origen_pedido text, sesion uuid)
  returns table (codigo text, rubro_legal text, tipo text, descripcion text, fuente text,
                 verificado boolean, exigido boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return query
      select r.codigo, r.rubro_legal, r.tipo, r.descripcion, r.fuente, r.verificado,
             r.codigo in (select e.codigo from nucleo.requisitos_exigidos(c) e)
        from {TABLA_REQUISITO} r
       order by r.rubro_legal, r.codigo;
  end
  $$;

-- Lo que la pantalla necesita de la configuración (los roles no: los decide el contexto).
create function nucleo.configuracion_proveedores_lectura(facultad text, actor uuid,
                                                         origen_pedido text, sesion uuid)
  returns table (dias_por_vencer integer, modo_exigencia text, admite_no_vence boolean)
  language plpgsql volatile security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
  begin
    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido, sesion);
    return query
      select k.dias_por_vencer, k.modo_exigencia, k.admite_no_vence
        from nucleo.configuracion_proveedores_vigente(c) k;
  end
  $$;
"""

ESTADO_CAMBIOS = (
    ("             when a.vence_el < nucleo.hoy_del_consorcio(c) then 'vencida'",
     "             when a.no_vence then 'vigente'\n"
     "             when a.vence_el < nucleo.hoy_del_consorcio(c) then 'vencida'"),
    ("a.vence_el <= nucleo.hoy_del_consorcio(c) + 30 then",
     "a.vence_el <= nucleo.hoy_del_consorcio(c)\n"
     "                             + (select k.dias_por_vencer\n"
     "                                  from nucleo.configuracion_proveedores_vigente(c) k) then"),
)  # fmt: skip
CARGAR_CAMBIOS = (
    ("sesion uuid)\n RETURNS TABLE(nueva_version_id",
     "sesion uuid, requisito text, no_vence_nuevo boolean)\n RETURNS TABLE(nueva_version_id"),
    ("    if reemplaza is not null then\n      select * into anterior",
     "    -- 0104: una fecha o «No vence», nunca las dos ni ninguna; «No vence» si la\n"
     "    -- configuración lo admite; el requisito, de la matriz y del mismo tipo.\n"
     "    if no_vence_nuevo is null or (no_vence_nuevo = (vence is not null)) then\n"
     "      raise exception 'hace falta la fecha de vencimiento o «No vence», nunca las dos'\n"
     "        using errcode = 'P0001', hint = 'vencimiento_invalido';\n"
     "    end if;\n"
     "    if no_vence_nuevo and not (select k.admite_no_vence\n"
     "                                 from nucleo.configuracion_proveedores_vigente(c) k) then\n"
     "      raise exception 'este consorcio no admite «No vence»' using errcode = 'P0001',\n"
     "        hint = 'no_vence_no_admitido';\n"
     "    end if;\n"
     "    if requisito is not null and not exists (\n"
     "         select 1 from nucleo.requisito_legal r\n"
     "          where r.codigo = requisito and r.tipo = tipo_nuevo) then\n"
     "      raise exception 'ese requisito no existe o no es de ese tipo'\n"
     "        using errcode = 'P0001', hint = 'requisito_invalido';\n"
     "    end if;\n"
     "    if reemplaza is not null then\n      select * into anterior"),
    ("       version_documento_id, cargada_por, identidad_carga, reemplaza_a)\n"
     "    values (c, proveedor, tipo_nuevo, btrim(descripcion_nueva), vence, d.id, archivo.id,"
     " actor,\n"
     "            identidad, reemplaza)",
     "       version_documento_id, cargada_por, identidad_carga, reemplaza_a, no_vence,\n"
     "       requisito_codigo)\n"
     "    values (c, proveedor, tipo_nuevo, btrim(descripcion_nueva), vence, d.id, archivo.id,"
     " actor,\n"
     "            identidad, reemplaza, no_vence_nuevo, requisito)"),
    ("'tipo', tipo_nuevo, 'vence_el', vence,",
     "'tipo', tipo_nuevo, 'vence_el', vence, 'no_vence', no_vence_nuevo,\n"
     "                         'requisito', requisito,"),
)  # fmt: skip
LEER_CAMBIOS = (
    ("reemplaza_a uuid, documento_vigente boolean)",
     "reemplaza_a uuid, documento_vigente boolean, no_vence boolean, requisito_codigo text)"),
    ("perform nucleo.exigir_lectura_egresos(c, facultad,",
     "perform nucleo.exigir_lectura_proveedores(c, facultad,"),
    ("a.cargada_en, a.reemplaza_a, y.estado = 'vigente'",
     "a.cargada_en, a.reemplaza_a, y.estado = 'vigente', a.no_vence,\n"
     "             a.requisito_codigo"),
)  # fmt: skip
EXPORTAR_CAMBIOS = (
    ("                                'reemplaza_a', a.reemplaza_a)",
     "                                'reemplaza_a', a.reemplaza_a, 'no_vence', a.no_vence,\n"
     "                                'requisito_codigo', a.requisito_codigo)"),
    ("       order by a.cargada_en, a.id;\n",
     "       order by a.cargada_en, a.id;\n"
     "    -- 0104: los rubros legales asignados y la configuración, con el nombre de quien\n"
     "    -- asignó.\n"
     "    return query\n"
     "      select 'rubro_legal_proveedor'::text,\n"
     "             jsonb_build_object('numero', e.numero, 'proveedor_id', e.proveedor_id,\n"
     "                                'rubro_legal', e.rubro_legal, 'accion', e.accion,\n"
     "                                'registrado_por_nombre',\n"
     "                                nucleo.nombre_para_exportar(c, e.registrado_por, hoy),\n"
     "                                'registrado_en', e.registrado_en)\n"
     "        from nucleo.rubro_legal_proveedor e\n"
     "       where e.consorcio_id = c\n"
     "       order by e.numero;\n"
     "    return query\n"
     "      select 'configuracion_proveedores'::text,\n"
     "             jsonb_build_object('numero', k.numero, 'dias_por_vencer', k.dias_por_vencer,\n"
     "                                'modo_exigencia', k.modo_exigencia,\n"
     "                                'admite_no_vence', k.admite_no_vence,\n"
     "                                'roles_que_ven', k.roles_que_ven,\n"
     "                                'requisitos_agregados', k.requisitos_agregados,\n"
     "                                'requisitos_omitidos', k.requisitos_omitidos,\n"
     "                                'origen', k.origen, 'registrada_en', k.registrada_en)\n"
     "        from nucleo.configuracion_proveedores k\n"
     "       where k.consorcio_id = c\n"
     "       order by k.numero;\n"),
)  # fmt: skip

EN_SU_LUGAR = ((ESTADO, ESTADO_CAMBIOS), (EXPORTAR_PROVEEDORES, EXPORTAR_CAMBIOS))
"""Mismo nombre, argumentos y resultado: `create or replace`."""
REDEFINIDAS = ((CARGAR_ANTES, CARGAR, CARGAR_CAMBIOS), (LEER, LEER, LEER_CAMBIOS))
"""Cambian los argumentos o el resultado: se borran y se vuelven a crear con el texto parchado."""

GUARDA_DOWNGRADE = f"""
lock table {TABLA_CONFIG}, {TABLA_RUBRO}, {TABLA_CONSTANCIA} in access exclusive mode;
do $$
begin
  if exists (select 1 from {TABLA_CONFIG}) or exists (select 1 from {TABLA_RUBRO})
     or exists (select 1 from {TABLA_CONSTANCIA}
                 where no_vence or requisito_codigo is not null) then
    raise exception '0104 no baja con configuración, rubros o constancias que la usan'
      using errcode = 'P0001', hint = 'downgrade_con_datos';
  end if;
end
$$;
"""


def _definicion(funcion: str, cambios, *, al_reves: bool) -> str:
    definicion = (
        op.get_bind()
        .execute(text("select pg_get_functiondef(cast(:f as regprocedure))"), {"f": funcion})
        .scalar_one()
    )
    for antes, despues in reversed(cambios) if al_reves else cambios:
        if al_reves:
            antes, despues = despues, antes
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0104: {funcion} no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


def _redefinir(actual: str, cambios, *, al_reves: bool) -> None:
    definicion = _definicion(actual, cambios, al_reves=al_reves)
    op.execute(f"drop function {actual}")
    op.execute(definicion)


def _sembrar() -> None:
    for codigo, rubro, tipo, descripcion, fuente, verificado in REQUISITOS:
        op.get_bind().execute(
            text(
                f"insert into {TABLA_REQUISITO}"
                " (codigo, rubro_legal, tipo, descripcion, fuente, verificado)"
                " values (:c, :r, :t, :d, :f, :v)"
            ),
            {"c": codigo, "r": rubro, "t": tipo, "d": descripcion, "f": fuente, "v": verificado},
        )


def upgrade() -> None:
    op.execute(TABLAS)
    _sembrar()
    op.execute(FUNCIONES)
    for funcion, cambios in EN_SU_LUGAR:
        op.execute(_definicion(funcion, cambios, al_reves=False))
    for antes, _despues, cambios in REDEFINIDAS:
        _redefinir(antes, cambios, al_reves=False)
    op.execute(RUTAS_SQL)
    for funcion in (*RUTAS, *INTERNAS, CARGAR, LEER):
        op.execute(f"revoke all on function {funcion} from public")
    for funcion in (*RUTAS, CARGAR, LEER):
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    op.execute(GUARDA_DOWNGRADE)
    for funcion in RUTAS:
        op.execute(f"drop function {funcion}")
    for _antes, despues, cambios in reversed(REDEFINIDAS):
        _redefinir(despues, cambios, al_reves=True)
    for funcion in (CARGAR_ANTES, LEER):
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")
    for funcion, cambios in reversed(EN_SU_LUGAR):
        op.execute(_definicion(funcion, cambios, al_reves=True))
    op.execute("drop trigger documentacion_legal on nucleo.orden_pago_version")
    for funcion in (EXIGIR_LECTURA, PUEDE_VER, EXIGIR_LEGAL, FALTANTES, RUBROS_DE, EXIGIDOS,
                    CONFIG_VIGENTE):  # fmt: skip
        op.execute(f"drop function {funcion}")
    op.execute(
        f"alter table {TABLA_CONSTANCIA} drop constraint vencimiento_definido,"
        " drop column requisito_codigo, drop column no_vence,"
        " alter column vence_el set not null"
    )
    op.execute(f"drop table {TABLA_RUBRO}")
    op.execute(f"drop table {TABLA_CONFIG}")
    op.execute(f"drop table {TABLA_REQUISITO}")
