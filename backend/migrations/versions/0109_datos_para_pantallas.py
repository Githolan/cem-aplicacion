"""Datos para que la pantalla no ofrezca un acto que la base va a negar (informe de fricción del
17-11-2026, criterio 8, «tanda de backend» aprobada por el propietario el mismo día). Solo lee: no
cambia ninguna regla, tabla ni permiso de escritura. Cada dato sale de la misma regla que ya aplica
la función que decide el acto.

- **Salteo por urgencia (`0106`; C8P-02, C8P-03 y comentario 40 del propietario).**
  `nucleo.salteos_documentacion_lectura` suma dos columnas, sin decir quién es la persona:
  `propio` (lo registró la cuenta de quien lee: la misma comparación con que
  `saltear_documentacion_legal` niega otro con `salteo_vigente`) y `de_quien_prepara` (lo
  registró quien lee o su misma identidad humana: la barrera de la orden no lo toma y niega con
  `salteo_de_quien_prepara`). Cambia el tipo que devuelve: se borra y se crea de nuevo, con el
  mismo permiso para `cem_app`.
- **Mantener un mandato del Consejo (`0023`; C8G-04).** `nucleo.condicion_consejo_hoy(mandato)`:
  si la persona del mandato cumple hoy una condición para integrar el Consejo, medida con
  `cobertura_consejo` como en `resolver_revision_mandato`. Con los controles de `mandato_detalle`
  (ámbito y `mandatos.ver`): `cobertura_consejo` sigue sin concederse a `cem_app`.
- **Abrir una invitación (`0007`, `0026`; C8G-11).** `nucleo.paso_invitacion(token, sesión)`: lo
  que pasaría al aceptar con esa sesión, en el orden de `aceptar_invitacion` (`crear_cuenta`,
  `entrar`, `otra_cuenta`, `propia`, `aceptar`). Nulo si la invitación no sirve. Lo que dice ya lo
  decía el 409 de aceptar a quien tiene el enlace; `propia` solo sale con la sesión de esa cuenta.
- **Abrir una invitación de mandato (`0015`; C8G-12 y MAN-15).**
  `nucleo.paso_invitacion_mandato(token, sesión)`: lo mismo en el orden de
  `aceptar_invitacion_mandato`, con `de_si_mismo` y `consejo_superpuesto`, que solo salen con la
  sesión de la cuenta del correo; `no_sirve` cuando aceptar respondería que la invitación no sirve
  (por aprobar, o quien la emitió o aprobó ya no está habilitado). Nulo si no existe.

Downgrade: se borran las tres funciones nuevas y la lectura de salteos vuelve a la de `0106`, con
el mismo texto. No hay datos que perder.

Revisión: 0109
Anterior: 0108
"""

import importlib.util
from pathlib import Path

from alembic import op

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None

LECTURA = "nucleo.salteos_documentacion_lectura(uuid, uuid, text, uuid, text, uuid)"
CONDICION = "nucleo.condicion_consejo_hoy(uuid)"
PASO = "nucleo.paso_invitacion(bytea, uuid)"
PASO_MANDATO = "nucleo.paso_invitacion_mandato(bytea, uuid)"
NUEVAS = (CONDICION, PASO, PASO_MANDATO)


def _lectura_0106() -> str:
    """El `create function` de la lectura tal como lo escribió `0106` (el mismo texto vuelve en
    el downgrade)."""
    ruta = Path(__file__).with_name("0106_salteo_documentacion_legal.py")
    spec = importlib.util.spec_from_file_location("migracion_0106", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    inicio = modulo.RUTAS_SQL.index("create function nucleo.salteos_documentacion_lectura(")
    fin = modulo.RUTAS_SQL.index("$$;", inicio) + len("$$;")
    return modulo.RUTAS_SQL[inicio:fin]


_EXIGIR = (
    "    perform nucleo.exigir_lectura_proveedores(c, facultad, actor, origen_pedido,"
    " sesion);\n"
)  # fmt: skip
CAMBIOS_LECTURA = (
    (
        "                 version integer, usado_en timestamptz)\n",
        "                 version integer, usado_en timestamptz, propio boolean,\n"
        "                 de_quien_prepara boolean)\n",
    ),
    (
        "    c uuid := plataforma.consorcio_actual();\n",
        "    c uuid := plataforma.consorcio_actual();\n    yo_humano uuid;\n",
    ),
    (_EXIGIR, _EXIGIR + "    yo_humano := nucleo.identidad_humana_de(c, actor);\n"),
    (
        "             u.orden_id, u.version, u.usado_en\n",
        "             u.orden_id, u.version, u.usado_en,\n"
        "             -- 0109: la comparación de `saltear_documentacion_legal`\n"
        "             -- (`salteo_vigente`).\n"
        "             s.registrado_por = actor,\n"
        "             -- 0109: la de la barrera de la orden (`salteo_de_quien_prepara`).\n"
        "             s.registrado_por = actor or s.identidad is not distinct from yo_humano\n",
    ),
)


def _lectura_0109() -> str:
    definicion = _lectura_0106()
    for antes, despues in CAMBIOS_LECTURA:
        if definicion.count(antes) != 1:
            raise RuntimeError(f"0109: la lectura de 0106 no tiene exactamente una vez {antes!r}")
        definicion = definicion.replace(antes, despues)
    return definicion


FUNCIONES = """
-- C8G-04: si la persona del mandato cumple hoy una condición para integrar el Consejo, con la
-- misma medida que `resolver_revision_mandato` (cobertura de hoy a hoy). Los controles de
-- `mandato_detalle`: ámbito y `mandatos.ver`. Nulo si el mandato no es de este consorcio.
create function nucleo.condicion_consejo_hoy(mandato uuid)
  returns boolean
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
    return (select nucleo.cobertura_consejo(m.id, hoy, hoy) @> hoy
              from nucleo.mandato m
             where m.consorcio_id = c and m.id = mandato);
  end
  $$;

-- C8G-11: lo que pasaría al aceptar la invitación con esa sesión, en el orden de
-- `aceptar_invitacion`. Nulo si la invitación no sirve (la consulta ya responde 404).
create function nucleo.paso_invitacion(token bytea, persona_sesion uuid)
  returns text
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion;
    p uuid;
  begin
    select * into i from nucleo.invitacion x where x.token_hash = token;
    if nucleo.motivo_invitacion_invalida(i) is not null then
      return null;
    end if;
    select x.id into p from nucleo.persona x
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
      return case when persona_sesion is null then 'crear_cuenta' else 'otra_cuenta' end;
    end if;
    if persona_sesion is null then
      return 'entrar';
    end if;
    if persona_sesion <> p then
      return 'otra_cuenta';
    end if;
    if i.invitada_por is not null
       and nucleo.misma_persona_humana(i.consorcio_id, i.invitada_por, p) then
      return 'propia';
    end if;
    return 'aceptar';
  end
  $$;

-- C8G-12 y MAN-15: lo mismo para una invitación de mandato, en el orden de
-- `aceptar_invitacion_mandato`. `no_sirve` donde aceptar respondería que no sirve; nulo si no
-- existe.
create function nucleo.paso_invitacion_mandato(token bytea, persona_sesion uuid)
  returns text
  language plpgsql stable security definer
  set search_path = pg_catalog, pg_temp
  as $$
  declare
    i nucleo.invitacion_mandato;
    m nucleo.mandato;
    hoy date;
    p uuid;
  begin
    select * into i from nucleo.invitacion_mandato x where x.token_hash = token;
    if i.id is null then
      return null;
    end if;
    select * into m from nucleo.mandato x where x.id = i.mandato_id;
    hoy := nucleo.hoy_del_consorcio(m.consorcio_id);
    if i.aceptada_en is not null or i.revocada_en is not null or i.aprobada_en is null
       or i.vence_en <= now()
       or not exists (select 1 from nucleo.consorcio co
                       where co.id = m.consorcio_id and co.eliminado_en is null)
       or nucleo.mandato_asociable(m, hoy) is not null
       or (i.emitida_por is not null
           and not nucleo.habilitado_para_mandatos(i.emitida_por, m.consorcio_id, hoy))
       or (i.aprobada_por is not null
           and not nucleo.habilitado_para_mandatos(i.aprobada_por, m.consorcio_id, hoy)) then
      return 'no_sirve';
    end if;
    select x.id into p from nucleo.persona x
     where x.correo = i.correo and x.eliminado_en is null;
    if p is null then
      return case when persona_sesion is null then 'crear_cuenta' else 'otra_cuenta' end;
    end if;
    if persona_sesion is null then
      return 'entrar';
    end if;
    if persona_sesion <> p then
      return 'otra_cuenta';
    end if;
    if p = i.emitida_por or p = i.aprobada_por then
      return 'de_si_mismo';
    end if;
    if exists (
         select 1 from nucleo.mandato x
          where x.consorcio_id = m.consorcio_id and x.persona_id = p
            and x.tipo = 'miembro_consejo'
            and x.causa_baja is distinct from 'error_carga'
            and (x.vigente_hasta is null or x.vigente_hasta > hoy)
            and daterange(x.vigente_desde, x.vigente_hasta)
                && daterange(m.vigente_desde, m.vigente_hasta)) then
      return 'consejo_superpuesto';
    end if;
    return 'aceptar';
  end
  $$;
"""


def _crear_lectura(definicion: str) -> None:
    op.execute(f"drop function {LECTURA}")
    op.execute(definicion)
    op.execute(f"revoke all on function {LECTURA} from public")
    op.execute(f"grant execute on function {LECTURA} to cem_app")


def upgrade() -> None:
    _crear_lectura(_lectura_0109())
    op.execute(FUNCIONES)
    for funcion in NUEVAS:
        op.execute(f"revoke all on function {funcion} from public")
        op.execute(f"grant execute on function {funcion} to cem_app")


def downgrade() -> None:
    for funcion in reversed(NUEVAS):
        op.execute(f"drop function {funcion}")
    _crear_lectura(_lectura_0106())
