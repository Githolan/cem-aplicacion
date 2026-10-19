"""Lectura de mandatos y decisiones y destinatario del mandato (H2b 2b-4c, PR 1).

Diseño: `doc/diseno/H2b_4c_Diseno.md` del repositorio CEM; decisiones del propietario del
19-10-2026 sobre los puntos 1 y 3 de §7 de 2b-4b.

**Facultades.** `mandatos.ver` y `decisiones.ver` entran al catálogo y a los paquetes de
`miembro_consejo` y `administrador`. El de `administrador_externo` no cambia: no tiene lectura
general. Los mandatos que ya existen conservan lo que otorgó su decisión: no se les agrega nada.

**Lectura con segunda barrera.** Cuatro funciones `security definer` que solo `cem_app` ejecuta,
como `padron_del_consorcio` (`0014`). Exigen consorcio y persona en el ámbito y un mandato
vigente hoy con la facultad en ese consorcio (`42501` si no). Devuelven lo mínimo:
- listas sin referencias, instrumentos ni hashes;
- el CUIT solo del administrador externo;
- nunca correo, teléfono ni el identificador de la persona del mandato.

`candidatos_a_mandato` (segunda barrera `mandatos.registrar`) devuelve las personas con un
vínculo confirmado, vigente o futuro, para elegir a quién se le da el alta.

**Destinatario.** Por la API, `alta_mandato` exige para `miembro_consejo` y `administrador` un
vínculo confirmado, vigente o futuro, de la persona en el mismo consorcio
(`persona_sin_vinculo`). El motivo es el mismo si la cuenta no existe, es de otro consorcio o
no tiene el vínculo confirmado: no permite enumerar cuentas. El administrador externo se da de
alta por la API sin cuenta (`externo_por_invitacion`); la cuenta se asocia por A7. La validación
del origen y del actor pasa ahora antes de mirar a la persona. Con origen `soporte` la función
sigue como en `0016` (vía excepcional, auditada).

**E15 con vigencia vacía.** `revocar_sesiones_por_vencimiento` (`0017`) deja afuera los
mandatos con `vigente_hasta <= vigente_desde`: nunca rigieron ni concedieron nada, y la decisión
4 del propietario dice que un mandato que nunca dio privilegios no revoca sesiones.

Downgrade: se niega (`downgrade_con_datos`) si algún mandato tiene otorgada una de las dos
facultades nuevas. Si no, vuelve a `alta_mandato` de `0016` y a
`revocar_sesiones_por_vencimiento` de `0017` (los mismos textos, leídos de esas migraciones),
quita las funciones y saca las facultades de los paquetes y del catálogo.

Revisión: 0018
Anterior: 0017
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

FACULTADES_NUEVAS = ("mandatos.ver", "decisiones.ver")
TIPOS_CON_LECTURA = ("miembro_consejo", "administrador")
LECTURAS = (
    "nucleo.mandatos_del_consorcio()",
    "nucleo.mandato_detalle(uuid)",
    "nucleo.decisiones_del_consorcio()",
    "nucleo.decision_detalle(uuid)",
    "nucleo.candidatos_a_mandato()",
)
INTERNAS = (
    "nucleo.tiene_facultad_vigente(uuid, uuid, text, date)",
    "nucleo.estado_mandato(nucleo.causa_baja, date, date, date)",
)


def _funcion_de(archivo: str, inicio: str) -> str:
    """El texto de una función tal como la dejó una migración anterior: es la base del cambio y
    lo que restaura el downgrade, sin copiarlo a mano."""
    ruta = Path(__file__).with_name(archivo)
    spec = importlib.util.spec_from_file_location(ruta.stem, ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    desde = modulo.UPGRADE.index(inicio)
    fin = modulo.UPGRADE.index("\n  $$;\n", desde) + len("\n  $$;\n")
    return modulo.UPGRADE[desde:fin]


def _alta_de_0016() -> str:
    return _funcion_de(
        "0016_mandatos_a15_e26_r10.py", "create or replace function nucleo.alta_mandato("
    )


def _vencimiento_de_0017() -> str:
    texto = _funcion_de(
        "0017_mandatos_revocar_al_vencer.py",
        "create function nucleo.revocar_sesiones_por_vencimiento(",
    )
    return texto.replace("create function", "create or replace function", 1)


VIGENTE_DESPUES_DEL_ALTA = """\
         and x.vigente_hasta > (x.creado_en at time zone co.zona_horaria)::date
"""
VIGENCIA_NO_VACIA = """\
         -- 2b-4c (0018): una vigencia vacía nunca rigió ni concedió nada (decisión 4).
         and x.vigente_hasta > x.vigente_desde
"""


def _vencimiento_0018() -> str:
    texto = _vencimiento_de_0017()
    assert texto.count(VIGENTE_DESPUES_DEL_ALTA) == 1
    return texto.replace(VIGENTE_DESPUES_DEL_ALTA, VIGENTE_DESPUES_DEL_ALTA + VIGENCIA_NO_VACIA)


EXISTE_PERSONA_0016 = """\
    if persona is not null and not exists (select 1 from nucleo.persona p where p.id = persona) then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;
"""
FIN_CONTROL_DE_ORIGEN = """\
      raise exception 'el origen soporte no lleva actor: lo registra el equipo'
        using errcode = 'P0001', hint = 'origen_invalido';
    end if;
"""
DESTINATARIO = """\
    -- 2b-4c (0018): el destinatario, después del origen y del actor. Por la API, el Consejo y
    -- el administrador necesitan un vínculo confirmado, vigente o futuro, en este consorcio; el
    -- motivo es el mismo si la cuenta no existe o es de otro consorcio (sin oráculo). El
    -- administrador externo entra sin cuenta: la cuenta se asocia por A7. Soporte conserva la
    -- vía de 0016.
    if origen_pedido = 'api' then
      if tipo_nuevo = 'administrador_externo' and persona is not null then
        raise exception 'el administrador externo entra sin cuenta: se asocia por invitación'
          using errcode = 'P0001', hint = 'externo_por_invitacion';
      end if;
      if tipo_nuevo <> 'administrador_externo' and persona is not null and not exists (
           select 1 from nucleo.vinculo v
             join nucleo.persona pe on pe.id = v.persona_id and pe.eliminado_en is null
             join nucleo.unidad u
               on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id
              and u.eliminado_en is null
            where v.consorcio_id = c and v.persona_id = persona
              and v.confirmado_en is not null
              and (v.vigente_hasta is null or v.vigente_hasta > nucleo.hoy_del_consorcio(c))) then
        raise exception 'la persona no tiene un vínculo confirmado en este consorcio'
          using errcode = 'P0001', hint = 'persona_sin_vinculo';
      end if;
    elsif persona is not null
          and not exists (select 1 from nucleo.persona p where p.id = persona) then
      raise exception 'la persona no existe' using errcode = 'P0001', hint = 'persona_invalida';
    end if;
"""


def _alta_0018() -> str:
    alta = _alta_de_0016()
    assert alta.count(EXISTE_PERSONA_0016) == 1 and alta.count(FIN_CONTROL_DE_ORIGEN) == 1
    alta = alta.replace(EXISTE_PERSONA_0016, "")
    return alta.replace(FIN_CONTROL_DE_ORIGEN, FIN_CONTROL_DE_ORIGEN + DESTINATARIO)


def _catalogo() -> str:
    facultades = ",\n  ".join(f"('{f}', 'mandatos', false)" for f in FACULTADES_NUEVAS)
    paquetes = ",\n  ".join(f"('{t}', '{f}')" for t in TIPOS_CON_LECTURA for f in FACULTADES_NUEVAS)
    return (
        f"insert into nucleo.facultad (codigo, area, economica) values\n  {facultades};\n"
        f"insert into nucleo.paquete_facultad (tipo, facultad) values\n  {paquetes};\n"
    )


LISTA_FACULTADES = ", ".join(f"'{f}'" for f in FACULTADES_NUEVAS)

FUNCIONES = r"""
-- Segunda barrera de las lecturas: un mandato vigente hoy de la persona, con esa facultad, en
-- ese consorcio. Interna: la llaman las funciones `security definer`, no `cem_app`.
create function nucleo.tiene_facultad_vigente(c uuid, quien uuid, codigo text, hoy date)
  returns boolean
  language sql stable
  set search_path = pg_catalog, pg_temp
  as $$
    select exists (
      select 1 from nucleo.mandato m
        join nucleo.facultad_otorgada f
          on f.consorcio_id = m.consorcio_id and f.mandato_id = m.id
       where m.consorcio_id = c and m.persona_id = quien and f.facultad = codigo
         and f.recurso is null and nucleo.mandato_vigente(m.id, hoy))
  $$;

-- Estado de un mandato para leerlo (A1: `vigente_hasta` exclusivo).
create function nucleo.estado_mandato(causa nucleo.causa_baja, desde date, hasta date, hoy date)
  returns text
  language sql immutable
  set search_path = pg_catalog, pg_temp
  as $$
    select case
      when causa = 'error_carga' then 'error_carga'
      when causa is not null then 'dado_de_baja'
      when hasta is not null and hasta <= desde then 'terminado'
      when desde > hoy then 'futuro'
      when hasta is not null and hasta <= hoy then 'terminado'
      else 'vigente'
    end
  $$;

create function nucleo.mandatos_del_consorcio()
  returns table (id uuid, tipo nucleo.tipo_mandato, nombre text, cuit text,
                 vigente_desde date, vigente_hasta date, estado text, con_cuenta boolean)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los mandatos' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if not nucleo.tiene_facultad_vigente(c, quien, 'mandatos.ver', hoy) then
      raise exception 'sin mandatos.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select m.id, m.tipo,
             case when m.tipo = 'administrador_externo'
                  then coalesce(m.mandatario_nombre, pe.nombre)
                  else coalesce(pe.nombre, m.mandatario_nombre) end,
             case when m.tipo = 'administrador_externo' then m.mandatario_cuit end,
             m.vigente_desde, m.vigente_hasta,
             nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy),
             m.persona_id is not null
        from nucleo.mandato m
        left join nucleo.persona pe on pe.id = m.persona_id
       where m.consorcio_id = c
       order by m.vigente_desde desc, m.id;
  end
  $$;

create function nucleo.mandato_detalle(mandato uuid)
  returns table (id uuid, tipo nucleo.tipo_mandato, nombre text, cuit text,
                 vigente_desde date, vigente_hasta date, estado text, con_cuenta boolean,
                 matricula text, decision_id uuid, facultades text[], causa_baja text,
                 baja_decision_id uuid, instrumento_referencia text, motivo_fin text,
                 finalizado_en timestamptz, origen text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los mandatos' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if not nucleo.tiene_facultad_vigente(c, quien, 'mandatos.ver', hoy) then
      raise exception 'sin mandatos.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select m.id, m.tipo,
             case when m.tipo = 'administrador_externo'
                  then coalesce(m.mandatario_nombre, pe.nombre)
                  else coalesce(pe.nombre, m.mandatario_nombre) end,
             case when m.tipo = 'administrador_externo' then m.mandatario_cuit end,
             m.vigente_desde, m.vigente_hasta,
             nucleo.estado_mandato(m.causa_baja, m.vigente_desde, m.vigente_hasta, hoy),
             m.persona_id is not null, m.matricula, m.decision_id,
             coalesce((select array_agg(f.facultad order by f.facultad)
                         from nucleo.facultad_otorgada f
                        where f.consorcio_id = m.consorcio_id and f.mandato_id = m.id),
                      '{}'::text[]),
             m.causa_baja::text, m.baja_decision_id, m.instrumento_referencia, m.motivo_fin,
             m.finalizado_en, m.origen
        from nucleo.mandato m
        left join nucleo.persona pe on pe.id = m.persona_id
       where m.consorcio_id = c and m.id = mandato;
  end
  $$;

create function nucleo.decisiones_del_consorcio()
  returns table (id uuid, tipo nucleo.tipo_decision, fecha date, descripcion text)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer las decisiones' using errcode = '42501';
    end if;
    if not nucleo.tiene_facultad_vigente(c, quien, 'decisiones.ver',
                                         nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin decisiones.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select d.id, d.tipo, d.fecha, d.descripcion
        from nucleo.decision_formal d
       where d.consorcio_id = c
       order by d.fecha desc, d.id desc;
  end
  $$;

create function nucleo.decision_detalle(decision uuid)
  returns table (id uuid, tipo nucleo.tipo_decision, fecha date, descripcion text,
                 referencia_acta text, sha256_acta text, origen text, registrada_por uuid)
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer las decisiones' using errcode = '42501';
    end if;
    if not nucleo.tiene_facultad_vigente(c, quien, 'decisiones.ver',
                                         nucleo.hoy_del_consorcio(c)) then
      raise exception 'sin decisiones.ver en este consorcio' using errcode = '42501';
    end if;
    return query
      select d.id, d.tipo, d.fecha, d.descripcion, d.referencia_acta, d.sha256_acta, d.origen,
             d.registrada_por
        from nucleo.decision_formal d
       where d.consorcio_id = c and d.id = decision;
  end
  $$;

-- A quién se le puede dar un mandato de Consejo o de administración por la API: las personas
-- con un vínculo confirmado, vigente o futuro, en el consorcio. Segunda barrera
-- `mandatos.registrar`: el identificador de la persona sale solo para quien puede dar el alta.
create function nucleo.candidatos_a_mandato()
  returns table (persona_id uuid, nombre text, unidades text[])
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    c uuid := plataforma.consorcio_actual();
    quien uuid := plataforma.persona_actual();
    hoy date;
  begin
    if c is null or quien is null then
      raise exception 'sin ámbito para leer los candidatos' using errcode = '42501';
    end if;
    hoy := nucleo.hoy_del_consorcio(c);
    if not nucleo.tiene_facultad_vigente(c, quien, 'mandatos.registrar', hoy) then
      raise exception 'sin mandatos.registrar en este consorcio' using errcode = '42501';
    end if;
    return query
      select pe.id, pe.nombre, array_agg(distinct u.designacion order by u.designacion)
        from nucleo.vinculo v
        join nucleo.persona pe on pe.id = v.persona_id and pe.eliminado_en is null
        join nucleo.unidad u
          on u.consorcio_id = v.consorcio_id and u.id = v.unidad_id and u.eliminado_en is null
       where v.consorcio_id = c
         and v.persona_id <> quien
         and v.confirmado_en is not null
         and (v.vigente_hasta is null or v.vigente_hasta > hoy)
       group by pe.id, pe.nombre
       order by pe.nombre, pe.id;
  end
  $$;
"""


def _privilegios() -> str:
    lineas = [f"revoke all on function {f} from public;" for f in LECTURAS + INTERNAS]
    lineas += [f"grant execute on function {f} to cem_app;" for f in LECTURAS]
    return "\n".join(lineas) + "\n"


def upgrade() -> None:
    op.execute(_catalogo())
    op.execute(FUNCIONES)
    op.execute(_privilegios())
    op.execute(_alta_0018())
    op.execute(_vencimiento_0018())


def downgrade() -> None:
    op.execute(
        rf"""
do $$
begin
  if exists (select 1 from nucleo.facultad_otorgada where facultad in ({LISTA_FACULTADES})) then
    raise exception 'el downgrade de 0018 se niega: hay mandatos con mandatos.ver o decisiones.ver'
      using errcode = 'P0001', hint = 'downgrade_con_datos',
            detail = 'las facultades otorgadas son constancia de lo que decidió el acta';
  end if;
end
$$;
"""
    )
    op.execute(_alta_de_0016())
    op.execute(_vencimiento_de_0017())
    for f in LECTURAS + INTERNAS:
        op.execute(f"drop function {f};")
    op.execute(
        f"delete from nucleo.paquete_facultad where facultad in ({LISTA_FACULTADES});\n"
        f"delete from nucleo.facultad where codigo in ({LISTA_FACULTADES});\n"
    )
